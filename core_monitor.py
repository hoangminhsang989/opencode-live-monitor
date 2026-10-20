import os
import sys
import glob
import json
import time
import io
import sqlite3
import threading
import psutil
from typing import List, Dict, Any, Optional
import win32gui
import win32con
import win32clipboard
import win32process
import win32api
from pywinauto import Application
import pyperclip
from PIL import Image

if sys.stdout:
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

APPDATA = os.environ.get('APPDATA', os.path.expanduser('~\\AppData\\Roaming'))
LOCALAPPDATA = os.environ.get('LOCALAPPDATA', os.path.expanduser('~\\AppData\\Local'))
USERPROFILE = os.environ.get('USERPROFILE', os.path.expanduser('~'))

DB_PATH = os.path.join(USERPROFILE, '.local', 'share', 'opencode', 'opencode.db').replace('\\', '/')
WINDOW_DIR = os.path.join(APPDATA, 'ai.opencode.desktop').replace('\\', '/')

def is_opencode_permission_prompt_showing() -> bool:
    """Kiểm tra xem OpenCode có đang hiển thị hộp thoại xin cấp quyền (Cho phép một lần/Cho phép luôn) hay không."""
    # 0. Ưu tiên kiểm tra qua REST API (ngầm 100%, không cần mở cửa sổ)
    try:
        from opencode_client import default_client
        if default_client.connect():
            perms = default_client.get_pending_permissions()
            if perms:
                return True
    except Exception:
        pass

    try:
        hwnd = win32gui.FindWindow(None, "OpenCode")
        if not hwnd or not win32gui.IsWindowVisible(hwnd):
            return False
        app = Application(backend="uia").connect(handle=hwnd, timeout=0.1)
        win = app.window(handle=hwnd)
        buttons = win.descendants(control_type="Button")
        target_keywords = ("cho phép một lần", "cho phép luôn", "allow once", "allow always")
        for b in buttons:
            txt = b.window_text().strip().lower()
            if any(kw in txt for kw in target_keywords):
                return True
    except Exception:
        pass
    return False

def get_active_opencode_tab_session_id() -> Optional[str]:
    """Lấy sessionId của tab đang mở hiện tại trên cửa sổ OpenCode."""
    pattern = os.path.join(WINDOW_DIR, 'opencode.window.*.dat').replace('\\', '/')
    window_files = glob.glob(pattern)
    if not window_files:
        return None
    window_files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    try:
        with open(window_files[0], 'r', encoding='utf-8') as f:
            data = json.load(f)
        recent_raw = data.get('tabs.recent', '{}')
        recent = json.loads(recent_raw) if isinstance(recent_raw, str) else recent_raw
        key = recent.get('key', '')
        if 'session/' in key:
            return key.split('session/')[-1].strip()
    except Exception:
        pass
    return None

def get_active_process_cmdlines() -> List[str]:
    cmdlines = []
    for p in psutil.process_iter(['name', 'cmdline']):
        try:
            name = (p.info.get('name') or '').lower()
            if name in ('powershell.exe', 'cmd.exe', 'python.exe', 'node.exe'):
                cmdline = ' '.join(p.info.get('cmdline') or [])
                cmdlines.append(cmdline.lower())
        except Exception:
            pass
    return cmdlines

def get_open_sessions() -> List[Dict[str, Any]]:
    pattern = os.path.join(WINDOW_DIR, 'opencode.window.*.dat').replace('\\', '/')
    window_files = glob.glob(pattern)
    if not window_files:
        return []
    
    # Sort by mtime descending to get the most recent window file
    window_files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    latest_file = window_files[0]
    
    try:
        with open(latest_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        return []

    tabs_raw = data.get('tabs', '[]')
    try:
        tabs = json.loads(tabs_raw) if isinstance(tabs_raw, str) else tabs_raw
    except Exception:
        tabs = []

    tabs_info_raw = data.get('tabs.info', '{}')
    try:
        tabs_info = json.loads(tabs_info_raw) if isinstance(tabs_info_raw, str) else tabs_info_raw
    except Exception:
        tabs_info = {}

    open_list = []
    for tab in tabs:
        if not isinstance(tab, dict) or tab.get('type') != 'session':
            continue
        session_id = tab.get('sessionId')
        if not session_id:
            continue
        
        info = None
        for k, v in tabs_info.items():
            if session_id in k:
                info = v
                break
        
        open_list.append({
            'sessionId': session_id,
            'title': info.get('title') if info else None,
            'directory': info.get('directory') if info else None
        })
    return open_list

def get_session_status(conn: sqlite3.Connection, session_id: str, active_cmdlines: List[str] = None, is_perm_showing: bool = False, active_tab_sid: str = None) -> Dict[str, Any]:
    c = conn.cursor()
    now_ms = int(time.time() * 1000)
    
    # 1. Lấy tiêu đề và thư mục từ DB
    c.execute("SELECT title, directory FROM session WHERE id = ?", (session_id,))
    s_row = c.fetchone()
    db_title = s_row[0] if s_row else session_id
    db_dir = s_row[1] if s_row else ''
    
    # 2. Lấy tin nhắn mới nhất
    c.execute("SELECT id, time_created, time_updated, data FROM message WHERE session_id = ? ORDER BY time_created DESC LIMIT 1", (session_id,))
    last_msg = c.fetchone()
    if not last_msg:
        return {
            'state': 'completed',
            'color': 'red',
            'label': 'Đã xong',
            'detail': 'Chưa có tin nhắn',
            'title': db_title,
            'directory': db_dir
        }
    
    msg_id, msg_tc, msg_tu, msg_data_raw = last_msg
    try:
        msg_data = json.loads(msg_data_raw)
    except Exception:
        msg_data = {}
    
    role = msg_data.get('role')
    
    # Nếu tin nhắn mới nhất là của user -> AI đang tiếp nhận xử lý -> XANH (Đang chạy)
    if role == 'user':
        return {
            'state': 'running',
            'color': 'green',
            'label': 'Đang chạy',
            'detail': 'Đang tiếp nhận lệnh...',
            'title': db_title,
            'directory': db_dir
        }
    
    # Kiểm tra các part của tin nhắn mới nhất
    c.execute("SELECT data, time_created FROM part WHERE message_id = ? ORDER BY time_created DESC", (msg_id,))
    parts = c.fetchall()
    
    running_question = False
    active_tool_name = ''
    active_tool_status = ''
    
    for p_row in parts:
        try:
            p_data = json.loads(p_row[0])
            p_type = p_data.get('type')
            state = p_data.get('state', {})
            status = state.get('status')
            tool_name = p_data.get('tool', '')
            
            if p_type == 'tool':
                if tool_name == 'question' and status == 'running':
                    running_question = True
                    break
                if status in ('running', 'pending'):
                    active_tool_name = tool_name
                    active_tool_status = status
                    break
        except Exception:
            pass

    # 1. 🟡 VÀNG: CHỈ KHI NGƯỜI DÙNG BẮT BUỘC PHẢI XÁC NHẬN MỚI CHẠY TIẾP
    # 1a. AI hỏi câu hỏi qua tool 'question' (chờ người dùng trả lời)
    if running_question:
        return {
            'state': 'waiting_user',
            'color': 'yellow',
            'label': 'Cần trả lời',
            'detail': 'Chờ bạn trả lời câu hỏi',
            'title': db_title,
            'directory': db_dir
        }

    # 1b. Hộp thoại xin cấp quyền đang thực sự hiển thị trên màn hình OpenCode chờ người dùng bấm Allow/Deny
    if is_perm_showing and (active_tab_sid == session_id or not active_tab_sid):
        return {
            'state': 'waiting_user',
            'color': 'yellow',
            'label': 'Cần cấp quyền',
            'detail': 'Chờ bạn xác nhận cấp quyền',
            'title': db_title,
            'directory': db_dir
        }
    
    finish_reason = msg_data.get('finish')
    completed_time = msg_data.get('time', {}).get('completed')
    has_error = msg_data.get('error') is not None
    
    # 2. 🟢 XANH: Đang gọi tool trung gian (finish == 'tool-calls') -> CHẮC CHẮN ĐANG CHẠY
    if finish_reason == 'tool-calls':
        detail = f'Thực thi tool: {active_tool_name}' if active_tool_name else 'Đang chạy tool...'
        return {
            'state': 'running',
            'color': 'green',
            'label': 'Đang chạy',
            'detail': detail,
            'title': db_title,
            'directory': db_dir
        }

    # 3. 🔴 ĐỎ: PHIÊN ĐÃ DỪNG HẲN / HOÀN THÀNH
    # Khi AI đã kết thúc toàn bộ lượt phản hồi (finish là stop, length, content-filter)
    # HOẶC người dùng đã bấm dừng (OpenCode tự gắn error hoặc completed_time trên tin nhắn)
    if (finish_reason in ('stop', 'length', 'content-filter')) or has_error or (completed_time is not None and finish_reason != 'tool-calls'):
        return {
            'state': 'completed',
            'color': 'red',
            'label': 'Đã xong',
            'detail': 'Hoàn thành / Đã dừng',
            'title': db_title,
            'directory': db_dir
        }
    
    # 4. 🟢 XANH: ĐANG CHẠY (finish is None, completed is None, error is None)
    # Tuyệt đối không tự ý ngắt sang Đỏ khi AI đang xử lý/suy nghĩ/soạn lệnh
    detail = 'Đang xử lý...'
    if active_tool_name:
        detail = f'Thực thi tool: {active_tool_name}' if active_tool_status == 'running' else f'Chuẩn bị tool: {active_tool_name}'
    elif parts:
        try:
            latest_p = json.loads(parts[0][0])
            lp_type = latest_p.get('type')
            if lp_type == 'reasoning':
                detail = 'Đang suy nghĩ (reasoning)'
            elif lp_type == 'text':
                detail = 'Đang sinh câu trả lời'
        except Exception:
            pass
    
    return {
        'state': 'running',
        'color': 'green',
        'label': 'Đang chạy',
        'detail': detail,
        'title': db_title,
        'directory': db_dir
    }

def poll_all_sessions():
    open_tabs = get_open_sessions()
    if not os.path.exists(DB_PATH):
        print(f"Database not found at {DB_PATH}")
        return []
    
    is_perm_showing = is_opencode_permission_prompt_showing()
    active_tab_sid = get_active_opencode_tab_session_id()
    
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    results = []
    try:
        for tab in open_tabs:
            sid = tab['sessionId']
            res = get_session_status(conn, sid, None, is_perm_showing, active_tab_sid)
            if tab.get('title'):
                res['title'] = tab['title']
            if tab.get('directory'):
                res['directory'] = tab['directory']
            res['sessionId'] = sid
            results.append(res)
    finally:
        conn.close()
    return results

def get_latest_assistant_response(session_id: str) -> Optional[str]:
    """Lấy câu trả lời gần nhất của AI trong phiên chỉ định."""
    if not os.path.exists(DB_PATH):
        return None
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    c = conn.cursor()
    try:
        c.execute("""
            SELECT m.id 
            FROM message m
            JOIN part p ON p.message_id = m.id
            WHERE m.session_id = ?
              AND json_extract(m.data, '$.role') = 'assistant'
              AND json_extract(p.data, '$.type') = 'text'
              AND length(coalesce(json_extract(p.data, '$.text'), '')) > 0
            ORDER BY m.time_created DESC LIMIT 1
        """, (session_id,))
        row = c.fetchone()
        if not row:
            return None
        msg_id = row[0]
        
        c.execute("""
            SELECT json_extract(data, '$.text') 
            FROM part 
            WHERE message_id = ? 
              AND json_extract(data, '$.type') = 'text'
            ORDER BY time_created ASC
        """, (msg_id,))
        texts = [r[0] for r in c.fetchall() if r[0]]
        return '\n'.join(texts).strip()
    finally:
        conn.close()

def send_image_to_clipboard_win32(image_path: str) -> bool:
    """Nạp file ảnh vào Windows Clipboard với cả CF_DIB và PNG chuẩn Electron/Chromium."""
    try:
        image = Image.open(image_path)
        
        # 1. CF_DIB (chuẩn bitmap Windows)
        output_bmp = io.BytesIO()
        image.convert("RGB").save(output_bmp, "BMP")
        dib_data = output_bmp.getvalue()[14:]  # Cắt bỏ 14 bytes header BMP để lấy DIB
        output_bmp.close()
        
        # 2. PNG (chuẩn Electron/Chrome HTML5 paste event)
        output_png = io.BytesIO()
        image.save(output_png, "PNG")
        png_data = output_png.getvalue()
        output_png.close()
        
        png_fmt = win32clipboard.RegisterClipboardFormat("PNG")
        
        # Thử mở Clipboard với retry
        opened = False
        for _ in range(5):
            try:
                win32clipboard.OpenClipboard()
                opened = True
                break
            except Exception:
                time.sleep(0.05)
        
        if not opened:
            return False
            
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32clipboard.CF_DIB, dib_data)
            win32clipboard.SetClipboardData(png_fmt, png_data)
            return True
        finally:
            try:
                win32clipboard.CloseClipboard()
            except Exception:
                pass
    except Exception as e:
        print(f"[send_image_to_clipboard_win32] Error: {e}")
        return False

def force_foreground_window(hwnd: int) -> bool:
    """Đưa cửa sổ lên phía trước một cách chắc chắn, vượt qua Foreground Stealing Lock của Windows 11."""
    try:
        fore_hwnd = win32gui.GetForegroundWindow()
        if fore_hwnd == hwnd:
            return True
        fore_thread, _ = win32process.GetWindowThreadProcessId(fore_hwnd)
        curr_thread = win32api.GetCurrentThreadId()
        
        attached = False
        if fore_thread != curr_thread:
            attached = win32process.AttachThreadInput(curr_thread, fore_thread, True)
            
        win32gui.BringWindowToTop(hwnd)
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        else:
            win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
            
        win32gui.SetForegroundWindow(hwnd)
        
        if attached:
            win32process.AttachThreadInput(curr_thread, fore_thread, False)
        return True
    except Exception:
        try:
            win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
            win32gui.SetForegroundWindow(hwnd)
            win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
            return True
        except Exception:
            return False

def send_paste_keys():
    """Gửi tổ hợp phím Ctrl + V trực tiếp bằng Win32 API (không bị chặn bới pywinauto)."""
    win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
    win32api.keybd_event(ord('V'), 0, 0, 0)
    time.sleep(0.05)
    win32api.keybd_event(ord('V'), 0, win32con.KEYEVENTF_KEYUP, 0)
    win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)

def send_enter_key():
    """Gửi phím Enter trực tiếp bằng Win32 API."""
    win32api.keybd_event(win32con.VK_RETURN, 0, 0, 0)
    time.sleep(0.05)
    win32api.keybd_event(win32con.VK_RETURN, 0, win32con.KEYEVENTF_KEYUP, 0)

_ui_action_lock = threading.RLock()

def get_latest_message_id(session_id: str) -> Optional[str]:
    """Lấy message ID mới nhất trong database cho phiên chỉ định."""
    if not os.path.exists(DB_PATH):
        return None
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        c = conn.cursor()
        c.execute("SELECT id FROM message WHERE session_id = ? ORDER BY time_created DESC LIMIT 1", (session_id,))
        row = c.fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None

def verify_message_submitted(session_id: str, before_msg_id: Optional[str], timeout_sec: float = 3.0, before_time: Optional[int] = None) -> tuple:
    """Kiểm tra trong SQLite xem phiên đã thực sự nhận tin nhắn user mới hay chưa."""
    if not os.path.exists(DB_PATH):
        return False, None
    if before_time is None:
        before_time = int(time.time() * 1000) - 2000
    start = time.time()
    while time.time() - start < timeout_sec:
        time.sleep(0.25)
        try:
            conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
            c = conn.cursor()
            # Lọc tin nhắn có role = 'user' tạo sau before_time hoặc có id khác before_msg_id
            c.execute("""
                SELECT id, time_created FROM message 
                WHERE session_id = ? 
                  AND json_extract(data, '$.role') = 'user'
                  AND time_created >= ?
                ORDER BY time_created DESC LIMIT 1
            """, (session_id, before_time))
            row = c.fetchone()
            conn.close()
            if row and row[0] != before_msg_id:
                return True, row[0]
        except Exception:
            pass
    return False, None

def send_prompt_to_opencode_session(session_title: str, prompt_text: str = "", image_path: str = None, session_id: str = None) -> tuple:
    """Gửi câu lệnh hoặc đính kèm ảnh vào phiên OpenCode với cơ chế xác minh 2 chiều qua database."""
    # Tự động phân giải session_id nếu chưa được truyền vào
    if not session_id and session_title:
        try:
            for s in poll_all_sessions():
                s_t = s.get('title', '')
                if s_t and (session_title.lower() in s_t.lower() or s_t.lower() in session_title.lower()):
                    session_id = s.get('sessionId')
                    break
        except Exception:
            pass

    # 1. ƯU TIÊN GỬI QUA REST API TRỰC TIẾP (HOÀN TOÀN NGẦM, KHÔNG CHẠM VÀO MÀN HÌNH/CHUỘT/PHÍM)
    if session_id:
        try:
            from opencode_client import default_client
            if default_client.connect():
                before_msg_id = get_latest_message_id(session_id)
                before_time = int(time.time() * 1000) - 1000
                api_ok, api_msg = default_client.send_prompt(session_id, prompt_text=prompt_text, image_path=image_path)
                if api_ok:
                    submitted, _ = verify_message_submitted(session_id, before_msg_id, timeout_sec=3.0, before_time=before_time)
                    if submitted:
                        return True, "Gửi thành công qua REST API (hoàn toàn ngầm, đã xác nhận database)."
                    # OpenCode REST API đã tiếp nhận (HTTP 200/204). Lệnh đã nằm trong hàng đợi xử lý.
                    # TUYỆT ĐỐI KHÔNG fallback sang GUI để tránh gửi đúp 2 lần!
                    return True, "Gửi thành công qua REST API (đã tiếp nhận vào hàng đợi OpenCode)."
                print(f"[send_prompt_to_opencode_session] REST API gửi thất bại ({api_msg}), fallback GUI...")
        except Exception as e:
            print(f"[send_prompt_to_opencode_session] REST API error: {e}, fallback GUI...")

    with _ui_action_lock:
        hwnd = win32gui.FindWindow(None, "OpenCode")
        if not hwnd:
            return False, "Không tìm thấy cửa sổ OpenCode đang chạy."

        before_msg_id = get_latest_message_id(session_id) if session_id else None
        before_time = int(time.time() * 1000) - 1000
        prev_hwnd = win32gui.GetForegroundWindow()
        try:
            # Nếu OpenCode đang bị thu nhỏ (minimize), khôi phục lại
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                time.sleep(0.1)

            # 1. Đưa OpenCode lên Foreground một cách chắc chắn trước khi thao tác
            force_foreground_window(hwnd)
            time.sleep(0.15)

            app = Application(backend="uia").connect(handle=hwnd, timeout=2.0)
            win = app.window(handle=hwnd)

            # 2. Chuyển đúng tab phiên
            active_sid = get_active_opencode_tab_session_id()
            already_active = bool(session_id and active_sid == session_id)

            if not already_active:
                target_tab = None
                # Tìm theo tiêu đề phiên
                for h in win.descendants(control_type="Hyperlink"):
                    txt = h.window_text().strip()
                    if "đóng tab" in txt.lower():
                        continue
                    if session_title.lower() in txt.lower() or txt.lower() in session_title.lower():
                        target_tab = h
                        break

                # Nếu tiêu đề bị lệch (vd worknc vs HMS-CAM), tìm theo vị trí index tab
                if not target_tab and session_id:
                    try:
                        open_sessions = get_open_sessions()
                        for idx, osess in enumerate(open_sessions):
                            if osess.get('sessionId') == session_id:
                                tab_links = [hl for hl in win.descendants(control_type="Hyperlink") if "đóng tab" not in hl.window_text().lower()]
                                if idx < len(tab_links):
                                    target_tab = tab_links[idx]
                                break
                    except Exception:
                        pass

                if target_tab:
                    try:
                        target_tab.invoke()
                        time.sleep(0.3)
                    except Exception:
                        pass

                    # Kiểm tra xem tab đã chuyển thật chưa, nếu invoke không ăn thì click trực tiếp
                    new_active = get_active_opencode_tab_session_id()
                    if session_id and new_active != session_id:
                        try:
                            target_tab.click_input()
                            time.sleep(0.35)
                        except Exception:
                            pass

            # 3. Tìm và focus ô nhập prompt (Edit) qua UIA
            edits = win.descendants(control_type="Edit")
            if not edits:
                if prev_hwnd and prev_hwnd != hwnd:
                    force_foreground_window(prev_hwnd)
                return False, "Không tìm thấy ô nhập lệnh trong cửa sổ OpenCode."

            edit = edits[0]
            try:
                edit.set_focus()
                time.sleep(0.1)
            except Exception:
                pass

            has_content = False

            # 4. Nếu có ảnh: nạp DIB + PNG vào Clipboard rồi Paste
            if image_path and os.path.exists(image_path):
                if not send_image_to_clipboard_win32(image_path):
                    if prev_hwnd and prev_hwnd != hwnd:
                        force_foreground_window(prev_hwnd)
                    return False, "Không thể nạp tệp ảnh vào Clipboard."
                send_paste_keys()
                has_content = True
                time.sleep(0.7)  # Chờ OpenCode gắn thumbnail ảnh xong

            # 5. Nếu có văn bản: nạp text vào Clipboard rồi Paste
            if prompt_text and prompt_text.strip():
                pyperclip.copy(prompt_text)
                time.sleep(0.05)
                send_paste_keys()
                has_content = True
                time.sleep(0.3)

            if not has_content:
                if prev_hwnd and prev_hwnd != hwnd:
                    force_foreground_window(prev_hwnd)
                return False, "Không có nội dung để gửi."

            # 6. Gửi lệnh và xác minh 2 chiều qua database
            send_enter_key()
            time.sleep(0.3)

            submitted = False
            if session_id:
                submitted, _ = verify_message_submitted(session_id, before_msg_id, timeout_sec=2.0, before_time=before_time)

            # Nếu Enter chưa submit được, thử bấm nút 'Gửi'
            if not submitted:
                for b in win.descendants(control_type="Button"):
                    if b.window_text().strip() == "Gửi" and b.is_enabled():
                        try:
                            b.click_input()
                            time.sleep(0.3)
                        except Exception:
                            send_enter_key()
                        break
                if session_id:
                    submitted, _ = verify_message_submitted(session_id, before_msg_id, timeout_sec=2.0, before_time=before_time)

            # Nếu vẫn chưa submit, thử gửi Ctrl+Enter
            if not submitted and session_id:
                win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
                win32api.keybd_event(win32con.VK_RETURN, 0, 0, 0)
                time.sleep(0.05)
                win32api.keybd_event(win32con.VK_RETURN, 0, win32con.KEYEVENTF_KEYUP, 0)
                win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
                submitted, _ = verify_message_submitted(session_id, before_msg_id, timeout_sec=2.0, before_time=before_time)

            # 7. Trả lại cửa sổ làm việc cũ của người dùng một cách chắc chắn
            if prev_hwnd and prev_hwnd != hwnd and win32gui.IsWindow(prev_hwnd):
                force_foreground_window(prev_hwnd)

            if session_id and not submitted:
                return False, "OpenCode không phản hồi lệnh gửi (chưa tiếp nhận tin nhắn vào hàng đợi)."

            return True, "Thành công"
        except Exception as e:
            if prev_hwnd and prev_hwnd != hwnd and win32gui.IsWindow(prev_hwnd):
                try:
                    force_foreground_window(prev_hwnd)
                except Exception:
                    pass
            return False, str(e)

def get_active_question_for_session(session_id: str) -> Optional[dict]:
    """Lấy dữ liệu câu hỏi đang chờ trả lời từ tool question trong database."""
    if not os.path.exists(DB_PATH):
        return None
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    c = conn.cursor()
    try:
        c.execute("""
            SELECT p.data 
            FROM part p
            WHERE p.session_id = ?
              AND json_extract(p.data, '$.tool') = 'question'
              AND json_extract(p.data, '$.state.status') = 'running'
            ORDER BY p.time_created DESC LIMIT 1
        """, (session_id,))
        row = c.fetchone()
        if not row:
            return None
        data = json.loads(row[0])
        return data.get('state', {}).get('input', {})
    finally:
        conn.close()

def approve_permission_in_opencode(session_title: str, action: str = "once", session_id: str = None) -> tuple:
    """Tự động phê duyệt quyền hoàn toàn ngầm không di chuyển chuột và không chiếm màn hình."""
    # 0. Ưu tiên qua REST API
    try:
        from opencode_client import default_client
        if default_client.connect():
            perms = default_client.get_pending_permissions()
            if perms:
                target_req = perms[0]
                target_sid = session_id
                if not target_sid and session_title:
                    for s in poll_all_sessions():
                        s_t = s.get('title', '')
                        if s_t and (session_title.lower() in s_t.lower() or s_t.lower() in session_title.lower()):
                            target_sid = s.get('sessionId')
                            break
                if target_sid:
                    for p in perms:
                        if p.get('sessionID') == target_sid:
                            target_req = p
                            break

                reply_val = "once" if action == "once" else ("always" if action == "always" else "reject")
                ok, msg = default_client.reply_permission(target_req['id'], reply=reply_val)
                if ok:
                    return True, f"Đã phản hồi quyền ({action}) thành công qua REST API."
    except Exception as e:
        print(f"[approve_permission_in_opencode] REST API error: {e}")

    with _ui_action_lock:
        hwnd = win32gui.FindWindow(None, "OpenCode")
        if not hwnd:
            return False, "Không tìm thấy cửa sổ OpenCode đang chạy."

        try:
            app = Application(backend="uia").connect(handle=hwnd, timeout=1.0)
            win = app.window(handle=hwnd)

            # 1. Chuyển sang đúng tab phiên bằng invoke (ngầm)
            if session_title:
                for h in win.descendants(control_type="Hyperlink"):
                    txt = h.window_text()
                    if session_title.lower() in txt.lower() and "đóng tab" not in txt.lower():
                        try:
                            h.invoke()
                            time.sleep(0.05)
                        except Exception:
                            pass
                        break

            # 2. Xác định từ khóa nút bấm tương ứng
            if action == "once":
                keywords = ("cho phép một lần", "allow once")
            elif action == "always":
                keywords = ("cho phép luôn", "allow always")
            else: # deny
                keywords = ("từ chối", "deny", "reject")

            # 3. Tìm nút bấm và gọi invoke trực tiếp (ngầm 100%, KHÔNG dùng chuột)
            buttons = win.descendants(control_type="Button")
            target_btn = None
            for b in buttons:
                txt = b.window_text().strip().lower()
                if any(kw in txt for kw in keywords):
                    target_btn = b
                    break

            if not target_btn:
                for c in win.descendants():
                    txt = c.window_text().strip().lower()
                    if any(kw in txt for kw in keywords):
                        target_btn = c
                        break

            if not target_btn:
                return False, f"Không tìm thấy nút bấm tương ứng với hành động '{action}' trên màn hình OpenCode."

            try:
                target_btn.invoke()
            except Exception:
                try:
                    target_btn.click_input()
                except Exception as e:
                    return False, str(e)

            return True, "Đã phê duyệt thành công"
        except Exception as e:
            return False, str(e)

def select_question_option_in_opencode(session_title: str, option_labels: list, session_id: str = None) -> tuple:
    """Tự động tích/chọn đáp án của câu hỏi hoàn toàn ngầm không di chuyển chuột."""
    # 0. Ưu tiên qua REST API
    try:
        from opencode_client import default_client
        if default_client.connect():
            questions = default_client.get_pending_questions()
            if questions:
                target_q = questions[0]
                target_sid = session_id
                if not target_sid and session_title:
                    for s in poll_all_sessions():
                        s_t = s.get('title', '')
                        if s_t and (session_title.lower() in s_t.lower() or s_t.lower() in session_title.lower()):
                            target_sid = s.get('sessionId')
                            break
                if target_sid:
                    for q in questions:
                        if q.get('sessionID') == target_sid:
                            target_q = q
                            break

                ok, msg = default_client.reply_question(target_q['id'], [option_labels])
                if ok:
                    return True, "Đã gửi câu trả lời thành công qua REST API."
    except Exception as e:
        print(f"[select_question_option_in_opencode] REST API error: {e}")

    with _ui_action_lock:
        hwnd = win32gui.FindWindow(None, "OpenCode")
        if not hwnd:
            return False, "Không tìm thấy cửa sổ OpenCode đang chạy."

        prev_hwnd = win32gui.GetForegroundWindow()
        try:
            app = Application(backend="uia").connect(handle=hwnd, timeout=1.0)
            win = app.window(handle=hwnd)

            # 1. Chuyển sang đúng tab phiên bằng invoke
            if session_title:
                for h in win.descendants(control_type="Hyperlink"):
                    txt = h.window_text()
                    if session_title.lower() in txt.lower() and "đóng tab" not in txt.lower():
                        try:
                            h.invoke()
                            time.sleep(0.05)
                        except Exception:
                            pass
                        break

            # 2. Chọn từng lựa chọn bằng invoke (ngầm)
            for label in option_labels:
                if not label:
                    continue
                clean_label = label.strip().lower()
                found_opt = None
                for d in win.descendants():
                    txt = d.window_text().strip().lower()
                    if clean_label in txt or txt in clean_label:
                        found_opt = d
                        break

                if found_opt:
                    try:
                        found_opt.invoke()
                    except Exception:
                        try:
                            found_opt.click_input()
                        except Exception:
                            pass
                    time.sleep(0.05)

            # 3. Gửi câu trả lời
            submit_btn = None
            for b in win.descendants(control_type="Button"):
                txt = b.window_text().strip().lower()
                if txt in ("gửi", "submit", "tiếp theo", "next"):
                    submit_btn = b
                    break

            if submit_btn:
                try:
                    submit_btn.invoke()
                except Exception:
                    try:
                        submit_btn.click_input()
                    except Exception:
                        pass
            else:
                # Gửi phím Ctrl+Enter trong tích tắc rồi trả lại cửa sổ cũ
                needs_restore = (prev_hwnd and prev_hwnd != hwnd)
                try:
                    win32gui.SetForegroundWindow(hwnd)
                except Exception:
                    pass
                win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
                win32api.keybd_event(win32con.VK_RETURN, 0, 0, 0)
                time.sleep(0.05)
                win32api.keybd_event(win32con.VK_RETURN, 0, win32con.KEYEVENTF_KEYUP, 0)
                win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
                if needs_restore:
                    try:
                        win32gui.SetForegroundWindow(prev_hwnd)
                    except Exception:
                        pass

            return True, "Đã gửi lựa chọn thành công"
        except Exception as e:
            return False, str(e)

def dismiss_question_in_opencode(session_title: str, session_id: str = None) -> tuple:
    """Bấm nút 'Bỏ qua' hoặc gửi phím Escape trong OpenCode để bỏ qua câu hỏi."""
    # 0. Ưu tiên qua REST API
    try:
        from opencode_client import default_client
        if default_client.connect():
            questions = default_client.get_pending_questions()
            if questions:
                target_q = questions[0]
                target_sid = session_id
                if not target_sid and session_title:
                    for s in poll_all_sessions():
                        s_t = s.get('title', '')
                        if s_t and (session_title.lower() in s_t.lower() or s_t.lower() in session_title.lower()):
                            target_sid = s.get('sessionId')
                            break
                if target_sid:
                    for q in questions:
                        if q.get('sessionID') == target_sid:
                            target_q = q
                            break

                ok, msg = default_client.reject_question(target_q['id'])
                if ok:
                    return True, "Đã bỏ qua câu hỏi thành công qua REST API."
    except Exception as e:
        print(f"[dismiss_question_in_opencode] REST API error: {e}")

    with _ui_action_lock:
        hwnd = win32gui.FindWindow(None, "OpenCode")
        if not hwnd:
            return False, "Không tìm thấy cửa sổ OpenCode đang chạy."

        prev_hwnd = win32gui.GetForegroundWindow()
        try:
            app = Application(backend="uia").connect(handle=hwnd, timeout=1.0)
            win = app.window(handle=hwnd)

            # 1. Chuyển sang đúng tab phiên
            if session_title:
                for h in win.descendants(control_type="Hyperlink"):
                    txt = h.window_text()
                    if session_title.lower() in txt.lower() and "đóng tab" not in txt.lower():
                        try:
                            h.invoke()
                            time.sleep(0.05)
                        except Exception:
                            pass
                        break

            # 2. Tìm nút 'Bỏ qua' / 'Dismiss'
            dismiss_btn = None
            for b in win.descendants(control_type="Button"):
                txt = b.window_text().strip().lower()
                if txt in ("bỏ qua", "dismiss", "hủy", "cancel"):
                    dismiss_btn = b
                    break

            if dismiss_btn:
                try:
                    dismiss_btn.invoke()
                except Exception:
                    try:
                        dismiss_btn.click_input()
                    except Exception:
                        pass
            else:
                needs_restore = (prev_hwnd and prev_hwnd != hwnd)
                try:
                    win32gui.SetForegroundWindow(hwnd)
                except Exception:
                    pass
                win32api.keybd_event(win32con.VK_ESCAPE, 0, 0, 0)
                time.sleep(0.05)
                win32api.keybd_event(win32con.VK_ESCAPE, 0, win32con.KEYEVENTF_KEYUP, 0)
                if needs_restore:
                    try:
                        win32gui.SetForegroundWindow(prev_hwnd)
                    except Exception:
                        pass

            return True, "Đã bỏ qua câu hỏi"
        except Exception as e:
            return False, str(e)

def create_new_session_in_opencode(title: str = "", directory: str = "") -> tuple:
    """Tạo một phiên làm việc mới trong OpenCode (ưu tiên REST API ngầm)."""
    try:
        from opencode_client import default_client
        if default_client.connect():
            ok, data = default_client.create_session(title=title, directory=directory)
            if ok:
                return True, data
    except Exception as e:
        print(f"[create_new_session_in_opencode] REST API error: {e}")

    with _ui_action_lock:
        hwnd = win32gui.FindWindow(None, "OpenCode")
        if not hwnd:
            return False, "Không tìm thấy cửa sổ OpenCode đang chạy."
        prev_hwnd = win32gui.GetForegroundWindow()
        try:
            force_foreground_window(hwnd)
            time.sleep(0.1)
            # Nhấn Ctrl+T để mở tab phiên mới trong OpenCode
            win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
            win32api.keybd_event(ord('T'), 0, 0, 0)
            time.sleep(0.05)
            win32api.keybd_event(ord('T'), 0, win32con.KEYEVENTF_KEYUP, 0)
            win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
            time.sleep(0.3)
            if prev_hwnd and prev_hwnd != hwnd and win32gui.IsWindow(prev_hwnd):
                force_foreground_window(prev_hwnd)
            return True, "Đã gửi phím tạo phiên mới (Ctrl+T) thành công."
        except Exception as e:
            if prev_hwnd and prev_hwnd != hwnd and win32gui.IsWindow(prev_hwnd):
                try:
                    force_foreground_window(prev_hwnd)
                except Exception:
                    pass
            return False, str(e)

def close_session_in_opencode(session_id: str = None, session_title: str = "") -> tuple:
    """Đóng / xóa phiên OpenCode (ưu tiên REST API ngầm)."""
    if not session_id and session_title:
        try:
            for s in poll_all_sessions():
                s_t = s.get('title', '')
                if s_t and (session_title.lower() in s_t.lower() or s_t.lower() in session_title.lower()):
                    session_id = s.get('sessionId')
                    break
        except Exception:
            pass

    if session_id:
        try:
            from opencode_client import default_client
            if default_client.connect():
                ok, msg = default_client.delete_session(session_id)
                if ok:
                    return True, "Đã đóng phiên làm việc thành công qua REST API."
        except Exception as e:
            print(f"[close_session_in_opencode] REST API error: {e}")

    with _ui_action_lock:
        hwnd = win32gui.FindWindow(None, "OpenCode")
        if not hwnd:
            return False, "Không tìm thấy cửa sổ OpenCode đang chạy."
        prev_hwnd = win32gui.GetForegroundWindow()
        try:
            force_foreground_window(hwnd)
            time.sleep(0.1)
            app = Application(backend="uia").connect(handle=hwnd, timeout=2.0)
            win = app.window(handle=hwnd)
            # Tìm nút đóng tab
            target_close = None
            for b in win.descendants(control_type="Button"):
                txt = b.window_text().strip().lower()
                if "đóng tab" in txt or "close tab" in txt:
                    target_close = b
                    break
            if target_close:
                try:
                    target_close.invoke()
                except Exception:
                    target_close.click_input()
                time.sleep(0.2)
                if prev_hwnd and prev_hwnd != hwnd and win32gui.IsWindow(prev_hwnd):
                    force_foreground_window(prev_hwnd)
                return True, "Đã đóng tab thành công qua giao diện."
            if prev_hwnd and prev_hwnd != hwnd and win32gui.IsWindow(prev_hwnd):
                force_foreground_window(prev_hwnd)
            return False, "Không tìm thấy nút đóng tab trên giao diện."
        except Exception as e:
            if prev_hwnd and prev_hwnd != hwnd and win32gui.IsWindow(prev_hwnd):
                try:
                    force_foreground_window(prev_hwnd)
                except Exception:
                    pass
            return False, str(e)

def switch_session_model_in_opencode(session_id: str, provider_id: str, model_id: str, variant: str = "default") -> tuple:
    """Đổi Model AI cho một phiên làm việc riêng biệt qua REST API."""
    try:
        from opencode_client import default_client
        if default_client.connect():
            ok, msg = default_client.switch_session_model(session_id, provider_id, model_id, variant)
            return ok, msg
        return False, "Không thể kết nối đến OpenCode REST API để đổi model."
    except Exception as e:
        return False, f"Lỗi đổi model: {e}"

def get_session_model_in_opencode(session_id: str) -> Optional[dict]:
    """Lấy thông tin model hiện tại của một phiên."""
    try:
        from opencode_client import default_client
        if default_client.connect():
            info = default_client.get_session_info(session_id)
            if info:
                return info.get("model")
    except Exception:
        pass
    return None

if __name__ == '__main__':
    sessions = poll_all_sessions()
    print(f"Found {len(sessions)} open sessions:")
    for s in sessions:
        print(f"[{s['color'].upper():6}] {s['label']:12} | {s['title']} ({s['directory']}) -> {s['detail']}")
