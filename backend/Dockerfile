FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg curl unzip ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# yt-dlp needs a JavaScript runtime to solve YouTube's "nsig" JS challenges.
# Without one, modern YouTube stream URLs are throttled to ~0 or rejected, so
# downloads hang at "Starting download". deno is yt-dlp's default JS runtime.
ENV DENO_VERSION=v2.9.6
RUN curl -fsSL "https://github.com/denoland/deno/releases/download/${DENO_VERSION}/deno-x86_64-unknown-linux-gnu.zip" -o /tmp/deno.zip && \
    unzip /tmp/deno.zip -d /usr/local/bin && \
    rm /tmp/deno.zip && \
    deno --version

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py ./
RUN mkdir -p /downloads
EXPOSE 5000
CMD ["python", "server.py"]
