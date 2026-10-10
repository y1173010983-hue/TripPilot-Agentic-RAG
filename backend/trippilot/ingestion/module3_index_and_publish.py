"""存库第三步：持久切片、建立可选真实向量索引，再发布当前版本。"""
from datetime import timedelta
from sqlalchemy import delete, select
from ..database import session_factory
from ..models import Chunk, Document, DocumentVersion, WorkItem, utcnow
from .module1_document_storage import DocumentStorage
from .module2_parse_and_chunk import step1_parse_document, step2_split_into_citable_chunks


def update_stage(db, job, stage):
    job.stage = stage
    job.lease_until = utcnow() + timedelta(minutes=10)
    db.commit()


def process_document_job(job_id: str, lease_token: str):
    """执行已领取的任务；失败保留原文件，成功后才切换文档当前版本。"""
    with session_factory() as db:
        job = db.get(WorkItem, job_id)
        version = db.get(DocumentVersion, job.target_id)
        document = db.get(Document, version.document_id)
        if document.deleted:
            raise ValueError("文档已删除，取消发布")
        version.status = "PARSING"
        update_stage(db, job, "解析原文件")
        pages = step1_parse_document(version.filename, DocumentStorage().read_original(version.storage_key))
        pieces = step2_split_into_citable_chunks(pages)
        version.status = "CHUNKING"
        update_stage(db, job, "保存正文切片")
        db.execute(delete(Chunk).where(Chunk.version_id == version.id))
        for i, part in enumerate(pieces):
            db.add(Chunk(version_id=version.id, position=i, page=part.page, heading=part.heading,
                         content=part.content))
        db.commit()
        chunks = list(db.scalars(select(Chunk).where(Chunk.version_id == version.id)))
        version.status = "INDEXING"
        update_stage(db, job, "建立检索索引")
        from ..retrieval.module2_knowledge_search import index_version
        index_version(document, version, chunks)
        db.refresh(document)
        db.refresh(job)
        if document.deleted or job.lease_token != lease_token or job.status != "running":
            raise ValueError("任务已取消或租约已变化，禁止发布")
        version.status = "READY"
        version.chunk_count = len(chunks)
        db.flush()
        latest_ready = db.scalar(
            select(DocumentVersion.id)
            .where(
                DocumentVersion.document_id == document.id,
                DocumentVersion.status == "READY",
            )
            .order_by(DocumentVersion.created_at.desc())
            .limit(1)
        )
        document.active_version_id = latest_ready
        job.status, job.stage = "completed", "文档已入库"
        db.commit()
