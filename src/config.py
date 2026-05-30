"""Settings from environment — loaded once at startup."""

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path


@dataclass
class Settings:
    bot_token: str
    allowed_users: list[int] = field(default_factory=list)
    mpvctl_path: str = ""
    api_server_url: str = ""

    # Access control
    @property
    def is_restricted(self) -> bool:
        return len(self.allowed_users) > 0


def _parse_int_list(raw: str | None) -> list[int]:
    if not raw:
        return []
    return [int(x.strip()) for x in raw.split(",") if x.strip()]


@lru_cache()
def get_settings() -> Settings:
    """Load settings from environment (called once at import)."""
    # Guess mpvctl.sh path relative to project root
    project_root = Path(__file__).resolve().parent.parent
    default_mpvctl = str(project_root / "mpvctl.sh")
    # If project-local doesn't exist, fall back to global
    if not Path(default_mpvctl).exists():
        default_mpvctl = os.path.expanduser("~/.hermes/scripts/mpvctl.sh")

    return Settings(
        bot_token=os.environ["BOT_TOKEN"],
        allowed_users=_parse_int_list(os.environ.get("ALLOWED_USERS", "")),
        mpvctl_path=os.environ.get("MPVCTL_PATH", default_mpvctl),
        api_server_url=os.environ.get("API_SERVER_URL", ""),
    )
