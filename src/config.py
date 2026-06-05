"""Settings from environment — loaded once at startup.

All host-specific paths live here so there is a single source of truth
(previously these were duplicated across mpvctl.sh, docker-compose.yml and
the systemd unit).
"""

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path


def _default_playlist_dirs() -> list[Path]:
    videos = Path(os.environ.get("VIDEOS_DIR", str(Path.home() / "Videos"))).expanduser()
    cats = ("cartoons", "movie", "shows", "tutorials")
    return [videos / cat / "playlists" for cat in cats]


@dataclass(frozen=True)
class Settings:
    bot_token: str
    allowed_users: list[int] = field(default_factory=list)
    api_server_url: str = ""

    # ── Host / runtime config ────────────────────────────────────────
    mpv_socket: str = "/tmp/mpv-socket"
    playlist_dirs: list[Path] = field(default_factory=_default_playlist_dirs)
    mpv_runner: str = "/tmp/mpv-runner.sh"  # falls back to "mpv" if absent
    display: str = ":0"
    # Shell commands run around the mpv launch (empty → skipped). They get
    # PLAYLIST / PLAYLIST_NAME / MPV_SOCKET / DISPLAY in the environment —
    # e.g. PRE_PLAY_HOOK="i3-msg workspace 10" or a notify-send script.
    pre_play_hook: str = ""
    post_play_hook: str = ""
    # Extra yt-dlp options for URL playback, passed as --ytdl-raw-options
    # (comma-separated key=value), applied to every URL.
    ytdl_options: str = ""
    # Browser whose cookies unlock login-gated sites (Instagram/Facebook).
    # Applied ONLY to those hosts: with YouTube, logged-in cookies make
    # yt-dlp's extraction hang/stall on bot checks, so cookies must not be
    # global (learned the hard way).
    ytdl_cookies_browser: str = ""
    # Also pkill stray mpv instances (ones not started by the bot) before
    # playing. Guarantees a single player on screen, but is rude on machines
    # where mpv is used manually — set KILL_STRAY_MPV=0 there; the bot's own
    # instance is always stopped gracefully over IPC first.
    kill_stray_mpv: bool = True
    lock_file: str = "/tmp/tg-mpv-bot.lock"
    scan_interval_min: int = 0   # >0 → auto-scan for new media every N minutes
    state_file: Path = field(  # remembers the last-played playlist (/mpv_last)
        default_factory=lambda: Path.home() / ".local/state/tg-mpv-bot/state.json"
    )

    @property
    def is_restricted(self) -> bool:
        return len(self.allowed_users) > 0


def _parse_int_list(raw: str | None) -> list[int]:
    if not raw:
        return []
    return [int(x.strip()) for x in raw.split(",") if x.strip()]


def _parse_path_list(raw: str | None) -> list[Path] | None:
    if not raw:
        return None
    return [Path(p.strip()).expanduser() for p in raw.split(os.pathsep) if p.strip()]


@lru_cache
def get_settings() -> Settings:
    """Load settings from the environment (memoised — called once)."""
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise SystemExit(
            "BOT_TOKEN is not set. Copy .env.example to .env and set BOT_TOKEN "
            "(get one from @BotFather), or export it in the environment."
        )

    return Settings(
        bot_token=token,
        allowed_users=_parse_int_list(os.environ.get("ALLOWED_USERS", "")),
        api_server_url=os.environ.get("API_SERVER_URL", ""),
        mpv_socket=os.environ.get("MPV_SOCKET", "/tmp/mpv-socket"),
        playlist_dirs=_parse_path_list(os.environ.get("PLAYLIST_DIRS"))
        or _default_playlist_dirs(),
        mpv_runner=os.environ.get("MPV_RUNNER", "/tmp/mpv-runner.sh"),
        display=os.environ.get("DISPLAY", ":0"),
        pre_play_hook=os.environ.get("PRE_PLAY_HOOK", ""),
        post_play_hook=os.environ.get("POST_PLAY_HOOK", ""),
        ytdl_options=os.environ.get("YTDL_OPTIONS", ""),
        ytdl_cookies_browser=os.environ.get("YTDL_COOKIES_BROWSER", ""),
        kill_stray_mpv=os.environ.get("KILL_STRAY_MPV", "1").lower()
        in ("1", "true", "yes"),
        lock_file=os.environ.get("LOCK_FILE", "/tmp/tg-mpv-bot.lock"),
        scan_interval_min=int(os.environ.get("SCAN_INTERVAL_MIN", "0") or "0"),
        state_file=Path(os.environ["STATE_FILE"]).expanduser()
        if os.environ.get("STATE_FILE")
        else Path.home() / ".local/state/tg-mpv-bot/state.json",
    )
