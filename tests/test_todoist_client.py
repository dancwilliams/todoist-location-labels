import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
import requests

import app as app_module


@pytest.fixture
def todoist_stub(monkeypatch):
    """A local HTTP server standing in for Todoist; answers every GET with `status`."""
    state = {"status": 200, "hits": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            state["hits"] += 1
            body = b'{"results": []}'
            self.send_response(state["status"])
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(app_module, "TODOIST_API_BASE", f"http://127.0.0.1:{server.server_port}")
    for adapter in app_module.todoist_http.adapters.values():
        monkeypatch.setattr(adapter, "max_retries", adapter.max_retries.new(backoff_factor=0))
    yield state
    server.shutdown()


def test_401_is_not_retried(todoist_stub):
    """B7: a revoked token must cost one request, not three plus waits."""
    todoist_stub["status"] = 401
    with pytest.raises(requests.exceptions.HTTPError):
        app_module.todoist_api_get("labels", "tok")
    assert todoist_stub["hits"] == 1


def test_503_is_retried_three_times_then_raises(todoist_stub):
    todoist_stub["status"] = 503
    with pytest.raises(requests.exceptions.RequestException):
        app_module.todoist_api_get("labels", "tok")
    assert todoist_stub["hits"] == 4


def test_labels_follow_next_cursor(monkeypatch):
    """B13: labels beyond the first page must not read as missing."""
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
