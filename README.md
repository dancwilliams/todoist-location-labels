# Todoist Location Labels

Automatically add location-based reminders to Todoist tasks by assigning labels. Map a Todoist label to a physical location, and any task with that label gets a location reminder on your phone.

## How It Works

1. Log in with your Todoist account (Pro or Business plan required for location reminders)
2. Map labels to locations using Google Places autocomplete
3. When you add a mapped label to a task, the webhook fires and creates a location reminder
4. Get reminded when you arrive at or leave the location

## Stack

- **Python 3.13** / Flask / Gunicorn
- **Fly.io** for hosting (region: `dfw`)
- **PostgreSQL** via SQLAlchemy
- **Todoist API v1** for labels, reminders, and webhooks
- **Google Maps Places API** for address autocomplete
- **OpenTelemetry** for tracing
- **uv** for dependency management
- **ruff** for linting/formatting

## Development

### Prerequisites

- [uv](https://docs.astral.sh/uv/) installed
- Environment variables set (see below)

### Setup

```bash
uv sync
uv run python app.py initdb   # Create database tables
uv run python app.py           # Dev server on port 5000
```

### Linting

```bash
uv run ruff format app.py
uv run ruff check app.py
```

### Environment Variables

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string (falls back to `sqlite:///test.db`) |
| `TODOIST_CLIENT_ID` | OAuth client ID |
| `TODOIST_CLIENT_SECRET` | OAuth client secret |
| `TODOIST_FLASK_SECRET_KEY` | Flask session secret |
| `GOOGLE_MAP_API_KEY` | Google Maps API key |
| `GOOGLE_ANALYTICS_ID` | Optional Google Analytics tracking ID |

## Deployment

Deploys automatically to Fly.io via GitHub Actions on push to `master`.

Manual deploy:
```bash
fly deploy
```

## CI/CD

- **CI**: Ruff lint/format + compile check on every push and PR
- **Deploy**: Auto-deploy to Fly.io on push to master
- **Dependabot**: Weekly dependency updates for Python, GitHub Actions, and Docker

## History

Originally by [@fangpenlin](https://github.com/fangpenlin/todoist-location-labels/), then [@IcyPalm](https://github.com/IcyPalm/todoist-location-labels), now maintained by [@dancwilliams](https://github.com/dancwilliams/todoist-location-labels).
