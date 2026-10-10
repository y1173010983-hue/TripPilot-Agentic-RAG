"""从 PyTorch 官方索引对应地址续传本机 GPU 安装包，并验证官方 SHA256。"""

import hashlib
import time
import urllib.error
import urllib.request
from pathlib import Path


MODEL_WHEEL_URL = "https://download.pytorch.org/whl/cu118/torch-2.7.1%2Bcu118-cp312-cp312-win_amd64.whl"
MODEL_WHEEL_SHA256 = "80855ec840b7b06372ff43535d01393a8ec101842618d1f9ed629572b52aed71"
WHEEL_PATH = Path(__file__).resolve().parents[1] / "data/wheels/torch-2.7.1+cu118-cp312-cp312-win_amd64.whl"


def step1_download_or_resume_wheel() -> None:
    """网络中断时保留已下载字节；服务端忽略 Range 时重新写入，避免拼接损坏。"""
    WHEEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, 11):
        offset = WHEEL_PATH.stat().st_size if WHEEL_PATH.exists() else 0
        request = urllib.request.Request(MODEL_WHEEL_URL, headers={"Range": f"bytes={offset}-"})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                append = response.status == 206
                if append and not response.headers.get("Content-Range", "").startswith(f"bytes {offset}-"):
                    raise ValueError("Server returned an unexpected byte range")
                count = offset if append else 0
                expected = count + int(response.headers["Content-Length"]) if response.headers.get("Content-Length") else None
                print(f"Attempt {attempt}: starting at {count / 1024**2:.1f} MiB", flush=True)
                last_report = time.monotonic()
                with WHEEL_PATH.open("ab" if append else "wb") as destination:
                    while block := response.read(1024 * 1024):
                        destination.write(block)
                        count += len(block)
                        if time.monotonic() - last_report >= 15:
                            destination.flush()
                            print(f"Downloaded {count / 1024**2:.1f} MiB", flush=True)
                            last_report = time.monotonic()
                if expected is not None and count != expected:
                    raise OSError("Incomplete download; retry from saved offset")
            return
        except urllib.error.HTTPError as error:
            if error.code == 416 and offset:
                return  # 文件可能已完整；下一步 SHA256 决定是否可以安装。
            print(f"Attempt {attempt}: HTTP {error.code}; partial file retained", flush=True)
        except (OSError, urllib.error.URLError) as error:
            print(f"Attempt {attempt}: {type(error).__name__}; partial file retained", flush=True)
        time.sleep(3)
    raise SystemExit("Download incomplete; rerun this script to resume")


def step2_verify_official_checksum() -> None:
    """只有文件与官方索引的摘要一致时，才报告可安装。"""
    with WHEEL_PATH.open("rb") as source:
        actual = hashlib.file_digest(source, "sha256").hexdigest()
    if actual != MODEL_WHEEL_SHA256:
        raise SystemExit("Checksum mismatch; do not install this wheel")
    print("Official wheel checksum verified: " + str(WHEEL_PATH), flush=True)


if __name__ == "__main__":
    step1_download_or_resume_wheel()
    step2_verify_official_checksum()
