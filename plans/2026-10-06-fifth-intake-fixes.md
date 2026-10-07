# Fifth-intake close-out: B5, B6 and B7 — Implementation Plan

Source: `plans/intake-2026-10-06-2.md` (the fifth intake report; B numbers below refer to it).
Planned at commit 9617b86 (master, Fly release v88), 2026-10-06. Two PRs to `master`, each merged by Dan on green CI. Merging deploys, so the code PR ends with a production check.

## Overview

The fifth intake found what the fourth's fix left: a sweep whose refusal includes a *delete* keeps the mapping while the other tasks' reminders have already moved, and the resubmit the 502 asks for re-adds blind, so each moved task ends with two identical reminders (B5, reproduced with a stateful stub). Two smaller ones: the Todoist answer shapes that raise `AttributeError` give a 500 instead of the 502 sentence (B6), and `.dockerignore` lets a manual `fly deploy` put `.env` in the image (B7). One PR carries all three and the docs; a sixth intake then confirms 0 tier 1, 0 tier 2.

## Decisions (Dan, 2026-10-06)

| # | Decision | Resolution |
|---|---|---|
| 1 | B5 approach | **Dedupe re-adds in the sweep**: `sweep_reminders` skips the add for a task that already has a reminder at the new place. The mapping rule of #46 (only-adds-refused saves; a refused delete keeps) is unchanged; a resubmit then converges from every mixed state. Rejected: saving the mapping on any refusal (the refused-delete task would keep a reminder no mapping matches, forever), and both (the second buys nothing once the first is in) |
| 2 | Phasing | One PR for the code (B5, B6, B7, docs), one for the intake report. Same shape as the last two plans |

Settled from evidence, not asked:
- **The routes' catch tuples stay `(RequestException, KeyError, TypeError)`.** The report's B6 sketch said they could shrink to `RequestException` once the shared functions check shapes. They cannot: a reminder dict missing `id` is still a `KeyError` at `app.py:318`, and that must still be a 502. The fix is guards only.
- **B6's guards go where every read and every write routes through**: `todoist_get_all` for pages, `todoist_run_commands` for sync answers. Not in `todoist_api_get`: the one caller it would additionally cover is `todoist_get_user` on the index page, which is not in the 2.12 clause and has never misbehaved.
- **`.dockerignore` gains `.env*` only.** The cache directories and `.coverage` would shrink the image, not close a hole.

## Current State Analysis

- `app.py` is 618 lines at 9617b86 (code unchanged since 60e1a86). Read it, `tests/conftest.py`, `tests/test_routes.py`, `tests/test_webhook.py` and `tests/test_todoist_client.py` before touching anything.
- `sweep_reminders` (`app.py:304-334`) reads every reminder the account has into `located` (`:313`), matches the old place (`:314`), and for each match appends a delete and, on an edit, an add (`:317-320`) without looking at whether `located` already holds a reminder for that task at `new_place`. That is the one line both duplicate paths go through. The stateful stub `scratchpad/probe_sweep.py` (fifth report, B5) shows `{'900': ['Oak', 'Oak'], '901': ['Oak', 'Oak']}` after a resubmit.
- `todoist_get_all` (`:162-179`) does `page["results"]` (`:173`) and `page.get("next_cursor")` (`:174`); the items reach `reminder.get` in `reminder_is_at` (`:297-300`). `todoist_run_commands` (`:250-266`) does `.get("sync_status", {})` on the answer (`:259`) and `sync_status.get` (`:263`). A non-dict at any of those raises `AttributeError`, which the routes (`:438`, `:492`) do not catch: `probe_items.py` and `probe_shape.py` printed `500` three times.
- `.dockerignore` is 7 lines (`fly.toml`, `.venv`, `.git`, `tests`, `plans`, `__pycache__`, `*.pyc`); `Dockerfile:10` is `COPY . /app`; `README.md:78-81` documents `fly deploy` as the manual path. No `.env` exists in this checkout (`git status --ignored --short` lists none).
- Tests: 47, `app.py` 91%. `fake_todoist` (`tests/conftest.py:100-177`) is stateless: `do_GET` answers `state["reminders"]` as configured, `do_POST` records commands and refuses by type. `test_malformed_reminders_page_during_a_sweep_is_a_502` (`tests/test_routes.py:261-270`) patches `todoist_api_get` to return a list. `test_editing_a_mapping_moves_its_reminders` (`:185-215`) is the model for asserting what a sweep sends. `tests/test_routes.py` imports `pytest` only inside one test (`:139`).
- `README.md:16` describes the refused-add case only. `CLAUDE.md` Data Flow 5 (`:19`) says an unexpected shape answers 502; the `todoist_run_commands` (`:79`) and `todoist_get_all` (`:84`) bullets say what raises.
- CI: `make check` on 3.13 and 3.14, deploy on push to master. Docker Desktop runs here (gitleaks ran in a container this session), so an image can be built locally for B7's proof.

## Desired End State

- A resubmitted edit never creates a second reminder on a task that already has one at the new place; the sweep logs how many re-adds it skipped.
- Any Todoist answer that is not the expected shape — page, results, items, sync answer, `sync_status` — raises `RequestException` in the shared function, so the webhook answers 503 and a route 502 with its sentence, and the CLAUDE.md clause is literally true.
- A `docker build` of a checkout holding a `.env` produces an image without it.
- A sixth `/repo-intake` at the code PR's merge commit reports 0 tier 1, 0 tier 2.

### How to verify (end to end, after the code PR deploys)
Dan's steps 2 to 5 of the third plan's "How to verify", on https://todoist-location-labels.fly.dev/: map a label at radius 100, tag a task (reminder appears, log `reminder_add sync result` ok), edit to 255 (`sweep matched 1 of N`, two ok), edit back to 100 (same), delete (`sweep matched 1 of N`, one ok). `fly logs -a todoist-location-labels --no-tail` shows no traceback and no `failed`, `refused`, `skipped` or `redelivery` line from the app. The agent reads the log. The dedupe itself cannot be provoked from the form (it needs a refusal); the test and the stub prove it.

## What We're NOT Doing

- A stateful mode for `fake_todoist` (fifth report, opinion 2). B5's regression test is stateless and fails first; the interleaving is proven by the stub.
- `exc_info` on the two swallowed-exception log lines (opinion 1), the `== 2` pin on the cursor test (opinion 3), `kill_signal = "SIGTERM"` (opinion 4), request status codes in the log (opinion 5), section 5's two cuts, the carried opinions.
- A guard in `todoist_api_get` or `todoist_get_user` (settled above).
- Changing paths, methods or status codes of any route; changing the mapping rule of #46.

## Rules for every phase

- One PR per phase, from a branch off current `master`. No attribution trailers.
- Every behaviour change lands with a test that fails on the code before the change; paste the failing run in the PR body (2.26).
- `make check` before pushing.
- Merging deploys; the merge is Dan's: `gh pr merge <n> --repo dancwilliams/todoist-location-labels --squash --delete-branch`.
- The agent never reads, prints or types a secret. B7's probe uses an empty dummy `.env` created and removed by the agent.

---

## Phase 1 (PR A): B5, B6, B7 and the docs

### Changes Required

#### 1. `app.py`: the sweep does not re-add what the task already has (B5; `:312-320`)

```python
    commands = []
    located = todoist_get_reminders(token)
    matched = [r for r in located if reminder_is_at(r, old_place)]
    # Zero of many is how a reminder that no longer matches its mapping shows up.
    app.logger.info("sweep matched %d of %d location reminders", len(matched), len(located))
    # After a refused delete, a missing status or a worker killed mid-sweep, some
    # tasks have moved and some have not. The resubmit the 502 asks for must
    # converge, so a task already holding a reminder at the new place gets no second one.
    already = {
        r["item_id"] for r in located if new_place is not None and reminder_is_at(r, new_place)
    }
    for reminder in matched:
        commands.append(reminder_delete_command(reminder["id"]))
        if new_place is not None and reminder["item_id"] not in already:
            commands.append(reminder_add_command(reminder["item_id"], *new_place))
    if skipped := sum(r["item_id"] in already for r in matched):
        app.logger.info("sweep: %d re-adds skipped, reminder already at the new place", skipped)
```

(ruff may re-wrap; keep the names.) The log line fires only when something was skipped, so Dan's normal log reading is unchanged. Traced against the stub's three cases: resubmit after a refused delete → deletes 900's and 901's Home reminders, skips both re-adds, `{'900': ['Oak'], '901': ['Oak']}`; after a status-less delete → deletes 901's webhook-added Home, skips its re-add; after every add refused → nothing at the new place, every re-add sent (unchanged).

#### 2. `app.py`: shape guards where every read and write routes through (B6; `:170-173`, `:259-263`)

In `todoist_get_all`, replace `items.extend(page["results"])` with

```python
        results = page.get("results") if isinstance(page, dict) else None
        if not isinstance(results, list) or not all(isinstance(r, dict) for r in results):
            raise requests.exceptions.RequestException(f"{endpoint}: unexpected page shape")
        items.extend(results)
```

In `todoist_run_commands`, replace the `sync_status = ...` line with

```python
        answer = todoist_sync(token, commands=batch)
        sync_status = answer.get("sync_status") if isinstance(answer, dict) else None
        if not isinstance(sync_status, dict):
            # Earlier batches are applied and later ones are not sent: the route
            # answers 502 and the resubmit converges (the sweep skips what moved).
            raise requests.exceptions.RequestException(f"{what}: unexpected sync answer")
```

`RequestException`, not a new class: every caller already answers 503 or 502 on it. A missing `sync_status` key used to make every command of the batch "refused" (`None` status) and raise `TodoistRefused`; it now raises `RequestException` directly. Same outcome at every caller (a refused delete and a failed request both keep the mapping with `SWEEP_FAILED`); a `sync_status` dict missing one uuid still goes the `TodoistRefused` path, unchanged. The docstring at `:163` ("Raises on API failure or an unexpected shape") stays true.

#### 3. `.dockerignore` (B7)

Append `.env*`. (`.env.example` is excluded with it; nothing reads it at runtime.)

#### 4. Tests

| Test | Protects | Fails on 9617b86 with |
|---|---|---|
| `test_resubmitted_edit_does_not_double_a_moved_reminder` (`tests/test_routes.py`): `fake_todoist["reminders"] = [HOME_REMINDER, dict(HOME_REMINDER, id="r2", name="2 Oak Ave"), dict(HOME_REMINDER, id="r3", item_id="902")]` (task 900 already moved, task 902 not); edit label 10 to `address="2 Oak Ave"` with `_form` defaults → 302, and `_sent(fake_todoist) == [("reminder_delete", {"id": "r1"}), ("reminder_delete", {"id": "r3"}), ("reminder_add", {"item_id": "902", "type": "location", "name": "2 Oak Ave", "loc_lat": "1.5", "loc_long": "2.5", "loc_trigger": "on_enter", "radius": 100})]`; log carries `sweep: 1 re-adds skipped` | B5: a half-applied edit converges on resubmit | the sent list has four commands, an add for 900 second |
| `test_malformed_todoist_answer_during_a_sweep_is_a_502` (`tests/test_routes.py`), replacing `test_malformed_reminders_page_during_a_sweep_is_a_502`: `@pytest.mark.parametrize("patch, answer", [("todoist_api_get", ["not", "a", "dict"]), ("todoist_api_get", {"results": ["not a reminder"]}), ("todoist_sync", []), ("todoist_sync", {"sync_status": []})])`; `fake_todoist["reminders"] = [HOME_REMINDER]` so the sync cases build a command; `monkeypatch.setattr(app_module, patch, lambda *a, **k: answer)`; delete the mapping → 502, body carries "Submit the same change again", row still there | B6: every unexpected shape is told to the user like any other failure | cases 2 to 4 raise `AttributeError: ... has no attribute 'get'` (TESTING mode); case 1 already passes |

Add `import pytest` at the top of `tests/test_routes.py` (the inline import at `:139` can stay or move up). Both expected values come from the contract (the commands a converged sweep must send; a 502 with the existing sentence), not the code under test.

#### 5. B7's proof, before and after the `.dockerignore` change

From the repo root, with no real `.env` present (confirmed above):

```
touch .env && docker build -q -t tll-probe . && docker run --rm --entrypoint sh tll-probe -c 'ls -a /app | grep -c "^\.env$"'; rm .env
```

Prints `1` on 9617b86 and `0` after; paste both in the PR body. `docker rmi tll-probe` afterwards. The dummy is empty; nothing secret is created.

#### 6. Docs

- `README.md:16`: "Changing a mapping deletes each of its reminders and creates it again at the new place. If Todoist refuses to create one, the page says so and that task's reminder comes back the next time the task changes. If it refuses to delete one, the page asks you to submit the change again; a resubmit never creates a second reminder."
- `CLAUDE.md` Data Flow 5: after "...and the mapping is left as it was." add "A resubmit skips the re-add for any task that already has a reminder at the new place, so a half-applied sweep converges instead of doubling."
- `CLAUDE.md` `sweep_reminders` bullet (`:77`): add "It logs `sweep: K re-adds skipped, reminder already at the new place` when a resubmit found tasks already moved."
- `CLAUDE.md` `todoist_run_commands` bullet (`:79`): "...raises `RequestException` when the sync answer is not an object holding a `sync_status` object, and `TodoistRefused` (a `RequestException` carrying uuid → status) when..."
- `CLAUDE.md` `todoist_get_all` bullet (`:84`): "...follows `next_cursor` until it is empty, for labels and reminders alike, and raises `RequestException` if a page is not an object holding a list of objects or if a cursor repeats; ..."
- `CLAUDE.md` Deployment: add "`.dockerignore` excludes `.env*`: `COPY . /app` copies the checkout, and a manual `fly deploy` would otherwise ship a local `.env`."
- `plans/intake-2026-10-06-2.md` is not edited (reports are not rewritten); the tuple correction is recorded in this plan and in the sixth report's "What changed" table.

### Success Criteria

#### Automated Verification
- [x] The B5 test and B6 cases 2 to 4 fail on 9617b86 for the stated reasons; output in the PR body (#49: three `AttributeError: ... has no attribute 'get'`, B5's list had the add for 900 at index 1 and four commands)
- [x] B7's probe prints `1` on 9617b86 and `0` on the branch; both lines in the PR body
- [x] `make check` exits 0; 51 tests (47 − 1 + 4 + 1), `app.py` coverage not below 91% (51 passed, 91%)
- [x] `git diff 9617b86 -- app.py | grep -c '^[-+].*except'` is 0 (the catch tuples are untouched) — the substring grep counts 2, both the word "unexpected" in the two new `RequestException` messages; `grep -cE '\bexcept\b'` is 0
- [x] CI green on the PR (run 37526736213, 3.13 and 3.14)
- [x] After Dan's merge: deploy job green; machine `started` with its check passing; `curl -s -o /dev/null -w '%{http_code}' https://todoist-location-labels.fly.dev/` prints 200 (#49 merged as 4ada2c5, run 37527310471 green, Fly v89 machine started 1/1 passing, GET / 200)

#### Manual Verification
- [x] "How to verify" steps 2 to 5; the agent reads the Fly log for `sweep matched 1 of N` on each of the three sweeps, no `skipped` line (nothing was half-applied), and no error line (Dan, 2026-10-06 20:39 UTC on v89: `reminder_add sync result` ok for mapping 25; edit to 255 and back to 100 each `sweep matched 1 of 1` with two `ok`; delete `sweep matched 1 of 1` with one `ok`; the un-tag webhook found 0 reminders; 0 lines matching traceback/failed/refused/skipped/redelivery/WARNING/ERROR)

**Implementation Note**: stop after this phase until Dan confirms the manual steps.

**Result** (2026-10-06): PR #49 merged as 4ada2c5, Fly v89. B5 dedupe, B6 guards, B7 `.dockerignore`, docs. The plan's except-line grep needed a word boundary (`unexpected` matched); catch tuples untouched. Live run clean.

---

## Phase 2 (PR B): Sixth intake

- Run `/repo-intake` at PR A's merge commit. The `improve` pass goes to a fresh agent that is given the code and not this plan's reasoning, scope `git diff 60e1a86..HEAD` (the fifth intake's commit), per standards "How the audit applies this file" 6. Brief it with the measured facts it cannot read from `plans/`: the `same_place_pairs` count of 0 (2026-10-05), the REST reminder field names read live on 2026-10-05, Fly's `hard_limit` having no default, Todoist applying each sync command on its own, `reminder_delete` answering `ok` for an unknown id; and give it the grep exclusion syntax outright (`git grep -n <pattern> -- . ':!plans'`), since the fifth run's agent leaked `plans/` lines through a broken filter. Name the decision interaction to trace: the dedupe meeting #46's save-on-adds-refused rule.
- Expected: 0 tier 1, 0 tier 2. If a row fails, STOP and report; do not fix inside the intake.
- The report's "What changed" table records that the catch tuples stayed, against the fifth report's sketch.
- Commit `plans/intake-2026-10-<day>.md` (or `-3` if the date is taken) and this plan's results in one docs-only PR.
- AutoMem: one pointer memory, linked to the fifth intake's memory (`4ff8ff72-bb02-4ebb-9011-fafec735044a`).

### Success Criteria
- [x] Report written with a status for every row of `standards.md` and a non-empty Not checked section (`plans/intake-2026-10-06-3.md`, 2026-10-06, at 4ada2c5)
- [x] Verdict 0 tier 1, 0 tier 2 — or the failing rows listed here with the reason. **0 tier 1, 2 tier 2**, both from one regression #49 introduced: 2.12 (`README.md:16`, `CLAUDE.md:19` "re-added at the new place" and `CLAUDE.md:81` "each pair in the same request" are false for an edit that changes only the coordinates; F1, F2) and 2.25 (F1 is a shipped defect the README contradicts). F1: `already` is keyed on the same (name, trigger, radius) the sweep matches on, so a coordinate-only edit puts every matched task in `already`, sends deletes only, and answers 302 with the reminders gone until each task next changes — reproduced with a stateful stub on 4ada2c5 and shown not to happen on 60e1a86. F2: a skipped re-add makes the list non-alternating, so a pair can straddle a batch boundary and a later-batch failure loses that task's reminder; the resubmit does not restore it. F3 (pre-existing, low): a resubmit to a *different* place strands the reminders at the half-applied one. Stopped here per this phase's rule; nothing fixed inside the intake
- [ ] PR B merged by Dan; this plan's result lines written under each phase

---

## Testing Strategy

- B5 is tested statelessly: the fake's reminder list is the state a half-applied sweep leaves, and the assertion is the exact command list a converged resubmit sends. The interleaving that produces that state is proven by `scratchpad/probe_sweep.py` (fifth report), not by the suite.
- B6 is tested through patches of `todoist_api_get` and `todoist_sync`, as the existing shape tests are: `fake_todoist` always answers a well-formed page and a well-formed `sync_status`.
- B7 is proven by the local image build, before and after. CI's deploy path is unaffected either way.
- No live Todoist check: nothing sent to Todoist changes in a normal sweep (a converged resubmit sends fewer commands; a first edit sends the same ones).

## Rollback

One deploy. `fly releases -a todoist-location-labels --image` lists the image refs; `fly deploy --image <previous ref>` rolls back.

## References

- Fifth intake: `plans/intake-2026-10-06-2.md`, sections 4 and 7
- Fourth-intake plan (the previous close-out, same shape): `plans/2026-10-06-fourth-intake-fixes.md`
- Third-intake plan (the "How to verify" steps): `plans/2026-10-05-third-intake-fixes.md`
- PR #46 (the mapping rule B5 leaves unchanged): https://github.com/dancwilliams/todoist-location-labels/pull/46
