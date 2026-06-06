FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    mpv \
    procps \
    && rm -rf /var/lib/apt/lists/*

# Dependency layer (cached until the lockfile changes), then the code.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project \
    # Bump yt-dlp to the nightly: YouTube breaks extraction faster than
    # stable releases. /mpv_update_ytdlp refreshes it in place at runtime.
    && uv pip install -U \
       "https://github.com/yt-dlp/yt-dlp-nightly-builds/releases/latest/download/yt-dlp.tar.gz"

COPY . .

CMD ["/app/.venv/bin/python", "bot.py"]
