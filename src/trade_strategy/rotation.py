from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from trade_strategy.backtest import BacktestConfig
from trade_strategy.indicators import daily_returns, realized_volatility, simple_moving_average


SPDR_SECTOR_SYMBOLS = [
    "XLB",
    "XLC",
    "XLE",
    "XLF",
    "XLI",
    "XLK",
    "XLP",
    "XLRE",
    "XLU",
    "XLV",
    "XLY",
]

SPDR_SECTOR_NAMES = {
    "XLB": "Materials",
    "XLC": "Communication Services",
    "XLE": "Energy",
    "XLF": "Financials",
    "XLI": "Industrials",
    "XLK": "Technology",
    "XLP": "Consumer Staples",
    "XLRE": "Real Estate",
    "XLU": "Utilities",
    "XLV": "Health Care",
    "XLY": "Consumer Discretionary",
}


@dataclass(frozen=True)
class SectorRotationConfig:
    score_lookback_weeks: int = 4
    volume_lookback_weeks: int = 26
    top_n: int = 1
    fast_window: int = 50
    slow_window: int = 200
    vol_window: int = 20
    target_vol: float = 0.12
    max_exposure: float = 1.0


def run_sector_rotation_backtest(
    prices_by_symbol: dict[str, pd.DataFrame],
    benchmark: pd.DataFrame,
    config: SectorRotationConfig | None = None,
    backtest_config: BacktestConfig | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Rank sector ETFs on Fridays and execute selected sectors at next open."""
    if config is None:
        config = SectorRotationConfig()
    if backtest_config is None:
        backtest_config = BacktestConfig()
    if not prices_by_symbol:
        raise ValueError("prices_by_symbol cannot be empty")
    if config.top_n <= 0:
        raise ValueError("top_n must be positive")
    if config.top_n > len(prices_by_symbol):
        raise ValueError("top_n cannot exceed the number of sector symbols")

    normalized_prices = {
        symbol: _validate_price_frame(symbol, frame)
        for symbol, frame in prices_by_symbol.items()
    }
    benchmark = _validate_price_frame("benchmark", benchmark)
    common_index = _common_index([*normalized_prices.values(), benchmark])
    if common_index.empty:
        raise ValueError("No overlapping dates across sector and benchmark data")

    normalized_prices = {
        symbol: frame.loc[common_index].copy()
        for symbol, frame in normalized_prices.items()
    }
    benchmark = benchmark.loc[common_index].copy()

    scores = build_weekly_flow_scores(normalized_prices, config)
    exposures = _build_unshifted_trend_exposures(normalized_prices, config)
    positions, trade_log = _build_open_execution_positions(
        common_index,
        scores,
        exposures,
        config,
    )

    open_returns = pd.DataFrame(
        {
            symbol: frame["open"].shift(-1) / frame["open"] - 1.0
            for symbol, frame in normalized_prices.items()
        },
        index=common_index,
    ).fillna(0.0)
    benchmark_returns = (benchmark["open"].shift(-1) / benchmark["open"] - 1.0).fillna(0.0)

    strategy_gross_return = (positions * open_returns).sum(axis=1)
    exposure = positions.abs().sum(axis=1)
    exposure_change = positions.diff().abs().sum(axis=1).fillna(exposure)
    transaction_cost = exposure_change * (backtest_config.cost_bps / 10_000)
    strategy_return = strategy_gross_return - transaction_cost

    result = pd.DataFrame(
        {
            "asset_return": benchmark_returns,
            "target_exposure": exposure,
            "exposure_change": exposure_change,
            "transaction_cost": transaction_cost,
            "strategy_return": strategy_return,
            "selected_symbol": _selected_symbol_by_day(positions),
            "selected_symbols": _selected_symbols_by_day(positions),
        },
        index=common_index,
    )
    result["equity"] = backtest_config.initial_capital * (1.0 + result["strategy_return"]).cumprod()
    result["buy_hold_equity"] = backtest_config.initial_capital * (1.0 + result["asset_return"]).cumprod()
    result["drawdown"] = result["equity"] / result["equity"].cummax() - 1.0
    result["buy_hold_drawdown"] = result["buy_hold_equity"] / result["buy_hold_equity"].cummax() - 1.0

    return result, trade_log


def build_weekly_flow_scores(
    prices_by_symbol: dict[str, pd.DataFrame],
    config: SectorRotationConfig,
) -> pd.DataFrame:
    """Score sectors using weekly price strength confirmed by dollar-volume expansion."""
    if config.score_lookback_weeks <= 0:
        raise ValueError("score_lookback_weeks must be positive")
    if config.volume_lookback_weeks <= 1:
        raise ValueError("volume_lookback_weeks must be greater than 1")

    score_by_symbol = {}
    for symbol, frame in prices_by_symbol.items():
        weekly = frame.resample("W-FRI").agg({"close": "last", "volume": "sum"}).dropna()
        weekly_return = weekly["close"].pct_change(config.score_lookback_weeks)
        dollar_volume = weekly["close"] * weekly["volume"]
        volume_ratio = dollar_volume / dollar_volume.rolling(config.volume_lookback_weeks).mean()
        score_by_symbol[symbol] = weekly_return * volume_ratio

    return pd.DataFrame(score_by_symbol).dropna(how="all")


def latest_sector_signal(
    prices_by_symbol: dict[str, pd.DataFrame],
    config: SectorRotationConfig | None = None,
) -> dict[str, object]:
    if config is None:
        config = SectorRotationConfig()
    normalized_prices = {
        symbol: _validate_price_frame(symbol, frame)
        for symbol, frame in prices_by_symbol.items()
    }
    common_index = _common_index(list(normalized_prices.values()))
    normalized_prices = {
        symbol: frame.loc[common_index].copy()
        for symbol, frame in normalized_prices.items()
    }
    scores = build_weekly_flow_scores(normalized_prices, config).dropna(how="all")
    exposures = _build_unshifted_trend_exposures(normalized_prices, config)
    if scores.empty:
        raise ValueError("Not enough data to build a latest sector signal")

    signal_date = scores.index[-1]
    score_row = scores.iloc[-1].dropna()
    ranked = score_row.sort_values(ascending=False)
    selected_symbols = [str(symbol) for symbol in ranked.head(config.top_n).index]
    exposure_index = exposures.index.searchsorted(signal_date, side="right") - 1
    if exposure_index < 0:
        raise ValueError("Not enough daily data to evaluate latest sector exposure")
    exposure_date = exposures.index[exposure_index]
    selected_exposures = {
        symbol: float(exposures.at[exposure_date, symbol]) / len(selected_symbols)
        for symbol in selected_symbols
    }

    return {
        "signal_date": signal_date,
        "selected_symbol": selected_symbols[0],
        "selected_symbols": selected_symbols,
        "flow_rank": {symbol: int(rank + 1) for rank, symbol in enumerate(ranked.index.astype(str))},
        "score": float(score_row[selected_symbols[0]]),
        "target_exposure": float(sum(selected_exposures.values())),
        "target_exposures": selected_exposures,
    }


def format_sector_symbol(symbol: str) -> str:
    symbol = symbol.upper()
    name = SPDR_SECTOR_NAMES.get(symbol)
    return f"{symbol} - {name}" if name else symbol


def _build_unshifted_trend_exposures(
    prices_by_symbol: dict[str, pd.DataFrame],
    config: SectorRotationConfig,
) -> pd.DataFrame:
    exposures = {}
    for symbol, frame in prices_by_symbol.items():
        returns = daily_returns(frame["close"])
        fast_ma = simple_moving_average(frame["close"], config.fast_window)
        slow_ma = simple_moving_average(frame["close"], config.slow_window)
        realized_vol = realized_volatility(returns, config.vol_window)
        trend_on = (frame["close"] > slow_ma) & (fast_ma > slow_ma)
        raw_exposure = config.target_vol / realized_vol
        exposures[symbol] = raw_exposure.clip(lower=0.0, upper=config.max_exposure).where(trend_on, 0.0)
    return pd.DataFrame(exposures)


def _build_open_execution_positions(
    index: pd.DatetimeIndex,
    scores: pd.DataFrame,
    exposures: pd.DataFrame,
    config: SectorRotationConfig,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    positions = pd.DataFrame(0.0, index=index, columns=exposures.columns)
    trades = []

    valid_scores = scores.dropna(how="all")
    for signal_date, score_row in valid_scores.iterrows():
        eligible_scores = score_row.dropna()
        if eligible_scores.empty:
            continue

        selected_symbols = [
            str(symbol)
            for symbol in eligible_scores.sort_values(ascending=False).head(config.top_n).index
        ]
        if signal_date not in exposures.index:
            friday_index = exposures.index.searchsorted(signal_date, side="right") - 1
            if friday_index < 0:
                continue
            exposure_date = exposures.index[friday_index]
        else:
            exposure_date = signal_date

        target_exposures = {
            symbol: float(exposures.at[exposure_date, symbol]) / len(selected_symbols)
            for symbol in selected_symbols
        }
        target_exposure = sum(target_exposures.values())
        execution_index = index.searchsorted(signal_date, side="right")
        if execution_index >= len(index):
            continue
        execution_date = index[execution_index]

        next_signal_index = valid_scores.index.searchsorted(signal_date, side="right")
        if next_signal_index < len(valid_scores.index):
            next_execution_index = index.searchsorted(valid_scores.index[next_signal_index], side="right")
        else:
            next_execution_index = len(index)

        for symbol, symbol_exposure in target_exposures.items():
            positions.iloc[execution_index:next_execution_index, positions.columns.get_loc(symbol)] = symbol_exposure
        trades.append(
            {
                "signal_date": signal_date,
                "execution_date": execution_date,
                "selected_symbol": selected_symbols[0],
                "selected_symbols": ",".join(selected_symbols),
                "score": float(eligible_scores[selected_symbols[0]]),
                "target_exposure": target_exposure,
                "target_exposures": ";".join(
                    f"{symbol}:{target_exposures[symbol]:.6f}" for symbol in selected_symbols
                ),
            }
        )

    return positions, pd.DataFrame(trades)


def _selected_symbol_by_day(positions: pd.DataFrame) -> pd.Series:
    selected = positions.idxmax(axis=1)
    selected = selected.where(positions.abs().sum(axis=1) > 0, "CASH")
    return selected


def _selected_symbols_by_day(positions: pd.DataFrame) -> pd.Series:
    selected = []
    for _, row in positions.iterrows():
        active = [symbol for symbol, exposure in row.items() if exposure > 0]
        selected.append(",".join(active) if active else "CASH")
    return pd.Series(selected, index=positions.index)


def _validate_price_frame(symbol: str, frame: pd.DataFrame) -> pd.DataFrame:
    required_columns = {"open", "close", "volume"}
    missing = required_columns.difference(frame.columns)
    if missing:
        missing_text = ", ".join(sorted(missing))
        raise ValueError(f"{symbol} is missing required columns: {missing_text}")
    return frame.sort_index().dropna(subset=["open", "close", "volume"])


def _common_index(frames: list[pd.DataFrame]) -> pd.DatetimeIndex:
    common_index = frames[0].index
    for frame in frames[1:]:
        common_index = common_index.intersection(frame.index)
    return common_index.sort_values()
