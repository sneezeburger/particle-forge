# TLS setup (HTTPS + WSS)

Particle Forge needs to run over **HTTPS**, not plain HTTP, because iOS Safari
only exposes `DeviceOrientationEvent`/`DeviceMotionEvent` (used for the
tilt/IMU controls) in a [secure context](https://developer.mozilla.org/en-US/docs/Web/Security/Secure_Contexts).
The remote-control WebSocket (`remote.html` ↔ `index.html`) then has to be
`wss://` too, since a page loaded over HTTPS cannot open a plain `ws://`
connection (mixed content).

There's no public domain here — this runs on a local Wi-Fi AP
(`10.42.0.1`) — so a self-signed certificate is used instead of a CA-issued
one. That requires two extra one-time steps described below: trusting the
cert in your browser, and (on iOS only) trusting it at the OS level.

## 1. Generate the self-signed certificate

Generate a cert/key pair with the AP's IP address as a Subject Alternative
Name (required — browsers ignore the CN field). Run this from the repo
root, since that's where `server.py` looks for `cert.pem`/`key.pem` by
default (both are gitignored):

```sh
openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout key.pem -out cert.pem -days 825 \
  -subj "/CN=10.42.0.1" \
  -addext "subjectAltName=IP:10.42.0.1"
```

Adjust `10.42.0.1` / `-days` as needed. Re-run whenever the AP's IP changes
or the cert expires.

## 2. Serve the app over HTTPS + WSS on one port

`server.py` (in this repo) serves the static files over HTTPS *and* relays
WebSocket messages for remote control, both on the **same port** — using
one port for both matters: see step 4.

Install its one dependency and run it from the repo root:

```sh
pip install --break-system-packages websockets   # or use a venv
python3 server.py
```

It reads `cert.pem`/`key.pem` from the repo root by default. Configure via
environment variables if needed:

| Variable               | Default                      |
|------------------------|-------------------------------|
| `PARTICLE_FORGE_HOST`  | `0.0.0.0`                    |
| `PARTICLE_FORGE_PORT`  | `8443`                       |
| `PARTICLE_FORGE_CERT`  | `<repo>/cert.pem`             |
| `PARTICLE_FORGE_KEY`   | `<repo>/key.pem`              |

e.g. to bind only to the AP's address:

```sh
PARTICLE_FORGE_HOST=10.42.0.1 python3 server.py
```

Its core logic:
- Loads `cert.pem`/`key.pem` into an `ssl.SSLContext` passed to
  `websockets.serve(..., ssl=ctx)`.
- A `process_request` hook inspects each incoming request: if it's **not**
  a WebSocket upgrade, it serves the request as a static file from the repo
  directory; if it **is** an upgrade, it falls through to the relay handler
  that broadcasts every message to all other connected clients.

Browse to `https://10.42.0.1:8443/index.html` (adjust host/port to match).

## 3. Trust the certificate in your browser (every device)

On first visit, the browser will warn that the certificate is self-signed
("Your connection is not private" / similar). Proceed past the warning
("Visit this website" / "Advanced → Proceed"). This is enough for desktop
browsers and for plain page loads on iOS.

## 4. iOS only: trust the certificate at the OS level

**This step is required for the remote-control WebSocket to work on iOS.**
WebKit/Safari does **not** extend a page's "proceed anyway" certificate
exception to WebSocket (`wss://`) connections, and there is no interactive
prompt to accept one for a raw socket — the handshake just fails silently,
forever. The only fix is to make the certificate trusted at the OS level,
via an installable configuration profile.

Generate the profile once, from the repo root (embeds `cert.der`, base64,
as a trusted root; output goes straight into the web root so `server.py`
can serve it, and both `*.der`/`*.mobileconfig` are gitignored):

```sh
openssl x509 -in cert.pem -outform der -out cert.der
python3 - <<'PY'
import base64, uuid
der = open("cert.der", "rb").read()
b64 = "\n".join(base64.b64encode(der).decode()[i:i+52] for i in range(0, len(base64.b64encode(der)), 52))
xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>PayloadContent</key><array><dict>
    <key>PayloadCertificateFileName</key><string>cert.der</string>
    <key>PayloadContent</key><data>\n{b64}\n</data>
    <key>PayloadDescription</key><string>Trusts the self-signed TLS certificate used by the Particle Forge local server (10.42.0.1), enabling HTTPS and WebSocket (WSS) connections without warnings.</string>
    <key>PayloadDisplayName</key><string>Particle Forge Local CA</string>
    <key>PayloadIdentifier</key><string>com.particleforge.cert.root</string>
    <key>PayloadType</key><string>com.apple.security.root</string>
    <key>PayloadUUID</key><string>{uuid.uuid4()}</string>
    <key>PayloadVersion</key><integer>1</integer>
  </dict></array>
  <key>PayloadDisplayName</key><string>Particle Forge Local Certificate</string>
  <key>PayloadIdentifier</key><string>com.particleforge.profile</string>
  <key>PayloadRemovalDisallowed</key><false/>
  <key>PayloadType</key><string>Configuration</string>
  <key>PayloadUUID</key><string>{uuid.uuid4()}</string>
  <key>PayloadVersion</key><integer>1</integer>
</dict></plist>"""
open("particle-forge-ca.mobileconfig", "w").write(xml)
PY
```

The server must send it with the MIME type `application/x-apple-aspen-config`
(special-cased in `server.py`'s static file handler) — otherwise iOS
downloads it as a plain file instead of offering to install it.

On the iPhone:

1. Visit `https://10.42.0.1:8443/particle-forge-ca.mobileconfig` in Safari.
2. Tap through "Profile Downloaded", then go to
   **Settings → General → VPN & Device Management** and install the
   "Particle Forge Local CA" profile.
3. Go to **Settings → General → About → Certificate Trust Settings** and
   enable full trust for it.
4. Reload the sim page. The `remote: ...` line in the on-screen stats HUD
   should now show `connected` instead of `disconnected — retrying...`.

This is a one-time step per device; it's independent of Wi-Fi reconnects and
only needs to be redone if the certificate is regenerated.
