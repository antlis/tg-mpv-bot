FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    mpv \
    procps \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    # yt-dlp powers URL streaming / /mpv_yt / Telegram files; the nightly is
    # required because YouTube breaks extraction faster than stable releases.
    # /mpv_update_ytdlp refreshes it in place at runtime.
    && pip install --no-cache-dir -U \
       "https://github.com/yt-dlp/yt-dlp-nightly-builds/releases/latest/download/yt-dlp.tar.gz"

COPY . .

CMD ["python", "bot.py"]
