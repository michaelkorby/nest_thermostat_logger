"""Shared poller health status, written by the poller and read by the dashboard.

The poller rewrites poller_status.json (next to config.json) after every poll.
The dashboard reads it to show whether data is flowing, whether the Nest
sign-in has expired, and whether the Google Drive copy is up to date.
"""
from __future__ import annotations

import json
import os
import pathlib
import time
from typing import Any, Dict

STATUS_FILENAME = "poller_status.json"


def status_path(base_dir: pathlib.Path) -> pathlib.Path:
    return base_dir / STATUS_FILENAME


def read_status(base_dir: pathlib.Path) -> Dict[str, Any]:
    """Return the last written status, or {} if missing/unreadable."""
    for _ in range(3):
        try:
            return json.loads(status_path(base_dir).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            time.sleep(0.2)  # mid-replace; try again
    return {}


def write_status(base_dir: pathlib.Path, status: Dict[str, Any]) -> None:
    """Atomically write the status file (best effort; never raises)."""
    target = status_path(base_dir)
    tmp = target.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(status, indent=2), encoding="utf-8")
        for _ in range(5):
            try:
                os.replace(tmp, target)
                return
            except PermissionError:  # reader has it open on Windows
                time.sleep(0.2)
    except OSError:
        pass
