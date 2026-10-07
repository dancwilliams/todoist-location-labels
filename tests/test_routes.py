import pytest
from conftest import HOME_REMINDER, OTHER_REMINDER, _sent

import app as app_module


def _mapping_id():
    return app_module.LocationLabel.query.filter_by(label_id=10).one().id


def _other_users_mapping():
    other = app_module.User(id=2, oauth_token="tok2")
    row = app_module.LocationLabel(
        user=other, label_id=20, name="Work", lat=3.0, long=4.0, loc_trigger="on_leave", radius=50.0
    )
    app_module.db.session.add_all([other, row])
    app_module.db.session.commit()
    return row.id


def test_delete_unknown_mapping_is_404(client, login):
    assert client.post("/delete_label_location/999").status_code == 404


def test_delete_someone_elses_mapping_is_404(client, login):
    other_id = _other_users_mapping()
    assert client.post(f"/delete_label_location/{other_id}").status_code == 404
    assert app_module.db.session.get(app_module.LocationLabel, other_id) is not None


def test_delete_by_get_is_refused(client, login):
    """B4: a link on another site must not be able to delete a mapping."""
    assert client.get(f"/delete_label_location/{_mapping_id()}").status_code == 405
    assert app_module.LocationLabel.query.count() == 1


def test_oauth_callback_without_session_state_is_401(client):
    assert client.get("/oauth/redirect?state=x&code=y").status_code == 401


def test_logout_when_not_logged_in_redirects(client):
    assert client.get("/logout").status_code == 302


def test_stale_session_renders_logged_out_page(client):
    """B9: a session whose user row is gone must not 500 on every page load."""
    with client.session_transaction() as sess:
        sess["user_id"] = 999
    r = client.get("/")
    assert r.status_code == 200
    assert b"Authorize" in r.data


def _form(**over):
    form = {
        "label_id": "30",
        "trigger": "on_enter",
        "address": "1 Main St",
        "lat": "1.5",
        "long": "2.5",
        "radius": "100",
    }
    form.update(over)
    return form


def test_create_mapping(client, login, fake_todoist):
    # Todoist strips the name, so the mapping is stored stripped.
    r = client.post("/create_label_location", data=_form(address=" 1 Main St "))
    assert r.status_code == 302
    assert fake_todoist["hits"] == 0  # a new mapping has no reminders to move
    row = app_module.LocationLabel.query.filter_by(label_id=30).one()
    assert (row.name, row.lat, row.long, row.radius, row.loc_trigger) == (
        "1 Main St",
        1.5,
        2.5,
        100.0,
        "on_enter",
    )


def test_create_rejects_bad_input(client, login):
    assert client.post("/create_label_location", data=_form(radius="abc")).status_code == 400
    assert client.post("/create_label_location", data=_form(lat="")).status_code == 400
    assert client.post("/create_label_location", data=_form(trigger="sideways")).status_code == 400
    # Values Todoist refuses or silently changes (read back 2026-10-05): a radius
    # over 255 comes back as 255, a fractional one or a 256-character name is refused.
    for bad in ({"radius": "256"}, {"radius": "0"}, {"radius": "100.5"}, {"address": "x" * 256}):
        assert client.post("/create_label_location", data=_form(**bad)).status_code == 400, bad
    # D6: Todoist stores nan and 95.0 as sent (measured 2026-10-05), so the form must refuse them.
    for bad in ({"lat": "nan"}, {"lat": "95"}, {"long": "inf"}):
        assert client.post("/create_label_location", data=_form(**bad)).status_code == 400, bad
    assert app_module.LocationLabel.query.filter_by(label_id=30).count() == 0


def test_second_label_at_the_same_place_is_refused(client, login, fake_todoist):
    """D2: two mappings matching the same reminders would undo each other's work."""
    r = client.post(
        "/create_label_location",
        data=_form(address="Home", lat="1.0", long="2.0", radius="100", trigger="on_enter"),
    )
    assert r.status_code == 400
    assert "Another label is already mapped" in r.get_data(as_text=True)
    assert app_module.LocationLabel.query.filter_by(label_id=30).count() == 0


def test_index_renders_delete_as_post_form(client, login, monkeypatch):
    monkeypatch.setattr(
        app_module, "todoist_get_labels", lambda token: [{"id": "10", "name": "Home"}]
    )
    monkeypatch.setattr(app_module, "todoist_get_user", lambda token: {"full_name": "Test User"})
    r = client.get("/")
    assert r.status_code == 200
    page = r.get_data(as_text=True)
    assert "Logout Test User" in page
    assert f'action="/delete_label_location/{_mapping_id()}"' in page
    assert 'method="post"' in page


def test_resubmitting_a_label_updates_it(client, login, fake_todoist):
    """B2: a second submit for the same label edits the mapping instead of hiding a duplicate."""
    client.post("/create_label_location", data=_form())
    r = client.post(
        "/create_label_location",
        data=_form(address="2 Oak Ave", lat="9.5", long="8.5", radius="250", trigger="on_leave"),
    )
    assert r.status_code == 302
    row = app_module.LocationLabel.query.filter_by(label_id=30).one()
    assert (row.name, row.lat, row.long, row.radius, row.loc_trigger) == (
        "2 Oak Ave",
        9.5,
        8.5,
        250.0,
        "on_leave",
    )


def test_database_refuses_duplicate_mapping(client, login):
    """B2: the constraint, not just the route, keeps one mapping per user and label."""
    from sqlalchemy.exc import IntegrityError

    dup = app_module.LocationLabel(
        user_id=login.id,
        label_id=10,
        name="Again",
        lat=0.0,
        long=0.0,
        loc_trigger="on_enter",
        radius=1.0,
    )
    app_module.db.session.add(dup)
    with pytest.raises(IntegrityError):
        app_module.db.session.commit()
    app_module.db.session.rollback()


def test_login_sets_a_30_day_secure_cookie(client, monkeypatch):
    """The session lives in a signed cookie: Secure, Lax, and good for 30 days."""
    from datetime import UTC, datetime, timedelta
    from email.utils import parsedate_to_datetime

    class TokenResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"access_token": "tok"}

    monkeypatch.setattr(app_module.todoist_http, "post", lambda *a, **k: TokenResponse())
    monkeypatch.setattr(app_module, "todoist_get_user", lambda token: {"id": 5})
    with client.session_transaction() as sess:
        sess["oauth_secret_state"] = "s"
    r = client.get("/oauth/redirect?state=s&code=c")
    assert r.status_code == 302
    cookie = next(h for h in r.headers.getlist("Set-Cookie") if h.startswith("session="))
    attrs = {
        p.strip().split("=")[0].lower(): p.strip().partition("=")[2] for p in cookie.split(";")[1:]
    }
    assert "secure" in attrs and "httponly" in attrs
    assert attrs["samesite"] == "Lax"
    lifetime = parsedate_to_datetime(attrs["expires"]) - datetime.now(UTC)
    assert timedelta(days=29) < lifetime <= timedelta(days=30)


def test_editing_a_mapping_moves_its_reminders(client, login, fake_todoist):
    """C1: the reminder a mapping created follows the mapping to its new address."""
    fake_todoist["reminders"] = [HOME_REMINDER, OTHER_REMINDER]
    r = client.post(
        "/create_label_location",
        data=_form(
            label_id="10",
            address="2 Oak Ave",
            lat="9.5",
            long="8.5",
            radius="250",
            trigger="on_leave",
        ),
    )
    assert r.status_code == 302
    assert _sent(fake_todoist) == [
        (
            "reminder_add",
            {
                "item_id": "900",
                "type": "location",
                "name": "2 Oak Ave",
                "loc_lat": "9.5",
                "loc_long": "8.5",
                "loc_trigger": "on_leave",
                "radius": 250,
            },
        ),
        ("reminder_delete", {"id": "r1"}),
    ]
    assert app_module.LocationLabel.query.filter_by(label_id=10).one().name == "2 Oak Ave"


def test_resubmitting_unchanged_values_touches_nothing(client, login, fake_todoist):
    fake_todoist["reminders"] = [HOME_REMINDER]
    edit = _form(
        label_id="10", address="Home", lat="1.0", long="2.0", radius="100", trigger="on_enter"
    )
    assert client.post("/create_label_location", data=edit).status_code == 302
    assert fake_todoist["hits"] == 0
    # A resubmit of what the form last accepted is a no-op at any precision: the
    # coordinates are stored as parsed, and a reminder's come back exactly as sent.
    edit["lat"] = "1.0000000001"
    assert client.post("/create_label_location", data=edit).status_code == 302
    hits = fake_todoist["hits"]
    assert client.post("/create_label_location", data=edit).status_code == 302
    assert fake_todoist["hits"] == hits


def test_deleting_a_mapping_removes_its_reminders(client, login, fake_todoist, caplog):
    """C1: deleting a mapping takes the reminders it created with it."""
    fake_todoist["reminders"] = [HOME_REMINDER, OTHER_REMINDER]
    assert client.post(f"/delete_label_location/{_mapping_id()}").status_code == 302
    assert _sent(fake_todoist) == [("reminder_delete", {"id": "r1"})]
    assert "sweep matched 1 of 2 location reminders" in caplog.text
    assert app_module.LocationLabel.query.count() == 0


def test_a_mapping_todoist_stored_differently_still_finds_its_reminders(
    client, login, fake_todoist
):
    """A row saved before the form refused such values: Todoist stored the radius as
    255 and the name stripped (read back 2026-10-05), and said "ok"."""
    row = app_module.LocationLabel.query.filter_by(label_id=10).one()
    row.name, row.radius = " Home ", 300.0
    app_module.db.session.commit()
    fake_todoist["reminders"] = [dict(HOME_REMINDER, radius=255)]
    assert client.post(f"/delete_label_location/{row.id}").status_code == 302
    assert _sent(fake_todoist) == [("reminder_delete", {"id": "r1"})]


def test_mapping_is_unchanged_when_todoist_is_down(client, login, fake_todoist):
    """If the reminders cannot be moved, the mapping stays as it was and the user is told."""
    fake_todoist["status"] = 503
    edit = client.post("/create_label_location", data=_form(label_id="10", address="2 Oak Ave"))
    delete = client.post(f"/delete_label_location/{_mapping_id()}")
    assert (edit.status_code, delete.status_code) == (502, 502)
    assert app_module.LocationLabel.query.filter_by(label_id=10).one().name == "Home"


@pytest.mark.parametrize(
    "patch, answer",
    [
        ("todoist_api_get", ["not", "a", "dict"]),
        ("todoist_api_get", {"results": ["not a reminder"]}),
        ("todoist_sync", []),
        ("todoist_sync", {"sync_status": []}),
    ],
)
def test_malformed_todoist_answer_during_a_sweep_is_a_502(
    client, login, fake_todoist, monkeypatch, patch, answer
):
    """B3, B6: a Todoist answer in an unexpected shape, read or written, is told to the
    user like any other failure, and the mapping is kept."""
    fake_todoist["reminders"] = [HOME_REMINDER]  # so the sync cases have a command to send
    monkeypatch.setattr(app_module, patch, lambda *a, **k: answer)
    r = client.post(f"/delete_label_location/{_mapping_id()}")
    assert r.status_code == 502
    assert "Submit the same change again" in r.get_data(as_text=True)
    assert app_module.LocationLabel.query.count() == 1


def test_refused_delete_keeps_the_mapping(client, login, fake_todoist):
    """D1: a mapping is not removed while Todoist still holds its reminders."""
    fake_todoist["reminders"] = [HOME_REMINDER]
    fake_todoist["refuse"] = ("reminder_delete",)
    assert client.post(f"/delete_label_location/{_mapping_id()}").status_code == 502
    assert app_module.LocationLabel.query.count() == 1


def test_refused_readd_during_edit_saves_the_mapping(client, login, fake_todoist, monkeypatch):
    """B1: when only adds are refused, every delete went through, so nothing is left at
    the old place. The mapping is saved at the new one (the webhook re-creates each
    missing reminder there), every batch is still sent, and the answer says so."""
    fake_todoist["reminders"] = [HOME_REMINDER, dict(HOME_REMINDER, id="r3", item_id="902")]
    fake_todoist["refuse"] = ("reminder_add",)
    monkeypatch.setattr(app_module, "SYNC_BATCH", 2)
    r = client.post("/create_label_location", data=_form(label_id="10", address="2 Oak Ave"))
    assert app_module.LocationLabel.query.filter_by(label_id=10).one().name == "2 Oak Ave"
    assert [t for t, _ in _sent(fake_todoist)] == ["reminder_add"] * 2 + ["reminder_delete"] * 2
    assert r.status_code == 502
    assert "comes back the next time its task changes" in r.get_data(as_text=True)


def test_resubmitted_edit_does_not_double_a_moved_reminder(client, login, fake_todoist, caplog):
    """B5: after a refused delete the mapping is kept while the other tasks' reminders
    have moved. The resubmit the 502 asks for must converge: a task already holding a
    reminder at the new place gets no second one."""
    fake_todoist["reminders"] = [
        HOME_REMINDER,  # task 900's old reminder, still there
        # task 900 already moved
        dict(HOME_REMINDER, id="r2", name="2 Oak Ave", loc_lat="1.5", loc_long="2.5"),
        dict(HOME_REMINDER, id="r3", item_id="902"),  # task 902 not moved
    ]
    r = client.post("/create_label_location", data=_form(label_id="10", address="2 Oak Ave"))
    assert r.status_code == 302
    assert _sent(fake_todoist) == [
        (
            "reminder_add",
            {
                "item_id": "902",
                "type": "location",
                "name": "2 Oak Ave",
                "loc_lat": "1.5",
                "loc_long": "2.5",
                "loc_trigger": "on_enter",
                "radius": 100,
            },
        ),
        ("reminder_delete", {"id": "r1"}),
        ("reminder_delete", {"id": "r3"}),
    ]
    assert "sweep: 1 tasks already at the new place" in caplog.text


def test_a_dropped_batch_is_one_post(client, login, fake_todoist):
    """The fixture's fault is a connection error the Retry does not retry (read=0), so
    a batch count in a test means batches, not attempts."""
    fake_todoist["reminders"] = [HOME_REMINDER]
    fake_todoist["drop_batches"] = (1,)
    assert client.post(f"/delete_label_location/{_mapping_id()}").status_code == 502
    assert fake_todoist["batches"] == 1
    assert fake_todoist["hits"] == 2  # one GET, one POST


def test_sweep_sends_adds_before_deletes(client, login, fake_todoist, monkeypatch):
    """F2: whatever prefix of a sweep Todoist applied, every task still holds a
    reminder, so the resubmit converges."""
    fake_todoist["reminders"] = [HOME_REMINDER, dict(HOME_REMINDER, id="r3", item_id="902")]
    monkeypatch.setattr(app_module, "SYNC_BATCH", 2)
    r = client.post("/create_label_location", data=_form(label_id="10", address="2 Oak Ave"))
    assert r.status_code == 302
    assert [t for t, _ in _sent(fake_todoist)] == [
        "reminder_add",
        "reminder_add",
        "reminder_delete",
        "reminder_delete",
    ]


HOME = ("Home", 1.0, 2.0, "on_enter", 100)  # the `user` fixture's mapping
OAK = ("2 Oak Ave", 1.5, 2.5, "on_enter", 100)  # a new name
HOME_MOVED = ("Home", 9.0, 2.0, "on_enter", 100)  # same key, other coordinates (F1)
ELSEWHERE = ("Somewhere else", 1.0, 2.0, "on_enter", 100)  # another mapping's key


def _rem(reminder_id, item_id, place):
    name, lat, long, trigger, radius = place
    return {
        "id": reminder_id,
        "type": "location",
        "item_id": item_id,
        "name": name,
        "loc_lat": str(lat),
        "loc_long": str(long),
        "loc_trigger": trigger,
        "radius": radius,
    }


# Each state is what the account holds before the submit, written against the old
# place and the target so every row also runs as a half-applied coordinate-only edit.
# Every row has a proven source (the sixth plan, decision 7); a new row needs one too.
STATES = {
    # every edit; three tasks, so a delete spans two requests under SYNC_BATCH = 2
    "first edit": lambda old, new: [
        _rem("r1", "900", old),
        _rem("r2", "901", old),
        _rem("r3", "902", old),
    ],
    # a sweep cut off after its adds (stub C, E): one task holds both, one the new only
    "half applied": lambda old, new: [
        _rem("r1", "900", old),
        _rem("r2", "900", new),
        _rem("r3", "901", new),
    ],
    # the above, then task 901 changed before the resubmit, so the webhook put the
    # old place back (stub C)
    "half applied, webhook re-added the old place": lambda old, new: [
        _rem("r1", "900", old),
        _rem("r2", "900", new),
        _rem("r3", "901", new),
        _rem("r4", "901", old),
    ],
    # the above with two tasks not yet moved (stub E, F2)
    "half applied, two still to move": lambda old, new: [
        _rem("r1", "900", old),
        _rem("r2", "900", new),
        _rem("r3", "901", old),
        _rem("r4", "902", old),
    ],
    # a resubmit after a saved sweep (for a delete: nothing left at the old place)
    "already converged": lambda old, new: [_rem("r1", "900", new)] if new else [],
    # another mapping's reminder on the same task is not this mapping's
    "another mapping's reminder": lambda old, new: [
        _rem("r1", "900", old),
        _rem("rx", "900", ELSEWHERE),
    ],
}
TARGETS = {"new name": OAK, "coordinates only": HOME_MOVED, "delete": None}
FAULTS = {
    "none": (),
    "first request dropped": (1,),  # nothing applied
    "second request dropped": (2,),  # the first batch applied, the rest not (F2)
}


# Written here, not imported from app: the expected values must not come from the code under test.
def _key(r):
    return (r["name"], r["loc_trigger"], r["radius"])


def _key_of(place):
    return (place[0], place[3], place[4])


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
            at = (
                r["name"],
                float(r["loc_lat"]),
                float(r["loc_long"]),
                r["loc_trigger"],
                r["radius"],
            )
            assert at == target, (task, r)
    assert [r for r in after if _key(r) not in keys] == [r for r in before if _key(r) not in keys]


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
def test_sweep_converges_from_any_state(
    client, login, fake_todoist, monkeypatch, state, target, fault
):
    """F1, F2, B5, B1: whatever a task holds and wherever a sweep was cut off, submitting
    the change (again) ends with exactly one reminder per task at the new place, or none."""
    place = TARGETS[target]
    before = STATES[state](HOME, place)
    fake_todoist["reminders"] = [dict(r) for r in before]
    monkeypatch.setattr(app_module, "SYNC_BATCH", 2)  # so a sweep is several requests
    mapping_id = _mapping_id()
    if place is None:

        def submit():
            return client.post(f"/delete_label_location/{mapping_id}")

    else:
        name, lat, long, trigger, radius = place
        form = _form(
            label_id="10",
            address=name,
            lat=str(lat),
            long=str(long),
            trigger=trigger,
            radius=str(radius),
        )

        def submit():
            return client.post("/create_label_location", data=form)

    fake_todoist["drop_batches"] = FAULTS[fault]
    r = submit()
    # A sweep too short to reach the fault, or with nothing to send, succeeds first time.
    if r.status_code == 502:
        assert "Submit the same change again" in r.get_data(as_text=True)
        assert app_module.LocationLabel.query.filter_by(label_id=10).one().name == "Home"
        fake_todoist["drop_batches"] = ()
        r = submit()  # the resubmit the page asks for
    assert r.status_code == 302
    _assert_converged(before, fake_todoist["reminders"], HOME, place)
    if place is not None:
        row = app_module.LocationLabel.query.filter_by(label_id=10).one()
        assert (row.name, row.lat, row.long, row.loc_trigger, row.radius) == place
    else:
        assert app_module.LocationLabel.query.count() == 0


def test_refused_delete_during_edit_keeps_the_mapping(client, login, fake_todoist):
    """D1, D4: a reminder still at the old place means the mapping must stay there."""
    fake_todoist["reminders"] = [HOME_REMINDER]
    fake_todoist["refuse"] = ("reminder_delete",)
    r = client.post("/create_label_location", data=_form(label_id="10", address="2 Oak Ave"))
    assert r.status_code == 502
    assert "Submit the same change again" in r.get_data(as_text=True)
    assert app_module.LocationLabel.query.filter_by(label_id=10).one().name == "Home"
