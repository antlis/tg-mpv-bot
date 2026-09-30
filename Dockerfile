FROM python:3.14-slim

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
    # xdotool backs the default POST_PLAY_HOOK (see docker-compose.yml):
    # a container-launched window doesn't reliably end up focused/raised
    # on every WM the way a host-native process's window would, so the
    # hook explicitly grabs focus after mpv maps its window. Works via
    # plain EWMH, not i3-specific.
    xdotool \
    && rm -rf /var/lib/apt/lists/*

# ffmpeg binary — opt-in (`--build-arg INSTALL_FFMPEG=true`, or INSTALL_FFMPEG
# in .env via docker compose). Needed by the panel's ⏺ record/clip button and
# for the 📥 upload button to merge separate video+audio streams; without it
# 📥 still works for library files and single-file stream formats.
ARG INSTALL_FFMPEG=false
RUN if [ "$INSTALL_FFMPEG" = "true" ]; then \
        apt-get update && apt-get install -y --no-install-recommends ffmpeg \
        && rm -rf /var/lib/apt/lists/*; \
    fi

ENV UV_PYTHON_PREFERENCE=only-system

COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --no-install-project \
    && uv pip install -U \
       "https://github.com/yt-dlp/yt-dlp-nightly-builds/releases/latest/download/yt-dlp.tar.gz"

# Chromium + its system libs for the headless-browser fallback (Playwright).
# Opt-in — it adds ~450 MB — via `--build-arg INSTALL_BROWSER=true` (or the
# INSTALL_BROWSER var in .env when building through docker compose). When off,
# the fallback no-ops and the bot reports the original failure. The browser
# path is fixed so it survives the runtime HOME override in docker-compose.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
ARG INSTALL_BROWSER=false
RUN if [ "$INSTALL_BROWSER" = "true" ]; then \
        /app/.venv/bin/playwright install --with-deps chromium \
        && rm -rf /var/lib/apt/lists/*; \
    fi

COPY . .

CMD ["uv", "run", "bot.py"]
