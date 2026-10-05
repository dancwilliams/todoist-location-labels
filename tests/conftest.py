import base64
import hashlib
import hmac
import json
import os
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ.setdefault("DATABASE_URL", "sqlite://")
for key in (
    "TODOIST_FLASK_SECRET_KEY",
    "TODOIST_CLIENT_ID",
    "TODOIST_CLIENT_SECRET",
    "GOOGLE_MAP_API_KEY",
):
    os.environ.setdefault(key, "test")

import pytest  # noqa: E402
from requests.adapters import HTTPAdapter  # noqa: E402

import app as app_module  # noqa: E402


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.app_context():
        app_module.db.create_all()
        yield app_module.app.test_client()
        app_module.db.session.remove()
        app_module.db.drop_all()


@pytest.fixture
def user(client):
    u = app_module.User(id=1, oauth_token="tok")
    app_module.db.session.add(u)
    app_module.db.session.add(
        app_module.LocationLabel(
            user=u,
            label_id=10,
            name="Home",
            lat=1.0,
            long=2.0,
            loc_trigger="on_enter",
            radius=100.0,
        )
    )
    app_module.db.session.commit()
    return u


@pytest.fixture
def post_webhook(client, monkeypatch):
    """POST an event to /webhook signed the way Todoist signs it (secret "test").

    Pass signature=None to send no header, or a string to send a wrong one.
    """
    monkeypatch.setattr(app_module, "client_secret", "test")

    def post(event, signature="valid"):
        body = json.dumps(event).encode()
        headers = {"Content-Type": "application/json"}
        if signature == "valid":
            signature = base64.b64encode(hmac.new(b"test", body, hashlib.sha256).digest()).decode()
        if signature is not None:
            headers["X-Todoist-Hmac-SHA256"] = signature
        return client.post("/webhook", data=body, headers=headers)

    return post


@pytest.fixture
def login(client, user):
    """Log the test client in as the `user` fixture."""
    with client.session_transaction() as sess:
        sess["user_id"] = user.id
    return user


@pytest.fixture
def fake_todoist(monkeypatch):
    """A local HTTP server standing in for the Todoist endpoints the app calls.

    state["labels"] and state["reminders"] are what reads return, state["commands"]
    collects every sync command received, state["posts"] the form fields of each
    POST, state["hits"] every request. state["status"] and state["stall"] (seconds)
    make it misbehave. The app's retry policy applies, with the waits removed.
    """
    state = {
        "labels": [],
        "reminders": [],
        "commands": [],
        "posts": [],
        "hits": 0,
        "status": 200,
        "stall": 0.0,
    }

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, payload):
            time.sleep(state["stall"])
            body = json.dumps(payload).encode()
            try:
                self.send_response(state["status"])
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except OSError:
                pass  # the client gave up waiting

        def do_GET(self):
            state["hits"] += 1
            self._reply({"results": state["labels"], "next_cursor": None})

        def do_POST(self):
            state["hits"] += 1
            raw = self.rfile.read(int(self.headers["Content-Length"])).decode()
            form = urllib.parse.parse_qs(raw)
            state["posts"].append(sorted(form))
            commands = json.loads(form.get("commands", ["[]"])[0])
            state["commands"].extend(commands)
            self._reply(
                {
                    "reminders": state["reminders"],
                    "sync_status": {c["uuid"]: "ok" for c in commands},
                }
            )

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(app_module, "TODOIST_API_BASE", f"http://127.0.0.1:{server.server_port}")
    policy = app_module.todoist_http.get_adapter("https://api.todoist.com").max_retries
    app_module.todoist_http.mount(
        "http://127.0.0.1", HTTPAdapter(max_retries=policy.new(backoff_factor=0))
    )
    yield state
    server.shutdown()
    app_module.todoist_http.adapters.pop("http://127.0.0.1")
