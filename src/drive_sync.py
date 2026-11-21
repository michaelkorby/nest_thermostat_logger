"""Copy the poller's CSVs and log into a Google Drive for Desktop folder.

The poller runs from a local folder (e.g. C:\\NestLogger) so it never depends on
Google Drive for Desktop, which only runs while you are logged in. This module
periodically copies the data files into a folder inside "My Drive"; Drive for
Desktop then uploads them whenever it is running. If you are logged out, the
copies still land on disk and upload at your next login.

Configured in config.json:
    "drive_sync": {
        "dir": "C:\\\\Users\\\\you\\\\My Drive\\\\Code\\\\nest_thermostat_logger",
        "interval_minutes": 30
    }
Files are copied into <dir>\\logs. <dir>\\pending_auth_code.txt is also watched
for sign-in codes (see oauth_reauth.NonBlockingReauth).
"""
from __future__ import annotations

import logging
import os
import pathlib
import shutil
import time
from typing import Optional

FILES_TO_COPY = ("*.csv", "poller.log")


class DriveFolderSync:
    def __init__(self, drive_dir: pathlib.Path, interval_minutes: float = 30) -> None:
        self.drive_dir = drive_dir
        self.dest_dir = drive_dir / "logs"
        self.interval_seconds = max(60.0, interval_minutes * 60)
        self.last_sync_at: Optional[float] = None
        self.last_error: Optional[str] = None
        self._last_attempt_at = 0.0

    @property
    def pending_code_file(self) -> pathlib.Path:
        return self.drive_dir / "pending_auth_code.txt"

    def maybe_sync(self, source_dir: pathlib.Path, force: bool = False) -> None:
        """Copy changed files if the interval has elapsed. Never raises."""
        now = time.time()
        if not force and now - self._last_attempt_at < self.interval_seconds:
            return
        self._last_attempt_at = now
        try:
            if not self.drive_dir.exists():
                raise OSError(f"Google Drive folder not found: {self.drive_dir}")
            self.dest_dir.mkdir(parents=True, exist_ok=True)
            copied = 0
            for pattern in FILES_TO_COPY:
                for src in source_dir.glob(pattern):
                    dest = self.dest_dir / src.name
                    try:
                        if dest.exists() and dest.stat().st_mtime >= src.stat().st_mtime:
                            continue
                    except OSError:
                        pass
                    tmp = dest.with_name(dest.name + ".tmp")
                    shutil.copy2(src, tmp)
                    os.replace(tmp, dest)
                    copied += 1
            self.last_sync_at = now
            self.last_error = None
            logging.info("Copied %d file(s) to Google Drive folder %s", copied, self.dest_dir)
        except Exception as exc:
            self.last_error = f"Could not copy data to Google Drive folder: {exc}"
            logging.warning("%s", self.last_error)
