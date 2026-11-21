from __future__ import annotations

import datetime as dt
import sys
import time
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from src.poller_status import read_status  # noqa: E402

PENDING_AUTH_CODE_FILE = BASE_DIR / "pending_auth_code.txt"
LOG_DIR = (BASE_DIR / "logs").resolve()
DATE_FORMAT = "%Y-%m-%d"


def list_log_files() -> Dict[str, Path]:
    if not LOG_DIR.exists():
        return {}
    return {
        file_path.stem.replace("_", " ").title(): file_path
        for file_path in sorted(LOG_DIR.glob("*.csv"))
    }


def load_log(filename: str) -> pd.DataFrame:
    # Key the cache on the file's modification time so new poller data shows
    # up; caching by filename alone kept the first-loaded data forever.
    mtime = (LOG_DIR / filename).stat().st_mtime
    return _load_log_cached(filename, mtime).copy()


@lru_cache(maxsize=32)
def _load_log_cached(filename: str, mtime: float) -> pd.DataFrame:
    file_path = LOG_DIR / filename
    df = pd.read_csv(file_path)

    if "Date" not in df or "Time (ET)" not in df:
        raise ValueError(f"CSV missing date/time columns: {file_path}")

    combined = df["Date"].astype(str) + " " + df["Time (ET)"].astype(str)
    df["timestamp"] = pd.to_datetime(combined, errors="coerce")
    df = df.dropna(subset=["timestamp"])
    df = df.sort_values("timestamp")
    df = df.set_index("timestamp")

    numeric_columns = [
        "Temperature at Thermostat or Sensor",
        "Heat Setpoint",
        "Cool Setpoint",
        "Outdoor Temperature",
        "Humidity",
    ]
    for column in numeric_columns:
        if column in df:
            df[column] = pd.to_numeric(df[column], errors="coerce")

    return df


def hvac_indicator(df: pd.DataFrame) -> pd.Series:
    hvac = df.get("HVAC Status", pd.Series(dtype=object)).fillna("OFF")
    return hvac.apply(lambda value: 1 if value in ("HEATING", "COOLING") else 0)


def filter_by_range(df: pd.DataFrame, start: dt.date, end: dt.date) -> pd.DataFrame:
    start_ts = pd.to_datetime(dt.datetime.combine(start, dt.time.min))
    # include entire end day
    end_ts = pd.to_datetime(dt.datetime.combine(end, dt.time.max))
    return df.loc[(df.index >= start_ts) & (df.index <= end_ts)]


def build_chart(df: pd.DataFrame) -> go.Figure:
    figure = go.Figure()

    figure.add_trace(
        go.Scatter(
            x=df.index,
            y=df.get("Temperature at Thermostat or Sensor"),
            mode="lines",
            name="Ambient Temperature",
            line=dict(color="#1f77b4"),
        )
    )

    if "Heat Setpoint" in df:
        figure.add_trace(
            go.Scatter(
                x=df.index,
                y=df["Heat Setpoint"],
                mode="lines",
                name="Heat Setpoint",
                line=dict(color="#d62728", dash="dot"),
            )
        )

    if "Cool Setpoint" in df:
        figure.add_trace(
            go.Scatter(
                x=df.index,
                y=df["Cool Setpoint"],
                mode="lines",
                name="Cool Setpoint",
                line=dict(color="#2ca02c", dash="dash"),
            )
        )

    if "Outdoor Temperature" in df:
        figure.add_trace(
            go.Scatter(
                x=df.index,
                y=df["Outdoor Temperature"],
                mode="lines",
                name="Outdoor Temperature",
                line=dict(color="#ff7f0e"),
            )
        )

    hvac = hvac_indicator(df)
    if not hvac.empty:
        figure.add_trace(
            go.Scatter(
                x=df.index,
                y=hvac,
                name="HVAC On",
                mode="lines",
                line=dict(shape="hv", color="#9467bd", width=0),
                fill="tozeroy",
                opacity=0.2,
                yaxis="y2",
            )
        )

    figure.update_layout(
        yaxis=dict(title="Temperature (°F)"),
        yaxis2=dict(
            title="HVAC Active",
            overlaying="y",
            side="right",
            range=[0, 1.2],
            showgrid=False,
        ),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=60, r=60, t=60, b=60),
    )

    return figure


def _ago(ts: Optional[float]) -> str:
    if not ts:
        return "never"
    minutes = (time.time() - ts) / 60
    if minutes < 1:
        return "just now"
    if minutes < 90:
        return f"{minutes:.0f} min ago"
    if minutes < 48 * 60:
        return f"{minutes / 60:.1f} hours ago"
    return f"{minutes / 1440:.1f} days ago"


def _when(ts: Optional[float]) -> str:
    if not ts:
        return "never"
    return dt.datetime.fromtimestamp(ts).strftime("%a %b %d, %I:%M %p")


def render_poller_health() -> None:
    """Show a banner describing whether the poller is healthy."""
    status = read_status(BASE_DIR)
    if not status:
        st.warning(
            "No poller status found. The NestPoller service may not be running, or it "
            "hasn't finished its first poll since being updated."
        )
        return

    interval = status.get("poll_interval_seconds") or 300
    updated_at = status.get("updated_at")
    last_success = status.get("last_success_at")
    problems = 0

    # 1. Poller not checking in at all (service stopped / crashed / laptop asleep)
    if updated_at and time.time() - updated_at > 3 * interval:
        problems += 1
        st.error(
            f"**The poller hasn't checked in since {_when(updated_at)} ({_ago(updated_at)}).** "
            "The NestPoller service may be stopped. From an Administrator prompt run: "
            r"`C:\nssm\win64\nssm.exe restart NestPoller`, then check `logs\poller.log`."
        )

    # 2. Nest sign-in expired
    if status.get("reauth_needed"):
        problems += 1
        with st.container(border=True):
            st.error(
                f"**Nest sign-in expired {_ago(status.get('reauth_since'))}. "
                f"No data has been logged since {_when(last_success)}.**"
            )
            if status.get("last_error") and "rejected" in status["last_error"]:
                st.warning(status["last_error"])
            auth_url = status.get("auth_url")
            if auth_url:
                st.markdown(
                    "1. Click **Sign in to Nest** and approve access to all thermostats.\n"
                    "2. Google sends you to a `localhost:8085` page. Unless you're on the "
                    "logger laptop itself, that page **won't load - that's expected**.\n"
                    "3. Copy the whole address from the browser's address bar, paste it below, "
                    "and click **Submit**. Do this right away - the code expires within minutes."
                )
                st.link_button("Sign in to Nest", auth_url, type="primary")
            with st.form("auth_code_form", clear_on_submit=True):
                pasted = st.text_input(
                    "Redirect URL (http://localhost:8085/?code=...)",
                    placeholder="http://localhost:8085/?code=4/0A...&scope=...",
                )
                if st.form_submit_button("Submit"):
                    if "code=" not in pasted and not pasted.strip().startswith("4/"):
                        st.error("That doesn't look like the redirect URL. It should contain `code=`.")
                    else:
                        try:
                            PENDING_AUTH_CODE_FILE.write_text(pasted.strip(), encoding="utf-8")
                            st.success(
                                "Saved. The poller checks every 10 seconds - refresh this page in "
                                "about 30 seconds to confirm the sign-in worked."
                            )
                        except OSError as exc:
                            st.error(f"Couldn't save the code: {exc}")

    # 3. Other poll errors
    elif status.get("last_error"):
        problems += 1
        st.warning(
            f"**The last poll failed** ({status.get('consecutive_failures', 1)} in a row; "
            f"last successful reading {_ago(last_success)}): {status['last_error']}"
        )

    # 4. Google Drive copy
    if status.get("drive_sync_error"):
        problems += 1
        st.warning(f"**Google Drive copy is failing:** {status['drive_sync_error']}")

    if problems == 0:
        drive = ""
        if status.get("drive_sync_dir"):
            drive = f" · copied to Google Drive folder {_ago(status.get('drive_sync_last_at'))}"
        st.success(f"Poller healthy · last reading {_ago(last_success)}{drive}", icon="✅")


def main() -> None:
    st.set_page_config(page_title="Nest Thermostat Dashboard", layout="wide")
    st.title("Nest Thermostat Dashboard")
    render_poller_health()
    st.caption("Choose a thermostat and time range to explore logged data.")

    log_files = list_log_files()
    if not log_files:
        st.error(f"No CSV logs found in {LOG_DIR}")
        st.stop()

    thermostat_names: List[str] = list(log_files.keys())
    default_thermostat = thermostat_names[0]

    with st.sidebar:
        st.header("Filters")
        selected_name = st.selectbox("Thermostat", thermostat_names, index=0)
        today = dt.date.today()
        default_start = today - dt.timedelta(days=7)
        date_range = st.date_input(
            "Date range",
            value=(default_start, today),
            min_value=today - dt.timedelta(days=365),
            max_value=today,
        )

        if isinstance(date_range, (tuple, list)) and len(date_range) == 2:
            start_date, end_date = date_range
        elif isinstance(date_range, (tuple, list)):
            # Mid-selection: only the start date has been picked so far
            start_date = end_date = date_range[0] if date_range else today
        else:
            start_date = date_range
            end_date = date_range

        show_humidity = st.checkbox("Show humidity", value=False)

    csv_filename = log_files[selected_name].name
    df = load_log(csv_filename)

    if df.empty:
        st.warning("No data available for the selected thermostat.")
        st.stop()

    filtered_df = filter_by_range(df, start_date, end_date)

    if filtered_df.empty:
        st.info("No log entries in the selected date range.")
        st.stop()

    figure = build_chart(filtered_df)

    if show_humidity and "Humidity" in filtered_df:
        figure.add_trace(
            go.Scatter(
                x=filtered_df.index,
                y=filtered_df["Humidity"],
                mode="lines",
                name="Humidity (%)",
                yaxis="y3",
                line=dict(color="#8c564b", dash="longdash"),
            )
        )
        figure.update_layout(
            yaxis3=dict(
                title="Humidity (%)",
                overlaying="y",
                side="left",
                position=0.02,
                range=[0, 100],
            )
        )

    st.plotly_chart(figure, width="stretch")

    with st.expander("Data preview"):
        st.dataframe(
            filtered_df.reset_index().rename(columns={"index": "Timestamp"}),
            width="stretch",
        )


if __name__ == "__main__":
    main()

