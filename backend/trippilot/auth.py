"""账号与权限：Argon2 密码、短期 JWT、逐对象校验；公共注册只能生成普通用户。"""
from datetime import datetime, timedelta, timezone
import jwt
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from .config import get_settings
from .database import get_db, session_factory
from .models import User

router = APIRouter(prefix="/auth", tags=["账号"])
passwords = PasswordHash.recommended()
bearer = HTTPBearer(auto_error=False)


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=80, pattern=r"^[\w@.+-]+$")
    password: str = Field(min_length=10, max_length=128)


def current_user(token: HTTPAuthorizationCredentials = Depends(bearer), db: Session = Depends(get_db)):
    """验签并从数据库加载用户，角色更改会在下一请求生效。"""
    try:
        payload = jwt.decode(token.credentials if token else "", get_settings().secret_key,
                             algorithms=["HS256"], audience="trippilot")
        user = db.get(User, payload["sub"])
        if not user:
            raise ValueError("account missing")
        return user
    except (jwt.PyJWTError, ValueError, KeyError):
        raise HTTPException(401, "请先登录") from None


def current_user_for_stream(token: HTTPAuthorizationCredentials = Depends(bearer)):
    """在返回 SSE 前完成鉴权并释放连接，避免流式响应持有长事务。"""
    with session_factory() as db:
        user = current_user(token, db)
        db.expunge(user)
        return user


def admin_user(user: User = Depends(current_user)):
    if user.role != "admin":
        raise HTTPException(403, "需要管理员权限")
    return user


@router.post("/register", status_code=201)
def register(body: Credentials, db: Session = Depends(get_db)):
    user = User(username=body.username, password_hash=passwords.hash(body.password), role="user")
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "用户名已存在") from None
    return {"id": user.id, "username": user.username}


@router.post("/login")
def login(body: Credentials, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.username == body.username))
    if not user or not passwords.verify(body.password, user.password_hash):
        raise HTTPException(401, "用户名或密码错误")
    expires = datetime.now(timezone.utc) + timedelta(hours=get_settings().token_hours)
    token = jwt.encode({"sub": user.id, "aud": "trippilot", "exp": expires}, get_settings().secret_key)
    return {"access_token": token, "token_type": "bearer", "user": {"id": user.id, "username": user.username, "role": user.role}}


@router.get("/me")
def me(user: User = Depends(current_user)):
    return {"id": user.id, "username": user.username, "role": user.role}
