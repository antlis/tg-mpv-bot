# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A standalone Telegram bot that remote-controls a local `mpv` instance (play/pause/seek/volume/playlist
selection) over Telegram. No LLM/AI tokens are involved. The bot talks to mpv's JSON IPC socket
directly from Python; only *launching* a playlist spawns a process. Extracted from a larger "Hermes"
bot to run independently.

## Run / develop

```bash
# Bare (uses ./venv per the systemd unit, or any venv)
pip install -r requirements.txt
python bot.py                              # standard Telegram API
API_SERVER_URL=http://localhost:8081 python bot.py   # local Bot API server (2GB uploads)

# Tests
pip install -r requirements-dev.txt
pytest                                     # config (pytest.ini): testpaths=tests, asyncio_mode=auto

# Docker (host networking + X11/i3 bind mounts)
docker compose up -d --build

# systemd user service
systemctl --user start tg-mpv-bot          # reads ~/.config/environment.d/99-tg-mpv-bot.conf
```

Required env: `BOT_TOKEN` (missing → `SystemExit` with a friendly message, not a traceback). Optional:
`ALLOWED_USERS` (comma-separated Telegram user IDs — **empty means open to everyone**, logged as a
warning), `API_SERVER_URL`, and host paths `MPV_SOCKET` / `PLAYLIST_DIRS` / `VIDEOS_DIR` / `MPV_RUNNER`
/ `I3SOCK` / `I3_WORKSPACE` / `DISPLAY`. See `.env.example`.

## Architecture

```
                            ┌─ src/mpv_ipc  ─▶ mpv JSON IPC (/tmp/mpv-socket)  pause/seek/vol/info
Telegram ─▶ bot.py ─▶ src/commands ─┤
            (polling)  (+ auth mw)  ├─ src/playlists ─▶ scan ~/Videos/*/playlists/*.m3u
                                    └─ src/player  ─▶ pkill -x mpv · i3 workspace · spawn mpv ─▶ X11
```

- `bot.py` — entry point. Builds the `Bot` (default `parse_mode=None` — replies are plain text;
  handlers that need formatting set HTML per-message), registers the command menu (`_build_menu`), and
  installs an `outer_middleware` for auth (rejects users not in `ALLOWED_USERS`) **only when
  `settings.is_restricted`**. Switches to a local Bot API server when `API_SERVER_URL` is set.
- `src/config.py` — frozen `Settings` dataclass loaded once via `@lru_cache get_settings()`. **Single
  source of truth for all host paths.** Tests that exercise env must call `get_settings.cache_clear()`.
- `src/mpv_ipc.py` — `MpvClient(socket_path)`: opens a short-lived Unix-socket connection per command,
  writes one JSON line, and reads newline-delimited replies **skipping async `event` lines** until the
  one matching `request_id`. Raises `MpvNotRunning` (socket dead) / `MpvError` (mpv said not-success).
  `adjust_volume` clamps to 0–130. Pure I/O with an injectable path → testable against a fake server.
- `src/playlists.py` — `discover()` (case-insensitive **stable** sort so global indices stay valid
  between a `/mpv_list` render and a later `/mpv_play <n>`; scans `*.m3u` directly in a playlists dir
  **and one level of nested folders**, whose name becomes the playlist's `subcategory`), `find()`
  (numeric index OR case-insensitive substring), and `validate()`/`missing_entries()` (the on-disk
  checker behind `/mpv_doctor`; resolves relative entries against the playlist's own dir, treats URLs
  as always-present).
- `src/player.py` — the only part that spawns a process. `build_launch_command()` is a pure helper
  (used `MPV_RUNNER` if it exists, else falls back to plain `mpv`). `play()` does `pkill -x mpv`,
  optional i3 workspace switch (skipped if `I3SOCK` unset/missing or `i3-msg` absent), then a detached
  `Popen(..., start_new_session=True)` — the Python-native equivalent of the old `setsid` detachment
  for non-TTY contexts (asyncio/systemd).
- `src/keyboards.py` — inline-keyboard builders for `/mpv_list`: **category → (subcategory) →
  paginated playlist buttons** (`PER_PAGE=8`). Flat categories (no subcategories) jump straight to the
  playlist list; categories with subcategories (tutorials → provider) show a subcategory menu first.
  Pure helpers (`categories`/`subcategories`/`indices_for`/`page_*`) are unit-tested. **Index-based**
  callback grammar (stable for a fixed library): `cats`, `c:<ci>[:<page>]`, `s:<ci>:<si>[:<page>]`,
  `pl:<global_index>`, `noop`.
- `src/commands.py` — handlers push blocking IPC/subprocess work to `asyncio.to_thread`. `_ipc()`
  centralizes error→message translation. Play buttons carry the **global** playlist index.

## Things to know before editing

- Adding a command touches **four** in-sync places: a handler in `src/commands.py`, the menu in
  `bot.py:_build_menu()`, the help text in `cmd_help`, and the README command table.
- All host assumptions now flow from `src/config.py` env vars. The deploy files (`docker-compose.yml`,
  `tg-mpv-bot.service`) still set concrete values (UID 1000 i3 socket, `DISPLAY=:0`, `HOME=/home/lad`)
  and must agree with them.
- `mpvctl.sh` is now **standalone/legacy** — kept for Hermes/CLI use (`./mpvctl.sh list|play|info`) but
  the bot no longer invokes it. It still hardcodes its own paths; it is *not* driven by `config.py`.
- The bot does not start mpv itself except via `src/player.play()`. IPC commands surface
  "mpv is not running" when the socket is dead.
</content>
</invoke>
