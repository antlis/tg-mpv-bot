# tg-mpv-bot 🎬

Standalone Telegram bot for mpv media control — play, pause, list, seek, volume
control via Telegram commands. Zero AI token cost (pure subprocess dispatch).

Extracted from the Lain bot (Hermes) into its own dedicated bot for independent
operation. Same `mpvctl.sh` under the hood.

## Commands

| Command | Description |
|---------|-------------|
| `/mpv_pause` | Pause playback |
| `/mpv_unpause` | Resume playback |
| `/mpv_quit` | Stop mpv and quit |
| `/mpv_volup` | Volume +10 |
| `/mpv_voldown` | Volume -10 |
| `/mpv_mute` | Toggle mute |
| `/mpv_list` | List playlists (cartoons, movie, shows) |
| `/mpv_info` | Show current playback status |
| `/mpv_fwd` | Seek forward 30s |
| `/mpv_back` | Seek backward 10s |
| `/mpv_play <query>` | Search & play by name or number |
| `/mpv <query>` | Alias for `/mpv_play` |
| `/help` | Show this help |

## Quick Start

```bash
# 1. Create bot on @BotFather, get token
# 2. Copy env and configure
cp .env.example .env
# Edit .env → set BOT_TOKEN from @BotFather

# 3. Run with Docker
docker compose up -d --build

# Or run bare (no Docker)
pip install -r requirements.txt
python bot.py
```

## Files

| Path | Purpose |
|------|---------|
| `bot.py` | Entry point — aiogram polling bot |
| `src/config.py` | Settings dataclass from env |
| `src/commands.py` | Telegram command handlers → `mpvctl.sh` |
| `mpvctl.sh` | Zero-token mpv control via IPC socket |
| `docker-compose.yml` | Docker deployment (host networking + X11 bind) |
| `Dockerfile` | Container build (Python 3.12 + mpv) |
| `scripts/tg-mpvctl.sh` | Hermes control script (start/stop/status) |

## Deployment Options

### Docker (recommended)

```bash
cd ~/Projects/tg-mpv-bot
docker compose up -d --build
```

The container uses `network_mode: host` and binds X11, i3, and video
directories for full mpv control.

### Systemd user service

```bash
ln -s ~/Projects/tg-mpv-bot/tg-mpv-bot.service ~/.config/systemd/user/
mkdir -p ~/.config/environment.d
cat > ~/.config/environment.d/99-tg-mpv-bot.conf << EOF
BOT_TOKEN=your_token_here
ALLOWED_USERS=418870313
EOF
systemctl --user daemon-reload
systemctl --user start tg-mpv-bot
systemctl --user enable tg-mpv-bot
```

### Hermes integration (via control script)

```bash
# Symlink control script to hermes scripts dir
ln -s ~/Projects/tg-mpv-bot/scripts/tg-mpvctl.sh ~/.hermes/scripts/tg-mpvctl.sh

# Then via quick_commands:
# /tg_mpv_start   — docker compose up -d --build
# /tg_mpv_stop    — docker compose down
# /tg_mpv_status  — check if running
```

## Access Control

Set `ALLOWED_USERS` in `.env` as comma-separated Telegram user IDs:

```env
ALLOWED_USERS=418870313,123456789
```

Leave empty to allow everyone.

## Architecture

```
Telegram ──▶ tg-mpv-bot (polling) ──▶ mpvctl.sh ──▶ mpv IPC (/tmp/mpv-socket)
                                  │                    └── X11 window
                                  └── i3-msg workspace 10
```

All heavy lifting is in `mpvctl.sh` — the bot is a thin aiogram wrapper.
For detailed mpvctl.sh reference, see `~/.hermes/skills/productivity/mpv-playlist-controller/`.
