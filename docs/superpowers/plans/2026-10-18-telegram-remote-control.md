# Kế hoạch Triển khai: Hệ thống Giám sát & Điều khiển OpenCode Từ Xa Qua Telegram Bot

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Xây dựng hệ thống điều khiển và giám sát các phiên OpenCode từ xa qua Telegram Bot, hỗ trợ menu nút bấm, chụp ảnh màn hình, xem bảng trạng thái đèn màu và tương tác duyệt câu hỏi dạng checkbox trực tiếp trên điện thoại.

**Architecture:** Tạo module `telegram_bot.py` chạy long-polling ngầm trong background thread được quản lý bởi `app.py`. Bot kết nối đọc cơ sở dữ liệu `opencode.db` để cập nhật trạng thái các phiên làm việc, gửi ảnh chụp màn hình máy tính qua Pillow, và hiển thị giao diện tương tác trên Telegram gồm Persistent Reply Keyboard (Menu nút bấm cố định) cùng Inline Keyboards (Checkbox/Radio duyệt quyền).

**Tech Stack:** Python 3.14, PySide6, SQLite3, Requests, Pillow (PIL), Windows Win32 API.

**Spec:** `E:\giam sat ai\docs\specs\2026-10-18-telegram-remote-control-design.md`

## Global Constraints
- Hệ thống chạy trên Windows 10/11 x64, Python 3.14.
- Bảo mật tuyệt đối: Chỉ chấp nhận tin nhắn và tương tác từ đúng `chat_id` đã cấu hình trong `config.json`.
- Luồng bot chạy nền (daemon thread) không được gây giật lag hoặc treo giao diện widget nổi PySide6.
- Toàn bộ nội dung giao diện người dùng hiển thị trên Telegram và máy tính đều sử dụng tiếng Việt.

## Review Focus
1. Telegram long-polling khi mất mạng hoặc token sai: Không làm crash ứng dụng, tự động thử lại với exponential backoff.
2. Tránh gửi trùng lặp thông báo (notification duplicate) khi quét vòng lặp 1 giây.
3. Checkbox đa lựa chọn trên Telegram: Quản lý trạng thái bộ đệm toggle `⬜ / ☑️` chính xác cho từng người dùng trước khi bấm xác nhận.
4. Chụp màn hình khi đa màn hình hoặc màn hình độ phân giải cao: Đảm bảo hình ảnh được nén dung lượng hợp lý (< 2MB) để gửi nhanh qua Telegram API.
5. Khi người lạ (chat_id khác) nhắn tin vào Bot: Chặn ngay lập tức và không phản hồi bất kỳ thông tin nào của máy tính.

---

### Task 1: Bộ điều khiển Telegram Bot & Menu nút bấm tương tác (ReplyKeyboard & Command Menu)

**Files:**
- Create: `E:\giam sat ai\telegram_bot.py`
- Test: `E:\giam sat ai\tests\test_telegram_bot.py`

**Interfaces:**
- Produces: `class OpenCodeTelegramBot` với các phương thức `start()`, `stop()`, `send_notification(text, reply_markup=None)`, `register_commands()`.
- Consumes: `E:\giam sat ai\config.json` lấy `telegram_bot_token`, `telegram_chat_id`, `telegram_enabled`.

- [ ] **Step 1: Viết test kiểm tra xác thực Chat ID và cấu trúc Menu nút bấm**

```python
# tests/test_telegram_bot.py
import pytest
from telegram_bot import OpenCodeTelegramBot

def test_auth_gate():
    bot = OpenCodeTelegramBot(token="fake", authorized_chat_id="123456")
    assert bot.is_authorized(123456) is True
    assert bot.is_authorized("123456") is True
    assert bot.is_authorized(999999) is False

def test_menu_keyboard_structure():
    bot = OpenCodeTelegramBot(token="fake", authorized_chat_id="123456")
    kb = bot.get_persistent_menu_keyboard()
    assert "keyboard" in kb
    texts = [btn["text"] for row in kb["keyboard"] for btn in row]
    assert any("Bảng trạng thái" in t for t in texts)
    assert any("Chụp màn hình" in t for t in texts)
```

- [ ] **Step 2: Chạy test để xác nhận test fail**

Run: `python -m pytest E:\giam sat ai\tests\test_telegram_bot.py -v`
Expected: FAIL (module chưa tồn tại).

- [ ] **Step 3: Cài đặt `OpenCodeTelegramBot` trong `E:\giam sat ai\telegram_bot.py`**
  - Khởi tạo bot với token và authorized_chat_id.
  - Cài đặt `setMyCommands` đăng ký lệnh `/status`, `/screen`, `/help`.
  - Cài đặt bàn phím menu cố định `ReplyKeyboardMarkup` gồm các nút: `[ 📊 Bảng trạng thái ]`, `[ 📸 Chụp màn hình ]`, `[ ❓ Trợ giúp ]`.
  - Xây dựng vòng lặp long-polling `getUpdates` với timeout 20s trong luồng ngầm daemon.

- [ ] **Step 4: Chạy test để xác nhận pass**

Run: `python -m pytest E:\giam sat ai\tests\test_telegram_bot.py -v`
Expected: PASS.

---

### Task 2: Chụp ảnh màn hình từ xa & Bảng trạng thái tương tác qua Telegram

**Files:**
- Modify: `E:\giam sat ai\telegram_bot.py`
- Test: `E:\giam sat ai\tests\test_telegram_features.py`

**Interfaces:**
- Produces: `handle_status_command()`, `handle_screen_command()`, `format_status_message(sessions)`.
- Consumes: `core_monitor.poll_all_sessions()` để lấy dữ liệu thời gian thực của các phiên OpenCode.

- [ ] **Step 1: Viết test định dạng tin nhắn trạng thái và nút bấm inline**

```python
# tests/test_telegram_features.py
from telegram_bot import format_status_message

def test_format_status_message():
    sample_sessions = [
        {'title': 'WorkNC', 'directory': 'D:/WorkNC', 'color': 'green', 'label': 'Đang chạy', 'detail': 'Chạy test'},
        {'title': 'QR', 'directory': 'E:/QR', 'color': 'red', 'label': 'Đã xong', 'detail': 'Phiên đã dừng'}
    ]
    text, markup = format_status_message(sample_sessions)
    assert "🟢" in text
    assert "🔴" in text
    assert "WorkNC" in text
    assert any(btn.get("callback_data") == "refresh_status" for row in markup["inline_keyboard"] for btn in row)
```

- [ ] **Step 2: Chạy test để kiểm tra fail**

Run: `python -m pytest E:\giam sat ai\tests\test_telegram_features.py -v`
Expected: FAIL.

- [ ] **Step 3: Triển khai xử lý lệnh `/screen` và `/status` trong `telegram_bot.py`**
  - Chức năng `/screen`: Chụp màn hình bằng `PIL.ImageGrab.grab()`, chuyển đổi thành JPEG buffer (chất lượng 80% để gửi nhanh), gửi qua Telegram API `sendPhoto`.
  - Chức năng `/status` & nút `[ 📊 Bảng trạng thái ]`: Quét `poll_all_sessions()`, hiển thị danh sách các phiên kèm nút bấm `[ 🔄 Làm mới ]`.
  - Xử lý callback `refresh_status`: Cập nhật nội dung tin nhắn cũ qua `editMessageText` mà không gửi tin nhắn mới.

- [ ] **Step 4: Chạy test để kiểm tra pass**

Run: `python -m pytest E:\giam sat ai\tests\test_telegram_features.py -v`
Expected: PASS.

---

### Task 3: Tương tác 2 chiều duyệt câu hỏi & Checkbox từ Telegram

**Files:**
- Modify: `E:\giam sat ai\telegram_bot.py`
- Modify: `E:\giam sat ai\core_monitor.py`
- Test: `E:\giam sat ai\tests\test_telegram_interaction.py`

**Interfaces:**
- Produces: `notify_question_or_permission(session_data, question_data)`, `handle_checkbox_toggle(callback_data)`, `handle_submit_answer(callback_data)`.
- Consumes: `core_monitor.py` khi phát hiện trạng thái 🟡 VÀNG.

- [ ] **Step 1: Viết test cho logic chuyển đổi nút Checkbox `⬜ / ☑️`**

```python
# tests/test_telegram_interaction.py
from telegram_bot import toggle_checkbox_selection

def test_checkbox_toggle():
    selected = set([0])
    # Toggle item 1
    selected = toggle_checkbox_selection(selected, 1)
    assert 1 in selected
    assert 0 in selected
    # Toggle item 0 off
    selected = toggle_checkbox_selection(selected, 0)
    assert 0 not in selected
    assert 1 in selected
```

- [ ] **Step 2: Chạy test để kiểm tra fail**

Run: `python -m pytest E:\giam sat ai\tests\test_telegram_interaction.py -v`
Expected: FAIL.

- [ ] **Step 3: Triển khai bảng nút Checkbox và Radio cho Telegram**
  - Khi phát hiện câu hỏi `question`:
    - Nếu `multiple: true`: Tạo các nút `[ ⬜ Nhãn lựa chọn ]`, kèm nút `[ 🚀 Xác nhận & Gửi ]`. Khi bấm, toggle giữa `⬜` và `☑️` qua `editMessageReplyMarkup`.
    - Nếu `multiple: false`: Tạo các nút lựa chọn trực tiếp. Bấm vào là duyệt gửi luôn.
  - Khi phát hiện xin cấp quyền (Permission prompt):
    - Tạo 2 nút: `[ ✅ Cho phép ]` và `[ ❌ Từ chối ]`.
  - Khi người dùng gửi lựa chọn: Cập nhật kết quả vào database OpenCode và phản hồi thông báo hoàn tất trên Telegram.

- [ ] **Step 4: Chạy test để kiểm tra pass**

Run: `python -m pytest E:\giam sat ai\tests\test_telegram_interaction.py -v`
Expected: PASS.

---

### Task 4: Tích hợp trọn gói vào ứng dụng `app.py` & Kiểm thử thực tế

**Files:**
- Modify: `E:\giam sat ai\app.py`
- Modify: `E:\giam sat ai\telegram_notifier.py`
- Test: Kiểm thử chức năng trực tiếp trên hệ thống.

- [ ] **Step 1: Tích hợp khởi động bot ngầm trong `app.py`**
  - Khi ứng dụng `app.py` chạy hoặc khi người dùng lưu cài đặt Telegram: Khởi động bot worker thread.
  - Khi đóng ứng dụng: Tự động giải phóng bot thread êm ái.

- [ ] **Step 2: Chạy kiểm thử toàn bộ test suite**

Run: `python -m pytest E:\giam sat ai\tests -v`
Expected: ALL PASS.

- [ ] **Step 3: Khởi động lại ứng dụng `app.py` và kiểm tra hoạt động trên máy tính**
  - Kiểm tra giao diện nổi PySide6.
  - Kiểm tra kết nối Telegram Bot.
