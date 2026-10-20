# OpenCode Live Monitor & Remote Controller

Phần mềm giám sát giao diện nổi (Floating HUD) và bộ điều khiển từ xa 2 chiều qua Telegram dành cho các phiên làm việc của OpenCode AI trên Windows.

---

## 🌟 Tính năng nổi bật

### 1. Giao diện HUD nổi trên màn hình (PySide6)
- **Thiết kế không viền, siêu mượt:** Kéo thả tự nhiên trên màn hình bằng DWM Native API (`WM_SYSCOMMAND + SC_MOVE`).
- **Tự động co giãn:** Tự động điều chỉnh kích thước theo số lượng tab/phiên OpenCode đang hoạt động.
- **Chế độ trong suốt đa dạng:** Tùy biến nhanh 3 mức độ hiển thị:
  - 👁 **Glass (Siêu trong suốt):** Nhìn xuyên thấu nội dung bên dưới, không che khuất không gian làm việc.
  - 👁 **Medium (Vừa phải):** Cân bằng giữa thẩm mỹ và độ tương phản.
  - 👁 **Tint (Tối rõ nét):** Tối ưu khi làm việc với nền sáng.
- **Đèn trạng thái 3 màu chuẩn xác:**
  - 🟢 **XANH (Running):** AI đang suy nghĩ, duyệt file, soạn code hoặc chạy công cụ.
  - 🟡 **VÀNG (Waiting):** AI dừng chờ người dùng trả lời câu hỏi (`tool question`) hoặc hộp thoại xin cấp quyền đang hiển thị.
  - 🔴 **ĐỎ (Completed/Stopped):** Phiên đã hoàn tất lượt trả lời hoặc đã dừng.

### 2. Điều khiển từ xa 2 chiều qua Telegram Bot
- **Bảng trạng thái trực tiếp (`/status`):** Cập nhật thời gian thực ngay trên tin nhắn với nút bật/tắt Auto-Refresh.
- **Tự động gửi thông báo:** Gửi nội dung trả lời chi tiết của AI ngay khi hoàn thành lượt.
- **Chụp ảnh màn hình (`/screen`):** Xem màn hình máy tính từ xa qua tin nhắn ảnh Telegram.
- **Phê duyệt quyền từ xa:** Cấp quyền chạy lệnh terminal, chỉnh sửa tệp hoặc từ chối trực tiếp từ điện thoại (`Cho phép một lần`, `Cho phép luôn`, `Từ chối`).
- **Trả lời câu hỏi trắc nghiệm:** Hỗ trợ Radio (chọn đơn) và Checkbox (chọn nhiều) kèm giải thích chi tiết và nút bỏ qua.
- **Tự động tiếp tục (Auto-Continue):**
  - Tùy chỉnh bật/tắt riêng cho từng phiên hoặc toàn bộ.
  - Giới hạn số lần tự động gửi (1 - 99 lần).
  - **Xác minh đóng (Closed-Loop SQLite Verification):** Tự động kiểm tra vào hàng đợi SQLite của OpenCode trước khi xác nhận lượt gửi thành công, hoàn trả lượt nếu thất bại.
- **Thao tác hoàn toàn chạy ngầm:** Ưu tiên 100% qua REST API ngầm trong bộ nhớ, không chiếm chuột, không cướp tiêu điểm bàn phím, không ghi đè Clipboard.

### 3. Điều khiển không chạm qua REST API & CLI (Zero UI Touch)
- **Tự động kết nối OpenCode Sidecar REST API:** Trích xuất token xác thực và cổng TCP trực tiếp từ bộ nhớ tiến trình NodeService qua Win32 API (`NtQueryInformationProcess`).
- **100% Vô hình & Không cướp chuột/phím:** Gửi prompt, cấp quyền, trả lời trắc nghiệm trực tiếp qua HTTP REST ngầm. Cửa sổ OpenCode không bị kéo lên màn hình, hoạt động ngay cả khi **máy tính đang khóa màn hình (Lock Screen)** hoặc điều khiển từ xa qua **SSH**.
- **Bộ công cụ dòng lệnh (`cli.py`):**
  - `python cli.py status` — Xem bảng trạng thái các phiên kèm tool đang chạy.
  - `python cli.py watch` — Dashboard TUI trực tiếp tự làm mới mỗi 1.5s.
  - `python cli.py send <session> <prompt>` — Gửi lệnh vào phiên chỉ định.
  - `python cli.py approve [once|always|reject]` — Cấp quyền chạy lệnh/sửa file.
  - `python cli.py question` & `python cli.py answer <1,2>` — Xem và trả lời câu hỏi.
  - `python cli.py abort <session>` — Dừng phiên đang chạy.
- **REST API Gateway độc lập (`api_server.py`):** Máy chủ FastAPI (cổng 7080) cung cấp REST API mở cho các ứng dụng ngoài, curl từ máy khác, bash script, webhooks.

---

## 🚀 Cài đặt & Sử dụng

### Yêu cầu hệ thống
- Hệ điều hành: **Windows 10 / Windows 11 (64-bit)**
- **Python 3.10+** (đã kiểm thử tương thích tốt trên Python 3.12, 3.14)
- Ứng dụng **OpenCode Desktop**

### Cài đặt thư viện
```bash
git clone https://github.com/hoangminhsang989/opencode-live-monitor.git
cd opencode-live-monitor
pip install -r requirements.txt
```

### Cấu hình Telegram Bot
1. Tạo tệp cấu hình từ mẫu:
   ```bash
   copy config.example.json config.json
   ```
2. Mở `config.json` và điền thông tin bot của bạn:
   ```json
   {
     "telegram_enabled": true,
     "telegram_bot_token": "YOUR_BOT_TOKEN_FROM_BOTFATHER",
     "telegram_chat_id": "YOUR_TELEGRAM_USER_ID"
   }
   ```
   *(Bạn cũng có thể cấu hình trực tiếp từ giao diện nổi bằng cách bấm vào biểu tượng ✈️ trên thanh tiêu đề)*.

### Khởi chạy ứng dụng
- **Chạy có cửa sổ dòng lệnh (Debug):**
  ```bash
  python app.py
  ```
- **Chạy ngầm hoàn toàn (Không hiện cửa sổ đen):**
  - Nhấp đúp vào `start_monitor.vbs` hoặc lối tắt `Bat Giam Sat OpenCode.lnk` trên Desktop.
- **Tự khởi động cùng Windows:**
  ```bash
  python setup_autostart.py
  ```

---

## 📂 Cấu trúc thư mục

```
opencode-live-monitor/
├── app.py                     # Giao diện chính PySide6 (HUD floating window)
├── core_monitor.py            # Quét & phân tích phiên OpenCode từ SQLite nội bộ
├── opencode_client.py         # Kết nối Sidecar REST API qua bộ nhớ
├── cli.py                     # Công cụ dòng lệnh CLI điều khiển & TUI live watch
├── api_server.py              # REST API Gateway độc lập (FastAPI cổng 7080)
├── telegram_bot.py            # Xử lý tương tác 2 chiều với Telegram Bot API
├── telegram_notifier.py       # Module phụ trợ gửi thông báo Telegram
├── start_monitor.vbs          # Script khởi chạy ngầm chống mở trùng lặp
├── start_monitor.bat          # File batch khởi chạy nhanh
├── setup_autostart.py         # Cài đặt tự khởi động cùng Windows
├── config.example.json        # Mẫu tệp cấu hình
├── requirements.txt           # Danh sách thư viện Python phụ thuộc
└── tests/
    ├── test_telegram_bot.py   # Bộ kiểm thử Telegram Bot (PyTest)
    └── test_opencode_api.py   # Bộ kiểm thử REST API & CLI (PyTest)
```

---

## 🛡 Bảo mật & Quyền riêng tư
- Tệp `config.json` chứa Token Bot và Chat ID cá nhân được bỏ qua bởi `.gitignore`, không bao giờ được đẩy lên Git.
- Bot Telegram tích hợp cổng xác thực người dùng (`is_authorized`): chỉ Chat ID được cấu hình trong `config.json` mới có quyền thực thi lệnh và nhận dữ liệu.

---

## 📄 Bản quyền
Phát hành theo giấy phép [MIT](LICENSE).
