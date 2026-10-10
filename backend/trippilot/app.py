"""FastAPI 装配：只注册路由、异常和静态资源，不承载业务步骤。"""
import logging
from pathlib import Path
from uuid import uuid4
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.orm import Session
from .auth import router as auth_router, admin_user
from .config import get_settings
from .database import get_db
from .ingestion.module4_admin_api import router as ingestion_router, require_knowledge_base
from .models import User
from .retrieval.module6_conversation_api import router as retrieval_router, SearchInput
from .retrieval.module2_knowledge_search import search_knowledge


def create_app():
    app = FastAPI(title="TripPilot 旅游智能助手", version="0.1.0")
    for router in (auth_router, ingestion_router, retrieval_router):
        app.include_router(router, prefix="/api/v1")

    @app.middleware("http")
    async def request_tracking(request: Request, call_next):
        trace_id = uuid4().hex
        try:
            response = await call_next(request)
        except Exception as exc:
            logging.getLogger(__name__).error("request_failed trace=%s exception=%s", trace_id, type(exc).__name__)
            response = JSONResponse({"detail": "服务内部错误", "trace_id": trace_id}, status_code=500)
        response.headers["X-Request-ID"] = trace_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/api/v1/health")
    def health(db: Session = Depends(get_db)):
        db.execute(text("SELECT 1"))
        s = get_settings()
        return {
            "status": "ok",
            "model_configured": bool(s.llm_model and s.llm_api_key),
            "model": s.llm_model,
            "search_mode": s.vector_backend,
            "reranker_configured": bool(
                s.reranker_base_url and s.reranker_api_key and s.reranker_model
            ),
            "storage_mode": s.storage_backend,
            "queue_mode": s.queue_backend,
            "map_configured": bool(s.amap_key),
            "booking_available": False,
        }

    @app.post("/api/v1/admin/knowledge-bases/{kb_id}/retrieval-test")
    def retrieval_test(kb_id: str, body: SearchInput, db: Session = Depends(get_db), user: User = Depends(admin_user)):
        require_knowledge_base(db, kb_id, user)
        return search_knowledge(db, user, body.query, kb_id, body.top_k)

    dist = Path("frontend/dist")
    if dist.exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    return app


app = create_app()
