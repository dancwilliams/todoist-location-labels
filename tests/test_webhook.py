import requests

import app as app_module

ITEM = "900"
HOME_REMINDER = {
    "id": "r1",
    "type": "location",
    "item_id": ITEM,
    "name": "Home",
    "loc_trigger": "on_enter",
    "radius": 100.0,
}


def _event(labels):
    return {
        "event_name": "item:updated",
        "initiator": {"id": "1"},
        "event_data": {"id": ITEM, "labels": labels},
    }


def _wire(monkeypatch, reminders):
    calls = {"add": [], "delete": []}
    monkeypatch.setattr(
        app_module, "todoist_get_labels", lambda token: [{"id": "10", "name": "Home"}]
    )
    monkeypatch.setattr(app_module, "todoist_get_reminders", lambda token: reminders)
    monkeypatch.setattr(app_module, "todoist_add_reminder", lambda *a: calls["add"].append(a))
    monkeypatch.setattr(app_module, "todoist_delete_reminder", lambda *a: calls["delete"].append(a))
    return calls


def test_adds_reminder_for_mapped_label(post_webhook, user, monkeypatch):
    calls = _wire(monkeypatch, reminders=[])
    r = post_webhook(_event(["Home"]))
    assert r.status_code == 200
    assert calls["add"] == [("tok", ITEM, "Home", 1.0, 2.0, "on_enter", 100.0)]
    assert calls["delete"] == []


def test_skips_when_reminder_exists(post_webhook, user, monkeypatch):
    calls = _wire(monkeypatch, reminders=[HOME_REMINDER])
    r = post_webhook(_event(["Home"]))
    assert r.status_code == 200
    assert calls["add"] == []
    assert calls["delete"] == []


def test_deletes_reminder_when_label_removed(post_webhook, user, monkeypatch):
    calls = _wire(monkeypatch, reminders=[HOME_REMINDER])
    r = post_webhook(_event([]))
    assert r.status_code == 200
    assert calls["add"] == []
    assert calls["delete"] == [("tok", "r1")]


def test_api_failure_does_not_delete(post_webhook, user, monkeypatch):
    """B12: a Todoist API failure must not read as 'task has no labels'."""

    def down(endpoint, token, params=None):
        raise requests.exceptions.ConnectionError("todoist down")

    calls = {"delete": []}
    monkeypatch.setattr(app_module, "todoist_api_get", down)
    monkeypatch.setattr(app_module, "todoist_get_reminders", lambda token: [HOME_REMINDER])
    monkeypatch.setattr(app_module, "todoist_add_reminder", lambda *a: None)
    monkeypatch.setattr(app_module, "todoist_delete_reminder", lambda *a: calls["delete"].append(a))
    r = post_webhook(_event(["Home"]))
    assert r.status_code == 503
    assert calls["delete"] == []


def test_rejects_unsigned_delivery(post_webhook, user, monkeypatch):
    """B1: without Todoist's signature nothing is read, added or deleted."""
    calls = _wire(monkeypatch, reminders=[HOME_REMINDER])
    r = post_webhook(_event([]), signature=None)
    assert r.status_code == 401
    assert calls == {"add": [], "delete": []}


def test_rejects_wrong_signature(post_webhook, user, monkeypatch):
    calls = _wire(monkeypatch, reminders=[HOME_REMINDER])
    r = post_webhook(_event([]), signature="bm90IHRoZSBzaWduYXR1cmU=")
    assert r.status_code == 401
    assert calls == {"add": [], "delete": []}


def test_unexpected_labels_shape_does_not_delete(post_webhook, user, monkeypatch):
    """A labels response that is not the v1 {"results": [...]} dict must not read as 'no labels'."""
    calls = {"delete": []}
    monkeypatch.setattr(
        app_module, "todoist_api_get", lambda endpoint, token, params=None: {"error": "x"}
    )
    monkeypatch.setattr(app_module, "todoist_get_reminders", lambda token: [HOME_REMINDER])
    monkeypatch.setattr(app_module, "todoist_add_reminder", lambda *a: None)
    monkeypatch.setattr(app_module, "todoist_delete_reminder", lambda *a: calls["delete"].append(a))
    r = post_webhook(_event(["Home"]))
    assert r.status_code == 503
    assert calls["delete"] == []


def test_failed_add_asks_for_redelivery(post_webhook, user, monkeypatch):
    """A reminder that could not be created must not be reported to Todoist as handled."""
    _wire(monkeypatch, reminders=[])

    def down(*args):
        raise requests.exceptions.ConnectionError("todoist down")

    monkeypatch.setattr(app_module, "todoist_add_reminder", down)
    assert post_webhook(_event(["Home"])).status_code == 503
