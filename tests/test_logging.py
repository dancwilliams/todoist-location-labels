import logging


def test_health_check_is_not_logged(client, caplog):
    """Fly's health check carries no Fly-Client-IP; it must not fill the log."""
    caplog.set_level(logging.INFO)
    assert client.get("/").status_code == 200
    assert [r for r in caplog.records if "Request made to" in r.getMessage()] == []


def test_real_visitor_is_logged(client, caplog):
    caplog.set_level(logging.INFO)
    client.get("/", headers={"Fly-Client-IP": "203.0.113.7"})
    assert [r.getMessage() for r in caplog.records if "Request made to" in r.getMessage()] == [
        "Request made to /: IP 203.0.113.7"
    ]
