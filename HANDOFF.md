# Finviz Weekly Scan Handoff

This project runs the Finviz-based weekly sector momentum scan and sends two ntfy messages every Friday after market close.

## Location

- Repo: `git@github.com:Squidlabs2/trade-finviz.git`
- Local path: `~/trade-finviz`
- Python env: `~/trade-finviz/.venv`

## ntfy

- Server: `https://ntfy.squidlabs.xyz`
- Topic: `weekly-finviz`
- Config source: `~/trade-finviz/.env`

Current `.env` values:

```bash
NTFY_URL=https://ntfy.squidlabs.xyz
NTFY_TOPIC=weekly-finviz
NTFY_TOKEN=
```

## Scheduler

- systemd user unit: `weekly-scan.service`
- systemd user timer: `weekly-scan.timer`
- Schedule: `Fri 16:15:00` local machine time

Timer files:

- `~/.config/systemd/user/weekly-scan.service`
- `~/.config/systemd/user/weekly-scan.timer`

Important fix:

- `~/.config/systemd/user/timers.target.wants/weekly-scan.timer` must be a symlink to `../weekly-scan.timer`
- A plain file there leaves the timer disabled even if the unit file exists

## What the script does

Entry point:

- `~/trade-finviz/scripts/weekly_scan.sh`

It runs:

```bash
PYTHONPATH=src .venv/bin/python -m trade_strategy.cli weekly-scan \
  --data-dir data \
  --use-finviz-sector-screener \
  --finviz-rsi-mode both \
  --holdings examples/holdings.csv \
  --leading-sector-stocks-only \
  --refresh-data \
  --fetch-start 2020-01-01 \
  --notify \
  --output outputs/weekly_scan.csv
```

Behavior:

- ranks sector flow first
- scans only the current leading sectors
- sends two ntfy messages:
  - `Weekly sector flow`
  - `Weekly stock candidates`
- groups candidates into:
  - `RSI not over 60`
  - `RSI over 60`

## Verification commands

Check timer:

```bash
systemctl --user status weekly-scan.timer
systemctl --user list-timers weekly-scan.timer
```

Run once now:

```bash
systemctl --user start weekly-scan.service
journalctl --user -u weekly-scan.service -n 50
```

Check ntfy topic directly:

```bash
curl https://ntfy.squidlabs.xyz/weekly-finviz/json?poll=1
```

Direct ntfy publish test:

```bash
curl -d 'test message' -H 'Title: ntfy direct test' \
  https://ntfy.squidlabs.xyz/weekly-finviz
```

## Current known-good state

Verified on the new machine `greg@192.168.1.26`:

- repo cloned to `~/trade-finviz`
- `.venv` exists and dependencies are installed
- manual `weekly-scan.service` run succeeded
- ntfy messages were accepted by `weekly-finviz`
- timer is enabled and waiting for the next Friday run

## If Codex is resuming work

Open Codex in `~/trade-finviz` and start with:

`Read HANDOFF.md and continue maintaining this Finviz weekly scan setup.`
