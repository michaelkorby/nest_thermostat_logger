from __future__ import annotations

import datetime as dt
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

BASE_DIR = Path(__file__).resolve().parents[1]
LOG_DIR = (BASE_DIR / "logs").resolve()
DATE_FORMAT = "%Y-%m-%d"


def list_log_files() -> Dict[str, Path]:
    if not LOG_DIR.exists():
        return {}
    return {
        file_path.stem.replace("_", " ").title(): file_path
        for file_path in sorted(LOG_DIR.glob("*.csv"))
    }


@lru_cache(maxsize=32)
def load_log(filename: str) -> pd.DataFrame:
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


def main() -> None:
    st.set_page_config(page_title="Nest Thermostat Dashboard", layout="wide")
    st.title("Nest Thermostat Dashboard")
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

        if isinstance(date_range, tuple):
            start_date, end_date = date_range
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

