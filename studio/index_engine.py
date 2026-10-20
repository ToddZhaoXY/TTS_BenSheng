"""Cancellable JSON-line bridge to a separate, persistent Python 3.11 process."""
import json
import os
import queue
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path

import soundfile as sf

from .errors import Cancelled
from .model_ids import INDEX_ENGINE_ID

ROOT = Path(__file__).resolve().parent.parent
INDEX_PATH = ROOT / "index-tts-main" / "checkpoints"
INDEX_PYTHON = ROOT / ".venv-index" / "bin" / "python"
MODEL_NAME = "IndexTTS-2.5"
PROTOCOL = "@@VOICE_STUDIO@@"


def readiness():
    required = ["gpt.pth", "codec.pth", "s2mel.pth", "config.yaml", "feat1.pt", "feat2.pt", "wav2vec2bert_stats.pt", "multilingual_zh_ja_yue_char_del.tiktoken", "qwen0.6bemo4-merge/model.safetensors", "hf_cache/w2v-bert-2.0/model.safetensors", "hf_cache/bigvgan/bigvgan_generator.pt", "hf_cache/campplus_cn_common.bin"]
    missing = [name for name in required if not (INDEX_PATH / name).is_file()]
    environment = INDEX_PYTHON.exists() and (ROOT / ".venv-index" / "runtime-ready.json").exists()
    return {"model_downloaded":not missing, "runtime_ready":environment, "missing_files":missing}


class IndexEngine:
    name = MODEL_NAME
    engine_id = INDEX_ENGINE_ID
    analysis_model = "qwen0.6bemo4-merge"
    native_speed = True

    def __init__(self):
        self.process = None
        self.events = queue.Queue()
        self.state = "not_loaded"
        self.error = None
        self.device = None
        self.cancelled = lambda: False
        self.counter = 0

    def bind_cancel(self, callback):
        self.cancelled = callback

    def _start(self):
        if self.process and self.process.poll() is None:
            return
        if not INDEX_PYTHON.exists():
            raise RuntimeError("IndexTTS 环境尚未安装，请运行「安装环境.command」。")
        if not readiness()["model_downloaded"]:
            raise RuntimeError("IndexTTS 模型文件不完整，请先补齐模型文件。")
        logdir = ROOT / "data" / "logs"
        logdir.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1", TOKENIZERS_PARALLELISM="false", PYTORCH_ENABLE_MPS_FALLBACK="1", PYTHONUNBUFFERED="1", MPLCONFIGDIR=str(ROOT / ".cache" / "matplotlib"), NUMBA_CACHE_DIR=str(ROOT / ".cache" / "numba"), NLTK_DATA=str(ROOT / ".cache" / "nltk"), XDG_CACHE_HOME=str(ROOT / ".cache"))
        self.events = queue.Queue()
        process = subprocess.Popen([str(INDEX_PYTHON), str(ROOT / "scripts" / "index_worker.py")], cwd=ROOT / "index-tts-main", env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, start_new_session=True)
        self.process = process
        events = self.events

        def read_output():
            with (logdir / "index-engine.log").open("a", encoding="utf-8") as log:
                for line in process.stdout:
                    if line.startswith(PROTOCOL):
                        try:
                            events.put(json.loads(line[len(PROTOCOL):]))
                        except ValueError:
                            pass
                    else:
                        log.write(line)
                        log.flush()
            events.put({"type":"exit"})
        threading.Thread(target=read_output, daemon=True).start()

    def _request(self, operation, **kwargs):
        self._start()
        self.counter += 1
        identifier = self.counter
        self.process.stdin.write(json.dumps({"id":identifier,"op":operation,**kwargs}, ensure_ascii=False) + "\n")
        self.process.stdin.flush()
        started = time.monotonic()
        while True:
            if self.cancelled():
                self.close()
                raise Cancelled()
            if time.monotonic() - started > 1800:
                self.close()
                raise RuntimeError("这一段运行超过 30 分钟，已停止。请缩短分段后重试。")
            try:
                event = self.events.get(timeout=0.2)
            except queue.Empty:
                if self.process is None or self.process.poll() is not None:
                    self.state = "error"
                    raise RuntimeError("声音引擎意外退出。可能内存不足；请关闭占用较大的应用后继续任务。详细信息见 data/logs/index-engine.log。")
                continue
            if event.get("type") == "exit":
                self.state = "error"
                raise RuntimeError("声音引擎启动或运行失败，详情见 data/logs/index-engine.log。")
            if event.get("id") != identifier:
                continue
            if event.get("type") == "error":
                self.state = "error"
                self.error = event.get("message", "模型运行失败。")
                self.close(preserve_error=True)
                raise RuntimeError(self.error)
            if event.get("type") == "result":
                self.device = event.get("device", self.device)
                self.error = None
                return event

    def load(self):
        if self.state == "ready" and self.process and self.process.poll() is None:
            return
        self.state = "loading"
        self._request("load")
        self.state = "ready"

    def analyze(self, text, previous="", following="", direction=""):
        self.state = "analyzing"
        result = self._request("analyze", text=text, previous=previous, following=following, direction=direction)
        self.state = "analysis_ready"
        return result["vector"]

    def synthesize(self, text, reference, transcript, language, seed, cancelled, *, emotion=None, speed=1.0):
        self.bind_cancel(cancelled)
        self.load()
        tempdir = ROOT / "data" / "engine-temp"
        tempdir.mkdir(parents=True, exist_ok=True)
        destination = tempdir / f"segment-{uuid.uuid4().hex}.wav"
        try:
            self._request("synthesize", text=text, reference=str(reference.resolve()), language=language, seed=seed, emotion=emotion, speed=speed, output=str(destination))
            waveform, rate = sf.read(destination, dtype="float32")
            return waveform, rate
        finally:
            destination.unlink(missing_ok=True)

    def close(self, preserve_error=False):
        process, self.process = self.process, None
        if process and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=3)
        self.state = "error" if preserve_error else "not_loaded"
        if process and process.stdin:
            process.stdin.close()
