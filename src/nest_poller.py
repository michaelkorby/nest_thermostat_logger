from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import logging
import pathlib
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional
from zoneinfo import ZoneInfo

import requests

TOKEN_URL = "https://oauth2.googleapis.com/token"
DEVICES_URL_TEMPLATE = (
    "https://smartdevicemanagement.googleapis.com/v1/enterprises/{project_id}/devices"
)


class NestPollerError(RuntimeError):
    """Raised when the poller encounters a recoverable error."""


@dataclass
class Config:
    project_id: str
    client_id: str
    client_secret: str
    refresh_token: str
    output_dir: pathlib.Path
    temperature_scale: str = "fahrenheit"
    timezone: ZoneInfo = ZoneInfo("America/New_York")

    @classmethod
    def from_dict(cls, data: Dict[str, Any], base_dir: pathlib.Path) -> "Config":
        missing = [field for field in ("project_id", "client_id", "client_secret", "refresh_token") if field not in data]
        if missing:
            raise NestPollerError(f"Config missing required fields: {', '.join(missing)}")

        output_dir = pathlib.Path(data.get("output_dir", "logs"))
        if not output_dir.is_absolute():
            output_dir = base_dir / output_dir

        temperature_scale = data.get("temperature_scale", "fahrenheit").lower()
        if temperature_scale not in ("fahrenheit", "celsius"):
            raise NestPollerError("temperature_scale must be either 'fahrenheit' or 'celsius'")

        timezone_name = data.get("timezone", "America/New_York")
        try:
            timezone = ZoneInfo(timezone_name)
        except Exception as exc:  # pragma: no cover - invalid tz config
            raise NestPollerError(f"Invalid timezone in config: {timezone_name}") from exc

        return cls(
            project_id=data["project_id"],
            client_id=data["client_id"],
            client_secret=data["client_secret"],
            refresh_token=data["refresh_token"],
            output_dir=output_dir,
            temperature_scale=temperature_scale,
            timezone=timezone,
        )


def load_config(path: pathlib.Path) -> Config:
    try:
        with path.open("r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError as exc:
        raise NestPollerError(f"Config file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise NestPollerError(f"Invalid JSON config file: {path}") from exc

    return Config.from_dict(raw, base_dir=path.parent)


def refresh_access_token(config: Config) -> str:
    payload = {
        "client_id": config.client_id,
        "client_secret": config.client_secret,
        "refresh_token": config.refresh_token,
        "grant_type": "refresh_token",
    }
    response = requests.post(TOKEN_URL, data=payload, timeout=15)
    if response.status_code != 200:
        raise NestPollerError(
            f"Failed to refresh access token: {response.status_code} {response.text}"
        )
    token_data = response.json()
    access_token = token_data.get("access_token")
    if not access_token:
        raise NestPollerError("Missing access_token in refresh response")
    return access_token


def fetch_devices(config: Config, access_token: str) -> Iterable[Dict[str, Any]]:
    url = DEVICES_URL_TEMPLATE.format(project_id=config.project_id)
    headers = {"Authorization": f"Bearer {access_token}"}
    response = requests.get(url, headers=headers, timeout=15)
    if response.status_code != 200:
        raise NestPollerError(
            f"Failed to fetch devices: {response.status_code} {response.text}"
        )
    payload = response.json()
    devices = payload.get("devices", [])
    logging.debug("Fetched %d devices from Nest SDM API", len(devices))
    return devices


def to_temperature(value_celsius: Optional[float], scale: str) -> Optional[float]:
    if value_celsius is None:
        return None
    if scale == "celsius":
        return round(value_celsius)
    return round((value_celsius * 9 / 5) + 32)


def sanitize_name(name: str) -> str:
    safe = "".join(ch if ch.isalnum() else "_" for ch in name)
    safe = "_".join(filter(None, safe.split("_")))
    return safe.lower()


def extract_thermostat_rows(devices: Iterable[Dict[str, Any]], config: Config) -> Dict[str, Dict[str, Any]]:
    rows: Dict[str, Dict[str, Any]] = {}
    local_time = dt.datetime.now(dt.timezone.utc).astimezone(config.timezone)
    date_str = local_time.date().isoformat()
    time_str = local_time.strftime("%H:%M")

    for device in devices:
        device_type = device.get("type", "")
        if not device_type.endswith("THERMOSTAT"):
            continue

        traits = device.get("traits", {})
        info = traits.get("sdm.devices.traits.Info", {})
        parent_relations = device.get("parentRelations", []) or []

        readable_name = info.get("customName")
        if not readable_name:
            readable_name = _first_parent_display_name(parent_relations)
        if not readable_name:
            readable_name = device.get("name", "").split("/")[-1] or "thermostat"

        ambient_c = _get_nested(traits, ["sdm.devices.traits.Temperature", "ambientTemperatureCelsius"])
        humidity = _get_nested(traits, ["sdm.devices.traits.Humidity", "ambientHumidityPercent"])
        setpoints = traits.get("sdm.devices.traits.ThermostatTemperatureSetpoint", {})
        hvac_status = _get_nested(traits, ["sdm.devices.traits.ThermostatHvac", "status"])

        row = {
            "Date": date_str,
            "Time (ET)": time_str,
            "Temperature at Thermostat or Sensor": to_temperature(
                ambient_c, config.temperature_scale
            ),
            "Humidity": humidity,
            "Heat Setpoint": to_temperature(
                setpoints.get("heatCelsius"), config.temperature_scale
            )
            if "heatCelsius" in setpoints
            else None,
            "Cool Setpoint": to_temperature(
                setpoints.get("coolCelsius"), config.temperature_scale
            )
            if "coolCelsius" in setpoints
            else None,
            "HVAC Status": hvac_status,
        }

        rows[sanitize_name(readable_name)] = row
        logging.debug("Prepared row for device '%s': %s", readable_name, row)
    return rows


def _first_parent_display_name(parent_relations: Iterable[Dict[str, Any]]) -> Optional[str]:
    for relation in parent_relations:
        display_name = relation.get("displayName")
        if display_name:
            return display_name
    return None


def _get_nested(data: Dict[str, Any], path: Iterable[str]) -> Optional[Any]:
    current = data
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def write_rows(rows: Dict[str, Dict[str, Any]], config: Config) -> None:
    config.output_dir.mkdir(parents=True, exist_ok=True)
    header = [
        "Date",
        "Time (ET)",
        "Temperature at Thermostat or Sensor",
        "Humidity",
        "Heat Setpoint",
        "Cool Setpoint",
        "HVAC Status",
    ]

    for device_slug, row in rows.items():
        file_path = config.output_dir / f"{device_slug}.csv"
        is_new_file = not file_path.exists()

        with file_path.open("a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=header)
            if is_new_file:
                writer.writeheader()
            writer.writerow(row)
        logging.info("Logged data for %s to %s", device_slug, file_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Poll Nest Smart Device Management API for thermostat data."
    )
    parser.add_argument(
        "--config",
        type=pathlib.Path,
        default=pathlib.Path("config.json"),
        help="Path to configuration JSON file.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging level (DEBUG, INFO, WARNING, ERROR).",
    )
    parser.add_argument(
        "--log-file",
        type=pathlib.Path,
        help="Optional path to a log file. When set, the file is overwritten each run.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    log_handlers = []
    if args.log_file:
        log_handlers.append(
            logging.FileHandler(args.log_file, mode="w", encoding="utf-8")
        )
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=log_handlers or None,
    )

    try:
        config = load_config(args.config)
        access_token = refresh_access_token(config)
        devices = fetch_devices(config, access_token)
        rows = extract_thermostat_rows(devices, config)

        if not rows:
            logging.warning("No thermostat devices found.")
            return

        write_rows(rows, config)
    except NestPollerError as exc:
        logging.error("Poller error: %s", exc)
    except requests.RequestException as exc:
        logging.error("Network error while communicating with Nest API: %s", exc)


if __name__ == "__main__":
    main()
