import base64
import hashlib
import hmac
import json
import os

os.environ.setdefault("DATABASE_URL", "sqlite://")
for key in (
    "TODOIST_FLASK_SECRET_KEY",
    "TODOIST_CLIENT_ID",
    "TODOIST_CLIENT_SECRET",
    "GOOGLE_MAP_API_KEY",
):
    os.environ.setdefault(key, "test")

import pytest  # noqa: E402

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
