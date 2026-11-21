"""
Long-running Nest poller scheduler.

Run as the NestPoller NSSM service with --duration 0 to poll every 5 minutes
indefinitely. After each poll it writes poller_status.json (read by the
dashboard) and periodically copies the data into a Google Drive folder.
"""

from __future__ import annotations

import argparse
import logging
import pathlib
import signal
import sys
import time
from datetime import datetime, timedelta
from typing import Optional

# Add parent directory to path so we can import nest_poller
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from src import nest_poller
from src.drive_sync import DriveFolderSync
from src.oauth_reauth import NonBlockingReauth, update_config_refresh_token
from src.poller_status import write_status

POLL_INTERVAL_SECONDS = 5 * 60  # 5 minutes
DEFAULT_RUN_DURATION_HOURS = 24
RUN_FOREVER = 0  # Sentinel: pass --duration 0 to run indefinitely (NSSM service mode)


class GracefulShutdown:
    """Handle graceful shutdown on Ctrl+C or SIGTERM."""

    def __init__(self) -> None:
        self.should_exit = False
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum: int, frame) -> None:
        sig_name = signal.Signals(signum).name
        logging.info("Received %s, shutting down gracefully...", sig_name)
        self.should_exit = True


def setup_logging(log_dir: pathlib.Path, log_level: str) -> pathlib.Path:
    """
    Set up logging to both console and a log file.

    Returns the path to the log file.
    """
    log_dir.mkdir(parents=True, exist_ok=True)

    # Single log file, overwritten each run
    log_file = log_dir / "poller.log"

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Clear any existing handlers
    root_logger.handlers.clear()

    # File handler - overwrite mode for fresh log each run
    file_handler = logging.FileHandler(log_file, mode="w", encoding="utf-8")
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    )
    root_logger.addHandler(file_handler)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    )
    root_logger.addHandler(console_handler)

    return log_file


class PollerState:
    """State carried between poll cycles."""

    def __init__(self) -> None:
        self.reauth: Optional[NonBlockingReauth] = None
        self.drive_sync: Optional[DriveFolderSync] = None
        self.last_error: Optional[str] = None
        self.last_success_at: Optional[float] = None
        self.consecutive_failures = 0
        self.output_dir: Optional[pathlib.Path] = None
        self.started_at = time.time()

    def end_reauth(self) -> None:
        if self.reauth is not None:
            self.reauth.close()
            self.reauth = None

    def configure_drive_sync(self, config: "nest_poller.Config") -> None:
        if config.drive_sync_dir is None:
            self.drive_sync = None
        elif self.drive_sync is None or self.drive_sync.drive_dir != config.drive_sync_dir:
            self.drive_sync = DriveFolderSync(config.drive_sync_dir, config.drive_sync_interval_minutes)


def run_single_poll(config_path: pathlib.Path, state: PollerState) -> bool:
    """
    Run a single poll cycle. Never blocks waiting for the user: if the refresh
    token has expired, it advances re-authorization by one step and returns.

    Returns True if data was logged, False otherwise. The reason for a failure
    is recorded in state.last_error for the dashboard.
    """
    try:
        config = nest_poller.load_config(config_path)
        state.output_dir = config.output_dir
        state.configure_drive_sync(config)
        new_refresh_token = None

        try:
            access_token, new_refresh_token = nest_poller.refresh_access_token(config)
            if state.reauth is not None:
                logging.info("Refresh token is valid again; cancelling pending re-authorization.")
                state.end_reauth()
        except nest_poller.RefreshTokenExpiredError as exc:
            if state.reauth is None:
                logging.warning("Refresh token expired: %s", exc)
                logging.info("Starting non-blocking re-authorization...")
                extra = [state.drive_sync.pending_code_file] if state.drive_sync else []
                state.reauth = NonBlockingReauth(
                    config_path=config_path,
                    project_id=config.project_id,
                    client_id=config.client_id,
                    client_secret=config.client_secret,
                    email_config=config.email,
                    extra_pending_files=extra,
                    dashboard_url=config.dashboard_url,
                )
            access_token = state.reauth.step()
            if access_token is None:
                state.last_error = state.reauth.last_error or "Nest sign-in expired; waiting for re-authorization."
                return False
            state.end_reauth()
            config = nest_poller.load_config(config_path)

        # Save new refresh token if provided
        if new_refresh_token:
            update_config_refresh_token(config_path, new_refresh_token)
            logging.info("Updated config.json with new refresh token from Google")

        devices = nest_poller.fetch_devices(config, access_token)
        rows = nest_poller.extract_thermostat_rows(devices, config)

        if not rows:
            logging.warning("No thermostat devices found.")
            state.last_error = "Nest returned no thermostats."
            return False

        nest_poller.write_rows(rows, config)
        state.last_error = None
        state.last_success_at = time.time()
        return True

    except nest_poller.NestPollerError as exc:
        logging.error("Poller error: %s", exc)
        state.last_error = str(exc)
        return False
    except Exception as exc:
        logging.exception("Unexpected error during poll: %s", exc)
        state.last_error = f"Unexpected error: {exc}"
        return False


def _write_status(config_path: pathlib.Path, state: PollerState, poll_interval_seconds: int) -> None:
    reauth = state.reauth
    sync = state.drive_sync
    write_status(config_path.parent, {
        "updated_at": time.time(),
        "started_at": state.started_at,
        "poll_interval_seconds": poll_interval_seconds,
        "last_success_at": state.last_success_at,
        "last_error": state.last_error,
        "consecutive_failures": state.consecutive_failures,
        "reauth_needed": reauth is not None,
        "reauth_since": reauth.waiting_since if reauth else None,
        "auth_url": reauth.auth_url if reauth else None,
        "drive_sync_dir": str(sync.drive_dir) if sync else None,
        "drive_sync_last_at": sync.last_sync_at if sync else None,
        "drive_sync_error": sync.last_error if sync else None,
    })


def run_scheduler(
    config_path: pathlib.Path,
    duration_hours: Optional[float],
    poll_interval_seconds: int,
    shutdown: GracefulShutdown,
) -> None:
    """Run the polling loop. Pass duration_hours=None to run indefinitely."""

    end_time = None if duration_hours is None else datetime.now() + timedelta(hours=duration_hours)
    poll_count = 0
    error_count = 0
    state = PollerState()

    if end_time is None:
        logging.info("Starting poller scheduler in service mode (runs indefinitely).")
    else:
        logging.info(
            "Starting poller scheduler. Will run until %s (%.1f hours)",
            end_time.strftime("%Y-%m-%d %H:%M:%S"),
            duration_hours,
        )
    logging.info("Poll interval: %d seconds (%d minutes)", poll_interval_seconds, poll_interval_seconds // 60)

    while (end_time is None or datetime.now() < end_time) and not shutdown.should_exit:
        poll_count += 1
        logging.info("=== Poll #%d starting ===", poll_count)

        success = run_single_poll(config_path, state)
        if not success:
            error_count += 1
            state.consecutive_failures += 1
        else:
            state.consecutive_failures = 0

        if state.drive_sync is not None and state.output_dir is not None:
            state.drive_sync.maybe_sync(state.output_dir)
        _write_status(config_path, state, poll_interval_seconds)

        logging.info(
            "Poll #%d complete. Total: %d polls, %d errors",
            poll_count, poll_count, error_count,
        )

        # Calculate sleep time, but check for shutdown/end periodically
        if (end_time is None or datetime.now() < end_time) and not shutdown.should_exit:
            next_poll = datetime.now() + timedelta(seconds=poll_interval_seconds)
            logging.info("Next poll at %s", next_poll.strftime("%H:%M:%S"))

            # Sleep in smaller chunks so we can respond to shutdown signals
            # While waiting for re-authorization, wake up early as soon as an
            # authorization code arrives (codes expire within minutes).
            sleep_chunk = 10  # seconds
            remaining = poll_interval_seconds
            while remaining > 0 and not shutdown.should_exit:
                time.sleep(min(sleep_chunk, remaining))
                remaining -= sleep_chunk
                if state.reauth is not None and state.reauth.code_available():
                    logging.info("Authorization code received; polling now.")
                    break

    state.end_reauth()
    if state.drive_sync is not None and state.output_dir is not None:
        state.drive_sync.maybe_sync(state.output_dir, force=True)  # final copy on shutdown

    if shutdown.should_exit:
        logging.info("Scheduler stopped by user request.")
    elif end_time is not None:
        logging.info("Scheduler completed %s-hour run.", duration_hours)

    logging.info(
        "Final stats: %d polls completed, %d errors",
        poll_count, error_count,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the Nest poller continuously for 24 hours."
    )
    parser.add_argument(
        "--config",
        type=pathlib.Path,
        default=pathlib.Path(__file__).parent.parent / "config.json",
        help="Path to configuration JSON file.",
    )
    parser.add_argument(
        "--log-dir",
        type=pathlib.Path,
        default=pathlib.Path(__file__).parent.parent / "logs",
        help="Directory for log files.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level.",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=DEFAULT_RUN_DURATION_HOURS,
        help="How many hours to run (default: 24). Pass 0 to run indefinitely (NSSM service mode).",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=POLL_INTERVAL_SECONDS,
        help="Seconds between polls (default: 300 = 5 minutes).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    log_file = setup_logging(args.log_dir, args.log_level)
    logging.info("Logging to %s", log_file)

    shutdown = GracefulShutdown()

    duration = None if args.duration == RUN_FOREVER else args.duration
    try:
        run_scheduler(
            config_path=args.config,
            duration_hours=duration,
            poll_interval_seconds=args.interval,
            shutdown=shutdown,
        )
    except Exception as exc:
        logging.exception("Fatal error in scheduler: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
