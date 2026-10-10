"""公共配置：环境变量集中校验，开发可用本地存储，生产必须提供真实密钥。"""
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """基础设施配置；业务代码通过 get_settings 读取，不散落读取环境变量。"""
    model_config = SettingsConfigDict(env_prefix="TRIP_", env_file=".env", extra="ignore")
    environment: Literal["development", "production", "test"] = "development"
    single_node_mode: bool = False
    database_url: str = "sqlite:///./data/trippilot.db"
    secret_key: str = "development-only-change-before-deployment"
    bootstrap_admin_username: str = "admin"
    bootstrap_admin_password: SecretStr = SecretStr("")
    storage_backend: Literal["local", "minio"] = "local"
    storage_root: Path = Path("data/knowledge")
    queue_backend: Literal["database", "celery"] = "database"
    redis_url: str = "redis://localhost:6379/0"
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    embedding_backend: Literal["api", "local"] = "api"
    embedding_local_path: str = Field(default="", validation_alias=AliasChoices("TRIP_EMBEDDING_LOCAL_PATH", "BGE_M3_PATH"))
    embedding_device: str = Field(default="cpu", validation_alias=AliasChoices("TRIP_EMBEDDING_DEVICE", "BGE_DEVICE"))
    embedding_fp16: bool = Field(default=False, validation_alias=AliasChoices("TRIP_EMBEDDING_FP16", "BGE_FP16"))
    embedding_batch_size: int = Field(default=4, ge=1, le=128)
    embedding_max_length: int = Field(default=8192, ge=1, le=8192)
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_model: str = Field(default="", validation_alias=AliasChoices("TRIP_EMBEDDING_MODEL", "BGE_M3"))
    embedding_dimension: int = 1024
    reranker_base_url: str = ""
    reranker_api_key: str = ""
    reranker_model: str = ""
    reranker_top_n: int = 12
    vector_backend: Literal["bm25", "milvus"] = "bm25"
    milvus_uri: str = "http://localhost:19530"
    milvus_token: str = ""
    amap_key: str = ""
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = ""
    minio_secret_key: str = ""
    minio_bucket: str = "trippilot"
    minio_secure: bool = False
    max_upload_bytes: int = 20 * 1024 * 1024
    token_hours: int = 12
    context_char_budget: int = 18000

    @model_validator(mode="after")
    def validate_production_configuration(self):
        """生产拒绝演示密钥和 SQLite；不允许误把本地模式当生产部署。"""
        if self.vector_backend == "milvus" and not all(
            [
                self.milvus_uri,
                self.embedding_configuration_complete(),
            ]
        ):
            raise ValueError("Milvus 模式需要 Milvus URI 和完整 Embedding 配置")
        if self.storage_backend == "minio" and not all(
            [self.minio_endpoint, self.minio_access_key, self.minio_secret_key]
        ):
            raise ValueError("MinIO 模式需要 endpoint、access key 和 secret key")
        if self.queue_backend == "celery" and not self.redis_url:
            raise ValueError("Celery 模式需要 Redis URL")
        reranker_values = [
            self.reranker_base_url,
            self.reranker_api_key,
            self.reranker_model,
        ]
        if any(reranker_values) and not all(reranker_values):
            raise ValueError("Reranker 的 Base URL、API Key 和模型必须同时配置")
        if self.environment == "production":
            if len(self.secret_key) < 32 or "development" in self.secret_key or "replace-" in self.secret_key:
                raise ValueError("生产环境需要随机生成且至少 32 字符的 SECRET_KEY")
            if not self.database_url.startswith("postgresql") and not self.single_node_mode:
                raise ValueError("生产环境必须使用 PostgreSQL")
            if not all([self.llm_base_url, self.llm_api_key, self.llm_model]):
                raise ValueError("生产环境需要完整的对话模型配置")
        return self

    def embedding_configuration_complete(self) -> bool:
        """只判断配置是否齐全；不加载模型、不检查 CUDA，也不请求模型服务。"""
        if self.embedding_backend == "local":
            return bool(self.embedding_local_path and self.embedding_model and self.embedding_device)
        return bool(self.embedding_base_url and self.embedding_api_key and self.embedding_model)

    def public_configuration_status(self) -> dict[str, str | bool]:
        """只输出能力状态和非敏感模式名，禁止配置检查把任何凭据打印到终端。"""
        return {
            "environment": self.environment,
            "database": "postgresql" if self.database_url.startswith("postgresql") else "sqlite",
            "storage": self.storage_backend,
            "queue": self.queue_backend,
            "retrieval": self.vector_backend,
            "llm_configured": bool(self.llm_base_url and self.llm_api_key and self.llm_model),
            "embedding_configured": self.embedding_configuration_complete(),
            "embedding_backend": self.embedding_backend,
            "reranker_configured": bool(
                self.reranker_base_url and self.reranker_api_key and self.reranker_model
            ),
            "map_configured": bool(self.amap_key),
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()


