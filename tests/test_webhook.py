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


def test_adds_reminder_for_mapped_label(client, user, monkeypatch):
    calls = _wire(monkeypatch, reminders=[])
    r = client.post("/webhook", json=_event(["Home"]))
    assert r.status_code == 200
    assert calls["add"] == [("tok", ITEM, "Home", 1.0, 2.0, "on_enter", 100.0)]
    assert calls["delete"] == []


def test_skips_when_reminder_exists(client, user, monkeypatch):
    calls = _wire(monkeypatch, reminders=[HOME_REMINDER])
    r = client.post("/webhook", json=_event(["Home"]))
    assert r.status_code == 200
    assert calls["add"] == []
    assert calls["delete"] == []


def test_deletes_reminder_when_label_removed(client, user, monkeypatch):
    calls = _wire(monkeypatch, reminders=[HOME_REMINDER])
    r = client.post("/webhook", json=_event([]))
    assert r.status_code == 200
    assert calls["add"] == []
    assert calls["delete"] == [("tok", "r1")]


def test_api_failure_does_not_delete(client, user, monkeypatch):
    """B12: a Todoist API failure must not read as 'task has no labels'."""

    def down(endpoint, token):
        raise requests.exceptions.ConnectionError("todoist down")

    calls = {"delete": []}
    monkeypatch.setattr(app_module, "todoist_api_get", down)
    monkeypatch.setattr(app_module, "todoist_get_reminders", lambda token: [HOME_REMINDER])
    monkeypatch.setattr(app_module, "todoist_add_reminder", lambda *a: None)
    monkeypatch.setattr(app_module, "todoist_delete_reminder", lambda *a: calls["delete"].append(a))
    r = client.post("/webhook", json=_event(["Home"]))
    assert r.status_code == 503
    assert calls["delete"] == []
