# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A local Windows media downloader: a PySide6 system-tray GUI frontend (`app.py`) paired with a Dockerized Flask + yt-dlp backend (`backend/server.py`). The frontend communicates with the backend over HTTP REST and Socket.IO for real-time progress events.

## Running the App

Start the backend (required first):
```powershell
cd C:\Users\jwell\OneDrive\Documents\Code\yt-dlp-dropper
docker compose up --build
```

Run the frontend tray app:
```powershell
python app.py
```

Override the backend address if needed:
```powershell
$env:YTDLP_BACKEND_URL = "http://127.0.0.1:5000"; python app.py
```

## Architecture

**Frontend (`app.py`)** — runs on Windows, no tests:
- `DownloadTask` — pure data model for a single download (id, url, status, progress, filename, error)
- `BackendClient(QObject)` — wraps `socketio.Client` in a daemon thread; exposes `submit_task`, `cancel_task`; emits `task_event` / `request_failed` Qt signals back to the UI thread
- `ProgressWindow(QMainWindow)` — owns `tasks: dict[id, DownloadTask]` and `widgets: dict[id, DownloadCardWidget]`; routes `download_event` socket payloads to `update_task`, which calls `_sync_widget_location` to move cards between the Active and Completed/Failed scroll areas
- `DropZone(QLabel)` — drag-and-drop target; emits `dropped(str)` for URLs and local file paths
- `DownloadCardWidget(QFrame)` — per-task card with progress bar, cancel/retry buttons; cancel/retry are Signals back to `ProgressWindow`

**Backend (`backend/server.py`)** — runs inside Docker:
- Flask app with Flask-SocketIO (`async_mode="threading"`)
- `active_tasks: dict[task_id, DownloadTask]` protected by `active_tasks_lock`
- `POST /download` — spawns a daemon thread running `download_worker`; returns 202 immediately
- `POST /cancel/<task_id>` — sets `task.cancelled` (a `threading.Event`); the progress hook raises `DownloadError("cancelled")` on next hook call
- `download_worker` uses `yt-dlp` with `make_progress_hook` which checks `task.is_cancelled()` and broadcasts `download_event` via Socket.IO to all clients
- Downloads land in the host's `%USERPROFILE%\OneDrive\Downloads\ytdlp` folder: `docker-compose.yml` bind-mounts `${USERPROFILE}/OneDrive/Downloads` → `/output`, and the backend writes to `DOWNLOAD_DIR=/output/ytdlp` (created on startup via `mkdir(parents=True, exist_ok=True)`)

**Key data flow:** frontend drops URL → `POST /download` → backend emits `download_event` via Socket.IO → `BackendClient.on_download_event` fires `task_event` signal → `ProgressWindow.handle_task_event` updates model and widget

## Dependencies

Frontend (install in a venv):
```powershell
pip install -r requirements.txt  # PySide6, python-socketio, requests
```

Backend (inside Docker — no manual install needed):
- Flask, Flask-SocketIO, python-socketio, yt-dlp, ffmpeg (apt)

## Rebuild After Backend Changes

```powershell
docker compose up --build
```

The Dockerfile base image is `python:3.14-slim` with ffmpeg installed via apt.
