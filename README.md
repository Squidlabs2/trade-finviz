# Trading Strategy Research Scaffold

This project starts with a conservative daily trend-following research loop for liquid US equities or ETFs.

It is meant for research and paper-trading validation, not financial advice.

## Strategy

The initial baseline is a moving-average trend strategy with volatility targeting:

- Go long when the close is above the long moving average.
- Stay in cash when the close is below the long moving average.
- Size exposure inversely to recent realized volatility.
- Charge transaction costs whenever target exposure changes.

This is intentionally simple. The point is to make the data, assumptions, and risk metrics explicit before adding complexity.

## Setup

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[test]"
```

For a minimal remote host, use the production dependency file instead:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

## Run With The Sample Data

```bash
python -m trade_strategy.cli backtest --csv examples/sample_prices.csv --fast-window 10 --slow-window 30
```

## Run With Your Own CSV

CSV input should contain at least:

- `date`
- `close`

Optional columns:

- `open`
- `high`
- `low`
- `volume`

```bash
python -m trade_strategy.cli backtest --csv data/your_symbol.csv --symbol SPY
```

## Fetch Data From Yahoo Finance

This requires network access and the optional `yfinance` dependency from `requirements.txt`.

```bash
python -m trade_strategy.cli fetch --symbol SPY --start 2005-01-01 --output data/SPY.csv
python -m trade_strategy.cli backtest --csv data/SPY.csv --symbol SPY
```

## Compare An ETF Basket

Fetch several liquid ETFs:

```bash
python -m trade_strategy.cli fetch --symbol SPY --start 2005-01-01 --output data/SPY.csv
python -m trade_strategy.cli fetch --symbol QQQ --start 2005-01-01 --output data/QQQ.csv
python -m trade_strategy.cli fetch --symbol TLT --start 2005-01-01 --output data/TLT.csv
python -m trade_strategy.cli fetch --symbol GLD --start 2005-01-01 --output data/GLD.csv
```

Compare the trend strategy against buy-and-hold:

```bash
python -m trade_strategy.cli compare \
  --csv data/SPY.csv data/QQQ.csv data/TLT.csv data/GLD.csv \
  --symbols SPY QQQ TLT GLD \
  --output outputs/etf_comparison.csv
```

Slice a specific market regime:

```bash
python -m trade_strategy.cli compare \
  --csv data/SPY.csv data/QQQ.csv data/TLT.csv data/GLD.csv \
  --symbols SPY QQQ TLT GLD \
  --start 2020-01-01 \
  --end 2022-12-31
```

## Weekly Sector Rotation

This ranks the current SPDR sector ETFs each Friday after the close using a weekly price-and-dollar-volume flow proxy, then enters the selected sector at the next trading week's open. The selected sector is still filtered and sized with the trend/volatility setup.

Fetch the sector data first:

```bash
for symbol in XLB XLC XLE XLF XLI XLK XLP XLRE XLU XLV XLY; do
  python -m trade_strategy.cli fetch --symbol "$symbol" --start 2005-01-01 --output "data/$symbol.csv"
done
```

Then run the rotation test:

```bash
python -m trade_strategy.cli sector-rotation \
  --data-dir data \
  --top-n 3 \
  --output outputs/sector_rotation.csv \
  --trades-output outputs/sector_rotation_trades.csv
```

## Weekly Momentum Scan With ntfy

The weekly scan looks for symbols that pass this rule set:

- Close is above the 50-day simple moving average.
- Close is above the 200-day simple moving average.
- Latest volume is at least 1.25x the 50-day average volume.
- 20-day momentum is positive.
- Symbols in the strongest weekly sector-flow ETFs are ranked higher.

This is a known style of strategy: trend following plus relative strength plus volume confirmation. It is similar in spirit to momentum/relative-strength systems such as CAN SLIM or Minervini-style trend templates, but simplified and fully rule-based.

Create a universe CSV with at least `symbol`; optionally add `sector_etf`:

```csv
symbol,sector_etf
MSFT,XLK
NVDA,XLK
LLY,XLV
XLE,XLE
```

Run a dry scan:

```bash
python -m trade_strategy.cli weekly-scan \
  --data-dir data \
  --use-finviz-sector-screener \
  --finviz-rsi-mode both \
  --holdings examples/holdings.csv \
  --leading-sector-stocks-only \
  --output outputs/weekly_scan.csv
```

Send an ntfy notification:

```bash
export NTFY_URL="https://ntfy.example.com"
export NTFY_TOPIC="your-topic"
export NTFY_TOKEN="optional-token"

python -m trade_strategy.cli weekly-scan \
  --data-dir data \
  --use-finviz-sector-screener \
  --finviz-rsi-mode both \
  --holdings examples/holdings.csv \
  --leading-sector-stocks-only \
  --refresh-data \
  --notify
```

With `--notify`, the command sends two ntfy messages in order:

1. `Weekly sector flow`: the leading sectors and their target exposure.
2. `Weekly stock candidates`: individual stocks in those sectors that pass the buy rules, plus any exit candidates from `--holdings`.

When `--use-sector-holdings` is enabled, the stock universe is built from the daily holdings files for the leading State Street sector ETFs, rather than only from the local starter universe.

When `--use-finviz-sector-screener` is enabled, the stock candidates come from a sector-wide Finviz-style screen for the leading sectors. This is broader than ETF holdings and mirrors filters like:

- sector equals the leading sector
- average volume over 400K
- current volume over 500K
- price from $5 to $50
- 4-week performance up
- RSI not over 60, RSI over 60, or both
- price above 50SMA

Use `--finviz-rsi-mode not-over-60` for earlier/less-extended setups, `--finviz-rsi-mode over-60` for stronger momentum setups like the linked Finviz screen, or `--finviz-rsi-mode both` to send both groups in the weekly notification.

For a weekly Friday-after-close timer on a remote Linux machine, create `.env` and install the user timer:

```bash
cp .env.example .env
nano .env
scripts/install_weekly_timer.sh
```

The timer runs Friday at 4:15 PM in the machine's local timezone.

To run the scan once immediately:

```bash
scripts/weekly_scan.sh
```

To check the scheduled job:

```bash
systemctl --user status weekly-scan.timer
journalctl --user -u weekly-scan.service -n 100
```

## Useful Parameters

```bash
python -m trade_strategy.cli backtest \
  --csv data/SPY.csv \
  --fast-window 50 \
  --slow-window 200 \
  --vol-window 20 \
  --target-vol 0.12 \
  --max-exposure 1.0 \
  --cost-bps 1.0
```

## Next Research Steps

1. Compare against buy-and-hold.
2. Add out-of-sample date splits.
3. Test a basket of ETFs rather than one symbol.
4. Add drawdown controls and rebalance limits.
5. Build a paper-trading signal exporter only after the research metrics are credible.
