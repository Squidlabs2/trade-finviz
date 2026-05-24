import pandas as pd

from trade_strategy.backtest import BacktestConfig, summarize_buy_hold, summarize_performance
from trade_strategy.rotation import (
    SectorRotationConfig,
    latest_sector_signal,
    run_sector_rotation_backtest,
)


def _price_frame(close_values):
    dates = pd.date_range("2020-01-01", periods=len(close_values), freq="B")
    close = pd.Series(close_values, index=dates)
    return pd.DataFrame(
        {
            "open": close.shift(1).fillna(close.iloc[0]),
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": 1_000_000,
        },
        index=dates,
    )


def test_sector_rotation_generates_trades_and_equity():
    first = [100 + index * 0.05 for index in range(320)]
    second = [100 + index * 0.02 for index in range(160)] + [103 + index * 0.15 for index in range(160)]
    benchmark = [100 + index * 0.04 for index in range(320)]
    config = SectorRotationConfig(
        score_lookback_weeks=2,
        volume_lookback_weeks=4,
        fast_window=10,
        slow_window=30,
        vol_window=10,
    )

    result, trades = run_sector_rotation_backtest(
        {"AAA": _price_frame(first), "BBB": _price_frame(second)},
        _price_frame(benchmark),
        config=config,
        backtest_config=BacktestConfig(initial_capital=10_000),
    )
    stats = summarize_performance(result)
    benchmark_stats = summarize_buy_hold(result)

    assert not trades.empty
    assert result["equity"].iloc[-1] > 0
    assert stats.trades > 0
    assert benchmark_stats.label == "Buy & hold"


def test_latest_sector_signal_uses_most_recent_weekly_score():
    first = [100 + index * 0.05 for index in range(320)]
    second = [100 + index * 0.02 for index in range(160)] + [103 + index * 0.15 for index in range(160)]
    config = SectorRotationConfig(
        score_lookback_weeks=2,
        volume_lookback_weeks=4,
        fast_window=10,
        slow_window=30,
        vol_window=10,
    )

    signal = latest_sector_signal(
        {"AAA": _price_frame(first), "BBB": _price_frame(second)},
        config,
    )

    assert signal["selected_symbol"] in {"AAA", "BBB"}
    assert signal["target_exposure"] >= 0.0


def test_sector_rotation_can_hold_top_three_sectors():
    first = [100 + index * 0.06 for index in range(320)]
    second = [100 + index * 0.05 for index in range(320)]
    third = [100 + index * 0.04 for index in range(320)]
    fourth = [100 + index * 0.01 for index in range(320)]
    benchmark = [100 + index * 0.04 for index in range(320)]
    config = SectorRotationConfig(
        score_lookback_weeks=2,
        volume_lookback_weeks=4,
        top_n=3,
        fast_window=10,
        slow_window=30,
        vol_window=10,
    )

    result, trades = run_sector_rotation_backtest(
        {
            "AAA": _price_frame(first),
            "BBB": _price_frame(second),
            "CCC": _price_frame(third),
            "DDD": _price_frame(fourth),
        },
        _price_frame(benchmark),
        config=config,
    )

    selected_counts = trades["selected_symbols"].str.split(",").str.len()
    assert selected_counts.eq(3).all()
    assert result["target_exposure"].max() <= 1.0
    assert result["selected_symbols"].str.contains(",").any()
