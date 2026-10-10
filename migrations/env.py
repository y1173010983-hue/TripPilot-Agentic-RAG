"""读取统一配置执行迁移，不在 alembic.ini 写数据库密码。"""
from alembic import context
from trippilot.database import Base, get_engine
import trippilot.models  # noqa: F401

with get_engine().connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()
