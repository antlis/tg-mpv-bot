"""Settings from environment — loaded once at startup.

All host-specific paths live here so there is a single source of truth
(previously these were duplicated across mpvctl.sh, docker-compose.yml and
the systemd unit).
"""

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

# Internet-radio presets for /mpv_radio (overridable via RADIO_STATIONS).
# All free, no auth. The SomaFM block is the full channel catalog from
# https://somafm.com/channels.json (popularity-sorted at generation time).
DEFAULT_RADIO_STATIONS: list[tuple[str, str]] = [
    ("Record Techno", "https://radiorecord.hostingradio.ru/techno96.aacp"),
    ("Record Trancemission", "https://radiorecord.hostingradio.ru/tm96.aacp"),
    ("Record Deep", "https://radiorecord.hostingradio.ru/deep96.aacp"),
    # Shoutcast, plain http (no TLS mount), 256k MP3
    ("Hardcore Radio NL — hardcore/gabber", "http://stream.hardcoreradio.nl:8000/;"),
    ("Radio Paradise — eclectic rock", "https://stream.radioparadise.com/mp3-192"),
    ("FIP — eclectic, Radio France", "https://icecast.radiofrance.fr/fip-midfi.mp3"),
    ("Nightride FM — synthwave", "https://stream.nightride.fm/nightride.mp3"),
    ("KEXP Seattle — indie", "https://kexp-mp3-128.streamguys1.com/kexp128.mp3"),
    ("SomaFM Groove Salad — ambient", "https://somafm.com/groovesalad.pls"),
    ("SomaFM Drone Zone — ambient", "https://somafm.com/dronezone.pls"),
    ("SomaFM Indie Pop Rocks! — alternative", "https://somafm.com/indiepop.pls"),
    ("SomaFM Groove Salad Classic — ambient", "https://somafm.com/gsclassic.pls"),
    ("SomaFM Secret Agent — lounge", "https://somafm.com/secretagent.pls"),
    ("SomaFM Deep Space One — ambient", "https://somafm.com/deepspaceone.pls"),
    ("SomaFM Underground 80s — alternative", "https://somafm.com/u80s.pls"),
    ("SomaFM Space Station Soma — electronic", "https://somafm.com/spacestation.pls"),
    ("SomaFM Lush — electronic", "https://somafm.com/lush.pls"),
    ("SomaFM Synphaera Radio — ambient", "https://somafm.com/synphaera.pls"),
    ("SomaFM Left Coast 70s", "https://somafm.com/seventies.pls"),
    ("SomaFM DEF CON Radio — electronic", "https://somafm.com/defcon.pls"),
    ("SomaFM Folk Forward", "https://somafm.com/folkfwd.pls"),
    ("SomaFM Boot Liquor — americana", "https://somafm.com/bootliquor.pls"),
    ("SomaFM Beat Blender — electronic", "https://somafm.com/beatblender.pls"),
    ("SomaFM Bossa Beyond — bossanova", "https://somafm.com/bossa.pls"),
    ("SomaFM ThistleRadio — celtic", "https://somafm.com/thistle.pls"),
    ("SomaFM The Trip — electronic", "https://somafm.com/thetrip.pls"),
    ("SomaFM PopTron — alternative", "https://somafm.com/poptron.pls"),
    ("SomaFM Heavyweight Reggae", "https://somafm.com/reggae.pls"),
    ("SomaFM Sonic Universe — jazz", "https://somafm.com/sonicuniverse.pls"),
    ("SomaFM Illinois Street Lounge", "https://somafm.com/illstreet.pls"),
    ("SomaFM Suburbs of Goa — world", "https://somafm.com/suburbsofgoa.pls"),
    ("SomaFM Seven Inch Soul — oldies", "https://somafm.com/7soul.pls"),
    ("SomaFM The Dark Zone — ambient", "https://somafm.com/darkzone.pls"),
    ("SomaFM Fluid — electronic", "https://somafm.com/fluid.pls"),
    ("SomaFM Vaporwaves — electronic", "https://somafm.com/vaporwaves.pls"),
    ("SomaFM Groove Salad 2 — ambient", "https://somafm.com/groovesalad2.pls"),
    ("SomaFM cliqhop idm — electronic", "https://somafm.com/cliqhop.pls"),
    ("SomaFM Drone Zone 2 — ambient", "https://somafm.com/dz2.pls"),
    ("SomaFM Tiki Time", "https://somafm.com/tikitime.pls"),
    ("SomaFM Digitalis — electronic", "https://somafm.com/digitalis.pls"),
    ("SomaFM Dub Step Beyond — electronic", "https://somafm.com/dubstep.pls"),
    ("SomaFM Black Rock FM — eclectic", "https://somafm.com/brfm.pls"),
    ("SomaFM Metal Detector", "https://somafm.com/metal.pls"),
    ("SomaFM n5MD Radio — specials", "https://somafm.com/n5md.pls"),
    ("SomaFM The In-Sound — pop", "https://somafm.com/insound.pls"),
    ("SomaFM Covers — eclectic", "https://somafm.com/covers.pls"),
    ("SomaFM Mission Control — ambient", "https://somafm.com/missioncontrol.pls"),
    ("SomaFM SF 10-33 — ambient", "https://somafm.com/sf1033.pls"),
    ("SomaFM SomaFM Specials", "https://somafm.com/specials.pls"),
    ("SomaFM Doomed — ambient", "https://somafm.com/doomed.pls"),
    ("SomaFM SF Police Scanner — live", "https://somafm.com/scanner.pls"),
    ("SomaFM SomaFM Live", "https://somafm.com/live.pls"),
    ("SomaFM Chillits Radio", "https://somafm.com/chillits.pls"),
    ("SomaFM SF in SF — spoken", "https://somafm.com/sfinsf.pls"),
]


def _parse_radio_stations(raw: str | None) -> list[tuple[str, str]]:
    """``Name=URL,Name=URL`` → pairs; empty/unset → the curated defaults.

    Split on the *first* ``=`` of each entry, so URLs with query strings
    (``?listen_key=…``) survive intact.
    """
    if not raw:
        return DEFAULT_RADIO_STATIONS
    stations = []
    for entry in raw.split(","):
        name, sep, url = entry.strip().partition("=")
        if sep and name.strip() and url.strip():
            stations.append((name.strip(), url.strip()))
    return stations or DEFAULT_RADIO_STATIONS


def _default_playlist_dirs() -> list[Path]:
    videos = Path(os.environ.get("VIDEOS_DIR", str(Path.home() / "Videos"))).expanduser()
    cats = ("cartoons", "movie", "shows", "tutorials")
    return [videos / cat / "playlists" for cat in cats]


@dataclass(frozen=True)
class Settings:
    bot_token: str
    allowed_users: list[int] = field(default_factory=list)
    api_server_url: str = ""
    # Host path of the local Bot API server's /var/lib/telegram-bot-api dir.
    # Required when that server runs in TELEGRAM_LOCAL mode: getFile then
    # returns container-side filesystem paths instead of serving bytes over
    # HTTP, and this mapping lets the bot read the files directly.
    api_local_files_dir: str = ""

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
    # yt-dlp format selection for URL playback. Capped at 1080p by default so
    # streaming starts fast; raise it if your pipe and screen can take it.
    ytdl_format: str = "bv*[height<=1080]+ba/b"
    # Subtitle languages to fetch for streamed URLs (yt-dlp --sub-langs
    # syntax, e.g. "en.*,ru.*"). Empty disables subtitle fetching.
    ytdl_sub_langs: str = "en.*"
    # Proxy for non-YouTube URL playback — both the yt-dlp probe and mpv's
    # own fetch go through it, so IP-locked CDN URLs are minted and fetched
    # from the same egress. For hosts whose direct line can't reach some
    # media CDNs (broken/blocked IPv6 etc.), e.g. "http://127.0.0.1:2080".
    media_proxy: str = ""
    # /mpv_radio presets: (display name, stream URL) pairs.
    radio_stations: list[tuple[str, str]] = field(
        default_factory=lambda: DEFAULT_RADIO_STATIONS
    )
    # Also pkill stray mpv instances (ones not started by the bot) before
    # playing. Guarantees a single player on screen, but is rude on machines
    # where mpv is used manually — set KILL_STRAY_MPV=0 there; the bot's own
    # instance is always stopped gracefully over IPC first.
    kill_stray_mpv: bool = True
    lock_file: str = "/tmp/tg-mpv-bot.lock"
    scan_interval_min: int = 0   # >0 → auto-scan for new media every N minutes
    ytdlp_update_days: int = 0   # >0 → auto-update yt-dlp nightly every N days
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
        api_local_files_dir=os.environ.get("API_LOCAL_FILES_DIR", ""),
        mpv_socket=os.environ.get("MPV_SOCKET", "/tmp/mpv-socket"),
        playlist_dirs=_parse_path_list(os.environ.get("PLAYLIST_DIRS"))
        or _default_playlist_dirs(),
        mpv_runner=os.environ.get("MPV_RUNNER", "/tmp/mpv-runner.sh"),
        display=os.environ.get("DISPLAY", ":0"),
        pre_play_hook=os.environ.get("PRE_PLAY_HOOK", ""),
        post_play_hook=os.environ.get("POST_PLAY_HOOK", ""),
        ytdl_options=os.environ.get("YTDL_OPTIONS", ""),
        ytdl_cookies_browser=os.environ.get("YTDL_COOKIES_BROWSER", ""),
        ytdl_format=os.environ.get("YTDL_FORMAT", "bv*[height<=1080]+ba/b"),
        ytdl_sub_langs=os.environ.get("YTDL_SUB_LANGS", "en.*"),
        media_proxy=os.environ.get("MEDIA_PROXY", ""),
        radio_stations=_parse_radio_stations(os.environ.get("RADIO_STATIONS")),
        kill_stray_mpv=os.environ.get("KILL_STRAY_MPV", "1").lower()
        in ("1", "true", "yes"),
        lock_file=os.environ.get("LOCK_FILE", "/tmp/tg-mpv-bot.lock"),
        scan_interval_min=int(os.environ.get("SCAN_INTERVAL_MIN", "0") or "0"),
        ytdlp_update_days=int(os.environ.get("YTDL_UPDATE_DAYS", "0") or "0"),
        state_file=Path(os.environ["STATE_FILE"]).expanduser()
        if os.environ.get("STATE_FILE")
        else Path.home() / ".local/state/tg-mpv-bot/state.json",
    )
