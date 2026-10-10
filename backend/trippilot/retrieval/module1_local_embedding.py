"""本地 BGE-M3 边界：仅从指定目录加载 Dense 模型，按配置的设备和精度执行推理。"""

from functools import lru_cache
from pathlib import Path
from threading import RLock

from ..config import get_settings


# 同一进程中的并发请求共用模型并串行占用 GPU；不同进程不能共享此锁或模型。
_model_lock = RLock()


@lru_cache(maxsize=1)
def step1_load_local_embedding_model(path: str, device: str, fp16: bool, max_length: int):
    """首次推理才加载模型；CUDA 不可用时明确失败，不偷偷切换 CPU 或云端。"""
    import torch
    from sentence_transformers import SentenceTransformer

    model_path = Path(path).expanduser()
    if not model_path.is_dir():
        raise ValueError("BGE-M3 本地模型目录不存在，请检查 BGE_M3_PATH")
    target = torch.device(device)
    if target.type == "cuda":
        index = target.index if target.index is not None else 0
        if not torch.cuda.is_available() or index >= torch.cuda.device_count():
            raise ValueError("指定 CUDA 设备不可用，请检查 PyTorch CUDA 版本和 BGE_DEVICE")
    if fp16 and target.type != "cuda":
        raise ValueError("本项目的 FP16 模式仅用于 CUDA；CPU 请显式设置 BGE_FP16=False")
    model = SentenceTransformer(
        str(model_path),
        device=device,
        local_files_only=True,
        trust_remote_code=False,
        model_kwargs={"torch_dtype": torch.float16 if fp16 else torch.float32},
    )
    model.max_seq_length = max_length
    model.eval()
    return model


def create_local_embeddings(texts: list[str]) -> list[list[float]]:
    """分批生成归一化 Dense 向量并检查维度；BM25 仍由 Milvus 内置函数生成。"""
    if not texts:
        return []
    import torch

    settings = get_settings()
    with _model_lock, torch.inference_mode():
        model = step1_load_local_embedding_model(
            settings.embedding_local_path,
            settings.embedding_device,
            settings.embedding_fp16,
            settings.embedding_max_length,
        )
        vectors = model.encode(
            texts,
            batch_size=settings.embedding_batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
    if vectors.shape != (len(texts), settings.embedding_dimension):
        raise ValueError("本地 Embedding 输出数量或维度与配置不一致")
    return vectors.astype("float32").tolist()
