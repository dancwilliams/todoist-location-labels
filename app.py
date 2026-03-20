import base64
import logging
import os
import sys
import urllib.parse
import uuid
from datetime import datetime

import requests
from flask import (
    Flask,
    abort,
    json,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_session import Session  # Import Session
from flask_sqlalchemy import SQLAlchemy
from opentelemetry import trace
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_fixed,
)

app = Flask(__name__)

# Configure logging
logging.basicConfig(level=logging.INFO)
app.logger.setLevel(logging.INFO)

# Configure your app for Flask-Session
app.config["SESSION_TYPE"] = "filesystem"
app.config["SESSION_PERMANENT"] = True
app.config["PERMANENT_SESSION_LIFETIME"] = 86400  # e.g., one day

# pool_pre_ping should help handle DB connection drops
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
    "DATABASE_URL", "sqlite:///test.db"
)
app.config["SQLALCHEMY_POOL_SIZE"] = 10
app.config["SQLALCHEMY_POOL_TIMEOUT"] = 30
app.config["SQLALCHEMY_POOL_RECYCLE"] = 299

app.secret_key = os.environ["TODOIST_FLASK_SECRET_KEY"]
db = SQLAlchemy(app)
client_id = os.environ["TODOIST_CLIENT_ID"]
client_secret = os.environ["TODOIST_CLIENT_SECRET"]
google_map_api_key = os.environ["GOOGLE_MAP_API_KEY"]
google_analytics_id = os.environ.get("GOOGLE_ANALYTICS_ID")

tracer = trace.get_tracer("todoist-flask")

Session(app)  # Initialize Flask-Session


class User(db.Model):
    id = db.Column(db.BigInteger, primary_key=True)
    oauth_token = db.Column(db.String(64), nullable=True)


class LocationLabel(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.BigInteger, db.ForeignKey("user.id"), nullable=False)
    user = db.relationship(
        "User", backref=db.backref("location_labels", lazy="dynamic")
    )
    label_id = db.Column(db.BigInteger, nullable=False, index=True)
    name = db.Column(db.String, nullable=False)
    long = db.Column(db.Float, nullable=False)
    lat = db.Column(db.Float, nullable=False)
    loc_trigger = db.Column(db.String, nullable=False)
    radius = db.Column(db.Float, nullable=False)


# with app.app_context():
#    db.create_all()


@tracer.start_as_current_span("get_current_user")
def get_current_user():
    user_id = session.get("user_id")
    if user_id is None:
        abort(401)
    user = User.query.get(user_id)
    if user is None:
        abort(401)
    return user


def log_request(route):
    ip = request.headers.get("Fly-Client-IP")
    app.logger.info(f"Request made to {route}: IP {ip} at {datetime.now()}")


def log_retry_attempt(retry_state):
    app.logger.warning(f"Retrying API Call: Attempt {retry_state.attempt_number}")


def log_retry_error(retry_state):
    app.logger.error(f"Retry failed: {retry_state.outcome.exception()}")


TODOIST_API_BASE = "https://api.todoist.com/api/v1"


def todoist_headers(token):
    return {"Authorization": f"Bearer {token}"}


@retry(
    retry=retry_if_exception_type(requests.exceptions.HTTPError),
    stop=stop_after_attempt(3),
    wait=wait_fixed(2),
    before=log_retry_attempt,
    after=log_retry_error,
)
def todoist_api_get(endpoint, token):
    """GET from Todoist API v1."""
    url = f"{TODOIST_API_BASE}/{endpoint}"
    response = requests.get(url, headers=todoist_headers(token), timeout=10)
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
    """Fetch all labels for a user."""
    try:
        result = todoist_api_get("labels", token)
        # API v1 returns paginated dict with 'results' key
        if isinstance(result, dict) and "results" in result:
            labels = result["results"]
        elif isinstance(result, list):
            labels = result
        else:
            app.logger.warning("Unexpected labels response: %s", str(result)[:300])
            labels = []
        app.logger.info("Fetched %d labels", len(labels))
        return labels
    except requests.exceptions.RequestException as e:
        app.logger.error(f"Failed to fetch labels: {e}")
        return []


def todoist_get_user(token):
    """Fetch user profile info."""
    try:
        result = todoist_api_get("user", token)
        app.logger.info("User API returned: %s", str(result)[:200])
        return result
    except requests.exceptions.RequestException as e:
        app.logger.error(f"Failed to fetch user: {e}")
        return {}


def todoist_sync(token, resource_types=None, commands=None):
    """Call Todoist Sync endpoint (API v1)."""
    url = f"{TODOIST_API_BASE}/sync"
    data = {
        "sync_token": "*",
        "resource_types": json.dumps(resource_types or ["all"]),
        "commands": json.dumps(commands or []),
    }
    response = requests.post(url, headers=todoist_headers(token), data=data, timeout=15)
    response.raise_for_status()
    return response.json()


def todoist_get_reminders(token):
    """Fetch all reminders via sync."""
    try:
        result = todoist_sync(token, resource_types=["reminders", "reminders_location"])
        return result.get("reminders", [])
    except requests.exceptions.RequestException as e:
        app.logger.error(f"Failed to fetch reminders: {e}")
        return []


def todoist_add_reminder(token, item_id, name, loc_lat, loc_long, loc_trigger, radius):
    """Add a location reminder via sync command."""
    cmd = {
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
    result = todoist_sync(token, commands=[cmd])
    sync_status = result.get("sync_status", {})
    app.logger.info("reminder_add sync result: %s", sync_status)
    for k, v in sync_status.items():
        if v != "ok":
            app.logger.error("reminder_add failed: %s -> %s", k, v)
    return result


def todoist_delete_reminder(token, reminder_id):
    """Delete a reminder via sync command."""
    cmd = {
        "type": "reminder_delete",
        "uuid": str(uuid.uuid4()),
        "args": {"id": str(reminder_id)},
    }
    result = todoist_sync(token, commands=[cmd])
    sync_status = result.get("sync_status", {})
    app.logger.info("reminder_delete sync result: %s", sync_status)
    return result


@app.route("/")
@tracer.start_as_current_span("index")
def index():
    log_request("/")
    user_id = session.get("user_id")
    kwargs = {
        "google_map_api_key": google_map_api_key,
        "google_analytics_id": google_analytics_id,
    }
    if user_id is not None:
        user = User.query.get(user_id)
        app.logger.info(f"user_id: {user_id}")
        labels = todoist_get_labels(user.oauth_token)
        kwargs["labels"] = labels
        user_info = todoist_get_user(user.oauth_token)
        kwargs["user_full_name"] = user_info.get("full_name", "")
        # map from label id to location labels
        location_labels = {}
        for item in user.location_labels.all():
            location_labels[str(item.label_id)] = item
        kwargs["location_labels"] = location_labels
    return render_template("index.html", **kwargs)


@app.route("/authorize")
@tracer.start_as_current_span("authorize")
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
@tracer.start_as_current_span("oauth_redirect")
def oauth_redirect():
    log_request("/oauth/redirect")
    state = session["oauth_secret_state"]
    if request.args.get("state") != state:
        return abort(401)
    code = request.args.get("code")
    if not code:
        return abort(400)
    try:
        resp = requests.post(
            "https://api.todoist.com/oauth/access_token",
            data=dict(
                client_id=client_id,
                client_secret=client_secret,
                code=code,
                redirect_uri=url_for("authorize", _external=True),
            ),
        )
        resp.raise_for_status()
    except requests.exceptions.HTTPError as err:
        app.logger.error(f"HTTP Error occurred: {err}")
        app.logger.error(f"Response status: {resp.status_code}")
        app.logger.error(f"Response headers: {resp.headers}")
        app.logger.error(f"Response body: {resp.text}")
        return abort(500)
    access_token = resp.json()["access_token"]
    user_info = todoist_get_user(access_token)
    if not user_info:
        app.logger.error("Failed to fetch user info after OAuth")
        return abort(500)
    user_id = user_info["id"]
    user = User.query.get(user_id)
    if user is None:
        user = User(id=user_id, oauth_token=access_token)
        db.session.add(user)
    else:
        user.oauth_token = access_token
    db.session.commit()
    session["user_id"] = user.id
    return redirect(url_for("index"))


@app.route("/logout")
@tracer.start_as_current_span("logout")
def logout():
    log_request("/logout")
    del session["user_id"]
    return redirect(url_for("index"))


@app.route("/delete_label_location/<int:label_location_id>")
@tracer.start_as_current_span("delete_label_location")
def delete_label_location(label_location_id):
    log_request(f"/delete_label_location/{label_location_id}")
    user = get_current_user()
    label_location = LocationLabel.query.filter_by(label_id=label_location_id).all()[0]
    if label_location is None:
        return abort(404)
    if label_location.user.id != user.id:
        return abort(401)

    db.session.delete(label_location)
    db.session.commit()
    return redirect(url_for("index"))


@app.route("/create_label_location", methods=["POST"])
@tracer.start_as_current_span("create_label_location")
def create_label_location():
    log_request("/create_label_location")
    user = get_current_user()
    label_id = int(request.form["label_id"])
    trigger = request.form["trigger"]
    address = request.form["address"]
    lat = float(request.form["lat"])
    long = float(request.form["long"])
    radius = float(request.form.get("radius", 300))
    location_label = LocationLabel(
        user=user,
        label_id=label_id,
        loc_trigger=trigger,
        long=long,
        lat=lat,
        name=address,
        radius=radius,
    )
    db.session.add(location_label)
    db.session.commit()
    return redirect(url_for("index"))


@app.route("/webhook", methods=["POST"])
@tracer.start_as_current_span("webhook")
def webhook():
    log_request("/webhook")
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
    user = User.query.get(int(initiator["id"]))
    if user is None:
        app.logger.warning("No user found for initiator %s", initiator["id"])
        return ""

    token = user.oauth_token

    # Get all user's labels (API v1) to map names -> IDs
    all_labels = todoist_get_labels(token)
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

    # Get existing location reminders for this item
    all_reminders = todoist_get_reminders(token)
    item_reminders = [
        r
        for r in all_reminders
        if r.get("type") == "location"
        and str(r.get("item_id")) == str(event_data["id"])
    ]
    app.logger.info("Existing location reminders for item: %d", len(item_reminders))

    # Find user's location-label configs
    user_location_labels = LocationLabel.query.filter_by(user_id=initiator["id"]).all()

    # Determine which location labels are NOT on this task (for deletion)
    task_label_id_strs = [str(lid) for lid in task_label_ids]
    not_used_location_labels = [
        ll for ll in user_location_labels if str(ll.label_id) not in task_label_id_strs
    ]

    # Delete reminders for removed labels
    for reminder in item_reminders:
        for ll in not_used_location_labels:
            if (
                reminder.get("name") == ll.name
                and reminder.get("loc_trigger") == ll.loc_trigger
                and reminder.get("radius") == ll.radius
            ):
                app.logger.info("Deleting reminder %s (label removed)", reminder["id"])
                try:
                    todoist_delete_reminder(token, reminder["id"])
                except Exception as e:
                    app.logger.error(
                        "Failed to delete reminder %s: %s", reminder["id"], e
                    )
                break

    # Add reminders for matching labels
    for label_id in task_label_ids:
        loc_labels = user.location_labels.filter_by(label_id=label_id).all()
        if not loc_labels:
            app.logger.info("No location config for label %s, skip", label_id)
            continue
        for loc_label in loc_labels:
            # Check for existing duplicate
            existing = [
                r
                for r in item_reminders
                if (
                    r.get("name") == loc_label.name
                    and r.get("loc_trigger") == loc_label.loc_trigger
                    and r.get("radius") == loc_label.radius
                )
            ]
            if existing:
                app.logger.info(
                    "Reminder already exists for item %s / location %s",
                    event_data["id"],
                    loc_label.name,
                )
                continue

            app.logger.info(
                "Adding location reminder: item=%s, location=%s, trigger=%s",
                event_data["id"],
                loc_label.name,
                loc_label.loc_trigger,
            )
            try:
                todoist_add_reminder(
                    token,
                    event_data["id"],
                    loc_label.name,
                    loc_label.lat,
                    loc_label.long,
                    loc_label.loc_trigger,
                    loc_label.radius,
                )
            except Exception as e:
                app.logger.error(
                    "Failed to add reminder for item %s: %s", event_data["id"], e
                )

    return "ok"


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "initdb":
        db.create_all()
    else:
        app.run(debug=True, use_reloader=True)
