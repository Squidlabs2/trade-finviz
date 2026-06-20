import pandas as pd
import zipfile

from trade_strategy.scanner import (
    ScanConfig,
    build_finviz_sector_url,
    fetch_sector_holdings_universe,
    format_scan_message,
    format_sector_flow_message,
    format_stock_candidates_message,
    load_universe,
    parse_sector_holdings_xlsx,
    parse_finviz_chart_rows,
    run_momentum_scan,
)


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


def test_load_universe_defaults_to_sector_etfs():
    universe = load_universe(None)

    assert "symbol" in universe
    assert "sector_etf" in universe
    assert "XLK" in universe["symbol"].tolist()


def test_weekly_scan_finds_volume_momentum_candidate(tmp_path):
    sector_values = [100 + index * 0.05 for index in range(260)]
    candidate_values = [50 + index * 0.08 for index in range(260)]
    candidate_volume = [1_000_000 for _ in range(259)] + [2_000_000]
    for symbol in ["XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY"]:
        _price_frame(sector_values).to_csv(tmp_path / f"{symbol}.csv", index=False)
    _price_frame(candidate_values, candidate_volume).to_csv(tmp_path / "TEST.csv", index=False)
    universe = pd.DataFrame({"symbol": ["TEST"], "sector_etf": ["XLK"]})

    scan, signal = run_momentum_scan(
        universe,
        tmp_path,
        ScanConfig(
            sma_window=50,
            long_sma_window=200,
            volume_window=50,
            min_volume_ratio=1.25,
            momentum_lookback_days=20,
        ),
    )
    message = format_scan_message(scan, signal, max_results=5)

    assert scan.iloc[0]["symbol"] == "TEST"
    assert scan.iloc[0]["status"] == "candidate"
    assert "TEST" in message


def test_weekly_scan_can_limit_candidates_to_leading_sectors(tmp_path):
    sector_values = [100 + index * 0.05 for index in range(260)]
    candidate_values = [50 + index * 0.08 for index in range(260)]
    candidate_volume = [1_000_000 for _ in range(259)] + [2_000_000]
    for symbol in ["XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY"]:
        _price_frame(sector_values).to_csv(tmp_path / f"{symbol}.csv", index=False)
    _price_frame(candidate_values, candidate_volume).to_csv(tmp_path / "LEAD.csv", index=False)
    _price_frame(candidate_values, candidate_volume).to_csv(tmp_path / "OTHER.csv", index=False)
    universe = pd.DataFrame(
        {
            "symbol": ["LEAD", "OTHER"],
            "sector_etf": ["XLB", "XLF"],
        }
    )

    scan, signal = run_momentum_scan(
        universe,
        tmp_path,
        ScanConfig(
            sma_window=50,
            long_sma_window=200,
            volume_window=50,
            min_volume_ratio=1.25,
            momentum_lookback_days=20,
            leading_sector_stocks_only=True,
        ),
    )
    message = format_scan_message(
        scan,
        signal,
        max_results=5,
        leading_sector_stocks_only=True,
    )

    assert set(scan["sector_etf"]).issubset(set(signal["selected_symbols"]))
    assert "Stock buy candidates in leading sectors" in message


def test_scan_message_reports_holding_exit():
    scan = pd.DataFrame(
        [
            {
                "symbol": "EXIT",
                "status": "watch",
                "close": 95.0,
                "sma_50": 100.0,
                "momentum": -0.02,
                "exit_signal": True,
            }
        ]
    )
    signal = {"selected_symbols": ["XLK"], "signal_date": pd.Timestamp("2026-05-22")}
    holdings = pd.DataFrame({"symbol": ["EXIT"]})

    message = format_scan_message(scan, signal, max_results=5, holdings=holdings)

    assert "Exit candidates" in message
    assert "EXIT" in message


def test_sector_and_stock_messages_are_separate():
    scan = pd.DataFrame(
        [
            {
                "symbol": "BUY",
                "status": "candidate",
                "sector_etf": "XLK",
                "sector_leader": True,
                "close": 120.0,
                "volume_ratio": 1.5,
                "momentum": 0.08,
                "exit_signal": False,
            }
        ]
    )
    signal = {
        "selected_symbols": ["XLK", "XLE", "XLV"],
        "signal_date": pd.Timestamp("2026-05-22"),
        "target_exposures": {"XLK": 0.2, "XLE": 0.1, "XLV": 0.15},
    }

    sector_message = format_sector_flow_message(signal)
    stock_message = format_stock_candidates_message(
        scan,
        signal,
        max_results=5,
        leading_sector_stocks_only=True,
    )

    assert "Leading sector flow" in sector_message
    assert "XLK - Technology" in sector_message
    assert "XLE - Energy" in sector_message
    assert "Sector exposure" in sector_message
    assert "BUY" not in sector_message
    assert "Stock scan for sector flow: XLK - Technology, XLE - Energy, XLV - Health Care" in stock_message
    assert "Stock buy candidates in leading sectors" in stock_message
    assert "XLK - Technology:" in stock_message
    assert "BUY (XLK - Technology)" in stock_message


def test_parse_sector_holdings_xlsx_extracts_tickers(tmp_path):
    workbook = tmp_path / "holdings.xlsx"
    shared = """<?xml version="1.0" encoding="UTF-8"?>
<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <si><t>Name</t></si><si><t>Ticker</t></si><si><t>Weight</t></si>
  <si><t>NVIDIA CORP</t></si><si><t>NVDA</t></si>
  <si><t>APPLE INC</t></si><si><t>AAPL</t></si>
</sst>"""
    sheet = """<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>
    <row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c><c r="E1" t="s"><v>2</v></c></row>
    <row r="2"><c r="A2" t="s"><v>3</v></c><c r="B2" t="s"><v>4</v></c><c r="E2"><v>14.5</v></c></row>
    <row r="3"><c r="A3" t="s"><v>5</v></c><c r="B3" t="s"><v>6</v></c><c r="E3"><v>12.0</v></c></row>
  </sheetData>
</worksheet>"""
    with zipfile.ZipFile(workbook, "w") as archive:
        archive.writestr("xl/sharedStrings.xml", shared)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)

    holdings = parse_sector_holdings_xlsx(workbook.read_bytes(), "XLK")

    assert holdings["symbol"].tolist() == ["NVDA", "AAPL"]
    assert holdings["sector_etf"].tolist() == ["XLK", "XLK"]


def test_parse_finviz_chart_rows_reads_chart_comment():
    html = """<html><!-- TS
NVO|44.96|10517400
AVTR|8.12|7201856
TE --></html>"""

    rows = parse_finviz_chart_rows(html, "XLV", "https://finviz.example")

    assert [row["symbol"] for row in rows] == ["NVO", "AVTR"]
    assert rows[0]["sector_etf"] == "XLV"
    assert rows[0]["volume"] == 10517400


def test_finviz_candidates_are_interleaved_by_sector_rank():
    from trade_strategy import scanner

    def fake_pages(sector_symbol, _finviz_sector, _max_results, _rsi_mode):
        return [
            {"symbol": f"{sector_symbol}A", "sector_etf": sector_symbol, "close": 10.0, "volume": 100, "finviz_url": ""},
            {"symbol": f"{sector_symbol}B", "sector_etf": sector_symbol, "close": 9.0, "volume": 90, "finviz_url": ""},
        ]

    original = scanner._fetch_finviz_sector_pages
    scanner._fetch_finviz_sector_pages = fake_pages
    try:
        rows = scanner.fetch_finviz_sector_candidates(["XLK", "XLE"], max_results_per_sector=2)
    finally:
        scanner._fetch_finviz_sector_pages = original

    assert rows["symbol"].tolist() == ["XLKA", "XLEA", "XLKB", "XLEB"]
    assert rows["screen_name"].tolist() == ["RSI not over 60"] * 4


def test_finviz_sector_url_uses_sector_wide_filter():
    url = build_finviz_sector_url("XLV")
    over_60_url = build_finviz_sector_url("XLV", rsi_mode="over-60")

    assert "sec_healthcare" in url
    assert "ta_sma50_pa" in url
    assert "sh_curvol_o500" in url
    assert "ta_rsi_nob60" in url
    assert "ta_rsi_ob60" in over_60_url


def test_stock_candidates_message_groups_finviz_rsi_screens():
    scan = pd.DataFrame(
        [
            {
                "symbol": "EARLY",
                "status": "candidate",
                "sector_etf": "XLV",
                "sector_leader": True,
                "close": 12.0,
                "volume": 1_000_000,
                "exit_signal": False,
                "screen_name": "RSI not over 60",
            },
            {
                "symbol": "STRONG",
                "status": "candidate",
                "sector_etf": "XLV",
                "sector_leader": True,
                "close": 14.0,
                "volume": 2_000_000,
                "exit_signal": False,
                "screen_name": "RSI over 60",
            },
        ]
    )
    signal = {"selected_symbols": ["XLV"], "signal_date": pd.Timestamp("2026-05-22")}

    message = format_stock_candidates_message(
        scan,
        signal,
        max_results=5,
        leading_sector_stocks_only=True,
    )

    assert "XLV - Health Care:" in message
    assert "RSI not over 60:" in message
    assert "EARLY (XLV - Health Care)" in message
    assert "RSI over 60:" in message
    assert "STRONG (XLV - Health Care)" in message
