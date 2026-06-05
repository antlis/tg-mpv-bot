# tg-mpv-bot 🎬

Standalone Telegram bot for mpv media control — play, pause, browse, seek,
volume control via Telegram commands. Zero AI token cost (pure IPC dispatch).

Extracted from the Lain bot (Hermes) into its own dedicated bot for independent
operation. The bot talks to mpv's JSON IPC socket directly from Python.

## Commands

| Command | Description |
|---------|-------------|
| `/mpv_list` | Browse playlists with inline buttons (by category) |
| `/mpv_play <query>` | Search & play by name or number |
| `/mpv <query>` | Alias for `/mpv_play` |
| `/mpv_search [category] <text>` | List all matching playlists as play buttons (e.g. `/mpv_search tutorials docker`) |
| `/mpv_last` | Resume the last-played playlist (position is restored by mpv) |
| `/mpv_info` | Now-playing panel with inline transport buttons |
| `/mpv_toggle` | Play/pause toggle (one command) |
| `/mpv_pause` | Pause playback |
| `/mpv_unpause` | Resume playback |
| `/mpv_quit` | Stop mpv and quit |
| `/mpv_fwd` | Seek forward 30s |
| `/mpv_back` | Seek backward 10s |
| `/mpv_next` | Next item in playlist |
| `/mpv_prev` | Previous item in playlist |
| `/mpv_ep <n>` | Jump to item N in the playlist |
| `/mpv_shuffle` | Shuffle the current playlist |
| `/mpv_loop` | Toggle looping the playlist |
| `/mpv_audio` | Switch to the next audio track (e.g. Spanish → English) |
| `/mpv_sub` | Switch to the next subtitle track |
| `/mpv_sub_toggle` | Show / hide subtitles |
| `/mpv_volup` | Volume +10 |
| `/mpv_voldown` | Volume -10 |
| `/mpv_mute` | Toggle mute |
| `/mpv_doctor` | Report playlists with missing files on disk |
| `/mpv_fix` | Repair broken playlists (re-point moved files, prune dead) |
| `/mpv_scan` | Create playlists for newly-added media (idempotent) |
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

## Tests & lint

```bash
pip install -r requirements-dev.txt
pytest
ruff check .
```

## Files

| Path | Purpose |
|------|---------|
| `bot.py` | Entry point — aiogram polling bot + auth middleware |
| `src/config.py` | Settings from env (single source of host paths) |
| `src/commands.py` | Telegram command + callback handlers |
| `src/mpv_ipc.py` | Direct JSON-IPC client for mpv (pause/seek/volume/info) |
| `src/playlists.py` | Playlist discovery, query matching, on-disk validation |
| `src/player.py` | Launch mpv (pkill + pre/post-play hooks + detached spawn) |
| `src/keyboards.py` | Inline-keyboard builders for browsing |
| `mpvctl.sh` | Standalone shell controller (Hermes/CLI use; not used by the bot) |
| `docker-compose.yml` | Docker deployment (host networking + X11 bind) |
| `Dockerfile` | Container build (Python 3.12 + mpv) |
| `scripts/tg-mpvctl.sh` | Hermes control script (start/stop/status) |

## Deployment Options

### Docker (recommended)

```bash
cd ~/Projects/tg-mpv-bot
docker compose up -d --build
```

The container uses `network_mode: host` and binds X11 and video
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
                          ┌─ src/mpv_ipc  ──▶ mpv JSON IPC (/tmp/mpv-socket)   pause/seek/vol/info
Telegram ─▶ bot.py ─▶ src/commands ─┤
            (polling)   (+ auth mw)  ├─ src/playlists ──▶ scan ~/Videos/*/playlists/*.m3u
                                     └─ src/player   ──▶ pkill mpv · pre-hook · spawn mpv · post-hook ─▶ X11
```

Playback/volume/info commands write straight to mpv's IPC socket from Python.
Only launching a playlist (`/mpv_play`, tapping a button) spawns a process.

Window-manager glue is **not** built in — set the optional hooks instead
(shell commands; they see `$PLAYLIST`, `$PLAYLIST_NAME`, `$MPV_SOCKET`,
`$DISPLAY`):

```bash
PRE_PLAY_HOOK="i3-msg workspace 10"          # i3: jump to the media workspace
PRE_PLAY_HOOK="swaymsg workspace 10"         # sway
PRE_PLAY_HOOK="hyprctl dispatch workspace 10"  # Hyprland
POST_PLAY_HOOK='notify-send "Now playing" "$PLAYLIST_NAME"'
```

Hook failures are logged and never block playback (15s timeout).
`src/keyboards` renders the browse UI for `/mpv_list`: **category → (subcategory)
→ playlist**. Categories come from the top-level media dirs (cartoons / movie /
shows / tutorials); a playlists dir may nest one level of folders, which become
subcategories (tutorials are grouped by provider, e.g. `frontend-masters`).

`mpvctl.sh` remains as a standalone shell controller for Hermes/CLI use but is
no longer invoked by the bot.
