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
# Both said with a 502. A sweep that failed with reminders still at the old
# place is finished by submitting again; one where only adds were refused has
# saved the mapping, and the webhook re-creates each missing reminder.
SWEEP_FAILED = "Todoist did not accept every change. Submit the same change again."
ADDS_REFUSED = (
    "Todoist did not create every reminder. The change is saved; "
    "a missing reminder comes back the next time its task changes."
)

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
            connect=0,  # nor is a stall while connecting
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


def todoist_get_all(endpoint, token, params=None):
    """Every item of a paginated v1 list. Raises on API failure or an unexpected shape."""
    # Pages are {"results": [...], "next_cursor": ...}. Every page is read, and
    # anything else raises, so a second page is never mistaken for "not there".
    # A cursor that repeats would loop until gunicorn's 30 s timeout killed the
    # worker; it has not been seen, and it raises rather than spin.
    items, cursor, seen = [], None, set()
    while True:
        page = todoist_api_get(
            endpoint, token, {**(params or {}), **({"cursor": cursor} if cursor else {})}
        )
        results = page.get("results") if isinstance(page, dict) else None
        if not isinstance(results, list) or not all(isinstance(r, dict) for r in results):
            raise requests.exceptions.RequestException(f"{endpoint}: unexpected page shape")
        items.extend(results)
        cursor = page.get("next_cursor")
        if not cursor:
            return items
        if cursor in seen:
            raise requests.exceptions.RequestException(f"{endpoint}: cursor {cursor!r} repeated")
        seen.add(cursor)


def todoist_get_labels(token):
    """Fetch all labels for a user. Raises RequestException on API failure."""
    labels = todoist_get_all("labels", token)
    app.logger.info("Fetched %d labels", len(labels))
    return labels


def todoist_get_reminders(token, task_id=None):
    """Location reminders: one task's, or all of them. Raises RequestException on API failure.

    Read through REST, not sync: a full sync is limited to 100 per user per
    15 minutes, and the webhook reads on every task change.
    """
    return todoist_get_all("location_reminders", token, {"task_id": task_id} if task_id else None)


def todoist_get_user(token):
    """Fetch user profile info. Raises RequestException on API failure."""
    result = todoist_api_get("user", token)
    app.logger.info("Fetched user %s", result.get("id"))
    return result


def todoist_sync(token, commands):
    """Send a batch of commands to the Todoist sync endpoint (API v1)."""
    response = todoist_http.post(
        f"{TODOIST_API_BASE}/sync",
        headers=todoist_headers(token),
        data={"commands": json.dumps(commands)},
        timeout=TODOIST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


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


class TodoistRefused(requests.exceptions.RequestException):
    """Todoist answered 200 and refused some commands: `refused` maps each uuid to its status."""

    def __init__(self, what, refused):
        super().__init__(f"{what} refused: {refused}")
        self.refused = refused


def todoist_run_commands(token, commands, what):
    """Send sync commands, at most SYNC_BATCH per request.

    Every batch is sent; then TodoistRefused is raised if any command was refused.
    Todoist applies each command on its own, so a refusal does not undo the rest.
    """
    refused = {}
    for start in range(0, len(commands), SYNC_BATCH):
        batch = commands[start : start + SYNC_BATCH]
        answer = todoist_sync(token, commands=batch)
        sync_status = answer.get("sync_status") if isinstance(answer, dict) else None
        if not isinstance(sync_status, dict):
            # Earlier batches are applied and later ones are not sent: the route
            # answers 502 and the resubmit converges (the sweep skips what moved).
            raise requests.exceptions.RequestException(f"{what}: unexpected sync answer")
        app.logger.info("%s sync result: %s", what, sync_status)
        # Todoist answers HTTP 200 and reports a refused command here. A command
        # with no status at all counts as refused too.
        status = {c["uuid"]: sync_status.get(c["uuid"]) for c in batch}
        refused.update({k: v for k, v in status.items() if v != "ok"})
    if refused:
        raise TodoistRefused(what, refused)


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


def match_key(place):
    """The values a reminder is matched on: (name, trigger, radius)."""
    return (place[0], place[3], place[4])


def reminder_is_at(reminder, place):
    """True if a location reminder carries this place's name, trigger and radius.

    This is the only link between a mapping and the reminders made from it: the
    app stores no reminder ids. A reminder made by hand never shares these values:
    Todoist's geocoder formats an address differently from Google Places.
    """
    return reminder.get("type") == "location" and match_key(place) == (
        reminder.get("name"),
        reminder.get("loc_trigger"),
        reminder.get("radius"),
    )


def coordinate_of(value):
    """A reminder's loc_lat or loc_long as a number, or None if it is not one."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def reminder_is_exactly_at(reminder, place):
    """reminder_is_at, and the coordinates agree: the one reminder an edit keeps.

    Todoist returns a coordinate as the string it was sent (read back 2026-10-06),
    and the app sends str(float), so a mapping's floats equal its own reminder's.
    """
    return reminder_is_at(reminder, place) and (
        coordinate_of(reminder.get("loc_lat")),
        coordinate_of(reminder.get("loc_long")),
    ) == (place[1], place[2])


def sweep_reminders(token, old_place, new_place=None):
    """Bring every task holding one of a mapping's reminders to exactly one at new_place.

    Invariant, from any starting state: a task that holds a reminder matching the
    mapping, at the old place or the new one, ends with exactly one reminder, exactly
    at the new place, and no other at either; for a delete (new_place None), with none.
    Adds are sent before deletes, so whatever prefix Todoist applied, every task still
    holds a reminder and the next sweep converges. A resubmit is therefore always safe.
    Returns the number of adds Todoist refused; raises RequestException on API failure
    or a refused delete, in which case a reminder is still at the old place.
    """
    located = todoist_get_reminders(token)
    by_task: dict[str, list[dict]] = {}
    for reminder in located:
        if reminder_is_at(reminder, old_place) or (
            new_place is not None and reminder_is_at(reminder, new_place)
        ):
            by_task.setdefault(reminder["item_id"], []).append(reminder)
    matched = sum(len(reminders) for reminders in by_task.values())
    # Zero of many is how a reminder that no longer matches its mapping shows up.
    app.logger.info("sweep matched %d of %d location reminders", matched, len(located))
    adds: list[dict] = []
    deletes: list[dict] = []
    for item_id, reminders in by_task.items():
        keep = None
        if new_place is not None:
            keep = next((r for r in reminders if reminder_is_exactly_at(r, new_place)), None)
            if keep is None:
                adds.append(reminder_add_command(item_id, *new_place))
        deletes.extend(reminder_delete_command(r["id"]) for r in reminders if r is not keep)
    if new_place is not None and (kept := len(by_task) - len(adds)):
        app.logger.info("sweep: %d tasks already at the new place", kept)
    try:
        todoist_run_commands(token, adds + deletes, "sweep")
    except TodoistRefused as e:
        if not set(e.refused) <= {c["uuid"] for c in adds}:
            raise
        # Every delete went through, so nothing is left at the old place and the
        # mapping must move with the reminders that did: kept at the old place,
        # it would no longer match them, the webhook would add a second reminder
        # at the old place when such a task changes, and a resubmit would then
        # leave that task with two at the new place.
        app.logger.warning("sweep: %d adds refused, mapping saved: %s", len(e.refused), e)
        return len(e.refused)
    return 0


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
        kwargs["location_labels"] = {str(ll.label_id): ll for ll in user.location_labels}
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
    except (requests.exceptions.RequestException, KeyError, TypeError) as e:
        app.logger.error("Could not remove reminders, mapping %s kept: %s", location_label.id, e)
        return abort(502, description=SWEEP_FAILED)

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
    # the reminders made from it. Todoist stores nan and out-of-range coordinates
    # as sent (measured 2026-10-05); nan fails every comparison, so it is refused here.
    if not (
        1 <= radius <= MAX_RADIUS
        and 1 <= len(address) <= MAX_NAME
        and -90 <= lat <= 90
        and -180 <= long <= 180
    ):
        return abort(400)
    new_place = (address, lat, long, trigger, radius)
    missing = 0
    # Two mappings that match the same reminders cannot be told apart, so the
    # webhook would delete for one what it added for the other.
    for other in user.location_labels:
        if other.label_id != label_id and match_key(place_of(other)) == match_key(new_place):
            return abort(
                400,
                description="Another label is already mapped to this address, trigger and radius. "
                "Change one of the three.",
            )
    # Submitting a label that is already mapped edits that mapping, and moves
    # the reminders it has already created to the new place.
    location_label = LocationLabel.query.filter_by(user_id=user.id, label_id=label_id).first()
    if location_label is None:
        location_label = LocationLabel(user=user, label_id=label_id)
        db.session.add(location_label)
    elif place_of(location_label) != new_place:
        try:
            missing = sweep_reminders(user.oauth_token, place_of(location_label), new_place)
        except (requests.exceptions.RequestException, KeyError, TypeError) as e:
            app.logger.error(
                "Could not move reminders, mapping %s unchanged: %s", location_label.id, e
            )
            return abort(502, description=SWEEP_FAILED)
    (
        location_label.name,
        location_label.lat,
        location_label.long,
        location_label.loc_trigger,
        location_label.radius,
    ) = new_place
    db.session.commit()
    if missing:
        return abort(502, description=ADDS_REFUSED)
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
    event_data = event["event_data"]
    app.logger.info(
        "Received webhook event %s for item %s, labels: %s",
        event["event_name"],
        event_data["id"],
        event_data.get("labels", []),
    )
    # user_id is who the event was delivered for; the initiator may be a
    # collaborator in a shared project who is not a user of this app.
    user = db.session.get(User, int(event["user_id"]))
    if user is None:
        app.logger.warning("No user found for user_id %s", event["user_id"])
        return ""

    token = user.oauth_token

    # Both reads must succeed before anything is added or deleted: a failed
    # labels fetch must not read as "task has no labels". A non-200 makes
    # Todoist redeliver the event (15 min later, up to three times).
    try:
        all_labels = todoist_get_labels(token)
        item_reminders = todoist_get_reminders(token, event_data["id"])
    except (requests.exceptions.RequestException, KeyError, TypeError) as e:
        app.logger.error("Todoist API unavailable, asking for redelivery: %s", e)
        return "todoist api error", 503

    # Map label names -> IDs (API v1 webhooks carry names)
    label_name_to_id = {label["name"]: label["id"] for label in all_labels}
    app.logger.info("User has %d labels, name->id map built", len(all_labels))

    # Map the task's label names to label IDs (API v1 webhooks carry names)
    task_label_ids = set()
    for name in event_data.get("labels", []):
        label_id = label_name_to_id.get(name)
        if label_id is None:
            app.logger.warning("Label '%s' not found in user's labels", name)
        else:
            task_label_ids.add(str(label_id))

    app.logger.info("Task label IDs: %s", sorted(task_label_ids))

    app.logger.info("Existing location reminders for item: %d", len(item_reminders))

    # Find user's location-label configs
    user_location_labels = LocationLabel.query.filter_by(user_id=user.id).all()
    by_label = {str(ll.label_id): ll for ll in user_location_labels}

    # Determine which location labels are NOT on this task (for deletion)
    not_used_location_labels = [
        ll for ll in user_location_labels if str(ll.label_id) not in task_label_ids
    ]

    # A failed add or delete answers 503 as well: reporting the event as handled
    # would leave the task wrong with nothing to retry it.
    try:
        # Delete reminders for removed labels
        for reminder in item_reminders:
            for ll in not_used_location_labels:
                if reminder_is_at(reminder, place_of(ll)):
                    app.logger.info("Deleting reminder %s (label removed)", reminder["id"])
                    todoist_run_commands(
                        token, [reminder_delete_command(reminder["id"])], "reminder_delete"
                    )
                    break

        # Add reminders for matching labels
        for label_id in task_label_ids:
            loc_label = by_label.get(label_id)
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
            command = reminder_add_command(event_data["id"], *place_of(loc_label))
            todoist_run_commands(token, [command], "reminder_add")
    except requests.exceptions.RequestException as e:
        app.logger.error("Todoist write failed, asking for redelivery: %s", e)
        return "todoist api error", 503

    return "ok"


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "initdb":
        db.create_all()
    else:
        app.run(debug=True, use_reloader=True)
