from __future__ import annotations

from pathlib import Path

import pandas as pd


REQUIRED_COLUMNS = {"date", "close"}


def load_price_csv(path: str | Path) -> pd.DataFrame:
    """Load OHLCV-style data and normalize the index/columns."""
    frame = pd.read_csv(path)
    frame.columns = [column.strip().lower() for column in frame.columns]

    missing = REQUIRED_COLUMNS.difference(frame.columns)
    if missing:
        missing_text = ", ".join(sorted(missing))
        raise ValueError(f"CSV is missing required columns: {missing_text}")

    frame["date"] = pd.to_datetime(frame["date"], utc=False)
    frame = frame.sort_values("date").drop_duplicates("date").set_index("date")
    for column in ("open", "high", "low", "close", "volume"):
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["close"])

    if frame.empty:
        raise ValueError("CSV does not contain any usable close prices")

    return frame


def fetch_yahoo(symbol: str, start: str, end: str | None = None) -> pd.DataFrame:
    """Fetch daily data from Yahoo Finance using yfinance."""
    try:
        import yfinance as yf
    except ImportError as exc:
        raise RuntimeError("Install yfinance or use --csv with local data") from exc

    data = yf.download(symbol, start=start, end=end, auto_adjust=True, progress=False)
    if data.empty:
        raise ValueError(f"No Yahoo Finance data returned for {symbol}")

    if isinstance(data.columns, pd.MultiIndex):
        data.columns = [column[0] for column in data.columns]

    data = data.rename(
        columns={
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
    )
    data.index.name = "date"
    return data.reset_index()


def save_price_csv(frame: pd.DataFrame, path: str | Path) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output, index=False)
