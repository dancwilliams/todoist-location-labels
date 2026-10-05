import app as app_module

BODY = b'{"event_name":"item:added"}'
# base64(HMAC-SHA256(key="test", BODY)), computed with openssl, not with the code under test:
#   printf '%s' '{"event_name":"item:added"}' | openssl dgst -sha256 -hmac test -binary | base64
SIGNATURE = "r+Tg3WSC/dwgQx4gm1sSJOk8vdaUSZUYPsEjGfJntWw="


def test_signature_matches_known_vector(monkeypatch):
    monkeypatch.setattr(app_module, "client_secret", "test")
    assert app_module.webhook_signature_ok(BODY, SIGNATURE) is True


def test_signature_rejects_tampered_body(monkeypatch):
    monkeypatch.setattr(app_module, "client_secret", "test")
    assert app_module.webhook_signature_ok(BODY + b" ", SIGNATURE) is False


def test_signature_rejects_missing_or_wrong_header(monkeypatch):
    monkeypatch.setattr(app_module, "client_secret", "test")
    assert app_module.webhook_signature_ok(BODY, None) is False
    assert app_module.webhook_signature_ok(BODY, "not-a-signature") is False


def test_signature_rejects_non_ascii_header(monkeypatch):
    """C3: garbage in the header is a rejection, not a crash."""
    monkeypatch.setattr(app_module, "client_secret", "test")
    assert app_module.webhook_signature_ok(BODY, "\xe9\xff") is False
