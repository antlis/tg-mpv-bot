#!/usr/bin/env bash
# tg-mpvctl.sh — Start/stop/status the tg-mpv-bot Docker service
# Usage: tg-mpvctl.sh {start|stop|restart|status}
set -euo pipefail

BOT_DIR="$HOME/Projects/tg-mpv-bot"

case "${1:-}" in
  start)
    cd "$BOT_DIR" || { echo "❌ Directory not found: $BOT_DIR"; exit 1; }
    if docker compose ps --services --filter "status=running" 2>/dev/null | grep -q "bot"; then
      echo "⚠️  Already running"
      docker compose ps --format "table {{.Name}}\t{{.Status}}"
      exit 0
    fi
    echo "▶ Starting tg-mpv-bot..."
    docker compose up -d --build 2>&1
    # Poll for readiness
    local timeout=30
    while [ $timeout -gt 0 ]; do
      if docker compose logs bot 2>/dev/null | grep -q "tg-mpv-bot starting"; then
        echo "✅ tg-mpv-bot ready"
        exit 0
      fi
      sleep 1
      timeout=$((timeout - 1))
    done
    echo "⚠️  Timed out waiting for ready signal"
    docker compose ps
    ;;
  stop)
    cd "$BOT_DIR" || { echo "❌ Directory not found: $BOT_DIR"; exit 1; }
    docker compose down
    echo "⏹  Stopped"
    ;;
  restart)
    cd "$BOT_DIR" || { echo "❌ Directory not found: $BOT_DIR"; exit 1; }
    docker compose down
    echo "♻️  Restarting..."
    sleep 2
    docker compose up -d --build 2>&1
    echo "✅ tg-mpv-bot restarted"
    ;;
  status)
    cd "$BOT_DIR" || { echo "❌ Directory not found: $BOT_DIR"; exit 1; }
    if docker compose ps --services --filter "status=running" 2>/dev/null | grep -q "bot"; then
      echo "✅ Status: RUNNING"
      docker compose ps --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}"
    else
      echo "⏹  Status: STOPPED"
    fi
    ;;
  *)
    echo "Usage: $0 {start|stop|restart|status}"
    exit 1
    ;;
esac
