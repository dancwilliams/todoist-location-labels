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


def test_delete_own_mapping(client, login):
    r = client.post(f"/delete_label_location/{_mapping_id()}")
    assert r.status_code == 302
    assert app_module.LocationLabel.query.count() == 0


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


def test_create_mapping(client, login):
    assert client.post("/create_label_location", data=_form()).status_code == 302
    row = app_module.LocationLabel.query.filter_by(label_id=30).one()
    assert (row.name, row.lat, row.long, row.radius, row.loc_trigger) == (
        "1 Main St",
        1.5,
        2.5,
        100.0,
        "on_enter",
    )


def test_create_rejects_bad_numbers_and_trigger(client, login):
    assert client.post("/create_label_location", data=_form(radius="abc")).status_code == 400
    assert client.post("/create_label_location", data=_form(lat="")).status_code == 400
    assert client.post("/create_label_location", data=_form(trigger="sideways")).status_code == 400
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


def test_resubmitting_a_label_updates_it(client, login):
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
