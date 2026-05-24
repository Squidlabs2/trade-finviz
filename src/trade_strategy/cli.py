from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from trade_strategy.backtest import (
    BacktestConfig,
    build_comparison,
    format_comparison,
    format_stats,
    run_backtest,
    slice_backtest_result,
    summarize_buy_hold,
    summarize_performance,
)
from trade_strategy.data import fetch_yahoo, load_price_csv, save_price_csv
from trade_strategy.notify import NtfyConfig, ntfy_config_from_env, send_ntfy_message
from trade_strategy.rotation import (
    SPDR_SECTOR_SYMBOLS,
    SectorRotationConfig,
    latest_sector_signal,
    run_sector_rotation_backtest,
)
from trade_strategy.scanner import (
    ScanConfig,
    format_scan_message,
    format_sector_flow_message,
    format_stock_candidates_message,
    fetch_sector_holdings_universe,
    fetch_finviz_sector_candidates,
    load_holdings,
    load_universe,
    refresh_symbol_data,
    run_momentum_scan,
)
from trade_strategy.strategies import trend_volatility_target_signal


def add_strategy_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--fast-window", type=int, default=50)
    parser.add_argument("--slow-window", type=int, default=200)
    parser.add_argument("--vol-window", type=int, default=20)
    parser.add_argument("--target-vol", type=float, default=0.12)
    parser.add_argument("--max-exposure", type=float, default=1.0)
    parser.add_argument("--cost-bps", type=float, default=1.0)
    parser.add_argument("--initial-capital", type=float, default=100_000.0)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Trading strategy research CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch_parser = subparsers.add_parser("fetch", help="Fetch daily Yahoo Finance data")
    fetch_parser.add_argument("--symbol", required=True)
    fetch_parser.add_argument("--start", required=True)
    fetch_parser.add_argument("--end")
    fetch_parser.add_argument("--output", required=True)

    backtest_parser = subparsers.add_parser("backtest", help="Run the baseline trend backtest")
    backtest_parser.add_argument("--csv", required=True)
    backtest_parser.add_argument("--symbol", default="SYMBOL")
    add_strategy_arguments(backtest_parser)
    backtest_parser.add_argument("--output")

    compare_parser = subparsers.add_parser("compare", help="Compare strategy and buy-and-hold across CSV files")
    compare_parser.add_argument(
        "--csv",
        nargs="+",
        required=True,
        help="One or more CSV paths. Symbols default to each file stem.",
    )
    compare_parser.add_argument(
        "--symbols",
        nargs="+",
        help="Optional symbols matching the CSV path order.",
    )
    compare_parser.add_argument("--start", help="Optional inclusive start date, for regime/date slicing.")
    compare_parser.add_argument("--end", help="Optional inclusive end date, for regime/date slicing.")
    add_strategy_arguments(compare_parser)
    compare_parser.add_argument("--output", help="Optional CSV report path.")

    rotation_parser = subparsers.add_parser(
        "sector-rotation",
        help="Rank sector ETFs each Friday and trade the selected sector at next open",
    )
    rotation_parser.add_argument("--data-dir", default="data")
    rotation_parser.add_argument("--sector-symbols", nargs="+", default=SPDR_SECTOR_SYMBOLS)
    rotation_parser.add_argument("--benchmark-symbol", default="SPY")
    rotation_parser.add_argument("--start", help="Optional inclusive start date, applied after signal history is built.")
    rotation_parser.add_argument("--end", help="Optional inclusive end date.")
    rotation_parser.add_argument("--score-lookback-weeks", type=int, default=4)
    rotation_parser.add_argument("--volume-lookback-weeks", type=int, default=26)
    rotation_parser.add_argument("--top-n", type=int, default=1)
    add_strategy_arguments(rotation_parser)
    rotation_parser.add_argument("--output", help="Optional daily backtest CSV path.")
    rotation_parser.add_argument("--trades-output", help="Optional weekly trade log CSV path.")

    scan_parser = subparsers.add_parser(
        "weekly-scan",
        help="Find weekly momentum/volume candidates and optionally notify ntfy",
    )
    scan_parser.add_argument("--data-dir", default="data")
    scan_parser.add_argument("--universe", help="CSV with symbol and optional sector_etf columns.")
    scan_parser.add_argument(
        "--use-sector-holdings",
        action="store_true",
        help="Build the stock universe from the current leading sector ETF holdings.",
    )
    scan_parser.add_argument(
        "--use-finviz-sector-screener",
        action="store_true",
        help="Use a Finviz-style sector-wide screen for stocks in the current leading sectors.",
    )
    scan_parser.add_argument(
        "--finviz-rsi-mode",
        choices=["not-over-60", "over-60", "both"],
        default="not-over-60",
        help="Finviz RSI screen to run when --use-finviz-sector-screener is enabled.",
    )
    scan_parser.add_argument(
        "--max-holdings-per-sector",
        type=int,
        default=100,
        help="Limit holdings loaded from each leading sector ETF.",
    )
    scan_parser.add_argument(
        "--holdings-universe-output",
        default="outputs/sector_holdings_universe.csv",
        help="Where to save the generated sector-holdings stock universe.",
    )
    scan_parser.add_argument("--holdings", help="Optional CSV with held symbols to check for exit signals.")
    scan_parser.add_argument("--refresh-data", action="store_true")
    scan_parser.add_argument("--fetch-start", default="2020-01-01")
    scan_parser.add_argument("--sma-window", type=int, default=50)
    scan_parser.add_argument("--long-sma-window", type=int, default=200)
    scan_parser.add_argument("--volume-window", type=int, default=50)
    scan_parser.add_argument("--min-volume-ratio", type=float, default=1.25)
    scan_parser.add_argument("--momentum-lookback-days", type=int, default=20)
    scan_parser.add_argument("--top-sector-count", type=int, default=3)
    scan_parser.add_argument(
        "--leading-sector-stocks-only",
        action="store_true",
        help="Only scan symbols whose sector_etf is one of the current leading sectors.",
    )
    scan_parser.add_argument("--max-results", type=int, default=20)
    scan_parser.add_argument("--output", default="outputs/weekly_scan.csv")
    scan_parser.add_argument("--notify", action="store_true")
    scan_parser.add_argument("--ntfy-url", help="Overrides NTFY_URL.")
    scan_parser.add_argument("--ntfy-topic", help="Overrides NTFY_TOPIC.")
    scan_parser.add_argument("--ntfy-token", help="Overrides NTFY_TOKEN.")

    return parser


def run_fetch(args: argparse.Namespace) -> None:
    data = fetch_yahoo(args.symbol, start=args.start, end=args.end)
    save_price_csv(data, args.output)
    print(f"Saved {len(data)} rows to {args.output}")


def run_backtest_command(args: argparse.Namespace) -> None:
    prices = maybe_slice_dates(load_price_csv(args.csv), args.start if hasattr(args, "start") else None, args.end if hasattr(args, "end") else None)
    signal = trend_volatility_target_signal(
        prices["close"],
        fast_window=args.fast_window,
        slow_window=args.slow_window,
        vol_window=args.vol_window,
        target_vol=args.target_vol,
        max_exposure=args.max_exposure,
    )
    result = run_backtest(
        signal,
        BacktestConfig(initial_capital=args.initial_capital, cost_bps=args.cost_bps),
    )
    stats = summarize_performance(result)
    buy_hold_stats = summarize_buy_hold(result)

    print(f"{args.symbol} baseline trend-volatility backtest")
    print(format_stats(stats))
    print("")
    print("Buy-and-hold benchmark")
    print(format_stats(buy_hold_stats))

    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        result.to_csv(output)
        print(f"Saved backtest rows to {output}")


def maybe_slice_dates(frame, start: str | None, end: str | None):
    if start:
        frame = frame.loc[frame.index >= start]
    if end:
        frame = frame.loc[frame.index <= end]
    if frame.empty:
        raise ValueError("No rows remain after applying date filters")
    return frame


def run_comparison(args: argparse.Namespace) -> None:
    if args.symbols and len(args.symbols) != len(args.csv):
        raise ValueError("--symbols must have the same count as --csv")

    rows = []
    report_rows = []
    for index, csv_path in enumerate(args.csv):
        symbol = args.symbols[index] if args.symbols else Path(csv_path).stem.upper()
        prices = load_price_csv(csv_path)
        signal = trend_volatility_target_signal(
            prices["close"],
            fast_window=args.fast_window,
            slow_window=args.slow_window,
            vol_window=args.vol_window,
            target_vol=args.target_vol,
            max_exposure=args.max_exposure,
        )
        result = run_backtest(
            signal,
            BacktestConfig(initial_capital=args.initial_capital, cost_bps=args.cost_bps),
        )
        result = slice_backtest_result(result, args.start, args.end)
        row = build_comparison(symbol, result)
        rows.append(row)
        for stats in (row.strategy, row.buy_hold):
            report_rows.append(
                {
                    "symbol": row.symbol,
                    "model": stats.label,
                    "total_return": stats.total_return,
                    "annual_return": stats.annual_return,
                    "annual_volatility": stats.annual_volatility,
                    "sharpe": stats.sharpe,
                    "max_drawdown": stats.max_drawdown,
                    "mean_exposure": stats.exposure_mean,
                    "turnover": stats.turnover,
                    "trades": stats.trades,
                }
            )

    print(format_comparison(rows))

    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(report_rows).to_csv(output, index=False)
        print(f"Saved comparison report to {output}")


def run_sector_rotation_command(args: argparse.Namespace) -> None:
    data_dir = Path(args.data_dir)
    sector_prices = {
        symbol: load_price_csv(data_dir / f"{symbol}.csv")
        for symbol in args.sector_symbols
    }
    benchmark = load_price_csv(data_dir / f"{args.benchmark_symbol}.csv")
    rotation_config = SectorRotationConfig(
        score_lookback_weeks=args.score_lookback_weeks,
        volume_lookback_weeks=args.volume_lookback_weeks,
        top_n=args.top_n,
        fast_window=args.fast_window,
        slow_window=args.slow_window,
        vol_window=args.vol_window,
        target_vol=args.target_vol,
        max_exposure=args.max_exposure,
    )
    result, trades = run_sector_rotation_backtest(
        sector_prices,
        benchmark,
        config=rotation_config,
        backtest_config=BacktestConfig(initial_capital=args.initial_capital, cost_bps=args.cost_bps),
    )
    result = slice_backtest_result(result, args.start, args.end)
    if args.start:
        trades = trades.loc[trades["execution_date"] >= args.start]
    if args.end:
        trades = trades.loc[trades["execution_date"] <= args.end]

    strategy_stats = summarize_performance(result, label="Sector rotation")
    benchmark_stats = summarize_buy_hold(result)
    signal = latest_sector_signal(sector_prices, rotation_config)

    print("Weekly sector-rotation backtest")
    print(format_stats(strategy_stats))
    print("")
    print(f"{args.benchmark_symbol} buy-and-hold benchmark")
    print(format_stats(benchmark_stats))
    print("")
    print(
        "Latest Friday signal: "
        f"{','.join(signal['selected_symbols'])} on {signal['signal_date'].date()} "
        f"with target exposure {signal['target_exposure']:.2%}"
    )

    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        result.to_csv(output)
        print(f"Saved daily rotation backtest to {output}")
    if args.trades_output:
        output = Path(args.trades_output)
        output.parent.mkdir(parents=True, exist_ok=True)
        trades.to_csv(output, index=False)
        print(f"Saved weekly trade log to {output}")


def run_weekly_scan_command(args: argparse.Namespace) -> None:
    holdings = load_holdings(args.holdings)

    if args.refresh_data:
        failures = refresh_symbol_data(SPDR_SECTOR_SYMBOLS, args.data_dir, args.fetch_start)
        for failure in failures:
            print(f"Data refresh warning: {failure}")

    scan_config = ScanConfig(
        sma_window=args.sma_window,
        long_sma_window=args.long_sma_window,
        volume_window=args.volume_window,
        min_volume_ratio=args.min_volume_ratio,
        momentum_lookback_days=args.momentum_lookback_days,
        max_results=args.max_results,
        top_sector_count=args.top_sector_count,
        leading_sector_stocks_only=args.leading_sector_stocks_only,
    )

    if args.use_finviz_sector_screener:
        sector_prices = {
            symbol: load_price_csv(Path(args.data_dir) / f"{symbol}.csv")
            for symbol in SPDR_SECTOR_SYMBOLS
        }
        sector_signal = latest_sector_signal(
            sector_prices,
            SectorRotationConfig(top_n=args.top_sector_count),
        )
        rsi_modes = (
            ["not-over-60", "over-60"]
            if args.finviz_rsi_mode == "both"
            else [args.finviz_rsi_mode]
        )
        scan_parts = [
            fetch_finviz_sector_candidates(
                sector_signal["selected_symbols"],
                max_results_per_sector=args.max_results,
                rsi_mode=rsi_mode,
            )
            for rsi_mode in rsi_modes
        ]
        scan = pd.concat(scan_parts, ignore_index=True) if scan_parts else pd.DataFrame()
        universe = scan[["symbol", "sector_etf"]].copy() if not scan.empty else pd.DataFrame(columns=["symbol", "sector_etf"])
    elif args.use_sector_holdings:
        sector_prices = {
            symbol: load_price_csv(Path(args.data_dir) / f"{symbol}.csv")
            for symbol in SPDR_SECTOR_SYMBOLS
        }
        sector_signal = latest_sector_signal(
            sector_prices,
            SectorRotationConfig(top_n=args.top_sector_count),
        )
        universe = fetch_sector_holdings_universe(
            sector_signal["selected_symbols"],
            max_holdings_per_sector=args.max_holdings_per_sector,
        )
        holdings_universe_output = Path(args.holdings_universe_output)
        holdings_universe_output.parent.mkdir(parents=True, exist_ok=True)
        universe.to_csv(holdings_universe_output, index=False)
        print(f"Saved leading-sector holdings universe to {holdings_universe_output}")
        if args.refresh_data:
            failures = refresh_symbol_data(sorted(universe["symbol"].tolist()), args.data_dir, args.fetch_start)
            for failure in failures:
                print(f"Data refresh warning: {failure}")
    else:
        universe = load_universe(args.universe)
        symbols = sorted(set(universe["symbol"].tolist()) | set(SPDR_SECTOR_SYMBOLS))
        if args.refresh_data:
            failures = refresh_symbol_data(symbols, args.data_dir, args.fetch_start)
            for failure in failures:
                print(f"Data refresh warning: {failure}")

    if not holdings.empty and args.refresh_data:
        missing_symbols = sorted(set(holdings["symbol"].tolist()) - set(universe["symbol"].tolist()))
        if missing_symbols:
            failures = refresh_symbol_data(missing_symbols, args.data_dir, args.fetch_start)
            for failure in failures:
                print(f"Data refresh warning: {failure}")

    if not args.use_finviz_sector_screener:
        scan, sector_signal = run_momentum_scan(universe, args.data_dir, scan_config)
    if not holdings.empty:
        missing_holdings = holdings.loc[~holdings["symbol"].isin(scan["symbol"])]
        if not missing_holdings.empty:
            holdings_universe = load_universe(args.holdings)
            holdings_config = ScanConfig(
                sma_window=args.sma_window,
                long_sma_window=args.long_sma_window,
                volume_window=args.volume_window,
                min_volume_ratio=args.min_volume_ratio,
                momentum_lookback_days=args.momentum_lookback_days,
                max_results=args.max_results,
                top_sector_count=args.top_sector_count,
                leading_sector_stocks_only=False,
            )
            holdings_scan, _ = run_momentum_scan(holdings_universe, args.data_dir, holdings_config)
            scan = pd.concat([scan, holdings_scan], ignore_index=True).drop_duplicates("symbol")
    sector_message = format_sector_flow_message(sector_signal)
    stock_message = format_stock_candidates_message(
        scan,
        sector_signal,
        args.max_results,
        holdings,
        leading_sector_stocks_only=args.leading_sector_stocks_only,
    )
    message = format_scan_message(
        scan,
        sector_signal,
        args.max_results,
        holdings,
        leading_sector_stocks_only=args.leading_sector_stocks_only,
    )

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    scan.to_csv(output, index=False)

    print(message)
    print(f"Saved weekly scan to {output}")

    if args.notify:
        ntfy_config = _build_ntfy_config(args)
        if ntfy_config is None:
            raise ValueError("Set NTFY_URL and NTFY_TOPIC, or pass --ntfy-url and --ntfy-topic")
        send_ntfy_message(ntfy_config, "Weekly sector flow", sector_message)
        print(f"Sent sector-flow ntfy notification to {ntfy_config.endpoint}")
        send_ntfy_message(ntfy_config, "Weekly stock candidates", stock_message)
        print(f"Sent stock-candidates ntfy notification to {ntfy_config.endpoint}")


def _build_ntfy_config(args: argparse.Namespace) -> NtfyConfig | None:
    if args.ntfy_url and args.ntfy_topic:
        return NtfyConfig(url=args.ntfy_url, topic=args.ntfy_topic, token=args.ntfy_token)
    return ntfy_config_from_env()


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "fetch":
        run_fetch(args)
    elif args.command == "backtest":
        run_backtest_command(args)
    elif args.command == "compare":
        run_comparison(args)
    elif args.command == "sector-rotation":
        run_sector_rotation_command(args)
    elif args.command == "weekly-scan":
        run_weekly_scan_command(args)
    else:
        parser.error(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
