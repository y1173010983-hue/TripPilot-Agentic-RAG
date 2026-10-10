"""业务数据库会话：SQLAlchemy 事务边界；框架检查点使用独立连接。"""
from functools import lru_cache
from pathlib import Path
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from .config import get_settings


class Base(DeclarativeBase):
    pass


@lru_cache
def get_engine():
    """建立线程安全连接池；SQLite 用于开发测试，生产使用 PostgreSQL。"""
    url = get_settings().database_url
    if url.startswith("sqlite"):
        Path("data").mkdir(exist_ok=True)
    engine = create_engine(url, pool_pre_ping=True,
                           connect_args={"check_same_thread": False, "timeout": 30} if url.startswith("sqlite") else {})
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def configure_sqlite(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=WAL")
    return engine


def session_factory():
    return sessionmaker(get_engine(), expire_on_commit=False)()


def get_db():
    """每请求一个 Session；写操作由业务服务显式提交。"""
    with session_factory() as session:
        yield session
