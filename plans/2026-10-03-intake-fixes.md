# Intake fixes: clean and stable — Implementation Plan

Source: `plans/intake-2026-10-02.md` (the intake report; rule IDs and bug IDs below refer to it).
Planned at commit 27e2c80, 2026-10-03. Each phase is one PR to `master`, merged only on green CI. Deploy is automatic on merge (`.github/workflows/ci.yml` deploy job), so every phase ends with a production check.

## Overview

The app works and Dan uses it daily. The intake found no crash in production, but: a webhook that trusts anyone (B1), a webhook that deletes reminders when Todoist's API hiccups (B12), six months of a tracing stack that records nothing (B0), zero tests, no branch protection, and a handful of small 500s. The goal of this plan is a repo that is in the fold (zero tier 1 and tier 2 FAIL) and a service that does nothing surprising to Dan's reminders.

## Decisions (grill-me, 2026-10-04)

| Decision | Resolution |
|---|---|
| Ordering | B12 (fail closed on Todoist API errors) ships in Phase 1 with its failing-first test; Phase 2 is signature only |
| Tracing | Removed, not repaired. Nothing recorded since 2026-03-20 and nobody noticed |
| Signature rollout | Two PRs; 2b enforces as soon as one real delivery logs a match, same day |
| Sessions | Signed cookie, 30 days, `Secure` + `Lax`; Flask-Session removed |
| Duplicate submit | Update in place (doubles as edit) |
| Production SQL and ID check | Agent runs them under a permission rule Dan adds for `fly ssh console` / `fly postgres connect` on this repo; read first, show counts, write on Dan's go |
| Delete route | POST keyed by row id; old GET returns 405 (only caller is the template) |
| Retries | urllib3 `Retry` on one shared session: 429/5xx only, GET and POST, 3 tries, 1-2-4 s |
| Health-check grace | 15 s |
| CI matrix | 3.13 and 3.14; drop 3.14 with a CLAUDE.md note if the lock will not install |
| Pool | `pool_pre_ping` + `pool_recycle` 299, `pool_size` dropped |
| Request logging | Kept only when `Fly-Client-IP` is present, so health checks are silent |

Verified during the interview: Todoist re-delivers any non-200 webhook response after 15 minutes, up to three times, same `X-Todoist-Delivery-ID`. Database host: Fly app `dcw-postgres-dev`, candidate databases `todoist_location_labels` and `dcw_todoist_location`; which is live is read in Phase 5.

## Current State Analysis

- `app.py` (494 lines) is the whole app; `templates/index.html` the whole UI. Read both before touching either.
- Production: Fly app `todoist-location-labels`, one machine, autostop/autostart, release v69 at planning time. `fly secrets list` shows 7 names: `DATABASE_URL`, `GOOGLE_MAP_API_KEY`, `HONEYCOMB_API_KEY`, `OTEL_SERVICE_NAME`, `TODOIST_CLIENT_ID`, `TODOIST_CLIENT_SECRET`, `TODOIST_FLASK_SECRET_KEY`.
- CI: `lint` job (ruff format check, ruff check, py_compile) then `deploy` on push to master. No tests, no mypy, no branch protection.
- Dev group: `ruff` only. Lock: 54 packages.
- `fly logs --no-tail` is a 100-line buffer, two thirds of it the app logging Fly's own health check.

## Desired End State

- `make check` runs format check, lint, mypy and pytest with coverage printed; CI runs the same on a clean runner; master requires it.
- `/webhook` rejects deliveries that Todoist did not sign and answers non-2xx when Todoist's API fails, so nothing is deleted on a hiccup.
- No OpenTelemetry, no Flask-Session, no Flask-Limiter, no tenacity, no devcontainer. About 130 fewer lines and 17 fewer direct dependencies.
- Duplicate mappings impossible; delete is a POST; no known 500 paths.
- README, CLAUDE.md and `.env.example` describe what actually runs.
- A rerun of `/repo-intake` reports in the fold.

### How to Verify (end to end, after every phase that deploys)
1. Open https://todoist-location-labels.fly.dev/, log in, the label list loads with your name in the header.
2. Add or confirm a mapping for one label.
3. In Todoist, add that label to a task: the location reminder appears on the task in the mobile app within a few seconds.
4. Remove the label from the task: the reminder disappears.
5. `fly logs -a todoist-location-labels --no-tail` shows the webhook lines for steps 3 and 4 and no traceback.

## What We're NOT Doing

- Changing endpoint paths other than the delete route (2.21). `/`, `/authorize`, `/oauth/redirect`, `/logout`, `/create_label_location`, `/webhook` keep their paths and methods.
- Migrating to the official Todoist SDK (no reminder support; recorded in the 2026-03-20 plan).
- Rewriting the template or changing the look. Bootstrap stays.
- Adding a rate limiter, CSRF framework, or auth beyond what the phases name.
- Rewriting git history for the five 2026-03-20 `Co-Authored-By` trailers (2.15 is forward-only).

## Rules for every phase

- One PR per phase, from a branch off current `master`; no direct pushes to master.
- No attribution trailers in commits or PR bodies.
- Secrets: the agent never reads, prints or types a secret value. Steps that need one are handed to Dan as a script that reads the value from where it already lives.
- Production containers are not restarted by hand; the deploy job does it on merge.
- Every behaviour change in `app.py` lands with a test that fails on the code before the change. Record the failing run in the PR body (2.26).
- Run `make check` locally before pushing (`uv run --no-sync ...` inside it, so it never resyncs the venv).

---

## Phase 1: Verification baseline, secrets hygiene, fail-closed webhook, branch protection

Clears 1.2, 2.3, 2.5, 2.6, 2.7, 2.8, 2.9, 2.17 and fixes B12. Everything later depends on this.

### Changes Required

#### 1. `.env.example` (new) and `.gitignore`

```
# Copy to .env for a local run. Production values live in Fly secrets (`fly secrets list`).
# DATABASE_URL may be omitted locally; the app falls back to sqlite:///test.db
DATABASE_URL=postgresql://user:password@host:5432/dbname
TODOIST_CLIENT_ID=
TODOIST_CLIENT_SECRET=
TODOIST_FLASK_SECRET_KEY=        # openssl rand -base64 32
GOOGLE_MAP_API_KEY=
GOOGLE_ANALYTICS_ID=             # optional
```

`.gitignore:98`: replace `.env` with:
```
.env*
!.env.example
```

#### 2. `pyproject.toml`

```toml
[dependency-groups]
dev = ["ruff", "mypy", "pytest", "pytest-cov", "types-requests"]

[tool.ruff]
line-length = 100
target-version = "py313"

[tool.ruff.lint]
select = ["E", "F", "I", "N", "W", "UP"]

[tool.mypy]
python_version = "3.13"
warn_return_any = true
ignore_missing_imports = true   # flask_session, flask_sqlalchemy legacy API

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "--cov=app --cov-report=term-missing"
```

Run `uv lock` and commit `uv.lock`. Run `uv run --no-sync ruff format app.py` once (line length changed) and fix whatever `UP`/`N` report; expect `dict(...)` calls and f-string logging to be untouched by these rules.

#### 3. `tests/conftest.py` (new)

The app reads its secrets at import time (`app.py:51-55`), so the environment must be set before `import app`:

```python
import os

os.environ.setdefault("DATABASE_URL", "sqlite://")
for k in ("TODOIST_FLASK_SECRET_KEY", "TODOIST_CLIENT_ID", "TODOIST_CLIENT_SECRET", "GOOGLE_MAP_API_KEY"):
    os.environ.setdefault(k, "test")

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
        app_module.LocationLabel(user=u, label_id=10, name="Home", lat=1.0, long=2.0,
                                 loc_trigger="on_enter", radius=100.0)
    )
    app_module.db.session.commit()
    return u
```

Flask-SQLAlchemy 3.x gives an in-memory SQLite URL a static pool, so one database is shared across the test.

#### 4. `tests/test_webhook.py` (new): the reconciliation, which is the only non-trivial logic in the repo

Three tests, all through `client.post("/webhook", json=...)`, with `monkeypatch.setattr(app_module, "todoist_get_labels", lambda token: [{"id": "10", "name": "Home"}])` and the same for `todoist_get_reminders`, `todoist_add_reminder`, `todoist_delete_reminder` recording their calls into a list:

- label on task, no existing reminder → `todoist_add_reminder` called once with `(tok, item_id, "Home", 1.0, 2.0, "on_enter", 100.0)`, delete not called.
- label on task, matching reminder exists (`{"id": "r1", "type": "location", "item_id": item_id, "name": "Home", "loc_trigger": "on_enter", "radius": 100.0}`) → neither add nor delete called.
- label not on task, matching reminder exists → `todoist_delete_reminder` called once with `(tok, "r1")`.

These pass on today's code; they are the safety net for Phases 2 to 5.

#### 4b. Fail closed on Todoist API errors (B12)

Move the tolerance out of the helpers and into the one caller that wants it:
- `todoist_get_labels`, `todoist_get_user`, `todoist_get_reminders`: remove the `try/except RequestException` and let it raise.
- `index()`: wrap the two calls; on `RequestException` log and render with `labels=[]` and `user_full_name=""` (the page shows an empty list instead of a 500, as today).
- `oauth_redirect()`: wrap `todoist_get_user`; on failure `abort(502)`.
- `webhook()`: wrap the body in `try/except requests.exceptions.RequestException as e:` → `app.logger.error(...)`; `return "todoist api error", 503`. Todoist re-delivers any non-200 after 15 minutes, up to three times.

Test, written and run before the change: `test_api_failure_does_not_delete`: `todoist_get_labels` raises `requests.exceptions.ConnectionError`; a matching reminder exists → expect status 503 and `todoist_delete_reminder` not called. On today's code it fails: status 200, delete called once. Paste that failing output in the PR body.

#### 5. `Makefile` (new)

```make
.PHONY: check
check:
	uv run --no-sync ruff format --check .
	uv run --no-sync ruff check .
	uv run --no-sync mypy app.py
	uv run --no-sync pytest
```

Document in README (replace the Linting section) and CLAUDE.md: `uv sync` then `make check`.

#### 6. `.github/workflows/ci.yml`

Rename job `lint` to `check`, matrix `python-version: ["3.13", "3.14"]`, `uv python install ${{ matrix.python-version }}` then `uv sync --frozen` and `make check`. `deploy` gets `needs: [check]`. If 3.14 fails to install the lock (psycopg2-binary or greenlet wheels), drop it to `["3.13"]` and write one line in CLAUDE.md saying why (2.8 allows "newest on which the locked dependencies install").

#### 7. Branch protection (after the PR merges, so the check name exists)

Run from this repo with the owner's `gh`:
```
gh api -X PUT repos/dancwilliams/todoist-location-labels/branches/master/protection \
  --input - <<'JSON'
{"required_status_checks":{"strict":true,"contexts":["check (3.13)"]},
 "enforce_admins":false,"required_pull_request_reviews":null,"restrictions":null,
 "allow_force_pushes":false,"allow_deletions":false}
JSON
```
Then `gh api repos/dancwilliams/todoist-location-labels/branches/master/protection --jq '.required_status_checks.contexts'` prints `["check (3.13)"]`. Add a line to CLAUDE.md under Deployment: "master requires the `check` status; no reviewers (solo repo)".

### Success Criteria

#### Automated Verification
- [ ] `make check` exits 0 locally and prints a coverage table with `app.py` on it
- [ ] CI run on the PR: `check (3.13)` green; `check (3.14)` green or removed with the CLAUDE.md note
- [ ] `git ls-files .env.example` lists the file; `git check-ignore .env .env.local` lists both
- [ ] After merge: `gh api .../branches/master/protection` returns 200 with the context above
- [ ] Deploy job green; `curl -s -o /dev/null -w '%{http_code}' https://todoist-location-labels.fly.dev/` prints 200

#### Manual Verification
- [ ] End-to-end steps 1 to 5 (the only behaviour change is B12, which is invisible when Todoist is healthy)

---

## Phase 2: Webhook signature (B1)

Two PRs, because a wrong signature check would silently stop every reminder. B12 moved to Phase 1; B7 is handled by the retry rewrite in Phase 4.

### PR 2a: verify but only log

#### 1. Signature helper in `app.py`, next to `todoist_headers`

```python
import hashlib, hmac  # add to imports

def webhook_signature_ok(raw_body: bytes, header: str | None) -> bool:
    expected = base64.b64encode(
        hmac.new(client_secret.encode(), raw_body, hashlib.sha256).digest()
    ).decode()
    return header is not None and hmac.compare_digest(expected, header)
```

Todoist signs the raw request body with the app's OAuth client secret: base64(HMAC-SHA256(client_secret, body)) in header `X-Todoist-Hmac-SHA256` (developer.todoist.com/api/v1, Webhooks). `request.get_data()` must be read before `request.json` touches the stream; it caches, so both work.

At the top of `webhook()`:
```python
raw = request.get_data()
if not webhook_signature_ok(raw, request.headers.get("X-Todoist-Hmac-SHA256")):
    app.logger.warning("webhook signature mismatch (not enforced yet)")
else:
    app.logger.info("webhook signature ok")
```

#### Tests
- `test_signature_helper`: fixed secret and body, assert `webhook_signature_ok(body, correct)` is True and False for a tampered body and for `None`. Compute `correct` once with Python's `hmac` in the test using the same formula is NOT acceptable (it proves the mock); instead paste a constant computed by hand once: secret `test`, body `b'{"event_name":"item:added"}'`, expected `base64(HMAC-SHA256)` as a literal string, and verify the literal against the Todoist docs' example if one is present.

### Success Criteria (2a)
- [ ] `make check` green; CI green; merged; deploy green
- [ ] Dan tags one task; `fly logs --no-tail` shows `webhook signature ok` for that delivery and no `mismatch` line

**Pause until that line is seen.** If a genuine Todoist delivery logs `mismatch`, STOP: the algorithm or header assumption is wrong; do not proceed to 2b. Once one real delivery matches, 2b goes the same day.

### PR 2b: enforce
- Replace the warning branch with `abort(401)`.
- Tests: `test_webhook_rejects_unsigned` (no header → 401, delete/add never called) and `test_webhook_rejects_bad_signature` (wrong header → 401). Both fail on 2a code (200), pass on 2b.
- Update CLAUDE.md "Todoist Webhooks" bullet: deliveries are verified against `X-Todoist-Hmac-SHA256` with the client secret.

### Success Criteria (2b)
- [ ] `make check` green; merged; deploy green
- [ ] End-to-end steps 3 to 5 still work
- [ ] `curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -d '{"event_name":"item:added"}' https://todoist-location-labels.fly.dev/webhook` prints 401

---

## Phase 3: Remove dead weight (B0, B3, R1, R2, 2.1, 2.12, 2.18, 2.19, 2.24)

One PR. Nothing here changes what a user sees.

#### 1. OpenTelemetry out
- `pyproject.toml`: remove the 12 `opentelemetry-*` lines; `uv lock`.
- `app.py`: remove `from opentelemetry import trace` (`:22`), `tracer = ...` (`:58`), and every `@tracer.start_as_current_span(...)` decorator (`:86, 232, 256, 274, 318, 326, 342, 367`).
- `Dockerfile:16`: `ENTRYPOINT ["gunicorn", "-b", "0.0.0.0:5000", "app:app"]`.
- After the deploy is green, Dan runs: `fly secrets unset HONEYCOMB_API_KEY OTEL_SERVICE_NAME -a todoist-location-labels` (this triggers one more release).
- CLAUDE.md: delete the OpenTelemetry/Honeycomb sentences (`:66, 72`); README: delete the OpenTelemetry stack line (`:19`).

*Alternative if Dan chooses repair instead:* add `opentelemetry-distro` to dependencies, keep the entrypoint, and have Dan set `OTEL_EXPORTER_OTLP_ENDPOINT=https://api.honeycomb.io` and `OTEL_EXPORTER_OTLP_HEADERS=x-honeycomb-team=<the existing HONEYCOMB_API_KEY value>` via `fly secrets set` from his own shell. Verification is a trace visible in Honeycomb for a `/webhook` request. Add a conftest guard so tests do not try to export.

#### 2. Other dead code and config
- `pyproject.toml`: remove `Flask-Limiter`.
- `git rm -r .devcontainer`.
- `app.py:47-49`: delete the three `SQLALCHEMY_POOL_*` lines; change `:42` to `{"pool_pre_ping": True, "pool_recycle": 299}`. One sync worker never needs a pool of 10; keep the recycle the original author wanted, now actually applied. CLAUDE.md:70 → "pool `pre_ping` on, recycle 299 s".
- `app.py:82-83` commented `create_all`, `:20` and `:60` trailing comments: delete.
- `app.py:97-99` `log_request`: keep, but return early when `Fly-Client-IP` is absent, so Fly's health check (no such header) logs nothing and real visitors still do (R1). Drop the `datetime.now()` from the message; the log line already carries a timestamp.
- `app.py:145-151`: `labels = result["results"]`.
- `templates/index.html:9` favicon link: delete (B11). `:260-262` jQuery/Popper/bootstrap.js: delete.
- `fly.toml:23`: `grace_period = "15s"` (R2).
- `.github/dependabot.yml`: add to the pip entry
  ```yaml
      groups:
        python:
          patterns: ["*"]
  ```
  and the same shape (`actions`, `docker`) for the other two.
- README "Stack" and a new "Cloud dependencies" paragraph: Fly.io (hosting), Fly Postgres (data), Todoist API (OAuth, labels, reminders, webhooks), Google Maps Places (address autocomplete, key is public in the page), Google Analytics (optional). Nothing works offline.

#### Tests
No behaviour change; Phase 1 suite must stay green. Add nothing.

### Success Criteria
- [ ] `grep -c opentelemetry uv.lock` prints 0; `grep -c limiter uv.lock` prints 0; `ls .devcontainer` fails
- [ ] `make check` green; merged; deploy green
- [ ] After Dan's `fly secrets unset`: `fly secrets list` shows 5 names
- [ ] `fly logs --no-tail` after ten minutes: no `Request made to /` lines; one machine start shows the health check passing with no prior failure line
- [ ] End-to-end steps 1 to 5

---

## Phase 4: Small bugs and the retry rewrite (B4, B5, B6, B7, B9, B10, input validation, tenacity)

One PR. Each item carries one test that fails first.

- **B4**: route becomes `@app.route("/delete_label_location/<int:location_label_id>", methods=["POST"])`, keyed by the row's primary key `id`; body `ll = db.session.get(LocationLabel, location_label_id); if ll is None or ll.user_id != user.id: abort(404)`. Template `:156-163`: replace the `<a>` with a one-button `<form method="post" action="{{ url_for('delete_label_location', location_label_id=location_labels[label['id']].id) }}" onsubmit="return confirm('Are you sure?')">`. Test: unknown id → 404 (today: 500); another user's row → 404; GET → 405.
- **B5**: `state = session.get("oauth_secret_state")`; missing or mismatched → 401. `session.pop("user_id", None)` in logout. Tests: cold hit on `/oauth/redirect?state=x&code=y` → 401 (today: 500); `/logout` without session → 302.
- **B6**: `timeout=10` on the OAuth `requests.post`. No test; a one-token change.
- **B9**: in `index()`, if `user is None`: `session.pop("user_id", None)` and render anonymous. Test: session with `user_id=999` → 200 and the Authorize button present.
- **B10**: `app.py:163` log only `result.get("id")`.
- **Input validation** (`create_label_location`): `trigger not in ("on_enter", "on_leave")` → 400; wrap the three `float()`/`int()` casts, `ValueError` → 400. Test: `radius=abc` → 400 (today: 500).
- **`User.query.get` → `db.session.get(User, ...)`** at `:91, 241, 306, 381`. No behaviour change; the suite covers it.
- **tenacity → urllib3 Retry** (B7 and a dependency): module-level
  ```python
  _http = requests.Session()
  _http.mount("https://", HTTPAdapter(max_retries=Retry(total=3, backoff_factor=1,
      status_forcelist=(429, 500, 502, 503, 504), allowed_methods=("GET", "POST"))))
  ```
  Use `_http.get/post` in `todoist_api_get`, `todoist_sync` and the OAuth exchange. POST retry is safe: every sync command carries a `uuid` and Todoist deduplicates on it. Delete `tenacity` from pyproject, the four imports (`:23-28`), `log_retry_attempt`/`log_retry_error` (`:102-107`) and the decorator (`:117-123`). Test: a mocked adapter answering 401 → exactly one request (today: three).

### Success Criteria
- [ ] `grep -c tenacity uv.lock` prints 0
- [ ] `make check` green; each new test's pre-fix failing run pasted in the PR body; merged; deploy green
- [ ] End-to-end steps 1 to 5, plus: delete a mapping from the UI and confirm it is gone after reload

---

## Phase 5: Data model and sessions (B2, B8 gate, Flask-Session removal)

### Gate first: label ID format (B8)

Before this phase Dan adds a permission rule allowing `fly ssh console` and `fly postgres connect` for this repo (settings skill). Then the agent runs, with Dan's personal Todoist API token exported in Dan's shell as `TODOIST_TOKEN` (Settings → Integrations → Developer; the agent never sees the value):
```
curl -s -H "Authorization: Bearer $TODOIST_TOKEN" https://api.todoist.com/api/v1/labels | python3 -c 'import json,sys; [print(l["id"], l["name"]) for l in json.load(sys.stdin)["results"]]'
```
If every `id` is all digits, proceed. If any `id` is not numeric (base32 letters), STOP: `label_id` must become `db.String`, the `int()` casts at `:346, :381` go, and the `<int:...>` route converters change; write that as a separate plan before this phase.

#### 1. Unique mapping per user and label (B2)
- Model: `__table_args__ = (db.UniqueConstraint("user_id", "label_id", name="uq_location_label_user_label"),)`.
- `create_label_location`: look up `LocationLabel.query.filter_by(user_id=user.id, label_id=label_id).first()`; if found, update its fields in place (changing the address for a label is the natural meaning of submitting it again); else insert. Test: two posts for the same label → one row, second values win (today: two rows).
- Production migration, before the deploy (the deploy does not run migrations). Host is Fly app `dcw-postgres-dev`; the live database is one of `todoist_location_labels` or `dcw_todoist_location`. First a read, shown to Dan: `fly ssh console -a dcw-postgres-dev -C "psql -U postgres -d <db> -c '\\dt' -c 'select count(*) from location_label'"` on both names to find the one with tables, then the duplicate count. Only after Dan's go, run
  ```sql
  DELETE FROM location_label a USING location_label b
   WHERE a.user_id = b.user_id AND a.label_id = b.label_id AND a.id > b.id;
  ALTER TABLE location_label ADD CONSTRAINT uq_location_label_user_label UNIQUE (user_id, label_id);
  ```
  The DELETE keeps the oldest row per pair. Count before and after; the ALTER fails loudly if any duplicate remains.

#### 2. Flask-Session out, signed cookie in
- `pyproject.toml`: remove `Flask-Session`; `uv lock`.
- `app.py`: delete `:20` import, `:36-39` SESSION_* config, `:60` `Session(app)`. Add
  ```python
  app.config.update(SESSION_COOKIE_SECURE=True, SESSION_COOKIE_SAMESITE="Lax",
                    PERMANENT_SESSION_LIFETIME=timedelta(days=30))
  ```
  and `session.permanent = True` right after `session["user_id"] = user.id` in `oauth_redirect`. Thirty days instead of today's one: Dan logs in daily now because the filesystem session expires in 86400 s; a signed cookie costs nothing to keep longer. Flip to `days=1` if he prefers.
- `.gitignore`: `flask_session/` line can stay or go.
- Everyone is logged out once at deploy. Say so in the PR body.

### Success Criteria
- [ ] Gate output recorded in the PR body (all numeric, or STOP)
- [ ] `grep -c flask-session uv.lock` prints 0
- [ ] `make check` green; merged; deploy green
- [ ] After the migration: `\d location_label` in psql shows `uq_location_label_user_label`
- [ ] End-to-end steps 1 to 5; log in once more after the cookie change; re-submit an existing label with a new address and see one entry with the new address

---

## Phase 6: Close out

- Run `/repo-intake` again. Expected: 0 tier 1, 0 tier 2, in the fold; the rerun scopes `improve` to `git diff 27e2c80..HEAD`.
- Tick the boxes above; link each PR next to its phase.
- Store the AutoMem pointer: repo, date, verdict, PR list.

## Rollback

Every phase is one deploy. `fly releases -a todoist-location-labels` lists versions; `fly deploy --image <previous image ref>` (from `fly releases --image`) rolls back in under a minute. Phase 5's constraint is the only one-way change; its rollback is `ALTER TABLE location_label DROP CONSTRAINT uq_location_label_user_label;`, and the deduped rows are gone by design.

## References
- Intake report: `plans/intake-2026-10-02.md`
- Previous plan (API v1 migration): `plans/2026-03-20-migrate-to-todoist-api-v1.md`
- Standards: `~/.claude/skills/repo-intake/standards.md`
- Todoist API v1 docs: https://developer.todoist.com/api/v1/ (Webhooks: `X-Todoist-Hmac-SHA256`, base64 HMAC-SHA256 with the client secret)
