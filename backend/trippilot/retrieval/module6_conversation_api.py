"""用户端 API：会话和消息持久化、运行控制、SSE、个人偏好与方案版本。"""
import asyncio
import json
import logging
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, func, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from ..auth import current_user, current_user_for_stream
from ..config import get_settings
from ..database import get_db, session_factory
from ..models import User, ChatSession, Message, ChatRun, Preference, TripPlan, WorkItem, new_id

logger = logging.getLogger(__name__)

router = APIRouter(tags=["用户端对话检索"])


def owned_session(db, sid, user):
    chat = db.get(ChatSession, sid)
    if not chat or chat.deleted or chat.user_id != user.id:
        raise HTTPException(404, "对话不存在")
    return chat


def owned_run(db, rid, user):
    run = db.get(ChatRun, rid)
    if not run:
        raise HTTPException(404, "运行不存在")
    owned_session(db, run.session_id, user)
    return run


class SessionInput(BaseModel):
    title: str = Field(default="新对话", min_length=1, max_length=120)


class ChatInput(BaseModel):
    session_id: str
    client_message_id: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1, max_length=12000)


class PreferenceInput(BaseModel):
    value: str = Field(min_length=1, max_length=1000)


class PlanAction(BaseModel):
    version: int = Field(ge=1)
    base_version: int = Field(ge=1)


@router.post("/sessions", status_code=201)
def create_session(body: SessionInput, db: Session = Depends(get_db), user: User = Depends(current_user)):
    chat = ChatSession(user_id=user.id, title=body.title)
    db.add(chat)
    db.commit()
    return chat


@router.get("/sessions")
def list_sessions(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return list(db.scalars(select(ChatSession).where(ChatSession.user_id == user.id, ChatSession.deleted.is_(False))
                           .order_by(ChatSession.created_at.desc()).limit(100)))


@router.patch("/sessions/{sid}")
def rename_session(sid: str, body: SessionInput, db: Session = Depends(get_db), user: User = Depends(current_user)):
    chat = owned_session(db, sid, user)
    chat.title = body.title
    db.commit()
    return chat


@router.get("/sessions/{sid}/messages")
def list_messages(
    sid: str,
    after: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """默认返回最新一页且保持正序；提供 after 游标供前端增量获取。"""
    owned_session(db, sid, user)
    page_size = min(max(limit, 1), 200)
    query = select(Message).where(Message.session_id == sid)
    if after:
        return list(
            db.scalars(
                query.where(Message.seq > max(0, after))
                .order_by(Message.seq)
                .limit(page_size)
            )
        )
    latest = list(db.scalars(query.order_by(Message.seq.desc()).limit(page_size)))
    latest.reverse()
    return latest


@router.delete("/sessions/{sid}", status_code=202)
def delete_session(sid: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    chat = owned_session(db, sid, user)
    chat.deleted = True
    if chat.active_run_id:
        run = db.get(ChatRun, chat.active_run_id)
        if run:
            run.status = "cancelled"
    chat.active_run_id = None
    db.add(WorkItem(kind="purge_chat", target_id=sid))
    db.commit()
    return {"deleted": True, "note": "会话消息及关联方案将清理，独立偏好保留"}


@router.post("/chat", status_code=202)
def send_message(body: ChatInput, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """同一会话用条件更新占用运行槽；事务同时保存消息、运行与持久待处理工作项。"""
    chat = owned_session(db, body.session_id, user)
    existing = db.scalar(select(Message).where(Message.session_id == chat.id,
                                               Message.client_message_id == body.client_message_id))
    if existing:
        run = db.scalar(select(ChatRun).where(ChatRun.user_message_id == existing.id))
        return {"run_id": run.id, "user_message_id": existing.id, "duplicate": True}
    rid = new_id()
    claimed = db.execute(update(ChatSession).where(ChatSession.id == chat.id, ChatSession.active_run_id.is_(None),
                                                   ChatSession.deleted.is_(False)).values(active_run_id=rid))
    if claimed.rowcount != 1:
        db.rollback()
        raise HTTPException(409, "当前正在生成，请先停止后再发送新要求")
    seq = (db.scalar(select(func.max(Message.seq)).where(Message.session_id == chat.id)) or 0) + 1
    message = Message(id=new_id(), session_id=chat.id, seq=seq, client_message_id=body.client_message_id,
                      role="user", content=body.content.strip())
    try:
        db.add(message)
        db.flush()
        run = ChatRun(id=rid, session_id=chat.id, user_message_id=message.id)
        db.add(run)
        job = WorkItem(kind="chat", target_id=rid)
        db.add(job)
        if chat.title == "新对话":
            chat.title = body.content[:50]
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "并发请求冲突，请刷新后重试") from None
    if get_settings().queue_backend == "celery":
        try:
            from ..celery_app import execute_work

            execute_work.delay(job.id)
        except Exception:
            # 任务已持久化；即时投递失败时仍由 Celery Beat 扫描兜底。
            logger.warning("chat_immediate_dispatch_failed job=%s", job.id)
    return {"run_id": rid, "user_message_id": message.id, "duplicate": False}


@router.get("/chat/runs/{rid}")
def get_run(rid: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    run = owned_run(db, rid, user)
    message = db.get(Message, run.assistant_message_id) if run.assistant_message_id else None
    job = db.scalar(select(WorkItem).where(WorkItem.kind == "chat", WorkItem.target_id == rid))
    return {"id": rid, "status": run.status, "stage": job.stage if job else "", "error": job.error if job else "",
            "message": message, "result": run.result}


@router.get("/chat/runs/{rid}/events")
async def stream_run(rid: str, request: Request, user: User = Depends(current_user_for_stream)):
    """SSE 输出持久快照；断线重新获取不会重复拼接，读取过程不长期持有数据库连接。"""
    with session_factory() as db:
        owned_run(db, rid, user)
    async def events():
        last = ""
        for _ in range(1200):
            if await request.is_disconnected():
                return
            with session_factory() as db:
                try:
                    data = get_run(rid, db, user)
                except HTTPException:
                    return
                if data["message"]:
                    m = data["message"]
                    data["message"] = {"id": m.id, "content": m.content, "citations": m.citations, "status": m.status}
            serialized = json.dumps(data, ensure_ascii=False, default=str)
            if serialized != last:
                yield "event: snapshot\ndata: " + serialized + "\n\n"
                last = serialized
            else:
                yield ": heartbeat\n\n"
            if data["status"] in {"completed", "failed", "cancelled"}:
                return
            await asyncio.sleep(0.5)
    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/chat/runs/{rid}/cancel")
def cancel_run(rid: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    run = owned_run(db, rid, user)
    if run.status in {"queued", "running"}:
        run.status = "cancelled"
        job = db.scalar(select(WorkItem).where(WorkItem.kind == "chat", WorkItem.target_id == run.id))
        if job:
            job.status, job.stage = "cancelled", "用户已停止生成"
        chat = db.get(ChatSession, run.session_id)
        chat.active_run_id = None
        if run.assistant_message_id:
            db.get(Message, run.assistant_message_id).status = "cancelled"
        db.commit()
    return {"status": run.status}


@router.get("/users/me/preferences")
def list_preferences(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return list(db.scalars(select(Preference).where(Preference.user_id == user.id)))


@router.put("/users/me/preferences/{key}")
def save_preference(key: str, body: PreferenceInput, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if len(key) > 100:
        raise HTTPException(422, "偏好名称过长")
    preference = db.scalar(select(Preference).where(Preference.user_id == user.id, Preference.key == key))
    if not preference:
        preference = Preference(user_id=user.id, key=key, value=body.value)
        db.add(preference)
    preference.value = body.value
    db.commit()
    return preference


@router.delete("/users/me/preferences/{key}")
def remove_preference(key: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    preference = db.scalar(select(Preference).where(Preference.user_id == user.id, Preference.key == key))
    if preference:
        db.delete(preference)
        db.commit()
    return {"deleted": True}


@router.get("/sessions/{sid}/plans")
def list_plans(sid: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    owned_session(db, sid, user)
    return list(db.scalars(select(TripPlan).where(TripPlan.session_id == sid).order_by(TripPlan.version.desc())))


@router.post("/sessions/{sid}/plans/restore")
def restore_plan(sid: str, body: PlanAction, db: Session = Depends(get_db), user: User = Depends(current_user)):
    chat = owned_session(db, sid, user)
    if chat.active_run_id:
        raise HTTPException(409, "生成期间不能恢复旧方案")
    latest = db.scalar(select(func.max(TripPlan.version)).where(TripPlan.session_id == sid))
    if latest != body.base_version:
        raise HTTPException(409, "方案已变更，请刷新版本")
    source = db.scalar(select(TripPlan).where(TripPlan.session_id == sid, TripPlan.version == body.version))
    if not source:
        raise HTTPException(404, "方案版本不存在")
    plan = TripPlan(session_id=sid, version=latest + 1, content=source.content, status="draft")
    db.add(plan)
    db.commit()
    return plan


class SearchInput(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    top_k: int = Field(default=8, ge=1, le=30)


@router.post("/knowledge/search")
def search(body: SearchInput, db: Session = Depends(get_db), user: User = Depends(current_user)):
    from .module2_knowledge_search import search_knowledge
    return search_knowledge(db, user, body.query, top_k=body.top_k)
