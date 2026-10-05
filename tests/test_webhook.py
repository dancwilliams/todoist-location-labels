import pytest
import requests
from conftest import HOME_REMINDER, _sent

import app as app_module

ITEM = HOME_REMINDER["item_id"]
HOME_ADD = (
    "reminder_add",
    {
        "item_id": ITEM,
        "type": "location",
        "name": "Home",
        "loc_lat": "1.0",
        "loc_long": "2.0",
        "loc_trigger": "on_enter",
        "radius": 100,
    },
)


def _event(labels, initiator="1"):
    return {
        "event_name": "item:updated",
        "user_id": "1",
        "initiator": {"id": initiator},
        "event_data": {"id": ITEM, "labels": labels},
    }


@pytest.fixture
def todoist(fake_todoist):
    """fake_todoist for an account whose one label is the `user` fixture's mapped label."""
    fake_todoist["labels"] = [{"id": "10", "name": "Home"}]
    return fake_todoist


def test_adds_reminder_for_mapped_label(post_webhook, user, todoist):
    r = post_webhook(_event(["Home"]))
    assert r.status_code == 200
    assert _sent(todoist) == [HOME_ADD]


def test_skips_when_reminder_exists(post_webhook, user, todoist):
    todoist["reminders"] = [HOME_REMINDER]
    r = post_webhook(_event(["Home"]))
    assert r.status_code == 200
    assert _sent(todoist) == []


def test_deletes_reminder_when_label_removed(post_webhook, user, todoist):
    todoist["reminders"] = [HOME_REMINDER]
    r = post_webhook(_event([]))
    assert r.status_code == 200
    assert _sent(todoist) == [("reminder_delete", {"id": "r1"})]


def test_api_failure_does_not_delete(post_webhook, user, todoist, monkeypatch):
    """B12: a Todoist API failure must not read as 'task has no labels'."""

    def down(endpoint, token, params=None):
        raise requests.exceptions.ConnectionError("todoist down")

    todoist["reminders"] = [HOME_REMINDER]
    monkeypatch.setattr(app_module, "todoist_api_get", down)
    r = post_webhook(_event(["Home"]))
    assert r.status_code == 503
    assert _sent(todoist) == []


def test_rejects_unsigned_delivery(post_webhook, user, todoist):
    """B1: without Todoist's signature nothing is read, added or deleted."""
    todoist["reminders"] = [HOME_REMINDER]
    r = post_webhook(_event([]), signature=None)
    assert r.status_code == 401
    assert todoist["hits"] == 0


def test_rejects_wrong_signature(post_webhook, user, todoist):
    todoist["reminders"] = [HOME_REMINDER]
    r = post_webhook(_event([]), signature="bm90IHRoZSBzaWduYXR1cmU=")
    assert r.status_code == 401
    assert todoist["hits"] == 0


def test_unexpected_labels_shape_does_not_delete(post_webhook, user, todoist, monkeypatch):
    """A labels response that is not the v1 {"results": [...]} dict must not read as 'no labels'."""
    todoist["reminders"] = [HOME_REMINDER]
    monkeypatch.setattr(
        app_module, "todoist_api_get", lambda endpoint, token, params=None: {"error": "x"}
    )
    r = post_webhook(_event(["Home"]))
    assert r.status_code == 503
    assert _sent(todoist) == []


def test_failed_add_asks_for_redelivery(post_webhook, user, todoist, monkeypatch):
    """A reminder that could not be created must not be reported to Todoist as handled."""

    def down(*args):
        raise requests.exceptions.ConnectionError("todoist down")

    monkeypatch.setattr(app_module, "todoist_add_reminder", down)
    assert post_webhook(_event(["Home"])).status_code == 503


def test_refused_add_asks_for_redelivery(post_webhook, user, todoist):
    """D1: Todoist refuses a command inside a 200 answer; that is not a handled event."""
    todoist["refuse"] = ("reminder_add",)
    assert post_webhook(_event(["Home"])).status_code == 503


def test_event_from_a_collaborator_reaches_the_user(post_webhook, user, todoist):
    """C6: in a shared project the initiator may be someone who does not use this app;
    the event is still for the user it was delivered to."""
    r = post_webhook(_event(["Home"], initiator="999"))
    assert r.status_code == 200
    assert _sent(todoist) == [HOME_ADD]
