import os
import sys
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, HTTPException, Body
from pydantic import BaseModel
import uvicorn

import core_monitor
from opencode_client import default_client

app = FastAPI(
    title="OpenCode Remote API Gateway",
    description="REST API Gateway điều khiển và giám sát OpenCode hoàn toàn ngầm không qua GUI",
    version="1.0.0"
)

class SendPromptRequest(BaseModel):
    session: str
    prompt: str
    image: Optional[str] = None

class ApprovePermissionRequest(BaseModel):
    request_id: Optional[str] = None
    session: Optional[str] = None
    action: str = "once"  # "once" | "always" | "reject"

class ReplyQuestionRequest(BaseModel):
    request_id: str
    answers: List[List[str]]

class DismissQuestionRequest(BaseModel):
    request_id: str

class AbortSessionRequest(BaseModel):
    session: str

class CreateSessionRequest(BaseModel):
    title: Optional[str] = ""
    directory: Optional[str] = ""

class SwitchModelRequest(BaseModel):
    provider_id: str
    model_id: str
    variant: Optional[str] = "default"

@app.get("/health")
def get_health():
    api_connected = default_client.is_connected()
    return {
        "status": "ok",
        "opencode_api_connected": api_connected,
        "opencode_sidecar_url": default_client._url
    }

@app.get("/api/status")
def get_status():
    """Lấy trạng thái thời gian thực của các tab/phiên OpenCode đang mở."""
    sessions = core_monitor.poll_all_sessions()
    pending_perms = default_client.get_pending_permissions()
    pending_quests = default_client.get_pending_questions()
    return {
        "count": len(sessions),
        "sessions": sessions,
        "pending_permissions": pending_perms,
        "pending_questions": pending_quests
    }

@app.get("/api/sessions/{session_id}/latest")
def get_latest_response(session_id: str):
    """Lấy câu trả lời gần nhất của AI trong phiên."""
    resp = core_monitor.get_latest_assistant_response(session_id)
    if resp is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy câu trả lời nào cho session này.")
    return {"session_id": session_id, "response": resp}

@app.post("/api/send")
def send_prompt(req: SendPromptRequest):
    """Gửi prompt/lệnh vào phiên OpenCode qua REST API (hoàn toàn ngầm)."""
    session_id = None
    resolved_title = req.session

    for s in core_monitor.poll_all_sessions():
        s_id = s.get('sessionId', '')
        s_t = s.get('title', '')
        if req.session == s_id or (req.session.lower() in s_t.lower()):
            session_id = s_id
            resolved_title = s_t
            break

    if not session_id and req.session.startswith("ses_"):
        session_id = req.session

    if not session_id:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy phiên khớp với '{req.session}'")

    ok, msg = core_monitor.send_prompt_to_opencode_session(
        session_title=resolved_title,
        prompt_text=req.prompt,
        image_path=req.image,
        session_id=session_id
    )

    if not ok:
        raise HTTPException(status_code=500, detail=msg)

    return {
        "success": True,
        "message": msg,
        "session_id": session_id,
        "title": resolved_title
    }

@app.get("/api/permissions")
def get_permissions():
    """Lấy danh sách yêu cầu cấp quyền đang chờ."""
    return default_client.get_pending_permissions()

@app.post("/api/permissions/approve")
def approve_permission(req: ApprovePermissionRequest):
    """Phê duyệt hoặc từ chối cấp quyền."""
    action = req.action.lower()
    if action == "deny":
        action = "reject"
    if action not in ("once", "always", "reject"):
        raise HTTPException(status_code=400, detail="action phải là once, always hoặc reject")

    if req.request_id:
        ok, msg = default_client.reply_permission(req.request_id, reply=action)
    else:
        ok, msg = core_monitor.approve_permission_in_opencode(req.session or "", action=action)

    if not ok:
        raise HTTPException(status_code=500, detail=msg)

    return {"success": True, "message": msg}

@app.get("/api/questions")
def get_questions():
    """Lấy danh sách câu hỏi đang chờ."""
    return default_client.get_pending_questions()

@app.post("/api/questions/reply")
def reply_question(req: ReplyQuestionRequest):
    """Gửi câu trả lời cho câu hỏi trắc nghiệm."""
    ok, msg = default_client.reply_question(req.request_id, req.answers)
    if not ok:
        raise HTTPException(status_code=500, detail=msg)
    return {"success": True, "message": msg}

@app.post("/api/questions/dismiss")
def dismiss_question(req: DismissQuestionRequest):
    """Bỏ qua câu hỏi."""
    ok, msg = default_client.reject_question(req.request_id)
    if not ok:
        raise HTTPException(status_code=500, detail=msg)
    return {"success": True, "message": msg}

@app.post("/api/abort")
def abort_session(req: AbortSessionRequest):
    """Dừng lượt đang chạy của phiên."""
    session_id = None
    for s in core_monitor.poll_all_sessions():
        s_id = s.get('sessionId', '')
        s_t = s.get('title', '')
        if req.session == s_id or (req.session.lower() in s_t.lower()):
            session_id = s_id
            break
    if not session_id and req.session.startswith("ses_"):
        session_id = req.session

    if not session_id:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy phiên '{req.session}'")

    ok, msg = default_client.abort_session(session_id)
    if not ok:
        raise HTTPException(status_code=500, detail=msg)
    return {"success": True, "message": msg}

@app.post("/api/sessions/create")
def create_session_endpoint(req: CreateSessionRequest):
    """Mở một phiên OpenCode mới."""
    ok, res = core_monitor.create_new_session_in_opencode(title=req.title or "", directory=req.directory or "")
    if not ok:
        raise HTTPException(status_code=500, detail=str(res))
    return {"success": True, "session": res}

@app.delete("/api/sessions/{session_id}")
def close_session_endpoint(session_id: str):
    """Đóng / xóa phiên OpenCode."""
    ok, msg = core_monitor.close_session_in_opencode(session_id=session_id)
    if not ok:
        raise HTTPException(status_code=500, detail=msg)
    return {"success": True, "message": msg}

@app.get("/api/models")
def get_available_models_endpoint():
    """Lấy danh sách các model đang kết nối và khả dụng."""
    return default_client.get_available_models()

@app.post("/api/sessions/{session_id}/model")
def switch_session_model_endpoint(session_id: str, req: SwitchModelRequest):
    """Đổi Model AI cho một phiên làm việc riêng biệt."""
    ok, msg = core_monitor.switch_session_model_in_opencode(
        session_id=session_id,
        provider_id=req.provider_id,
        model_id=req.model_id,
        variant=req.variant or "default"
    )
    if not ok:
        raise HTTPException(status_code=500, detail=msg)
    return {"success": True, "message": msg}

if __name__ == "__main__":
    port = int(os.environ.get("OPENCODE_GATEWAY_PORT", 7080))
    print(f"Khởi động OpenCode REST API Gateway tại http://127.0.0.1:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port)
