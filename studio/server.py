import json
import os
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .audio import prepare_reference
from .engine import ROOT, MODEL_NAME as QWEN_MODEL_NAME, readiness as qwen_readiness
from .index_engine import readiness, MODEL_NAME
from .jobs import JobManager, save_json
from .model_ids import INDEX_ENGINE_ID, QWEN_ENGINE_ID
from .restart import restart_process
from .text import MAX_TEXT, split_text
from .video import VideoManager
from .video_routes import video_router


class TextRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TEXT)
    segment_length: int = Field(default=80, ge=24, le=160)


Emotion = Literal["happy", "angry", "sad", "afraid", "disgusted", "melancholic", "surprised", "calm"]


class PlanRequest(TextRequest):
    emotion_mode: Literal["auto", "manual", "reference"] = "auto"
    emotion: Emotion = "calm"
    emotion_strength: float = Field(default=0.6, ge=0, le=1)
    direction: str = Field(default="", max_length=300)
    contextual: bool = True


class SegmentOverride(BaseModel):
    index: int = Field(ge=0, le=50000)
    emotion: Literal["reference", "happy", "angry", "sad", "afraid", "disgusted", "melancholic", "surprised", "calm"]
    strength: float = Field(ge=0, le=1)


class GenerateRequest(PlanRequest):
    title: str = Field(default="", max_length=100)
    voice_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    speed: float = Field(default=1, ge=0.75, le=1.3)
    gap: float = Field(default=0.3, ge=0, le=2)
    language: Literal["Chinese", "English", "auto"] = "Chinese"
    seed: int = Field(default=42, ge=0, le=2147480000)
    mode: Literal["short", "long", "video"] = "short"
    plan_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    segment_overrides: list[SegmentOverride] = Field(default_factory=list, max_length=2200)


class ModelRequest(BaseModel):
    engine: Literal["index-tts-2.5", "qwen3-tts"]


def create_app(data_dir=None, engine=None, engine_factories=None, restart_callback=None):
    data = Path(data_dir or os.environ.get("STUDIO_DATA_DIR", ROOT / "data"))
    voices = data / "voices"
    voices.mkdir(parents=True, exist_ok=True)
    manager = JobManager(data, engine, engine_factories)
    videos = VideoManager(data, manager)
    service_instance = uuid.uuid4().hex

    def model_catalog():
        index = readiness()
        qwen = qwen_readiness()
        return [
            {"id": INDEX_ENGINE_ID, "name": MODEL_NAME, "description": "情绪分析与逐段控制", "supports_emotion": True, **index},
            {"id": QWEN_ENGINE_ID, "name": QWEN_MODEL_NAME, "description": "速度优先的自然克隆", "supports_emotion": False, **qwen},
        ]

    def active_model():
        return next(item for item in model_catalog() if item["id"] == manager.selected_engine_id)

    @asynccontextmanager
    async def lifespan(app):
        yield
        videos.shutdown()
        manager.shutdown()
        if hasattr(manager.engine, "close"):
            manager.engine.close()
        if manager.legacy_engine is not None:
            manager.legacy_engine.close()

    app = FastAPI(title="本声 · 私人录音棚", lifespan=lifespan)
    app.state.manager = manager
    app.state.videos = videos
    app.include_router(video_router(videos))
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"])

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin:
            parsed = urlsplit(origin)
            if parsed.netloc != request.headers.get("host") or parsed.scheme not in ("http", "https"):
                return JSONResponse({"detail": "请从本地工作台操作。"}, status_code=403)
        if request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "请从本地工作台操作。"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(ValueError)
    async def invalid_value(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    def voice_record(identifier):
        if len(identifier) != 32 or any(c not in "0123456789abcdef" for c in identifier):
            raise HTTPException(404, "声音不存在。")
        path = voices / identifier / "voice.json"
        if not path.is_file():
            raise HTTPException(404, "声音不存在。")
        return json.loads(path.read_text(encoding="utf-8"))

    def get_job(identifier):
        try:
            return manager.get(identifier)
        except KeyError:
            raise HTTPException(404, "任务不存在。")

    @app.get("/api/status")
    def status():
        selected = active_model()
        return {**selected, "model_state": manager.engine.state,
                "model_error": manager.engine.error, "model": selected["name"],
                "engine": selected["id"], "models": model_catalog(),
                "device":getattr(manager.engine,"device",None), "local_only": True, "video_active": videos.has_active_jobs(),
                "service_instance": service_instance}

    @app.post("/api/engine/select")
    def select_engine(request: ModelRequest):
        try:
            manager.select_engine(request.engine)
        except ValueError as exc:
            if manager.has_active_jobs():
                raise HTTPException(409, str(exc))
            raise
        return status()

    @app.post("/api/service/restart", status_code=202)
    def restart_service():
        with manager.lock:
            if manager.has_active_jobs() or videos.has_active_jobs():
                raise HTTPException(409, "还有任务在执行或排队，请完成或停止后再重启服务。")
            (restart_callback or (lambda: restart_process(manager)))()
        return {"status": "restarting", "service_instance": service_instance}

    @app.get("/api/voices")
    def list_voices():
        records = []
        for path in voices.glob("*/voice.json"):
            try:
                records.append(json.loads(path.read_text(encoding="utf-8")))
            except (ValueError, OSError):
                continue
        return sorted(records, key=lambda r: r["created_at"], reverse=True)

    @app.post("/api/engine/unload")
    def unload_engine():
        with manager.lock:
            if manager.has_active_jobs():
                raise HTTPException(409, "还有任务在执行或排队，请完成或停止后再释放内存。")
            if hasattr(manager.engine, "close"):
                manager.engine.close()
            if manager.legacy_engine is not None:
                manager.legacy_engine.close()
        return {"status":"unloaded"}

    @app.post("/api/voices", status_code=201)
    def upload_voice(name: str = Form(...), transcript: str = Form(""), audio: UploadFile = File(...)):
        from datetime import datetime, timezone
        if not name.strip() or len(name.strip()) > 60:
            raise ValueError("请给声音起一个 1–60 字的名字。")
        if len(transcript) > 2000:
            raise ValueError("参考录音备注最多 2,000 字。")
        suffix = Path(audio.filename or "").suffix.lower()
        if suffix not in {".wav", ".mp3", ".m4a", ".flac", ".aac", ".ogg", ".webm", ".aiff", ".aif", ".mp4"}:
            raise ValueError("请上传 WAV、MP3、M4A、FLAC 或常见录音文件。")
        identifier = uuid.uuid4().hex
        directory = voices / identifier
        directory.mkdir()
        source = directory / ("original" + suffix)
        try:
            size = 0
            with source.open("wb") as target:
                while chunk := audio.file.read(1024 * 1024):
                    size += len(chunk)
                    if size > 40 * 1024 * 1024:
                        raise ValueError("参考录音文件不能超过 40 MB。")
                    target.write(chunk)
            info = prepare_reference(source, directory / "reference.wav")
            record = {"id": identifier, "name": name.strip(), "transcript": transcript.strip(),
                      "created_at": datetime.now(timezone.utc).isoformat(), **info}
            save_json(directory / "voice.json", record)
            return record
        except Exception:
            # This directory contains only the incomplete upload created by this request.
            shutil.rmtree(directory)
            raise
        finally:
            audio.file.close()

    @app.get("/api/voices/{identifier}/audio")
    def voice_audio(identifier: str):
        voice_record(identifier)
        return FileResponse(voices / identifier / "reference.wav", media_type="audio/wav")

    @app.post("/api/preview")
    def preview(request: TextRequest):
        return {"segments": split_text(request.text, request.segment_length)}

    @app.post("/api/jobs", status_code=201)
    def generate(request: GenerateRequest):
        voice = voice_record(request.voice_id)
        selected = active_model()
        if engine is None and not all(selected[key] for key in ("model_downloaded", "runtime_ready")):
            raise ValueError(f"{selected['name']} 模型或运行环境尚未准备好，请先运行「安装环境.command」。")
        if manager.selected_engine_id == QWEN_ENGINE_ID and request.emotion_mode != "reference":
            raise ValueError("Qwen 不支持逐段情绪控制，请选择“跟随参考录音”或切换到 IndexTTS-2.5。")
        return manager.create(request.model_dump(), voice)

    @app.post("/api/plans", status_code=201)
    def analyze(request: PlanRequest):
        if manager.selected_engine_id != INDEX_ENGINE_ID:
            raise ValueError("Qwen 不支持语气分析，请切换到 IndexTTS-2.5。")
        if engine is None and not all(readiness()[key] for key in ("model_downloaded", "runtime_ready")):
            raise ValueError("IndexTTS 模型或运行环境尚未准备好。")
        return manager.create_plan(request.model_dump())

    @app.get("/api/jobs")
    def list_jobs():
        return manager.recent()

    @app.get("/api/jobs/{identifier}")
    def job(identifier: str):
        return get_job(identifier)

    @app.post("/api/jobs/{identifier}/cancel")
    def cancel(identifier: str):
        get_job(identifier)
        return manager.cancel(identifier)

    @app.post("/api/jobs/{identifier}/resume")
    def resume(identifier: str):
        get_job(identifier)
        return manager.resume(identifier)

    @app.get("/api/jobs/{identifier}/files/{filename}")
    def download(identifier: str, filename: str):
        job = get_job(identifier)
        if filename not in {"narration.wav", "narration.mp3", "captions.srt", "script.txt", "metadata.json", "voice-studio.zip"} or job["status"] != "completed":
            raise HTTPException(404, "文件尚未生成。")
        media = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".srt": "application/x-subrip", ".zip": "application/zip", ".txt": "text/plain", ".json": "application/json"}
        suffix = Path(filename).suffix
        return FileResponse(manager.directory / identifier / filename, media_type=media[suffix], filename=filename,
                            content_disposition_type="inline" if suffix in {".wav", ".mp3"} else "attachment")

    @app.get("/")
    def index():
        return FileResponse(ROOT / "web" / "index.html")

    app.mount("/static", StaticFiles(directory=ROOT / "web"), name="static")
    return app
