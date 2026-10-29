#!/usr/bin/env python3
"""One-time helper: obtain a Gmail refresh token (scope gmail.send) for the MDA app.

Run it on your Mac, not on the server:

    python3 scripts/gmail-refresh-token.py

It asks for the OAuth client ID and client secret of a Google "Desktop app" client, opens a Google consent page,
and receives the answer on a local address (127.0.0.1). It then writes the three values to
~/mda-local-data/gmail-oauth.env with permissions 600. Nothing is printed except the file path.

Standard library only. The values are never sent anywhere except Google's own token endpoint.
"""
import getpass
import http.server
import os
import secrets
import threading
import urllib.parse
import urllib.request
import json
import webbrowser
from pathlib import Path

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/gmail.send"
OUT = Path.home() / "mda-local-data" / "gmail-oauth.env"


def main() -> None:
    client_id = input("OAuth client ID (ends with .apps.googleusercontent.com): ").strip()
    client_secret = getpass.getpass("OAuth client secret (input hidden): ").strip()
    if not client_id or not client_secret:
        raise SystemExit("Both the client ID and the client secret are required.")

    state = secrets.token_urlsafe(24)
    result: dict = {}
    done = threading.Event()

    class Callback(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 (http.server naming)
            params = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query))
            if params.get("state") != state:
                result["error"] = "state mismatch"
            elif "error" in params:
                result["error"] = params["error"]
            else:
                result["code"] = params.get("code", "")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Done. You can close this window.")
            done.set()

        def log_message(self, *args):  # keep the terminal quiet
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Callback)
    redirect_uri = f"http://127.0.0.1:{server.server_address[1]}/callback"
    threading.Thread(target=server.serve_forever, daemon=True).start()

    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",   # needed to receive a refresh token
        "prompt": "consent",        # makes Google return a refresh token every time
        "state": state,
    })
    print("Opening the Google consent page. Sign in with the dedicated Gmail account and allow sending.")
    print("If the browser does not open, paste this address into it:\n" + url)
    webbrowser.open(url)

    done.wait(timeout=600)
    server.shutdown()
    if "code" not in result or not result["code"]:
        raise SystemExit(f"No authorization code received ({result.get('error', 'timed out')}). Nothing was saved.")

    body = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "code": result["code"],
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }).encode("ascii")
    try:
        with urllib.request.urlopen(urllib.request.Request(TOKEN_URL, data=body), timeout=15) as r:
            tokens = json.load(r)
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"Google refused the token exchange (HTTP {exc.code}). Nothing was saved.")

    refresh = tokens.get("refresh_token")
    if not refresh:
        raise SystemExit("Google did not return a refresh token. Remove the app's access in your Google account "
                         "and run this script again.")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    content = (
        f"MDA_GMAIL_CLIENT_ID={client_id}\n"
        f"MDA_GMAIL_CLIENT_SECRET={client_secret}\n"
        f"MDA_GMAIL_REFRESH_TOKEN={refresh}\n"
    )
    fd = os.open(OUT, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(content)
    os.chmod(OUT, 0o600)
    print(f"Saved the three values to: {OUT}")
    print("Copy them into /etc/mda/env on the server (see docs/AUTH_SETUP.md). Do not paste them into chat.")


if __name__ == "__main__":
    main()
