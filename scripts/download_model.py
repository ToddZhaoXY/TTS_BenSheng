"""Download the public model into this project; never uploads user data."""
import hashlib
import argparse
import json
import subprocess
import sys
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from studio.engine import MODEL_ID, MODEL_PATH

REVISION = "1eccf1cb2519b5a4e8a95b5f0544f3303568164f"
WEIGHTS = {
    "model.safetensors": (1829344448, "d7c7ed3e3464e3e59de0f955b3755891fa8319ff061c3f0307fe2e1343bc122d"),
    "speech_tokenizer/model.safetensors": (682293092, "836b7b357f5ea43e889936a3709af68dfe3751881acefe4ecf0dbd30ba571258"),
}


def verify_local():
    required = ["config.json", "generation_config.json", "merges.txt", "vocab.json", "tokenizer_config.json", "preprocessor_config.json", "speech_tokenizer/config.json", "speech_tokenizer/preprocessor_config.json"]
    for name in required:
        path = MODEL_PATH / name
        if not path.is_file():
            raise RuntimeError("缺少文件：" + name)
        if path.suffix == ".json":
            json.loads(path.read_text(encoding="utf-8"))
    for name, (size, checksum) in WEIGHTS.items():
        path = MODEL_PATH / name
        if not valid(path, {"size":size, "lfs":{"oid":checksum}}):
            raise RuntimeError("模型文件校验未通过：" + name)
        print("校验通过：" + name, flush=True)
    (MODEL_PATH / ".ready").write_text(REVISION + "\n", encoding="utf-8")


def fetch_ranges(url, pending, size):
    """Bounded parallel range transfers, assembled then SHA-256 verified by caller."""
    block_size = 8 * 1024 * 1024
    parts = pending.with_name(pending.name + ".chunks")
    parts.mkdir(exist_ok=True)

    def block(index):
        start = index * block_size
        end = min(size, start + block_size) - 1
        path = parts / f"{index:05}.part"
        expected = end - start + 1
        if path.exists() and path.stat().st_size == expected:
            return path
        temporary = path.with_suffix(".pending")
        subprocess.run(["curl", "--fail", "--location", "--silent", "--show-error", "--retry", "4",
                        "--connect-timeout", "30", "--max-time", "300", "--range", f"{start}-{end}",
                        "--output", str(temporary), url], check=True)
        if temporary.stat().st_size != expected:
            raise RuntimeError(f"下载分块长度不正确：{index}")
        temporary.replace(path)
        return path

    indices = range((size + block_size - 1) // block_size)
    with ThreadPoolExecutor(max_workers=16) as executor:
        paths = list(executor.map(block, indices))
    with pending.open("wb") as output:
        for path in paths:
            with path.open("rb") as source:
                shutil.copyfileobj(source, output)


def valid(path, entry):
    if not path.is_file() or path.stat().st_size != entry["size"]:
        return False
    if entry.get("lfs"):
        with path.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        return digest == entry["lfs"]["oid"]
    return True


def fetch(entry):
    relative = Path(entry["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Invalid model file path")
    path = MODEL_PATH / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if valid(path, entry):
        print(f"已校验：{relative}", flush=True)
        return
    pending = path.with_suffix(path.suffix + ".part")
    if not valid(pending, entry):
        url = f"https://huggingface.co/{MODEL_ID}/resolve/{REVISION}/{relative.as_posix()}"
        print(f"下载：{relative}（{entry['size'] / 1000000:.1f} MB）", flush=True)
        if entry["size"] > 32 * 1024 * 1024:
            fetch_ranges(url, pending, entry["size"])
        else:
            subprocess.run(["curl", "--fail", "--location", "--silent", "--show-error", "--retry", "4",
                            "--connect-timeout", "25", "--max-time", "300", "--continue-at", "-",
                            "--output", str(pending), url], check=True)
    if not valid(pending, entry):
        raise RuntimeError(f"文件校验失败：{relative}，请重新下载。")
    pending.replace(path)
    parts = pending.with_name(pending.name + ".chunks")
    if parts.exists():
        # These are only this downloader's disposable, now verified transfer blocks.
        shutil.rmtree(parts)
    print(f"完成：{relative}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify-only", action="store_true", help="只校验本地文件，不访问网络")
    args = parser.parse_args()
    if args.verify_only:
        verify_local()
        print("本地模型已就绪，无需下载。", flush=True)
        sys.exit(0)
    print("正在下载公开的 Qwen3-TTS 模型。录音和文稿不会上传。", flush=True)
    url = f"https://huggingface.co/api/models/{MODEL_ID}/tree/{REVISION}?recursive=true"
    response = subprocess.run(["curl", "-sSfL", "--retry", "3", "--max-time", "60", url], capture_output=True, text=True, check=True)
    entries = [entry for entry in json.loads(response.stdout) if entry["type"] == "file" and entry["path"] != ".gitattributes"]
    print(f"模型共 {sum(e['size'] for e in entries) / 1e9:.2f} GB，支持断点续传。", flush=True)
    with ThreadPoolExecutor(max_workers=3) as executor:
        list(executor.map(fetch, entries))
    required = ["config.json", "model.safetensors", "tokenizer_config.json", "speech_tokenizer/config.json", "speech_tokenizer/model.safetensors"]
    missing = [name for name in required if not (MODEL_PATH / name).is_file()]
    if missing:
        raise RuntimeError("模型文件缺失：" + ", ".join(missing))
    verify_local()
    print("声音模型已准备好。现在可以启动录音棚。", flush=True)
