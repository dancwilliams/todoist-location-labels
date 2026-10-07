# Seventh-intake close-out: a working `initdb`, two true sentences, a delete that can be cut off — Implementation Plan

Source: `plans/intake-2026-10-07.md` (the seventh intake report; G, D and T numbers below refer to it).
Planned at commit 8952df0 (master, Fly release v92), 2026-10-07. Two PRs to `master`, each merged by Dan on green CI. Merging deploys, so the code PR ends with a production check even though nothing it changes runs in production.

## Overview

The seventh intake found no code bug in the sweep rewrite and one in the one block of `app.py` no test has ever run: `uv run python app.py initdb`, the documented way to create tables on a fresh database, has crashed since Flask-SQLAlchemy 3 (certainly since 2026-03-20, possibly since 2023-04-06), because `db.create_all()` runs outside an app context. Beside it, a docstring and a CLAUDE.md sentence are false for one sub-case each, and the convergence table's delete rows never reach the fault they claim to. One PR fixes the four; an eighth intake then checks the diff. G2 (a label removed from a task between a half-applied edit and the resubmit leaves that task holding the new reminder until its next change) is out of this plan by Dan's decision below.

## Decisions (Dan, 2026-10-07)

| # | Decision | Resolution |
|---|---|---|
| 1 | How to test `initdb` | **A subprocess test** that runs `python app.py initdb` against a `tmp_path` SQLite file and reads the schema back. The broken thing is the `__main__` dispatch, which nothing imports; extracting an `initdb()` function would leave the dispatch untested and add a name for one caller |
| 2 | Eighth intake | **Phase 2 of this plan**, scoped to the diff, the pattern of the fourth to sixth plans and the gate to "in the fold" |
| 3 | G2 | **Out.** Neither the README clause nor the label-aware sweep is chosen; the eighth intake will list G2 again as a known, pre-existing, undecided bug. Bugs do not move the verdict (`standards.md`, "In the fold means zero tier 1 FAIL and zero tier 2 FAIL") |
| 4 | T1 | **Three reminders in the `first edit` row**, so a delete is two requests under `SYNC_BATCH = 2` and the dropped second one leaves one reminder for the resubmit. Same proven source as two (tag three tasks). The comment at `tests/test_routes.py:460-461` then describes what the rows do and stays. **Row only, no explicit half-applied-delete test** (grill): the table is the single gate for sweep states; the PR body carries the batch counts |
| 5 | The subprocess test's environment (grill) | **Dummies merged over the environment**: `{**os.environ, **DUMMIES, "DATABASE_URL": ...}` with the four secret variables forced to `"test"`. A real value exported in the shell can never reach the subprocess (the conftest's `setdefault` would keep it); the coverage hook's variables and `PATH` are kept |
| 6 | D1 and D2 wordings (grill) | **As written** in §3 and §4 |
| 7 | 2.12's check (grill) | **`standards.md` amended 2026-10-07** by Dan's ruling, made at the end of the grill and applied by the agent as a direct instruction: every documented command that needs no production credential is run, in a scratch directory, and listed with its exit code; a command is traced only when it cannot run here. Phase 2 points at the amended row rather than restating it |
| 8 | Phase boundary (grill) | **No manual step.** Phase 1 ends at Dan's merge plus the deploy check; Phase 2 starts in a fresh session on Dan's go |

Verified in scratch during the grill (2026-10-07, repo untouched): under `uv run`, `sys.executable` is `.venv/bin/python3`; coverage's subprocess hook `a1_coverage.pth` is installed, so pytest-cov counts the `__main__` lines; with three tasks in `first edit`, the 45 table cases pass and `first edit / delete / second request dropped` answers 502 with `batches == 2` and one reminder left, then 302 with none.

Settled from evidence, not asked:
- **Why `initdb` broke is only partly recorded.** The import-time `with app.app_context(): db.create_all()` that made the command work as a side effect was commented out in f4bd48c (2023-04-06); Flask-SQLAlchemy 3.0 (2022-10) removed the implicit app; the requirement was unpinned until `uv.lock` pinned 3.1.1 in 0e08269 (2026-03-20). The date it first failed is unrecorded and not needed: the fix is the same.
- **Nothing in this plan changes runtime behaviour.** The image's entrypoint is gunicorn (`Dockerfile:14`); `initdb` never runs there; the docstring, CLAUDE.md and the test table are not code paths. The deploy that merging triggers is checked because it happens, not because it is at risk.

## Current State Analysis

- `app.py:656-660` at 8952df0:
  ```python
  if __name__ == "__main__":
      if len(sys.argv) >= 2 and sys.argv[1] == "initdb":
          db.create_all()
      else:
          app.run(debug=True, use_reloader=True)
  ```
  `db.create_all()` with no context raises `RuntimeError: Working outside of application context` from `flask_sqlalchemy/extension.py:687` (3.1.1), exit 1, no table created. Reproduced in the intake against `DATABASE_URL=sqlite:///<scratch>`. The same call inside `with app.app_context():` creates `user` and `location_label` with `CONSTRAINT uq_location_label_user_label UNIQUE (user_id, label_id)` (also reproduced).
- The tests create tables through their own fixture inside a context (`tests/conftest.py:60-66`) and set the five env vars with `os.environ.setdefault` before importing `app` (`:11-18`), `DATABASE_URL` to `sqlite://`. Nothing runs the `__main__` block; coverage lists `:657-660` as missed.
- `sweep_reminders`' docstring, `app.py:340-341`: "raises RequestException on API failure or a refused delete, in which case a reminder is still at the old place." False when Todoist applied the last request and the answer was lost or malformed (the intake's test_E: 502, `[900 Oak, 901 Oak]`, mapping still Home). `CLAUDE.md:19` and `README.md:16` say only that the mapping is left as it was, which is true.
- `CLAUDE.md:80`, last two sentences: "A sweep in which an add and a delete were both refused keeps the mapping, and the refused-add task holds nothing until its next change re-creates it; the page says to submit again, which cannot restore that one. Never observed; no fourth message for it." True when the refusals hit different tasks (test_C); false when both hit one task, whose refused delete leaves its old reminder for the resubmit to move (test_D).
- `tests/test_routes.py:388-419` `STATES`; `:390` `"first edit": lambda old, new: [_rem("r1", "900", old), _rem("r2", "901", old)]`. Under `SYNC_BATCH = 2` (`:480`) a delete of this state is two commands in one request, so `second request dropped` (`:424`) never fires and the first submit answers 302 (the test allows this at `:490-491`). `:460-461`: "a half-applied delete is 'first edit' with a request dropped", which no row does. The three delete-target states are `first edit`, `already converged` (nothing to send) and `another mapping's reminder` (one delete); `CASES` at `:462-467` excludes the `half applied` states from the delete target.
- Uncommitted in the tree: the sixth plan's last tick (`plans/2026-10-06-sixth-intake-fixes.md`, PR B merged). It rides in PR A.
- Tests: 98, `app.py` 91%. CI: `make check` on 3.13 and 3.14, deploy on push to master.

## Desired End State

- `uv run python app.py initdb` creates the schema on an empty database, unique constraint included, exit 0; a test runs the command as a process and fails on 8952df0.
- The docstring and `CLAUDE.md:80` are true for every sub-case the fake can produce.
- The `first edit` row holds three reminders, so one delete row is cut off mid-way and resubmitted; the comment at `:460-461` is true.
- An eighth `/repo-intake` at the code PR's merge commit reports 0 tier 1, 0 tier 2, with G2 as its one listed bug (pre-existing, undecided).

### How to verify (end to end)
`tests/test_initdb.py` is the end-to-end check for G1: it runs the documented command the way the README says to, against a fresh file. After the merge: deploy green, machine `started` with its check passing, GET / 200.

## What We're NOT Doing

- G2 (decision 3). No README clause, no label read in the sweep, no `STATES` row with labels as an input.
- Any change to `sweep_reminders` or the webhook. The docstring edit is text inside the function; `git diff 8952df0 -- app.py` must show no change to a statement there.
- A migration tool. `CLAUDE.md:91` stays: the production constraint was added by hand, and `initdb` is for a fresh database.
- Extracting `initdb()` or adding a CLI (decision 1).
- Section 5 of the report (the two deletions), the opinions.
- A `FAULTS` entry for a refusal or an "applied, answer lost" request, a per-uuid refusal in the fake, or a third place in the table (report opinion 4): no state needs them yet.

## Rules for every phase

- One PR per phase, from a branch off current `master`. No attribution trailers.
- Every behaviour change lands with a test that fails on the code before the change; paste the failing run in the PR body (2.26). T1 is a test change, so its evidence is the before/after batch count, pasted too.
- `make check` before pushing.
- Merging deploys; the merge is Dan's: `gh pr merge <n> --repo dancwilliams/todoist-location-labels --squash --delete-branch`.
- The agent never reads, prints or types a secret. The subprocess test forces the four secret variables to dummies over whatever the shell exported.
- The convergence table stays the gate for any change to `sweep_reminders` (the sixth plan); this plan changes a row's size, not its source.

---

## Phase 1 (PR A): `initdb` under a context, the two sentences, the three-task row

### Changes Required

#### 1. `app.py:657-658`

```python
    if len(sys.argv) >= 2 and sys.argv[1] == "initdb":
        with app.app_context():  # Flask-SQLAlchemy 3 finds the engine through current_app
            db.create_all()
```

#### 2. `tests/test_initdb.py` (new)

```python
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app.py"
# Forced over the environment so a real value exported in the shell never reaches the subprocess.
DUMMIES = {
    key: "test"
    for key in (
        "TODOIST_FLASK_SECRET_KEY",
        "TODOIST_CLIENT_ID",
        "TODOIST_CLIENT_SECRET",
        "GOOGLE_MAP_API_KEY",
    )
}


def test_initdb_creates_the_schema_on_an_empty_database(tmp_path):
    """G1: the documented `python app.py initdb` must create the tables, unique
    constraint included. It ran create_all outside an app context and crashed."""
    db = tmp_path / "fresh.db"
    run = subprocess.run(
        [sys.executable, str(APP), "initdb"],
        cwd=APP.parent,
        env={**os.environ, **DUMMIES, "DATABASE_URL": f"sqlite:///{db}"},
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stderr
    (sql,) = sqlite3.connect(db).execute(
        "select sql from sqlite_master where name = 'location_label'"
    ).fetchone()
    assert "CONSTRAINT uq_location_label_user_label UNIQUE (user_id, label_id)" in sql
```

`DUMMIES` overrides the four secret variables whatever the shell exported (the conftest's `setdefault` at `tests/conftest.py:11-18` would keep a real one), `os.environ` keeps `PATH` and the coverage hook's variables, and `DATABASE_URL` points at the scratch file. `sys.executable` is the uv venv's interpreter under `uv run` (verified). On 8952df0 the assertion on `returncode` fails with the `RuntimeError` traceback in `run.stderr`. Expected cost: one interpreter start, well under a second.

#### 3. `app.py:340-341` (D1), the docstring's last sentence

```
    Returns the number of adds Todoist refused; raises RequestException on API failure
    or a refused delete, and the mapping must then stay where it was: a reminder may
    still be at the old place, or every one may have moved with the answer lost. The
    resubmit finishes the first and finds nothing to do for the second.
```

#### 4. `CLAUDE.md:80` (D2), the last two sentences replaced

"A sweep in which an add and a delete were both refused keeps the mapping; a task whose add was refused and whose delete went through holds nothing until its next change re-creates it, and the page's "submit again" cannot restore that one (if both refusals hit one task, its old reminder is still there and the resubmit moves it). Never observed; no fourth message for it."

#### 5. `CLAUDE.md:91`, append one clause

"… and `initdb` creates it on a fresh database (`tests/test_initdb.py` runs the command as a process; it is the only test of the `__main__` block)."

#### 6. `tests/test_routes.py:389-390` (T1)

```python
    # every edit; three tasks, so a delete spans two requests under SYNC_BATCH = 2
    "first edit": lambda old, new: [
        _rem("r1", "900", old),
        _rem("r2", "901", old),
        _rem("r3", "902", old),
    ],
```

Traced, not yet run: for the delete target, `second request dropped` now applies two deletes and drops the third → 502 `SWEEP_FAILED`, mapping kept, one reminder left → resubmit sends one delete → 302, none left. For the edit targets the sweep is three adds then three deletes, three requests; `second request dropped` applies the adds for 900 and 901 and drops the add for 902 with the delete of r1 → tasks 900 and 901 hold both, 902 holds the old → 502 → resubmit: keep 900's and 901's new, add 902's, delete r1, r2, r3 → converges. Nine cases change; `CASES` stays 45 and the ids do not change. No other test reads `STATES`.

### Success Criteria

#### Automated Verification
- [x] `tests/test_initdb.py` fails on 8952df0's `app.py` (`returncode` 1, `RuntimeError: Working outside of application context` in the pasted stderr) and passes after §1; output in the PR body
- [x] T1 evidence in the PR body: on 8952df0, `first edit / delete / second request dropped` answers 302 first time with `fake_todoist["batches"] == 1`; after §6 it answers 502 then 302 with batches 2 then 1 (a scratchpad run with the two values printed, or a temporary assertion, never committed)
- [x] `make check` exits 0; the test count is 98 + 1 = 99; `app.py` coverage not below 91%
- [x] `git diff 8952df0 -- app.py` shows exactly two hunks, the docstring (`:340-341`) and the `__main__` block (`:657-658`); `git diff 8952df0 -- app.py | grep -cE '^[-+].*\bexcept\b'` is 0; no hunk touches a statement of `sweep_reminders` or anything below `def webhook`
- [x] The documented command run by hand once, as the README says it, against a scratch file: `DATABASE_URL=sqlite:///<scratchpad>/x.db uv run python app.py initdb` exits 0 and `sqlite3 <file> .schema` (or the Python equivalent) shows both tables
- [x] CI green on the PR (3.13 and 3.14)
- [x] After Dan's merge: deploy job green; machine `started` with its check passing; `curl -s -o /dev/null -w '%{http_code}' https://todoist-location-labels.fly.dev/` prints 200

#### Manual Verification
- None. Nothing in this phase runs in production; the automated list above is the whole check. Dan may run the by-hand `initdb` line himself if he wants to see it, but the test is the proof.

**Result (2026-10-07)**: PR #53 squash-merged by Dan as 76be84c; CI run 37649637149 green on 3.13 and 3.14; deploy run 37653266884 green, Fly release v93, machine 91854667f4e938 `started` with `servicecheck-00-http-5000` passing, GET / 200. 99 tests, `app.py` 91%. One grill claim was wrong: pytest-cov does not count the subprocess's `__main__` lines (the installed `a1_coverage.pth` starts coverage only under `COVERAGE_PROCESS_START`/`COVERAGE_PROCESS_CONFIG`, which pytest-cov does not set); `app.py:659-663` stay listed as missed. Counting them would need coverage's `patch = ["subprocess"]`; not done, out of scope.

**Implementation Note**: no pause for manual steps. The phase ends at Dan's merge plus the deploy check (the last three boxes above). Phase 2 starts in a fresh session on Dan's go (`/clear`, then `/implement_plan plans/2026-10-07-seventh-intake-fixes.md`), never in the session that ran Phase 1.

---

## Phase 2 (PR B): Eighth intake

- Run `/repo-intake` at PR A's merge commit, scope `git diff 8952df0..HEAD` plus every caller and callee of `sweep_reminders` and the `__main__` block, per standards "How the audit applies this file" 6. The `improve` pass goes to a fresh agent given the code and not `plans/` or `thoughts/`, with the exclusion syntax outright (`git grep -n <pattern> -- . ':!plans' ':!thoughts'`), briefed as the seventh run's was (Phase 0's coordinate measurement, the 0 same-place pairs of 2026-10-05, per-command application, `reminder_delete` answering `ok` for an unknown id, the hand-made address measurement, the untouched catch tuples, the webhook out of scope), plus: G2 is known, pre-existing and undecided, to be listed and not re-derived. The brief keeps the state-walk form and the reproduced/hypothetical labelling with the script that produced each state.
- **2.12 is checked as amended on 2026-10-07** (decision 7): every documented command that needs no production credential is run, in a scratch directory, and listed with its exit code. Here: `cp .env.example .env` (in scratch), `uv run python app.py initdb` against a scratch SQLite, `make check`. Traced, not run, with the reason in the report: `uv sync` (the intake never installs or syncs; CI's clean-runner `uv sync --frozen` is its proof) and `uv run python app.py` (starts a server; traced to `app.run`).
- Expected: 0 tier 1, 0 tier 2; bugs: G2 only. If a row fails, STOP and report; do not fix inside the intake. If the failing row is a sweep state, it becomes a `STATES` row first.
- Commit `plans/intake-2026-10-<day>.md` and this plan's results in one docs-only PR.
- AutoMem: one pointer memory, linked to the seventh intake's memory (`7496a96d-9334-433d-951d-8b915153c626`).

### Success Criteria
- [x] Report written with a status for every row of `standards.md`, every documented command listed with its exit code, and a non-empty Not checked section
- [x] Verdict 0 tier 1, 0 tier 2 — or the failing rows listed here with the reason: **0 tier 1, 1 tier 2 (2.25)**. G2, known since the seventh run and deferred by decision 3, is named only in `plans/`; the row says "not only in a plan", issues are disabled, and no override sentence is in CLAUDE.md. The seventh report's 2.25 PASS had overlooked G2. Two new shipped defects, H1 (a second label can take a half-applied key and its sweep moves the first label's reminders) and H2 (a refused add plus a later dropped request loses that task's reminder and the resubmit answers 302), both pre-existing, both needing a never-observed dropped request, were first named in the report and add the by-construction component. 2.12 PASS as amended: `cp .env.example .env`, `initdb` and `make check` run with exit 0; `uv sync`, `uv run python app.py` and `fly deploy` traced with the reason. No code bug in #53; the three-task row converges under every target and fault, requests 3 and 4 dropped and answer-lost included (106 scratch cases + 45 table rows). Report: `plans/intake-2026-10-07-2.md`. Not fixed inside the intake; fix order 1 is Dan's choice (a README clause, a CLAUDE.md override, or the code fixes)
- [x] PR B merged by Dan; this plan's result lines written under each phase

**Result (2026-10-07)**: PR #54 squash-merged by Dan as 895d375; master run 37657785119 green (check 3.13, check 3.14, deploy); Fly release v94 (same code as v93), machine 91854667f4e938 `started` with `servicecheck-00-http-5000` passing, GET / 200. Verdict 0 tier 1, 1 tier 2 (2.25), bugs G2, H1, H2; report `plans/intake-2026-10-07-2.md`. This plan is closed; 2.25 and the two new bugs are the next plan's input, if Dan wants one. This tick sits uncommitted on master and rides in the next PR.

---

## Testing Strategy

- G1 is tested at the process boundary, the only place the bug exists: the test runs the interpreter on `app.py` with `initdb`, as the README tells a person to, and reads the schema back from the file. Nothing is mocked; the only substitution is a SQLite file for Postgres, and the failure is in Flask-SQLAlchemy before any engine is used, so the backend does not matter to the fail-first run.
- D1 and D2 are sentences; their truth was established by the intake's state walk (`scratchpad/test_audit_states.py`, test_C, test_D, test_E, 8 passed). They are not re-tested; the implementing session re-reads them against `app.py:364-376` before committing.
- T1 is a change to the table's inputs, checked by the table itself (the 45 cases still pass) and by the before/after batch counts in the PR body.
- The webhook suite is unchanged.

## Rollback

One deploy, no behaviour change in it. `fly releases -a todoist-location-labels --image` lists the image refs; `fly deploy --image <previous ref>` rolls back. A rollback would restore the broken `initdb`, which production never runs.

## References

- Seventh intake: `plans/intake-2026-10-07.md`, sections 1, 4 and 7
- Sixth plan (the sweep's invariant, the convergence-table rule, decision 7 on proven sources): `plans/2026-10-06-sixth-intake-fixes.md`
- Flask-SQLAlchemy 3.0 changelog, "An active Flask application context is always required to access session and engine": https://flask-sqlalchemy.readthedocs.io/en/stable/changes/#version-3-0-0
