"""
core/gemini.py tests. The google-genai SDK isn't installed in this sandbox
(imports are lazy, inside functions), so these tests focus on what's
directly testable without it: the new OpenRouter fallback rung (key/model
lookup, the HTTP call itself via a mocked requests.post, and its wiring
into call()'s existing ladder/cooldown machinery). The pre-existing Live/
REST ladder logic these tests exercise around OpenRouter had no test
coverage before this - these tests reach it only incidentally, by making
every Gemini rung fail so the ladder falls through to OpenRouter.
"""
import json

import pytest

from core import gemini


@pytest.fixture(autouse=True)
def _isolated_key_file(tmp_path, monkeypatch):
    monkeypatch.setattr(gemini, "_KEY_FILE", tmp_path / "api_keys.json")
    monkeypatch.setattr(gemini, "_cached_key", None)
    monkeypatch.setattr(gemini, "_cached_openrouter_key", None)
    monkeypatch.setattr(gemini, "_cooldown", {})


def _write_keys(tmp_path, gemini_dir, **data):
    (gemini_dir).write_text(json.dumps(data), encoding="utf-8")


# ── openrouter_api_key() / _openrouter_model() ──────────────────────────────

def test_openrouter_api_key_empty_when_file_missing():
    assert gemini.openrouter_api_key() == ""


def test_openrouter_api_key_empty_when_key_not_present(tmp_path):
    gemini._KEY_FILE.write_text(json.dumps({"gemini_api_key": "g-key"}), encoding="utf-8")
    assert gemini.openrouter_api_key() == ""


def test_openrouter_api_key_reads_configured_value():
    gemini._KEY_FILE.write_text(json.dumps({"openrouter_api_key": "sk-or-abc"}), encoding="utf-8")
    assert gemini.openrouter_api_key() == "sk-or-abc"


def test_openrouter_api_key_is_cached_until_refresh():
    gemini._KEY_FILE.write_text(json.dumps({"openrouter_api_key": "sk-or-abc"}), encoding="utf-8")
    assert gemini.openrouter_api_key() == "sk-or-abc"

    gemini._KEY_FILE.write_text(json.dumps({"openrouter_api_key": "sk-or-changed"}), encoding="utf-8")
    assert gemini.openrouter_api_key() == "sk-or-abc"          # stale, cached
    assert gemini.openrouter_api_key(refresh=True) == "sk-or-changed"


def test_openrouter_model_falls_back_when_unset():
    gemini._KEY_FILE.write_text(json.dumps({"openrouter_api_key": "k"}), encoding="utf-8")
    assert gemini._openrouter_model() == gemini._OPENROUTER_MODEL_FALLBACK


def test_openrouter_model_uses_configured_value():
    gemini._KEY_FILE.write_text(
        json.dumps({"openrouter_api_key": "k", "openrouter_model": "some/other-model:free"}),
        encoding="utf-8",
    )
    assert gemini._openrouter_model() == "some/other-model:free"


# ── _contents_to_text() ──────────────────────────────────────────────────────

def test_contents_to_text_plain_string():
    assert gemini._contents_to_text("hello") == "hello"


def test_contents_to_text_list_of_strings():
    assert gemini._contents_to_text(["line one", "line two"]) == "line one\nline two"


def test_contents_to_text_dict_with_text_key():
    assert gemini._contents_to_text([{"text": "hi"}]) == "hi"


def test_contents_to_text_object_with_text_attribute():
    class _Part:
        text = "part text"
    assert gemini._contents_to_text([_Part()]) == "part text"


def test_contents_to_text_image_becomes_a_note_not_silently_dropped():
    class _ImagePart:
        text = None
        inline_data = object()
    result = gemini._contents_to_text([_ImagePart()])
    assert "omitted" in result.lower()


# ── _openrouter_call() ───────────────────────────────────────────────────────

class _FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def test_openrouter_call_raises_without_a_key():
    with pytest.raises(RuntimeError, match="no OpenRouter API key"):
        gemini._openrouter_call("hi", None, 10_000)


def test_openrouter_call_success_returns_reply_text(monkeypatch):
    gemini._KEY_FILE.write_text(json.dumps({"openrouter_api_key": "sk-or-abc"}), encoding="utf-8")
    captured = {}

    def _fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        return _FakeResponse(200, {"choices": [{"message": {"content": "  Sure thing!  "}}]})

    monkeypatch.setattr("requests.post", _fake_post)

    reply = gemini._openrouter_call("What's up?", None, 10_000)

    assert reply.text == "Sure thing!"
    assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer sk-or-abc"
    assert captured["json"]["messages"][-1] == {"role": "user", "content": "What's up?"}
    assert captured["timeout"] >= 10.0


def test_openrouter_call_includes_the_system_instruction(monkeypatch):
    gemini._KEY_FILE.write_text(json.dumps({"openrouter_api_key": "k"}), encoding="utf-8")
    captured = {}

    def _fake_post(*a, **kw):
        captured["json"] = kw["json"]
        return _FakeResponse(200, {"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr("requests.post", _fake_post)

    gemini._openrouter_call("hi", {"system_instruction": "Reply in French."}, 10_000)

    system_msg = captured["json"]["messages"][0]["content"]
    assert "Reply in French." in system_msg


def test_openrouter_call_429_is_reported_as_quota_exhaustion(monkeypatch):
    gemini._KEY_FILE.write_text(json.dumps({"openrouter_api_key": "k"}), encoding="utf-8")
    monkeypatch.setattr("requests.post", lambda *a, **kw: _FakeResponse(429))

    with pytest.raises(RuntimeError, match="429"):
        gemini._openrouter_call("hi", None, 10_000)


def test_openrouter_call_http_error_raises(monkeypatch):
    gemini._KEY_FILE.write_text(json.dumps({"openrouter_api_key": "k"}), encoding="utf-8")
    monkeypatch.setattr("requests.post", lambda *a, **kw: _FakeResponse(500))

    with pytest.raises(RuntimeError):
        gemini._openrouter_call("hi", None, 10_000)


def test_openrouter_call_empty_reply_raises(monkeypatch):
    gemini._KEY_FILE.write_text(json.dumps({"openrouter_api_key": "k"}), encoding="utf-8")
    monkeypatch.setattr("requests.post", lambda *a, **kw: _FakeResponse(200, {"choices": [{"message": {"content": ""}}]}))

    with pytest.raises(RuntimeError, match="empty"):
        gemini._openrouter_call("hi", None, 10_000)


# ── call()'s ladder actually reaches OPENROUTER as the last rung ───────────

def test_call_falls_through_to_openrouter_once_every_gemini_rung_fails(monkeypatch):
    gemini._KEY_FILE.write_text(
        json.dumps({"gemini_api_key": "g-key", "openrouter_api_key": "sk-or-abc"}), encoding="utf-8",
    )

    def _boom_live(contents, config, timeout_ms, key):
        raise RuntimeError("Live is down")
    monkeypatch.setattr(gemini, "_live_call", _boom_live)

    class _BoomClient:
        class models:
            @staticmethod
            def generate_content(**kwargs):
                raise RuntimeError("REST is down")
    monkeypatch.setattr(gemini, "client", lambda timeout_ms=0, key="": _BoomClient())

    monkeypatch.setattr("requests.post", lambda *a, **kw: _FakeResponse(
        200, {"choices": [{"message": {"content": "from OpenRouter"}}]}))

    result = gemini.text("hi", tier=gemini.FAST)

    assert result == "from OpenRouter"


def test_call_returns_default_when_openrouter_has_no_key_either(monkeypatch):
    """Gemini exhausted AND OpenRouter not configured - a real "nothing
    could answer" case, not a crash."""
    gemini._KEY_FILE.write_text(json.dumps({"gemini_api_key": "g-key"}), encoding="utf-8")

    monkeypatch.setattr(gemini, "_live_call", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("down")))

    class _BoomClient:
        class models:
            @staticmethod
            def generate_content(**kwargs):
                raise RuntimeError("REST is down")
    monkeypatch.setattr(gemini, "client", lambda timeout_ms=0, key="": _BoomClient())

    result = gemini.text("hi", tier=gemini.FAST, default="fallback text")

    assert result == "fallback text"


def test_openrouter_is_not_on_the_search_ladder():
    assert gemini.OPENROUTER not in gemini._LADDERS[gemini.SEARCH]


def test_openrouter_cooldown_reuses_the_existing_mechanism(monkeypatch):
    """A 429 from OpenRouter must cool down exactly like a Gemini rung
    running out of quota - no special-casing needed since _cool()/
    _cooling() are keyed by the model string, and "openrouter" is just
    another entry in the ladder tuple."""
    gemini._KEY_FILE.write_text(
        json.dumps({"gemini_api_key": "g-key", "openrouter_api_key": "k"}), encoding="utf-8",
    )
    monkeypatch.setattr(gemini, "_live_call", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("down")))

    class _BoomClient:
        class models:
            @staticmethod
            def generate_content(**kwargs):
                raise RuntimeError("REST is down")
    monkeypatch.setattr(gemini, "client", lambda timeout_ms=0, key="": _BoomClient())
    monkeypatch.setattr("requests.post", lambda *a, **kw: _FakeResponse(429))

    gemini.call("hi", tier=gemini.FAST)

    assert gemini._cooling(gemini.OPENROUTER) is True
