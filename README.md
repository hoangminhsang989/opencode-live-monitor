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
- **Thao tác hoàn toàn chạy ngầm:** Gửi tin nhắn, gõ lệnh hoặc phê duyệt vào cửa sổ OpenCode bằng UI Automation, không chiếm chuột, không cướp tiêu điểm bàn phím.

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
├── telegram_bot.py            # Xử lý tương tác 2 chiều với Telegram Bot API
├── telegram_notifier.py       # Module phụ trợ gửi thông báo Telegram
├── start_monitor.vbs          # Script khởi chạy ngầm chống mở trùng lặp
├── start_monitor.bat          # File batch khởi chạy nhanh
├── setup_autostart.py         # Cài đặt tự khởi động cùng Windows
├── config.example.json        # Mẫu tệp cấu hình
├── requirements.txt           # Danh sách thư viện Python phụ thuộc
└── tests/
    └── test_telegram_bot.py   # Bộ kiểm thử tự động (PyTest)
```

---

## 🛡 Bảo mật & Quyền riêng tư
- Tệp `config.json` chứa Token Bot và Chat ID cá nhân được bỏ qua bởi `.gitignore`, không bao giờ được đẩy lên Git.
- Bot Telegram tích hợp cổng xác thực người dùng (`is_authorized`): chỉ Chat ID được cấu hình trong `config.json` mới có quyền thực thi lệnh và nhận dữ liệu.

---

## 📄 Bản quyền
Phát hành theo giấy phép [MIT](LICENSE).
