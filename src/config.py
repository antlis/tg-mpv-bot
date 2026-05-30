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
    videos = Path(os.environ.get("VIDEOS_DIR", str(Path.home() / "Videos")))
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
    i3_socket: str = ""          # empty → don't switch workspaces
    i3_workspace: str = "10"

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
        i3_socket=os.environ.get("I3SOCK", os.environ.get("I3_SOCKET", "")),
        i3_workspace=os.environ.get("I3_WORKSPACE", "10"),
    )
