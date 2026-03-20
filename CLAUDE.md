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
4. Webhook handler checks if the task's labels match any configured location-labels
5. If matched, adds a location reminder via the Todoist API v1

### Key Components
- **`app.py`** - All application logic (routes, models, webhook handler)
- **`templates/index.html`** - Single-page UI (Bootstrap 4, Google Maps Places API)

### Database Models (PostgreSQL via SQLAlchemy)
- **`User`** - `id` (BigInteger PK, Todoist user ID), `oauth_token`
- **`LocationLabel`** - `label_id`, `name` (address), `lat`, `long`, `loc_trigger` (`on_enter`/`on_leave`), `radius`

### APIs Used
- **Todoist API v1** - Labels, reminders, user info (`https://api.todoist.com/api/v1/`)
- **Todoist Webhooks** - Receives `item:added`/`item:updated` events
- **Google Maps Places API** - Address autocomplete in the UI

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
pip install -r requirements.txt
# Set environment variables above
python app.py          # Dev server on port 5000
python app.py initdb   # Create database tables
```

## Deployment
- **Platform**: Fly.io (`fly.toml`)
- **Container**: Python 3.11 Alpine + gunicorn, instrumented with OpenTelemetry/Honeycomb
- **Entrypoint**: `opentelemetry-instrument gunicorn -b [::]:5000 app:app`

## Critical: API Migration Required

**The Todoist Sync API v9 and REST API v2 were shut down on February 10, 2026.**

The app previously used:
- `todoist-python/` - Vendored fork of the archived `todoist-python` Sync API client (Sync API v9) - **DEAD**
- REST API v2 (`/rest/v2/labels`) for fetching labels in the UI - **DEAD**

Must migrate to:
- **Todoist API v1** (`https://api.todoist.com/api/v1/`) - the new unified API
- Direct HTTP calls for reminders (`POST /api/v1/reminders`) - the official `todoist-api-python` SDK v3.2.1 does not expose reminder methods
- Labels API v1 (`GET /api/v1/labels`) for the UI
- Location reminders are still supported in API v1 with params: `item_id`, `type="location"`, `loc_lat`, `loc_long`, `loc_trigger`, `radius`, `name`

## Known Issues

### App Fully Broken Since Feb 10, 2026
The vendored `todoist-python` library calls Sync API v9 endpoints that no longer exist. Every webhook fails silently. The UI label fetch also fails (REST API v2 shutdown).

### DB Schema Note
`LocationLabel.label_id` stores Todoist label IDs. In the new API v1, labels still have string IDs. The webhook `event_data["labels"]` contains label **names** (strings), not IDs. Label matching must compare names, not IDs.

## Development Notes
- Code formatted with `black`
- Tenacity retry wrapper on API calls (3 attempts, 2s wait)
- OpenTelemetry tracing via Honeycomb on all routes
- Flask-Session with filesystem backend for session persistence
- SQLAlchemy pool configured with `pre_ping`, size 10, recycle 299s
