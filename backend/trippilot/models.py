"""公共持久化合同：用户、知识版本、消息和工作项；JSON 用整体赋值触发更新。"""
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .database import Base


def new_id() -> str:
    return uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Record:
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class User(Record, Base):
    __tablename__ = "users"
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(20), default="user")


class KnowledgeBase(Record, Base):
    __tablename__ = "knowledge_bases"
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    visibility: Mapped[str] = mapped_column(String(20), default="public")
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))


class Document(Record, Base):
    __tablename__ = "knowledge_documents"
    knowledge_base_id: Mapped[str] = mapped_column(ForeignKey("knowledge_bases.id"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    active_version_id: Mapped[str | None] = mapped_column(String(32))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)


class DocumentVersion(Record, Base):
    __tablename__ = "knowledge_document_versions"
    document_id: Mapped[str] = mapped_column(ForeignKey("knowledge_documents.id"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    storage_key: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), default="STORED")
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)


class Chunk(Record, Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (UniqueConstraint("version_id", "position"),)
    version_id: Mapped[str] = mapped_column(ForeignKey("knowledge_document_versions.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    page: Mapped[int | None] = mapped_column(Integer)
    heading: Mapped[str] = mapped_column(Text, default="")
    content: Mapped[str] = mapped_column(Text)


class WorkItem(Record, Base):
    """持久工作队列同时承担 outbox：消息队列断连也不会丢任务。"""
    __tablename__ = "work_items"
    kind: Mapped[str] = mapped_column(String(20))
    target_id: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    stage: Mapped[str] = mapped_column(String(80), default="等待处理")
    error: Mapped[str] = mapped_column(Text, default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime)
    lease_token: Mapped[str | None] = mapped_column(String(32))


class ChatSession(Record, Base):
    __tablename__ = "travel_sessions"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(120), default="新对话")
    summary: Mapped[str] = mapped_column(Text, default="")
    summary_through: Mapped[int] = mapped_column(Integer, default=0)
    requirements: Mapped[dict] = mapped_column(JSON, default=dict)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    active_run_id: Mapped[str | None] = mapped_column(String(32))


class Message(Record, Base):
    __tablename__ = "chat_messages"
    __table_args__ = (UniqueConstraint("session_id", "client_message_id"), UniqueConstraint("session_id", "seq"))
    session_id: Mapped[str] = mapped_column(ForeignKey("travel_sessions.id"), index=True)
    client_message_id: Mapped[str] = mapped_column(String(100))
    seq: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    citations: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="completed")


class ChatRun(Record, Base):
    __tablename__ = "chat_runs"
    session_id: Mapped[str] = mapped_column(ForeignKey("travel_sessions.id"), index=True)
    user_message_id: Mapped[str] = mapped_column(ForeignKey("chat_messages.id"))
    assistant_message_id: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(30), default="queued")
    result: Mapped[dict] = mapped_column(JSON, default=dict)


class Preference(Record, Base):
    __tablename__ = "user_preferences"
    __table_args__ = (UniqueConstraint("user_id", "key"),)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    key: Mapped[str] = mapped_column(String(100))
    value: Mapped[str] = mapped_column(Text)


class TripPlan(Record, Base):
    __tablename__ = "trip_plans"
    __table_args__ = (UniqueConstraint("session_id", "version"),)
    session_id: Mapped[str] = mapped_column(ForeignKey("travel_sessions.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    content: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), default="draft")
