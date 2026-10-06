# Third-intake fixes: a Todoist failure is a failure — Implementation Plan

Source: `plans/intake-2026-10-05.md` (the third intake report; D, C and rule IDs below refer to it).
Planned at commit 721300a (master, Fly release v80), 2026-10-05; revised the same day after a grill-me interview. Each phase is one PR to `master`, merged only on green CI. Deploy is automatic on merge, so every phase ends with a production check.

## Overview

The third intake found the repo three tier 2 rows short of the fold (2.7, 2.12, 2.25) and four bugs. Most of it is one theme: what the app does when Todoist does not do what it was asked. A command Todoist refuses is logged and reported as done (D1), a connect stall is retried past the worker timeout (D3), a half-applied sweep is reported with no hint to retry (D4), and two mappings at one place undo each other's work (D2). This plan fixes those, moves every read of reminders off the full sync (D5), fixes the shared-project lookup (C6), and closes the cheap low items.

## Decisions (grill-me, 2026-10-05)

Dan ruled on each of these; the first design this plan was written with differed on 1, 4, 5, 7 and 8.

| # | Decision | Resolution | Basis |
|---|---|---|---|
| 1 | How an edit moves reminders | Delete-then-add in one sync batch, as today. No in-place move | Every refusal measured was a bad value, and the form blocks those since #40. A delete and an add in one batch leave the count unchanged, so the account limit (300, read 2026-10-05) cannot be hit. In-place moves would cap an edit at about 80 tagged tasks |
| 2 | A refused command | Always raises. The webhook answers 503 and Todoist redelivers (every 15 minutes, at most three times, per its docs); a route answers 502 and the mapping is kept | One rule, no list of which errors are permanent |
| 3 | Learning of failures | No alerting. README and CLAUDE.md say failures show only in Fly's log | Transient failures heal through redelivery; no permanent cause is known |
| 4 | Two labels at one place | The second is refused with a 400 and a sentence | Two mappings with the same address, trigger and radius make identical reminders; with none allowed, the webhook and the sweep are correct as they stand |
| 5 | Pull requests | Three: A failure handling and C6; B the read path; C input rules and cleanup | A clears the verdict with proven mechanisms and does not wait on B |
| 6 | Rows saved before #40 | Fixed in the data by a hand-run `UPDATE`. No hardening of `place_of` | The set is closed: every row saved since v80 is validated |
| 7 | C6, shared projects | In, PR A. The user is looked up by the event's `user_id` | Todoist's webhook docs: `user_id` is "the ID of the user that is the destination for the event"; `initiator` "may be ... a collaborator from a shared project" |
| 8 | D5, full sync per webhook | In, PR B. The webhook and the sweep both read through `GET /api/v1/location_reminders`; the sync read is deleted | One read path, and no full sync left to rate-limit |
| 9 | Gates | Dan's production test of #40 on v80 and the old-rows count are both done before PR A merges. Work on PR A may start before | A failure of the test is then the radius fix's alone |

Settled from evidence, not asked:
- Connect stalls: `connect=0` on the `Retry`, like `read=0`. Measured: 4 attempts today.
- The 502 sentence must not say nothing is lost. With decision 1, a refused re-add does lose that reminder until the task next changes.

Facts measured against the live API on 2026-10-05 (scratch tasks, deleted afterwards):
- Sync `reminder_add` refuses radius 0 and -5, an empty name, a fractional radius and a name over 255 characters. It accepts `loc_lat` of `nan` and of `95.0` and stores them as sent.
- Sync `reminder_delete` answers `ok` for an unknown id and for a repeated delete.
- Sync `reminder_update` answers `Reminder not found` for a location reminder. Recorded so nobody tries it again.
- `GET /api/v1/location_reminders`: with `task_id`, one task's reminders (171 ms); without, the same reminder the sync read returned, with identical `name`, `loc_trigger`, `radius`, `item_id` and `type`; `limit` is accepted. A reminder added through sync was visible to the by-task read at once. Paging across more than one page is NOT verified: the account holds one location reminder.

## Current State Analysis

- `app.py` (566 lines) is the whole app. Read it and `templates/index.html` before touching either.
- `todoist_run_commands` (`app.py:223-231`) logs any `sync_status` other than `ok` and returns.
- `Retry(total=3, read=0, ...)` at `app.py:111-117` has no `connect`.
- The webhook finds its user by `event["initiator"]["id"]` (`app.py:466`, `:474`, `:516`).
- Reminders are read by a full sync (`app.py:176-195`): once per webhook (`:486`), filtered to the task afterwards (`:508-512`), and once per sweep (`:285`).
- `create_label_location` (`app.py:410-452`) accepts any finite-or-not `lat` and `long`, and a place identical to another label's.
- `tests/conftest.py:118-130`: `fake_todoist` answers `ok` to every command and answers every GET with the labels. `tests/test_webhook.py:24-32`: `_wire` replaces `todoist_add_reminder` and `todoist_delete_reminder`, so the webhook tests never reach `todoist_run_commands`.
- `plans/intake-2026-10-05.md` and this plan are not committed. They ride in PR A, because a documentation-only merge would deploy for nothing.

## Desired End State

- No path on which the app tells Todoist or the user that a change happened when it did not.
- No full sync anywhere: `grep -c sync_token app.py` prints 0.
- No two mappings of one user can match the same reminders.
- An event in a shared project reaches the user it was delivered for.
- CLAUDE.md's sentences about failure handling are true, each naming what was tested.
- README has a "Known limitations" section, so 2.25 has a home.
- A rerun of `/repo-intake` reports 0 tier 1 and 0 tier 2.

### How to verify (end to end, after every phase that deploys)
1. Open https://todoist-location-labels.fly.dev/, log in, the label list loads.
2. Map a label at radius 100. Put it on a task: the reminder appears. Log: `reminder_add sync result` with `ok`.
3. Submit the same label with radius 255 (pick the address again). Log: `sweep matched 1 of N location reminders`, then `sweep sync result` with two `ok`.
4. Submit it again with radius 100. Log: the same two lines. This is the step that swept nothing on v79.
5. Delete the mapping. Log: `sweep matched 1 of N`; the reminder leaves the task.
6. `fly logs -a todoist-location-labels --no-tail` shows no traceback and no `failed`, `refused` or `asking for redelivery` line.

## What We're NOT Doing

- Moving reminders in place through the REST update endpoint (decision 1). Pull it in if a `sweep refused` line for a `reminder_add` ever appears in the log.
- Alerting or log shipping (decision 3).
- Allowing shared places and reconciling by place (decision 4).
- Hardening `place_of` against old rows (decision 6).
- Storing reminder ids. Matching stays by value; that decision is recorded in CLAUDE.md.
- A flash-message or error-page framework. A 400 or 502 carries one sentence through `abort`'s `description`, which Flask prints on its default error page (checked 2026-10-05).
- Changing paths or methods of any route.
- The items in the report's section 6 (opinions).

## Rules for every phase

- One PR per phase, from a branch off current `master`. No attribution trailers.
- Every behaviour change lands with a test that fails on the code before the change; paste the failing run in the PR body (2.26).
- The agent never reads, prints or types a secret. The Todoist token for live checks is read by the script from `~/.config/todoist-location-labels/token`, where Dan saved it on 2026-10-05.
- Merging deploys. The agent's merge was refused by the permission classifier on 2026-10-05, so the merge is Dan's: `gh pr merge <n> --repo dancwilliams/todoist-location-labels --squash --delete-branch`. The same goes for any read of the production database.
- A live check writes only to a scratch task it creates and deletes.
- `make check` before pushing.

---

## Phase 0: Gates (no code; both before PR A merges)

### 0a. #40 through the deployed app
Dan runs steps 2 to 5 of "How to verify" on v80. The agent reads the Fly log.
- Expected: three `sweep matched 1 of N location reminders` lines, one per edit and one for the delete.
- If any says `0 of N`: STOP. The radius fix did not hold in production; diagnose before PR A merges.

### 0b. What is in the production table
The implementing session writes this to a script (the proven form is `fly ssh console -a dcw-postgres-dev -C "psql -U postgres -d todoist_location_labels -c '...'"`, `plans/2026-10-03-intake-fixes.md:373`) and hands Dan one line to run. Read-only, no addresses, no tokens:

```sql
select count(*)                                              as total,
       count(*) filter (where radius > 255)                  as over_255,
       count(*) filter (where radius < 1)                    as under_1,
       count(*) filter (where radius <> floor(radius))       as fractional,
       count(*) filter (where length(name) > 255)            as long_name,
       count(*) filter (where name <> btrim(name))           as padded_name,
       count(*) filter (where lat = 'NaN' or long = 'NaN'
                           or abs(lat) > 90 or abs(long) > 180) as bad_coords
  from location_label;

select count(*) as same_place_pairs
  from location_label a join location_label b
    on a.user_id = b.user_id and a.id < b.id
   and btrim(a.name) = btrim(b.name) and a.loc_trigger = b.loc_trigger
   and least(floor(a.radius), 255) = least(floor(b.radius), 255);

-- What the update below would do to a NaN, an infinity and a negative; expect 255, 255, 1.
select least(greatest(floor('NaN'::float8), 1), 255),
       least(greatest(floor('Infinity'::float8), 1), 255),
       least(greatest(floor(-5::float8), 1), 255);
```

- `over_255` to `padded_name` all zero: D8 and D9 are closed with no change.
- Any non-zero: this matters once PR A ships, because a row Todoist refuses would then answer 503 on every event for its label. After Dan's go, and only if the third query printed 255, 255, 1:
  ```sql
  update location_label
     set radius = least(greatest(floor(radius), 1), 255), name = left(btrim(name), 255)
   where radius > 255 or radius < 1 or radius <> floor(radius)
      or length(name) > 255 or name <> btrim(name);
  ```
  Count before and after. Rows above 255 already have their reminders at 255 and padded names already have theirs stripped, so the update makes the row equal what Todoist holds. Rows that were fractional, under 1 or over-long never had a reminder accepted.
- `bad_coords` non-zero: Dan re-enters those mappings on the page after PR C.
- `same_place_pairs` non-zero: D2 is live for those labels today, and PR C's rule only stops new ones. List the pairs (`select a.label_id, b.label_id` over the same join) for Dan; changing one of the two radii by a metre separates them. Do it before PR C merges.

### Success Criteria
- [x] 0a: PASSED on v80, 2026-10-05 (Fly log, UTC, mapping row 19): 23:42:13 `reminder_add sync result` ok; 23:42:45 edit to 255, `sweep matched 1 of 1 location reminders`, two ok; 23:43:33 edit back to 100, `sweep matched 1 of 1`, two ok; 23:43:58 delete, `sweep matched 1 of 1`, one ok. No error, refused or redelivery line.
- [x] 0b: DONE 2026-10-05. Before: total 12, over_255 1, under_1 0, fractional 0, long_name 0, padded_name 0, bad_coords 0; same_place_pairs 0; the third query printed 255, 255, 1. Update run by Dan: `UPDATE 1`; after: total 12, every other count 0. D8 and D9 closed in the data; no pairs to separate; no coordinates to re-enter.

Connection note: the proven form of the psql command is `fly ssh console -a dcw-postgres-dev -C "sh -c 'PGPASSWORD=\$OPERATOR_PASSWORD psql -h \$FLY_PRIVATE_IP -p 5433 -U postgres -d todoist_location_labels -c \"<sql>\"'"` (the socket and localhost both refuse; the SQL must carry no quote and no `$`, so NaN is `(chr(78)||chr(97)||chr(78))::float8`).

---

## Phase 1 (PR A): A failure is a failure (D1, D3, D4, C6; clears 2.7, 2.12, 2.25)

### Changes Required

#### 1. `app.py`: refusals raise (`:223-231`)

```python
def todoist_run_commands(token, commands, what):
    """Send sync commands, at most SYNC_BATCH per request. Raises if Todoist refuses any."""
    for start in range(0, len(commands), SYNC_BATCH):
        batch = commands[start : start + SYNC_BATCH]
        sync_status = todoist_sync(token, commands=batch).get("sync_status", {})
        app.logger.info("%s sync result: %s", what, sync_status)
        # Todoist answers HTTP 200 and reports a refused command here. A command
        # with no status at all counts as refused too.
        refused = {c["uuid"]: sync_status.get(c["uuid"]) for c in batch}
        refused = {k: v for k, v in refused.items() if v != "ok"}
        if refused:
            raise requests.exceptions.RequestException(f"{what} refused: {refused}")
```

No new exception class: every caller already catches `RequestException` and answers 503 (`:555`) or 502 (`:401`, `:439`). `sweep_reminders` itself does not change.

#### 2. `app.py`: say what to do in the 502 (`:403`, `:443`)

Both become:

```python
        return abort(
            502,
            description="Todoist did not accept every change. Submit the same change again. "
            "If a task's reminder is missing, it comes back the next time that task changes.",
        )
```

This is D4's fix and the honest statement of decision 1: a sweep that stopped part-way is finished by submitting again, and a reminder whose re-add was refused is re-created by the webhook at that task's next update.

#### 3. `app.py`: connect stalls are not retried (`:112-113`)

```python
            total=3,
            connect=0,  # nor is a stall while connecting
            read=0,  # a stall is not retried: 4 x 10 s would outlast gunicorn's 30 s worker timeout
```

#### 4. `app.py`: the event's user, not its initiator (C6; `:466-476`, `:516`)

```python
    # user_id is who the event was delivered for; the initiator may be a
    # collaborator in a shared project who is not a user of this app.
    user = db.session.get(User, int(event["user_id"]))
    if user is None:
        app.logger.warning("No user found for user_id %s", event["user_id"])
        return ""
```

Delete `initiator = event["initiator"]`, and `:516` becomes `filter_by(user_id=user.id)`.

#### 5. `tests/conftest.py`: the fake can refuse

- `state` gains `"refuse": ()`, the command types to refuse. In `do_POST`, a command whose `type` is in it gets `{"error": "Invalid argument value", "error_code": 20, "http_code": 400}` as its status, the shape Todoist returned on 2026-10-05.
- Move `HOME_REMINDER`, `OTHER_REMINDER` and `_sent` here from `tests/test_routes.py:175-187`, and delete the copy at `tests/test_webhook.py:6-13` (report section 5, item 4).

#### 6. `tests/test_webhook.py`: through the wire

- `_event` gains `"user_id": "1"` beside the initiator.
- Replace `_wire` with `fake_todoist`: set `fake_todoist["labels"] = [{"id": "10", "name": "Home"}]` and `fake_todoist["reminders"]`, and assert on `_sent(fake_todoist)`. The existing tests keep their names and meaning; the add assertion becomes one `reminder_add` command with `"radius": 100`, `"loc_lat": "1.0"`, `"loc_long": "2.0"`. The two tests that patch `todoist_api_get` keep that patch and assert no commands were sent. `test_failed_add_asks_for_redelivery` stays as it is: it makes the add raise after the reads succeed, which one status for the whole fake cannot express.
- In `tests/test_routes.py`, the literal `250.0` in the expected command becomes `250`.

New tests, each run on 721300a first:

| Test | Protects | Fails today with |
|---|---|---|
| `test_refused_add_asks_for_redelivery` (webhook; `refuse=("reminder_add",)`, label on task) | A reminder Todoist refused is not reported as handled | 200 |
| `test_refused_delete_keeps_the_mapping` (routes; `refuse=("reminder_delete",)`) | A mapping is not removed while its reminders remain | 302, row gone |
| `test_refused_readd_during_edit_keeps_the_mapping` (routes; `refuse=("reminder_add",)`) | A refused edit is reported and the mapping keeps its old values; the 502 body carries the sentence | 302, row changed |
| `test_a_stalled_connect_is_not_retried` (`tests/test_todoist_client.py`) | A connect stall costs one attempt | 4 attempts (measured while planning) |
| `test_event_from_a_collaborator_reaches_the_user` (webhook; `user_id` "1", initiator id "999", label on task) | An event delivered for a user is handled for that user | no command sent |

The connect test, since the mechanism is not obvious:

```python
def test_a_stalled_connect_is_not_retried(monkeypatch):
    """D3: a connect that times out costs one attempt, like a stalled read."""
    attempts = []

    def stalled(address, *args, **kwargs):
        attempts.append(address)
        raise TimeoutError("timed out")

    monkeypatch.setattr("urllib3.util.connection.create_connection", stalled)
    policy = app_module.todoist_http.get_adapter("https://api.todoist.com").max_retries
    monkeypatch.setitem(
        app_module.todoist_http.adapters, "https://", HTTPAdapter(max_retries=policy.new(backoff_factor=0))
    )
    with pytest.raises(requests.exceptions.RequestException):
        app_module.todoist_api_get("labels", "tok")
    assert len(attempts) == 1
```

#### 7. Docs

- `CLAUDE.md:74`: "...or Todoist refuses or fails an add or delete (an HTTP error, or a per-command error inside a 200 answer; both tested), it answers 503...". The comment at `app.py:524` is then true as written.
- `CLAUDE.md:78`: "A stall is not retried, connecting or reading (`connect=0`, `read=0`; both tested): each call fails after `TODOIST_TIMEOUT`. The bound is per call, not per request."
- `CLAUDE.md` webhook paragraph (`:39`) and the label-name note (`:43`): the user is `event["user_id"]`; `initiator` is not used, because it may be a collaborator.
- `CLAUDE.md` Development Notes, new lines: the sync `reminder_update` command does not work for location reminders (tried 2026-10-05); failures are visible only in Fly's log, which keeps about 100 lines.
- `README.md`, new section after "How It Works":
  ```markdown
  ## Known limitations

  - Reminders are matched to a mapping by address, trigger and radius. A reminder you make by hand with the same three values is treated as the app's.
  - Changing a mapping deletes each of its reminders and creates it again at the new place. If Todoist refuses to create one, the page says so and that task's reminder comes back the next time the task changes.
  - Two labels mapped to the same address, trigger and radius can make a task's reminder disappear and reappear on alternate updates.
  - When something fails, the only record is the hosting log, which keeps a few minutes of history.
  ```
  PR C removes the third bullet.
- Commit `plans/intake-2026-10-05.md` and this plan in this PR.

### Success Criteria

#### Automated Verification
- [x] Each test in the table fails on 721300a for the stated reason; output in the PR body
- [x] `make check` exits 0
- [x] CI green on the PR (#41, `check (3.13)` and `check (3.14)` pass on b22760d)
- [x] After Dan's merge: deploy job green and `curl -s -o /dev/null -w '%{http_code}' https://todoist-location-labels.fly.dev/` prints 200 (PR #41 squash-merged as 8ad6ec4, run 37391269580 all green, Fly release v81, worker booted 23:57:34Z, check passing)

#### Manual Verification
- [x] Before the merge: Phase 0a and 0b are done and recorded above
- [x] After the deploy: "How to verify" steps 1 to 6 — PASSED on v81, 2026-10-06 (Fly log, UTC, mapping row 20): 00:00:55 `reminder_add` ok; 00:02:09 edit to 255, `sweep matched 1 of 1`, two ok; 00:02:41 edit back, `sweep matched 1 of 1`, two ok; 00:02:57 delete, `sweep matched 1 of 1`, one ok. No refused, failed, redelivery or traceback line.

**Implementation Note**: stop after this phase until Dan confirms the manual steps.

**Result**: PR #41, squash-merged as 8ad6ec4, Fly release v81. Five tests failing-first on 721300a; 43 tests, `app.py` 91%. One departure from the text: the 502 sentence is one constant `SWEEP_FAILED`. Side fix: `fake_todoist`'s shutdown poll is 0.01 s (suite 12.8 s → 1.0 s). D1, D3, D4, C6 closed; 2.7, 2.12, 2.25 cleared pending the fourth intake.

---

## Phase 2 (PR B): Reminders are read through REST (D5)

### Changes Required

#### 1. `app.py`: one pager for every list (`:153-166`, `:176-195`)

```python
def todoist_get_all(endpoint, token, params=None):
    """Every item of a paginated v1 list. Raises on API failure or an unexpected shape."""
    # Pages are {"results": [...], "next_cursor": ...}. Every page is read, and
    # anything else raises, so a second page is never mistaken for "not there".
    items, cursor = [], None
    while True:
        page = todoist_api_get(endpoint, token, {**(params or {}), **({"cursor": cursor} if cursor else {})})
        items.extend(page["results"])
        cursor = page.get("next_cursor")
        if not cursor:
            return items


def todoist_get_labels(token):
    """Fetch all labels for a user. Raises RequestException on API failure."""
    labels = todoist_get_all("labels", token)
    app.logger.info("Fetched %d labels", len(labels))
    return labels


def todoist_get_reminders(token, task_id=None):
    """Location reminders: one task's, or all of them. Raises RequestException on API failure.

    Read through REST, not sync: a full sync is limited to 100 per user per
    15 minutes, and the webhook reads on every task change.
    """
    return todoist_get_all("location_reminders", token, {"task_id": task_id} if task_id else None)
```

`todoist_sync` loses its read half and becomes commands only:

```python
def todoist_sync(token, commands):
    """Send a batch of commands to the Todoist sync endpoint (API v1)."""
    response = todoist_http.post(
        f"{TODOIST_API_BASE}/sync",
        headers=todoist_headers(token),
        data={"commands": json.dumps(commands)},
        timeout=TODOIST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()
```

#### 2. `app.py`: the two readers

- Webhook (`:486`): `item_reminders = todoist_get_reminders(token, event_data["id"])`. Delete the filter at `:508-512`; keep the `Existing location reminders for item` log line.
- Sweep (`:285`): `located = todoist_get_reminders(token)`. The `type == "location"` filter goes; `reminder_is_at` keeps its own type check, which REST results satisfy (`type` is `location` in every one read on 2026-10-05).
- While in this block (report section 5, item 5): the per-label query at `:537` becomes a lookup in the already-loaded list, `by_label = {str(ll.label_id): ll for ll in user_location_labels}`.

#### 3. `tests/conftest.py`: the fake serves the list

`do_GET` dispatches on the path: `/labels` as now; `/location_reminders` answers `{"results": [...], "next_cursor": None}` with `state["reminders"]`, narrowed to those whose `item_id` equals the `task_id` query parameter when one is sent. The by-task narrowing copies what the real endpoint did on 2026-10-05 (it returned the scratch task's one reminder while the account held others). `do_POST` no longer returns reminders.

#### 4. Tests

| Test | Protects | Fails on PR A's code with |
|---|---|---|
| `test_reading_reminders_is_not_a_full_sync` (replaces the second half of `test_a_command_does_not_ask_for_a_full_sync`): a webhook and a sweep make no POST carrying `sync_token` | The rate-limited call is gone | `["resource_types", "sync_token"]` posted |
| `test_webhook_ignores_other_tasks_reminders`: the fake holds a matching reminder on task 901; the event is for task 900 with the label on → one `reminder_add` | The by-task read does not let another task's reminder count as this one's | passes today too; kept because the filter moved from the app to the request and nothing else pins it |
| `test_labels_follow_next_cursor` | Unchanged; it now covers `todoist_get_all`, which reminders share | |

The second row does not fail first. It is a contract test for behaviour that moved, not a regression test; say so in the PR body.

#### 5. Docs
- `CLAUDE.md:33-38`: add `GET /api/v1/location_reminders` (all, or one task's with `task_id`; paginated) and remove "Fetching reminders" from the sync list.
- `CLAUDE.md:79`: replace with "The app never requests a full sync. Reminders are read through REST; the sync endpoint is used for commands only."
- `CLAUDE.md:81`: `todoist_get_all` follows `next_cursor` for labels and reminders alike.

### Success Criteria

#### Automated Verification
- [x] `test_reading_reminders_is_not_a_full_sync` fails on PR A's code; output in the PR body (fails on 8ad6ec4 with `['resource_types', 'sync_token']` posted)
- [x] `grep -c sync_token app.py` prints 0
- [x] `make check` exits 0 (44 passed, `app.py` 90%)
- [x] Live check on a scratch task, before the PR opens (script written by the implementing session): add three reminders; `todoist_api_get("location_reminders", token, {"task_id": id, "limit": 2})` returns two results and a non-empty `next_cursor`; `todoist_get_all` with the same params returns all three; `todoist_get_reminders(token, id)` returns only that task's. If the first page does not carry a cursor, STOP: paging does not work the way labels do, and the sweep must keep the sync read — PASSED 2026-10-06 on scratch task `6hh7pMCwJG5RXcRx`: first page 2 results, cursor present; `todoist_get_all` 3 (radii 100, 150, 200); by-task read 3, every `item_id` the scratch task's, every `type` `location`; account total 0 → 3 → task deleted (204), by-task read 0. The account held no other location reminder at the time, so the by-task narrowing against other tasks rests on the 2026-10-05 read above.
- [x] CI green; after Dan's merge, deploy green (PR #42 squash-merged as ba75428, run 37400979718 all green, Fly release v82, machine started with its check passing, `/` answers 200)

#### Manual Verification
- [x] "How to verify" steps 2 to 5: every one of them reads reminders through the new path — PASSED on v82, 2026-10-06 (Fly log, UTC, mapping row 21, label `test_label`): 14:41:34 `Existing location reminders for item: 0`, `reminder_add` ok; 14:41:57 edit to 255, `sweep matched 1 of 1`, two ok; 14:42:34 edit back, `sweep matched 1 of 1`, two ok; 14:43:16 delete, `sweep matched 1 of 1`, one ok.
- [x] Untag the task before step 5 and tag it again: the reminder is removed and re-added, not duplicated — 14:43:01 untag: `Existing location reminders for item: 1`, `Deleting reminder` + `reminder_delete` ok; 14:43:06 retag: `Existing ... 0`, `reminder_add` ok. No refused, failed, redelivery or traceback line; the only `[error]` lines are Fly's health probe at two autostart wake-ups (14:30:24Z, 14:40:28Z), passing 4-5 s later before gunicorn's worker booted.

**Implementation Note**: stop after this phase until Dan confirms.

**Result**: PR #42, squash-merged as ba75428, Fly release v82. `test_reading_reminders_is_not_a_full_sync` failing-first on 8ad6ec4; 44 tests, `app.py` 90%. Live paging gate passed on a scratch task (limit=2 pages with a cursor). `grep -c sync_token app.py` is 0. D5 closed; report section 5 item 5 done.

---

## Phase 3 (PR C): Input rules and sweeping up (D2, D6, D7, report section 5)

### Changes Required

#### 1. `app.py`: one key for matching, and no second mapping on it (D2; `:261-274`, `:410-452`)

```python
def match_key(place):
    """The values a reminder is matched on: (name, trigger, radius)."""
    return (place[0], place[3], place[4])
```

`reminder_is_at` compares `(reminder.get("name"), reminder.get("loc_trigger"), reminder.get("radius"))` with `match_key(place)`. In `create_label_location`, after `new_place` is built:

```python
    # Two mappings that match the same reminders cannot be told apart, so the
    # webhook would delete for one what it added for the other.
    for other in user.location_labels:
        if other.label_id != label_id and match_key(place_of(other)) == match_key(new_place):
            return abort(
                400,
                description="Another label is already mapped to this address, trigger and radius. "
                "Change one of the three.",
            )
```

The check covers creating and editing, since both go through this route.

#### 2. `app.py`: coordinates (D6)

`-90 <= lat <= 90 and -180 <= long <= 180` joins the range condition. A NaN fails every comparison, so it is refused without `math`.

#### 3. `templates/index.html`: the address must come from the list (D7; `:264-282`)

```js
var box = document.getElementById('autocomplete');
box.addEventListener('input', function () { box.setCustomValidity('Pick an address from the list'); });
// in fillInAddress, after the two assignments:
box.setCustomValidity('');
```

Typing in the box marks it invalid until a suggestion is picked, so the browser refuses to submit a name with another place's coordinates. No test harness for the page; verified by hand.

#### 4. Report section 5
Delete `ignore_missing_imports` (item 1); delete `[tool.ruff.format]` and `target-version` (item 2); delete `[env] PRIMARY_REGION` from `fly.toml` (item 3; the deploy is its test); dict comprehension in `index()` (item 6). Items 4 and 5 were done in PRs A and B.

#### 5. Tests

| Test | Fails on PR B's code with |
|---|---|
| `test_second_label_at_the_same_place_is_refused`: the fixture's label 10 is at Home, `on_enter`, 100; posting label 30 with the same three values answers 400, the body carries the sentence, no row for 30 | 302, row created |
| `test_create_rejects_bad_input` gains `{"lat": "nan"}`, `{"lat": "95"}`, `{"long": "inf"}` | 302 for each |

`test_resubmitting_unchanged_values_touches_nothing` must pass unchanged: a mapping is never refused for matching itself.

#### 6. Docs
- `CLAUDE.md:75`: a third consequence of matching by value, and its guard: two mappings of one user may not share name, trigger and radius; the form refuses the second.
- `CLAUDE.md:76`: coordinates must be finite and in range, because Todoist stores `nan` as sent (measured 2026-10-05).
- `README.md` Known limitations: remove the third bullet.

### Success Criteria

#### Automated Verification
- [x] The new test and the three new rows fail on PR B's code; output in the PR body (on ba75428: `test_second_label_at_the_same_place_is_refused` 302 == 400; `test_create_rejects_bad_input` fails at `{"lat": "nan"}` with `sqlite3.IntegrityError: NOT NULL constraint failed: location_label.lat`, SQLite storing NaN as NULL, where the plan expected a 302 — production Postgres would have stored it)
- [x] `make check` exits 0 (45 passed, `app.py` 90%; mypy clean without `ignore_missing_imports`); CI green
- [ ] After Dan's merge: deploy green; machine `started` with its check passing after the `fly.toml` change

#### Manual Verification
- [ ] Before the merge: Phase 0b's `same_place_pairs` is zero, or the pairs are separated
- [ ] Map a second label to the same address with the same trigger and radius: the page says another label already has this place
- [ ] Type an address and submit without picking a suggestion: the browser shows "Pick an address from the list". Pick one, then change a character: blocked again
- [ ] "How to verify" steps 2 to 5

---

## Phase 4: Close out

- Run `/repo-intake` (fourth run). The `improve` pass goes to a fresh agent that is given the code and not this plan's reasoning (report section 8, item 4). Scope: `git diff 721300a..HEAD`. Expected: 0 tier 1, 0 tier 2.
- Tick the boxes; write each phase's result under it, with PR, commit and release.
- AutoMem: one pointer memory, linked to the third intake; remove the `open-loop` tag from the sweep bug's memory once 0a has passed.

## Testing Strategy

- The fake moves closer to the real thing twice: in PR A it can refuse a command, in PR B it serves the reminder list. Every expected value in a new test comes from a read-back on 2026-10-05 or from the form's own contract, not from the code under test.
- Anything that changes what is asked of Todoist gets a live check on a scratch task before its PR opens. That is PR B. `fake_todoist` cannot vouch for Todoist; v79 is the proof.
- PR A changes only what the app does with Todoist's answers, and PR C only what the form accepts, so neither needs a live check beyond Dan's production steps.
- Manual steps are Dan's, in production, after each deploy.

## Performance Considerations

- A webhook today costs one labels read and one full sync. After PR B it costs one labels read and one small list read (171 ms measured), and it no longer counts against the 100 full syncs per 15 minutes.
- A sweep reads all location reminders in pages. An account can hold 300 (limit read 2026-10-05); the page size is whatever Todoist's default is, which is not known. `todoist_get_all` follows the cursor either way.
- Whether the REST list has a rate limit of its own is not documented and not known.

## Rollback

Every phase is one deploy. `fly releases -a todoist-location-labels --image` lists the image refs; `fly deploy --image <previous ref>` rolls back. Phase 0b's `update` is the only one-way change; it only rewrites rows to the values Todoist already holds for them.

## References

- Intake report: `plans/intake-2026-10-05.md`
- Previous plan: `plans/2026-10-03-intake-fixes.md`
- Standards: `~/.claude/skills/repo-intake/standards.md`
- Todoist API v1: https://developer.todoist.com/api/v1/ (Webhooks, "Request Format": `user_id` and `initiator`; "Failed Delivery": redelivery every 15 minutes, at most three times; Location reminders: `GET /api/v1/location_reminders`; limits: 100 full syncs and 1000 partial syncs per user per 15 minutes)
