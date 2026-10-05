#!/usr/bin/env python3
"""Single-port HTTPS static file server + WSS broadcast relay.

Self-signed certs are trusted per host:port in browsers (and WebSocket
connections can't show a "proceed anyway" prompt — see README.md), so
running the relay on a separate port from the static site requires a
*second*, easy-to-miss trust step. This combines both onto the same
port/cert the sim page is served from: once that one certificate is
trusted, both the page load and its WebSocket connection succeed.

- Plain HTTP GET requests are served as static files from this script's
  directory (i.e. the repo root).
- WebSocket upgrade requests are handled as a pure broadcast relay: every
  message from one client is sent to every other connected client,
  verbatim, with no server-side state. The sim page (index.html) and the
  remote-control page (remote.html) agree on a small JSON message
  protocol between themselves; see their inline comments.

Requires a self-signed cert/key pair (cert.pem/key.pem) next to this
script — see README.md for how to generate one.

Configurable via environment variables (all optional):
  PARTICLE_FORGE_HOST  bind address (default: 0.0.0.0)
  PARTICLE_FORGE_PORT  port for both HTTPS and WSS (default: 8443)
  PARTICLE_FORGE_CERT  path to cert.pem (default: cert.pem next to this script)
  PARTICLE_FORGE_KEY   path to key.pem  (default: key.pem next to this script)
"""
import asyncio
import mimetypes
import os
import ssl

import websockets
from websockets.datastructures import Headers
from websockets.http11 import Response

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BIND_ADDR = os.environ.get("PARTICLE_FORGE_HOST", "0.0.0.0")
PORT = int(os.environ.get("PARTICLE_FORGE_PORT", "8443"))
CERT = os.environ.get("PARTICLE_FORGE_CERT", os.path.join(SCRIPT_DIR, "cert.pem"))
KEY = os.environ.get("PARTICLE_FORGE_KEY", os.path.join(SCRIPT_DIR, "key.pem"))
WEB_ROOT = os.path.realpath(SCRIPT_DIR)

clients = set()


def serve_static(request_path):
    path = request_path.split("?", 1)[0].split("#", 1)[0]
    if path == "/":
        path = "/index.html"
    full_path = os.path.realpath(os.path.join(WEB_ROOT, path.lstrip("/")))
    if not (full_path == WEB_ROOT or full_path.startswith(WEB_ROOT + os.sep)):
        return Response(403, "Forbidden", Headers(), b"403 Forbidden")
    if not os.path.isfile(full_path):
        return Response(404, "Not Found", Headers(), b"404 Not Found")
    with open(full_path, "rb") as f:
        body = f.read()
    if full_path.endswith(".mobileconfig"):
        # iOS only offers to install a configuration profile (our self-signed
        # root CA) when served with this exact MIME type.
        content_type = "application/x-apple-aspen-config"
    else:
        content_type = mimetypes.guess_type(full_path)[0] or "application/octet-stream"
    headers = Headers()
    headers["Content-Type"] = content_type
    headers["Content-Length"] = str(len(body))
    return Response(200, "OK", headers, body)


async def process_request(connection, request):
    # Leave WebSocket upgrade requests (relay traffic) to the handler below.
    if request.headers.get("Upgrade", "").lower() == "websocket":
        return None
    return serve_static(request.path)


async def relay_handler(ws):
    clients.add(ws)
    try:
        async for msg in ws:
            stale = set()
            for c in clients:
                if c is ws:
                    continue
                try:
                    await c.send(msg)
                except websockets.exceptions.ConnectionClosed:
                    stale.add(c)
            clients.difference_update(stale)
    finally:
        clients.discard(ws)


async def main():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=CERT, keyfile=KEY)
    async with websockets.serve(
        relay_handler, BIND_ADDR, PORT, ssl=ctx, process_request=process_request
    ):
        print(f"Serving https://{BIND_ADDR}:{PORT} and wss://{BIND_ADDR}:{PORT} (same port)")
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
