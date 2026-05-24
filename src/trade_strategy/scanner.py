from __future__ import annotations

from io import BytesIO
from dataclasses import dataclass
from pathlib import Path
import re
from urllib.parse import urlencode
import urllib.request
import zipfile
import xml.etree.ElementTree as ET

import pandas as pd

from trade_strategy.data import fetch_yahoo, load_price_csv, save_price_csv
from trade_strategy.indicators import simple_moving_average
from trade_strategy.rotation import SPDR_SECTOR_SYMBOLS, SectorRotationConfig, latest_sector_signal


SSGA_HOLDINGS_URL_TEMPLATE = (
    "https://www.ssga.com/library-content/products/fund-data/etfs/us/"
    "holdings-daily-us-en-{symbol}.xlsx"
)

FINVIZ_SECTOR_BY_ETF = {
    "XLB": "basicmaterials",
    "XLC": "communicationservices",
    "XLE": "energy",
    "XLF": "financial",
    "XLI": "industrials",
    "XLK": "technology",
    "XLP": "consumerdefensive",
    "XLRE": "realestate",
    "XLU": "utilities",
    "XLV": "healthcare",
    "XLY": "consumercyclical",
}

FINVIZ_RSI_FILTERS = {
    "not-over-60": ("ta_rsi_nob60", "RSI not over 60"),
    "over-60": ("ta_rsi_ob60", "RSI over 60"),
}

FINVIZ_BASE_FILTERS = [
    "sh_avgvol_o400",
    "sh_curvol_o500",
    "sh_price_5to50",
    "ta_perf_4wup",
]


@dataclass(frozen=True)
class ScanConfig:
    sma_window: int = 50
    long_sma_window: int = 200
    volume_window: int = 50
    min_volume_ratio: float = 1.25
    momentum_lookback_days: int = 20
    max_results: int = 20
    top_sector_count: int = 3
    leading_sector_stocks_only: bool = False


def load_universe(path: str | Path | None) -> pd.DataFrame:
    if path is None:
        return pd.DataFrame({"symbol": SPDR_SECTOR_SYMBOLS, "sector_etf": SPDR_SECTOR_SYMBOLS})

    universe = pd.read_csv(path)
    universe.columns = [column.strip().lower() for column in universe.columns]
    if "symbol" not in universe.columns:
        raise ValueError("Universe CSV must include a symbol column")
    if "sector_etf" not in universe.columns:
        universe["sector_etf"] = ""
    universe["symbol"] = universe["symbol"].astype(str).str.upper().str.strip()
    universe["sector_etf"] = universe["sector_etf"].astype(str).str.upper().str.strip()
    return universe.drop_duplicates("symbol")


def refresh_symbol_data(symbols: list[str], data_dir: str | Path, start: str) -> list[str]:
    output_dir = Path(data_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    failures = []
    for symbol in symbols:
        try:
            data = fetch_yahoo(symbol, start=start)
        except Exception as exc:
            failures.append(f"{symbol}: {exc}")
            continue
        save_price_csv(data, output_dir / f"{symbol}.csv")
    return failures


def fetch_sector_holdings_universe(
    sector_symbols: list[str],
    max_holdings_per_sector: int | None = None,
) -> pd.DataFrame:
    rows = []
    for sector_symbol in sector_symbols:
        holdings = fetch_sector_holdings(sector_symbol)
        if max_holdings_per_sector:
            holdings = holdings.head(max_holdings_per_sector)
        rows.append(holdings)

    if not rows:
        return pd.DataFrame(columns=["symbol", "name", "sector_etf", "weight"])

    universe = pd.concat(rows, ignore_index=True)
    universe = universe.sort_values(["sector_etf", "weight"], ascending=[True, False])
    universe = universe.drop_duplicates("symbol")
    return universe[["symbol", "name", "sector_etf", "weight"]]


def fetch_finviz_sector_candidates(
    sector_symbols: list[str],
    max_results_per_sector: int = 60,
    rsi_mode: str = "not-over-60",
) -> pd.DataFrame:
    if rsi_mode not in FINVIZ_RSI_FILTERS:
        raise ValueError(f"Unknown Finviz RSI mode: {rsi_mode}")

    rows = []
    for sector_rank, sector_symbol in enumerate(sector_symbols):
        sector_symbol = sector_symbol.upper()
        finviz_sector = FINVIZ_SECTOR_BY_ETF.get(sector_symbol)
        if not finviz_sector:
            continue
        sector_rows = _fetch_finviz_sector_pages(sector_symbol, finviz_sector, max_results_per_sector, rsi_mode)
        for row_rank, row in enumerate(sector_rows):
            row["sector_rank"] = sector_rank
            row["finviz_rank"] = row_rank
            row["screen_name"] = FINVIZ_RSI_FILTERS[rsi_mode][1]
            row["screen_key"] = rsi_mode
        rows.extend(sector_rows)

    if not rows:
        return pd.DataFrame(
            columns=[
                "symbol",
                "sector_etf",
                "close",
                "volume",
                "status",
                "finviz_url",
                "screen_name",
                "screen_key",
            ]
        )

    frame = pd.DataFrame(rows).drop_duplicates("symbol")
    frame = frame.sort_values(["finviz_rank", "sector_rank", "volume"], ascending=[True, True, False])
    frame["status"] = "candidate"
    frame["sector_leader"] = True
    frame["score"] = frame["volume"]
    frame["volume_ratio"] = 0.0
    frame["momentum"] = 0.0
    frame["exit_signal"] = False
    return frame


def build_finviz_sector_url(
    sector_symbol: str,
    start_row: int | None = None,
    rsi_mode: str = "not-over-60",
) -> str:
    if rsi_mode not in FINVIZ_RSI_FILTERS:
        raise ValueError(f"Unknown Finviz RSI mode: {rsi_mode}")
    finviz_sector = FINVIZ_SECTOR_BY_ETF[sector_symbol.upper()]
    rsi_filter = FINVIZ_RSI_FILTERS[rsi_mode][0]
    filters = [f"sec_{finviz_sector}", *FINVIZ_BASE_FILTERS, rsi_filter, "ta_sma50_pa"]
    params = {
        "v": "211",
        "f": ",".join(filters),
        "ft": "4",
        "o": "-volume",
        "ar": "180",
    }
    if start_row is not None and start_row > 1:
        params["r"] = str(start_row)
    return f"https://finviz.com/screener?{urlencode(params)}"


def _fetch_finviz_sector_pages(
    sector_symbol: str,
    finviz_sector: str,
    max_results: int,
    rsi_mode: str,
) -> list[dict[str, object]]:
    rows = []
    start_row = 1
    seen = set()
    while len(rows) < max_results:
        url = build_finviz_sector_url(sector_symbol, start_row=start_row, rsi_mode=rsi_mode)
        html = _fetch_text(url)
        page_rows = parse_finviz_chart_rows(html, sector_symbol, url)
        page_rows = [row for row in page_rows if row["symbol"] not in seen]
        if not page_rows:
            break
        for row in page_rows:
            rows.append(row)
            seen.add(row["symbol"])
            if len(rows) >= max_results:
                break
        start_row += len(page_rows)
    return rows


def parse_finviz_chart_rows(html: str, sector_symbol: str, url: str) -> list[dict[str, object]]:
    rows = []
    match = re.search(r"<!-- TS\s*(.*?)\s*TE -->", html, flags=re.DOTALL)
    if not match:
        return rows
    for line in match.group(1).splitlines():
        parts = [part.strip() for part in line.split("|")]
        if len(parts) != 3:
            continue
        symbol, close_text, volume_text = parts
        try:
            close = float(close_text)
            volume = int(float(volume_text))
        except ValueError:
            continue
        rows.append(
            {
                "symbol": symbol.upper(),
                "sector_etf": sector_symbol.upper(),
                "close": close,
                "volume": volume,
                "finviz_url": url,
            }
        )
    return rows


def _fetch_text(url: str) -> str:
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "Mozilla/5.0 trade-strategy-weekly-scan/0.1")
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read().decode("utf-8", errors="ignore")


def fetch_sector_holdings(sector_symbol: str) -> pd.DataFrame:
    symbol = sector_symbol.upper()
    url = SSGA_HOLDINGS_URL_TEMPLATE.format(symbol=symbol.lower())
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "trade-strategy-weekly-scan/0.1")
    with urllib.request.urlopen(req, timeout=30) as response:
        content = response.read()
    return parse_sector_holdings_xlsx(content, symbol)


def parse_sector_holdings_xlsx(content: bytes, sector_symbol: str) -> pd.DataFrame:
    rows = _read_xlsx_rows(content)
    header_index = None
    for index, row in enumerate(rows):
        normalized = [cell.strip().lower() for cell in row]
        if "ticker" in normalized and "name" in normalized and "weight" in normalized:
            header_index = index
            break
    if header_index is None:
        raise ValueError(f"Could not find holdings header in {sector_symbol} workbook")

    header = [cell.strip().lower() for cell in rows[header_index]]
    name_index = header.index("name")
    ticker_index = header.index("ticker")
    weight_index = header.index("weight")

    holdings = []
    for row in rows[header_index + 1 :]:
        if len(row) <= max(name_index, ticker_index, weight_index):
            continue
        ticker = row[ticker_index].strip().upper()
        name = row[name_index].strip()
        if not ticker or ticker in {"-", "CASH_USD"}:
            continue
        if " " in ticker:
            continue
        try:
            weight = float(row[weight_index])
        except ValueError:
            continue
        holdings.append(
            {
                "symbol": ticker,
                "name": name,
                "sector_etf": sector_symbol.upper(),
                "weight": weight,
            }
        )

    return pd.DataFrame(holdings)


def _read_xlsx_rows(content: bytes) -> list[list[str]]:
    namespace = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(BytesIO(content)) as archive:
        shared_strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared_root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in shared_root.findall("x:si", namespace):
                shared_strings.append("".join(text.text or "" for text in item.findall(".//x:t", namespace)))

        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        parsed_rows = []
        for row in sheet.findall(".//x:row", namespace):
            values = []
            next_index = 0
            for cell in row.findall("x:c", namespace):
                reference = cell.get("r", "")
                cell_index = _cell_column_index(reference) if reference else next_index
                while len(values) < cell_index:
                    values.append("")
                value = cell.find("x:v", namespace)
                if value is None or value.text is None:
                    values.append("")
                elif cell.get("t") == "s":
                    values.append(shared_strings[int(value.text)])
                else:
                    values.append(value.text)
                next_index = cell_index + 1
            parsed_rows.append(values)
    return parsed_rows


def _cell_column_index(reference: str) -> int:
    letters = []
    for character in reference:
        if character.isalpha():
            letters.append(character.upper())
        else:
            break
    index = 0
    for letter in letters:
        index = index * 26 + (ord(letter) - ord("A") + 1)
    return max(index - 1, 0)


def run_momentum_scan(
    universe: pd.DataFrame,
    data_dir: str | Path,
    config: ScanConfig | None = None,
) -> tuple[pd.DataFrame, dict[str, object]]:
    if config is None:
        config = ScanConfig()

    data_path = Path(data_dir)
    sector_prices = {
        symbol: load_price_csv(data_path / f"{symbol}.csv")
        for symbol in SPDR_SECTOR_SYMBOLS
        if (data_path / f"{symbol}.csv").exists()
    }
    if len(sector_prices) < config.top_sector_count:
        raise ValueError("Not enough sector ETF data files exist for the requested top sector count")

    sector_signal = latest_sector_signal(
        sector_prices,
        SectorRotationConfig(top_n=config.top_sector_count),
    )
    leading_sectors = set(sector_signal["selected_symbols"])
    if config.leading_sector_stocks_only:
        universe = universe.loc[
            universe["sector_etf"].isin(leading_sectors) | universe["symbol"].isin(leading_sectors)
        ]

    rows = []
    for _, item in universe.iterrows():
        symbol = str(item["symbol"])
        sector_etf = str(item.get("sector_etf", "")).upper()
        csv_path = data_path / f"{symbol}.csv"
        if not csv_path.exists():
            rows.append({"symbol": symbol, "status": "missing_data", "sector_etf": sector_etf})
            continue

        frame = load_price_csv(csv_path)
        score = score_symbol(symbol, frame, sector_etf, leading_sectors, config)
        rows.append(score)

    result = pd.DataFrame(rows)
    for column, default in (
        ("symbol", ""),
        ("status", "watch"),
        ("sector_etf", ""),
        ("sector_leader", False),
        ("score", 0.0),
        ("volume_ratio", 0.0),
        ("momentum", 0.0),
        ("exit_signal", False),
    ):
        if column not in result:
            result[column] = default
    ranked = result.loc[result["status"].eq("candidate")].sort_values(
        ["sector_leader", "score", "volume_ratio", "momentum"],
        ascending=[False, False, False, False],
    )
    non_candidates = result.loc[~result["status"].eq("candidate")]
    return pd.concat([ranked, non_candidates], ignore_index=True), sector_signal


def score_symbol(
    symbol: str,
    frame: pd.DataFrame,
    sector_etf: str,
    leading_sectors: set[str],
    config: ScanConfig,
) -> dict[str, object]:
    required_rows = max(config.long_sma_window, config.volume_window, config.momentum_lookback_days) + 1
    if len(frame) < required_rows:
        return {"symbol": symbol, "status": "insufficient_history", "sector_etf": sector_etf}

    close = frame["close"]
    volume = frame["volume"] if "volume" in frame else pd.Series(0.0, index=frame.index)
    sma = simple_moving_average(close, config.sma_window)
    long_sma = simple_moving_average(close, config.long_sma_window)
    avg_volume = volume.rolling(config.volume_window, min_periods=config.volume_window).mean()

    latest_close = float(close.iloc[-1])
    latest_sma = float(sma.iloc[-1])
    latest_long_sma = float(long_sma.iloc[-1])
    latest_volume = float(volume.iloc[-1])
    latest_avg_volume = float(avg_volume.iloc[-1])
    volume_ratio = latest_volume / latest_avg_volume if latest_avg_volume > 0 else 0.0
    momentum = latest_close / float(close.iloc[-config.momentum_lookback_days - 1]) - 1.0

    above_50 = latest_close > latest_sma
    above_200 = latest_close > latest_long_sma
    strong_volume = volume_ratio >= config.min_volume_ratio
    positive_momentum = momentum > 0
    sector_leader = sector_etf in leading_sectors or symbol in leading_sectors
    candidate = above_50 and above_200 and strong_volume and positive_momentum

    score = momentum * volume_ratio
    if sector_leader:
        score *= 1.25

    return {
        "symbol": symbol,
        "status": "candidate" if candidate else "watch",
        "sector_etf": sector_etf,
        "sector_leader": sector_leader,
        "close": latest_close,
        "sma_50": latest_sma,
        "sma_200": latest_long_sma,
        "volume_ratio": volume_ratio,
        "momentum": momentum,
        "score": score,
        "exit_signal": latest_close < latest_sma or momentum <= 0,
    }


def load_holdings(path: str | Path | None) -> pd.DataFrame:
    if path is None:
        return pd.DataFrame(columns=["symbol"])
    holdings = pd.read_csv(path)
    holdings.columns = [column.strip().lower() for column in holdings.columns]
    if "symbol" not in holdings.columns:
        raise ValueError("Holdings CSV must include a symbol column")
    holdings["symbol"] = holdings["symbol"].astype(str).str.upper().str.strip()
    return holdings.drop_duplicates("symbol")


def format_scan_message(
    scan: pd.DataFrame,
    sector_signal: dict[str, object],
    max_results: int,
    holdings: pd.DataFrame | None = None,
    leading_sector_stocks_only: bool = False,
) -> str:
    return "\n\n".join(
        [
            format_sector_flow_message(sector_signal),
            format_stock_candidates_message(
                scan,
                sector_signal,
                max_results,
                holdings=holdings,
                leading_sector_stocks_only=leading_sector_stocks_only,
            ),
        ]
    )


def format_sector_flow_message(sector_signal: dict[str, object]) -> str:
    leading = ",".join(sector_signal["selected_symbols"])
    lines = [
        f"Leading sector flow: {leading}",
        f"Signal date: {sector_signal['signal_date'].date()}",
    ]
    target_exposures = sector_signal.get("target_exposures", {})
    if target_exposures:
        lines.append("")
        lines.append("Sector exposure:")
        for symbol, exposure in target_exposures.items():
            lines.append(f"{symbol}: {exposure:.2%}")
    return "\n".join(lines)


def format_stock_candidates_message(
    scan: pd.DataFrame,
    sector_signal: dict[str, object],
    max_results: int,
    holdings: pd.DataFrame | None = None,
    leading_sector_stocks_only: bool = False,
) -> str:
    candidates = scan.loc[scan["status"].eq("candidate")]
    held_symbols = set() if holdings is None or holdings.empty else set(holdings["symbol"].tolist())
    exit_signal = scan["exit_signal"].eq(True)
    exits = scan.loc[scan["symbol"].isin(held_symbols) & exit_signal]
    leading = ",".join(sector_signal["selected_symbols"])
    lines = [
        f"Stock scan for sector flow: {leading}",
    ]

    if not exits.empty:
        lines.append("")
        lines.append("Exit candidates:")
        for _, row in exits.iterrows():
            lines.append(
                f"{row['symbol']}: close {row['close']:.2f}, "
                f"50SMA {row['sma_50']:.2f}, 20d mom {row['momentum']:.2%}"
            )

    if candidates.empty:
        lines.append("")
        if leading_sector_stocks_only:
            lines.append(
                "No stock buy candidates in leading sectors passed: "
                "above 50SMA, above 200SMA, strong volume, positive momentum."
            )
        else:
            lines.append("No buy candidates passed: above 50SMA, above 200SMA, strong volume, positive momentum.")
        return "\n".join(lines)

    lines.append("")
    if leading_sector_stocks_only:
        lines.append("Stock buy candidates in leading sectors:")
    else:
        lines.append("Buy candidates:")
    if "screen_name" in candidates.columns:
        for screen_name, screen_rows in candidates.groupby("screen_name", sort=False):
            lines.append(f"{screen_name}:")
            for _, row in screen_rows.head(max_results).iterrows():
                lines.append(_format_candidate_line(row))
    else:
        for _, row in candidates.head(max_results).iterrows():
            lines.append(_format_candidate_line(row))

    lines.append("")
    lines.append("Exit rule: close below 50SMA or 20-day momentum turns negative.")
    return "\n".join(lines)


def _format_candidate_line(row: pd.Series) -> str:
    sector = f" {row['sector_etf']}" if row.get("sector_etf") else ""
    leader = " sector-flow" if bool(row.get("sector_leader")) else ""
    if "volume" in row and pd.notna(row.get("volume")):
        return (
            f"{row['symbol']}{sector}: close {row['close']:.2f}, "
            f"volume {int(row['volume']):,}{leader}"
        )
    return (
        f"{row['symbol']}{sector}: close {row['close']:.2f}, "
        f"vol {row['volume_ratio']:.2f}x, 20d mom {row['momentum']:.2%}{leader}"
    )
