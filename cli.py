import os
import sys
import time
import argparse
from typing import Optional, List

if sys.stdout:
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

import core_monitor
from opencode_client import default_client

def cmd_status(args):
    """Hiển thị trạng thái các tab/phiên OpenCode đang mở."""
    sessions = core_monitor.poll_all_sessions()
    if not sessions:
        print("Không tìm thấy phiên OpenCode nào đang mở.")
        return

    print("=" * 95)
    print(f"{'STATUS':<10} | {'TIÊU ĐỀ PHIÊN':<30} | {'THƯ MỤC':<20} | {'CHI TIẾT'}")
    print("-" * 95)

    color_icons = {
        'green': '🟢 RUNNING',
        'yellow': '🟡 WAITING',
        'red': '🔴 STOPPED'
    }

    for s in sessions:
        st_icon = color_icons.get(s.get('color'), s.get('color', '').upper())
        title = (s.get('title') or s.get('sessionId') or '')[:30]
        directory = os.path.basename(s.get('directory') or '')[:20]
        detail = s.get('detail') or ''
        print(f"{st_icon:<10} | {title:<30} | {directory:<20} | {detail}")

    print("=" * 95)

    # Hiển thị thông tin chờ xác nhận nếu có
    perms = default_client.get_pending_permissions()
    if perms:
        print(f"\n⚠️  Có {len(perms)} yêu cầu cấp quyền đang chờ:")
        for p in perms:
            print(f"  - [{p.get('id')}] Quyền: {p.get('permission')} | Session: {p.get('sessionID')} | Mẫu: {p.get('patterns')}")

    questions = default_client.get_pending_questions()
    if questions:
        print(f"\n❓ Có {len(questions)} câu hỏi đang chờ trả lời:")
        for q in questions:
            for item in q.get('questions', []):
                print(f"  - Câu hỏi: {item.get('question')}")
                for idx, opt in enumerate(item.get('options', []), 1):
                    print(f"    [{idx}] {opt.get('label')}: {opt.get('description')}")

def cmd_send(args):
    """Gửi câu lệnh / prompt vào phiên chỉ định."""
    session_target = args.session
    prompt_text = args.prompt
    image_path = getattr(args, 'image', None)

    # Tìm session_id từ danh sách tab mở hoặc ID truyền vào
    session_id = None
    resolved_title = session_target
    for s in core_monitor.poll_all_sessions():
        s_id = s.get('sessionId', '')
        s_t = s.get('title', '')
        if session_target == s_id or (session_target.lower() in s_t.lower()):
            session_id = s_id
            resolved_title = s_t
            break

    if not session_id and session_target.startswith("ses_"):
        session_id = session_target

    if not session_id:
        print(f"❌ Không tìm thấy phiên nào khớp với: '{session_target}'")
        return 1

    print(f"🚀 Đang gửi lệnh đến phiên '{resolved_title}' ({session_id})...")
    ok, msg = core_monitor.send_prompt_to_opencode_session(
        session_title=resolved_title,
        prompt_text=prompt_text,
        image_path=image_path,
        session_id=session_id
    )
    if ok:
        print(f"✅ Thành công: {msg}")
        return 0
    else:
        print(f"❌ Thất bại: {msg}")
        return 1

def cmd_approve(args):
    """Phê duyệt hoặc từ chối cấp quyền."""
    action = args.action.lower()
    if action not in ("once", "always", "reject", "deny"):
        print("Hành động không hợp lệ. Chọn: once, always, reject")
        return 1
    if action == "deny":
        action = "reject"

    ok, msg = core_monitor.approve_permission_in_opencode(session_title=args.session or "", action=action)
    if ok:
        print(f"✅ {msg}")
        return 0
    else:
        print(f"❌ {msg}")
        return 1

def cmd_question(args):
    """Xem danh sách câu hỏi đang chờ."""
    questions = default_client.get_pending_questions()
    if not questions:
        print("Không có câu hỏi nào đang chờ.")
        return

    for q in questions:
        print(f"Question ID: {q.get('id')} (Session: {q.get('sessionID')})")
        for q_idx, item in enumerate(q.get('questions', []), 1):
            print(f"\n👉 Câu {q_idx}: {item.get('question')}")
            for opt_idx, opt in enumerate(item.get('options', []), 1):
                desc = f" - {opt.get('description')}" if opt.get('description') else ""
                print(f"   [{opt_idx}] {opt.get('label')}{desc}")

def cmd_answer(args):
    """Trả lời câu hỏi theo số thứ tự lựa chọn."""
    questions = default_client.get_pending_questions()
    if not questions:
        print("Không có câu hỏi nào đang chờ.")
        return 1

    target_q = questions[0]
    q_items = target_q.get('questions', [])
    if not q_items:
        print("Không tìm thấy nội dung câu hỏi.")
        return 1

    options = q_items[0].get('options', [])
    selected_indices = [int(x.strip()) for x in args.options.split(',') if x.strip().isdigit()]
    if not selected_indices:
        print("Lựa chọn không hợp lệ. Ví dụ: python cli.py answer 1 hoặc 1,2")
        return 1

    chosen_labels = []
    for idx in selected_indices:
        if 1 <= idx <= len(options):
            chosen_labels.append(options[idx - 1].get('label'))

    if not chosen_labels:
        print(f"Không tìm thấy lựa chọn nào tương ứng với số {selected_indices}")
        return 1

    print(f"Đang gửi lựa chọn: {chosen_labels}")
    ok, msg = default_client.reply_question(target_q.get('id'), [chosen_labels])
    if ok:
        print(f"✅ {msg}")
        return 0
    else:
        print(f"❌ {msg}")
        return 1

def cmd_abort(args):
    """Dừng phiên làm việc đang chạy."""
    session_target = args.session
    session_id = None
    for s in core_monitor.poll_all_sessions():
        s_id = s.get('sessionId', '')
        s_t = s.get('title', '')
        if session_target == s_id or (session_target.lower() in s_t.lower()):
            session_id = s_id
            break

    if not session_id and session_target.startswith("ses_"):
        session_id = session_target

    if not session_id:
        print(f"❌ Không tìm thấy phiên nào khớp với: '{session_target}'")
        return 1

    ok, msg = default_client.abort_session(session_id)
    if ok:
        print(f"✅ {msg}")
        return 0
    else:
        print(f"❌ {msg}")
        return 1

def cmd_log(args):
    """In câu trả lời gần nhất của AI trong phiên."""
    session_target = args.session
    session_id = None
    for s in core_monitor.poll_all_sessions():
        s_id = s.get('sessionId', '')
        s_t = s.get('title', '')
        if session_target == s_id or (session_target.lower() in s_t.lower()):
            session_id = s_id
            break

    if not session_id and session_target.startswith("ses_"):
        session_id = session_target

    if not session_id:
        print(f"❌ Không tìm thấy phiên nào khớp với: '{session_target}'")
        return 1

    resp = core_monitor.get_latest_assistant_response(session_id)
    if resp:
        print(f"=== CÂU TRẢ LỜI GẦN NHẤT CỦA AI ({session_id}) ===")
        print(resp)
    else:
        print(f"Chưa có câu trả lời nào từ AI trong phiên {session_id}.")

def cmd_new(args):
    """Mở một phiên làm việc mới."""
    title = args.title or ""
    directory = args.dir or ""
    ok, res = core_monitor.create_new_session_in_opencode(title=title, directory=directory)
    if ok:
        sid = res.get("id") if isinstance(res, dict) else ""
        print(f"✅ Đã mở phiên mới thành công! ID: {sid}")
        return 0
    else:
        print(f"❌ Không thể tạo phiên: {res}")
        return 1

def cmd_close(args):
    """Đóng một phiên làm việc."""
    session_target = args.session
    session_id = None
    resolved_title = session_target
    for s in core_monitor.poll_all_sessions():
        s_id = s.get('sessionId', '')
        s_t = s.get('title', '')
        if session_target == s_id or (session_target.lower() in s_t.lower()):
            session_id = s_id
            resolved_title = s_t
            break
    if not session_id and session_target.startswith("ses_"):
        session_id = session_target

    ok, msg = core_monitor.close_session_in_opencode(session_id=session_id, session_title=resolved_title)
    if ok:
        print(f"✅ {msg}")
        return 0
    else:
        print(f"❌ {msg}")
        return 1

def cmd_models(args):
    """Liệt kê các model AI đang kết nối và khả dụng."""
    models = default_client.get_available_models()
    if not models:
        print("Không tìm thấy model nào khả dụng hoặc chưa kết nối REST API.")
        return 1
    print(f"=== DANH SÁCH {len(models)} MODEL KHẢ DỤNG ===")
    current_p = None
    for m in models:
        pid = m.get("providerID")
        if pid != current_p:
            current_p = pid
            print(f"\n[{m.get('providerName')} - {pid}]")
        print(f"  • {m.get('modelID')} ({m.get('modelName')})")
    return 0

def cmd_set_model(args):
    """Đổi Model AI cho phiên chỉ định."""
    session_target = args.session
    session_id = None
    for s in core_monitor.poll_all_sessions():
        s_id = s.get('sessionId', '')
        s_t = s.get('title', '')
        if session_target == s_id or (session_target.lower() in s_t.lower()):
            session_id = s_id
            break
    if not session_id and session_target.startswith("ses_"):
        session_id = session_target

    if not session_id:
        print(f"❌ Không tìm thấy phiên '{session_target}'")
        return 1

    ok, msg = core_monitor.switch_session_model_in_opencode(session_id, args.provider, args.model)
    if ok:
        print(f"✅ {msg}")
        return 0
    else:
        print(f"❌ {msg}")
        return 1

def cmd_watch(args):
    """Giám sát thời gian thực dạng TUI terminal."""
    interval = max(0.5, float(args.interval))
    print(f"Bắt đầu giám sát thời gian thực (Cập nhật mỗi {interval}s, nhấn Ctrl+C để thoát)...")
    try:
        while True:
            os.system('cls' if os.name == 'nt' else 'clear')
            print(f"[OpenCode Monitor] {time.strftime('%Y-%m-%d %H:%M:%S')}")
            cmd_status(args)
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nĐã dừng giám sát.")

def main():
    parser = argparse.ArgumentParser(description="OpenCode CLI Remote Controller & Monitor")
    subparsers = parser.add_subparsers(dest="command", help="Lệnh thực thi")

    # status
    p_status = subparsers.add_parser("status", help="Xem trạng thái các tab/phiên đang mở")

    # send
    p_send = subparsers.add_parser("send", help="Gửi prompt vào phiên chỉ định")
    p_send.add_argument("session", help="Tên phiên hoặc session_id")
    p_send.add_argument("prompt", help="Nội dung câu lệnh hoặc prompt")
    p_send.add_argument("--image", help="Đường dẫn file ảnh đính kèm (tùy chọn)")

    # approve
    p_approve = subparsers.add_parser("approve", help="Phê duyệt hoặc từ chối cấp quyền")
    p_approve.add_argument("action", nargs="?", default="once", choices=["once", "always", "reject", "deny"], help="Hành động (once/always/reject)")
    p_approve.add_argument("--session", help="Tên phiên lọc (tùy chọn)")

    # question
    p_question = subparsers.add_parser("question", help="Xem danh sách câu hỏi đang chờ")

    # answer
    p_answer = subparsers.add_parser("answer", help="Trả lời câu hỏi theo số thứ tự (ví dụ: 1 hoặc 1,2)")
    p_answer.add_argument("options", help="Số thứ tự các lựa chọn cách nhau bởi dấu phẩy")

    # abort
    p_abort = subparsers.add_parser("abort", help="Dừng lượt của phiên đang chạy")
    p_abort.add_argument("session", help="Tên phiên hoặc session_id")

    # log
    p_log = subparsers.add_parser("log", help="Xem phản hồi gần nhất của AI")
    p_log.add_argument("session", help="Tên phiên hoặc session_id")

    # new
    p_new = subparsers.add_parser("new", help="Tạo / mở một phiên làm việc mới")
    p_new.add_argument("--title", help="Tiêu đề phiên (tùy chọn)")
    p_new.add_argument("--dir", help="Thư mục làm việc (tùy chọn)")

    # close
    p_close = subparsers.add_parser("close", help="Đóng một phiên làm việc")
    p_close.add_argument("session", help="Tên phiên hoặc session_id")

    # models
    p_models = subparsers.add_parser("models", help="Xem danh sách model AI khả dụng")

    # set-model
    p_set_model = subparsers.add_parser("set-model", help="Đổi Model AI cho một phiên")
    p_set_model.add_argument("session", help="Tên phiên hoặc session_id")
    p_set_model.add_argument("provider", help="ID Provider (ví dụ: 9router, jerouter, darkapi)")
    p_set_model.add_argument("model", help="ID Model (ví dụ: ag/gemini-3.8-flash-high, step-5-unrestricted)")

    # watch
    p_watch = subparsers.add_parser("watch", help="Bảng điều khiển trực tiếp làm mới liên tục")
    p_watch.add_argument("--interval", "-i", default=1.5, type=float, help="Chu kỳ làm mới (giây)")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    commands = {
        'status': cmd_status,
        'send': cmd_send,
        'approve': cmd_approve,
        'question': cmd_question,
        'answer': cmd_answer,
        'abort': cmd_abort,
        'log': cmd_log,
        'new': cmd_new,
        'close': cmd_close,
        'models': cmd_models,
        'set-model': cmd_set_model,
        'watch': cmd_watch
    }

    cmd_fn = commands.get(args.command)
    if cmd_fn:
        sys.exit(cmd_fn(args) or 0)

if __name__ == '__main__':
    main()
