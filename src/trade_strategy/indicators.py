from __future__ import annotations

import pandas as pd


TRADING_DAYS_PER_YEAR = 252


def simple_moving_average(series: pd.Series, window: int) -> pd.Series:
    if window <= 0:
        raise ValueError("window must be positive")
    return series.rolling(window=window, min_periods=window).mean()


def daily_returns(close: pd.Series) -> pd.Series:
    return close.pct_change().fillna(0.0)


def realized_volatility(returns: pd.Series, window: int) -> pd.Series:
    if window <= 1:
        raise ValueError("window must be greater than 1")
    return returns.rolling(window=window, min_periods=window).std() * (TRADING_DAYS_PER_YEAR**0.5)
