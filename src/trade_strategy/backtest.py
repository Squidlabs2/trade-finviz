from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from trade_strategy.indicators import TRADING_DAYS_PER_YEAR, daily_returns


@dataclass(frozen=True)
class BacktestConfig:
    initial_capital: float = 100_000.0
    cost_bps: float = 1.0


@dataclass(frozen=True)
class PerformanceStats:
    label: str
    total_return: float
    annual_return: float
    annual_volatility: float
    sharpe: float
    max_drawdown: float
    exposure_mean: float
    turnover: float
    trades: int


@dataclass(frozen=True)
class ComparisonRow:
    symbol: str
    strategy: PerformanceStats
    buy_hold: PerformanceStats


def run_backtest(signal: pd.DataFrame, config: BacktestConfig | None = None) -> pd.DataFrame:
    if config is None:
        config = BacktestConfig()
    if "close" not in signal or "target_exposure" not in signal:
        raise ValueError("signal must contain close and target_exposure columns")

    result = signal.copy()
    result["asset_return"] = daily_returns(result["close"])
    result["exposure_change"] = result["target_exposure"].diff().abs().fillna(result["target_exposure"].abs())
    result["transaction_cost"] = result["exposure_change"] * (config.cost_bps / 10_000)
    result["strategy_return"] = (
        result["target_exposure"] * result["asset_return"] - result["transaction_cost"]
    )
    result["equity"] = config.initial_capital * (1.0 + result["strategy_return"]).cumprod()
    result["buy_hold_equity"] = config.initial_capital * (1.0 + result["asset_return"]).cumprod()
    result["drawdown"] = result["equity"] / result["equity"].cummax() - 1.0
    result["buy_hold_drawdown"] = result["buy_hold_equity"] / result["buy_hold_equity"].cummax() - 1.0
    return result


def slice_backtest_result(result: pd.DataFrame, start: str | None = None, end: str | None = None) -> pd.DataFrame:
    sliced = result.copy()
    if start:
        sliced = sliced.loc[sliced.index >= start]
    if end:
        sliced = sliced.loc[sliced.index <= end]
    if sliced.empty:
        raise ValueError("No rows remain after applying date filters")

    sliced = sliced.copy()
    first_index = sliced.index[0]
    sliced.loc[first_index, "asset_return"] = 0.0
    sliced.loc[first_index, "strategy_return"] = 0.0
    sliced.loc[first_index, "transaction_cost"] = 0.0
    sliced.loc[first_index, "exposure_change"] = 0.0

    initial_equity = result["equity"].iloc[0]
    sliced["equity"] = initial_equity * (1.0 + sliced["strategy_return"]).cumprod()
    sliced["buy_hold_equity"] = initial_equity * (1.0 + sliced["asset_return"]).cumprod()
    sliced["drawdown"] = sliced["equity"] / sliced["equity"].cummax() - 1.0
    sliced["buy_hold_drawdown"] = sliced["buy_hold_equity"] / sliced["buy_hold_equity"].cummax() - 1.0
    return sliced


def summarize_performance(
    result: pd.DataFrame,
    return_column: str = "strategy_return",
    equity_column: str = "equity",
    drawdown_column: str = "drawdown",
    exposure_column: str | None = "target_exposure",
    turnover_column: str | None = "exposure_change",
    label: str = "Strategy",
) -> PerformanceStats:
    for column in (return_column, equity_column, drawdown_column):
        if column not in result:
            raise ValueError(f"backtest result is missing required column: {column}")

    returns = result[return_column].dropna()
    if returns.empty:
        raise ValueError("backtest result does not contain returns")

    years = max(len(returns) / TRADING_DAYS_PER_YEAR, 1 / TRADING_DAYS_PER_YEAR)
    total_return = result[equity_column].iloc[-1] / result[equity_column].iloc[0] - 1.0
    annual_return = (1.0 + total_return) ** (1.0 / years) - 1.0
    annual_volatility = returns.std() * np.sqrt(TRADING_DAYS_PER_YEAR)
    sharpe = annual_return / annual_volatility if annual_volatility > 0 else np.nan
    max_drawdown = result[drawdown_column].min()
    exposure_mean = result[exposure_column].mean() if exposure_column else 1.0
    turnover = result[turnover_column].sum() if turnover_column else 0.0
    trades = int((result[turnover_column] > 0).sum()) if turnover_column else 1

    return PerformanceStats(
        label=label,
        total_return=float(total_return),
        annual_return=float(annual_return),
        annual_volatility=float(annual_volatility),
        sharpe=float(sharpe),
        max_drawdown=float(max_drawdown),
        exposure_mean=float(exposure_mean),
        turnover=float(turnover),
        trades=trades,
    )


def summarize_buy_hold(result: pd.DataFrame) -> PerformanceStats:
    return summarize_performance(
        result,
        return_column="asset_return",
        equity_column="buy_hold_equity",
        drawdown_column="buy_hold_drawdown",
        exposure_column=None,
        turnover_column=None,
        label="Buy & hold",
    )


def build_comparison(symbol: str, result: pd.DataFrame) -> ComparisonRow:
    return ComparisonRow(
        symbol=symbol,
        strategy=summarize_performance(result, label="Strategy"),
        buy_hold=summarize_buy_hold(result),
    )


def format_stats(stats: PerformanceStats) -> str:
    return "\n".join(
        [
            f"Total return:      {stats.total_return:>8.2%}",
            f"Annual return:     {stats.annual_return:>8.2%}",
            f"Annual volatility: {stats.annual_volatility:>8.2%}",
            f"Sharpe:            {stats.sharpe:>8.2f}",
            f"Max drawdown:      {stats.max_drawdown:>8.2%}",
            f"Mean exposure:     {stats.exposure_mean:>8.2%}",
            f"Turnover:          {stats.turnover:>8.2f}x",
            f"Trades:            {stats.trades:>8d}",
        ]
    )


def format_comparison(rows: list[ComparisonRow]) -> str:
    headers = [
        "Symbol",
        "Model",
        "AnnRet",
        "AnnVol",
        "Sharpe",
        "MaxDD",
        "TotRet",
        "AvgExp",
        "Trades",
    ]
    lines = ["  ".join(f"{header:>10}" for header in headers)]
    for row in rows:
        for stats in (row.strategy, row.buy_hold):
            lines.append(
                "  ".join(
                    [
                        f"{row.symbol:>10}",
                        f"{stats.label:>10}",
                        f"{stats.annual_return:>10.2%}",
                        f"{stats.annual_volatility:>10.2%}",
                        f"{stats.sharpe:>10.2f}",
                        f"{stats.max_drawdown:>10.2%}",
                        f"{stats.total_return:>10.2%}",
                        f"{stats.exposure_mean:>10.2%}",
                        f"{stats.trades:>10d}",
                    ]
                )
            )
    return "\n".join(lines)
