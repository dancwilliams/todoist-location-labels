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
- `POST /api/v1/sync` — used for:
  - Fetching reminders (`resource_types=["reminders", "reminders_location"]`)
  - `reminder_add` command (type `location`, args: `item_id`, `name`, `loc_lat`, `loc_long`, `loc_trigger`, `radius`)
  - `reminder_delete` command
- **Todoist Webhooks** — `/webhook` receives `item:added` / `item:updated` events. Every delivery is verified against `X-Todoist-Hmac-SHA256` (base64 HMAC-SHA256 of the raw body, keyed with `TODOIST_CLIENT_SECRET`); anything else gets 401 before the body is read. Confirmed against a real delivery on 2026-10-04. Tests sign with the `post_webhook` fixture.
- **Google Maps Places API** — address autocomplete in the UI

### Label ID / Name Mapping (webhook behavior)
Webhook `event_data["labels"]` contains label **names** (strings), not IDs. The handler fetches the user's full label list on every webhook, builds a name→id map, and converts before matching against the `LocationLabel.label_id` column. Keep this in mind when editing webhook logic — don't compare `event_data["labels"]` directly to `label_id`.

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
- Formatting + linting via `ruff` (config in `pyproject.toml`; rules E/F/I/N/W/UP, line length 100, double quotes); `mypy app.py` non-strict with `warn_return_any`
- Tests in `tests/` (pytest, coverage printed). `tests/conftest.py` sets dummy env vars before importing `app`, because the module reads its secrets at import time. The webhook reconciliation is the path that must stay covered.
- The webhook fails closed: if the labels or reminders fetch raises, it answers 503 and Todoist redelivers (15 min, up to 3 times). An empty labels list is never inferred from an API failure.
- Tenacity retry wrapper on Todoist GETs (3 attempts, 2s wait) — note: sync POSTs are not wrapped
- No tracing. The OpenTelemetry stack was removed 2026-10-04: it had recorded nothing since the March migration (no configurator was installed, so every span was a no-op) and nobody missed it.
- `log_request` skips requests without `Fly-Client-IP`, which is how Fly's 15-second health check stays out of the log.
- Flask-Session with filesystem backend for session persistence
- SQLAlchemy engine: `pool_pre_ping` on, `pool_recycle` 299 s, set through `SQLALCHEMY_ENGINE_OPTIONS` (Flask-SQLAlchemy 3 ignores the old `SQLALCHEMY_POOL_*` keys)
- Dependencies managed by `uv` (`pyproject.toml` + `uv.lock`); there is no `requirements.txt`
