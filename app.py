import base64
import hashlib
import hmac
import json
import logging
import os
import sys
import urllib.parse
import uuid
from datetime import timedelta

import requests
from flask import (
    Flask,
    abort,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_sqlalchemy import SQLAlchemy
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

app = Flask(__name__)

# Configure logging
logging.basicConfig(level=logging.INFO)
app.logger.setLevel(logging.INFO)

# The session is Flask's signed cookie: it holds only user_id and the OAuth
# state, needs no server-side store, and so survives deploys and restarts.
app.config.update(
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
)

# pre_ping and recycle guard against Fly Postgres dropping idle connections
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True, "pool_recycle": 299}
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///test.db")

app.secret_key = os.environ["TODOIST_FLASK_SECRET_KEY"]
db = SQLAlchemy(app)
client_id = os.environ["TODOIST_CLIENT_ID"]
client_secret = os.environ["TODOIST_CLIENT_SECRET"]
google_map_api_key = os.environ["GOOGLE_MAP_API_KEY"]
google_analytics_id = os.environ.get("GOOGLE_ANALYTICS_ID")


class User(db.Model):  # type: ignore[name-defined]
    id = db.Column(db.BigInteger, primary_key=True)
    oauth_token = db.Column(db.String(64), nullable=True)


class LocationLabel(db.Model):  # type: ignore[name-defined]
    # One mapping per user and label. Production has this constraint from a
    # one-off ALTER TABLE (2026-10-05); create_all only adds it to new databases.
    __table_args__ = (
        db.UniqueConstraint("user_id", "label_id", name="uq_location_label_user_label"),
    )
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.BigInteger, db.ForeignKey("user.id"), nullable=False)
    user = db.relationship("User", backref=db.backref("location_labels", lazy="dynamic"))
    label_id = db.Column(db.BigInteger, nullable=False, index=True)
    name = db.Column(db.String, nullable=False)
    long = db.Column(db.Float, nullable=False)
    lat = db.Column(db.Float, nullable=False)
    loc_trigger = db.Column(db.String, nullable=False)
    radius = db.Column(db.Float, nullable=False)


def get_current_user():
    user_id = session.get("user_id")
    if user_id is None:
        abort(401)
    user = db.session.get(User, user_id)
    if user is None:
        abort(401)
    return user


def log_request(route):
    ip = request.headers.get("Fly-Client-IP")
    if ip is None:  # Fly's health check: one line every 15 s would bury everything else
        return
    app.logger.info("Request made to %s: IP %s", route, ip)


TODOIST_API_BASE = "https://api.todoist.com/api/v1"

TODOIST_TIMEOUT = 10  # seconds, per request
SYNC_BATCH = 100  # Todoist accepts at most 100 commands per sync request
# A location reminder's limits, read back from the live API on 2026-10-05: a
# larger radius is stored as 255, a longer name or a fractional radius is refused.
MAX_RADIUS = 255  # meters
MAX_NAME = 255  # characters

# One session for every outbound call. A 429 or 5xx answer is retried three
# times, after waits of 0, 2 and 4 s (measured: urllib3 does not wait before
# the first retry), GET and POST alike: sync commands carry a uuid Todoist
# dedupes on, so a retried POST cannot apply twice. Anything else, a 401
# included, fails at once. Retry-After is ignored so a rate limit cannot hold
# the worker for minutes.
todoist_http = requests.Session()
todoist_http.mount(
    "https://",
    HTTPAdapter(
        max_retries=Retry(
            total=3,
            read=0,  # a stall is not retried: 4 x 10 s would outlast gunicorn's 30 s worker timeout
            backoff_factor=1,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET", "POST"),
            respect_retry_after_header=False,
            raise_on_status=False,
        )
    ),
)


def todoist_headers(token):
    return {"Authorization": f"Bearer {token}"}


def webhook_signature_ok(raw_body: bytes, header: str | None) -> bool:
    """Todoist signs each delivery: base64(HMAC-SHA256(client_secret, raw body))."""
    expected = base64.b64encode(hmac.new(client_secret.encode(), raw_body, hashlib.sha256).digest())
    # Bytes, not str: compare_digest raises on a non-ASCII str, and the header is caller-supplied.
    return header is not None and hmac.compare_digest(expected, header.encode("latin-1", "replace"))


def todoist_api_get(endpoint, token, params=None):
    """GET from Todoist API v1."""
    url = f"{TODOIST_API_BASE}/{endpoint}"
    response = todoist_http.get(
        url, headers=todoist_headers(token), params=params, timeout=TODOIST_TIMEOUT
    )
    if not response.ok:
        app.logger.error(
            "Todoist API GET %s failed: %s %s body=%s",
            url,
            response.status_code,
            response.reason,
            response.text[:500],
        )
    response.raise_for_status()
    return response.json()


def todoist_get_labels(token):
    """Fetch all labels for a user. Raises RequestException on API failure."""
    # API v1 returns pages: {"results": [...], "next_cursor": ...}. Every page is
    # read, and anything else raises, so neither a malformed response nor a
    # second page is ever mistaken for "label not on this account".
    labels, cursor = [], None
    while True:
        page = todoist_api_get("labels", token, {"cursor": cursor} if cursor else None)
        labels.extend(page["results"])
        cursor = page.get("next_cursor")
        if not cursor:
            break
    app.logger.info("Fetched %d labels", len(labels))
    return labels


def todoist_get_user(token):
    """Fetch user profile info. Raises RequestException on API failure."""
    result = todoist_api_get("user", token)
    app.logger.info("Fetched user %s", result.get("id"))
    return result


def todoist_sync(token, resource_types=None, commands=None):
    """Call the Todoist sync endpoint (API v1): a read, a batch of commands, or both."""
    url = f"{TODOIST_API_BASE}/sync"
    data = {}
    if resource_types:  # a read; left out for a command, or Todoist returns the whole account
        data["sync_token"] = "*"
        data["resource_types"] = json.dumps(resource_types)
    if commands:
        data["commands"] = json.dumps(commands)
    response = todoist_http.post(
        url, headers=todoist_headers(token), data=data, timeout=TODOIST_TIMEOUT
    )
    response.raise_for_status()
    return response.json()


def todoist_get_reminders(token):
    """Fetch all reminders via sync. Raises RequestException on API failure."""
    result = todoist_sync(token, resource_types=["reminders", "reminders_location"])
    return result.get("reminders", [])


def reminder_add_command(item_id, name, loc_lat, loc_long, loc_trigger, radius):
    return {
        "type": "reminder_add",
        "temp_id": str(uuid.uuid4()),
        "uuid": str(uuid.uuid4()),
        "args": {
            "item_id": str(item_id),
            "type": "location",
            "name": name,
            "loc_lat": str(loc_lat),
            "loc_long": str(loc_long),
            "loc_trigger": loc_trigger,
            "radius": radius,
        },
    }


def reminder_delete_command(reminder_id):
    return {
        "type": "reminder_delete",
        "uuid": str(uuid.uuid4()),
        "args": {"id": str(reminder_id)},
    }


def todoist_run_commands(token, commands, what):
    """Send sync commands, at most SYNC_BATCH per request, and log any Todoist refused."""
    for start in range(0, len(commands), SYNC_BATCH):
        result = todoist_sync(token, commands=commands[start : start + SYNC_BATCH])
        sync_status = result.get("sync_status", {})
        app.logger.info("%s sync result: %s", what, sync_status)
        for k, v in sync_status.items():
            if v != "ok":
                app.logger.error("%s failed: %s -> %s", what, k, v)


def todoist_add_reminder(token, item_id, name, loc_lat, loc_long, loc_trigger, radius):
    """Add a location reminder via sync command."""
    command = reminder_add_command(item_id, name, loc_lat, loc_long, loc_trigger, radius)
    todoist_run_commands(token, [command], "reminder_add")


def todoist_delete_reminder(token, reminder_id):
    """Delete a reminder via sync command."""
    todoist_run_commands(token, [reminder_delete_command(reminder_id)], "reminder_delete")


def place_of(location_label):
    """What a reminder is built from: (name, lat, long, trigger, radius), as Todoist stores it.

    Todoist strips the name and stores any radius over MAX_RADIUS as MAX_RADIUS,
    answering "ok" either way. The form refuses such values now; a row saved
    before it did is normalised here, or it would never match its own reminders.
    """
    return (
        location_label.name.strip(),
        location_label.lat,
        location_label.long,
        location_label.loc_trigger,
        min(int(location_label.radius), MAX_RADIUS),
    )


def reminder_is_at(reminder, place):
    """True if a location reminder carries this place's name, trigger and radius.

    This is the only link between a mapping and the reminders made from it: the
    app stores no reminder ids. A reminder the user made by hand with the same
    three values is indistinguishable from one of ours.
    """
    name, _lat, _long, loc_trigger, radius = place
    return (
        reminder.get("type") == "location"
        and reminder.get("name") == name
        and reminder.get("loc_trigger") == loc_trigger
        and reminder.get("radius") == radius
    )


def sweep_reminders(token, old_place, new_place=None):
    """Remove every reminder made from a mapping; for an edit, re-add each at the new place.

    Without this, changing or deleting a mapping would strand its reminders: they
    would match no mapping, so no later webhook could remove them. Raises
    RequestException on API failure, before or between batches.
    """
    commands = []
    located = [r for r in todoist_get_reminders(token) if r.get("type") == "location"]
    matched = [r for r in located if reminder_is_at(r, old_place)]
    # Zero of many is how a reminder that no longer matches its mapping shows up.
    app.logger.info("sweep matched %d of %d location reminders", len(matched), len(located))
    for reminder in matched:
        commands.append(reminder_delete_command(reminder["id"]))
        if new_place is not None:
            commands.append(reminder_add_command(reminder["item_id"], *new_place))
    todoist_run_commands(token, commands, "sweep")


@app.route("/")
def index():
    log_request("/")
    user_id = session.get("user_id")
    kwargs: dict[str, object] = {
        "google_map_api_key": google_map_api_key,
        "google_analytics_id": google_analytics_id,
    }
    user = db.session.get(User, user_id) if user_id is not None else None
    if user_id is not None and user is None:
        session.pop("user_id", None)  # stale session: the user row is gone
    if user is not None:
        app.logger.info("user_id: %s", user_id)
        try:
            labels = todoist_get_labels(user.oauth_token)
            user_info = todoist_get_user(user.oauth_token)
        except (requests.exceptions.RequestException, KeyError, TypeError) as e:
            app.logger.error("Todoist API unavailable, rendering without labels: %s", e)
            labels, user_info = [], {}
        kwargs["labels"] = labels
        kwargs["user_full_name"] = user_info.get("full_name", "")
        # map from label id to location labels
        location_labels = {}
        for item in user.location_labels.all():
            location_labels[str(item.label_id)] = item
        kwargs["location_labels"] = location_labels
    return render_template("index.html", **kwargs)


@app.route("/authorize")
def authorize():
    log_request("/authorize")
    state = base64.b64encode(os.urandom(32)).decode("utf8")
    session["oauth_secret_state"] = state
    return redirect(
        "https://app.todoist.com/oauth/authorize?"
        + urllib.parse.urlencode(
            dict(
                client_id=client_id,
                scope="data:read_write,data:delete",
                state=state,
            )
        )
    )


@app.route("/oauth/redirect")
def oauth_redirect():
    log_request("/oauth/redirect")
    state = session.get("oauth_secret_state")
    if not state or request.args.get("state") != state:
        return abort(401)
    code = request.args.get("code")
    if not code:
        return abort(400)
    try:
        resp = todoist_http.post(
            "https://api.todoist.com/oauth/access_token",
            data=dict(
                client_id=client_id,
                client_secret=client_secret,
                code=code,
                redirect_uri=url_for("authorize", _external=True),
            ),
            timeout=TODOIST_TIMEOUT,
        )
        resp.raise_for_status()
    except requests.exceptions.RequestException as err:
        app.logger.error("OAuth token exchange failed: %s", err)
        return abort(502)
    access_token = resp.json()["access_token"]
    try:
        user_info = todoist_get_user(access_token)
    except requests.exceptions.RequestException as e:
        app.logger.error("Failed to fetch user info after OAuth: %s", e)
        return abort(502)
    user_id = user_info["id"]
    user = db.session.get(User, user_id)
    if user is None:
        user = User(id=user_id, oauth_token=access_token)
        db.session.add(user)
    else:
        user.oauth_token = access_token
    db.session.commit()
    session["user_id"] = user.id
    session.permanent = True  # 30 days, see PERMANENT_SESSION_LIFETIME
    return redirect(url_for("index"))


@app.route("/logout")
def logout():
    log_request("/logout")
    session.pop("user_id", None)
    return redirect(url_for("index"))


@app.route("/delete_label_location/<int:location_label_id>", methods=["POST"])
def delete_label_location(location_label_id):
    log_request(f"/delete_label_location/{location_label_id}")
    user = get_current_user()
    location_label = db.session.get(LocationLabel, location_label_id)
    if location_label is None or location_label.user_id != user.id:
        return abort(404)
    try:
        sweep_reminders(user.oauth_token, place_of(location_label))
    except requests.exceptions.RequestException as e:
        app.logger.error("Could not remove reminders, mapping %s kept: %s", location_label.id, e)
        return abort(502)

    db.session.delete(location_label)
    db.session.commit()
    return redirect(url_for("index"))


@app.route("/create_label_location", methods=["POST"])
def create_label_location():
    log_request("/create_label_location")
    user = get_current_user()
    trigger = request.form["trigger"]
    address = request.form["address"].strip()
    try:
        label_id = int(request.form["label_id"])
        lat = float(request.form["lat"])
        long = float(request.form["long"])
        radius = int(request.form["radius"])
    except ValueError:
        return abort(400)  # e.g. an address typed without picking a suggestion: no lat/long
    if trigger not in ("on_enter", "on_leave"):
        return abort(400)
    # Only what Todoist stores unchanged is accepted, so a mapping always equals
    # the reminders made from it.
    if not (1 <= radius <= MAX_RADIUS and 1 <= len(address) <= MAX_NAME):
        return abort(400)
    # Submitting a label that is already mapped edits that mapping, and moves
    # the reminders it has already created to the new place.
    new_place = (address, lat, long, trigger, radius)
    location_label = LocationLabel.query.filter_by(user_id=user.id, label_id=label_id).first()
    if location_label is None:
        location_label = LocationLabel(user=user, label_id=label_id)
        db.session.add(location_label)
    elif place_of(location_label) != new_place:
        try:
            sweep_reminders(user.oauth_token, place_of(location_label), new_place)
        except requests.exceptions.RequestException as e:
            app.logger.error(
                "Could not move reminders, mapping %s unchanged: %s", location_label.id, e
            )
            return abort(502)
    (
        location_label.name,
        location_label.lat,
        location_label.long,
        location_label.loc_trigger,
        location_label.radius,
    ) = new_place
    db.session.commit()
    return redirect(url_for("index"))


@app.route("/webhook", methods=["POST"])
def webhook():
    log_request("/webhook")
    # Only Todoist knows the client secret, so only Todoist can sign a delivery.
    # Checked before the body is trusted for anything.
    if not webhook_signature_ok(request.get_data(), request.headers.get("X-Todoist-Hmac-SHA256")):
        app.logger.warning("webhook rejected: bad or missing signature")
        return abort(401)
    event = request.json
    if event["event_name"] not in ["item:added", "item:updated"]:
        return ""
    initiator = event["initiator"]
    event_data = event["event_data"]
    app.logger.info(
        "Received webhook event %s for item %s, labels: %s",
        event["event_name"],
        event_data["id"],
        event_data.get("labels", []),
    )
    user = db.session.get(User, int(initiator["id"]))
    if user is None:
        app.logger.warning("No user found for initiator %s", initiator["id"])
        return ""

    token = user.oauth_token

    # Both reads must succeed before anything is added or deleted: a failed
    # labels fetch must not read as "task has no labels". A non-200 makes
    # Todoist redeliver the event (15 min later, up to three times).
    try:
        all_labels = todoist_get_labels(token)
        all_reminders = todoist_get_reminders(token)
    except (requests.exceptions.RequestException, KeyError, TypeError) as e:
        app.logger.error("Todoist API unavailable, asking for redelivery: %s", e)
        return "todoist api error", 503

    # Map label names -> IDs (API v1 webhooks carry names)
    label_name_to_id = {label["name"]: label["id"] for label in all_labels}
    app.logger.info("User has %d labels, name->id map built", len(all_labels))

    # Map the task's label names to label IDs
    task_label_names = event_data.get("labels", [])
    task_label_ids = []
    for name in task_label_names:
        label_id = label_name_to_id.get(name)
        if label_id is not None:
            task_label_ids.append(label_id)
        else:
            app.logger.warning("Label '%s' not found in user's labels", name)

    app.logger.info("Task label IDs: %s", task_label_ids)

    # Existing location reminders for this item
    item_reminders = [
        r
        for r in all_reminders
        if r.get("type") == "location" and str(r.get("item_id")) == str(event_data["id"])
    ]
    app.logger.info("Existing location reminders for item: %d", len(item_reminders))

    # Find user's location-label configs
    user_location_labels = LocationLabel.query.filter_by(user_id=initiator["id"]).all()

    # Determine which location labels are NOT on this task (for deletion)
    task_label_id_strs = [str(lid) for lid in task_label_ids]
    not_used_location_labels = [
        ll for ll in user_location_labels if str(ll.label_id) not in task_label_id_strs
    ]

    # A failed add or delete answers 503 as well: reporting the event as handled
    # would leave the task wrong with nothing to retry it.
    try:
        # Delete reminders for removed labels
        for reminder in item_reminders:
            for ll in not_used_location_labels:
                if reminder_is_at(reminder, place_of(ll)):
                    app.logger.info("Deleting reminder %s (label removed)", reminder["id"])
                    todoist_delete_reminder(token, reminder["id"])
                    break

        # Add reminders for matching labels
        for label_id in task_label_ids:
            loc_label = user.location_labels.filter_by(label_id=label_id).first()
            if loc_label is None:
                app.logger.info("No location config for label %s, skip", label_id)
                continue
            if any(reminder_is_at(r, place_of(loc_label)) for r in item_reminders):
                app.logger.info(
                    "Reminder already exists for item %s / mapping %s",
                    event_data["id"],
                    loc_label.id,
                )
                continue
            app.logger.info(
                "Adding location reminder: item=%s, mapping=%s, trigger=%s",
                event_data["id"],
                loc_label.id,
                loc_label.loc_trigger,
            )
            todoist_add_reminder(token, event_data["id"], *place_of(loc_label))
    except requests.exceptions.RequestException as e:
        app.logger.error("Todoist write failed, asking for redelivery: %s", e)
        return "todoist api error", 503

    return "ok"


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "initdb":
        db.create_all()
    else:
        app.run(debug=True, use_reloader=True)
