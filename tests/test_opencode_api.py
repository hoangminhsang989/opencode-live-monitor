import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

import core_monitor
from opencode_client import OpenCodeAPIClient
from api_server import app

def test_api_client_payload_structure():
    client = OpenCodeAPIClient()
    client._url = "http://127.0.0.1:55050"
    client._auth_headers = {"Authorization": "Basic test"}

    with patch("requests.post") as mock_post, patch.object(client, "is_connected", return_value=True):
        mock_post.return_value.status_code = 204

        ok, msg = client.send_prompt("ses_123", "xin chao")
        assert ok is True
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert args[0] == "http://127.0.0.1:55050/session/ses_123/prompt_async"
        assert kwargs["json"] == {
            "parts": [
                {"type": "text", "text": "xin chao"}
            ]
        }

def test_api_client_permission_reply():
    client = OpenCodeAPIClient()
    client._url = "http://127.0.0.1:55050"
    client._auth_headers = {"Authorization": "Basic test"}

    with patch("requests.post") as mock_post, patch.object(client, "is_connected", return_value=True):
        mock_post.return_value.status_code = 200

        ok, msg = client.reply_permission("per_123", reply="once")
        assert ok is True
        args, kwargs = mock_post.call_args
        assert args[0] == "http://127.0.0.1:55050/permission/per_123/reply"
        assert kwargs["json"] == {"reply": "once"}

def test_api_client_question_reply():
    client = OpenCodeAPIClient()
    client._url = "http://127.0.0.1:55050"
    client._auth_headers = {"Authorization": "Basic test"}

    with patch("requests.post") as mock_post, patch.object(client, "is_connected", return_value=True):
        mock_post.return_value.status_code = 200

        ok, msg = client.reply_question("que_123", [["Lua chon 1"]])
        assert ok is True
        args, kwargs = mock_post.call_args
        assert args[0] == "http://127.0.0.1:55050/question/que_123/reply"
        assert kwargs["json"] == {"answers": [["Lua chon 1"]]}

def test_core_monitor_send_prompt_prefers_rest(monkeypatch):
    from opencode_client import default_client

    mock_send = MagicMock(return_value=(True, "OK"))
    monkeypatch.setattr(default_client, "connect", lambda: True)
    monkeypatch.setattr(default_client, "send_prompt", mock_send)
    monkeypatch.setattr(core_monitor, "verify_message_submitted", lambda sid, b_id, timeout_sec: (True, "msg_new"))

    ok, msg = core_monitor.send_prompt_to_opencode_session("Test Session", "lenh tiep tuc", session_id="ses_abc")
    assert ok is True
    assert "REST API" in msg
    mock_send.assert_called_once_with("ses_abc", prompt_text="lenh tiep tuc", image_path=None)

def test_core_monitor_approve_prefers_rest(monkeypatch):
    from opencode_client import default_client

    mock_reply = MagicMock(return_value=(True, "OK"))
    monkeypatch.setattr(default_client, "connect", lambda: True)
    monkeypatch.setattr(default_client, "get_pending_permissions", lambda: [{"id": "per_999", "sessionID": "ses_abc"}])
    monkeypatch.setattr(default_client, "reply_permission", mock_reply)

    ok, msg = core_monitor.approve_permission_in_opencode("Test Session", action="always")
    assert ok is True
    assert "REST API" in msg
    mock_reply.assert_called_once_with("per_999", reply="always")

def test_api_server_health():
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data
    assert data["status"] == "ok"

def test_api_server_status(monkeypatch):
    monkeypatch.setattr(core_monitor, "poll_all_sessions", lambda: [
        {"sessionId": "ses_1", "title": "Test Tab", "color": "green", "state": "running", "detail": "Test"}
    ])
    client = TestClient(app)
    resp = client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    assert data["sessions"][0]["sessionId"] == "ses_1"

def test_api_server_send_endpoint(monkeypatch):
    from opencode_client import default_client

    monkeypatch.setattr(core_monitor, "poll_all_sessions", lambda: [
        {"sessionId": "ses_1", "title": "Test Tab", "color": "green"}
    ])
    monkeypatch.setattr(default_client, "connect", lambda: True)
    monkeypatch.setattr(default_client, "send_prompt", lambda s_id, prompt_text, image_path: (True, "Sent"))
    monkeypatch.setattr(core_monitor, "verify_message_submitted", lambda sid, b_id, timeout_sec: (True, "msg_new"))

    client = TestClient(app)
    resp = client.post("/api/send", json={"session": "Test Tab", "prompt": "chay tiep"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["session_id"] == "ses_1"
