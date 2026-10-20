import io
import os
import sys
import time
import json
import threading
import requests
from PIL import ImageGrab
from typing import Dict, Any, List, Optional

from core_monitor import (
    poll_all_sessions, get_latest_assistant_response, send_prompt_to_opencode_session,
    get_active_question_for_session, approve_permission_in_opencode, select_question_option_in_opencode,
    dismiss_question_in_opencode, create_new_session_in_opencode, close_session_in_opencode,
    switch_session_model_in_opencode, get_session_model_in_opencode
)

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config.json")
LOG_FILE = os.path.join(os.path.dirname(__file__), "monitor.log")
TEMP_DIR = os.path.join(os.environ.get("LOCALAPPDATA", "C:/tmp"), "Temp", "opencode").replace("\\", "/")
os.makedirs(TEMP_DIR, exist_ok=True)

def log_msg(text: str):
    """Ghi nhật ký hoạt động vào tệp monitor.log."""
    try:
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{now}] {text}\n")
    except Exception:
        pass

class OpenCodeTelegramBot:
    def __init__(self, config_getter, config_updater=None):
        self.config_getter = config_getter
        self.config_updater = config_updater
        self.running = False
        self.polling_thread: Optional[threading.Thread] = None
        self.refresh_thread: Optional[threading.Thread] = None
        self.last_update_id = 0
        
        # State tracking to notify on changes
        self.session_states: Dict[str, str] = {}
        
        # Active live auto-refresh dashboard tracking
        self.active_dashboard: Optional[Dict[str, Any]] = None

        # Đếm số lệnh tự động tiếp tục đã gửi cho mỗi phiên (theo cấu hình auto_continue_count)
        self.auto_continue_sent: Dict[str, int] = {}

        # Các phiên đang trong quá trình thực thi gửi tự động tiếp tục (tránh chạy trùng lặp)
        self.auto_continue_in_progress: set = set()

        # Các phiên đang trong quá trình gửi lệnh thủ công / prompt (tránh gửi trùng khi bấm liên tiếp)
        self.prompt_send_in_progress: set = set()

        # User targeted chat session mode
        self.user_chat_target: Dict[str, Dict[str, Any]] = {}

        # Pending user input waiting for session selection
        self.pending_input: Dict[str, Dict[str, Any]] = {}

        # Chờ người dùng gõ số lượng lệnh tự động gửi thủ công bằng bàn phím
        self.waiting_ac_count_input: Dict[str, Dict[str, Any]] = {}

        # Active question choices tracking for interactive callbacks
        # { session_id: { "title": str, "options": list, "selected_indices": set, "multiple": bool } }
        self.question_state: Dict[str, Dict[str, Any]] = {}

        # Reusable HTTP connection pool for low latency
        self.http_session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=10, max_retries=1)
        self.http_session.mount("https://", adapter)
        self.http_session.mount("http://", adapter)

    def get_token(self) -> str:
        return self.config_getter().get("telegram_bot_token", "").strip()

    def get_authorized_chat_id(self) -> str:
        return str(self.config_getter().get("telegram_chat_id", "")).strip()

    def is_enabled(self) -> bool:
        cfg = self.config_getter()
        return bool(cfg.get("telegram_enabled", False) and self.get_token() and self.get_authorized_chat_id())

    def is_authorized(self, chat_id: Any) -> bool:
        auth_id = self.get_authorized_chat_id()
        if not auth_id:
            return False
        return str(chat_id).strip() == auth_id

    def save_config_field(self, key: str, value: Any):
        cfg = self.config_getter()
        cfg[key] = value
        if self.config_updater:
            self.config_updater(cfg)
        else:
            try:
                with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                    json.dump(cfg, f)
            except Exception as e:
                print(f"[TelegramBot] Failed to save config: {e}")

    def get_persistent_menu_keyboard(self) -> Dict[str, Any]:
        """Bàn phím Menu nút bấm cố định dưới màn hình chat Telegram."""
        return {
            "keyboard": [
                [{"text": "📊 Bảng trạng thái"}, {"text": "📸 Chụp màn hình"}],
                [{"text": "➕ Mở phiên mới"}, {"text": "❓ Trợ giúp"}]
            ],
            "resize_keyboard": True,
            "persistent": True
        }

    def api_request(self, method: str, data: dict = None, files: dict = None, timeout: int = 15) -> Optional[dict]:
        token = self.get_token()
        if not token:
            return None
        url = f"https://api.telegram.org/bot{token}/{method}"
        try:
            if files:
                res = self.http_session.post(url, data=data, files=files, timeout=timeout)
            else:
                res = self.http_session.post(url, json=data, timeout=timeout)
            if res.status_code == 200:
                return res.json()
            else:
                print(f"[TelegramBot] API {method} error: {res.status_code} - {res.text}")
        except Exception as e:
            print(f"[TelegramBot] API {method} exception: {e}")
        return None

    def register_bot_commands(self):
        """Đăng ký menu lệnh chuẩn (nút Menu góc trái khung chat Telegram)."""
        commands = [
            {"command": "status", "description": "Xem bảng trạng thái các phiên OpenCode"},
            {"command": "new", "description": "Tạo / mở một phiên làm việc mới"},
            {"command": "screen", "description": "Chụp ảnh màn hình máy tính gửi về điện thoại"},
            {"command": "help", "description": "Xem hướng dẫn và các phím tắt"}
        ]
        self.api_request("setMyCommands", {"commands": commands})

    def start(self):
        if self.running or not self.is_enabled():
            return
        self.running = True
        self.register_bot_commands()
        
        # 1. Thread nhận lệnh từ Telegram (Long Polling)
        self.polling_thread = threading.Thread(target=self._polling_loop, daemon=True)
        self.polling_thread.start()

        # 2. Thread tự động làm mới bảng trạng thái (Auto-refresh)
        self.refresh_thread = threading.Thread(target=self._auto_refresh_loop, daemon=True)
        self.refresh_thread.start()
        
        print("[TelegramBot] Polling & Auto-refresh threads started.")

    def stop(self):
        self.running = False

    def send_photo(self, chat_id: str, image_bytes: bytes, caption: str = ""):
        token = self.get_token()
        if not token:
            return
        url = f"https://api.telegram.org/bot{token}/sendPhoto"
        try:
            files = {"photo": ("screenshot.jpg", image_bytes, "image/jpeg")}
            data = {"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"}
            self.http_session.post(url, data=data, files=files, timeout=25)
        except Exception as e:
            print(f"[TelegramBot] sendPhoto failed: {e}")

    def send_message(self, chat_id: str, text: str, reply_markup: dict = None):
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        else:
            payload["reply_markup"] = self.get_persistent_menu_keyboard()
        return self.api_request("sendMessage", payload)

    def edit_message(self, chat_id: str, message_id: int, text: str, reply_markup: dict = None):
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        return self.api_request("editMessageText", payload)

    def download_telegram_file(self, file_id: str, dest_path: str) -> bool:
        token = self.get_token()
        if not token:
            return False
        info = self.api_request("getFile", {"file_id": file_id})
        if not info or not info.get("ok"):
            return False
        file_path = info.get("result", {}).get("file_path")
        if not file_path:
            return False
        url = f"https://api.telegram.org/file/bot{token}/{file_path}"
        try:
            r = requests.get(url, timeout=25)
            if r.status_code == 200:
                with open(dest_path, "wb") as f:
                    f.write(r.content)
                return True
        except Exception as e:
            print(f"[TelegramBot] download_telegram_file failed: {e}")
        return False

    # --- HANDLERS ---

    def handle_status(self, chat_id: str, message_id: int = None, auto_refresh: bool = True):
        sessions = poll_all_sessions()
        text, markup = self.build_status_message(sessions, auto_refresh=auto_refresh)
        if message_id:
            res = self.edit_message(chat_id, message_id, text, markup)
            msg_id = message_id
        else:
            res = self.send_message(chat_id, text, markup)
            msg_id = res.get("result", {}).get("message_id") if res else None

        # Track this dashboard for live auto-refresh
        if msg_id:
            self.active_dashboard = {
                "chat_id": chat_id,
                "message_id": msg_id,
                "auto_refresh": auto_refresh,
                "user_auto_refresh": auto_refresh,
                "view": "status",
                "last_updated": time.time(),
                "last_content": text
            }

    def _pause_dashboard_auto_refresh(self):
        """Tạm dừng tự làm mới bảng trạng thái khi tin nhắn dashboard đang hiển thị giao diện khác
        (chi tiết phiên, soạn lệnh...) để tránh vòng lặp auto-refresh ghi đè menu người dùng đang bấm."""
        if self.active_dashboard:
            self.active_dashboard["auto_refresh"] = False
            self.active_dashboard["view"] = "detail"

    @staticmethod
    def _get_auto_continue_max(cfg: dict, session_id: str = None) -> int:
        """Số lượng lệnh tự động gửi tối đa (ưu tiên cấu hình riêng cho từng phiên nếu có, mặc định 1, giới hạn 1-99)."""
        if session_id:
            s_map = cfg.get("auto_continue_session_limits", {})
            if isinstance(s_map, dict) and session_id in s_map:
                try:
                    return max(1, min(99, int(s_map[session_id])))
                except (TypeError, ValueError):
                    pass
        try:
            return max(1, min(99, int(cfg.get("auto_continue_count", 1))))
        except (TypeError, ValueError):
            return 1

    def build_status_message(self, sessions: list, auto_refresh: bool = True) -> tuple:
        now_str = time.strftime("%H:%M:%S")
        refresh_tag = "🟢 BẬT" if auto_refresh else "⏸ TẮT"
        
        lines = [f"⚡ <b>BẢNG GIÁM SÁT OPENCODE</b>"]
        lines.append(f"🔄 <i>Tự động làm mới: {refresh_tag} ({now_str})</i>\n")
        
        counts = {"green": 0, "yellow": 0, "red": 0}
        for s in sessions:
            c = s.get("color", "green")
            counts[c] = counts.get(c, 0) + 1

        summary = f"🟢 <b>{counts['green']} Đang chạy</b>  |  🟡 <b>{counts['yellow']} Cần trả lời</b>  |  🔴 <b>{counts['red']} Đã xong</b>\n"
        lines.append(summary)
        lines.append("─────────────────────")

        # Session buttons
        session_buttons = []
        if not sessions:
            lines.append("<i>Hiện không có phiên OpenCode nào đang mở.</i>")
        else:
            cfg = self.config_getter()
            ac_map = cfg.get("auto_continue_sessions", {})

            for i, s in enumerate(sessions, 1):
                icon = "🟢" if s.get("color") == "green" else ("🟡" if s.get("color") == "yellow" else "🔴")
                title = s.get("title", f"Phiên {i}")
                sid = s.get("sessionId", "")
                directory = s.get("directory", "")
                dir_name = os.path.basename(directory.rstrip('\\/')) or directory
                detail = s.get("detail", "")
                
                ac_tag = " [⚡Tự chạy]" if ac_map.get(sid, False) else ""
                lines.append(f"<b>{i}.</b> {icon} <b>{title}</b>{ac_tag}")
                lines.append(f"   📁 <code>[{dir_name}]</code>  👉 <i>{detail}</i>\n")
                
                short_title = title if len(title) <= 14 else title[:13] + "…"
                session_buttons.append({"text": f"{icon} {i}. {short_title}", "callback_data": f"session_detail:{sid}"})

        inline_keyboard = []
        for i in range(0, len(session_buttons), 2):
            inline_keyboard.append(session_buttons[i:i+2])

        toggle_refresh_btn = (
            {"text": "⏸ Tắt tự làm mới", "callback_data": "toggle_refresh:off"}
            if auto_refresh else
            {"text": "▶️ Bật tự làm mới", "callback_data": "toggle_refresh:on"}
        )
        inline_keyboard.append([
            {"text": "🔄 Làm mới ngay", "callback_data": "refresh_status"},
            toggle_refresh_btn
        ])
        inline_keyboard.append([
            {"text": "➕ Mở phiên mới", "callback_data": "menu_new_session"},
            {"text": "📸 Chụp màn hình", "callback_data": "cmd_screen"}
        ])

        return "\n".join(lines), {"inline_keyboard": inline_keyboard}

    def get_title_for_session(self, session_id: str) -> str:
        sessions = poll_all_sessions()
        target = next((s for s in sessions if s.get("sessionId") == session_id), None)
        if target and target.get("title"):
            return target.get("title")
        from core_monitor import DB_PATH
        if os.path.exists(DB_PATH):
            try:
                import sqlite3
                conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
                c = conn.cursor()
                row = c.execute("SELECT title FROM session WHERE id = ?", (session_id,)).fetchone()
                conn.close()
                if row and row[0]:
                    return row[0]
            except Exception:
                pass
        return session_id

    def handle_session_detail(self, chat_id: str, message_id: int, session_id: str):
        sessions = poll_all_sessions()
        target = next((s for s in sessions if s.get("sessionId") == session_id), None)
        
        title = self.get_title_for_session(session_id)
        directory = target.get("directory", "") if target else ""
        color = target.get("color", "red") if target else "red"
        label = target.get("label", "Đã xong") if target else "Đã xong"
        detail = target.get("detail", "Hoàn thành") if target else "Phiên đã dừng"
        icon = "🟢" if color == "green" else ("🟡" if color == "yellow" else "🔴")

        cfg = self.config_getter()
        ac_map = cfg.get("auto_continue_sessions", {})
        is_ac_on = ac_map.get(session_id, False)
        ac_max = self._get_auto_continue_max(cfg, session_id)
        ac_sent = self.auto_continue_sent.get(session_id, 0)
        
        ac_btn_text = "⚡ Tự gửi 'tiếp tục': 🟢 BẬT" if is_ac_on else "⚡ Tự gửi 'tiếp tục': ⏸ TẮT"
        ac_callback = f"toggle_ac:{session_id}:off" if is_ac_on else f"toggle_ac:{session_id}:on"

        if is_ac_on:
            ac_state_text = f"🟢 BẬT (đã tự gửi {ac_sent}/{ac_max} lệnh)"
        else:
            ac_state_text = f"⏸ TẮT (mức đặt: {ac_max} lệnh)"

        model_info = get_session_model_in_opencode(session_id)
        model_display = "Mặc định hệ thống"
        if model_info:
            mid = model_info.get("id", "")
            pid = model_info.get("providerID", "")
            model_display = f"{mid} ({pid})" if pid else mid

        text = (
            f"📌 <b>CHI TIẾT PHIÊN LÀM VIỆC</b>\n\n"
            f"🏷️ <b>Tiêu đề:</b> <b>{title}</b>\n"
            f"📁 <b>Thư mục:</b> <code>{directory}</code>\n"
            f"📊 <b>Trạng thái:</b> {icon} <b>{label}</b>\n"
            f"ℹ️ <b>Chi tiết:</b> <i>{detail}</i>\n"
            f"🧠 <b>Model AI:</b> <code>{model_display}</code>\n"
            f"⚡ <b>Tự động tiếp tục:</b> {ac_state_text}\n"
            f"─────────────────────\n"
            f"👇 <i>Chọn thao tác hoặc chỉnh số lần tự gửi bên dưới:</i>"
        )

        self.waiting_ac_count_input.pop(chat_id, None)

        buttons = [
            [{"text": "📜 Xem câu trả lời gần nhất", "callback_data": f"read_ans:{session_id}"}],
            [{"text": "✍️ Soạn phản hồi / Gửi ảnh", "callback_data": f"chat_mode:{session_id}"}],
            [{"text": "💬 Gửi 'tiếp tục' ngay", "callback_data": f"send_prompt:{session_id}"}],
            [
                {"text": "🧠 Đổi Model AI", "callback_data": f"choose_model:{session_id}"},
                {"text": "❌ Đóng phiên này", "callback_data": f"confirm_close_session:{session_id}"}
            ],
            [{"text": ac_btn_text, "callback_data": ac_callback}],
            [
                {"text": "➖ 1", "callback_data": f"adjust_ac_count:{session_id}:-1"},
                {"text": f"🎯 Tối đa: {ac_max} lần", "callback_data": f"cycle_ac_count:{session_id}"},
                {"text": "➕ 1", "callback_data": f"adjust_ac_count:{session_id}:1"}
            ],
            [
                {"text": "1 lần", "callback_data": f"set_ac_count:{session_id}:1"},
                {"text": "3 lần", "callback_data": f"set_ac_count:{session_id}:3"},
                {"text": "5 lần", "callback_data": f"set_ac_count:{session_id}:5"},
                {"text": "10 lần", "callback_data": f"set_ac_count:{session_id}:10"}
            ],
            [{"text": "✍️ Tự gõ số lượng khác (Bàn phím)", "callback_data": f"prompt_ac_input:{session_id}"}],
            [{"text": "🔙 Quay lại bảng trạng thái", "callback_data": "back_to_status"}]
        ]
        markup = {"inline_keyboard": buttons}
        self._pause_dashboard_auto_refresh()
        self.edit_message(chat_id, message_id, text, markup)

    def activate_ac_count_input_mode(self, chat_id: str, session_id: str, message_id: int = None):
        """Kích hoạt chế độ cho người dùng gõ số lượng lệnh tự động gửi từ bàn phím."""
        title = self.get_title_for_session(session_id)
        cfg = self.config_getter()
        curr_max = self._get_auto_continue_max(cfg, session_id)

        self.waiting_ac_count_input[chat_id] = {
            "session_id": session_id,
            "title": title,
            "message_id": message_id,
            "expires_at": time.time() + 300 # 5 phút hết hạn
        }

        text = (
            f"🔢 <b>NHẬP SỐ LƯỢNG LỆNH TỰ ĐỘNG GỬI</b>\n\n"
            f"📌 Phiên: <b>{title}</b>\n"
            f"⚙️ Mức hiện tại: <b>{curr_max} lần</b>\n\n"
            f"👉 <b>Hãy gõ và gửi một con số từ 1 đến 99</b> vào khung chat Telegram này.\n"
            f"<i>(Ví dụ: gõ <code>15</code> hoặc <code>25</code> rồi bấm Gửi)</i>\n\n"
            f"<i>Chế độ này tự hết hạn sau 5 phút nếu bạn không nhập.</i>"
        )
        markup = {
            "inline_keyboard": [
                [{"text": "❌ Hủy bỏ / Quay lại", "callback_data": f"session_detail:{session_id}"}]
            ]
        }
        if message_id:
            self._pause_dashboard_auto_refresh()
            self.edit_message(chat_id, message_id, text, markup)
        else:
            self.send_message(chat_id, text, markup)

    def handle_ac_count_text_input(self, chat_id: str, text: str):
        """Xử lý con số mà người dùng vừa gõ từ bàn phím Telegram."""
        target = self.waiting_ac_count_input.get(chat_id)
        if not target or time.time() > target.get("expires_at", 0):
            self.waiting_ac_count_input.pop(chat_id, None)
            self.handle_incoming_user_message(chat_id, text=text)
            return

        sid = target["session_id"]
        title = target["title"]
        msg_id = target.get("message_id")

        clean_text = text.strip()
        try:
            val = int(clean_text)
            if not (1 <= val <= 99):
                raise ValueError("Out of range")
        except (ValueError, TypeError):
            self.send_message(
                chat_id,
                f"⚠️ Con số <code>{clean_text}</code> không hợp lệ!\n"
                f"Vui lòng chỉ gõ một số nguyên từ <b>1 đến 99</b> (Ví dụ: <code>15</code>)."
            )
            return

        # Con số hợp lệ! Cập nhật cấu hình
        self.waiting_ac_count_input.pop(chat_id, None)
        cfg = self.config_getter()
        s_limits = cfg.get("auto_continue_session_limits", {}).copy()
        s_limits[sid] = val
        self.save_config_field("auto_continue_session_limits", s_limits)
        self.save_config_field("auto_continue_count", val)

        # Tự động BẬT luôn chế độ tự chạy cho phiên này
        ac_map = cfg.get("auto_continue_sessions", {}).copy()
        ac_map[sid] = True
        self.save_config_field("auto_continue_sessions", ac_map)
        self.auto_continue_sent.pop(sid, None)

        self.send_message(
            chat_id,
            f"✅ <b>ĐÃ CÀI ĐẶT THÀNH CÔNG!</b>\n\n"
            f"📌 Phiên: <b>{title}</b>\n"
            f"⚡ Tự động gửi 'tiếp tục': 🟢 <b>BẬT</b>\n"
            f"🔢 Số lượng lệnh tối đa: <b>{val} lần</b>\n\n"
            f"Mỗi khi phiên chạy xong, bot sẽ tự động gửi lệnh 'tiếp tục' (tối đa {val} lần) cho bạn!"
        )

        # Tự động kích hoạt gửi ngay nếu phiên đang dừng
        self.trigger_auto_continue_if_eligible(sid, title, chat_id)

        if msg_id:
            try:
                self.handle_session_detail(chat_id, msg_id, sid)
            except Exception:
                pass

    def handle_read_answer(self, chat_id: str, session_id: str, message_id: int = None):
        answer = get_latest_assistant_response(session_id)
        title = self.get_title_for_session(session_id)

        if not answer:
            text = (
                f"ℹ️ <b>[CÂU TRẢ LỜI]</b>\n"
                f"📌 <b>Phiên:</b> <b>{title}</b>\n\n"
                f"<i>Chưa có câu trả lời dạng văn bản nào từ AI trong phiên này.</i>"
            )
        else:
            clean_answer = (
                answer.replace("&", "&amp;")
                      .replace("<", "&lt;")
                      .replace(">", "&gt;")
            )
            if len(clean_answer) > 3500:
                clean_answer = clean_answer[:3500] + "\n\n<i>...(Nội dung quá dài, đã được rút gọn)</i>"
            
            text = (
                f"💬 <b>CÂU TRẢ LỜI GẦN NHẤT CỦA AI:</b>\n"
                f"📌 <b>Phiên:</b> <b>{title}</b>\n"
                f"─────────────────────\n\n"
                f"{clean_answer}"
            )

        markup = {
            "inline_keyboard": [
                [{"text": "✍️ Phản hồi / Gửi ảnh vào phiên này", "callback_data": f"chat_mode:{session_id}"}],
                [{"text": "💬 Gửi 'tiếp tục' ngay", "callback_data": f"send_prompt:{session_id}"}],
                [{"text": "🔙 Quay lại phiên này", "callback_data": f"session_detail:{session_id}"}],
                [{"text": "📊 Xem bảng trạng thái", "callback_data": "back_to_status"}]
            ]
        }
        self.send_message(chat_id, text, markup)

    def activate_chat_mode(self, chat_id: str, session_id: str, message_id: int = None):
        title = self.get_title_for_session(session_id)
        
        self.user_chat_target[chat_id] = {
            "session_id": session_id,
            "title": title,
            "expires_at": time.time() + 600 # 10 minutes timeout
        }

        text = (
            f"🎯 <b>KẾT NỐI SOẠN LỆNH CHO PHIÊN:</b>\n"
            f"📌 <b>{title}</b>\n\n"
            f"👉 <b>Hãy gửi tin nhắn tiếp theo</b> (tin nhắn văn bản hoặc <b>kèm ảnh</b>).\n"
            f"Nội dung sẽ được đưa thẳng vào ô nhập lệnh của phiên này trong OpenCode!\n\n"
            f"<i>(Chế độ này tự hết hạn sau 10 phút hoặc bấm Hủy bên dưới)</i>"
        )
        markup = {
            "inline_keyboard": [
                [{"text": "❌ Hủy chế độ soạn lệnh", "callback_data": "cancel_chat_mode"}],
                [{"text": "🔙 Quay lại phiên này", "callback_data": f"session_detail:{session_id}"}]
            ]
        }
        if message_id:
            self._pause_dashboard_auto_refresh()
            self.edit_message(chat_id, message_id, text, markup)
        else:
            self.send_message(chat_id, text, markup)

    def handle_new_session_menu(self, chat_id: str, message_id: int = None):
        """Hiển thị menu tạo phiên làm việc mới với danh sách thư mục dự án 1 chạm."""
        text = (
            "➕ <b>MỞ PHIÊN LÀM VIỆC MỚI</b>\n\n"
            "👉 Hãy chọn một <b>thư mục dự án</b> bên dưới để OpenCode mở phiên làm việc ngay:\n\n"
            "<i>(Phiên sẽ được tạo ngầm và tự động xuất hiện trên bảng trạng thái)</i>"
        )
        buttons = [
            [{"text": "📐 E:\\auto-3d (CAD -> 3D)", "callback_data": "create_session_proj:E:\\auto-3d"}],
            [{"text": "📱 E:\\QR (HMS-QR Mobile Web)", "callback_data": "create_session_proj:E:\\QR"}],
            [{"text": "⚙️ D:\\HMS-CAM-W (WorkNC Clone)", "callback_data": "create_session_proj:D:\\HMS-CAM-W"}],
            [{"text": "🎮 D:\\GAME\\HF (CFVN Replica)", "callback_data": "create_session_proj:D:\\GAME\\HF"}],
            [{"text": "🤖 E:\\giam sat ai (Live Monitor)", "callback_data": "create_session_proj:E:\\giam sat ai"}],
            [{"text": "📁 Thư mục mặc định (Users)", "callback_data": "create_session_proj:default"}],
            [{"text": "🔙 Quay lại bảng trạng thái", "callback_data": "back_to_status"}]
        ]
        markup = {"inline_keyboard": buttons}
        if message_id:
            self._pause_dashboard_auto_refresh()
            self.edit_message(chat_id, message_id, text, markup)
        else:
            self.send_message(chat_id, text, markup)

    def handle_create_session(self, chat_id: str, proj: str, message_id: int = None):
        target_dir = "" if proj == "default" else proj
        folder_name = os.path.basename(target_dir.rstrip("\\/")) if target_dir else "Phiên mới"
        title = f"{folder_name}"
        
        ok, res = create_new_session_in_opencode(title=title, directory=target_dir)
        if ok:
            sid = res.get("id") if isinstance(res, dict) else ""
            msg = (
                f"✅ <b>ĐÃ MỞ PHIÊN MỚI THÀNH CÔNG!</b>\n\n"
                f"📌 <b>Tiêu đề:</b> <b>{title}</b>\n"
                f"📁 <b>Thư mục:</b> <code>{target_dir or 'Mặc định'}</code>\n"
                f"🆔 <b>Session ID:</b> <code>{sid}</code>\n\n"
                f"Phiên đã xuất hiện trên OpenCode và bảng trạng thái! 🟢"
            )
            buttons = []
            if sid:
                buttons.append([{"text": "💬 Gửi lệnh đầu tiên", "callback_data": f"chat_mode:{sid}"}])
                buttons.append([{"text": "🧠 Cài đặt Model", "callback_data": f"choose_model:{sid}"}])
            buttons.append([{"text": "📊 Xem bảng trạng thái", "callback_data": "back_to_status"}])
            markup = {"inline_keyboard": buttons}
            if message_id:
                self.edit_message(chat_id, message_id, msg, markup)
            else:
                self.send_message(chat_id, msg, markup)
        else:
            err_msg = f"❌ <b>Không thể mở phiên mới:</b>\n<i>{res}</i>"
            if message_id:
                self.edit_message(chat_id, message_id, err_msg, {"inline_keyboard": [[{"text": "🔙 Quay lại", "callback_data": "menu_new_session"}]]})
            else:
                self.send_message(chat_id, err_msg)

    def handle_confirm_close_session(self, chat_id: str, message_id: int, session_id: str):
        title = self.get_title_for_session(session_id)
        text = (
            f"⚠️ <b>XÁC NHẬN ĐÓNG PHIÊN</b>\n\n"
            f"📌 Phiên: <b>{title}</b>\n"
            f"🆔 ID: <code>{session_id}</code>\n\n"
            f"Bạn có chắc chắn muốn đóng phiên làm việc này không?\n"
            f"<i>(Phiên sẽ được gỡ khỏi OpenCode và bảng trạng thái)</i>"
        )
        buttons = [
            [{"text": "🗑️ Xác nhận đóng ngay", "callback_data": f"do_close_session:{session_id}"}],
            [{"text": "🔙 Hủy / Quay lại", "callback_data": f"session_detail:{session_id}"}]
        ]
        self._pause_dashboard_auto_refresh()
        self.edit_message(chat_id, message_id, text, {"inline_keyboard": buttons})

    def handle_do_close_session(self, chat_id: str, message_id: int, session_id: str):
        title = self.get_title_for_session(session_id)
        ok, res = close_session_in_opencode(session_id=session_id, session_title=title)
        if ok:
            # Dọn dẹp trạng thái bộ nhớ bot của phiên này
            self.auto_continue_sent.pop(session_id, None)
            self.auto_continue_in_progress.discard(session_id)
            self.prompt_send_in_progress.discard(session_id)
            self.session_states.pop(session_id, None)

            text = f"✅ <b>ĐÃ ĐÓNG PHIÊN THÀNH CÔNG!</b>\n\n📌 Đã đóng: <b>{title}</b>"
            markup = {"inline_keyboard": [[{"text": "📊 Xem bảng trạng thái", "callback_data": "back_to_status"}]]}
            self.edit_message(chat_id, message_id, text, markup)
        else:
            text = f"❌ <b>Không thể đóng phiên {title}:</b>\n<i>{res}</i>"
            markup = {"inline_keyboard": [[{"text": "🔙 Quay lại", "callback_data": f"session_detail:{session_id}"}]]}
            self.edit_message(chat_id, message_id, text, markup)

    def handle_choose_model_menu(self, chat_id: str, message_id: int, session_id: str):
        title = self.get_title_for_session(session_id)
        curr_model = get_session_model_in_opencode(session_id)
        curr_display = "Mặc định hệ thống"
        if curr_model:
            mid = curr_model.get("id", "")
            pid = curr_model.get("providerID", "")
            curr_display = f"{mid} ({pid})" if pid else mid

        text = (
            f"🧠 <b>CÀI ĐẶT MODEL AI CHO PHIÊN:</b>\n"
            f"📌 <b>{title}</b>\n\n"
            f"⚙️ <b>Model hiện tại:</b> <code>{curr_display}</code>\n\n"
            f"👉 <i>Chọn một Model bên dưới để gán riêng cho phiên này:</i>"
        )

        models_presets = [
            ("⚡ Gemini 3.8 Flash High", "9router", "ag/gemini-3.8-flash-high"),
            ("🚀 Claude 4.6 Sonnet", "9router", "ag/claude-sonnet-4-6"),
            ("🧠 Claude 4.6 Opus Thinking", "9router", "ag/claude-opus-4-6-thinking"),
            ("🌙 GPT-6 Luna (1M Context)", "9router", "cx/gpt-6-luna[1m]"),
            ("💎 Step-5 Unrestricted", "jerouter", "step-5-unrestricted"),
            ("⚡ DeepSeek Unrestricted", "jerouter", "deepseek-unrestricted"),
            ("🌟 Kimi-K3", "darkapi", "kimi-k3"),
        ]

        buttons = []
        for label, pid, mid in models_presets:
            is_active = (curr_model and curr_model.get("providerID") == pid and curr_model.get("id") == mid)
            mark = " ✅" if is_active else ""
            buttons.append([{"text": f"{label}{mark}", "callback_data": f"set_model:{session_id}:{pid}:{mid}"}])

        buttons.append([{"text": "🔙 Quay lại chi tiết phiên", "callback_data": f"session_detail:{session_id}"}])
        self._pause_dashboard_auto_refresh()
        self.edit_message(chat_id, message_id, text, {"inline_keyboard": buttons})

    def handle_set_session_model(self, chat_id: str, message_id: int, session_id: str, provider_id: str, model_id: str):
        title = self.get_title_for_session(session_id)
        ok, res = switch_session_model_in_opencode(session_id, provider_id, model_id)
        if ok:
            self.handle_choose_model_menu(chat_id, message_id, session_id)
        else:
            self.send_message(chat_id, f"❌ Lỗi khi đổi model cho phiên {title}: {res}")

    def deliver_content_to_session(self, chat_id: str, session_title: str, prompt_text: str = "", image_path: str = None, session_id: str = None):
        if session_id:
            if session_id in self.prompt_send_in_progress or session_id in self.auto_continue_in_progress:
                self.send_message(chat_id, f"⏳ Đang có lệnh được gửi vào phiên <b>{session_title}</b>, vui lòng đợi hoàn thành!")
                return
            self.prompt_send_in_progress.add(session_id)

        content_desc = "hình ảnh kèm văn bản" if (image_path and prompt_text) else ("hình ảnh" if image_path else f"lệnh '{prompt_text}'")
        ack_res = self.send_message(chat_id, f"⏳ <b>Đang gửi {content_desc} vào OpenCode...</b>\n📌 Phiên: <b>{session_title}</b>")
        ack_msg_id = ack_res.get("result", {}).get("message_id") if ack_res else None

        def _worker():
            try:
                log_msg(f"[Deliver] Bắt đầu gửi vào {session_title} (sid={session_id}): {prompt_text[:80]}")
                success, err = send_prompt_to_opencode_session(session_title, prompt_text=prompt_text, image_path=image_path, session_id=session_id)
                if ack_msg_id:
                    try:
                        self.api_request("deleteMessage", {"chat_id": chat_id, "message_id": ack_msg_id})
                    except Exception:
                        pass

                if success:
                    log_msg(f"[Deliver] Thành công vào {session_title}")
                    msg = (
                        f"✅ <b>ĐÃ GỬI THÀNH CÔNG!</b>\n\n"
                        f"📌 Phiên: <b>{session_title}</b>\n"
                        f"📝 Nội dung: <code>{prompt_text[:150]}</code>\n\n"
                        f"AI trên máy tính đang tiếp nhận và chạy tiếp! 🟢"
                    )
                    self.send_message(chat_id, msg)
                else:
                    log_msg(f"[Deliver] Thất bại vào {session_title}: {err}")
                    err_msg = f"❌ <b>KHÔNG THỂ GỬI LỆNH</b> vào phiên <b>{session_title}</b>:\n<i>{err}</i>"
                    self.send_message(chat_id, err_msg)
            except Exception as e:
                log_msg(f"[Deliver] Exception vào {session_title}: {e}")
                print(f"[TelegramBot] deliver_content_to_session error: {e}")
                self.send_message(chat_id, f"❌ <b>Lỗi khi gửi lệnh</b> vào phiên <b>{session_title}</b>:\n<i>{e}</i>")
            finally:
                if session_id:
                    self.prompt_send_in_progress.discard(session_id)

        threading.Thread(target=_worker, daemon=True).start()

    def handle_incoming_user_message(self, chat_id: str, text: str = "", photo_file_id: str = None, caption: str = ""):
        # 1. Check if user has an active chat target
        target = self.user_chat_target.get(chat_id)
        if target and time.time() < target.get("expires_at", 0):
            session_title = target["title"]
            sid = target.get("session_id")
            # Gửi thủ công -> reset bộ đếm tự động tiếp tục của phiên
            self.auto_continue_sent.pop(sid, None)
            temp_img = None
            if photo_file_id:
                temp_img = os.path.join(TEMP_DIR, f"tg_{int(time.time()*1000)}.jpg")
                if not self.download_telegram_file(photo_file_id, temp_img):
                    self.send_message(chat_id, "❌ Không thể tải ảnh từ Telegram.")
                    return
            
            prompt_content = caption if photo_file_id else text
            self.deliver_content_to_session(chat_id, session_title, prompt_text=prompt_content, image_path=temp_img, session_id=sid)
            return

        # 2. If no active target, show a quick session picker
        sessions = poll_all_sessions()
        if not sessions:
            self.send_message(chat_id, "⚠️ Hiện không có phiên OpenCode nào đang mở trên máy tính.")
            return

        # Save pending input
        self.pending_input[chat_id] = {
            "text": text,
            "photo_file_id": photo_file_id,
            "caption": caption
        }

        buttons = []
        for i, s in enumerate(sessions, 1):
            sid = s.get("sessionId", "")
            title = s.get("title", f"Phiên {i}")
            icon = "🟢" if s.get("color") == "green" else ("🟡" if s.get("color") == "yellow" else "🔴")
            short_title = title if len(title) <= 15 else title[:14] + "…"
            buttons.append({"text": f"{icon} {i}. {short_title}", "callback_data": f"pick_target:{sid}"})

        keyboard = []
        for i in range(0, len(buttons), 2):
            keyboard.append(buttons[i:i+2])
        keyboard.append([{"text": "❌ Hủy bỏ", "callback_data": "cancel_pending_input"}])

        content_name = "hình ảnh" if photo_file_id else f"nội dung: \"{text[:40]}...\""
        self.send_message(
            chat_id,
            f"❓ Bạn muốn chuyển <b>{content_name}</b> vào phiên làm việc nào?",
            {"inline_keyboard": keyboard}
        )

    def handle_send_prompt_action(self, chat_id: str, session_id: str):
        title = self.get_title_for_session(session_id)
        if session_id in self.prompt_send_in_progress or session_id in self.auto_continue_in_progress:
            self.send_message(chat_id, f"⏳ Phiên <b>{title}</b> đang trong quá trình gửi lệnh, vui lòng không bấm liên tục!")
            return

        cfg = self.config_getter()
        prompt_text = cfg.get("auto_continue_prompt", "tiếp tục")
        # Gửi thủ công -> reset bộ đếm tự động tiếp tục của phiên
        self.auto_continue_sent.pop(session_id, None)
        self.deliver_content_to_session(chat_id, title, prompt_text=prompt_text, session_id=session_id)

    def handle_screenshot(self, chat_id: str):
        ack_res = self.send_message(chat_id, "📸 <i>Đang chụp màn hình máy tính...</i>")
        ack_msg_id = ack_res.get("result", {}).get("message_id") if ack_res else None

        def _worker():
            try:
                im = ImageGrab.grab(all_screens=True)
                buf = io.BytesIO()
                im.save(buf, format="JPEG", quality=80)
                buf.seek(0)
                now_str = time.strftime("%H:%M:%S")
                self.send_photo(chat_id, buf.getvalue(), f"🖥️ <b>Màn hình máy tính</b> lúc <code>{now_str}</code>")
                if ack_msg_id:
                    self.api_request("deleteMessage", {"chat_id": chat_id, "message_id": ack_msg_id})
            except Exception as e:
                err_text = f"❌ Không thể chụp màn hình: {e}"
                if ack_msg_id:
                    self.edit_message(chat_id, ack_msg_id, err_text)
                else:
                    self.send_message(chat_id, err_text)

        threading.Thread(target=_worker, daemon=True).start()

    def handle_help(self, chat_id: str):
        help_text = (
            "🤖 <b>HƯỚNG DẪN ĐIỀU KHIỂN OPENCODE TỪ XA</b>\n\n"
            "Bạn có thể bấm trực tiếp các <b>nút bấm menu</b> bên dưới bàn phím hoặc dùng các lệnh:\n\n"
            "📊 <b>Bảng trạng thái</b>: Xem danh sách các phiên đang mở (🟢 / 🟡 / 🔴).\n"
            "   • <b>Tự động làm mới trực tiếp</b> khi bạn đang mở xem trên Telegram.\n"
            "   • Mỗi phiên có <b>nút bấm riêng</b> để xem chi tiết và <b>đọc câu trả lời của AI</b>.\n"
            "   • Bật/tắt <b>tự động gửi 'tiếp tục'</b> riêng cho từng phiên theo ý bạn.\n"
            "   • Số lượng lệnh tự động gửi cho mỗi phiên chỉnh trong <b>Cài đặt</b> của phần mềm (mặc định 1 lệnh).\n\n"
            "✍️ <b>Gửi phản hồi & Hình ảnh riêng cho từng phiên</b>:\n"
            "   • Bạn có thể bấm <b>[ ✍️ Soạn phản hồi / Gửi ảnh ]</b> trong từng phiên.\n"
            "   • Hoặc chỉ cần <b>gửi trực tiếp văn bản hoặc hình ảnh</b> vào bot, bot sẽ hỏi bạn muốn gửi vào phiên nào!\n\n"
            "💬 <b>Gửi lệnh 'tiếp tục' ngay</b>: Bấm nút là lệnh được gõ và gửi thẳng vào OpenCode trên máy tính.\n"
            "➕ <b>Mở phiên mới</b>: Mở ngay tab phiên làm việc mới theo thư mục dự án 1 chạm.\n"
            "❌ <b>Đóng phiên</b>: Đóng và gỡ bỏ tab phiên đang mở trên máy tính.\n"
            "🧠 <b>Đổi Model AI</b>: Cài đặt model AI (Gemini, Claude, GPT, Step, DeepSeek, Kimi) riêng cho từng phiên.\n"
            "📸 <b>Chụp màn hình</b>: Chụp ảnh màn hình máy tính gửi về điện thoại ngay lập tức.\n"
            "❓ <b>Trợ giúp</b>: Hiển thị lại hướng dẫn này."
        )
        self.send_message(chat_id, help_text, self.get_persistent_menu_keyboard())

    # --- AUTO-REFRESH BACKGROUND LOOP ---

    def _auto_refresh_loop(self):
        """Tự động làm mới bảng trạng thái trên tin nhắn Telegram nếu người dùng đang xem."""
        while self.running:
            time.sleep(2.5)
            if not self.is_enabled() or not self.active_dashboard:
                continue

            dash = self.active_dashboard
            if not dash.get("auto_refresh"):
                continue
            # Chỉ làm mới khi tin nhắn dashboard đang ở giao diện bảng trạng thái,
            # không ghi đè khi người dùng đang xem chi tiết phiên / soạn lệnh
            if dash.get("view") != "status":
                continue

            # Only auto-refresh for up to 5 minutes after last interaction
            if time.time() - dash.get("last_updated", 0) > 300:
                continue

            try:
                chat_id = dash["chat_id"]
                msg_id = dash["message_id"]
                sessions = poll_all_sessions()
                new_text, new_markup = self.build_status_message(sessions, auto_refresh=True)

                if new_text != dash.get("last_content"):
                    res = self.edit_message(chat_id, msg_id, new_text, new_markup)
                    if res and res.get("ok"):
                        dash["last_content"] = new_text
                    elif res and "message is not modified" not in res.get("description", "").lower():
                        pass
            except Exception as e:
                pass

    # --- POLLING LOOP ---

    def _polling_loop(self):
        while self.running:
            if not self.is_enabled():
                time.sleep(2)
                continue
            
            try:
                data = {"offset": self.last_update_id + 1, "timeout": 15}
                res = self.api_request("getUpdates", data=data, timeout=25)
                if not res or not res.get("ok"):
                    time.sleep(2)
                    continue

                for update in res.get("result", []):
                    self.last_update_id = update.get("update_id", self.last_update_id)
                    self._dispatch_update(update)

            except Exception as e:
                print(f"[TelegramBot] Polling loop error: {e}")
                time.sleep(3)

    def _dispatch_update(self, update: dict):
        # 1. Message handling
        if "message" in update:
            msg = update["message"]
            from_id = msg.get("from", {}).get("id")
            if not self.is_authorized(from_id):
                print(f"[TelegramBot] Unauthorized message from {from_id}")
                return

            chat_id = str(from_id)
            text = (msg.get("text") or "").strip()
            photo = msg.get("photo")
            caption = (msg.get("caption") or "").strip()

            # Command & menu check
            if text in ("/status", "📊 Bảng trạng thái"):
                self.waiting_ac_count_input.pop(chat_id, None)
                self.handle_status(chat_id, auto_refresh=True)
            elif text in ("/new", "➕ Mở phiên mới"):
                self.waiting_ac_count_input.pop(chat_id, None)
                self.handle_new_session_menu(chat_id)
            elif text in ("/screen", "📸 Chụp màn hình"):
                self.handle_screenshot(chat_id)
            elif text in ("/help", "/start", "❓ Trợ giúp"):
                self.waiting_ac_count_input.pop(chat_id, None)
                self.handle_help(chat_id)
            elif chat_id in self.waiting_ac_count_input and text:
                # Người dùng đang gõ số lượng lệnh tự động gửi từ bàn phím!
                self.handle_ac_count_text_input(chat_id, text)
            elif photo:
                # User sent a photo!
                file_id = photo[-1]["file_id"] # largest size
                self.handle_incoming_user_message(chat_id, photo_file_id=file_id, caption=caption)
            elif text:
                # User sent regular text!
                self.handle_incoming_user_message(chat_id, text=text)

        # 2. Callback query handling (Inline buttons clicked)
        elif "callback_query" in update:
            cb = update["callback_query"]
            from_id = cb.get("from", {}).get("id")
            if not self.is_authorized(from_id):
                return

            chat_id = str(from_id)
            data = cb.get("data", "")
            msg = cb.get("message", {})
            msg_id = msg.get("message_id")

            # Instant visual feedback on Telegram phone screen
            toast = "⏳ Đang xử lý..."
            if data == "refresh_status":
                toast = "🔄 Đang làm mới bảng trạng thái..."
            elif data == "menu_new_session":
                toast = "➕ Mở danh mục dự án mới..."
            elif data.startswith("create_session_proj:"):
                toast = "🚀 Đang tạo phiên làm việc mới..."
            elif data.startswith("confirm_close_session:"):
                toast = "⚠️ Xác nhận đóng phiên..."
            elif data.startswith("do_close_session:"):
                toast = "🗑️ Đang đóng phiên..."
            elif data.startswith("choose_model:"):
                toast = "🧠 Tải danh sách Model..."
            elif data.startswith("set_model:"):
                toast = "⚙️ Đang đổi Model cho phiên..."
            elif data == "cmd_screen":
                toast = "📸 Đang chụp ảnh màn hình máy tính..."
            elif data.startswith("send_prompt:"):
                toast = "💬 Đang gửi lệnh 'tiếp tục' vào OpenCode..."
            elif data.startswith("perm_act:"):
                toast = "⏳ Đang thực hiện phê duyệt..."
            elif data.startswith("q_radio_select:") or data.startswith("q_cb_submit:"):
                toast = "🚀 Đang gửi lựa chọn cho AI..."
            elif data.startswith("read_ans:"):
                toast = "📜 Đang tải câu trả lời..."
            elif data.startswith("session_detail:"):
                toast = "🔍 Đang mở chi tiết phiên..."
            elif data.startswith("prompt_ac_input:"):
                toast = "✍️ Chuyển sang chế độ nhập số lượng..."
            elif data.startswith("set_ac_count:") or data.startswith("adjust_ac_count:") or data.startswith("cycle_ac_count:"):
                toast = "🔢 Đang cập nhật số lần tự gửi..."

            self.api_request("answerCallbackQuery", {"callback_query_id": cb.get("id"), "text": toast})

            if data == "refresh_status":
                self.handle_status(chat_id, message_id=msg_id, auto_refresh=True)
            elif data == "toggle_refresh:off":
                self.handle_status(chat_id, message_id=msg_id, auto_refresh=False)
            elif data == "toggle_refresh:on":
                self.handle_status(chat_id, message_id=msg_id, auto_refresh=True)
            elif data == "back_to_status":
                resume_refresh = True
                if self.active_dashboard:
                    resume_refresh = self.active_dashboard.get("user_auto_refresh", True)
                self.handle_status(chat_id, message_id=msg_id, auto_refresh=resume_refresh)
            elif data == "menu_new_session":
                self.handle_new_session_menu(chat_id, message_id=msg_id)
            elif data.startswith("create_session_proj:"):
                proj = data.split("create_session_proj:")[-1]
                self.handle_create_session(chat_id, proj, message_id=msg_id)
            elif data.startswith("confirm_close_session:"):
                sid = data.split("confirm_close_session:")[-1]
                self.handle_confirm_close_session(chat_id, msg_id, sid)
            elif data.startswith("do_close_session:"):
                sid = data.split("do_close_session:")[-1]
                self.handle_do_close_session(chat_id, msg_id, sid)
            elif data.startswith("choose_model:"):
                sid = data.split("choose_model:")[-1]
                self.handle_choose_model_menu(chat_id, msg_id, sid)
            elif data.startswith("set_model:"):
                parts = data.split(":")
                sid = parts[1]
                pid = parts[2]
                mid = ":".join(parts[3:])
                self.handle_set_session_model(chat_id, msg_id, sid, pid, mid)
            elif data == "cmd_screen":
                self.handle_screenshot(chat_id)
            elif data == "cancel_chat_mode":
                self.user_chat_target.pop(chat_id, None)
                self.send_message(chat_id, "✅ Đã hủy chế độ soạn lệnh.")
            elif data == "cancel_pending_input":
                self.pending_input.pop(chat_id, None)
                self.send_message(chat_id, "✅ Đã hủy gửi nội dung.")
            elif data.startswith("session_detail:"):
                sid = data.split("session_detail:")[-1]
                self.handle_session_detail(chat_id, msg_id, sid)
            elif data.startswith("chat_mode:"):
                sid = data.split("chat_mode:")[-1]
                self.activate_chat_mode(chat_id, sid, message_id=msg_id)
            elif data.startswith("read_ans:"):
                sid = data.split("read_ans:")[-1]
                self.handle_read_answer(chat_id, sid, message_id=msg_id)
            elif data.startswith("send_prompt:"):
                sid = data.split("send_prompt:")[-1]
                self.handle_send_prompt_action(chat_id, sid)
            elif data.startswith("toggle_ac:"):
                parts = data.split(":")
                sid = parts[1]
                action = parts[2]
                cfg = self.config_getter()
                ac_map = cfg.get("auto_continue_sessions", {}).copy()
                ac_map[sid] = (action == "on")
                self.save_config_field("auto_continue_sessions", ac_map)
                if action == "on":
                    # Bật lại tự động tiếp tục -> cấp lại ngân sách số lệnh tự động gửi
                    self.auto_continue_sent.pop(sid, None)
                    title = self.get_title_for_session(sid)
                    self.trigger_auto_continue_if_eligible(sid, title, chat_id)
                self.handle_session_detail(chat_id, msg_id, sid)
            elif data.startswith("set_ac_count:"):
                parts = data.split(":")
                sid = parts[1]
                val = int(parts[2])
                cfg = self.config_getter()
                s_limits = cfg.get("auto_continue_session_limits", {}).copy()
                s_limits[sid] = max(1, min(99, val))
                self.save_config_field("auto_continue_session_limits", s_limits)
                self.save_config_field("auto_continue_count", s_limits[sid])
                ac_map = cfg.get("auto_continue_sessions", {}).copy()
                ac_map[sid] = True
                self.save_config_field("auto_continue_sessions", ac_map)
                self.auto_continue_sent.pop(sid, None)
                self.handle_session_detail(chat_id, msg_id, sid)
                title = self.get_title_for_session(sid)
                self.trigger_auto_continue_if_eligible(sid, title, chat_id)
            elif data.startswith("adjust_ac_count:"):
                parts = data.split(":")
                sid = parts[1]
                delta = int(parts[2])
                cfg = self.config_getter()
                curr = self._get_auto_continue_max(cfg, sid)
                new_val = max(1, min(99, curr + delta))
                s_limits = cfg.get("auto_continue_session_limits", {}).copy()
                s_limits[sid] = new_val
                self.save_config_field("auto_continue_session_limits", s_limits)
                self.save_config_field("auto_continue_count", new_val)
                ac_map = cfg.get("auto_continue_sessions", {}).copy()
                ac_map[sid] = True
                self.save_config_field("auto_continue_sessions", ac_map)
                self.auto_continue_sent.pop(sid, None)
                self.handle_session_detail(chat_id, msg_id, sid)
                title = self.get_title_for_session(sid)
                self.trigger_auto_continue_if_eligible(sid, title, chat_id)
            elif data.startswith("cycle_ac_count:"):
                sid = data.split("cycle_ac_count:")[-1]
                cfg = self.config_getter()
                curr = self._get_auto_continue_max(cfg, sid)
                presets = [1, 2, 3, 5, 10, 20]
                next_val = presets[0]
                for p in presets:
                    if p > curr:
                        next_val = p
                        break
                s_limits = cfg.get("auto_continue_session_limits", {}).copy()
                s_limits[sid] = next_val
                self.save_config_field("auto_continue_session_limits", s_limits)
                self.save_config_field("auto_continue_count", next_val)
                ac_map = cfg.get("auto_continue_sessions", {}).copy()
                ac_map[sid] = True
                self.save_config_field("auto_continue_sessions", ac_map)
                self.auto_continue_sent.pop(sid, None)
                self.handle_session_detail(chat_id, msg_id, sid)
                title = self.get_title_for_session(sid)
                self.trigger_auto_continue_if_eligible(sid, title, chat_id)
            elif data.startswith("prompt_ac_input:"):
                sid = data.split("prompt_ac_input:")[-1]
                self.activate_ac_count_input_mode(chat_id, sid, message_id=msg_id)
            elif data.startswith("pick_target:"):
                sid = data.split("pick_target:")[-1]
                pending = self.pending_input.pop(chat_id, None)
                if pending:
                    title = self.get_title_for_session(sid)
                    # Gửi thủ công -> reset bộ đếm tự động tiếp tục của phiên
                    self.auto_continue_sent.pop(sid, None)
                    
                    temp_img = None
                    if pending.get("photo_file_id"):
                        temp_img = os.path.join(TEMP_DIR, f"tg_{int(time.time()*1000)}.jpg")
                        self.download_telegram_file(pending["photo_file_id"], temp_img)
                    
                    prompt_txt = pending.get("caption") if pending.get("photo_file_id") else pending.get("text")
                    self.deliver_content_to_session(chat_id, title, prompt_text=prompt_txt, image_path=temp_img, session_id=sid)
            elif data.startswith("perm_act:"):
                parts = data.split(":")
                sid = parts[1]
                action = parts[2]
                title = self.get_title_for_session(sid)
                action_names = {"once": "Cho phép một lần", "always": "Cho phép luôn", "deny": "Từ chối"}
                act_vn = action_names.get(action, action)
                
                ack = self.send_message(chat_id, f"⏳ <i>Đang gửi phê duyệt [{act_vn}] vào OpenCode...</i>")
                ack_id = ack.get("result", {}).get("message_id") if ack else None
                
                def _perm_worker():
                    ok, err = approve_permission_in_opencode(title, action=action, session_id=sid)
                    if ack_id:
                        try:
                            self.api_request("deleteMessage", {"chat_id": chat_id, "message_id": ack_id})
                        except Exception:
                            pass
                    if ok:
                        msg = f"✅ Đã phê duyệt: <b>[{act_vn}]</b> cho phiên <b>{title}</b>.\nAI đang tiếp tục thực thi! 🟢"
                    else:
                        msg = f"❌ Không thể phê duyệt tự động:\n<i>{err}</i>"
                    self.send_message(chat_id, msg)
                threading.Thread(target=_perm_worker, daemon=True).start()
            elif data.startswith("q_radio_select:"):
                parts = data.split(":")
                sid = parts[1]
                idx = int(parts[2])
                q_info = self.question_state.get(sid)
                if not q_info:
                    q_data = get_active_question_for_session(sid)
                    if q_data and q_data.get("questions"):
                        q_obj = q_data["questions"][0]
                        q_info = {
                            "session_id": sid,
                            "title": self.get_title_for_session(sid),
                            "options": [opt.get("label", "") for opt in q_obj.get("options", [])],
                            "selected_indices": set(),
                            "multiple": q_obj.get("multiple", False)
                        }
                        self.question_state[sid] = q_info
                
                title = q_info.get("title") if q_info else self.get_title_for_session(sid)
                options = q_info.get("options", []) if q_info else []
                selected_label = options[idx] if (idx < len(options)) else f"Lựa chọn {idx+1}"

                ack = self.send_message(chat_id, f"⏳ <i>Đang gửi đáp án [{selected_label}] vào OpenCode...</i>")
                ack_id = ack.get("result", {}).get("message_id") if ack else None

                def _radio_worker():
                    ok, err = select_question_option_in_opencode(title, [selected_label], session_id=sid)
                    if ack_id:
                        try:
                            self.api_request("deleteMessage", {"chat_id": chat_id, "message_id": ack_id})
                        except Exception:
                            pass
                    if ok:
                        msg = f"✅ Đã chọn và gửi đáp án thành công:\n👉 <b>{selected_label}</b>\n(Phiên: <b>{title}</b>)\n\nAI đang tiếp tục xử lý! 🟢"
                    else:
                        msg = f"❌ Không thể gửi lựa chọn tự động:\n<i>{err}</i>"
                    self.send_message(chat_id, msg)
                threading.Thread(target=_radio_worker, daemon=True).start()
            elif data.startswith("q_cb_toggle:"):
                parts = data.split(":")
                sid = parts[1]
                idx = int(parts[2])
                q_info = self.question_state.get(sid)
                if not q_info:
                    q_data = get_active_question_for_session(sid)
                    if q_data and q_data.get("questions"):
                        q_obj = q_data["questions"][0]
                        q_info = {
                            "session_id": sid,
                            "title": self.get_title_for_session(sid),
                            "options": [opt.get("label", "") for opt in q_obj.get("options", [])],
                            "selected_indices": set(),
                            "multiple": q_obj.get("multiple", False)
                        }
                        self.question_state[sid] = q_info

                if q_info:
                    if idx in q_info["selected_indices"]:
                        q_info["selected_indices"].remove(idx)
                    else:
                        q_info["selected_indices"].add(idx)

                    keyboard = []
                    for o_idx, o_label in enumerate(q_info["options"]):
                        is_sel = o_idx in q_info["selected_indices"]
                        box = "☑️" if is_sel else "⬜"
                        short_lbl = o_label if len(o_label) <= 24 else o_label[:23] + "…"
                        keyboard.append([{"text": f"{box} {o_idx+1}. {short_lbl}", "callback_data": f"q_cb_toggle:{sid}:{o_idx}"}])
                    
                    sel_count = len(q_info["selected_indices"])
                    keyboard.append([{"text": f"🚀 Xác nhận & Gửi ({sel_count} mục)", "callback_data": f"q_cb_submit:{sid}"}])
                    keyboard.append([
                        {"text": "✍️ Soạn câu trả lời tự do", "callback_data": f"chat_mode:{sid}"},
                        {"text": "⏭ Bỏ qua câu hỏi", "callback_data": f"q_dismiss:{sid}"}
                    ])
                    keyboard.append([
                        {"text": "📸 Chụp màn hình", "callback_data": "cmd_screen"},
                        {"text": "📊 Bảng trạng thái", "callback_data": "refresh_status"}
                    ])

                    self.api_request("editMessageReplyMarkup", {
                        "chat_id": chat_id,
                        "message_id": msg_id,
                        "reply_markup": {"inline_keyboard": keyboard}
                    })
            elif data.startswith("q_cb_submit:"):
                sid = data.split("q_cb_submit:")[-1]
                q_info = self.question_state.get(sid)
                title = q_info.get("title") if q_info else self.get_title_for_session(sid)
                if q_info:
                    selected_labels = [q_info["options"][i] for i in sorted(q_info["selected_indices"]) if i < len(q_info["options"])]
                    if not selected_labels:
                        self.send_message(chat_id, "⚠️ Bạn chưa chọn mục nào! Hãy tích chọn ít nhất 1 mục rồi bấm Xác nhận.")
                        return

                    ack = self.send_message(chat_id, f"⏳ <i>Đang gửi {len(selected_labels)} lựa chọn vào OpenCode...</i>")
                    ack_id = ack.get("result", {}).get("message_id") if ack else None

                    def _cb_submit_worker():
                        ok, err = select_question_option_in_opencode(title, selected_labels, session_id=sid)
                        if ack_id:
                            try:
                                self.api_request("deleteMessage", {"chat_id": chat_id, "message_id": ack_id})
                            except Exception:
                                pass
                        if ok:
                            labels_str = "\n".join([f"• <b>{l}</b>" for l in selected_labels])
                            msg = f"✅ Đã gửi {len(selected_labels)} lựa chọn thành công:\n{labels_str}\n(Phiên: <b>{title}</b>)\n\nAI đang tiếp tục xử lý! 🟢"
                        else:
                            msg = f"❌ Không thể gửi lựa chọn tự động:\n<i>{err}</i>"
                        self.send_message(chat_id, msg)
                    threading.Thread(target=_cb_submit_worker, daemon=True).start()
            elif data.startswith("q_dismiss:"):
                sid = data.split("q_dismiss:")[-1]
                title = self.get_title_for_session(sid)
                ack = self.send_message(chat_id, f"⏳ <i>Đang gửi lệnh bỏ qua câu hỏi cho phiên {title}...</i>")
                ack_id = ack.get("result", {}).get("message_id") if ack else None

                def _dismiss_worker():
                    ok, err = dismiss_question_in_opencode(title, session_id=sid)
                    if ack_id:
                        try:
                            self.api_request("deleteMessage", {"chat_id": chat_id, "message_id": ack_id})
                        except Exception:
                            pass
                    if ok:
                        msg = f"✅ Đã bỏ qua câu hỏi thành công cho phiên: <b>{title}</b>.\nAI đang tiếp tục xử lý! 🟢"
                    else:
                        msg = f"❌ Không thể bỏ qua câu hỏi tự động:\n<i>{err}</i>"
                    self.send_message(chat_id, msg)
                threading.Thread(target=_dismiss_worker, daemon=True).start()

    # --- AUTO-CONTINUE WORKER ---

    def trigger_auto_continue_if_eligible(self, sid: str, title: str, chat_id: str = None) -> bool:
        """Kích hoạt gửi tự động tiếp tục nếu phiên đủ điều kiện (đang completed, còn hạn mức, chưa có luồng gửi)."""
        if not self.is_enabled():
            return False

        if sid in self.auto_continue_in_progress or sid in self.prompt_send_in_progress:
            log_msg(f"[AutoContinue] Bỏ qua {title} ({sid}): đang có luồng gửi chạy.")
            return False

        if not chat_id:
            chat_id = self.get_authorized_chat_id()
        if not chat_id:
            return False

        cfg = self.config_getter()
        ac_map = cfg.get("auto_continue_sessions", {})
        is_ac_on = ac_map.get(sid, False) or cfg.get("auto_continue_all", False)
        if not is_ac_on:
            return False

        ac_max_sid = self._get_auto_continue_max(cfg, sid)
        ac_sent = self.auto_continue_sent.get(sid, 0)
        if ac_sent >= ac_max_sid:
            log_msg(f"[AutoContinue] Bỏ qua {title} ({sid}): đã hết hạn mức ({ac_sent}/{ac_max_sid}).")
            return False

        # Đánh dấu đang gửi và tăng số lần đã gửi
        self.auto_continue_in_progress.add(sid)
        self.auto_continue_sent[sid] = ac_sent + 1
        prompt_text = cfg.get("auto_continue_prompt", "tiếp tục")

        log_msg(f"[AutoContinue] Kích hoạt luồng gửi tiếp tục cho {title} ({sid}) [lượt {ac_sent + 1}/{ac_max_sid}]")
        threading.Thread(
            target=self._auto_continue_worker,
            args=(chat_id, sid, title, prompt_text, ac_sent + 1, ac_max_sid),
            daemon=True
        ).start()
        return True

    def _auto_continue_worker(self, chat_id: str, session_id: str, title: str, prompt_text: str,
                              sent_idx: int = 1, max_count: int = 1):
        try:
            time.sleep(1.0)
            log_msg(f"[Worker] Đang gửi '{prompt_text}' vào {title} (sid={session_id})")
            success, err = send_prompt_to_opencode_session(title, prompt_text, session_id=session_id)
            if success:
                log_msg(f"[Worker] Thành công gửi vào {title} (sid={session_id})")
                self.send_message(
                    chat_id,
                    f"⚡ <b>[TỰ ĐỘNG TIẾP TỤC]</b>\n\n"
                    f"📌 Phiên: <b>{title}</b>\n"
                    f"Đã tự động gửi lệnh: <code>{prompt_text}</code>\n"
                    f"🔢 Số lệnh tự động: <b>{sent_idx}/{max_count}</b>\n"
                    f"AI đang tiếp tục làm việc! 🟢"
                )
            else:
                log_msg(f"[Worker] Thất bại gửi vào {title} (sid={session_id}): {err}")
                # Hoàn lại 1 lượt nếu gửi lỗi để không bị mất lượt oan
                if session_id in self.auto_continue_sent and self.auto_continue_sent[session_id] > 0:
                    self.auto_continue_sent[session_id] -= 1
                self.send_message(
                    chat_id,
                    f"⚠️ <b>[TỰ ĐỘNG TIẾP TỤC THẤT BẠI]</b>\n\n"
                    f"📌 Phiên: <b>{title}</b>\n"
                    f"Lỗi: <i>{err}</i>\n"
                    f"Bạn có thể bấm nút <b>[ 💬 Gửi 'tiếp tục' ]</b> bên dưới để thử lại."
                )
                print(f"[TelegramBot] Auto-continue failed for '{title}': {err}")
        finally:
            self.auto_continue_in_progress.discard(session_id)

    # --- SCAN SESSIONS & AUTO NOTIFY ---

    def check_and_notify(self, sessions: list):
        if not self.is_enabled():
            return

        chat_id = self.get_authorized_chat_id()
        if not chat_id:
            return

        cfg = self.config_getter()
        ac_map = cfg.get("auto_continue_sessions", {})
        prompt_text = cfg.get("auto_continue_prompt", "tiếp tục")
        ac_max = self._get_auto_continue_max(cfg)

        for s in sessions:
            sid = s.get("sessionId")
            if not sid:
                continue

            current_state = s.get("state")  # 'running', 'completed', 'waiting_user'
            prev_state = self.session_states.get(sid)

            if current_state == "running":
                self.auto_continue_in_progress.discard(sid)

            if prev_state is None:
                self.session_states[sid] = current_state
                # Nếu phiên đã ở trạng thái completed và có bật tự động tiếp tục -> kích hoạt chạy ngay
                if current_state == "completed":
                    self.trigger_auto_continue_if_eligible(sid, s.get("title", sid), chat_id)
                continue

            if current_state != prev_state:
                self.session_states[sid] = current_state
                title = s.get("title", sid)
                directory = s.get("directory", "")
                detail = s.get("detail", "")
                now_str = time.strftime("%H:%M:%S")
                log_msg(f"[StateChange] Phiên {title} ({sid}): {prev_state} -> {current_state}")

                # 🔴 Phiên hoàn thành -> Gửi thông báo kèm luôn toàn bộ câu trả lời của AI
                if current_state == "completed" and prev_state in ("running", "waiting_user"):
                    is_ac_on = ac_map.get(sid, False) or cfg.get("auto_continue_all", False)
                    ac_max_sid = self._get_auto_continue_max(cfg, sid)
                    ac_sent = self.auto_continue_sent.get(sid, 0)
                    will_continue = is_ac_on and (ac_sent < ac_max_sid) and (sid not in self.auto_continue_in_progress)

                    answer = get_latest_assistant_response(sid)
                    answer_section = ""
                    if answer:
                        clean_ans = answer.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                        if len(clean_ans) > 2800:
                            clean_ans = clean_ans[:2800] + "\n\n<i>...(Nội dung quá dài, bấm nút bên dưới để xem toàn bộ)</i>"
                        answer_section = f"\n─────────────────────\n💬 <b>Câu trả lời của AI:</b>\n\n{clean_ans}\n"

                    msg = (
                        f"🔴 <b>[OPENCODE - ĐÃ XONG]</b>\n\n"
                        f"📌 <b>Phiên:</b> <b>{title}</b>\n"
                        f"📁 <b>Thư mục:</b> <code>{directory}</code>\n"
                        f"ℹ️ <b>Trạng thái:</b> {detail}\n"
                        f"⏰ <b>Thời gian:</b> {now_str}"
                        f"{answer_section}"
                    )
                    
                    if will_continue:
                        msg += f"\n⚡ <i>(Đang tự động gửi lệnh '{prompt_text}' [{ac_sent + 1}/{ac_max_sid}]...)</i>"
                        self.trigger_auto_continue_if_eligible(sid, title, chat_id)
                    elif is_ac_on and ac_sent >= ac_max_sid:
                        msg += f"\n⚡ <i>(Đã gửi đủ {ac_max_sid} lệnh tự động — tự động tiếp tục tạm dừng. Bật/tắt lại trong chi tiết phiên để gửi thêm.)</i>"

                    markup = {
                        "inline_keyboard": [
                            [
                                {"text": "💬 Gửi 'tiếp tục'", "callback_data": f"send_prompt:{sid}"},
                                {"text": "✍️ Soạn phản hồi / Gửi ảnh", "callback_data": f"chat_mode:{sid}"}
                            ],
                            [
                                {"text": "📜 Đọc toàn bộ", "callback_data": f"read_ans:{sid}"},
                                {"text": "📊 Bảng trạng thái", "callback_data": "refresh_status"}
                            ]
                        ]
                    }
                    self.send_message(chat_id, msg, markup)

                # 🟡 Phiên cần người dùng duyệt quyền hoặc trả lời câu hỏi
                elif current_state == "waiting_user":
                    q_data = get_active_question_for_session(sid)
                    if q_data and q_data.get("questions"):
                        q_list = q_data["questions"]
                        q_obj = q_list[0]
                        q_text = q_obj.get("question", "Vui lòng chọn đáp án:")
                        q_header = q_obj.get("header", "CÂU HỎI TỪ AI")
                        options = q_obj.get("options", [])
                        is_multi = q_obj.get("multiple", False)

                        self.question_state[sid] = {
                            "session_id": sid,
                            "title": title,
                            "header": q_header,
                            "question": q_text,
                            "options": [opt.get("label", "") for opt in options],
                            "selected_indices": set(),
                            "multiple": is_multi
                        }

                        clean_q_text = q_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                        clean_q_header = q_header.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

                        lines = [
                            f"🟡 <b>[OPENCODE - CÂU HỎI TỪ AI]</b>\n",
                            f"📌 <b>Phiên:</b> <b>{title}</b>",
                            f"❓ <b>{clean_q_header}:</b>\n{clean_q_text}\n",
                            f"<i>({'Lựa chọn nhiều - Checkbox' if is_multi else 'Chọn một câu trả lời'})</i>",
                            "─────────────────────"
                        ]

                        for idx, opt in enumerate(options, 1):
                            lbl = opt.get("label", f"Lựa chọn {idx}").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                            desc = opt.get("description", "").strip().replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                            lines.append(f"<b>{idx}. {lbl}</b>")
                            if desc:
                                lines.append(f"   📝 <i>{desc}</i>\n")
                            else:
                                lines.append("")

                        lines.append("─────────────────────")
                        if is_multi:
                            lines.append("👇 <i>Tích chọn các mục bên dưới rồi bấm <b>Xác nhận & Gửi</b>:</i>")
                        else:
                            lines.append("👇 <i>Chạm vào một lựa chọn bên dưới để chọn & gửi ngay:</i>")

                        msg = "\n".join(lines)

                        keyboard = []
                        if is_multi:
                            for idx, opt in enumerate(options):
                                lbl = opt.get("label", f"Lựa chọn {idx+1}")
                                short_lbl = lbl if len(lbl) <= 24 else lbl[:23] + "…"
                                keyboard.append([{"text": f"⬜ {idx+1}. {short_lbl}", "callback_data": f"q_cb_toggle:{sid}:{idx}"}])
                            keyboard.append([{"text": "🚀 Xác nhận & Gửi lựa chọn", "callback_data": f"q_cb_submit:{sid}"}])
                        else:
                            for idx, opt in enumerate(options):
                                lbl = opt.get("label", f"Lựa chọn {idx+1}")
                                short_lbl = lbl if len(lbl) <= 25 else lbl[:24] + "…"
                                keyboard.append([{"text": f"👉 {idx+1}. {short_lbl}", "callback_data": f"q_radio_select:{sid}:{idx}"}])

                        keyboard.append([
                            {"text": "✍️ Soạn câu trả lời tự do", "callback_data": f"chat_mode:{sid}"},
                            {"text": "⏭ Bỏ qua câu hỏi", "callback_data": f"q_dismiss:{sid}"}
                        ])
                        keyboard.append([
                            {"text": "📸 Chụp màn hình", "callback_data": "cmd_screen"},
                            {"text": "📊 Bảng trạng thái", "callback_data": "refresh_status"}
                        ])
                        self.send_message(chat_id, msg, {"inline_keyboard": keyboard})
                    else:
                        # Hộp thoại xin cấp quyền (Permission prompt)
                        msg = (
                            f"🟡 <b>[OPENCODE - CẦN CẤP QUYỀN]</b>\n\n"
                            f"📌 <b>Phiên:</b> <b>{title}</b>\n"
                            f"📁 <b>Thư mục:</b> <code>{directory}</code>\n"
                            f"⚠️ <b>Nội dung:</b> {detail}\n"
                            f"─────────────────────\n"
                            f"👇 <i>Chạm để phê duyệt trực tiếp từ điện thoại:</i>"
                        )
                        markup = {
                            "inline_keyboard": [
                                [
                                    {"text": "✅ Cho phép một lần", "callback_data": f"perm_act:{sid}:once"},
                                    {"text": "🔄 Cho phép luôn", "callback_data": f"perm_act:{sid}:always"}
                                ],
                                [
                                    {"text": "❌ Từ chối", "callback_data": f"perm_act:{sid}:deny"},
                                    {"text": "📸 Chụp màn hình kiểm tra", "callback_data": "cmd_screen"}
                                ],
                                [
                                    {"text": "📊 Bảng trạng thái", "callback_data": "refresh_status"}
                                ]
                            ]
                        }
                        self.send_message(chat_id, msg, markup)
