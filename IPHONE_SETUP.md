# iPhone Share-Sheet Setup

Two ways to send a video URL from your iPhone browser directly to the yt-dlp Dropper backend. Choose one or use both.

| | **iOS Shortcut** | **PWA Share Target** |
|---|---|---|
| Works over plain HTTP | ✅ | ❌ (needs HTTPS) |
| Appears in share sheet | ✅ | ✅ (after install) |
| Setup time | ~5 min | ~10 min |
| Requires app install | No | Add to Home Screen |
| Works in Safari, Chrome, DDG | ✅ all | ✅ all |

---

## Option A — iOS Shortcut (recommended for plain HTTP / LAN)

This is the fastest path. The shortcut appears in every browser's share sheet and POSTs the URL straight to the backend over your local Wi-Fi.

### 1. Find your PC's local IP

On the Windows machine running the backend:

```powershell
(Get-NetIPAddress -AddressFamily IPv4 -InterfaceAlias Wi-Fi).IPAddress
# e.g. 192.168.1.42
```

### 2. Create the shortcut on your iPhone

1. Open the **Shortcuts** app.
2. Tap **+** (top right) to create a new shortcut.
3. Tap **Add Action** → search **"Get Contents of URL"** → select it.
4. Configure the action:
   - **URL**: `http://192.168.1.42:5000/download` *(replace with your PC's IP)*
   - **Method**: POST
   - **Request Body**: JSON
   - Add a JSON key **`url`** with value **Shortcut Input** (tap the blue token button to pick it from the variables list)
5. Tap the shortcut name at the top → rename it to **"Queue on yt-dlp"** (or whatever you like).
6. Tap **Done**.

### 3. Add the shortcut to the share sheet

1. In the **Shortcuts** app, long-press the shortcut card → **Details**.
2. Enable **"Show in Share Sheet"** (toggle it on).
3. Back in the shortcut settings, under **Receive**, enable **URLs**.

### 4. Use it

Open any video in Safari, Chrome, or DuckDuckGo → tap the **Share ↑** button → scroll to your shortcut → tap it. A notification briefly confirms the POST succeeded (or shows an error if the backend is unreachable).

> **Tip:** If the backend isn't reachable from the shortcut, check that Docker is running and that your iPhone and PC are on the **same Wi-Fi network**. If you've set a Windows Firewall rule, make sure port 5000 is allowed for Private networks.

---

## Option B — PWA Web Share Target (requires HTTPS)

The backend itself serves a small installable web app. Once installed to the iPhone home screen, it registers as a share target — identical UX to a native app.

### Why HTTPS?

iOS Safari only registers a PWA as a share target when the page was loaded over **HTTPS**. Plain `http://192.168.x.x:5000` won't work for installation (the share-sheet entry never appears).

### Recommended: Tailscale (free, 5-min setup)

[Tailscale](https://tailscale.com) creates an encrypted mesh network between your devices and gives each machine a stable `*.ts.net` hostname with automatic HTTPS certificates — no port forwarding, no self-signed cert hassle.

1. **Install Tailscale** on your Windows PC and your iPhone (both free).
2. Sign in to the same Tailscale account on both devices.
3. On the Windows PC, enable HTTPS certificates for the machine:
   ```powershell
   tailscale cert <your-machine-name>.ts.net
   # Tailscale prints the cert + key paths
   ```
4. Pass the cert paths to the backend. Add these environment variables to `docker-compose.yml`:
   ```yaml
   environment:
     - DOWNLOAD_DIR=/output/ytdlp
     - SSL_CERT=/certs/cert.pem
     - SSL_KEY=/certs/key.pem
   volumes:
     - type: bind
       source: ${USERPROFILE}/OneDrive/Downloads
       target: /output
     - type: bind
       source: C:/path/to/tailscale/certs   # folder holding cert.pem + key.pem
       target: /certs
   ```
   Then update `server.py`'s `socketio.run(...)` call to pass `ssl_context`:
   ```python
   import ssl, os
   cert = os.environ.get("SSL_CERT")
   key  = os.environ.get("SSL_KEY")
   ctx  = None
   if cert and key:
       ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
       ctx.load_cert_chain(cert, key)
   socketio.run(app, host="0.0.0.0", port=5000,
                allow_unsafe_werkzeug=True, ssl_context=ctx)
   ```
5. Rebuild: `docker compose up --build`.

### Install the PWA

1. On your iPhone, open **Safari** and navigate to `https://<your-machine>.ts.net:5000`.
2. Tap the **Share ↑** button → **Add to Home Screen** → **Add**.
3. A new icon ("yt-dlp") appears on your home screen.

### Use it

Open any video in any browser → **Share ↑** → **yt-dlp Dropper** (scroll the share sheet if needed). A small page briefly shows "Queued!" and closes itself.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Shortcut returns error | Backend not running or wrong IP | Run `docker compose up`, verify IP with `ipconfig` |
| Share sheet entry missing | PWA not installed / no HTTPS | Use Tailscale; re-add to Home Screen |
| "No URL found" on share page | App shared text, not a URL | Some apps (Reddit, Twitter) put the URL in the `text` field — the share page handles this automatically |
| Download starts but stalls | yt-dlp JS challenge issue | Already handled by deno in the Dockerfile; run `docker compose up --build` to pull a fresh yt-dlp |
| Port 5000 unreachable from iPhone | Windows Firewall blocking | Allow port 5000 in Windows Defender Firewall → Inbound Rules |
