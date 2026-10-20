"""Download the audited IndexTTS 2.5 manifest without running upstream code.

Transfers use bounded HTTP ranges and verify Content-Range, size and final
publisher checksums. Completed blocks survive interruption for resuming.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "index-tts-main" / "checkpoints"
MANIFEST = ROOT / "scripts" / "index25-download-manifest.json"
TRANSFER = ROOT / ".cache" / "index25-downloads"
PROGRESS = ROOT / "index-tts-main" / "download-progress.json"
BLOCK_SIZE = 8 * 1024 * 1024


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def digest_file(path, item):
    algorithm = item.get("algorithm", "sha256")
    digest = hashlib.sha1() if algorithm == "git-sha1" else hashlib.sha256()
    if algorithm == "git-sha1":
        digest.update(f"blob {item['size']}\0".encode())
    with path.open("rb") as source:
        for data in iter(lambda: source.read(BLOCK_SIZE), b""):
            digest.update(data)
    return digest.hexdigest()


def target(item):
    path = (DEST / item["path"]).resolve()
    if not path.is_relative_to(DEST.resolve()):
        raise ValueError("Manifest path escapes the model directory")
    return path


def chunk_path(item, index):
    return TRANSFER / item["checksum"] / f"{index:05}.part"


def transfer(item, index, stop):
    path = chunk_path(item, index)
    start = index * BLOCK_SIZE
    expected = min(BLOCK_SIZE, item["size"] - start)
    end = start + expected - 1
    if path.is_file() and path.stat().st_size == expected:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(".pending")
    headers = path.with_suffix(".headers")
    last_error = ""
    for attempt in range(4):
        if stop.is_set():
            raise InterruptedError("Download stopped")
        url = item["urls"][attempt % len(item["urls"])]
        result = subprocess.run([
            "curl", "--silent", "--show-error", "--fail", "--location",
            "--connect-timeout", "20", "--max-time", "180",
            "--speed-limit", "2048", "--speed-time", "30",
            "--range", f"{start}-{end}", "--max-filesize", str(expected),
            "--dump-header", str(headers), "--output", str(pending),
            "--write-out", "%{http_code}", url,
        ], capture_output=True, text=True)
        ranges = re.findall(r"(?im)^content-range:\s*bytes\s+(\d+)-(\d+)/(\d+)", headers.read_text(errors="replace") if headers.exists() else "")
        correct_range = bool(ranges) and tuple(map(int, ranges[-1])) == (start, end, item["size"])
        whole_file = index == 0 and expected == item["size"] and result.stdout.strip() == "200"
        if result.returncode == 0 and pending.exists() and pending.stat().st_size == expected and (correct_range or whole_file):
            pending.replace(path)
            headers.unlink(missing_ok=True)
            return expected
        last_error = f"HTTP {result.stdout.strip()}, curl exit {result.returncode}, block {index}"
        stop.wait(min(1 + attempt, 3))
    raise RuntimeError(f"{item['path']}: {last_error}")


def assemble(item):
    destination = target(item)
    destination.parent.mkdir(parents=True, exist_ok=True)
    pending = destination.with_name(destination.name + ".download")
    with pending.open("wb") as output:
        for index in range((item["size"] + BLOCK_SIZE - 1) // BLOCK_SIZE):
            with chunk_path(item, index).open("rb") as source:
                shutil.copyfileobj(source, output, BLOCK_SIZE)
    if pending.stat().st_size != item["size"] or digest_file(pending, item) != item["checksum"]:
        raise RuntimeError("Publisher checksum mismatch: " + item["path"])
    if destination.exists():
        raise FileExistsError("Refusing to overwrite an existing file: " + str(destination))
    pending.replace(destination)
    # Only remove this downloader's completed, publisher-verified temporary blocks.
    shutil.rmtree(TRANSFER / item["checksum"], ignore_errors=True)


def verify_local():
    """Verify every required file without network access; record readiness on success."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    marker = DEST / ".download-complete.json"
    try:
        for item in manifest["files"]:
            path = target(item)
            if not path.is_file() or path.stat().st_size != item["size"] or digest_file(path, item) != item["checksum"]:
                raise RuntimeError("模型文件缺失或校验失败（文件未改动）：" + item["path"])
            print("Verified: " + item["path"], flush=True)
    except Exception:
        marker.unlink(missing_ok=True)
        raise
    result = {"manifest":str(MANIFEST), "files":[item["path"] for item in manifest["files"]],
              "total_bytes":sum(item["size"] for item in manifest["files"])}
    atomic_json(marker, result)
    print(f"全部 {len(result['files'])} 个文件校验通过，无需下载。", flush=True)
    return result


def run(workers):
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    items = manifest["files"]
    DEST.mkdir(parents=True, exist_ok=True)
    TRANSFER.mkdir(parents=True, exist_ok=True)
    total = sum(item["size"] for item in items)
    done = 0
    completed = []
    todo = []
    pending_counts = {}
    for item in items:
        path = target(item)
        if path.exists():
            if path.stat().st_size != item["size"] or digest_file(path, item) != item["checksum"]:
                raise RuntimeError("Existing file did not match the manifest; preserved: " + str(path))
            done += item["size"]
            completed.append(item["path"])
            continue
        if item["size"] == 0:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch(exist_ok=False)
            completed.append(item["path"])
            continue
        count = (item["size"] + BLOCK_SIZE - 1) // BLOCK_SIZE
        pending_counts[item["path"]] = count
        for index in range(count):
            block = chunk_path(item, index)
            size = min(BLOCK_SIZE, item["size"] - index * BLOCK_SIZE)
            if block.exists() and block.stat().st_size == size:
                done += size
            todo.append((item, index))
    todo.sort(key=lambda pair: (pair[0]["size"], pair[1]))
    stop = threading.Event()
    state_lock = threading.Lock()
    started = time.monotonic()
    initial = done
    errors = []
    state = {"status":"downloading", "total_bytes":total, "downloaded_bytes":done,
             "verified_files":completed, "file_count":len(items), "destination":str(DEST), "errors":errors}

    def report(final=False):
        elapsed = time.monotonic() - started
        with state_lock:
            state.update(downloaded_bytes=done, verified_files=list(completed), elapsed_seconds=round(elapsed,1), errors=list(errors))
            atomic_json(PROGRESS, state)
        speed = (done-initial)/max(elapsed,1)/1e6
        print(f"{done/1e9:.2f}/{total/1e9:.2f} GB ({100*done/max(total,1):.1f}%), verified {len(completed)}/{len(items)}, average {speed:.2f} MB/s", flush=True)

    def reporter():
        while not stop.wait(15):
            report()

    reporter_thread = threading.Thread(target=reporter, daemon=True)
    reporter_thread.start()
    report()
    try:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(transfer, item, index, stop): item for item,index in todo}
            for future in as_completed(futures):
                item = futures[future]
                try:
                    done += future.result()
                    pending_counts[item["path"]] -= 1
                    if pending_counts[item["path"]] == 0:
                        assemble(item)
                        completed.append(item["path"])
                        print("Verified: " + item["path"], flush=True)
                except Exception as exc:
                    errors.append(str(exc))
                    print("Transfer error: " + str(exc), flush=True)
        if errors:
            state["status"] = "incomplete"
            raise RuntimeError(f"{len(errors)} transfer errors; completed blocks retained for resume")
        state["status"] = "completed"
        atomic_json(DEST / ".download-complete.json", {"manifest":str(MANIFEST), "files":completed, "total_bytes":total})
    finally:
        stop.set()
        reporter_thread.join(timeout=2)
        report(final=True)
    return state


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=4, help="并行下载数，默认 4，范围 1–24")
    parser.add_argument("--verify-only", action="store_true", help="只校验本地模型，不访问网络")
    args = parser.parse_args()
    if not 1 <= args.workers <= 24:
        parser.error("workers must be between 1 and 24")
    if args.verify_only:
        verify_local()
    else:
        run(args.workers)
