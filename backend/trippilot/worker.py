"""可靠后台执行：数据库工作项为事实来源，支持本地轮询和 Celery 分发。"""
from datetime import timedelta
import logging
import time
from sqlalchemy import select, update, delete, func, or_, and_
from .database import session_factory
from .models import WorkItem, utcnow, new_id, DocumentVersion, Chunk, ChatRun, ChatSession, Message, TripPlan

logger = logging.getLogger(__name__)


def claim_work(job_id=None):
    """通过条件更新领取租约；重复队列消息不能同时执行同一个任务。"""
    with session_factory() as db:
        condition = or_(WorkItem.status == "queued", and_(WorkItem.status == "running", WorkItem.lease_until < utcnow()))
        query = select(WorkItem.id).where(condition).order_by(WorkItem.created_at).limit(1)
        if job_id:
            query = query.where(WorkItem.id == job_id)
        jid = db.scalar(query)
        if not jid:
            return None
        token = new_id()
        result = db.execute(update(WorkItem).where(WorkItem.id == jid, condition).values(
            status="running", lease_until=utcnow() + timedelta(minutes=10), lease_token=token,
            attempts=WorkItem.attempts + 1))
        db.commit()
        return (jid, token) if result.rowcount == 1 else None


def process_chat(job_id, token):
    """执行有持久检查点的图，周期落盘真实 Token 增量，完成时幂等保存回复与方案。"""
    from .retrieval.module5_agent_workflow import build_travel_graph, checkpoint_store
    from .retrieval.module7_performance_monitor import calculate_queue_wait_ms
    processing_started_at = utcnow()
    processing_started_clock = time.perf_counter()
    with session_factory() as db:
        job = db.get(WorkItem, job_id)
        run = db.get(ChatRun, job.target_id)
        chat = db.get(ChatSession, run.session_id)
        if run.status == "cancelled" or chat.deleted:
            job.status = "cancelled"
            db.commit()
            return
        run.status = "running"
        run.result = {
            **(run.result or {}),
            "performance": {
                "queue_wait_ms": calculate_queue_wait_ms(job.created_at, processing_started_at),
                "steps": {},
            },
        }
        if not run.assistant_message_id:
            seq = (db.scalar(select(func.max(Message.seq)).where(Message.session_id == chat.id)) or 0) + 1
            message = Message(id=new_id(), session_id=chat.id, client_message_id="assistant:" + run.id,
                              seq=seq, role="assistant", content="", status="generating")
            db.add(message)
            run.assistant_message_id = message.id
        rid, sid, mid = run.id, chat.id, run.assistant_message_id
        job.stage = "理解问题并检索旅游资料"
        db.commit()
    partial, last_flush = [], [0.0]

    def persist_delta(delta):
        partial.append(delta)
        with session_factory() as db:
            job = db.get(WorkItem, job_id)
            run = db.get(ChatRun, rid)
            if run.status != "running" or job.lease_token != token:
                raise RuntimeError("生成已取消或任务租约已变化")
            if time.monotonic() - last_flush[0] > 0.25:
                db.get(Message, mid).content = "".join(partial)
                job.stage = "正在生成回答"
                job.lease_until = utcnow() + timedelta(minutes=10)
                db.commit()
                last_flush[0] = time.monotonic()

    with checkpoint_store() as saver:
        graph = build_travel_graph(saver, persist_delta)
        config = {"configurable": {"thread_id": sid}, "recursion_limit": 24}
        snapshot = graph.get_state(config)
        initial = {"run_id": rid, "session_id": sid}
        if snapshot.values.get("run_id") == rid and snapshot.next:
            initial = None
        result = graph.invoke(initial, config)
    with session_factory() as db:
        job = db.get(WorkItem, job_id)
        run = db.get(ChatRun, rid)
        chat = db.get(ChatSession, sid)
        if run.status != "running" or job.lease_token != token or chat.deleted:
            return
        message = db.get(Message, mid)
        message.content, message.citations, message.status = result["answer"], result["evidence"], "completed"
        if result.get("plan"):
            version = (db.scalar(select(func.max(TripPlan.version)).where(TripPlan.session_id == sid)) or 0) + 1
            db.add(TripPlan(session_id=sid, version=version, content={**result["plan"], "validation_issues": result["issues"]},
                            status="needs_review" if result["issues"] else "draft"))
        performance = dict((run.result or {}).get("performance") or {})
        performance["processing_ms"] = round((time.perf_counter() - processing_started_clock) * 1000, 1)
        performance["total_ms"] = round(performance.get("queue_wait_ms", 0) + performance["processing_ms"], 1)
        run.result = {"intent": result["decision"]["intent"], "requirements": result["requirements"],
                      "issues": result["issues"], "tool_results": result["tool_results"],
                      "performance": performance}
        run.status, chat.active_run_id = "completed", None
        job.status, job.stage = "completed", "回答已保存"
        db.commit()


def process_purge(job_id):
    from .ingestion.module1_document_storage import DocumentStorage
    from .retrieval.module5_agent_workflow import checkpoint_store
    with session_factory() as db:
        job = db.get(WorkItem, job_id)
        if job.kind == "purge":
            versions = list(db.scalars(select(DocumentVersion).where(DocumentVersion.document_id == job.target_id)))
            for version in versions:
                DocumentStorage().delete_original(version.storage_key)
                from .config import get_settings
                if get_settings().vector_backend == "milvus":
                    from .retrieval.module2_knowledge_search import vector_client, collection_name
                    vector_client().delete(collection_name(), filter=f'version_id == "{version.id}"')
                db.execute(delete(Chunk).where(Chunk.version_id == version.id))
        else:
            sid = job.target_id
            with checkpoint_store() as saver:
                saver.delete_thread(sid)
            db.execute(delete(ChatRun).where(ChatRun.session_id == sid))
            db.execute(delete(Message).where(Message.session_id == sid))
            db.execute(delete(TripPlan).where(TripPlan.session_id == sid))
            chat = db.get(ChatSession, sid)
            chat.summary, chat.requirements = "", {}
        job.status, job.stage = "completed", "数据已清理"
        db.commit()


def run_one(job_id=None):
    claimed = claim_work(job_id)
    if not claimed:
        return False
    jid, token = claimed
    with session_factory() as db:
        kind = db.get(WorkItem, jid).kind
    try:
        if kind == "ingestion":
            from .ingestion.module3_index_and_publish import process_document_job
            process_document_job(jid, token)
        elif kind == "chat":
            process_chat(jid, token)
        else:
            process_purge(jid)
    except Exception as exc:
        # 不记录 HTTP 请求/响应或异常堆栈，防止供应商 Key 随 URL 泄露。
        from pydantic import ValidationError
        from .retrieval.module1_model_gateway import ModelUnavailable
        if isinstance(exc, ValidationError):
            safe = "模型返回的数据格式不完整，请补充需求后重试"
        else:
            safe = str(exc)[:300] if isinstance(exc, (ValueError, ModelUnavailable)) else "任务处理失败，请检查配置或稍后重试"
        logger.warning("work_failed job=%s exception=%s", jid, type(exc).__name__)
        with session_factory() as db:
            job = db.get(WorkItem, jid)
            if job.lease_token != token:
                return True
            job.status, job.error = "failed", safe
            if kind == "ingestion":
                db.get(DocumentVersion, job.target_id).status = "FAILED"
            if kind == "chat":
                run = db.get(ChatRun, job.target_id)
                if run:
                    run.status = "cancelled" if run.status == "cancelled" else "failed"
                    chat = db.get(ChatSession, run.session_id)
                    if chat.active_run_id == run.id:
                        chat.active_run_id = None
                    if run.assistant_message_id:
                        message = db.get(Message, run.assistant_message_id)
                        message.status = run.status
                        if not message.content:
                            message.content = safe
            db.commit()
    return True


def run_worker():
    """本地后台进程；UI 关闭不影响任务；停止进程后可通过数据库租约恢复。"""
    while True:
        if not run_one():
            time.sleep(1)
