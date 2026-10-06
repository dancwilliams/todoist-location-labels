# Fourth-intake close-out: B3, B4 and the deletions — Implementation Plan

Source: `plans/intake-2026-10-06.md` (the fourth intake report; B and section-5 item numbers below refer to it).
Planned at commit e146127 (master, Fly release v86), 2026-10-06. Two PRs to `master`, each merged by Dan on green CI. Merging deploys, so the code PR ends with a production check.

## Overview

The fourth intake's two tier 2 rows were cleared by #46 (B1 and the "in one batch" sentence). What is left is two low bugs on the read path (a malformed reminders page during a sweep answers 500 instead of the 502 with its sentence; a cursor that repeats loops until gunicorn kills the worker) and six small deletions. One PR carries all of it; a fifth intake then confirms 0 tier 1, 0 tier 2.

## Decisions (Dan, 2026-10-06)

| # | Decision | Resolution |
|---|---|---|
| 1 | Google Analytics (section 5, item 2) | Stays. "Somebody may need that." Not touched by this plan |
| 2 | Phasing | One PR for the code (B3, B4, deletions), one for the intake report |

Settled from evidence, not asked:
- **The `fly.toml` concurrency block stays.** The report's section 5, item 3 called it Fly's defaults. Fly's configuration reference (read 2026-10-06, https://docs.fly.io/reference/configuration): `kill_signal` "sends a `SIGINT` signal ... by default", `kill_timeout` "The default is 5 seconds", `soft_limit` "If unset, the default is 20" — but `hard_limit` "If unset, no `hard_limit` is enforced." Deleting the block would remove the 25-connection cap in front of one sync gunicorn worker. Only `kill_signal` and `kill_timeout` go.

## Current State Analysis

- `app.py` is 626 lines at e146127. Read it, `tests/conftest.py`, `tests/test_routes.py`, `tests/test_webhook.py` and `tests/test_todoist_client.py` before touching anything.
- `todoist_get_all` (`app.py:162-176`) does `page["results"]` (`:171`) and follows `page.get("next_cursor")` (`:172`) with no cap and no repeat check.
- The webhook catches `(RequestException, KeyError, TypeError)` on its reads (`:549`). The two routes catch `RequestException` only, around `sweep_reminders` (`:412` delete, `:498` edit). A `TypeError` or `KeyError` from the sweep's read therefore reaches Flask's default 500 handler. Reproduced by the intake's subagent (its P4) with `todoist_api_get` patched to return a list.
- `todoist_add_reminder` and `todoist_delete_reminder` (`:264-273`) each wrap `todoist_run_commands` with one command and have one production caller each (`:589`, `:611`). `tests/test_webhook.py:112` patches `todoist_add_reminder`; `tests/test_todoist_client.py:32,76` call `todoist_delete_reminder` directly.
- The webhook builds `task_label_ids` (`:557-568`), `task_label_id_strs` (`:576`) and `not_used_location_labels` (`:577-579`); `by_label` is keyed by `str(label_id)` (`:574`) and looked up with `str(label_id)` (`:584`).
- `templates/index.html:57-59` styles `.container-narrow > hr` (no such element in the page) and `:75-77` styles `.marketing p + h4` (the one `.marketing` div, `:145`, has no `p` before its `h4`). Verified by `grep -c container-narrow templates/index.html` = 1 (the rule itself) and reading `:145-173`.
- `tests/test_routes.py:20` `test_delete_own_mapping` asserts 302 and an empty table; `:236` `test_deleting_a_mapping_removes_its_reminders` asserts the same plus the command sent and the log line.
- `.gitignore` has 58 active patterns from the GitHub Python template. What this repo produces: `.venv/`, `__pycache__/`, `.env*` (except `.env.example`), `test.db` (the SQLite fallback, `app.py:43`), `.coverage`, `.mypy_cache/`, `.ruff_cache/`, `.pytest_cache/`, and `.python-version` (present, ignored by the current `:74`). `.vscode/` is in the current file and is kept. `.claude/` shows as ignored through the global excludes file, not this one.
- `fly.toml:3-4` are `kill_signal = "SIGINT"` and `kill_timeout = "5s"`.
- CI: `make check` on 3.13 and 3.14, deploy on push to master. 46 tests, `app.py` 91%.

## Desired End State

- A sweep whose reminders read comes back malformed answers 502 with `SWEEP_FAILED`, like a network failure does; the mapping is unchanged.
- `todoist_get_all` raises on a repeated cursor; the webhook answers 503 and a route 502.
- The six deletions done; `GOOGLE_ANALYTICS_ID` and everything that reads it untouched; the `fly.toml` concurrency block untouched.
- A fifth `/repo-intake` at the code PR's merge commit reports 0 tier 1, 0 tier 2.

### How to verify (end to end, after the code PR deploys)
Dan's steps 2 to 5 of the third plan's "How to verify", on https://todoist-location-labels.fly.dev/: map a label at radius 100, tag a task (reminder appears, log `reminder_add sync result` ok), edit to 255 (`sweep matched 1 of N`, two ok), edit back to 100 (same), delete (`sweep matched 1 of N`, one ok). `fly logs -a todoist-location-labels --no-tail` shows no traceback and no `failed`, `refused` or `redelivery` line. The agent reads the log.

## What We're NOT Doing

- Google Analytics (decision 1).
- The `fly.toml` concurrency block (settled above).
- Section 6 of the report (opinions): error pages, `100.0 M`, the `urllib3` import, the OAuth state, pinned actions, Bootstrap.
- A page cap on `todoist_get_all`. A repeat check catches the loop the subagent reproduced; a cap would add a number with no measured basis (the largest list is 300 reminders at an unknown page size).
- Changing paths, methods or status codes of any route.

## Rules for every phase

- One PR per phase, from a branch off current `master`. No attribution trailers.
- Every behaviour change lands with a test that fails on the code before the change; paste the failing run in the PR body (2.26).
- `make check` before pushing.
- Merging deploys; the merge is Dan's: `gh pr merge <n> --repo dancwilliams/todoist-location-labels --squash --delete-branch`.
- The agent never reads, prints or types a secret.

---

## Phase 1 (PR A): B3, B4 and the deletions

### Changes Required

#### 1. `app.py`: the routes catch what the read can raise (B3; `:412`, `:498`)

Both `except requests.exceptions.RequestException as e:` become

```python
        except (requests.exceptions.RequestException, KeyError, TypeError) as e:
```

with the same log line and `abort(502, description=SWEEP_FAILED)` after. The webhook already has this tuple (`:549`); the comment there explains it.

#### 2. `app.py`: a repeated cursor raises (B4; `:162-176`)

```python
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
        items.extend(page["results"])
        cursor = page.get("next_cursor")
        if not cursor:
            return items
        if cursor in seen:
            raise requests.exceptions.RequestException(f"{endpoint}: cursor {cursor!r} repeated")
        seen.add(cursor)
```

`RequestException`, not a new class: every caller already answers 503 or 502 on it.

#### 3. `app.py`: inline the two one-command wrappers (section 5, item 4; `:264-273`, `:589`, `:611`)

Delete `todoist_add_reminder` and `todoist_delete_reminder`. The two call sites become

```python
                    todoist_run_commands(token, [reminder_delete_command(reminder["id"])], "reminder_delete")
```
```python
            command = reminder_add_command(event_data["id"], *place_of(loc_label))
            todoist_run_commands(token, [command], "reminder_add")
```

(line-wrapped as ruff wants). `tests/test_webhook.py:112` patches `todoist_run_commands` instead; `tests/test_todoist_client.py:32` and `:76` call `todoist_run_commands(token, [reminder_delete_command("r1")], "reminder_delete")`. The log lines `reminder_add sync result` and `reminder_delete sync result` are unchanged, so Dan's log reading is unchanged.

#### 4. `app.py`: one set of label ids in the webhook (section 5, item 5; `:557-579`)

```python
    # Map the task's label names to label IDs (API v1 webhooks carry names)
    task_label_ids = set()
    for name in event_data.get("labels", []):
        label_id = label_name_to_id.get(name)
        if label_id is None:
            app.logger.warning("Label '%s' not found in user's labels", name)
        else:
            task_label_ids.add(str(label_id))
    app.logger.info("Task label IDs: %s", sorted(task_label_ids))
    ...
    not_used_location_labels = [
        ll for ll in user_location_labels if str(ll.label_id) not in task_label_ids
    ]
```

and the add loop iterates `for label_id in task_label_ids:` with `by_label.get(label_id)`. `task_label_id_strs` is gone. The `Task label IDs` log line keeps its name; its value is sorted so the log is stable.

#### 5. `templates/index.html`: two dead rules (section 5, item 6; `:57-59`, `:75-77`)

Delete the `.container-narrow > hr` and `.marketing p + h4` blocks.

#### 6. `tests/test_routes.py`: one overlapping test (section 5, item 7; `:20-23`)

Delete `test_delete_own_mapping`; `test_deleting_a_mapping_removes_its_reminders` (`:236`) asserts everything it did.

#### 7. `.gitignore` (section 5, item 1)

Replace the file with:

```
__pycache__/
.venv/
.env*
!.env.example
test.db
.coverage
.mypy_cache/
.ruff_cache/
.pytest_cache/
.python-version
.vscode/
```

Before committing: `git status --ignored --short | grep '^!!'` on the old and the new file list the same ignored paths, or the difference is named in the PR body.

#### 8. `fly.toml` (section 5, item 3, narrowed)

Delete lines 3-4 (`kill_signal`, `kill_timeout`). The concurrency block stays (see Decisions). The deploy is the test.

#### 9. Tests

| Test | Protects | Fails on e146127 with |
|---|---|---|
| `test_malformed_reminders_page_during_a_sweep_is_a_502` (`tests/test_routes.py`): `monkeypatch` `todoist_api_get` to return `["not", "a", "dict"]`; delete the fixture's mapping → 502, body carries "Submit the same change again", row still there | B3: the sweep's read failing oddly is told to the user like any other failure | `TypeError` raised (TESTING mode) |
| `test_a_repeated_cursor_raises` (`tests/test_todoist_client.py`): `todoist_api_get` patched to answer `{"results": [], "next_cursor": "same"}` every time → `pytest.raises(RequestException)`, and the fake was called at most 3 times | B4: a cursor that repeats does not spin | times out or the call count exceeds 3 (write the probe with a counter that raises `AssertionError` past 50 calls so the failing run terminates) |

Both expected values come from the contract (a 502 with the existing sentence; a raise), not the code under test.

#### 10. Docs

- `CLAUDE.md`, the `todoist_get_all` bullet: "...follows `next_cursor` until it is empty, for labels and reminders alike, and raises if a cursor repeats; reading only the first page would make later labels look removed and later reminders look absent."
- `CLAUDE.md` Data Flow 5: "If Todoist cannot be reached, answers in an unexpected shape, or refuses a delete, the request answers 502..."
- `plans/intake-2026-10-06.md` is not edited (reports are not rewritten); the correction to section 5, item 3 is recorded in this plan and in the fifth report's "What changed" table.

### Success Criteria

#### Automated Verification
- [x] The two new tests fail on e146127 for the stated reasons; output in the PR body (`TypeError: list indices must be integers or slices, not str` from `app.py:171`; `AssertionError: todoist_get_all is looping`, 51 calls)
- [x] `make check` exits 0; 47 tests (46 + 2 − 1), `app.py` coverage not below 91% (47 passed, 91%)
- [x] `git status --ignored --short` before and after the `.gitignore` change lists the same paths (or the difference is in the PR body) (identical, 9 paths)
- [x] `grep -ci google_analytics_id app.py templates/index.html .env.example README.md CLAUDE.md` unchanged from e146127 (the counts are 2, 3, 1, 2, 1 at both commits, not the 1, 2, 1, 2, 1 written here; GA untouched)
- [x] `grep -A3 'http_service.concurrency' fly.toml` still prints `connections`, 25, 20
- [x] CI green on the PR (#47, run 37512412371, check 3.13 and 3.14)
- [x] After Dan's merge: deploy job green; machine `started` with its check passing; `curl -s -o /dev/null -w '%{http_code}' https://todoist-location-labels.fly.dev/` prints 200 (merged as 60e1a86, run 37513170698 deploy success, Fly v87 machine 91854667f4e938 started 1/1 passing at 18:45 UTC, GET / 200)

#### Manual Verification
- [x] "How to verify" steps 2 to 5; the agent reads the Fly log for `sweep matched 1 of N` on each of the three sweeps and no error line (Dan ran them on v87, 19:39 UTC: `reminder_add` ok on mapping 24; `sweep matched 1 of 1` at 19:39:17, :38, :47 with two, two and one `ok`; no app error line. The buffer's only `[error]` lines are Fly's health probe 1 s into each cold start, passing 4 s later — the autostart boot, not the app)

**Result** (2026-10-06): PR #47 merged by Dan as 60e1a86, Fly release v87. All criteria met. The plan's GA grep counts were miscounted (real: 2, 3, 1, 2, 1 at both commits); GA untouched either way.

**Implementation Note**: stop after this phase until Dan confirms the manual steps.

---

## Phase 2 (PR B): Fifth intake

- Run `/repo-intake` at PR A's merge commit. The `improve` pass goes to a fresh agent that is given the code and not this plan's reasoning, scope `git diff 42f3fc7..HEAD` (the fourth intake's commit), per standards "How the audit applies this file" 6. Brief the agent with the measured facts it cannot read from `plans/`: the Phase 0b `same_place_pairs` count of 0 (2026-10-05), the REST reminder field names read live on 2026-10-05, and the Fly `hard_limit` default above.
- Expected: 0 tier 1, 0 tier 2. If a row fails, STOP and report; do not fix inside the intake.
- The report's "What changed" table records the `fly.toml` correction.
- Commit `plans/intake-2026-10-<day>.md` and this plan's results in one docs-only PR.
- AutoMem: one pointer memory, linked to the fourth intake's memory (`ce283c04-344e-4a3d-8b24-8177b6cf7b53`).

### Success Criteria
- [x] Report written with a status for every row of `standards.md` and a non-empty Not checked section (`plans/intake-2026-10-06-2.md`, 2026-10-06, at 60e1a86)
- [x] Verdict 0 tier 1, 0 tier 2 — or the failing rows listed here with the reason. **0 tier 1, 2 tier 2**, both on an edge case never observed and cleared by one fix: 2.12 (CLAUDE.md Data Flow 5 says an unexpected shape answers 502; the shapes that raise `AttributeError` — results items that are not dicts, a sync answer or `sync_status` that is not a dict — answer 500; report B6) and 2.25 (B5: a refused or status-less *delete* in an edit sweep keeps the mapping while the other tasks moved, and the resubmit the 502 asks for leaves each of them with two reminders — B1's mirror, left open by #46; not in the README). B7: `.dockerignore` lacks `.env*` under `COPY . /app`. Stopped here per this phase's rule; nothing fixed inside the intake
- [ ] PR B merged by Dan; this plan's result lines written under each phase

**Result** (2026-10-06): fifth intake at 60e1a86 found what the fourth's fix left: B5 (reproduced with a stateful stub, `scratchpad/probe_sweep.py`), B6 (three probes), B7 (read). Fix order in the report: B5 and B6 in one PR (`sweep_reminders` skips a re-add the task already has; `todoist_get_all` and `todoist_run_commands` raise `RequestException` on an unexpected shape), B7 one line. The `fly.toml` correction is in the report's "What changed" table. Runtime window clean (15 min 29 s, 100 lines, 6/6 sync statuses `ok`, machine on 60e1a86).

---

## Testing Strategy

- B3 and B4 are tested through patches of `todoist_api_get`, as `test_labels_follow_next_cursor` and `test_unexpected_labels_shape_does_not_delete` already are: `fake_todoist` always answers a well-formed page, and a repeating cursor is a shape, not a status.
- The deletions change no behaviour; the suite and Dan's production run cover them. The `fly.toml` trim is proven by the deploy and the machine's check.
- No live Todoist check: nothing sent to Todoist changes.

## Rollback

One deploy. `fly releases -a todoist-location-labels --image` lists the image refs; `fly deploy --image <previous ref>` rolls back.

## References

- Fourth intake: `plans/intake-2026-10-06.md`, sections 4 and 5
- Third-intake plan (the "How to verify" steps): `plans/2026-10-05-third-intake-fixes.md`
- Fly configuration reference: https://docs.fly.io/reference/configuration (`kill_signal`, `kill_timeout`, `http_service.concurrency`)
