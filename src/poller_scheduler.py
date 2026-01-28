"""
Long-running poller scheduler that runs for 24 hours.

Start this script once (e.g., via Windows Task Scheduler at midnight) and it will
poll the Nest API every 5 minutes until the 24-hour window expires. This is more
reliable than depending on Task Scheduler to fire every 5 minutes.
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
from src.oauth_reauth import (
    ReauthorizationInProgressError,
    perform_reauthorization,
    update_config_refresh_token,
)

POLL_INTERVAL_SECONDS = 5 * 60  # 5 minutes
DEFAULT_RUN_DURATION_HOURS = 24


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


def run_single_poll(config_path: pathlib.Path) -> bool:
    """
    Run a single poll cycle.

    Returns True if successful, False if an error occurred.
    """
    try:
        config = nest_poller.load_config(config_path)

        try:
            access_token, new_refresh_token = nest_poller.refresh_access_token(config)
        except nest_poller.RefreshTokenExpiredError as exc:
            logging.warning("Refresh token expired: %s", exc)
            logging.info("Starting automatic re-authorization...")
            access_token, new_refresh_token = perform_reauthorization(
                config_path=config_path,
                project_id=config.project_id,
                client_id=config.client_id,
                client_secret=config.client_secret,
                email_config=config.email,
            )
            config = nest_poller.load_config(config_path)

        # Save new refresh token if provided
        if new_refresh_token:
            update_config_refresh_token(config_path, new_refresh_token)
            logging.info("Updated config.json with new refresh token from Google")

        devices = nest_poller.fetch_devices(config, access_token)
        rows = nest_poller.extract_thermostat_rows(devices, config)

        if not rows:
            logging.warning("No thermostat devices found.")
            return True  # Not an error, just no devices

        nest_poller.write_rows(rows, config)
        return True

    except ReauthorizationInProgressError as exc:
        logging.warning("%s", exc)
        return True  # Not a failure, just waiting for user
    except nest_poller.NestPollerError as exc:
        logging.error("Poller error: %s", exc)
        return False
    except Exception as exc:
        logging.exception("Unexpected error during poll: %s", exc)
        return False


def run_scheduler(
    config_path: pathlib.Path,
    duration_hours: float,
    poll_interval_seconds: int,
    shutdown: GracefulShutdown,
) -> None:
    """Run the polling loop for the specified duration."""

    end_time = datetime.now() + timedelta(hours=duration_hours)
    poll_count = 0
    error_count = 0

    logging.info(
        "Starting poller scheduler. Will run until %s (%.1f hours)",
        end_time.strftime("%Y-%m-%d %H:%M:%S"),
        duration_hours,
    )
    logging.info("Poll interval: %d seconds (%d minutes)", poll_interval_seconds, poll_interval_seconds // 60)

    while datetime.now() < end_time and not shutdown.should_exit:
        poll_count += 1
        logging.info("=== Poll #%d starting ===", poll_count)

        success = run_single_poll(config_path)
        if not success:
            error_count += 1

        logging.info(
            "Poll #%d complete. Total: %d polls, %d errors",
            poll_count, poll_count, error_count,
        )

        # Calculate sleep time, but check for shutdown/end periodically
        if datetime.now() < end_time and not shutdown.should_exit:
            next_poll = datetime.now() + timedelta(seconds=poll_interval_seconds)
            logging.info("Next poll at %s", next_poll.strftime("%H:%M:%S"))

            # Sleep in smaller chunks so we can respond to shutdown signals
            sleep_chunk = 10  # seconds
            remaining = poll_interval_seconds
            while remaining > 0 and not shutdown.should_exit:
                time.sleep(min(sleep_chunk, remaining))
                remaining -= sleep_chunk

    if shutdown.should_exit:
        logging.info("Scheduler stopped by user request.")
    else:
        logging.info("Scheduler completed 24-hour run.")

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
        help="How many hours to run (default: 24).",
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

    try:
        run_scheduler(
            config_path=args.config,
            duration_hours=args.duration,
            poll_interval_seconds=args.interval,
            shutdown=shutdown,
        )
    except Exception as exc:
        logging.exception("Fatal error in scheduler: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
