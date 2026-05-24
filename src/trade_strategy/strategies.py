from __future__ import annotations

import pandas as pd

from trade_strategy.indicators import daily_returns, realized_volatility, simple_moving_average


def trend_volatility_target_signal(
    close: pd.Series,
    fast_window: int = 50,
    slow_window: int = 200,
    vol_window: int = 20,
    target_vol: float = 0.12,
    max_exposure: float = 1.0,
) -> pd.DataFrame:
    """Build target exposure for a long/cash trend strategy."""
    if fast_window >= slow_window:
        raise ValueError("fast_window must be smaller than slow_window")
    if target_vol <= 0:
        raise ValueError("target_vol must be positive")
    if max_exposure <= 0:
        raise ValueError("max_exposure must be positive")

    returns = daily_returns(close)
    fast_ma = simple_moving_average(close, fast_window)
    slow_ma = simple_moving_average(close, slow_window)
    realized_vol = realized_volatility(returns, vol_window)

    trend_on = (close > slow_ma) & (fast_ma > slow_ma)
    raw_exposure = target_vol / realized_vol
    exposure = raw_exposure.clip(lower=0.0, upper=max_exposure).where(trend_on, 0.0)
    exposure = exposure.shift(1).fillna(0.0)

    return pd.DataFrame(
        {
            "close": close,
            "fast_ma": fast_ma,
            "slow_ma": slow_ma,
            "realized_vol": realized_vol,
            "target_exposure": exposure,
        },
        index=close.index,
    )
