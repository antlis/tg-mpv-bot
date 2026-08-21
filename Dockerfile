FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    mpv \
    procps \
    # i3-wm only for its `i3-msg` binary — lets PRE_PLAY_HOOK do WM glue
    # (e.g. `i3-msg workspace 10`) for hosts running i3. i3-msg with no
    # explicit socket discovers it via the X11 root window property, so
    # it works over the forwarded DISPLAY without mounting the host's
    # i3 IPC socket (which goes stale every reboot anyway — see
    # CLAUDE.md). Hosts on another WM just leave PRE_PLAY_HOOK unset.
    i3-wm \
    && rm -rf /var/lib/apt/lists/*

ENV UV_PYTHON_PREFERENCE=only-system

COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --no-install-project \
    && uv pip install -U \
       "https://github.com/yt-dlp/yt-dlp-nightly-builds/releases/latest/download/yt-dlp.tar.gz"

COPY . .

CMD ["uv", "run", "bot.py"]
