# yt-dlp Dropper

A local Windows downloader using a Dockerized `yt-dlp` backend and a PySide6 tray/UI frontend.

## Architecture

- `backend/`: Flask + Flask-SocketIO server inside Docker
- `app.py`: PySide6 tray app with drag/drop queue, progress, cancel/retry, and socket updates

## Setup

1. Start the backend:
   ```powershell
   cd C:\Users\jwell\OneDrive\Documents\Code\yt-dlp-dropper
   docker compose up --build
   ```

2. Run the tray app:
   ```powershell
   python app.py
   ```

3. Drag URLs or file paths into the app window.

## Notes

- The backend listens on `http://127.0.0.1:5000` by default.
- Use `YTDLP_BACKEND_URL` to override the backend address.
- Downloads are saved as **`.mp4`** into `%USERPROFILE%\OneDrive\Downloads\ytdlp`
  on the Windows host (e.g. `C:\Users\jwell\OneDrive\Downloads\ytdlp`).

## Where downloads land

`docker-compose.yml` bind-mounts your `%USERPROFILE%\OneDrive\Downloads` folder
into the container as `/output`, and the backend writes finished files to the
`ytdlp` subfolder (`DOWNLOAD_DIR=/output/ytdlp`). The backend creates that
`ytdlp` folder on startup if it doesn't already exist, so completed files never
stay trapped inside the container. Change the destination by editing the
`source:` path in `docker-compose.yml` (the `${USERPROFILE}` bind mount) and/or
the `DOWNLOAD_DIR` environment variable.

## Output format

The backend requests mp4-native streams and merges to an `.mp4` container
(`bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best` with
`merge_output_format="mp4"`). This avoids the `.webm` output you get when
AV1 video is paired with Opus audio.

## Important: always start with Compose

Start the backend with `docker compose up --build` **from the project root** so
the `${USERPROFILE}/OneDrive/Downloads` bind mount is applied and finished files
land on the Windows filesystem. Do **not** start the container via the Docker
Desktop "Run" button or a bare `docker run` — without the bind mount, downloads
are written to an internal Docker volume and never appear in your Downloads
folder. (`docker compose` reads `USERPROFILE` from your shell, so run it from a
normal PowerShell session where that variable is set.)

Rebuild after any backend change so the container isn't running stale code:

```powershell
docker compose up --build
```
