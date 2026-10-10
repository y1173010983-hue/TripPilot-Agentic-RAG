"""管理端接口：知识库、拖拽上传、版本、切片预览、重试与启停删除。"""
import io
from datetime import date
from pathlib import Path
from urllib.parse import quote
from typing import Literal
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..auth import admin_user
from ..config import get_settings
from ..database import get_db
from ..models import KnowledgeBase, Document, DocumentVersion, Chunk, WorkItem, User, new_id
from .module1_document_storage import DocumentStorage

router = APIRouter(prefix="/admin", tags=["管理端存库"], dependencies=[Depends(admin_user)])


class KnowledgeInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=3000)
    visibility: Literal["public", "private"] = "public"


class DocumentPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    enabled: bool | None = None


def require_knowledge_base(db, kb_id, user):
    kb = db.get(KnowledgeBase, kb_id)
    if not kb or kb.owner_id != user.id:
        raise HTTPException(404, "知识库不存在或无权管理")
    return kb


def require_document(db, doc_id, user):
    doc = db.get(Document, doc_id)
    if not doc or doc.deleted:
        raise HTTPException(404, "文档不存在")
    require_knowledge_base(db, doc.knowledge_base_id, user)
    return doc


@router.post("/knowledge-bases", status_code=201)
def create_knowledge_base(body: KnowledgeInput, db: Session = Depends(get_db), user: User = Depends(admin_user)):
    kb = KnowledgeBase(**body.model_dump(), owner_id=user.id)
    db.add(kb)
    db.commit()
    return kb


@router.get("/knowledge-bases")
def list_knowledge_bases(db: Session = Depends(get_db), user: User = Depends(admin_user)):
    return list(db.scalars(select(KnowledgeBase).where(KnowledgeBase.owner_id == user.id)))


@router.post("/knowledge-bases/{kb_id}/documents", status_code=202)
async def upload_document(kb_id: str, file: UploadFile = File(...), city: str = Form(""),
                          category: str = Form("综合攻略"), source_url: str = Form(""),
                          valid_until: date | None = Form(None), document_id: str | None = Form(None),
                          db: Session = Depends(get_db), user: User = Depends(admin_user)):
    """每个文件一个事务：原文件保存后创建版本和持久任务；同库相同内容去重。"""
    require_knowledge_base(db, kb_id, user)
    data = await file.read(get_settings().max_upload_bytes + 1)
    filename = Path((file.filename or "document.txt").replace("\\", "/")).name[:255]
    storage = DocumentStorage()
    suffix, digest = storage.step1_validate_file(filename, data)
    duplicate = db.scalar(select(DocumentVersion).join(Document).where(
        Document.knowledge_base_id == kb_id, Document.deleted.is_(False), DocumentVersion.sha256 == digest))
    if duplicate:
        return {"document_id": duplicate.document_id, "version_id": duplicate.id, "duplicate": True}
    doc = require_document(db, document_id, user) if document_id else Document(
        id=new_id(), knowledge_base_id=kb_id, title=filename)
    if doc.knowledge_base_id != kb_id:
        raise HTTPException(409, "文档不属于所选知识库")
    vid = new_id()
    key = f"{kb_id}/{doc.id}/{vid}/original/source{suffix}"
    storage.step2_save_original(key, data)
    version = DocumentVersion(id=vid, document_id=doc.id, filename=filename, sha256=digest,
                              storage_key=key, size_bytes=len(data), metadata_json={"city": city,
                              "category": category, "source_url": source_url,
                              "valid_until": valid_until.isoformat() if valid_until else None})
    job = WorkItem(kind="ingestion", target_id=vid)
    try:
        db.add(doc)
        db.flush()
        db.add(version)
        db.add(job)
        db.commit()
    except Exception:
        db.rollback()
        storage.delete_original(key)
        raise
    return {"document_id": doc.id, "version_id": vid, "job_id": job.id, "status": "queued", "duplicate": False}


@router.get("/knowledge-bases/{kb_id}/documents")
def list_documents(kb_id: str, offset: int = 0, db: Session = Depends(get_db), user: User = Depends(admin_user)):
    require_knowledge_base(db, kb_id, user)
    docs = db.scalars(select(Document).where(Document.knowledge_base_id == kb_id, Document.deleted.is_(False))
                      .order_by(Document.created_at.desc()).offset(max(0, offset)).limit(100))
    result = []
    for doc in docs:
        versions = list(db.scalars(select(DocumentVersion).where(DocumentVersion.document_id == doc.id)
                                  .order_by(DocumentVersion.created_at.desc())))
        job = db.scalar(select(WorkItem).where(WorkItem.target_id == versions[0].id)
                        .order_by(WorkItem.created_at.desc())) if versions else None
        result.append({"id": doc.id, "title": doc.title, "enabled": doc.enabled,
                       "active_version_id": doc.active_version_id, "versions": versions, "job": job})
    return result


@router.get("/documents/{doc_id}/chunks")
def list_chunks(doc_id: str, version_id: str | None = None, db: Session = Depends(get_db), user: User = Depends(admin_user)):
    doc = require_document(db, doc_id, user)
    vid = version_id or doc.active_version_id
    version = db.get(DocumentVersion, vid) if vid else None
    if not version or version.document_id != doc.id:
        return []
    return list(db.scalars(select(Chunk).where(Chunk.version_id == vid).order_by(Chunk.position).limit(300)))


@router.get("/documents/{doc_id}/original")
def download_original(doc_id: str, version_id: str | None = None, db: Session = Depends(get_db), user: User = Depends(admin_user)):
    doc = require_document(db, doc_id, user)
    version = db.get(DocumentVersion, version_id or doc.active_version_id) if (version_id or doc.active_version_id) else None
    if not version or version.document_id != doc.id:
        raise HTTPException(404, "版本不存在")
    data = DocumentStorage().read_original(version.storage_key)
    return StreamingResponse(io.BytesIO(data), media_type="application/octet-stream",
                             headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(version.filename)}"})


@router.patch("/documents/{doc_id}")
def update_document(doc_id: str, body: DocumentPatch, db: Session = Depends(get_db), user: User = Depends(admin_user)):
    doc = require_document(db, doc_id, user)
    for key, value in body.model_dump(exclude_none=True).items():
        setattr(doc, key, value)
    db.commit()
    return doc


@router.delete("/documents/{doc_id}", status_code=202)
def delete_document(doc_id: str, db: Session = Depends(get_db), user: User = Depends(admin_user)):
    """先切断检索可见性，物理清理交给幂等任务，避免删一半仍可引用。"""
    doc = require_document(db, doc_id, user)
    doc.deleted, doc.enabled = True, False
    db.add(WorkItem(kind="purge", target_id=doc.id))
    db.commit()
    return {"deleted": True}


@router.get("/ingestion-jobs/{job_id}")
def get_job(job_id: str, db: Session = Depends(get_db), user: User = Depends(admin_user)):
    job = db.get(WorkItem, job_id)
    version = db.get(DocumentVersion, job.target_id) if job and job.kind == "ingestion" else None
    if not version:
        raise HTTPException(404, "任务不存在")
    require_document(db, version.document_id, user)
    return job


@router.post("/ingestion-jobs/{job_id}/retry")
def retry_job(job_id: str, db: Session = Depends(get_db), user: User = Depends(admin_user)):
    job = get_job(job_id, db, user)
    if job.status != "failed":
        raise HTTPException(409, "仅失败任务可以重试")
    job.status, job.error, job.stage = "queued", "", "等待重试"
    db.commit()
    return job
