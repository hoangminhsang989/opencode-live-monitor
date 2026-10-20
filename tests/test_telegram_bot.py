import pytest
import threading
from telegram_bot import OpenCodeTelegramBot

def test_auth_gate():
    cfg = {"telegram_bot_token": "fake_token", "telegram_chat_id": "123456", "telegram_enabled": True}
    bot = OpenCodeTelegramBot(lambda: cfg)
    
    assert bot.is_authorized(123456) is True
    assert bot.is_authorized("123456") is True
    assert bot.is_authorized(" 123456 ") is True
    assert bot.is_authorized(999999) is False
    assert bot.is_authorized("attacker") is False

def test_menu_keyboard_structure():
    cfg = {"telegram_bot_token": "fake_token", "telegram_chat_id": "123456", "telegram_enabled": True}
    bot = OpenCodeTelegramBot(lambda: cfg)
    kb = bot.get_persistent_menu_keyboard()
    
    assert "keyboard" in kb
    assert kb.get("resize_keyboard") is True
    assert kb.get("persistent") is True
    
    texts = [btn["text"] for row in kb["keyboard"] for btn in row]
    assert any("Bảng trạng thái" in t for t in texts)
    assert any("Chụp màn hình" in t for t in texts)
    assert any("Trợ giúp" in t for t in texts)

def test_build_status_message_with_session_buttons_and_auto_refresh():
    cfg = {
        "telegram_bot_token": "fake_token",
        "telegram_chat_id": "123456",
        "telegram_enabled": True,
        "auto_continue_sessions": {"sid_1": True}
    }
    bot = OpenCodeTelegramBot(lambda: cfg)
    
    sample_sessions = [
        {'title': 'WorkNC', 'sessionId': 'sid_1', 'directory': 'D:/WorkNC', 'color': 'green', 'label': 'Đang chạy', 'detail': 'Chạy test'},
        {'title': 'QR', 'sessionId': 'sid_2', 'directory': 'E:/QR', 'color': 'red', 'label': 'Đã xong', 'detail': 'Phiên đã dừng'},
        {'title': 'HF', 'sessionId': 'sid_3', 'directory': 'D:/HF', 'color': 'yellow', 'label': 'Cần cấp quyền', 'detail': 'Chờ cấp quyền'}
    ]
    # Auto refresh ON
    text, markup = bot.build_status_message(sample_sessions, auto_refresh=True)
    assert "🟢 BẬT" in text
    assert "[⚡Tự chạy]" in text
    assert "inline_keyboard" in markup
    cb_data = [btn["callback_data"] for row in markup["inline_keyboard"] for btn in row]
    
    # Verify per-session buttons exist
    assert "session_detail:sid_1" in cb_data
    assert "session_detail:sid_2" in cb_data
    assert "session_detail:sid_3" in cb_data
    assert "refresh_status" in cb_data
    assert "toggle_refresh:off" in cb_data
    assert "cmd_screen" in cb_data

def test_toggle_auto_continue_config():
    saved_cfg = {}
    def fake_updater(c):
        nonlocal saved_cfg
        saved_cfg = c.copy()

    initial_cfg = {"telegram_bot_token": "token", "telegram_chat_id": "123", "auto_continue_sessions": {}}
    bot = OpenCodeTelegramBot(lambda: initial_cfg, config_updater=fake_updater)

    bot.save_config_field("auto_continue_sessions", {"session_abc": True})
    assert saved_cfg.get("auto_continue_sessions", {}).get("session_abc") is True

    bot.save_config_field("auto_continue_sessions", {"session_abc": False})
    assert saved_cfg.get("auto_continue_sessions", {}).get("session_abc") is False

def test_auto_continue_max_clamp():
    cfg = {"telegram_bot_token": "t", "telegram_chat_id": "123", "telegram_enabled": True}
    bot = OpenCodeTelegramBot(lambda: cfg)

    assert bot._get_auto_continue_max({}) == 1
    assert bot._get_auto_continue_max({"auto_continue_count": 5}) == 5
    assert bot._get_auto_continue_max({"auto_continue_count": 0}) == 1
    assert bot._get_auto_continue_max({"auto_continue_count": -3}) == 1
    assert bot._get_auto_continue_max({"auto_continue_count": 500}) == 99
    assert bot._get_auto_continue_max({"auto_continue_count": "abc"}) == 1
    assert bot._get_auto_continue_max({"auto_continue_count": None}) == 1
    assert bot._get_auto_continue_max({"auto_continue_count": 3, "auto_continue_session_limits": {"s1": 8}}, "s1") == 8
    assert bot._get_auto_continue_max({"auto_continue_count": 3, "auto_continue_session_limits": {"s1": 8}}, "s2") == 3

def test_session_detail_pauses_dashboard_auto_refresh(monkeypatch):
    cfg = {
        "telegram_bot_token": "t", "telegram_chat_id": "123", "telegram_enabled": True,
        "auto_continue_sessions": {"sid_1": True}, "auto_continue_count": 3
    }
    bot = OpenCodeTelegramBot(lambda: cfg)
    bot.active_dashboard = {
        "chat_id": "123", "message_id": 10, "auto_refresh": True,
        "user_auto_refresh": True, "view": "status", "last_content": "old"
    }

    monkeypatch.setattr("telegram_bot.poll_all_sessions", lambda: [
        {"sessionId": "sid_1", "title": "WorkNC", "directory": "D:/W",
         "color": "green", "label": "Đang chạy", "detail": "ok"}
    ])
    monkeypatch.setattr(bot, "edit_message", lambda *a, **k: {"ok": True})

    bot.handle_session_detail("123", 10, "sid_1")

    # Menu chức năng phải không bị vòng auto-refresh ghi đè
    assert bot.active_dashboard["auto_refresh"] is False
    assert bot.active_dashboard["view"] == "detail"
    assert bot.active_dashboard["user_auto_refresh"] is True

def test_check_and_notify_respects_auto_continue_count(monkeypatch):
    cfg = {
        "telegram_bot_token": "t", "telegram_chat_id": "123", "telegram_enabled": True,
        "auto_continue_sessions": {"sid_1": True}, "auto_continue_count": 2
    }
    bot = OpenCodeTelegramBot(lambda: cfg)

    sent = []
    monkeypatch.setattr(bot, "send_message", lambda chat_id, text, markup=None: sent.append(text))
    monkeypatch.setattr(bot, "is_enabled", lambda: True)
    monkeypatch.setattr(bot, "get_authorized_chat_id", lambda: "123")
    monkeypatch.setattr("telegram_bot.get_latest_assistant_response", lambda sid: "answer")

    started = []
    class FakeThread:
        def start(self):
            started.append(1)
    monkeypatch.setattr(threading, "Thread", lambda *a, **k: FakeThread())

    running = [{"sessionId": "sid_1", "title": "W", "state": "running", "directory": "D:/W", "detail": "chạy"}]
    done = [{"sessionId": "sid_1", "title": "W", "state": "completed", "directory": "D:/W", "detail": "xong"}]

    bot.check_and_notify(running)   # lần đầu gặp phiên -> chỉ ghi nhận trạng thái
    assert len(started) == 0

    bot.check_and_notify(done)      # xong lượt 1 -> tự gửi 1/2
    assert bot.auto_continue_sent["sid_1"] == 1
    assert len(started) == 1

    bot.check_and_notify(running)
    bot.check_and_notify(done)      # xong lượt 2 -> tự gửi 2/2
    assert bot.auto_continue_sent["sid_1"] == 2
    assert len(started) == 2

    bot.check_and_notify(running)
    bot.check_and_notify(done)      # hết ngân sách -> không tự gửi nữa
    assert bot.auto_continue_sent["sid_1"] == 2
    assert len(started) == 2
    assert any("Đã gửi đủ 2 lệnh tự động" in t for t in sent)

def test_manual_send_resets_auto_continue_counter():
    cfg = {"telegram_bot_token": "t", "telegram_chat_id": "123", "telegram_enabled": True}
    bot = OpenCodeTelegramBot(lambda: cfg)
    bot.auto_continue_sent["sid_1"] = 2

    bot.auto_continue_sent.pop("sid_1", None)
    assert bot.auto_continue_sent.get("sid_1", 0) == 0

def test_handle_ac_count_text_input(monkeypatch):
    saved_cfg = {}
    def fake_updater(c):
        nonlocal saved_cfg
        saved_cfg = c.copy()

    cfg = {
        "telegram_bot_token": "t", "telegram_chat_id": "123", "telegram_enabled": True,
        "auto_continue_sessions": {}, "auto_continue_session_limits": {}
    }
    bot = OpenCodeTelegramBot(lambda: cfg, config_updater=fake_updater)

    sent = []
    monkeypatch.setattr(bot, "send_message", lambda chat_id, text, markup=None: sent.append(text))
    monkeypatch.setattr(bot, "handle_session_detail", lambda *a, **k: None)

    bot.waiting_ac_count_input["123"] = {
        "session_id": "sid_1",
        "title": "WorkNC",
        "message_id": 50,
        "expires_at": 9999999999
    }

    # Nhap chu khong phai so
    bot.handle_ac_count_text_input("123", "khong phai so")
    assert any("không hợp lệ" in t for t in sent)
    assert "123" in bot.waiting_ac_count_input

    # Nhap so vuot 99
    bot.handle_ac_count_text_input("123", "200")
    assert any("không hợp lệ" in t for t in sent)
    assert "123" in bot.waiting_ac_count_input

    # Nhap so hop le "15"
    sent.clear()
    bot.handle_ac_count_text_input("123", " 15 ")
    assert any("ĐÃ CÀI ĐẶT THÀNH CÔNG" in t for t in sent)
    assert any("15 lần" in t for t in sent)
    assert "123" not in bot.waiting_ac_count_input
    assert saved_cfg.get("auto_continue_session_limits", {}).get("sid_1") == 15
    assert saved_cfg.get("auto_continue_sessions", {}).get("sid_1") is True

def test_auto_continue_already_completed_session(monkeypatch):
    cfg = {
        "telegram_bot_token": "t", "telegram_chat_id": "123", "telegram_enabled": True,
        "auto_continue_sessions": {"sid_1": True}, "auto_continue_count": 3
    }
    bot = OpenCodeTelegramBot(lambda: cfg)
    started = []
    class FakeThread:
        def start(self):
            started.append(1)
    monkeypatch.setattr(threading, "Thread", lambda *a, **k: FakeThread())
    monkeypatch.setattr(bot, "send_message", lambda *a, **k: None)

    # Phiên đã ở trạng thái completed ngay từ lúc bot khởi động
    done = [{"sessionId": "sid_1", "title": "HF", "state": "completed", "directory": "D:/HF", "detail": "xong"}]
    bot.check_and_notify(done)

    # Phải tự động kích hoạt gửi tiếp tục ngay lập tức!
    assert len(started) == 1
    assert bot.auto_continue_sent.get("sid_1") == 1
