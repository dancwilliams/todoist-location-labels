import pytest
import requests
from requests.adapters import HTTPAdapter

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
            app_module.todoist_run_commands(
                "tok", [app_module.reminder_delete_command("r1")], "reminder_delete"
            )
    assert fake_todoist["hits"] == 1


def test_a_stalled_connect_is_not_retried(monkeypatch):
    """D3: a connect that times out costs one attempt, like a stalled read."""
    attempts = []

    def stalled(address, *args, **kwargs):
        attempts.append(address)
        raise TimeoutError("timed out")

    monkeypatch.setattr("urllib3.util.connection.create_connection", stalled)
    policy = app_module.todoist_http.get_adapter("https://api.todoist.com").max_retries
    monkeypatch.setitem(
        app_module.todoist_http.adapters,
        "https://",
        HTTPAdapter(max_retries=policy.new(backoff_factor=0)),
    )
    with pytest.raises(requests.exceptions.RequestException):
        app_module.todoist_api_get("labels", "tok")
    assert len(attempts) == 1


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


def test_a_repeated_cursor_raises(monkeypatch):
    """B4: a cursor that repeats must raise, not spin until gunicorn kills the worker."""
    calls = []

    def same_page_forever(endpoint, token, params=None):
        calls.append(params)
        assert len(calls) <= 50, "todoist_get_all is looping"
        return {"results": [], "next_cursor": "same"}

    monkeypatch.setattr(app_module, "todoist_api_get", same_page_forever)
    with pytest.raises(requests.exceptions.RequestException):
        app_module.todoist_get_labels("tok")
    assert len(calls) <= 3


def test_reading_reminders_is_not_a_full_sync(fake_todoist):
    """C4, D5: a command sends only itself, and a read is not a sync at all. A full sync
    is limited to 100 per user per 15 minutes."""
    app_module.todoist_run_commands(
        "tok", [app_module.reminder_delete_command("r1")], "reminder_delete"
    )
    app_module.todoist_get_reminders("tok")
    assert fake_todoist["posts"] == [["commands"]]
