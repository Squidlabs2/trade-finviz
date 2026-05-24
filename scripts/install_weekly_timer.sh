#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SYSTEMD_DIR="${HOME}/.config/systemd/user"

mkdir -p "$SYSTEMD_DIR"

cat > "$SYSTEMD_DIR/weekly-scan.service" <<SERVICE
[Unit]
Description=Weekly market momentum scan

[Service]
Type=oneshot
WorkingDirectory=$PROJECT_DIR
ExecStart=$PROJECT_DIR/scripts/weekly_scan.sh
SERVICE

cat > "$SYSTEMD_DIR/weekly-scan.timer" <<'TIMER'
[Unit]
Description=Run weekly market momentum scan after Friday close

[Timer]
OnCalendar=Fri *-*-* 16:15:00
Persistent=true

[Install]
WantedBy=timers.target
TIMER

systemctl --user daemon-reload
systemctl --user enable --now weekly-scan.timer

echo "Installed weekly-scan.timer for $PROJECT_DIR"
echo "Check status with: systemctl --user status weekly-scan.timer"
echo "Run once now with: systemctl --user start weekly-scan.service"
