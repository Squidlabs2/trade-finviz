import pandas as pd

from trade_strategy.backtest import (
    BacktestConfig,
    build_comparison,
    format_comparison,
    run_backtest,
    slice_backtest_result,
    summarize_buy_hold,
    summarize_performance,
)
from trade_strategy.strategies import trend_volatility_target_signal


def test_backtest_generates_equity_curve():
    dates = pd.date_range("2020-01-01", periods=260, freq="B")
    close = pd.Series([100 + index * 0.1 for index in range(len(dates))], index=dates)

    signal = trend_volatility_target_signal(
        close,
        fast_window=10,
        slow_window=30,
        vol_window=10,
        target_vol=0.10,
    )
    result = run_backtest(signal, BacktestConfig(initial_capital=10_000, cost_bps=1.0))
    stats = summarize_performance(result)

    assert result["equity"].iloc[-1] > 0
    assert stats.trades > 0
    assert "buy_hold_equity" in result
    assert "buy_hold_drawdown" in result


def test_strategy_is_flat_before_indicators_are_available():
    dates = pd.date_range("2020-01-01", periods=40, freq="B")
    close = pd.Series(range(100, 140), index=dates)

    signal = trend_volatility_target_signal(
        close,
        fast_window=5,
        slow_window=20,
        vol_window=5,
    )

    assert signal["target_exposure"].iloc[:20].eq(0.0).all()


def test_buy_hold_summary_uses_asset_return():
    dates = pd.date_range("2020-01-01", periods=80, freq="B")
    close = pd.Series([100 + index for index in range(len(dates))], index=dates)
    signal = trend_volatility_target_signal(
        close,
        fast_window=5,
        slow_window=20,
        vol_window=5,
    )

    result = run_backtest(signal)
    buy_hold = summarize_buy_hold(result)

    assert buy_hold.label == "Buy & hold"
    assert buy_hold.exposure_mean == 1.0
    assert buy_hold.total_return > 0


def test_comparison_format_includes_strategy_and_benchmark():
    dates = pd.date_range("2020-01-01", periods=80, freq="B")
    close = pd.Series([100 + index for index in range(len(dates))], index=dates)
    signal = trend_volatility_target_signal(
        close,
        fast_window=5,
        slow_window=20,
        vol_window=5,
    )

    result = run_backtest(signal)
    comparison = build_comparison("TEST", result)
    table = format_comparison([comparison])

    assert "TEST" in table
    assert "Strategy" in table
    assert "Buy & hold" in table


def test_slice_backtest_result_preserves_prior_indicator_history():
    dates = pd.date_range("2020-01-01", periods=300, freq="B")
    close = pd.Series([100 + index * 0.2 for index in range(len(dates))], index=dates)
    signal = trend_volatility_target_signal(
        close,
        fast_window=10,
        slow_window=50,
        vol_window=10,
    )
    result = run_backtest(signal)

    sliced = slice_backtest_result(result, start="2020-10-01", end="2020-12-31")

    assert not sliced.empty
    assert sliced["target_exposure"].sum() > 0
    assert sliced["strategy_return"].iloc[0] == 0.0
