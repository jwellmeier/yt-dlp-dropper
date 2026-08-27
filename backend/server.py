import os
import threading
import uuid
from pathlib import Path

from flask import Flask, jsonify, request
from flask_socketio import SocketIO
from yt_dlp import DownloadError, YoutubeDL

DOWNLOAD_DIR = Path(os.environ.get("DOWNLOAD_DIR", "/downloads"))
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

active_tasks = {}
active_tasks_lock = threading.Lock()


class DownloadTask:
    def __init__(self, task_id: str, url: str):
        self.task_id = task_id
        self.url = url
        self.status = "queued"
        self.progress = 0.0
        self.message = "Queued"
        self.filename = None
        self.error = None
        self.cancelled = threading.Event()

    def emit_update(self):
        payload = {
            "task_id": self.task_id,
            "url": self.url,
            "status": self.status,
            "progress": self.progress,
            "message": self.message,
            "filename": self.filename,
            "error": self.error,
        }
        socketio.emit("download_event", payload)

    def set_status(self, status: str, message: str = None, progress: float = None, filename: str = None, error: str = None):
        self.status = status
        if message is not None:
            self.message = message
        if progress is not None:
            self.progress = progress
        if filename is not None:
            self.filename = filename
        if error is not None:
            self.error = error
        self.emit_update()

    def cancel(self):
        self.cancelled.set()

    def is_cancelled(self) -> bool:
        return self.cancelled.is_set()


def make_progress_hook(task: DownloadTask):
    def hook(info: dict):
        status = info.get("status")
        if status == "downloading":
            downloaded = info.get("downloaded_bytes", 0) or 0
            total = info.get("total_bytes") or info.get("total_bytes_estimate")
            percent = 0.0
            if total:
                percent = round(downloaded / total * 100.0, 1)
            task.set_status(
                "downloading",
                message=f"Downloading {percent}%",
                progress=percent,
            )
            if task.is_cancelled():
                raise DownloadError("cancelled")
        elif status == "finished":
            filename = info.get("filename")
            task.set_status(
                "completed",
                message="Download completed",
                progress=100.0,
                filename=filename,
            )

    return hook


def download_worker(task: DownloadTask):
    task.set_status("downloading", message="Starting download")

    ytdl_options = {
        "format": "bestvideo+bestaudio/best",
        "noplaylist": True,
        "outtmpl": str(DOWNLOAD_DIR / "%(title)s.%(ext)s"),
        "progress_hooks": [make_progress_hook(task)],
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": False,
        "retries": 3,
        "continuedl": True,
        "noprogress": False,
        "concurrent_fragment_downloads": 5,
    }

    try:
        with YoutubeDL(ytdl_options) as ydl:
            ydl.download([task.url])
    except DownloadError as exc:
        if task.is_cancelled():
            task.set_status("cancelled", message="Download cancelled", error=str(exc))
        else:
            task.set_status("error", message="Download failed", error=str(exc))
    except Exception as exc:
        task.set_status("error", message="Download failed", error=str(exc))
    finally:
        with active_tasks_lock:
            active_tasks.pop(task.task_id, None)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/download", methods=["POST"])
def download():
    data = request.get_json(force=True, silent=True) or {}
    url = data.get("url")
    if not url:
        return jsonify({"error": "Missing URL"}), 400

    task_id = data.get("task_id") or str(uuid.uuid4())
    task = DownloadTask(task_id, url)
    with active_tasks_lock:
        active_tasks[task_id] = task

    thread = threading.Thread(target=download_worker, args=(task,), daemon=True)
    thread.start()
    task.emit_update()
    return jsonify({"task_id": task_id, "status": "queued"}), 202


@app.route("/cancel/<task_id>", methods=["POST"])
def cancel(task_id: str):
    with active_tasks_lock:
        task = active_tasks.get(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404
    task.cancel()
    task.set_status("cancelled", message="Cancelling...", error="User requested cancel")
    return jsonify({"task_id": task_id, "cancelled": True})


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000, allow_unsafe_werkzeug=True)
