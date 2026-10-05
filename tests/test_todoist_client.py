import pytest
import requests

import app as app_module


def test_401_is_not_retried(fake_todoist):
    """A revoked token must cost one request, not three plus waits."""
    fake_todoist["status"] = 401
    with pytest.raises(requests.exceptions.HTTPError):
        app_module.todoist_api_get("labels", "tok")
    assert fake_todoist["hits"] == 1


def test_503_is_retried_three_times_then_raises(fake_todoist):
    fake_todoist["status"] = 503
    with pytest.raises(requests.exceptions.RequestException):
        app_module.todoist_api_get("labels", "tok")
    assert fake_todoist["hits"] == 4


@pytest.mark.parametrize("call", ["read", "write"])
def test_a_stalled_todoist_is_not_retried(fake_todoist, monkeypatch, call):
    """C2: a stall costs one timeout, so a webhook cannot outlast the worker timeout."""
    monkeypatch.setattr(app_module, "TODOIST_TIMEOUT", 0.2)
    fake_todoist["stall"] = 0.6
    with pytest.raises(requests.exceptions.RequestException):
        if call == "read":
            app_module.todoist_api_get("labels", "tok")
        else:
            app_module.todoist_delete_reminder("tok", "r1")
    assert fake_todoist["hits"] == 1


def test_labels_follow_next_cursor(monkeypatch):
    """Labels beyond the first page must not read as missing."""
    pages = {
        None: {"results": [{"id": "1", "name": "A"}], "next_cursor": "c1"},
        "c1": {"results": [{"id": "2", "name": "B"}], "next_cursor": None},
    }
    seen = []

    def fake_get(endpoint, token, params=None):
        seen.append((endpoint, (params or {}).get("cursor")))
        return pages[(params or {}).get("cursor")]

    monkeypatch.setattr(app_module, "todoist_api_get", fake_get)
    assert [label["name"] for label in app_module.todoist_get_labels("tok")] == ["A", "B"]
    assert seen == [("labels", None), ("labels", "c1")]


def test_a_command_does_not_ask_for_a_full_sync(fake_todoist):
    """C4: adding or deleting a reminder sends the command and nothing else."""
    app_module.todoist_delete_reminder("tok", "r1")
    app_module.todoist_get_reminders("tok")
    assert fake_todoist["posts"] == [["commands"], ["resource_types", "sync_token"]]
