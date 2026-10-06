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
    import pytest
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
        ("reminder_delete", {"id": "r1"}),
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
    ]
    assert app_module.LocationLabel.query.filter_by(label_id=10).one().name == "2 Oak Ave"


def test_resubmitting_unchanged_values_touches_nothing(client, login, fake_todoist):
    fake_todoist["reminders"] = [HOME_REMINDER]
    r = client.post(
        "/create_label_location",
        data=_form(
            label_id="10", address="Home", lat="1.0", long="2.0", radius="100", trigger="on_enter"
        ),
    )
    assert r.status_code == 302
    assert fake_todoist["hits"] == 0


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


def test_malformed_reminders_page_during_a_sweep_is_a_502(client, login, monkeypatch):
    """B3: a reminders read that comes back in an unexpected shape is told to the user
    like any other failure, and the mapping is kept."""
    monkeypatch.setattr(
        app_module, "todoist_api_get", lambda endpoint, token, params=None: ["not", "a", "dict"]
    )
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
    assert [t for t, _ in _sent(fake_todoist)] == ["reminder_delete", "reminder_add"] * 2
    assert r.status_code == 502
    assert "comes back the next time its task changes" in r.get_data(as_text=True)


def test_refused_delete_during_edit_keeps_the_mapping(client, login, fake_todoist):
    """D1, D4: a reminder still at the old place means the mapping must stay there."""
    fake_todoist["reminders"] = [HOME_REMINDER]
    fake_todoist["refuse"] = ("reminder_delete",)
    r = client.post("/create_label_location", data=_form(label_id="10", address="2 Oak Ave"))
    assert r.status_code == 502
    assert "Submit the same change again" in r.get_data(as_text=True)
    assert app_module.LocationLabel.query.filter_by(label_id=10).one().name == "Home"
