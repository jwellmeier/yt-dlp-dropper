import json
import os
import threading
import uuid
from pathlib import Path

from flask import Flask, Response, jsonify, request
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
        # Newer Flask-SocketIO broadcasts to all clients in the namespace by
        # default and no longer accepts the broadcast= kwarg.
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
            # This fires when a single stream finishes downloading, BEFORE any
            # merge/remux. The reported filename is a temporary per-format file
            # (e.g. ".f399.mp4") that yt-dlp deletes during merging, so we only
            # record it as a fallback and mark completion later (postprocessor
            # hook / after download returns), once the final path is known.
            task.filename = info.get("filename")
            task.set_status(
                "processing",
                message="Processing (merging/converting)",
                progress=100.0,
            )

    return hook


def make_postprocessor_hook(task: DownloadTask):
    def hook(info: dict):
        # info_dict carries the current on-disk path; after the final
        # postprocessor (merge/remux/move) it points at the finished .mp4.
        info_dict = info.get("info_dict") or {}
        filepath = info_dict.get("filepath")
        if filepath:
            task.filename = filepath

    return hook


def download_worker(task: DownloadTask):
    task.set_status("downloading", message="Starting download")

    ytdl_options = {
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "outtmpl": str(DOWNLOAD_DIR / "%(title)s.%(ext)s"),
        "progress_hooks": [make_progress_hook(task)],
        "postprocessor_hooks": [make_postprocessor_hook(task)],
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": False,
        "retries": 3,
        "continuedl": True,
        "noprogress": False,
        "concurrent_fragment_downloads": 5,
        # Allow yt-dlp to fetch its remote EJS challenge-solver components when a
        # site (currently YouTube) needs them in addition to the local deno
        # runtime. Without this some videos still fail the JS challenge.
        "remote_components": ["ejs:github"],
    }

    try:
        with YoutubeDL(ytdl_options) as ydl:
            ydl.download([task.url])
        # download() returned without raising: all downloading and
        # postprocessing (merge/remux to mp4) is done. task.filename now holds
        # the final on-disk path (set by the postprocessor hook, or the
        # progress hook for single-file downloads with no postprocessing).
        final_name = os.path.basename(task.filename) if task.filename else None
        task.set_status(
            "completed",
            message=f"Saved {final_name}" if final_name else "Download completed",
            progress=100.0,
            filename=task.filename,
        )
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


# ─── Download API ─────────────────────────────────────────────────────────────

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


# ─── PWA: assets ──────────────────────────────────────────────────────────────

_ICON_SVG = """\
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 192 192">
  <rect width="192" height="192" rx="38" fill="#6c63ff"/>
  <rect x="84" y="36" width="24" height="78" rx="6" fill="white"/>
  <polygon points="96,150 50,104 142,104" fill="white"/>
  <rect x="44" y="160" width="104" height="14" rx="7" fill="white" opacity="0.85"/>
</svg>"""

_MANIFEST = json.dumps({
    "name": "yt-dlp Dropper",
    "short_name": "yt-dlp",
    "description": "Queue video downloads to your PC from your phone",
    "display": "standalone",
    "orientation": "portrait",
    "background_color": "#0f0f1a",
    "theme_color": "#6c63ff",
    "start_url": "/",
    "scope": "/",
    "icons": [
        {
            "src": "/icon.svg",
            "sizes": "any",
            "type": "image/svg+xml",
            "purpose": "any maskable",
        }
    ],
    "share_target": {
        "action": "/share",
        "method": "GET",
        "params": {
            "title": "title",
            "text": "text",
            "url": "url",
        },
    },
})

# Minimal service worker — only purpose is to satisfy the PWA installability
# requirement on Chrome/Android. iOS Safari doesn't require a SW for "Add to
# Home Screen" but registers it anyway; keeping it trivial avoids caching bugs.
_SW_JS = """\
const CACHE = "ytdlp-v1";
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", () => self.clients.claim());
// No caching strategy: always go to the network so share pages are fresh.
self.addEventListener("fetch", () => {});
"""

_INDEX_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>yt-dlp Dropper</title>
<link rel="manifest" href="/manifest.json">
<link rel="apple-touch-icon" href="/icon.svg">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="yt-dlp">
<meta name="theme-color" content="#6c63ff">
<style>
:root {
  --bg:      #0f0f1a;
  --card:    #16162a;
  --border:  #24244a;
  --accent:  #6c63ff;
  --aglow:   rgba(108,99,255,.3);
  --text:    #e4e4f0;
  --muted:   #7070a0;
  --ok:      #38e89e;
  --err:     #ff5c72;
}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html{height:100%}
body{
  min-height:100%;
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,sans-serif;
  background:var(--bg);color:var(--text);line-height:1.5;
  padding:max(env(safe-area-inset-top),28px) 18px max(env(safe-area-inset-bottom),24px);
}
h2{font-size:.8rem;font-weight:700;text-transform:uppercase;letter-spacing:.1em;
   color:var(--muted);margin-bottom:14px}
a{color:var(--accent)}
code{font-family:ui-monospace,monospace;font-size:.9em;
     background:rgba(108,99,255,.15);padding:1px 5px;border-radius:4px}

/* ── header ── */
.header{display:flex;align-items:center;gap:14px;
  padding-bottom:22px;margin-bottom:20px;border-bottom:1px solid var(--border)}
.logo{
  width:54px;height:54px;background:var(--accent);border-radius:16px;flex-shrink:0;
  display:flex;align-items:center;justify-content:center;font-size:30px;
  box-shadow:0 4px 22px var(--aglow)}
.app-name{font-size:1.35rem;font-weight:700}
.status{font-size:.82rem;color:var(--muted);display:flex;align-items:center;gap:6px;margin-top:3px}
.dot{width:8px;height:8px;border-radius:50%;background:var(--muted);flex-shrink:0}
.dot.ok{background:var(--ok)} .dot.err{background:var(--err)}

/* ── card ── */
.card{background:var(--card);border:1px solid var(--border);border-radius:16px;
  padding:20px;margin-bottom:16px}

/* ── install steps ── */
.steps ol{padding-left:1.3em}
.steps li{margin-bottom:10px;font-size:.93rem}
.steps li strong{color:var(--text)}
.note{
  margin-top:14px;padding:12px 14px;border-radius:10px;font-size:.83rem;
  color:var(--muted);line-height:1.6;
  background:rgba(108,99,255,.1);border:1px solid rgba(108,99,255,.25)}
.note strong{color:var(--text)}

/* ── form ── */
.row{display:flex;gap:10px;margin-top:0}
input[type=url]{
  flex:1;background:var(--bg);border:1px solid var(--border);border-radius:10px;
  color:var(--text);font-size:.95rem;padding:11px 14px;outline:none;
  transition:border-color .15s;-webkit-appearance:none}
input[type=url]:focus{border-color:var(--accent)}
input[type=url]::placeholder{color:var(--muted)}
button{
  background:var(--accent);border:none;border-radius:10px;color:#fff;
  cursor:pointer;font-size:.95rem;font-weight:600;padding:11px 20px;
  transition:opacity .15s;white-space:nowrap;-webkit-tap-highlight-color:transparent}
button:active{opacity:.7}
#result{margin-top:10px;font-size:.88rem;min-height:18px}
.ok-msg{color:var(--ok)} .err-msg{color:var(--err)}
</style>
</head>
<body>

<div class="header">
  <div class="logo">⬇</div>
  <div>
    <div class="app-name">yt-dlp Dropper</div>
    <div class="status"><span class="dot" id="dot"></span><span id="status-txt">Checking…</span></div>
  </div>
</div>

<section class="card steps">
  <h2>Install on iPhone</h2>
  <ol>
    <li>Open this page in <strong>Safari</strong> (required for Add to Home Screen)</li>
    <li>Tap the <strong>Share ↑</strong> button in Safari's toolbar</li>
    <li>Scroll down → tap <strong>Add to Home Screen</strong> → <strong>Add</strong></li>
    <li>From any browser, share a video URL and pick <strong>yt-dlp Dropper</strong></li>
  </ol>
  <p class="note">
    <strong>⚠ iOS requires HTTPS</strong> for the share-sheet target to register.<br>
    Easiest fix: install <a href="https://tailscale.com" target="_blank">Tailscale</a> on both your PC and iPhone,
    then open this page at <code>https://&lt;pc-name&gt;.ts.net:5000</code> instead.<br><br>
    Prefer plain HTTP over your local network? Use the
    <strong>iOS Shortcut</strong> method in
    <a href="https://github.com/jwellmeier/yt-dlp-dropper/blob/master/IPHONE_SETUP.md" target="_blank">IPHONE_SETUP.md</a>
    — it works without HTTPS and appears in the share sheet too.
  </p>
</section>

<section class="card">
  <h2>Quick Submit</h2>
  <form id="form" autocomplete="off">
    <div class="row">
      <input type="url" id="url-in" placeholder="https://youtube.com/watch?v=…" required>
      <button type="submit">Queue</button>
    </div>
    <div id="result"></div>
  </form>
</section>

<script>
if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js');

const dot = document.getElementById('dot');
const statusTxt = document.getElementById('status-txt');
fetch('/health').then(r => r.json()).then(() => {
  dot.className = 'dot ok'; statusTxt.textContent = 'Backend online';
}).catch(() => {
  dot.className = 'dot err'; statusTxt.textContent = 'Backend offline';
});

document.getElementById('form').addEventListener('submit', async e => {
  e.preventDefault();
  const url = document.getElementById('url-in').value.trim();
  const result = document.getElementById('result');
  result.textContent = 'Queueing…'; result.className = '';
  try {
    const r = await fetch('/download', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({url})
    });
    if (r.ok) {
      result.textContent = '✓ Queued!'; result.className = 'ok-msg';
      document.getElementById('url-in').value = '';
    } else {
      const d = await r.json().catch(() => ({}));
      result.textContent = '✗ ' + (d.error || 'Server error'); result.className = 'err-msg';
    }
  } catch {
    result.textContent = '✗ Cannot reach backend'; result.className = 'err-msg';
  }
});
</script>
</body>
</html>
"""

_SHARE_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>yt-dlp Dropper</title>
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="theme-color" content="#6c63ff">
<style>
:root{
  --bg:#0f0f1a;--card:#16162a;--border:#24244a;
  --accent:#6c63ff;--aglow:rgba(108,99,255,.4);
  --text:#e4e4f0;--muted:#7070a0;--ok:#38e89e;--err:#ff5c72;
}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%}
body{
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
  background:var(--bg);color:var(--text);
  display:flex;align-items:center;justify-content:center;
  padding:24px;text-align:center;
}
.card{
  background:var(--card);border:1px solid var(--border);border-radius:22px;
  padding:40px 28px;width:100%;max-width:360px
}
.icon{
  width:72px;height:72px;background:var(--accent);border-radius:20px;
  display:flex;align-items:center;justify-content:center;font-size:36px;
  margin:0 auto 20px;box-shadow:0 6px 28px var(--aglow)
}
h1{font-size:1.2rem;margin-bottom:10px}
.preview{
  font-size:.72rem;color:var(--muted);word-break:break-all;
  padding:8px 12px;background:rgba(255,255,255,.04);
  border-radius:8px;margin-bottom:26px;
  max-height:58px;overflow:hidden;display:none
}
.spinner{
  width:38px;height:38px;
  border:3px solid rgba(108,99,255,.25);border-top-color:var(--accent);
  border-radius:50%;margin:0 auto;animation:spin .75s linear infinite
}
@keyframes spin{to{transform:rotate(360deg)}}
.big-icon{font-size:44px;margin:0 auto 8px}
.ok-label{color:var(--ok);font-weight:600;margin:8px 0 4px}
.err-label{color:var(--err);font-weight:600;margin:8px 0 4px}
.sub{color:var(--muted);font-size:.85rem;margin-bottom:20px}
.btn{
  display:inline-block;margin-top:18px;padding:11px 28px;
  background:var(--accent);color:#fff;text-decoration:none;
  border-radius:12px;font-weight:600;font-size:.95rem;
  -webkit-tap-highlight-color:transparent
}
</style>
</head>
<body>
<div class="card">
  <div class="icon">⬇</div>
  <h1 id="heading">Sending to yt-dlp…</h1>
  <div class="preview" id="preview"></div>
  <div id="body"><div class="spinner"></div></div>
</div>

<script>
// Web Share Target delivers the shared content as GET query params.
// Different apps populate different fields: YouTube shares via ?url=,
// others may put the URL inside ?text=, so we check all three.
function extractUrl() {
  const p = new URLSearchParams(location.search);
  const re = /https?:\\/\\/[^\\s"<>]+/;
  for (const v of [p.get('url'), p.get('text'), p.get('title')]) {
    if (v) { const m = v.match(re); if (m) return m[0].replace(/[.,;)]$/, ''); }
  }
  return null;
}

async function run() {
  const heading = document.getElementById('heading');
  const preview = document.getElementById('preview');
  const body    = document.getElementById('body');

  const videoUrl = extractUrl();

  if (!videoUrl) {
    heading.textContent = 'No URL found';
    body.innerHTML = '<p class="sub">Nothing was shared, or the content had no recognisable URL.</p>'
                   + '<a class="btn" href="/">\\u2190 Home</a>';
    return;
  }

  preview.textContent = videoUrl;
  preview.style.display = 'block';

  try {
    const r = await fetch('/download', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({url: videoUrl})
    });

    if (r.ok) {
      heading.textContent = 'Queued!';
      body.innerHTML = '<div class="big-icon">\\u2713</div>'
                     + '<p class="ok-label">Download queued on your PC</p>'
                     + '<p class="sub">You can close this tab.</p>'
                     + '<a class="btn" href="/">Done</a>';
      // window.close() only works in certain contexts; try anyway, then fall
      // back to navigating home so the standalone PWA window doesn't hang.
      setTimeout(() => {
        try { window.close(); } catch {}
        setTimeout(() => { location.href = '/'; }, 600);
      }, 2200);
    } else {
      const d = await r.json().catch(() => ({}));
      heading.textContent = 'Error';
      body.innerHTML = '<p class="err-label">\\u2717 ' + (d.error || 'Server error') + '</p>'
                     + '<a class="btn" href="/">\\u2190 Home</a>';
    }
  } catch {
    heading.textContent = 'Cannot reach backend';
    body.innerHTML = '<p class="sub">Make sure the yt-dlp Dropper backend container is running.</p>'
                   + '<a class="btn" href="/">\\u2190 Home</a>';
  }
}

run();
</script>
</body>
</html>
"""


# ─── PWA routes ───────────────────────────────────────────────────────────────

@app.route("/")
def pwa_index():
    return Response(_INDEX_HTML, mimetype="text/html")


@app.route("/manifest.json")
def pwa_manifest():
    return Response(_MANIFEST, mimetype="application/json")


@app.route("/sw.js")
def pwa_sw():
    # Service-Worker-Allowed header lets the SW claim the full origin scope
    # even though it's served from the root path.
    return Response(
        _SW_JS,
        mimetype="application/javascript",
        headers={"Service-Worker-Allowed": "/"},
    )


@app.route("/icon.svg")
def pwa_icon():
    return Response(_ICON_SVG, mimetype="image/svg+xml")


@app.route("/share")
def pwa_share():
    # Web Share Target (iOS/Android) opens this GET endpoint with the shared
    # content as query parameters (?url=, ?text=, ?title=). The page's JS
    # picks out the URL and POSTs it to /download.
    return Response(_SHARE_HTML, mimetype="text/html")


if __name__ == "__main__":
    # This is a local, single-user dev tool; the Werkzeug dev server is fine.
    # Newer Flask-SocketIO refuses to start it without this explicit opt-in.
    socketio.run(app, host="0.0.0.0", port=5000, allow_unsafe_werkzeug=True)
