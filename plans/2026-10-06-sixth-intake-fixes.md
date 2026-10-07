# Sixth-intake close-out: a convergent sweep, tested as an invariant — Implementation Plan

Source: `plans/intake-2026-10-06-3.md` (the sixth intake report; F numbers below refer to it).
Planned at commit 4ada2c5 (master, Fly release v89), 2026-10-06. One measurement, then three PRs to `master`, each merged by Dan on green CI. Merging deploys, so the code PR ends with a production check.

## Overview

Four rounds in a row patched one state of `sweep_reminders` and shipped the state next to it: #46 (adds-only refused saves the mapping) left B5; #49 (skip a re-add the task already has) made F1 and F2. The function is written as a script — delete what matched, add at the new place — and every fix adds an `if` for one more starting state. This plan rewrites it as a per-task reconciliation with one stated invariant, orders adds before deletes so any applied prefix is a state the next sweep converges from, and tests the invariant over a table of starting states against a fake that applies commands. A new state is then a new table row, not a new branch. Three PRs: the sixth report and this plan (docs), the code, the seventh intake.

## Decisions (Dan, 2026-10-06)

| # | Decision | Resolution |
|---|---|---|
| 1 | Rewrite vs. patch | **Rewrite** `sweep_reminders` as a reconciliation (about 40 lines in one function). The three-line patch the report sketched (dedupe only when the match key changes; pairs before lone deletes) would be the seventh special case |
| 2 | Command order | **Adds before deletes.** A task holds two reminders for the seconds between requests; accepted |
| 3 | Live read-back | **One scratch-task measurement** of what `loc_lat` / `loc_long` come back as through REST (Phase 0). The script reads the token from `~/.config/todoist-location-labels/token`; the agent never prints it |
| 4 | Webhook reconcile | **Stays as is** (`app.py:593-622`). A second implementation of "what should this task hold", stable since the third plan; folding it in doubles the diff |
| 5 | A reminder at the mapping's key with other coordinates (grill, Q1) | **The mapping is authoritative for its key**: the sweep replaces it with one at the mapping's coordinates. Keeping any key-matching reminder regardless of coordinates is F1 |
| 6 | Hand-made reminders (grill) | **Not a case.** Dan put a location reminder on a task by hand, then mapped the same address and tagged the task: Todoist stored `202 South 4th Street, Lumberton, MS, USA`, the app `202 S 4th St, Lumberton, MS, USA` (screenshot, 2026-10-06). Todoist's geocoder and Google Places format the same address differently, so a hand-made reminder never shares a key with a mapping. Nothing is designed, tested or documented for it; `CLAUDE.md:76`'s "hand-made" sentence is replaced by this fact |
| 7 | Which states the table holds (grill, Q5) | **Only states with a proven source** — reproduced through an app path (stub, test or production log). Dropped: a duplicate at the old place (only B1/B5 before their fixes could make one; the new order cannot), the hand-made rows, and the refused-delete fault (no delete refusal has ever been measured; `reminder_delete` answers `ok` for an unknown id; the state it would leave is the one a dropped delete request leaves, which stays). The coordinate-only target stays: not an assumed event but an accepted input, since the route sweeps on any of the five values (`app.py:505`) |
| 8 | Refused adds and the invariant (grill, Q3) | The invariant excepts a task whose add Todoist refused: it comes back on its next task change. The #46 rule is unchanged; a mixed refusal (an add and a delete in one sweep) keeps the mapping and leaves the refused-add task empty until its next change, documented, no fourth message |
| 9 | F3 (grill, Q6) | One README sentence, not called a bug: the page asks for the same change, and a different one strands the first one's reminders |

Settled from evidence, not asked:
- **The stateful fake is in.** The fourth and fifth plans each listed it under What We're NOT Doing; the result was two rounds of tests that could only assert what was sent, never what a task ended with. Every one of B1, B5, F1 and F2 is a wrong end state.
- **`match_key` stays (name, trigger, radius)** for "belongs to this mapping": it is what the same-key guard at `app.py:490-498` and the webhook rely on. Exactness (coordinates included) is a second predicate used only to choose which reminder an edit keeps.
- **F3 is a README sentence**, not code: a resubmit to a third place strands the reminders at the second, and without stored reminder ids no sweep can find them. The page already asks for "the same change" (`app.py:103`).
- **The `sweep matched N of M` line keeps its name**; Dan's manual check reads it. `M` is still every location reminder the account holds; `N` now counts reminders at the old place *or* the new one, which is the same number on a normal edit and larger on a resubmit.

## Current State Analysis

- `app.py` is 634 lines at 4ada2c5. Read it, `tests/conftest.py`, `tests/test_routes.py`, `tests/test_webhook.py` and `tests/test_todoist_client.py` before touching anything.
- `sweep_reminders` (`app.py:312-350`): reads every reminder (`:321`), matches the old place by `reminder_is_at` (`:322`), builds `already` with the same predicate against the new place (`:328-330`), then per matched reminder appends a delete and, unless the task is in `already`, an add (`:331-334`). Returns the refused-add count or raises (`:337-350`).
- `reminder_is_at` (`:298-309`) compares `match_key` only: (name, trigger, radius). Coordinates are not compared anywhere. The REST reminder carries `loc_lat` and `loc_long` (read live 2026-10-05, third plan, "Facts measured"); their type on read-back was not recorded, which is Phase 0.
- `place_of` (`:277-290`) returns `(name, lat, long, trigger, radius)` with lat/long as the `Float` columns.
- The route sweeps whenever `place_of(location_label) != new_place` (`:505`), coordinates included; a coordinate-only edit therefore sweeps with `match_key(old) == match_key(new)`, which is F1 (stub scenario D: `302`, `reminders={}`).
- `todoist_run_commands` (`:253-274`) sends every batch of `SYNC_BATCH` (100, `:95`), raises `RequestException` on a malformed answer and `TodoistRefused` after all batches if any status is not `ok`.
- `fake_todoist` (`tests/conftest.py:100-177`) is stateless: `do_GET` answers `state["reminders"]` as configured (`:137-144`), `do_POST` records commands and refuses by type without touching the list (`:146-160`). `state["status"]` and `state["stall"]` apply to every reply.
- Tests asserting the sweep's command order: `test_editing_a_mapping_moves_its_reminders` (`tests/test_routes.py:185-215`, `[delete, add]`), `test_refused_readd_during_edit_saves_the_mapping` (`:291-302`, `[delete, add] * 2` with `SYNC_BATCH=2`), `test_resubmitted_edit_does_not_double_a_moved_reminder` (`:305-332`, `[delete, delete, add]` and the `re-adds skipped` log line). All three change under decision 2.
- Docs that describe the sweep: `README.md:16`; `CLAUDE.md:19` (Data Flow 5), `:76` (linked by value), `:78` (log lines), `:81` ("each pair in the same request").
- Uncommitted in the tree: `plans/intake-2026-10-06-3.md`, this plan, the fifth plan's Phase 2 close-out, one line in the fourth plan.
- Tests: 51, `app.py` 91%. CI: `make check` on 3.13 and 3.14, deploy on push to master.

## Desired End State

- **The invariant.** After `sweep_reminders(token, old, new)`, every task that held a reminder matching the mapping — at `old` or at `new` by `match_key` — holds exactly one reminder, exactly at `new` (all five values), and none other matching either place; for `new=None`, it holds none. The one exception is a task whose add Todoist refused: it holds none until its next task change, when the webhook re-creates it. This is reached from any starting state the app can produce, so a resubmit of the same change converges, and a run cut off at any batch leaves a state the next run converges from.
- The invariant is a parametrized test over a table of proven starting states × targets × faults, against a fake that applies commands. Every row passes; the rows for F1 and F2 fail on 4ada2c5.
- `README.md` and `CLAUDE.md` say what the sweep guarantees and the one thing it cannot (F3).
- A seventh `/repo-intake` at the code PR's merge commit reports 0 tier 1, 0 tier 2.

### How to verify (end to end, after the code PR deploys)
Dan's steps 2 to 5 of the third plan's "How to verify", on https://todoist-location-labels.fly.dev/, plus one coordinate-only edit (Phase 1, Manual Verification, spelled out there). The agent reads `fly logs -a todoist-location-labels --no-tail` after: no traceback, no `failed`, `refused`, `redelivery`, `WARNING` or `ERROR` line from the app.

## What We're NOT Doing

- Touching the webhook's reconcile (decision 4), `todoist_run_commands`, `todoist_get_all`, the routes' paths, methods or status codes, or the #46 rule (adds-only refused ⇒ mapping saved).
- Storing reminder ids. F3 would need them; the README sentence is the fix.
- Section 5 of the report (the two deletions), opinions 2 to 4.
- A tolerance for coordinates beyond what Phase 0 measures.
- Any state, row, branch or sentence for a case without a proven source (decisions 6 and 7): duplicates, hand-made collisions, refused deletes beyond the one existing test.
- Changing the webhook's `fake_todoist` behaviour beyond what applying commands implies; its tests read before they write and keep passing (checked in Phase 1).

## Rules for every phase

- One PR per phase, from a branch off current `master`. No attribution trailers.
- Every behaviour change lands with a test that fails on the code before the change; paste the failing run in the PR body (2.26).
- `make check` before pushing.
- Merging deploys; the merge is Dan's: `gh pr merge <n> --repo dancwilliams/todoist-location-labels --squash --delete-branch`.
- The agent never reads, prints or types a secret. Phase 0's script reads the token file itself and prints reminder fields only.
- **The convergence table is the gate for any change to `sweep_reminders` from now on.** A new starting state becomes a new row before any code changes — and a row needs a proven source: reproduced through an app path (a stub, a test, a production log line), never reasoned. A change that needs a row deleted is a design change and goes back to a plan. A state nobody can produce is reported as hypothetical and not designed for (Dan, 2026-10-06).

---

## Phase 0: measure the coordinate read-back, then land the docs (PR 0)

### 0a. Live read-back (no PR; result recorded here)

Script written by the implementing session to the scratchpad. It reads the token from `~/.config/todoist-location-labels/token`, and against the live API: `item_add` one scratch task; `reminder_add` two location reminders on it, `loc_lat`/`loc_long` sent as the app sends them (`str(float)`): `("1.5", "2.5")` and `("32.776664", "-96.796988")`; `GET /api/v1/location_reminders?task_id=<id>`; print for each reminder `type(r["loc_lat"]).__name__`, `repr(r["loc_lat"])`, `repr(r["loc_long"])`, `repr(r["radius"])`, and `float(r["loc_lat"]) == 1.5` / `== 32.776664`; then `reminder_delete` both and `item_delete` the task. Nothing else is printed.

Decision rule, pre-stated:
- **Round-trips exactly** (both comparisons `True`, whether the type is `str` or `float`): `coordinate_of(value)` is `float(value)` and exactness is `==` on floats. Python's `str(float)` → `float(str)` is exact, so a mapping's floats equal its own reminder's.
- **Does not round-trip** (Todoist rounds or reformats): record the decimals it keeps as `D`; `coordinate_of` rounds to `D`, `place_of` rounds lat/long to `D`, **and the route rounds `lat` and `long` to `D` where it parses them** (`app.py:471-472`), or the unchanged check at `:505` would compare a rounded `place_of` with an unrounded `new_place` and sweep on every resubmit of identical values. Same approach as the radius and the name (`:277-290`). The convergence rows still hold, since the fake stores what Phase 0 measured.
- **Either branch:** `test_resubmitting_unchanged_values_touches_nothing` (`tests/test_routes.py:218-227`) gains a case that submits the mapping's values with a 10-decimal coordinate (`lat="1.0000000001"`) after a first submit of the same, asserting the second one makes no Todoist call: a resubmit of what the form last accepted is a no-op.
- **A non-numeric or missing value** on a real reminder (`coordinate_of` returns `None`) counts as not exactly at the place: it is deleted and replaced, never kept.

- [x] Script run; the four printed lines and the branch taken recorded here, in this plan, with the date. Scratch task and reminders deleted (the script's last two calls answer `ok`). **Result (2026-10-06, `scratchpad/phase0_readback.py`):** `item_add` ok; `reminder_add` ok, ok; read back 2: `loc_lat str '1.5', loc_long str '2.5', radius 100, float(loc_lat)==sent True, float(loc_long)==sent True` and `loc_lat str '32.776664', loc_long str '-96.796988', radius 100, True, True`; cleanup `reminder_delete` ×2 and `item_delete` all ok. **Round-trip branch:** coordinates are returned as strings exactly as sent; `coordinate_of` is `float(value)`, no rounding, no change to `place_of` or the route. The fake's `_stored` keeps `loc_lat`/`loc_long` as the strings the command carried.

### 0b. PR 0: the sixth report and the plans

Docs only: `plans/intake-2026-10-06-3.md`, this plan, `plans/2026-10-06-fifth-intake-fixes.md` (Phase 2 close-out), `plans/2026-10-06-fourth-intake-fixes.md` (one line). No code. Lands master's record of the state before the code changes.

- [x] PR 0 opened; CI green (docs-only still runs `check`); merged by Dan (#50 merged as 9d74b0e, 2026-10-06; run 37553999329 check 3.13, 3.14 and deploy green — a docs-only deploy, Fly v90 — GET / 200)

---

## Phase 1 (PR A): the convergent sweep, the stateful fake, the invariant table

### Changes Required

#### 1. `app.py`: exactness (after `reminder_is_at`, `:298-309`)

```python
def coordinate_of(value):
    """A reminder's loc_lat or loc_long as a number, or None if it is not one."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def reminder_is_exactly_at(reminder, place):
    """reminder_is_at, and the coordinates agree: the one reminder an edit keeps."""
    return reminder_is_at(reminder, place) and (
        coordinate_of(reminder.get("loc_lat")),
        coordinate_of(reminder.get("loc_long")),
    ) == (place[1], place[2])
```

If Phase 0 took the rounding branch, `coordinate_of` returns `round(float(value), D)` and `place_of` returns `round(location_label.lat, D)` and `round(location_label.long, D)`, with `D` a named module constant beside `MAX_RADIUS` carrying the measurement date.

#### 2. `app.py`: `sweep_reminders` (`:312-350`), replaced whole

```python
def sweep_reminders(token, old_place, new_place=None):
    """Bring every task holding one of a mapping's reminders to exactly one at new_place.

    Invariant, from any starting state: a task that holds a reminder matching the
    mapping, at the old place or the new one, ends with exactly one reminder, exactly
    at the new place, and no other at either; for a delete (new_place None), with none.
    Adds are sent before deletes, so whatever prefix Todoist applied, every task still
    holds a reminder and the next sweep converges. A resubmit is therefore always safe.
    Returns the number of adds Todoist refused; raises RequestException on API failure
    or a refused delete, in which case a reminder is still at the old place.
    """
    located = todoist_get_reminders(token)
    by_task: dict[str, list[dict]] = {}
    for reminder in located:
        if reminder_is_at(reminder, old_place) or (
            new_place is not None and reminder_is_at(reminder, new_place)
        ):
            by_task.setdefault(reminder["item_id"], []).append(reminder)
    matched = sum(len(reminders) for reminders in by_task.values())
    # Zero of many is how a reminder that no longer matches its mapping shows up.
    app.logger.info("sweep matched %d of %d location reminders", matched, len(located))
    adds, deletes = [], []
    for item_id, reminders in by_task.items():
        keep = None
        if new_place is not None:
            keep = next((r for r in reminders if reminder_is_exactly_at(r, new_place)), None)
            if keep is None:
                adds.append(reminder_add_command(item_id, *new_place))
        deletes.extend(reminder_delete_command(r["id"]) for r in reminders if r is not keep)
    if new_place is not None and (kept := len(by_task) - len(adds)):
        app.logger.info("sweep: %d tasks already at the new place", kept)
    try:
        todoist_run_commands(token, adds + deletes, "sweep")
    except TodoistRefused as e:
        if not set(e.refused) <= {c["uuid"] for c in adds}:
            raise
        # Every delete went through, so nothing is left at the old place and the
        # mapping must move with the reminders that did: kept at the old place,
        # it would no longer match them, the webhook would add a second reminder
        # at the old place when such a task changes, and a resubmit would then
        # leave that task with two at the new place.
        app.logger.warning("sweep: %d adds refused, mapping saved: %s", len(e.refused), e)
        return len(e.refused)
    return 0
```

(ruff may re-wrap; keep the names.) Traced against the report's stub scenarios: C (refused delete, webhook, resubmit) → task 900 keeps Oak, deletes Home; 901 keeps Oak, deletes the webhook's Home. D (coordinate-only) → no reminder is exactly at the new place, so each task gets an add and a delete: `{'900': ['Home@9.0'], '901': ['Home@9.0']}`. B (duplicate) → one add, two deletes. E (batch failure) → adds went first, so the task whose later batch failed holds both; the resubmit keeps the new one. A (third place) → unchanged, F3. `test_resubmitting_unchanged_values_touches_nothing` is unaffected: the route still skips the sweep when the five-tuple is equal (`:505`).

The `already` set, the `re-adds skipped` line and the per-reminder loop go. The ADDS_REFUSED path (`:339-349`) keeps its rule and its comment; only the uuid set is now `adds`.

#### 3. `tests/conftest.py`: the fake applies commands (`:146-160`)

`do_POST` becomes

```python
        def do_POST(self):
            state["hits"] += 1
            raw = self.rfile.read(int(self.headers["Content-Length"])).decode()
            form = urllib.parse.parse_qs(raw)
            state["posts"].append(sorted(form))
            commands = json.loads(form.get("commands", ["[]"])[0])
            state["commands"].extend(commands)
            state["batches"] += 1
            if state["batches"] in state["drop_batches"]:
                self.close_connection = True  # no reply: a connection error, not retried
                return
            status = {}
            for c in commands:
                if c["type"] in state["refuse"]:
                    status[c["uuid"]] = REFUSED
                    continue
                if c["type"] == "reminder_add":
                    state["reminders"].append(_stored(c["args"], f"f{len(state['commands'])}"))
                elif c["type"] == "reminder_delete":
                    state["reminders"] = [
                        r for r in state["reminders"] if r["id"] != c["args"]["id"]
                    ]
                status[c["uuid"]] = "ok"
            self._reply({"sync_status": status})
```

with `state["batches"] = 0` and `state["drop_batches"] = ()` added to the dict, and at module level

```python
def _stored(args, reminder_id):
    """What a reminder_add leaves in the account, as the REST read returns it (Phase 0)."""
    return {
        "id": reminder_id,
        "type": "location",
        "item_id": args["item_id"],
        "name": args["name"],
        "loc_lat": args["loc_lat"],   # or float(args["loc_lat"]) / rounded, per Phase 0
        "loc_long": args["loc_long"],
        "loc_trigger": args["loc_trigger"],
        "radius": args["radius"],
    }
```

The fake applies what Todoist applies: `ok` commands change the list, refused ones do not, a dropped batch changes nothing. The docstring (`:101-109`) gains a sentence saying so and naming `drop_batches`. `HOME_REMINDER` and `OTHER_REMINDER` gain `loc_lat`/`loc_long` in the measured type (`"1.0"`, `"2.0"`; `OTHER_REMINDER` keeps Home's coordinates, which is fine, its name differs).

Dropping the connection instead of answering 503: the `Retry` has `read=0` (`app.py:122`), so a reply that never comes is a `ConnectionError` at once, one POST per batch, and `state["batches"]` counts batches and not retries. A 503 would be retried three times and the counter would drift. Checked in `test_a_dropped_batch_is_one_post` below.

#### 4. `tests/test_routes.py`: the invariant table

New module-level data and one parametrized test; the three order-dependent tests updated.

```python
HOME = ("Home", 1.0, 2.0, "on_enter", 100)          # the `user` fixture's mapping
OAK = ("2 Oak Ave", 1.5, 2.5, "on_enter", 100)      # a new name
HOME_MOVED = ("Home", 9.0, 2.0, "on_enter", 100)    # same key, other coordinates (F1)
ELSEWHERE = ("Somewhere else", 1.0, 2.0, "on_enter", 100)  # another mapping's key


def _rem(reminder_id, item_id, place):
    name, lat, long, trigger, radius = place
    return {"id": reminder_id, "type": "location", "item_id": item_id, "name": name,
            "loc_lat": str(lat), "loc_long": str(long), "loc_trigger": trigger, "radius": radius}


# Each state is what the account holds before the submit, written against the old
# place and the target so every row also runs as a half-applied coordinate-only edit.
# Every row has a proven source (the sixth plan, decision 7); a new row needs one too.
STATES = {
    # every edit
    "first edit": lambda old, new: [_rem("r1", "900", old), _rem("r2", "901", old)],
    # a sweep cut off after its adds (stub C, E): one task holds both, one the new only
    "half applied": lambda old, new: [_rem("r1", "900", old), _rem("r2", "900", new), _rem("r3", "901", new)],
    # the above, then task 901 changed before the resubmit, so the webhook put the old place back (stub C)
    "half applied, webhook re-added the old place": lambda old, new: [
        _rem("r1", "900", old), _rem("r2", "900", new), _rem("r3", "901", new), _rem("r4", "901", old)],
    # the above with two tasks not yet moved (stub E, F2)
    "half applied, two still to move": lambda old, new: [
        _rem("r1", "900", old), _rem("r2", "900", new), _rem("r3", "901", old), _rem("r4", "902", old)],
    # a resubmit after a saved sweep (for a delete: nothing left at the old place)
    "already converged": lambda old, new: [_rem("r1", "900", new)] if new else [],
    # another mapping's reminder on the same task is not this mapping's
    "another mapping's reminder": lambda old, new: [_rem("r1", "900", old), _rem("rx", "900", ELSEWHERE)],
}
TARGETS = {"new name": OAK, "coordinates only": HOME_MOVED, "delete": None}
FAULTS = {
    "none": (),
    "first request dropped": (1,),   # nothing applied
    "second request dropped": (2,),  # the first batch applied, the rest not (F2)
}


def _key(r):
    return (r["name"], r["loc_trigger"], r["radius"])


def _assert_converged(before, after, old, target):
    """The invariant: every task that held one of the mapping's reminders holds exactly
    one, exactly at the target, or none for a delete; everything else is untouched."""
    keys = {_key_of(old)} | ({_key_of(target)} if target else set())
    tasks = {r["item_id"] for r in before if _key(r) in keys}
    for task in tasks:
        have = [r for r in after if r["item_id"] == task and _key(r) in keys]
        if target is None:
            assert have == [], (task, have)
        else:
            assert len(have) == 1, (task, have)
            r = have[0]
            assert (r["name"], float(r["loc_lat"]), float(r["loc_long"]), r["loc_trigger"], r["radius"]) == target
    assert [r for r in after if _key(r) not in keys] == [r for r in before if _key(r) not in keys]


def _key_of(place):
    return (place[0], place[3], place[4])


# A half-applied *edit* followed by a delete is F3 (a different change), documented and
# not a row; a half-applied delete is "first edit" with a request dropped.
CASES = [
    (state, target)
    for state in STATES
    for target in TARGETS
    if TARGETS[target] is not None or not state.startswith("half applied")
]


@pytest.mark.parametrize("fault", FAULTS, ids=FAULTS)
@pytest.mark.parametrize("state, target", CASES, ids=[f"{s} / {t}" for s, t in CASES])
def test_sweep_converges_from_any_state(client, login, fake_todoist, monkeypatch, state, target, fault):
    """F1, F2, B5, B1: whatever a task holds and wherever a sweep was cut off, submitting
    the change (again) ends with exactly one reminder per task at the new place, or none."""
    place = TARGETS[target]
    before = STATES[state](HOME, place)
    fake_todoist["reminders"] = [dict(r) for r in before]
    monkeypatch.setattr(app_module, "SYNC_BATCH", 2)  # so a sweep is several requests
    if place is None:
        submit = lambda: client.post(f"/delete_label_location/{_mapping_id()}")
    else:
        name, lat, long, trigger, radius = place
        submit = lambda: client.post("/create_label_location", data=_form(
            label_id="10", address=name, lat=str(lat), long=str(long), trigger=trigger, radius=str(radius)))
    fake_todoist["drop_batches"] = FAULTS[fault]
    r = submit()
    if r.status_code == 502:  # a sweep too short to reach the fault, or with nothing to send, succeeds
        assert "Submit the same change again" in r.get_data(as_text=True)
        assert app_module.LocationLabel.query.filter_by(label_id=10).one().name == "Home"
    else:
        assert r.status_code == 302
    fake_todoist["drop_batches"] = ()
    r = submit()  # the resubmit the page asks for; a no-op if the first one was saved
    assert r.status_code == 302
    _assert_converged(before, fake_todoist["reminders"], HOME, place)
    if place is not None:
        row = app_module.LocationLabel.query.filter_by(label_id=10).one()
        assert (row.name, row.lat, row.long, row.loc_trigger, row.radius) == place
    else:
        assert app_module.LocationLabel.query.count() == 0
```

Six states for each edit target, three for the delete, × three faults = 45 cases (each starts the fake's HTTP server; the suite stays under a few seconds). `_key`, `_key_of` and the exactness line in `_assert_converged` are written in the test, not imported from `app`: the expected values must not come from the code under test (2.26). Under the `coordinates only` target the half-applied rows hold `Home@9.0` reminders beside `Home@1.0` ones — the same key — which is what pins decision 5 and the exactness predicate: a key-only "already there" check keeps the wrong one or deletes both.

Two small tests beside it:

| Test | Protects | Fails on 4ada2c5 with |
|---|---|---|
| `test_a_dropped_batch_is_one_post`: `fake_todoist["reminders"] = [HOME_REMINDER]`, `drop_batches = (1,)`, delete the mapping → 502, `fake_todoist["batches"] == 1`, `hits == 2` (one GET, one POST) | the fixture's fault is a connection error that the `Retry` does not retry, so a batch count means batches | n/a: tests the fixture, written with it |
| `test_sweep_sends_adds_before_deletes`: `fake_todoist["reminders"] = [HOME_REMINDER, dict(HOME_REMINDER, id="r3", item_id="902")]`, `SYNC_BATCH = 2`, edit label 10 to `2 Oak Ave` → the sent types are `["reminder_add", "reminder_add", "reminder_delete", "reminder_delete"]` | decision 2, the property F2's fix rests on | `['reminder_delete', 'reminder_add', ...]` |

Updated tests (behaviour change, intentional): `test_editing_a_mapping_moves_its_reminders` expects `[add, delete]` (`:200-214`); `test_refused_readd_during_edit_saves_the_mapping` expects `["reminder_add"] * 2 + ["reminder_delete"] * 2` (`:300`); `test_resubmitted_edit_does_not_double_a_moved_reminder` expects `[add 902, delete r1, delete r3]` and the log line `sweep: 1 tasks already at the new place` (`:316-332`). Their docstrings stay.

Rows that fail on 4ada2c5 (paste in the PR body): every `coordinates only` row with a reminder at `Home@1.0` (F1: 302 with no reminder left, or the wrong one kept), `half applied, two still to move / new name / second request dropped` (F2: on 4ada2c5 the list is `[del r1, del r3, add 901, del r4, add 902]`, the second request `[add 901, del r4]` is dropped, and after the resubmit task 901 holds nothing; on the new code the first request is the two adds, so every task still holds a reminder), plus the three updated order assertions. Traced here, not yet run: the implementing session pastes the actual run.

#### 5. Docs

- `README.md:16`: "Changing a mapping moves each of its reminders to the new place, and deleting it removes them. If Todoist refuses part of a change, the page says so: either the change is saved and a missing reminder comes back the next time its task changes, or the page asks you to submit the same change again. Submitting it again is always safe. Submitting a *different* change instead leaves the reminders the first one made at a place no label maps any more; remove those by hand in Todoist."
- `CLAUDE.md:19` (Data Flow 5): replace the paragraph with the invariant: "Editing or deleting a mapping in the UI sweeps Todoist first (`sweep_reminders`): every task holding a reminder that matches the mapping, at the old place or the new one, ends with exactly one reminder exactly at the new place (coordinates included), or none for a delete. Adds are sent before deletes, so whatever prefix Todoist applied, every task still holds a reminder and the next sweep converges; a resubmit of the same change is always safe. If Todoist cannot be reached, answers in an unexpected shape, or refuses a delete, the request answers 502 with a sentence saying to submit the change again, and the mapping is left as it was. If only adds were refused, every delete went through, so the mapping is saved at the new place and the 502 says each missing reminder comes back the next time its task changes. A resubmit to a *different* place strands the reminders at the half-applied one (no mapping matches them, the app stores no reminder ids); the README says so."
- `CLAUDE.md:76`: replace "a reminder the user made by hand with identical values is treated as the app's" with "a reminder made by hand in Todoist never shares a key with a mapping: Todoist's geocoder formats the same address differently from Google Places (`202 South 4th Street` against `202 S 4th St`, measured 2026-10-06), so the app and the user can each hold a reminder for one address on one task and neither touches the other's". Add "`reminder_is_exactly_at` adds the coordinates (`coordinate_of`: `float`, measured to round-trip exactly on 2026-10-06 — or the rounding rule from Phase 0) and decides only which reminder an edit keeps; the mapping is authoritative for its key, coordinates included."
- `CLAUDE.md` `todoist_run_commands` bullet (`:80`), append: "A sweep in which an add and a delete were both refused keeps the mapping, and the refused-add task holds nothing until its next change re-creates it; the page says to submit again, which cannot restore that one. Never observed; no fourth message for it."
- `CLAUDE.md:78`: "`sweep_reminders` logs `sweep matched N of M location reminders`, N counting reminders at the old place or the new one. Zero of many on an edit or delete of a mapping that has tagged tasks means matching is broken again. It logs `sweep: K tasks already at the new place` when a resubmit found some."
- `CLAUDE.md:81`: "An edit therefore deletes and re-adds, all adds first and then all deletes, 100 commands per request (`SYNC_BATCH`); an edit of more than 50 tagged tasks is several requests, and a failure in a later one leaves tasks holding both reminders, which the resubmit resolves."
- `CLAUDE.md` Development Notes, new bullet: "`fake_todoist` applies commands: `ok` adds and deletes change its reminder list the way Todoist's do, refused ones do not, and `drop_batches` makes the n-th sync request a connection error. `test_sweep_converges_from_any_state` is the gate for any change to the sweep: a new starting state is a new row in `STATES` before the code changes, and a row needs a proven source (reproduced through an app path, never reasoned); a state nobody can produce is not designed for."
- `README.md:15` (the hand-made bullet): "A reminder you add by hand in Todoist is yours: Todoist writes the address differently from this app, so the two never match" — replacing the current "is treated as the app's" sentence, which decision 6 showed to be false in practice.

### Success Criteria

#### Automated Verification
- [x] The rows and assertions named in §4 fail on 4ada2c5 for the stated reasons; output in the PR body (2026-10-06: 23 failed, 50 passed in `tests/test_routes.py` with 4ada2c5's `app.py` under the new tests — all 18 `coordinates only` rows, `half applied, two still to move / new name / second request dropped`, the three updated order assertions and `test_sweep_sends_adds_before_deletes`; the F1 and F2 rows both fail at `_assert_converged` with `('901', [])`, task 901 holding nothing)
- [x] `make check` exits 0; `app.py` coverage not below 91%; the test count is 51 + 45 + 2 = 98 (98 passed, 91%; the new uncovered lines are `coordinate_of`'s `except`, which has no proven-source row)
- [x] `git diff 4ada2c5 -- app.py | grep -cE '^[-+].*\bexcept\b'` is 0 (catch tuples untouched) — counts 1, which is §1's own `coordinate_of` clause; `^-.*\bexcept\b` counts 0, so no existing clause changed
- [x] `grep -n 'def webhook' -A 100 app.py` is byte-identical to 4ada2c5's (`git diff 4ada2c5 -- app.py` shows no hunk below the `@app.route("/webhook")` line) — decision 4 (one hunk, `-309,43 +309,69`)

Deviations from the §3/§4 sketches, all in the tests: (a) the fake names a new reminder by the command's `temp_id`, not `f{len(commands)}` — the latter is the same for every add in one batch, so one delete would have removed both; (b) the test resubmits only after a 502: a delete that answered 302 has removed the row, so there is nothing to resubmit, and a saved edit's no-op resubmit is `test_resubmitting_unchanged_values_touches_nothing`; (c) in `test_resubmitted_edit_does_not_double_a_moved_reminder`, task 900's moved reminder carries the new coordinates (`1.5`, `2.5`), since the kept reminder must be exactly at the new place. Phase 0's "either branch" case (a resubmit with `lat="1.0000000001"` makes no Todoist call) landed here too, inside that test, so the count stays 98.
- [x] CI green on the PR (3.13 and 3.14) (PR #51, run 37556508333, both pass)
- [ ] After Dan's merge: deploy job green; machine `started` with its check passing; `curl -s -o /dev/null -w '%{http_code}' https://todoist-location-labels.fly.dev/` prints 200

#### Manual Verification
- [ ] Steps 2 to 5 of the third plan's "How to verify": map a label at radius 100, tag a task (reminder appears; log `reminder_add sync result` ok), edit to 255 (`sweep matched 1 of N`, two ok), edit back to 100 (same), delete (`sweep matched 1 of N`, one ok)
- [ ] One coordinate-only edit, between the radius edits: with the mapping at radius 100 and the task tagged, open the page, press F12, in the Console run `document.getElementById('lat').value = (parseFloat(document.getElementById('lat').value) + 0.001).toFixed(6)` after re-picking the same address from the suggestions (the form refuses a submit with no suggestion picked), leave every other field as it was, press **Add**. Expected: the page reloads with the mapping listed once; the log shows `sweep matched 1 of N` and a `sweep sync result` with two `ok`; in the Todoist app the task has exactly one location reminder (open the task, the reminder row shows the address). On 4ada2c5 this step would log `sweep: 1 re-adds skipped` and the task would have no reminder
- [ ] The agent reads the Fly log: no traceback, no `failed`, `refused`, `redelivery`, `WARNING` or `ERROR` line from the app

**Implementation Note**: stop after this phase until Dan confirms the manual steps.

---

## Phase 2 (PR B): Seventh intake

- Run `/repo-intake` at PR A's merge commit, scope `git diff <PR 0 merge>..HEAD` plus every caller and callee of `sweep_reminders`, per standards "How the audit applies this file" 6. The `improve` pass goes to a fresh agent given the code and not `plans/`, with the exclusion syntax outright (`git grep -n <pattern> -- . ':!plans'`), briefed with: Phase 0's measurement, the 0 same-key pairs (2026-10-05), Todoist applying each command on its own, `reminder_delete` answering `ok` for an unknown id, the hand-made measurement of 2026-10-06 (Todoist and Google format one address differently), the untouched catch tuples, the webhook deliberately out of scope. **The brief asks for a state walk, not only a named interaction**, names the identity case explicitly (an edit where `match_key(old) == match_key(new)`), and **requires every state it walks to be labelled reproduced or hypothetical** with the script or log that reproduced it; a hypothetical state is reported, not fixed, and is not a table row.
- Expected: 0 tier 1, 0 tier 2. If a row fails, STOP and report; do not fix inside the intake. If the failing row is a sweep state, it becomes a `STATES` row first.
- Commit `plans/intake-2026-10-<day>.md` and this plan's results in one docs-only PR.
- AutoMem: one pointer memory, linked to the sixth intake's memory (`a1fa3659-e7df-4504-8b02-45801cf3af36`).

### Success Criteria
- [ ] Report written with a status for every row of `standards.md` and a non-empty Not checked section
- [ ] Verdict 0 tier 1, 0 tier 2 — or the failing rows listed here with the reason
- [ ] PR B merged by Dan; this plan's result lines written under each phase

---

## Testing Strategy

- The sweep is tested as an invariant over proven starting states, targets and faults, against a fake that applies commands. The stub scenarios of the fifth and sixth reports (`probe_sweep.py`, `probe_sweep6.py`) are superseded by `STATES`: C, D and E are rows; A is F3 (documented, not a row); B (a duplicate) has no source the new code can produce and is not a row (decision 7).
- What the fake cannot show: what Todoist stores for a coordinate. That is Phase 0's one live measurement, and `_stored` in the fake mirrors it. Any later change to what is sent for a coordinate needs a new read-back, as `CLAUDE.md:77` already says for the radius and the name.
- Nothing sent to Todoist changes in kind; the order changes (adds first) and is what the production run exercises.
- The webhook suite is unchanged and must stay green with the applying fake: every webhook test reads before it writes, so the list it reads is the one it configured.

## Rollback

One deploy. `fly releases -a todoist-location-labels --image` lists the image refs; `fly deploy --image <previous ref>` rolls back. The data in Todoist is unaffected by a rollback: 4ada2c5's sweep converges from any state the new one leaves (an extra reminder at the new place is "already at the new place" to it), except that a coordinate-only edit on the old code would again delete (F1), which the README of PR 0 does not promise against.

## References

- Sixth intake: `plans/intake-2026-10-06-3.md`, sections 4, 7 and 8
- Fifth and fourth plans (the two rounds this replaces): `plans/2026-10-06-fifth-intake-fixes.md`, `plans/2026-10-06-fourth-intake-fixes.md`
- Third plan, "Facts measured against the live API on 2026-10-05" and the token-file rule: `plans/2026-10-05-third-intake-fixes.md:30-36`, `:80`
- PR #46 (the adds-only-refused rule, unchanged): https://github.com/dancwilliams/todoist-location-labels/pull/46
