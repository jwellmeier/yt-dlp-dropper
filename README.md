# yt-dlp Dropper

A local Windows downloader using a Dockerized `yt-dlp` backend and a PySide6 tray/UI frontend.

## Architecture

- `backend/`: Flask + Flask-SocketIO server inside Docker
- `app.py`: PySide6 tray app with drag/drop queue, progress, cancel/retry, and socket updates

## Setup

1. Start the backend:
   ```powershell
   cd C:\Users\jwell\Documents\yt-dlp-dropper
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
- Downloads are saved into `backend/downloads` through Docker volume mapping.
