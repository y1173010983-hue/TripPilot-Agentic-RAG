"""运维命令：初始化数据库、创建管理员、运行工作进程与模型连通验证。"""
import argparse
import getpass
import json
from sqlalchemy import select
from .database import session_factory
from .models import User
from .auth import passwords


def main():
    """解析运维步骤；管理员可交互创建或显式读取本机初始化配置。"""
    parser = argparse.ArgumentParser(description="TripPilot 本地与企业部署管理")
    parser.add_argument(
        "command",
        choices=["init-db", "create-admin", "worker", "check-config", "check-model"],
    )
    parser.add_argument("--username")
    parser.add_argument("--from-env", action="store_true", help="创建管理员时读取本机初始化配置，不在命令行传密码")
    args = parser.parse_args()
    if args.command == "init-db":
        from alembic.config import Config
        from alembic import command
        command.upgrade(Config("alembic.ini"), "head")
        from .retrieval.module5_agent_workflow import checkpoint_store

        with checkpoint_store(initialize=True):
            pass
        print("业务数据库及对话检查点迁移完成")
    elif args.command == "create-admin":
        from .config import get_settings

        settings = get_settings()
        username = args.username or (settings.bootstrap_admin_username if args.from_env else "admin")
        password = (
            settings.bootstrap_admin_password.get_secret_value()
            if args.from_env else getpass.getpass("管理员密码（至少 10 位，不回显）: ")
        )
        if len(password) < 10:
            raise SystemExit("密码至少 10 位")
        with session_factory() as db:
            if db.scalar(select(User).where(User.username == username)):
                raise SystemExit("账号已存在，不自动覆盖")
            db.add(User(username=username, password_hash=passwords.hash(password), role="admin"))
            db.commit()
        print("管理员已创建")
    elif args.command == "check-config":
        from .config import get_settings

        print(json.dumps(get_settings().public_configuration_status(), ensure_ascii=False, indent=2))
    elif args.command == "check-model":
        from .retrieval.module1_model_gateway import ModelGateway
        result = ModelGateway().complete_json([{"role": "user", "content": '仅返回JSON：{"status":"ok"}'}], 32)
        print("模型连接成功" if result.get("status") == "ok" else "模型返回非预期结果")
    else:
        from .worker import run_worker
        run_worker()


if __name__ == "__main__":
    main()
