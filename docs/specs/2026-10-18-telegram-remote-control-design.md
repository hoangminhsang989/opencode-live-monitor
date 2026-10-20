# Thiết kế: Hệ thống Giám sát & Điều khiển OpenCode Từ Xa Qua Telegram Bot

## 1. Mục đích & Bối cảnh (Intent & Why)
Người dùng cần quản lý, giám sát và điều khiển nhiều phiên làm việc OpenCode trên máy tính để bàn (Windows) khi ở xa qua điện thoại thông qua Telegram Bot. Khi AI hoàn thành hoặc dừng lại hỏi câu hỏi duyệt (cần trả lời), người dùng có thể xem chi tiết, tích chọn đáp án (dạng checkbox/nút bấm) hoặc gõ trả lời trực tiếp trên Telegram; đồng thời có thể chụp ảnh màn hình máy tính từ xa và gửi lệnh điều khiển.

---

## 2. Mục tiêu (Goals) & Phạm vi tính năng
1. **Giám sát trạng thái từ xa (Live Dashboard)**:
   - Nhận diện đầy đủ các phiên OpenCode đang mở và trạng thái hiện tại (🟢 Đang chạy, 🟡 Cần trả lời, 🔴 Đã xong/Đã dừng).
   - Lệnh `/status` hiển thị bảng danh sách phiên kèm nút bấm `[ 🔄 Làm mới ]`.
2. **Duyệt câu hỏi & Tương tác 2 chiều (Question / Approval via Telegram)**:
   - Khi phiên gặp tool `question` (trạng thái 🟡):
     - Gửi thông báo kèm nội dung câu hỏi và danh sách các lựa chọn.
     - **Câu hỏi chọn nhiều (Checkbox)**: Hiển thị các nút `⬜ / ☑️`. Người dùng bấm để toggle trạng thái chọn, sau đó bấm `[ 🚀 Xác nhận & Gửi ]`.
     - **Câu hỏi chọn 1 (Radio)**: Mỗi lựa chọn là 1 nút bấm trực tiếp, bấm là duyệt gửi ngay.
     - **Nhập tự do**: Hỗ trợ người dùng gõ câu trả lời dạng text.
     - Nạp câu trả lời trở lại OpenCode để AI tiếp tục chạy mà không cần ngồi trước PC.
3. **Điều khiển phiên làm việc (Remote Session Control)**:
   - Danh sách phiên có nút bấm để chọn phiên cụ thể.
   - **Gửi lệnh mới (Prompt)**: Nhắn tin trực tiếp từ Telegram để đưa lệnh mới vào phiên đang chọn.
   - **Dừng khẩn cấp (`[ 🛑 Dừng phiên ]`)**: Bấm dừng phiên từ xa nếu cần.
   - **Xem kết quả (`[ 📜 Xem kết quả gần nhất ]`)**: Đọc nhanh nội dung AI vừa sinh ra.
4. **Chụp ảnh màn hình máy tính từ xa (Remote Screenshot)**:
   - Lệnh `/screen`: Chụp màn hình máy tính hiện tại (Pillow ImageGrab) và gửi trực tiếp hình ảnh về Telegram.
5. **Bảo mật tuyệt đối (Security)**:
   - Chỉ duy nhất `Chat ID` được cấu hình trong `config.json` mới có quyền truy cập, nhận tin nhắn và bấm nút điều khiển. Mọi ID khác đều bị từ chối và ghi log.
6. **Kiến trúc luồng ngầm (Background Worker)**:
   - Chạy một luồng nền riêng (Daemon Thread) xử lý Telegram Long Polling (`getUpdates`), không làm ảnh hưởng hoặc đóng băng giao diện nổi `PySide6`.

---

## 3. Ngoài phạm vi (Non-goals & Anti-goals)
- **Non-goal**: Không biến Telegram thành terminal đầy đủ (không stream từng ký tự stdout liên tục để tránh spam giới hạn rate limit của Telegram).
- **Anti-goal**: Không làm chậm ứng dụng nổi `OpenCodeLiveMonitor` hoặc xung đột đọc ghi cơ sở dữ liệu SQLite của OpenCode.
- **Anti-goal**: Không lưu trữ token hoặc dữ liệu ra ngoài máy cục bộ.

---

## 4. Các quyết định kỹ thuật đã thống nhất
- **Nền tảng**: Python 3.14 (sẵn có `requests`, `PySide6`, `psutil`, `Pillow`).
- **Cơ chế nạp câu trả lời vào OpenCode**:
  - Khi người dùng gửi câu trả lời từ Telegram: Cập nhật bản ghi `part` của tool `question` trong `opencode.db` (`state.status = 'completed'`, `state.output = ...`, `state.metadata = ...`), đồng thời gửi thông báo xác nhận thành công về Telegram.
- **Cấu hình**: Lưu tại `E:\giam sat ai\config.json` (`telegram_bot_token`, `telegram_chat_id`, `telegram_enabled`).

---

## 5. Dành cho lập trình viên (Left to Builder)
- Tối ưu hóa định dạng tin nhắn HTML/Markdown cho đẹp mắt, dễ đọc trên màn hình điện thoại.
- Cơ chế quản lý trạng thái phiên đang chọn để chat tiếp (session focus trên bot).
- Cơ chế gom cụm (debounce) tránh gửi trùng tin nhắn khi mạng chập chờn.
