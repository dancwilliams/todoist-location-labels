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
- **Todoist Webhooks** — `/webhook` receives `item:added` / `item:updated` events
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
uv sync                # install deps from uv.lock
# Set environment variables above
uv run python app.py          # Dev server on port 5000
uv run python app.py initdb   # Create database tables
```

## Deployment
- **Platform**: Fly.io (`fly.toml`)
- **Container**: Python 3.13 Alpine + `uv sync --frozen --no-dev`
- **Entrypoint**: `opentelemetry-instrument gunicorn -b 0.0.0.0:5000 app:app`
- Instrumented with OpenTelemetry, exporting to Honeycomb

## Development Notes
- Formatting + linting via `ruff` (config in `pyproject.toml`; rules E/F/I/W, line length 88, double quotes)
- Tenacity retry wrapper on Todoist GETs (3 attempts, 2s wait) — note: sync POSTs are not wrapped
- OpenTelemetry tracing via Honeycomb on all routes (`@tracer.start_as_current_span`)
- Flask-Session with filesystem backend for session persistence
- SQLAlchemy pool configured with `pre_ping`, size 10, recycle 299s
- Dependencies managed by `uv` (`pyproject.toml` + `uv.lock`); there is no `requirements.txt`
