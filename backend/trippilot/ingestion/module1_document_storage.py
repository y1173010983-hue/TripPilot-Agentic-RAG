"""存库第一步：验证文件、生成安全对象键、持久保存原文件与 SHA-256。"""
import hashlib
import io
from pathlib import Path
from zipfile import ZipFile, BadZipFile
from fastapi import HTTPException
from ..config import get_settings

SUPPORTED = {".pdf", ".docx", ".md", ".txt"}


class DocumentStorage:
    """提供单一文件存储边界，业务不感知本地目录与 MinIO 的差异。"""
    def __init__(self):
        self.settings = get_settings()

    def step1_validate_file(self, filename: str, data: bytes) -> tuple[str, str]:
        """验证真实内容及限制，返回后缀和哈希；不接受任意可执行文件。"""
        suffix = Path(filename).suffix.lower()
        if suffix not in SUPPORTED:
            raise HTTPException(415, "仅支持 PDF、DOCX、MD、TXT")
        if not data:
            raise HTTPException(422, "文件为空")
        if len(data) > self.settings.max_upload_bytes:
            raise HTTPException(413, "单文件最大 20 MB")
        if suffix == ".pdf" and not data.startswith(b"%PDF-"):
            raise HTTPException(415, "文件不是有效 PDF")
        if suffix == ".docx":
            try:
                with ZipFile(io.BytesIO(data)) as archive:
                    if "word/document.xml" not in archive.namelist():
                        raise ValueError("missing document")
                    if sum(x.file_size for x in archive.infolist()) > 100 * 1024 * 1024:
                        raise HTTPException(413, "DOCX 解压后过大")
            except (BadZipFile, ValueError):
                raise HTTPException(415, "无效 DOCX 文件") from None
        if suffix in {".md", ".txt"}:
            try:
                data.decode("utf-8-sig")
            except UnicodeDecodeError:
                raise HTTPException(422, "文本需要 UTF-8 编码") from None
        return suffix, hashlib.sha256(data).hexdigest()

    def local_path(self, key: str) -> Path:
        root = self.settings.storage_root.resolve()
        target = (root / key).resolve()
        if not target.is_relative_to(root):
            raise ValueError("非法存储路径")
        return target

    def minio_client(self):
        from minio import Minio
        s = self.settings
        return Minio(s.minio_endpoint, access_key=s.minio_access_key,
                     secret_key=s.minio_secret_key, secure=s.minio_secure)

    def ensure_minio_bucket(self, client) -> None:
        """首次写入前创建私有存储桶；并发创建时只忽略“桶已存在”这一种竞态。"""
        if client.bucket_exists(self.settings.minio_bucket):
            return
        try:
            client.make_bucket(self.settings.minio_bucket)
        except Exception:
            if not client.bucket_exists(self.settings.minio_bucket):
                raise

    def step2_save_original(self, key: str, data: bytes) -> None:
        """原文件写入成功后才允许创建处理任务；本地使用原子替换。"""
        if self.settings.storage_backend == "local":
            path = self.local_path(key)
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(path.suffix + ".tmp")
            temp.write_bytes(data)
            temp.replace(path)
        else:
            client = self.minio_client()
            self.ensure_minio_bucket(client)
            client.put_object(self.settings.minio_bucket, key, io.BytesIO(data), len(data))

    def read_original(self, key: str) -> bytes:
        if self.settings.storage_backend == "local":
            return self.local_path(key).read_bytes()
        response = self.minio_client().get_object(self.settings.minio_bucket, key)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    def delete_original(self, key: str) -> None:
        if self.settings.storage_backend == "local":
            self.local_path(key).unlink(missing_ok=True)
        else:
            self.minio_client().remove_object(self.settings.minio_bucket, key)
