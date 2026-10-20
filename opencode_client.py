import os
import sys
import time
import base64
import mimetypes
import ctypes
from typing import Optional, Dict, Any, List, Tuple
import psutil
import requests

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010

class PROCESS_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ('Reserved1', ctypes.c_void_p),
        ('PebBaseAddress', ctypes.c_void_p),
        ('Reserved2', ctypes.c_void_p * 2),
        ('UniqueProcessId', ctypes.c_void_p),
        ('Reserved3', ctypes.c_void_p)
    ]

class OpenCodeAPIClient:
    """
    Client giao tiếp trực tiếp với Sidecar REST API của OpenCode Desktop.
    Tự động đọc cổng TCP và Password từ tiến trình NodeService của OpenCode trong bộ nhớ.
    """
    def __init__(self):
        self._url: Optional[str] = None
        self._password: Optional[str] = None
        self._auth_headers: Optional[Dict[str, str]] = None
        self._sidecar_pid: Optional[int] = None
        self._last_discover_time: float = 0.0

    def is_connected(self) -> bool:
        if not self._url or not self._auth_headers:
            return self.connect()
        try:
            r = requests.get(f"{self._url}/global/health", headers=self._auth_headers, timeout=1.0)
            return r.status_code == 200
        except Exception:
            return self.connect()

    def connect(self) -> bool:
        """Tự động phát hiện PID, cổng và Password của OpenCode Sidecar."""
        pid = self._find_sidecar_pid()
        if not pid:
            self._reset()
            return False

        # Nếu PID không đổi và health OK thì giữ nguyên
        if self._sidecar_pid == pid and self._url and self._auth_headers:
            try:
                r = requests.get(f"{self._url}/global/health", headers=self._auth_headers, timeout=1.0)
                if r.status_code == 200:
                    return True
            except Exception:
                pass

        password = self._read_password_from_process(pid)
        if not password:
            self._reset()
            return False

        port = self._find_listening_port(pid)
        if not port:
            self._reset()
            return False

        url = f"http://127.0.0.1:{port}"
        auth_str = base64.b64encode(f"opencode:{password}".encode()).decode()
        headers = {
            "Authorization": f"Basic {auth_str}",
            "Content-Type": "application/json"
        }

        # Kiểm tra sức khỏe kết nối
        try:
            r = requests.get(f"{url}/global/health", headers=headers, timeout=1.5)
            if r.status_code == 200:
                self._url = url
                self._password = password
                self._auth_headers = headers
                self._sidecar_pid = pid
                self._last_discover_time = time.time()
                return True
        except Exception:
            pass

        self._reset()
        return False

    def _reset(self):
        self._url = None
        self._password = None
        self._auth_headers = None
        self._sidecar_pid = None

    def _find_sidecar_pid(self) -> Optional[int]:
        for p in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                name = p.info.get('name') or ''
                if 'OpenCode' in name:
                    cmdline = ' '.join(p.info.get('cmdline') or [])
                    if 'NodeService' in cmdline:
                        return p.info['pid']
            except Exception:
                pass
        return None

    def _read_password_from_process(self, pid: int) -> Optional[str]:
        try:
            ntdll = ctypes.WinDLL('ntdll.dll')
            kernel32 = ctypes.WinDLL('kernel32.dll')

            hProcess = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
            if not hProcess:
                return None

            try:
                pbi = PROCESS_BASIC_INFORMATION()
                return_length = ctypes.c_ulong()
                status = ntdll.NtQueryInformationProcess(hProcess, 0, ctypes.byref(pbi), ctypes.sizeof(pbi), ctypes.byref(return_length))
                if status != 0 or not pbi.PebBaseAddress:
                    return None

                # Đọc ProcessParameters từ PEB (offset 0x20 trên Windows 64-bit)
                proc_params_addr = ctypes.c_void_p()
                read_bytes = ctypes.c_size_t()
                kernel32.ReadProcessMemory(hProcess, ctypes.c_void_p(pbi.PebBaseAddress + 0x20), ctypes.byref(proc_params_addr), 8, ctypes.byref(read_bytes))
                if not proc_params_addr.value:
                    return None

                # Đọc Environment pointer từ RTL_USER_PROCESS_PARAMETERS (offset 0x80 trên 64-bit)
                env_addr = ctypes.c_void_p()
                kernel32.ReadProcessMemory(hProcess, ctypes.c_void_p(proc_params_addr.value + 0x80), ctypes.byref(env_addr), 8, ctypes.byref(read_bytes))
                if not env_addr.value:
                    return None

                # Đọc khối Environment (UTF-16LE block)
                buffer = ctypes.create_string_buffer(65536)
                kernel32.ReadProcessMemory(hProcess, env_addr, buffer, 65536, ctypes.byref(read_bytes))
                raw = buffer.raw[:read_bytes.value]
                env_str = raw.decode('utf-16-le', errors='ignore')
                entries = env_str.split('\x00')
                for e in entries:
                    if e.startswith('OPENCODE_SERVER_PASSWORD='):
                        return e.split('=', 1)[1].strip()
            finally:
                kernel32.CloseHandle(hProcess)
        except Exception:
            pass
        return None

    def _find_listening_port(self, pid: int) -> Optional[int]:
        try:
            for conn in psutil.net_connections(kind='tcp'):
                if conn.status == psutil.CONN_LISTEN and conn.pid == pid:
                    if conn.laddr.ip in ('127.0.0.1', '0.0.0.0'):
                        return conn.laddr.port
        except Exception:
            pass
        return None

    # ================= CÁC API THỰC THI CHÍNH =================

    def send_prompt(self, session_id: str, prompt_text: str = "", image_path: Optional[str] = None) -> Tuple[bool, str]:
        """Gửi prompt và/hoặc ảnh vào session chỉ định qua REST API."""
        if not self.is_connected():
            return False, "Không thể kết nối đến OpenCode REST API."

        parts = []
        if prompt_text:
            parts.append({
                "type": "text",
                "text": prompt_text
            })

        if image_path and os.path.exists(image_path):
            try:
                mime_type, _ = mimetypes.guess_type(image_path)
                if not mime_type:
                    mime_type = "image/png"
                with open(image_path, "rb") as f:
                    b64_data = base64.b64encode(f.read()).decode()
                parts.append({
                    "type": "file",
                    "mime": mime_type,
                    "filename": os.path.basename(image_path),
                    "url": f"data:{mime_type};base64,{b64_data}"
                })
            except Exception as e:
                return False, f"Lỗi đọc file ảnh: {e}"

        if not parts:
            return False, "Nội dung prompt hoặc file đính kèm không được rỗng."

        payload = {
            "parts": parts
        }

        try:
            url = f"{self._url}/session/{session_id}/prompt_async"
            r = requests.post(url, headers=self._auth_headers, json=payload, timeout=5.0)
            if r.status_code in (200, 204):
                return True, "Đã gửi câu lệnh thành công qua REST API."
            return False, f"API trả về mã lỗi {r.status_code}: {r.text}"
        except Exception as e:
            return False, f"Lỗi gửi request REST API: {e}"

    def get_pending_permissions(self) -> List[Dict[str, Any]]:
        """Lấy danh sách các yêu cầu cấp quyền đang chờ xác nhận."""
        if not self.is_connected():
            return []
        try:
            r = requests.get(f"{self._url}/permission", headers=self._auth_headers, timeout=2.0)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        return []

    def reply_permission(self, request_id: str, reply: str = "once", message: str = "") -> Tuple[bool, str]:
        """
        Phê duyệt hoặc từ chối cấp quyền.
        reply: "once" | "always" | "reject"
        """
        if not self.is_connected():
            return False, "Không thể kết nối đến OpenCode REST API."
        try:
            url = f"{self._url}/permission/{request_id}/reply"
            payload = {"reply": reply}
            if message:
                payload["message"] = message
            r = requests.post(url, headers=self._auth_headers, json=payload, timeout=3.0)
            if r.status_code == 200:
                return True, f"Đã phản hồi cấp quyền ({reply}) thành công."
            return False, f"Lỗi phản hồi quyền {r.status_code}: {r.text}"
        except Exception as e:
            return False, f"Lỗi gọi API cấp quyền: {e}"

    def get_pending_questions(self) -> List[Dict[str, Any]]:
        """Lấy danh sách các câu hỏi đang chờ người dùng trả lời."""
        if not self.is_connected():
            return []
        try:
            r = requests.get(f"{self._url}/question", headers=self._auth_headers, timeout=2.0)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        return []

    def reply_question(self, request_id: str, answers: List[List[str]]) -> Tuple[bool, str]:
        """
        Trả lời câu hỏi trắc nghiệm.
        answers: danh sách các lựa chọn được chọn cho từng câu hỏi, ví dụ [["Option 1"]]
        """
        if not self.is_connected():
            return False, "Không thể kết nối đến OpenCode REST API."
        try:
            url = f"{self._url}/question/{request_id}/reply"
            payload = {"answers": answers}
            r = requests.post(url, headers=self._auth_headers, json=payload, timeout=3.0)
            if r.status_code == 200:
                return True, "Đã gửi câu trả lời thành công."
            return False, f"Lỗi trả lời câu hỏi {r.status_code}: {r.text}"
        except Exception as e:
            return False, f"Lỗi gọi API trả lời câu hỏi: {e}"

    def reject_question(self, request_id: str) -> Tuple[bool, str]:
        """Bỏ qua / Từ chối câu hỏi."""
        if not self.is_connected():
            return False, "Không thể kết nối đến OpenCode REST API."
        try:
            url = f"{self._url}/question/{request_id}/reject"
            r = requests.post(url, headers=self._auth_headers, json={}, timeout=3.0)
            if r.status_code == 200:
                return True, "Đã bỏ qua câu hỏi thành công."
            return False, f"Lỗi bỏ qua câu hỏi {r.status_code}: {r.text}"
        except Exception as e:
            return False, f"Lỗi gọi API bỏ qua câu hỏi: {e}"

    def abort_session(self, session_id: str) -> Tuple[bool, str]:
        """Dừng / Hủy bỏ lượt chạy của phiên."""
        if not self.is_connected():
            return False, "Không thể kết nối đến OpenCode REST API."
        try:
            url = f"{self._url}/session/{session_id}/abort"
            r = requests.post(url, headers=self._auth_headers, json={}, timeout=3.0)
            if r.status_code in (200, 204):
                return True, "Đã dừng phiên thành công."
            return False, f"Lỗi dừng phiên {r.status_code}: {r.text}"
        except Exception as e:
            return False, f"Lỗi gọi API dừng phiên: {e}"

    def list_sessions(self) -> List[Dict[str, Any]]:
        """Lấy danh sách tất cả các phiên."""
        if not self.is_connected():
            return []
        try:
            r = requests.get(f"{self._url}/session", headers=self._auth_headers, timeout=3.0)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        return []

# Singleton instance dùng chung
default_client = OpenCodeAPIClient()
