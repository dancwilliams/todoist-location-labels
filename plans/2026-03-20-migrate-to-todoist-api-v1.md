# Migrate to Todoist API v1 — Implementation Plan

## Overview

The Todoist Sync API v9 and REST API v2 were shut down on February 10, 2026. The app is completely broken. We need to migrate all API calls to the new unified Todoist API v1 (`https://api.todoist.com/api/v1/`), remove the vendored `todoist-python` library, and replace it with direct HTTP calls using `requests`.

Location reminders are confirmed still supported in API v1 (Pro/Business plans). The reminder creation still uses a sync-style command pattern via `POST /api/v1/sync`.

## Current State Analysis

Every Todoist API call in `app.py` is broken:

| What | Old Endpoint (DEAD) | New Endpoint |
|---|---|---|
| Full sync | `POST /sync/v9/sync` | `POST /api/v1/sync` |
| Labels list (UI) | `GET /rest/v2/labels` | `GET /api/v1/labels` |
| User info | Via sync `resource_types=["user"]` | `GET /api/v1/user` |
| Create reminder | `reminder_add` via `/sync/v9/sync` | `reminder_add` via `/api/v1/sync` |
| Delete reminder | `reminder_delete` via `/sync/v9/sync` | `reminder_delete` via `/api/v1/sync` |
| OAuth authorize | `todoist.com/oauth/authorize` | `app.todoist.com/oauth/authorize` |
| OAuth token | `todoist.com/oauth/access_token` | `api.todoist.com/oauth/access_token` |

### Key API v1 Changes
- **Resource IDs are now strings**, not integers
- **Task labels are name strings**, not integer IDs (in both API responses and webhooks)
- **Sync endpoint moved** from `/sync/v9/sync` to `/api/v1/sync` (lowercase)
- **OAuth authorize URL** changed to `app.todoist.com`

## Desired End State

- App uses only Todoist API v1 endpoints
- Vendored `todoist-python/` directory is removed (but kept in git history)
- All API calls are direct HTTP via `requests` with proper error handling/logging
- Webhook handler correctly creates location reminders
- OAuth flow works with updated URLs

### How to Verify
1. OAuth login succeeds, user name displays
2. Labels load in the UI dropdown
3. Creating a label-location mapping works
4. Adding a label to a task in Todoist triggers the webhook
5. Webhook creates a location reminder visible in Todoist mobile app

## What We're NOT Doing

- Migrating to the `todoist-api-python` SDK (it has no reminder methods)
- Changing the database schema (label_id column stays BigInteger)
- Changing the UI/template
- Adding webhook signature verification (future improvement)
- Adding unit tests (future improvement)

## Implementation Approach

Replace all Todoist API interactions with a small set of helper functions that make direct HTTP calls. The sync-based command pattern for reminders is preserved (it's how API v1 works), but implemented with plain `requests` instead of the vendored library.

---

## Phase 1: Create API v1 Helper Functions

### Overview
Add helper functions for all Todoist API v1 calls, replacing the vendored library.

### Changes Required:

#### 1. New helper functions in `app.py`

Add these functions after the existing `get_todoist_labels` function (around line 122):

```python
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
    response.raise_for_status()
    return response.json()

def todoist_get_labels(token):
    """Fetch all labels for a user."""
    try:
        return todoist_api_get("labels", token)
    except requests.exceptions.RequestException as e:
        app.logger.error(f"Failed to fetch labels: {e}")
        return []

def todoist_get_user(token):
    """Fetch user profile info."""
    try:
        return todoist_api_get("user", token)
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
    import uuid
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
    app.logger.info("reminder_add sync result: %s", result.get("sync_status", {}))
    return result

def todoist_delete_reminder(token, reminder_id):
    """Delete a reminder via sync command."""
    import uuid
    cmd = {
        "type": "reminder_delete",
        "uuid": str(uuid.uuid4()),
        "args": {"id": str(reminder_id)},
    }
    result = todoist_sync(token, commands=[cmd])
    app.logger.info("reminder_delete sync result: %s", result.get("sync_status", {}))
    return result
```

### Success Criteria:

#### Automated Verification:
- [ ] `python -c "import app"` succeeds (no import errors)
- [ ] No syntax errors: `python -m py_compile app.py`

#### Manual Verification:
- [ ] N/A — functions not yet wired up

---

## Phase 2: Migrate OAuth Flow

### Overview
Update the OAuth authorize and token exchange URLs.

### Changes Required:

#### 1. Update authorize URL
**File:** `app.py`, line 156-163

Change `"https://todoist.com/oauth/authorize?"` to `"https://app.todoist.com/oauth/authorize?"`.

#### 2. Update token exchange + replace Sync API user lookup
**File:** `app.py`, lines 178-210

Replace the oauth_redirect function body:
- Change token exchange URL from `"https://todoist.com/oauth/access_token"` to `"https://api.todoist.com/oauth/access_token"`
- Replace `todoist.TodoistAPI(access_token)` + `api.sync()` + `api.user.get_id()` with `todoist_get_user(access_token)` to get user ID and info
- User ID comes from `user_info["id"]` instead of `api.user.get_id()`

```python
@app.route("/oauth/redirect")
@tracer.start_as_current_span("oauth_redirect")
def oauth_redirect():
    log_request('/oauth/redirect')
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
```

### Success Criteria:

#### Automated Verification:
- [ ] `python -m py_compile app.py` passes

#### Manual Verification:
- [ ] OAuth login flow works end-to-end
- [ ] User name displays correctly after login
- [ ] User record is created/updated in the database

**Pause for manual testing before proceeding.**

---

## Phase 3: Migrate Index Route

### Overview
Replace the REST v2 labels call and Sync API user info with API v1 equivalents.

### Changes Required:

#### 1. Update index route
**File:** `app.py`, lines 124-147

Replace the `todoist.TodoistAPI` + `api.sync()` + `api.user.get()` pattern and the old `get_todoist_labels()` call with the new helper functions:

```python
@app.route("/")
@tracer.start_as_current_span("index")
def index():
    log_request('/')
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
```

#### 2. Remove old `get_todoist_labels` function
Delete the old `get_todoist_labels` function (lines 109-122) since it called the dead REST v2 endpoint. The new `todoist_get_labels` replaces it.

#### 3. Remove old `resilient_api_call` function
Delete `resilient_api_call` (lines 97-107) — replaced by `todoist_api_get` which has the same retry logic.

### Success Criteria:

#### Automated Verification:
- [ ] `python -m py_compile app.py` passes

#### Manual Verification:
- [ ] Index page loads with label list populated
- [ ] User name shows in the header
- [ ] Existing label-location mappings display correctly
- [ ] Creating a new label-location mapping works

**Pause for manual testing before proceeding.**

---

## Phase 4: Migrate Webhook Handler

### Overview
This is the critical phase — replace the entire webhook handler's Todoist API interactions with API v1 calls. This is where reminders get created.

### Changes Required:

#### 1. Rewrite the webhook handler
**File:** `app.py`, lines 262-357

The new webhook handler:
- Uses `todoist_get_labels()` instead of `api.labels.all()` for name→ID mapping
- Uses `todoist_get_reminders()` instead of `api.reminders.all()` for existing reminders
- Uses `todoist_add_reminder()` instead of `api.reminders.add()` + `api.commit()`
- Uses `todoist_delete_reminder()` instead of `api.reminders.delete()` + `api.commit()`
- Each reminder add/delete is committed immediately (no batching needed)

```python
@app.route("/webhook", methods=["POST"])
@tracer.start_as_current_span("webhook")
def webhook():
    log_request('/webhook')
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
        r for r in all_reminders
        if r.get("type") == "location" and str(r.get("item_id")) == str(event_data["id"])
    ]
    app.logger.info("Existing location reminders for item: %d", len(item_reminders))

    # Find user's location-label configs
    user_location_labels = LocationLabel.query.filter_by(user_id=initiator["id"]).all()

    # Determine which location labels are NOT on this task (for deletion)
    not_used_location_labels = [
        ll for ll in user_location_labels
        if str(ll.label_id) not in [str(lid) for lid in task_label_ids]
    ]

    # Delete reminders for removed labels
    for reminder in item_reminders:
        for ll in not_used_location_labels:
            if (reminder.get("name") == ll.name
                    and reminder.get("loc_trigger") == ll.loc_trigger
                    and reminder.get("radius") == ll.radius):
                app.logger.info("Deleting reminder %s (label removed)", reminder["id"])
                try:
                    todoist_delete_reminder(token, reminder["id"])
                except Exception as e:
                    app.logger.error("Failed to delete reminder %s: %s", reminder["id"], e)
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
                r for r in item_reminders
                if (r.get("name") == loc_label.name
                    and r.get("loc_trigger") == loc_label.loc_trigger
                    and r.get("radius") == loc_label.radius)
            ]
            if existing:
                app.logger.info(
                    "Reminder already exists for item %s / location %s",
                    event_data["id"], loc_label.name,
                )
                continue

            app.logger.info(
                "Adding location reminder: item=%s, location=%s, trigger=%s",
                event_data["id"], loc_label.name, loc_label.loc_trigger,
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
```

#### Key differences from old handler:
1. **No more `api.sync()` full sync** — uses targeted `GET /api/v1/labels` and sync with `resource_types=["reminders"]`
2. **No more batched `api.commit()`** — each reminder add/delete is its own sync call with immediate result logging
3. **ID comparisons use `str()`** — API v1 IDs are strings
4. **Better error handling** — each API call is wrapped in try/except with logging
5. **Label matching simplified** — clear name→ID mapping via `todoist_get_labels()`

### Success Criteria:

#### Automated Verification:
- [ ] `python -m py_compile app.py` passes

#### Manual Verification:
- [ ] Create a task in Todoist with a label that has a location mapping
- [ ] Check app logs — webhook received, label matched, reminder_add called
- [ ] Check Todoist mobile app — location reminder appears on the task
- [ ] Update a task to remove the label — reminder gets deleted
- [ ] Check logs for any sync_status errors

**Pause for manual testing before proceeding.**

---

## Phase 5: Cleanup

### Overview
Remove the vendored `todoist-python` library and clean up unused imports/code.

### Changes Required:

#### 1. Remove vendored library reference
**File:** `app.py`

Remove these lines:
```python
sys.path.append("todoist-python")
import todoist
```

#### 2. Remove unused imports
Remove `import sys` if no longer used (check `sys.argv` usage at bottom — still needed for `initdb`).

#### 3. Add `uuid` import at top of file
Move `import uuid` to the top-level imports since the helper functions use it.

#### 4. Remove `todoist-python/` from tracking (optional)
The directory can be deleted or left in place. If deleted:
```bash
git rm -r todoist-python/
```
This preserves it in git history but removes it from the working tree.

#### 5. Clean up requirements.txt
No changes needed — `requests` is already a dependency, and the vendored library was never in requirements.txt.

### Success Criteria:

#### Automated Verification:
- [ ] `python -m py_compile app.py` passes
- [ ] `grep -r "todoist-python\|import todoist\|from todoist" app.py` returns nothing
- [ ] App starts without import errors

#### Manual Verification:
- [ ] Full end-to-end test: login, view labels, add location, trigger webhook, verify reminder

---

## Testing Strategy

### Manual Testing Steps:
1. Deploy to Fly.io (or run locally with ngrok for webhook testing)
2. Log in via OAuth — verify redirect and user creation
3. View index page — verify labels load and existing mappings show
4. Add a new label-location mapping
5. In Todoist, create a task and apply the mapped label
6. Check app logs for webhook processing and API responses
7. Check Todoist mobile app for the location reminder
8. Remove the label from the task and verify reminder deletion

### What to Watch in Logs:
- `reminder_add sync result: {'<uuid>': 'ok'}` — success
- `reminder_add sync result: {'<uuid>': {'error_code': ..., 'error': ...}}` — API rejected it
- `Label 'X' not found in user's labels` — label matching issue
- `No location config for label X, skip` — no LocationLabel record for this label

## Migration Notes

- **No database migration needed** — the schema is unchanged. `label_id` is stored as BigInteger which can hold both old integer IDs and new string IDs (when cast to int).
- **API v1 IDs are strings** — but they're numeric strings, so `int()` conversion still works. The `label_id` in `LocationLabel` was always stored via `int(request.form["label_id"])`. As long as the API v1 label `id` field is a numeric string, this continues to work.
- **Existing LocationLabel records** should still work if the user's label IDs haven't changed between API versions. If they have, users may need to re-create their mappings.

## References

- [Todoist API v1 Documentation](https://developer.todoist.com/api/v1/)
- [Todoist Sync API v9 Reference (legacy)](https://developer.todoist.com/sync/v9/)
- [todoist-api-python SDK](https://github.com/Doist/todoist-api-python)
- [Location Reminders Help Article (March 2026)](https://www.todoist.com/help/articles/use-location-reminders-in-todoist-uGcwH2AJ6)
