from __future__ import annotations

import argparse
from urllib.error import URLError

import pandas as pd

from trade_strategy import cli


def _price_frame(close_values, volume_values=None):
    dates = pd.date_range("2020-01-01", periods=len(close_values), freq="B")
    close = pd.Series(close_values, index=dates)
    if volume_values is None:
        volume_values = [1_000_000 for _ in close_values]
    volume = pd.Series(volume_values, index=dates)
    return pd.DataFrame(
        {
            "date": dates,
            "open": close.shift(1).fillna(close.iloc[0]).values,
            "high": (close * 1.01).values,
            "low": (close * 0.99).values,
            "close": close.values,
            "volume": volume.values,
        }
    )


def test_weekly_scan_raises_when_network_sources_fail(tmp_path, monkeypatch):
    sector_values = [100 + index * 0.05 for index in range(260)]
    for symbol in cli.SPDR_SECTOR_SYMBOLS:
        _price_frame(sector_values).to_csv(tmp_path / f"{symbol}.csv", index=False)

    local_universe = pd.DataFrame({"symbol": ["TEST"], "sector_etf": ["XLK"]})
    local_universe.to_csv(tmp_path / "universe.csv", index=False)
    _price_frame([50 + index * 0.08 for index in range(260)]).to_csv(tmp_path / "TEST.csv", index=False)

    def fail_finviz(*_args, **_kwargs):
        raise URLError("finviz down")

    def fail_holdings(*_args, **_kwargs):
        raise URLError("holdings down")

    monkeypatch.setattr(cli, "fetch_finviz_sector_candidates", fail_finviz)
    monkeypatch.setattr(cli, "fetch_sector_holdings_universe", fail_holdings)

    args = argparse.Namespace(
        data_dir=str(tmp_path),
        universe=str(tmp_path / "universe.csv"),
        use_sector_holdings=False,
        use_finviz_sector_screener=True,
        finviz_rsi_mode="both",
        max_holdings_per_sector=100,
        holdings_universe_output=str(tmp_path / "sector_holdings_universe.csv"),
        holdings=None,
        refresh_data=False,
        fetch_start="2020-01-01",
        sma_window=50,
        long_sma_window=200,
        volume_window=50,
        min_volume_ratio=1.25,
        momentum_lookback_days=20,
        top_sector_count=3,
        leading_sector_stocks_only=False,
        max_results=20,
        output=str(tmp_path / "weekly_scan.csv"),
        notify=False,
        ntfy_url=None,
        ntfy_topic=None,
        ntfy_token=None,
    )

    try:
        cli.run_weekly_scan_command(args)
    except URLError:
        pass
    else:
        raise AssertionError("Expected URLError when both network sources fail")
