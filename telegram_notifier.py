import json
import threading
import time
import requests
from typing import Optional, Dict

class TelegramNotifier:
    def __init__(self, config_getter):
        self.config_getter = config_getter
        # Map: session_id -> last_state ('running', 'completed', 'waiting_user')
        self.session_states: Dict[str, str] = {}
        # Cooldown tracker to prevent repeated notifications
        self.last_sent_time: Dict[str, float] = {}

    def is_enabled(self) -> bool:
        cfg = self.config_getter()
        token = cfg.get("telegram_bot_token", "").strip()
        chat_id = cfg.get("telegram_chat_id", "").strip()
        enabled = cfg.get("telegram_enabled", False)
        return bool(enabled and token and chat_id)

    def send_raw_message(self, text: str) -> bool:
        cfg = self.config_getter()
        token = cfg.get("telegram_bot_token", "").strip()
        chat_id = cfg.get("telegram_chat_id", "").strip()
        if not token or not chat_id:
            return False

        def _worker():
            try:
                url = f"https://api.telegram.org/bot{token}/sendMessage"
                payload = {
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True
                }
                requests.post(url, json=payload, timeout=8)
            except Exception as e:
                print(f"[TelegramNotifier] Error sending: {e}")

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        return True

    def send_test_message(self) -> str:
        cfg = self.config_getter()
        token = cfg.get("telegram_bot_token", "").strip()
        chat_id = cfg.get("telegram_chat_id", "").strip()
        if not token or not chat_id:
            return "Vui lòng nhập đầy đủ Bot Token và Chat ID!"
        
        try:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = {
                "chat_id": chat_id,
                "text": "⚡ <b>OpenCode Live Monitor</b>\n\n✅ Kết nối Telegram Bot thành công!\nBạn sẽ nhận được thông báo khi phiên làm việc hoàn thành hoặc cần bạn trả lời.",
                "parse_mode": "HTML"
            }
            res = requests.post(url, json=payload, timeout=8)
            if res.status_code == 200:
                return "OK"
            else:
                return f"Lỗi từ Telegram ({res.status_code}): {res.text}"
        except Exception as e:
            return f"Lỗi kết nối: {str(e)}"

    def check_and_notify(self, sessions: list):
        if not self.is_enabled():
            return

        now = time.time()
        for s in sessions:
            sid = s.get("sessionId")
            if not sid:
                continue

            current_state = s.get("state")  # 'running', 'completed', 'waiting_user'
            prev_state = self.session_states.get(sid)
            
            # Initial state tracking (don't alert on first discovery unless waiting_user)
            if prev_state is None:
                self.session_states[sid] = current_state
                continue

            # Detect state transition
            if current_state != prev_state:
                self.session_states[sid] = current_state
                title = s.get("title", sid)
                directory = s.get("directory", "")
                detail = s.get("detail", "")
                now_str = time.strftime("%H:%M:%S")

                # Case 1: Session Completed (Chuyển sang ĐÃ XONG / ĐÃ DỪNG)
                if current_state == "completed" and prev_state == "running":
                    msg = (
                        f"🔴 <b>[OPENCODE - ĐÃ XONG]</b>\n\n"
                        f"📌 <b>Phiên:</b> {title}\n"
                        f"📁 <b>Thư mục:</b> <code>{directory}</code>\n"
                        f"ℹ️ <b>Trạng thái:</b> {detail}\n"
                        f"⏰ <b>Thời gian:</b> {now_str}"
                    )
                    self.send_raw_message(msg)

                # Case 2: Session Waiting User (Chuyển sang CẦN BẠN TRẢ LỜI)
                elif current_state == "waiting_user":
                    msg = (
                        f"🟡 <b>[OPENCODE - CẦN BẠN TRẢ LỜI]</b>\n\n"
                        f"📌 <b>Phiên:</b> {title}\n"
                        f"📁 <b>Thư mục:</b> <code>{directory}</code>\n"
                        f"❓ <b>Nội dung:</b> {detail}\n"
                        f"⏰ <b>Thời gian:</b> {now_str}"
                    )
                    self.send_raw_message(msg)
