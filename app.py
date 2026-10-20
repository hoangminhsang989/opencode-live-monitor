import os
import sys
import json
import win32gui
import win32con
import win32process
import psutil

from PySide6.QtCore import Qt, QTimer, QPoint
from PySide6.QtGui import QColor, QCursor
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFrame, QScrollArea, QGraphicsDropShadowEffect,
    QDialog, QLineEdit, QCheckBox, QMessageBox, QSpinBox
)

from core_monitor import poll_all_sessions
from telegram_bot import OpenCodeTelegramBot

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config.json")

def focus_opencode():
    """Bring the OpenCode desktop application window to the foreground."""
    def enum_cb(hwnd, results):
        if win32gui.IsWindowVisible(hwnd):
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            try:
                p = psutil.Process(pid)
                if 'opencode' in p.name().lower():
                    results.append(hwnd)
            except Exception:
                pass

    hwnds = []
    win32gui.EnumWindows(enum_cb, hwnds)
    for hwnd in hwnds:
        try:
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(hwnd)
            break
        except Exception:
            pass

class TelegramSettingsDialog(QDialog):
    def __init__(self, parent=None, notifier: TelegramNotifier = None, on_saved=None):
        super().__init__(parent)
        self.notifier = notifier
        self.on_saved = on_saved
        self.setWindowTitle("Cài đặt Telegram Bot")
        self.setFixedSize(360, 340)
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        
        self.init_ui()
        self.load_data()

    def init_ui(self):
        self.setStyleSheet("""
            QDialog {
                background-color: #18181b;
                color: #f4f4f5;
                font-family: 'Segoe UI', system-ui, sans-serif;
            }
            QLabel {
                color: #e4e4e7;
                font-size: 11px;
            }
            QLabel#heading {
                font-size: 13px;
                font-weight: bold;
                color: #38bdf8;
            }
            QCheckBox {
                color: #f4f4f5;
                font-size: 12px;
                font-weight: 600;
                spacing: 8px;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border-radius: 4px;
                border: 1px solid #52525b;
                background-color: #27272a;
            }
            QCheckBox::indicator:checked {
                background-color: #0284c7;
                border-color: #38bdf8;
            }
            QLineEdit {
                background-color: #27272a;
                border: 1px solid #3f3f46;
                border-radius: 6px;
                padding: 6px 10px;
                color: #ffffff;
                font-size: 11px;
            }
            QLineEdit:focus {
                border-color: #38bdf8;
            }
            QPushButton {
                background-color: #27272a;
                border: 1px solid #3f3f46;
                border-radius: 6px;
                color: #f4f4f5;
                padding: 6px 12px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #3f3f46;
            }
            QPushButton#btnSave {
                background-color: #0284c7;
                border-color: #0369a1;
                color: #ffffff;
            }
            QPushButton#btnSave:hover {
                background-color: #0369a1;
            }
            QFrame#guideBox {
                background-color: rgba(255, 255, 255, 0.04);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 6px;
                padding: 8px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Title
        title_lbl = QLabel("✈️ THÔNG BÁO TELEGRAM")
        title_lbl.setObjectName("heading")
        layout.addWidget(title_lbl)

        # Enable Checkbox
        self.chk_enable = QCheckBox("Bật thông báo khi ĐÃ XONG hoặc CẦN TRẢ LỜI")
        layout.addWidget(self.chk_enable)

        # Bot Token
        layout.addWidget(QLabel("Bot Token (từ @BotFather):"))
        self.txt_token = QLineEdit()
        self.txt_token.setPlaceholderText("Ví dụ: 789123456:AAFlk_...")
        layout.addWidget(self.txt_token)

        # Chat ID
        layout.addWidget(QLabel("Chat ID của bạn (từ @userinfobot):"))
        self.txt_chat_id = QLineEdit()
        self.txt_chat_id.setPlaceholderText("Ví dụ: 123456789")
        layout.addWidget(self.txt_chat_id)

        # Auto-continue settings
        self.chk_auto_continue_all = QCheckBox("Tự động gửi lệnh tiếp tục cho TẤT CẢ các phiên")
        layout.addWidget(self.chk_auto_continue_all)

        layout.addWidget(QLabel("Nội dung lệnh tiếp tục (Mặc định: tiếp tục):"))
        self.txt_auto_prompt = QLineEdit()
        self.txt_auto_prompt.setPlaceholderText("tiếp tục")
        layout.addWidget(self.txt_auto_prompt)

        layout.addWidget(QLabel("Số lượng lệnh tự động gửi cho mỗi phiên (1-99):"))
        self.spn_auto_count = QSpinBox()
        self.spn_auto_count.setRange(1, 99)
        self.spn_auto_count.setValue(1)
        self.spn_auto_count.setToolTip(
            "Mỗi khi phiên chạy xong, bot tự gửi lệnh 'tiếp tục'.\n"
            "Số này giới hạn tổng số lệnh tự động gửi cho mỗi phiên\n"
            "(đếm từ khi bật, reset khi bạn bật/tắt lại hoặc gửi lệnh thủ công)."
        )
        layout.addWidget(self.spn_auto_count)

        # Test Status Label
        self.lbl_status = QLabel("")
        self.lbl_status.setStyleSheet("font-size: 11px;")
        layout.addWidget(self.lbl_status)

        # Action Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)

        self.btn_test = QPushButton("Gửi thử (Test)")
        self.btn_test.clicked.connect(self.on_test_clicked)
        btn_layout.addWidget(self.btn_test)

        btn_layout.addStretch()

        self.btn_save = QPushButton("Lưu cấu hình")
        self.btn_save.setObjectName("btnSave")
        self.btn_save.clicked.connect(self.on_save_clicked)
        btn_layout.addWidget(self.btn_save)

        layout.addLayout(btn_layout)

        # Mini Guide Box
        guide_box = QFrame()
        guide_box.setObjectName("guideBox")
        g_layout = QVBoxLayout(guide_box)
        g_layout.setContentsMargins(8, 8, 8, 8)
        g_layout.setSpacing(4)
        
        g_title = QLabel("💡 <b>Cách lấy Token & Chat ID siêu nhanh:</b>")
        g_step1 = QLabel("1. Chat với <b>@BotFather</b> gõ <code>/newbot</code> để tạo bot và lấy <b>Token</b>.")
        g_step2 = QLabel("2. Bấm <b>Start</b> bot vừa tạo, rồi chat với <b>@userinfobot</b> để lấy <b>Id</b>.")
        for lbl in (g_title, g_step1, g_step2):
            lbl.setStyleSheet("color: #a1a1aa; font-size: 10px;")
            g_layout.addWidget(lbl)

        layout.addWidget(guide_box)

    def load_data(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.chk_enable.setChecked(bool(data.get("telegram_enabled", False)))
                    self.txt_token.setText(data.get("telegram_bot_token", ""))
                    self.txt_chat_id.setText(data.get("telegram_chat_id", ""))
                    self.chk_auto_continue_all.setChecked(bool(data.get("auto_continue_all", False)))
                    self.txt_auto_prompt.setText(data.get("auto_continue_prompt", "tiếp tục"))
                    try:
                        self.spn_auto_count.setValue(max(1, min(99, int(data.get("auto_continue_count", 1)))))
                    except (TypeError, ValueError):
                        self.spn_auto_count.setValue(1)
            except Exception:
                pass

    def on_test_clicked(self):
        token = self.txt_token.text().strip()
        chat_id = self.txt_chat_id.text().strip()
        if not token or not chat_id:
            self.lbl_status.setText("⚠️ Chưa nhập Token hoặc Chat ID!")
            self.lbl_status.setStyleSheet("color: #facc15;")
            return

        self.lbl_status.setText("Đang gửi tin nhắn thử...")
        self.lbl_status.setStyleSheet("color: #38bdf8;")
        QApplication.processEvents()

        cfg = {"telegram_bot_token": token, "telegram_chat_id": chat_id, "telegram_enabled": True}
        temp_bot = OpenCodeTelegramBot(lambda: cfg)
        res = temp_bot.send_message(
            chat_id,
            "⚡ <b>OpenCode Live Monitor</b>\n\n✅ Kết nối Telegram Bot thành công!\nBạn có thể bấm vào các nút menu bên dưới để bắt đầu điều khiển từ xa."
        )
        if res and res.get("ok"):
            self.lbl_status.setText("✅ Gửi thành công! Kiểm tra tin nhắn Telegram.")
            self.lbl_status.setStyleSheet("color: #4ade80;")
        else:
            err = res.get("description", "Lỗi gửi tin nhắn") if res else "Lỗi kết nối tới Telegram"
            self.lbl_status.setText(f"❌ {err[:60]}")
            self.lbl_status.setStyleSheet("color: #f87171;")

    def on_save_clicked(self):
        token = self.txt_token.text().strip()
        chat_id = self.txt_chat_id.text().strip()
        enabled = self.chk_enable.isChecked()
        auto_all = self.chk_auto_continue_all.isChecked()
        prompt_txt = self.txt_auto_prompt.text().strip() or "tiếp tục"

        data = {}
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            except Exception:
                pass

        data["telegram_enabled"] = enabled
        data["telegram_bot_token"] = token
        data["telegram_chat_id"] = chat_id
        data["auto_continue_all"] = auto_all
        data["auto_continue_prompt"] = prompt_txt
        data["auto_continue_count"] = self.spn_auto_count.value()

        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f)
            if self.on_saved:
                self.on_saved()
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Lỗi", f"Không thể lưu cấu hình: {e}")

class SessionRowWidget(QFrame):
    def __init__(self, session_data: dict, parent=None):
        super().__init__(parent)
        self.session_id = session_data.get('sessionId')
        self.setCursor(QCursor(Qt.PointingHandCursor))
        self.setObjectName("sessionRow")
        self.setFixedHeight(38)
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 3, 8, 3)
        layout.setSpacing(8)
        
        # Color dot indicator (compact 8x8px)
        self.dot = QFrame()
        self.dot.setFixedSize(8, 8)
        self.dot.setObjectName("statusDot")
        layout.addWidget(self.dot, alignment=Qt.AlignVCenter)
        
        # Text container (compact 2-line layout)
        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(1)
        
        title_text = session_data.get('title') or self.session_id
        self.title_label = QLabel(title_text)
        self.title_label.setObjectName("rowTitle")
        self.title_label.setToolTip(title_text)
        
        directory = session_data.get('directory') or ''
        dir_name = os.path.basename(directory.rstrip('\\/')) or directory
        detail = session_data.get('detail') or ''
        
        sub_text = f"[{dir_name}] {detail}" if dir_name else detail
        self.sub_label = QLabel(sub_text)
        self.sub_label.setObjectName("rowSubtitle")
        self.sub_label.setToolTip(f"Thư mục: {directory}\nTrạng thái: {detail}")
        
        text_layout.addWidget(self.title_label)
        text_layout.addWidget(self.sub_label)
        layout.addLayout(text_layout, stretch=1)
        
        # Status text badge
        self.badge_label = QLabel()
        self.badge_label.setObjectName("rowBadge")
        layout.addWidget(self.badge_label, alignment=Qt.AlignVCenter)
        
        self.update_data(session_data)

    def update_data(self, session_data: dict):
        color = session_data.get('color', 'green')
        detail = session_data.get('detail', '')
        directory = session_data.get('directory') or ''
        dir_name = os.path.basename(directory.rstrip('\\/')) or directory
        
        title_text = session_data.get('title') or self.session_id
        self.title_label.setText(title_text)
        self.title_label.setToolTip(title_text)
        
        sub_text = f"[{dir_name}] {detail}" if dir_name else detail
        self.sub_label.setText(sub_text)
        self.sub_label.setToolTip(f"Thư mục: {directory}\nTrạng thái: {detail}")
        
        if color == 'green':
            dot_color = "#22c55e"
            badge_bg = "rgba(34, 197, 94, 0.35)"
            badge_border = "rgba(34, 197, 94, 0.6)"
            badge_fg = "#bbf7d0"
            badge_text = "Đang chạy"
        elif color == 'yellow':
            dot_color = "#facc15"
            badge_bg = "rgba(234, 179, 8, 0.35)"
            badge_border = "rgba(234, 179, 8, 0.6)"
            badge_fg = "#fef08a"
            badge_text = "Cần trả lời"
        else:  # red
            dot_color = "#ef4444"
            badge_bg = "rgba(239, 68, 68, 0.40)"
            badge_border = "rgba(239, 68, 68, 0.7)"
            badge_fg = "#fecaca"
            badge_text = "Đã xong"
        
        self.dot.setStyleSheet(f"""
            QFrame#statusDot {{
                background-color: {dot_color};
                border-radius: 4px;
                border: 1px solid rgba(255, 255, 255, 0.5);
            }}
        """)
        
        self.badge_label.setText(badge_text)
        self.badge_label.setStyleSheet(f"""
            QLabel#rowBadge {{
                background-color: {badge_bg};
                border: 1px solid {badge_border};
                color: {badge_fg};
                border-radius: 3px;
                padding: 1px 5px;
                font-size: 10px;
                font-weight: 600;
            }}
        """)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            focus_opencode()
        super().mousePressEvent(event)

class OpenCodeMonitorWindow(QWidget):
    def __init__(self):
        super().__init__()
        
        self.setWindowFlags(
            Qt.FramelessWindowHint |
            Qt.WindowStaysOnTopHint |
            Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        
        self.is_collapsed = False
        self.drag_position = QPoint()
        self.opacity_mode = 'glass'
        self.cached_config = {}
        
        self.bot = OpenCodeTelegramBot(self.get_config_dict, config_updater=self.save_config_from_bot)
        
        self.init_ui()
        self.load_config()
        self.bot.start()
        
        # Polling timer (every 1000ms)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_sessions)
        self.timer.start(1000)
        
        self.refresh_sessions()

    def get_config_dict(self):
        return self.cached_config

    def save_config_from_bot(self, new_cfg: dict):
        self.cached_config = new_cfg
        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(new_cfg, f)
        except Exception:
            pass

    def init_ui(self):
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(6, 6, 6, 6)
        
        # Container Card
        self.container = QFrame(self)
        self.container.setObjectName("mainContainer")
        
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(16)
        shadow.setColor(QColor(0, 0, 0, 150))
        shadow.setOffset(0, 3)
        self.container.setGraphicsEffect(shadow)
        
        self.container_layout = QVBoxLayout(self.container)
        self.container_layout.setContentsMargins(8, 7, 8, 8)
        self.container_layout.setSpacing(6)
        
        # --- HEADER ---
        self.header = QFrame(self.container)
        self.header.setObjectName("headerFrame")
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(2, 0, 2, 0)
        header_layout.setSpacing(5)
        
        self.title_icon = QLabel("⚡")
        self.title_icon.setStyleSheet("font-size: 12px; background: transparent;")
        header_layout.addWidget(self.title_icon)
        
        self.title_text = QLabel("OpenCode")
        self.title_text.setObjectName("headerTitle")
        header_layout.addWidget(self.title_text)

        # Allow dragging from container and header smoothly
        self.container.mousePressEvent = self.mousePressEvent
        self.header.mousePressEvent = self.mousePressEvent
        self.title_text.mousePressEvent = self.mousePressEvent
        
        # Summary Counter Badge
        self.summary_badge = QLabel("0")
        self.summary_badge.setObjectName("summaryBadge")
        header_layout.addWidget(self.summary_badge)
        
        header_layout.addStretch()
        
        # Telegram Settings Button
        self.btn_telegram = QPushButton("✈️")
        self.btn_telegram.setObjectName("btnHeaderAction")
        self.btn_telegram.setFixedSize(18, 18)
        self.btn_telegram.setToolTip("Cấu hình thông báo Telegram Bot")
        self.btn_telegram.clicked.connect(self.open_telegram_settings)
        header_layout.addWidget(self.btn_telegram)
        
        # Opacity / Transparency cycle button
        self.btn_opacity = QPushButton("👁")
        self.btn_opacity.setObjectName("btnHeaderAction")
        self.btn_opacity.setFixedSize(18, 18)
        self.btn_opacity.setToolTip("Đổi độ trong suốt: Trong suốt / Vừa / Tối")
        self.btn_opacity.clicked.connect(self.cycle_opacity)
        header_layout.addWidget(self.btn_opacity)
        
        # Minimize / Collapse Button
        self.btn_collapse = QPushButton("—")
        self.btn_collapse.setObjectName("btnHeaderAction")
        self.btn_collapse.setFixedSize(18, 18)
        self.btn_collapse.setToolTip("Thu nhỏ / Mở rộng")
        self.btn_collapse.clicked.connect(self.toggle_collapse)
        header_layout.addWidget(self.btn_collapse)
        
        # Close Button
        self.btn_close = QPushButton("✕")
        self.btn_close.setObjectName("btnCloseAction")
        self.btn_close.setFixedSize(18, 18)
        self.btn_close.setToolTip("Đóng ứng dụng")
        self.btn_close.clicked.connect(self.close)
        header_layout.addWidget(self.btn_close)
        
        self.container_layout.addWidget(self.header)
        
        # Divider
        self.divider = QFrame()
        self.divider.setFrameShape(QFrame.HLine)
        self.divider.setObjectName("dividerLine")
        self.container_layout.addWidget(self.divider)
        
        # --- SESSION LIST AREA ---
        self.scroll_area = QScrollArea(self.container)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setObjectName("scrollArea")
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        
        self.list_widget = QWidget()
        self.list_widget.setObjectName("listWidget")
        self.list_layout = QVBoxLayout(self.list_widget)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(3)
        self.list_layout.addStretch()
        
        self.scroll_area.setWidget(self.list_widget)
        self.container_layout.addWidget(self.scroll_area)
        
        self.main_layout.addWidget(self.container)
        
        self.apply_styles()
        self.setFixedWidth(310)

    def open_telegram_settings(self):
        dlg = TelegramSettingsDialog(self, notifier=self.bot, on_saved=self.on_settings_saved)
        dlg.exec()

    def on_settings_saved(self):
        self.load_config()
        if self.bot:
            self.bot.stop()
            self.bot.start()

    def cycle_opacity(self):
        modes = ['glass', 'medium', 'tint']
        idx = (modes.index(self.opacity_mode) + 1) % len(modes) if self.opacity_mode in modes else 0
        self.opacity_mode = modes[idx]
        self.apply_styles()
        self.save_config()

    def apply_styles(self):
        if self.opacity_mode == 'tint':
            container_bg = "rgba(10, 10, 16, 0.40)"
            container_border = "rgba(255, 255, 255, 0.22)"
            row_bg = "rgba(255, 255, 255, 0.08)"
            row_border = "rgba(255, 255, 255, 0.12)"
            row_hover = "rgba(255, 255, 255, 0.20)"
        elif self.opacity_mode == 'medium':
            container_bg = "rgba(10, 10, 16, 0.26)"
            container_border = "rgba(255, 255, 255, 0.22)"
            row_bg = "rgba(255, 255, 255, 0.06)"
            row_border = "rgba(255, 255, 255, 0.10)"
            row_hover = "rgba(255, 255, 255, 0.16)"
        else: # 'glass' - Siêu trong suốt nhìn xuyên thấu
            self.opacity_mode = 'glass'
            container_bg = "rgba(8, 8, 14, 0.14)"
            container_border = "rgba(255, 255, 255, 0.20)"
            row_bg = "rgba(255, 255, 255, 0.04)"
            row_border = "rgba(255, 255, 255, 0.08)"
            row_hover = "rgba(255, 255, 255, 0.14)"

        self.setStyleSheet(f"""
            QFrame#mainContainer {{
                background-color: {container_bg};
                border: 1px solid {container_border};
                border-radius: 10px;
            }}
            QFrame#headerFrame {{
                background: transparent;
            }}
            QLabel#headerTitle {{
                color: #ffffff;
                font-size: 11px;
                font-weight: 700;
                font-family: 'Segoe UI', system-ui, sans-serif;
                background: transparent;
            }}
            QLabel#summaryBadge {{
                background-color: rgba(255, 255, 255, 0.14);
                color: #ffffff;
                border-radius: 3px;
                padding: 1px 5px;
                font-size: 10px;
                font-weight: 600;
            }}
            QPushButton#btnHeaderAction {{
                background-color: transparent;
                color: #d1d5db;
                border: none;
                border-radius: 3px;
                font-size: 10px;
                font-weight: bold;
            }}
            QPushButton#btnHeaderAction:hover {{
                background-color: rgba(255, 255, 255, 0.20);
                color: #ffffff;
            }}
            QPushButton#btnCloseAction {{
                background-color: transparent;
                color: #d1d5db;
                border: none;
                border-radius: 3px;
                font-size: 10px;
                font-weight: bold;
            }}
            QPushButton#btnCloseAction:hover {{
                background-color: #ef4444;
                color: #ffffff;
            }}
            QFrame#dividerLine {{
                background-color: rgba(255, 255, 255, 0.15);
                max-height: 1px;
                border: none;
            }}
            QScrollArea#scrollArea {{
                background: transparent;
                border: none;
            }}
            QWidget#listWidget {{
                background: transparent;
            }}
            QFrame#sessionRow {{
                background-color: {row_bg};
                border: 1px solid {row_border};
                border-radius: 6px;
            }}
            QFrame#sessionRow:hover {{
                background-color: {row_hover};
                border-color: rgba(255, 255, 255, 0.35);
            }}
            QLabel#rowTitle {{
                color: #ffffff;
                font-size: 11px;
                font-weight: 600;
                font-family: 'Segoe UI', system-ui, sans-serif;
                background: transparent;
            }}
            QLabel#rowSubtitle {{
                color: #d1d5db;
                font-size: 10px;
                font-family: 'Segoe UI', system-ui, sans-serif;
                background: transparent;
            }}
            QScrollBar:vertical {{
                border: none;
                background: rgba(18, 18, 24, 0.2);
                width: 4px;
                margin: 0px;
                border-radius: 2px;
            }}
            QScrollBar::handle:vertical {{
                background: rgba(255, 255, 255, 0.35);
                min-height: 16px;
                border-radius: 2px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
        """)

    def toggle_collapse(self):
        self.is_collapsed = not self.is_collapsed
        if self.is_collapsed:
            self.scroll_area.hide()
            self.divider.hide()
            self.btn_collapse.setText("+")
            self.setFixedHeight(44)
        else:
            self.scroll_area.show()
            self.divider.show()
            self.btn_collapse.setText("—")
            self.adjust_height()

    def adjust_height(self):
        if self.is_collapsed:
            return
        count = self.list_layout.count() - 1 # exclude stretch
        
        overhead = (
            self.main_layout.contentsMargins().top() + self.main_layout.contentsMargins().bottom() +
            self.container_layout.contentsMargins().top() + self.container_layout.contentsMargins().bottom() +
            self.header.sizeHint().height() + self.divider.sizeHint().height() +
            self.container_layout.spacing() * 2
        )
        
        if count <= 0:
            list_height = 40
            self.scroll_area.setFixedHeight(list_height)
            self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            target_h = list_height + overhead + 4
        elif count <= 7:
            list_height = count * 38 + max(0, count - 1) * 3
            self.scroll_area.setFixedHeight(list_height)
            self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            target_h = list_height + overhead + 4
        else:
            list_height = 7 * 38 + 6 * 3
            self.scroll_area.setFixedHeight(list_height)
            self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            target_h = list_height + overhead + 4

        current_y = self.pos().y()
        screen = QApplication.primaryScreen().availableGeometry()
        if current_y + target_h > screen.bottom():
            new_y = max(screen.top(), screen.bottom() - target_h - 10)
            self.move(self.pos().x(), new_y)

        self.setFixedHeight(target_h)

    def refresh_sessions(self):
        try:
            sessions = poll_all_sessions()
        except Exception:
            sessions = []

        # Send Telegram notification if state changed
        if hasattr(self, 'bot') and self.bot:
            try:
                self.bot.check_and_notify(sessions)
            except Exception as e:
                try:
                    from telegram_bot import log_msg
                    log_msg(f"[app.py] Error in check_and_notify: {e}")
                except Exception:
                    pass

        counts = {'red': 0, 'yellow': 0, 'green': 0}
        for s in sessions:
            c = s.get('color', 'green')
            counts[c] = counts.get(c, 0) + 1

        summary_parts = []
        if counts['green'] > 0:
            summary_parts.append(f"🟢 {counts['green']}")
        if counts['yellow'] > 0:
            summary_parts.append(f"🟡 {counts['yellow']}")
        if counts['red'] > 0:
            summary_parts.append(f"🔴 {counts['red']}")
        
        summary_text = " ".join(summary_parts) if summary_parts else f"{len(sessions)} phiên"
        self.summary_badge.setText(summary_text)

        # Update or rebuild row widgets
        existing_rows = []
        for i in range(self.list_layout.count() - 1):
            w = self.list_layout.itemAt(i).widget()
            if isinstance(w, SessionRowWidget):
                existing_rows.append(w)

        same_sessions = (
            len(existing_rows) == len(sessions) and
            all(r.session_id == s.get('sessionId') for r, s in zip(existing_rows, sessions))
        ) if sessions else False

        if same_sessions:
            for row_widget, s in zip(existing_rows, sessions):
                row_widget.update_data(s)
        else:
            while self.list_layout.count() > 1:
                item = self.list_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()

            if not sessions:
                empty_lbl = QLabel("Không có phiên OpenCode nào mở")
                empty_lbl.setAlignment(Qt.AlignCenter)
                empty_lbl.setStyleSheet("color: #d1d5db; font-size: 11px; padding: 10px 0; background: transparent;")
                self.list_layout.insertWidget(0, empty_lbl)
            else:
                for s in sessions:
                    row = SessionRowWidget(s)
                    self.list_layout.insertWidget(self.list_layout.count() - 1, row)
            self.adjust_height()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            win32gui.ReleaseCapture()
            win32gui.SendMessage(int(self.winId()), win32con.WM_SYSCOMMAND, win32con.SC_MOVE + win32con.HTCAPTION, 0)
            self.save_config()
            event.accept()

    def save_config(self):
        pos = self.pos()
        data = self.cached_config.copy()
        data["x"] = pos.x()
        data["y"] = pos.y()
        data["opacity_mode"] = self.opacity_mode
        self.cached_config = data
        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f)
        except Exception:
            pass

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.cached_config = data
                    x, y = data.get("x"), data.get("y")
                    if x is not None and y is not None:
                        self.move(x, y)
                    self.opacity_mode = data.get("opacity_mode", "glass")
                    self.apply_styles()
                    return
            except Exception:
                pass
        
        screen = QApplication.primaryScreen().availableGeometry()
        x = screen.right() - self.width() - 15
        y = screen.bottom() - 280
        self.move(max(x, 0), max(y, 0))
        self.apply_styles()

    def closeEvent(self, event):
        if hasattr(self, 'bot') and self.bot:
            self.bot.stop()
        super().closeEvent(event)

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("OpenCodeLiveMonitor")
    
    window = OpenCodeMonitorWindow()
    window.show()
    
    sys.exit(app.exec())

if __name__ == '__main__':
    main()
