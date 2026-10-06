# Todoist Location Labels

## Overview
Flask web app that automatically adds **location-based reminders** to Todoist tasks based on label assignments. Users associate Todoist labels with physical locations (address, lat/long, radius, trigger type). When a task with that label is created/updated, a webhook fires and the app adds a location reminder to the task.

Originally by @fangpenlin, then @IcyPalm, now maintained by @dancwilliams.

## Architecture

**Single-file Flask app** (`app.py`) deployed on **Fly.io** (region: `dfw`).

### Data Flow
1. User authenticates via Todoist OAuth (scopes: `data:read_write,data:delete`)
2. User maps Todoist labels to locations via the web UI (Google Places autocomplete for addresses)
3. Todoist sends webhooks on `item:added` / `item:updated` to `/webhook`
4. Webhook handler reconciles the task's labels against configured location-labels:
   - Adds location reminders for newly-matching labels (dedupes against existing reminders)
   - Deletes reminders whose matching label is no longer on the task
5. Editing or deleting a mapping in the UI sweeps Todoist first (`sweep_reminders`): every reminder made from that mapping is deleted, and for an edit re-added on the same task at the new place. If Todoist cannot be reached or refuses a delete, the request answers 502 with a sentence saying to submit the change again, and the mapping is left as it was. If only re-adds were refused, every delete went through, so the mapping is saved at the new place and the 502 says each missing reminder comes back the next time its task changes (the webhook re-creates it there). Kept at the old place, the mapping would no longer match the reminders that did move, and a task change plus a resubmit would leave that task with two reminders.

### Key Components
- **`app.py`** - All application logic (routes, models, webhook handler, Todoist API client)
- **`templates/index.html`** - Single-page UI (Bootstrap 4, Google Maps Places API)

### Database Models (PostgreSQL via SQLAlchemy)
- **`User`** - `id` (BigInteger PK, Todoist user ID), `oauth_token`
- **`LocationLabel`** - `label_id` (BigInteger), `name` (address), `lat`, `long`, `loc_trigger` (`on_enter`/`on_leave`), `radius`

## Todoist API Integration

The app talks to **Todoist API v1** (`https://api.todoist.com/api/v1/`) exclusively via direct HTTP with `requests` — there is no `todoist-python` / `todoist-api-python` SDK in the dependency tree. The official SDK does not expose reminder methods, so reminder add/delete is done through the v1 sync endpoint.

- `GET /api/v1/labels` — list user's labels (returns a paginated `{"results": [...]}` dict)
- `GET /api/v1/user` — user profile (used post-OAuth and for the UI greeting)
- `GET /api/v1/location_reminders` — the user's location reminders, all of them or one task's with `task_id`; paginated like labels
- `POST /api/v1/sync` — commands only:
  - `reminder_add` command (type `location`, args: `item_id`, `name`, `loc_lat`, `loc_long`, `loc_trigger`, `radius`)
  - `reminder_delete` command
- **Todoist Webhooks** — `/webhook` receives `item:added` / `item:updated` events. Every delivery is verified against `X-Todoist-Hmac-SHA256` (base64 HMAC-SHA256 of the raw body, keyed with `TODOIST_CLIENT_SECRET`); anything else gets 401 before the body is parsed (the raw bytes are read to compute the HMAC). Confirmed against a real delivery on 2026-10-04. Tests sign with the `post_webhook` fixture. The user is `event["user_id"]`, which Todoist documents as the user the event is delivered for; `initiator` is not used, because in a shared project it may be a collaborator who is not a user of this app.
- **Google Maps Places API** — address autocomplete in the UI

### Label ID / Name Mapping (webhook behavior)
Webhook `event_data["labels"]` contains label **names** (strings), not IDs. The handler fetches the full label list of the event's user (`event["user_id"]`, not `initiator`) on every webhook, builds a name→id map, and converts before matching against the `LocationLabel.label_id` column. Keep this in mind when editing webhook logic — don't compare `event_data["labels"]` directly to `label_id`.

## Environment Variables
| Variable | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string (falls back to `sqlite:///test.db`) |
| `TODOIST_CLIENT_ID` | OAuth client ID |
| `TODOIST_CLIENT_SECRET` | OAuth client secret |
| `TODOIST_FLASK_SECRET_KEY` | Flask session secret |
| `GOOGLE_MAP_API_KEY` | Google Maps API key |
| `GOOGLE_ANALYTICS_ID` | Optional Google Analytics tracking ID |

## Running Locally
```bash
cp .env.example .env   # then fill in the values
uv sync                # install deps from uv.lock (dev group included)
uv run python app.py          # Dev server on port 5000
uv run python app.py initdb   # Create database tables
make check                    # what CI runs: ruff format --check, ruff check, mypy, pytest
```

## Deployment
- **Platform**: Fly.io (`fly.toml`)
- **Container**: Python 3.13 Alpine + `uv sync --frozen --no-dev`
- **Entrypoint**: `gunicorn --no-control-socket -b 0.0.0.0:5000 app:app`
- `--no-control-socket` is load-bearing. gunicorn 25 starts its control socket in a background thread that logs just as the master forks the first worker; a fork that lands mid-write leaves the worker deadlocked on its first log line, and gunicorn's timeout never fires for a worker that has not sent its first heartbeat. The machine then sits "started" and unreachable until Fly autostops it (outage 2026-10-04, 22:01-22:06 UTC).
- `master` requires the `check` status; no reviewers (solo repo). Merging to master deploys.

## Development Notes
- Formatting + linting via `ruff` (config in `pyproject.toml`; rules E/F/I/N/W/UP, line length 100, double quotes); `mypy app.py` non-strict with `warn_return_any` and `check_untyped_defs`
- Tests in `tests/` (pytest, coverage printed). `tests/conftest.py` sets dummy env vars before importing `app`, because the module reads its secrets at import time. The webhook reconciliation is the path that must stay covered. `fake_todoist` is a local HTTP server standing in for Todoist, for tests of what is sent over the wire.
- The webhook fails closed: if the labels or reminders fetch raises, or Todoist refuses or fails an add or delete (an HTTP error, or a per-command error inside a 200 answer; both tested), it answers 503 and Todoist redelivers (15 min, up to 3 times). An empty labels list is never inferred from an API failure. Reconciliation is safe to repeat.
- A mapping and its reminders are linked only by value: `reminder_is_at` compares name, trigger and radius. The app stores no reminder ids. Three consequences: anything that changes a mapping must go through `sweep_reminders` or its reminders are stranded; a reminder the user made by hand with identical values is treated as the app's; and two mappings of one user may not share name, trigger and radius (`match_key`), or the webhook would delete for one what it added for the other, so the form refuses the second with a 400.
- Todoist does not store a location reminder as sent (read back from the live API 2026-10-05): a radius over 255 is stored as 255, the name is stripped, and the sync answer is `ok` either way; a fractional radius or a name over 255 characters is refused per command (`sync_status` carries the error, HTTP is still 200). So the form accepts only a whole radius from 1 to 255 and a stripped name of at most 255 characters, and `place_of` normalises rows saved before that. Coordinates must be finite and in range (`-90..90`, `-180..180`), because Todoist stores `nan` and `95.0` as sent (measured 2026-10-05). Anything that compares or sends a mapping goes through `place_of`. `fake_todoist` echoes what a test configures, so it cannot show this; a change to what is sent needs a read-back from the real API.
- `sweep_reminders` logs `sweep matched N of M location reminders`. Zero of many on an edit or delete of a mapping that has tagged tasks means matching is broken again.
- All outbound Todoist calls go through one `requests.Session` (`todoist_http`) with urllib3 `Retry`: a 429 or 5xx answer is retried three times after waits of 0, 2 and 4 s (measured against a stub: 4 attempts, 6.0 s), GET and POST alike; a 401 is not retried. POST retry is safe because every sync command carries a `uuid` Todoist dedupes on. A stall is not retried, connecting or reading (`connect=0`, `read=0`; both tested): each call fails after `TODOIST_TIMEOUT` (10 s), because gunicorn runs one sync worker with a 30 s timeout. The bound is per call, not per request. Slow error answers are still retried, so four 5xx answers that each take close to 10 s could exceed it; that has not been seen.
- `todoist_run_commands` sends every batch, then raises `TodoistRefused` (a `RequestException` carrying uuid → status) when `sync_status` holds anything but `ok` for a command it sent, or nothing at all (the refusal is tested through `fake_todoist`; the missing status is not). Todoist applies each command on its own: a refused one does not undo the others in its request. There is no list of which refusals are permanent: every one is a 503 from the webhook and a 502 from a route.
- The sync `reminder_update` command does not work for location reminders: it answers `Reminder not found` (tried against the live API 2026-10-05). An edit therefore deletes and re-adds, each pair in the same request, 100 commands per request (`SYNC_BATCH`); an edit of more than 50 tagged tasks is several requests.
- There is no alerting. A failure shows only in Fly's log, which keeps about 100 lines.
- The app never requests a full sync. Reminders are read through REST (`GET /api/v1/location_reminders`, by `task_id` in the webhook, all of them in a sweep); the sync endpoint is used for commands only. A full sync is limited to 100 per user per 15 minutes, and the webhook used to make one on every task change.
- The mapped address is not logged; webhook lines carry the mapping's row id.
- `todoist_get_all` follows `next_cursor` until it is empty, for labels and reminders alike; reading only the first page would make later labels look removed and later reminders look absent.
- Deleting a mapping is `POST /delete_label_location/<row id>` (the `LocationLabel.id`, not the Todoist label id). GET is refused so a cross-site link cannot delete.
- No tracing. The OpenTelemetry stack was removed 2026-10-04: it had recorded nothing since the March migration (no configurator was installed, so every span was a no-op) and nobody missed it.
- `log_request` skips requests without `Fly-Client-IP`, which is how Fly's 15-second health check stays out of the log.
- Sessions are Flask's signed cookie (`Secure`, `SameSite=Lax`, 30 days), signed with `TODOIST_FLASK_SECRET_KEY`. It holds `user_id` and the OAuth state only; the Todoist token stays in Postgres. Rotating the secret logs everyone out, which is the only revocation path. Flask-Session was removed 2026-10-05: its filesystem store was wiped on every deploy.
- `LocationLabel` is unique on `(user_id, label_id)`. Submitting an already-mapped label edits it. There is no migration tool: the production constraint was added by hand (`ALTER TABLE location_label ADD CONSTRAINT uq_location_label_user_label UNIQUE (user_id, label_id)`), and `initdb` creates it on a fresh database.
- Production data is database `todoist_location_labels` on Fly app `dcw-postgres-dev`. A second database there, `dcw_todoist_location`, is a stale copy with no writes since at least 2025-02; the app does not use it.
- SQLAlchemy engine: `pool_pre_ping` on, `pool_recycle` 299 s, set through `SQLALCHEMY_ENGINE_OPTIONS` (Flask-SQLAlchemy 3 ignores the old `SQLALCHEMY_POOL_*` keys)
- Dependencies managed by `uv` (`pyproject.toml` + `uv.lock`); there is no `requirements.txt`
