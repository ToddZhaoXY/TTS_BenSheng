import copy
import json
import logging
import threading
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import soundfile as sf

from .audio import assemble, write_segment
from .engine import Cancelled, VoiceEngine, QwenEngine
from .emotion import EMOTIONS, automatic, complete_plan, fingerprint, preset, valid_delivery
from .migrations import repair_job
from .model_ids import INDEX_ENGINE_ID, QWEN_ENGINE_ID, engine_id
from .text import build_srt, split_text

logger = logging.getLogger(__name__)
ACTIVE = {"queued", "planning", "loading", "generating", "exporting", "cancelling"}


def save_json(path: Path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


class JobManager:
    def __init__(self, data: Path, engine=None, engine_factories=None):
        self.data = data
        self.directory = data / "jobs"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.settings_path = data / "settings.json"
        self.engine_factories = engine_factories or {INDEX_ENGINE_ID: VoiceEngine, QWEN_ENGINE_ID: QwenEngine}
        selected = INDEX_ENGINE_ID
        if engine is None and self.settings_path.is_file():
            try:
                stored = json.loads(self.settings_path.read_text(encoding="utf-8")).get("engine")
                if stored in self.engine_factories:
                    selected = stored
            except (ValueError, OSError):
                logger.warning("Ignored invalid studio settings")
        self.engine = engine or self.engine_factories[selected]()
        engine_id(self.engine)
        self.legacy_engine = None
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="voice-inference")
        self.lock = threading.RLock()
        self.jobs = {}
        self.events = {}
        for path in self.directory.glob("*/job.json"):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
                job = repair_job(path, job, save_json)
                if job["status"] in ACTIVE:
                    job.update(status="interrupted", message="上次运行已中断，已完成的段落仍然保留。")
                    save_json(path, job)
                self.jobs[job["id"]] = job
            except (ValueError, KeyError, OSError):
                logger.warning("Skipped invalid job record %s", path.name)

    @property
    def selected_engine_id(self):
        return engine_id(self.engine)

    def has_active_jobs(self):
        return any(job.get("status") in ACTIVE for job in self.jobs.values())

    def select_engine(self, identifier):
        if identifier not in self.engine_factories:
            raise ValueError("不支持这个声音模型。")
        with self.lock:
            if self.has_active_jobs():
                raise ValueError("还有任务在执行或排队，请完成或停止后再切换模型。")
            if identifier == self.selected_engine_id:
                return identifier
            replacement = self.engine_factories[identifier]()
            if engine_id(replacement) != identifier:
                raise ValueError("模型标识不匹配，已取消切换。")
            if hasattr(self.engine, "close"):
                self.engine.close()
            if self.legacy_engine is not None and hasattr(self.legacy_engine, "close"):
                self.legacy_engine.close()
                self.legacy_engine = None
            self.engine = replacement
            save_json(self.settings_path, {"engine": identifier})
            return identifier

    def get(self, identifier):
        with self.lock:
            return copy.deepcopy(self.jobs[identifier])

    def recent(self):
        with self.lock:
            return [copy.deepcopy(j) for j in sorted(self.jobs.values(), key=lambda j: j["created_at"], reverse=True)[:30]]

    def update(self, identifier, **changes):
        with self.lock:
            self.jobs[identifier].update(changes)
            save_json(self.directory / identifier / "job.json", self.jobs[identifier])

    def create(self, request, voice):
        with self.lock:
            if sum(j["status"] in ACTIVE for j in self.jobs.values()) >= 3:
                raise ValueError("已有 3 个任务正在处理，请等待或取消后再添加。")
            segments = split_text(request["text"], request["segment_length"])
            plan_provenance = {}
            if request.get("plan_id"):
                if self.selected_engine_id != INDEX_ENGINE_ID:
                    raise ValueError("Qwen 不支持复用语气分析，请切换到 IndexTTS-2.5。")
                plan = self.jobs.get(request["plan_id"])
                if (not plan or plan.get("kind") != "analysis" or plan["status"] != "planned"
                        or plan.get("engine") != INDEX_ENGINE_ID or not complete_plan(plan)):
                    raise ValueError("这份语气分析尚未完成，请重新分析。")
                if (plan.get("plan_fingerprint") != fingerprint(request)
                        or [{k: s.get(k) for k in ("text", "paragraph_end")} for s in plan["segments"]] != segments):
                    raise ValueError("文稿或语气设置已改变，请重新分析后再生成。")
                segments = copy.deepcopy(plan["segments"])
                plan_provenance = {key: plan[key] for key in ("analysis_model", "analysis_device") if key in plan}
            for override in request.get("segment_overrides", []):
                index = override["index"]
                if index < 0 or index >= len(segments):
                    raise ValueError("段落序号超出了文稿范围。")
                segments[index]["delivery"] = preset(override["emotion"], override["strength"], "override")
            identifier = uuid.uuid4().hex
            job = {
                "id": identifier, "title": request["title"].strip() or request["text"].strip()[:24],
                "created_at": datetime.now(timezone.utc).isoformat(),
                "status": "queued", "message": "已加入队列", "completed": 0, "analyzed": 0,
                "engine": engine_id(self.engine), "kind": "analysis" if request.get("analysis_only") else "speech",
                "plan_fingerprint": fingerprint(request),
                "total": len(segments), "segments": segments, "request": request,
                "voice": {"id": voice["id"], "name": voice["name"], "transcript": voice["transcript"]},
                "duration": None, "elapsed": 0, "timeline": [],
                **plan_provenance,
            }
            (self.directory / identifier / "chunks").mkdir(parents=True)
            self.jobs[identifier] = job
            self.events[identifier] = threading.Event()
            save_json(self.directory / identifier / "job.json", job)
            self.executor.submit(self.run, identifier)
            return copy.deepcopy(job)

    def create_plan(self, request):
        request = {"title":"文稿语气分析", "speed":1.0, "gap":0.3, "language":"Chinese", "seed":42, "mode":"long", **request, "analysis_only":True}
        return self.create(request, {"id":"", "name":"语气分析", "transcript":""})

    def prepare_plan(self, identifier, engine, cancelled):
        job = self.get(identifier)
        segments = job["segments"]
        request = job["request"]
        mode = request.get("emotion_mode", "reference")
        strength = request.get("emotion_strength", 0.6)
        self.update(identifier, status="planning", message="正在分析文稿语气…")
        for index, segment in enumerate(segments):
            analysis_provenance = {}
            if cancelled():
                raise Cancelled()
            if not valid_delivery(segment.get("delivery")):
                if mode == "reference":
                    segment["delivery"] = preset("reference", 0, "reference")
                elif mode == "manual":
                    segment["delivery"] = preset(request.get("emotion", "calm"), strength)
                else:
                    self.update(identifier, message=f"正在分析第 {index+1} / {len(segments)} 段的情绪")
                    context = request.get("contextual", True)
                    previous = segments[index-1]["text"] if context and index else ""
                    following = segments[index+1]["text"] if context and index+1 < len(segments) else ""
                    values = engine.analyze(segment["text"], previous, following, request.get("direction", ""))
                    segment["delivery"] = automatic(values, strength)
                    analysis_provenance = {"analysis_model": getattr(engine, "analysis_model", None),
                                           "analysis_device": getattr(engine, "device", None)}
            if not valid_delivery(segment.get("delivery")):
                raise ValueError("段落语气结果不完整，请重新分析。")
            self.update(identifier, segments=copy.deepcopy(segments), analyzed=index+1, **analysis_provenance)
        return segments

    def cancel(self, identifier):
        with self.lock:
            if self.jobs[identifier]["status"] not in ACTIVE:
                return self.get(identifier)
            self.events[identifier].set()
            self.update(identifier, status="cancelling", message="正在停止，已完成的段落会保留。")
            return self.get(identifier)

    def resume(self, identifier):
        with self.lock:
            if self.jobs[identifier]["status"] not in {"failed", "cancelled", "interrupted"}:
                raise ValueError("这个任务当前不需要继续。")
            if sum(j["status"] in ACTIVE for j in self.jobs.values()) >= 3:
                raise ValueError("队列已满，请稍后继续。")
            self.events[identifier] = threading.Event()
            self.update(identifier, status="queued", message="等待继续未完成的段落。")
            self.executor.submit(self.run, identifier)
            return self.get(identifier)

    def run(self, identifier):
        start = time.monotonic()
        job = self.get(identifier)
        directory = self.directory / identifier
        cancelled = self.events[identifier].is_set
        engine = self.engine
        try:
            if cancelled():
                raise Cancelled()
            identifier_engine = job.get("engine", QWEN_ENGINE_ID)
            if identifier_engine not in {INDEX_ENGINE_ID, QWEN_ENGINE_ID}:
                raise ValueError("任务的模型标识无法识别，已停止，未切换到其他模型。请新建任务。")
            if job.get("kind") == "analysis" and identifier_engine != INDEX_ENGINE_ID:
                raise ValueError("旧 Qwen 任务不支持语气分析，请新建 IndexTTS 分析任务。")
            if identifier_engine == engine_id(self.engine):
                if self.legacy_engine is not None:
                    self.legacy_engine.close()
                    self.legacy_engine = None
            else:
                if hasattr(self.engine, "close"):
                    self.engine.close()
                if self.legacy_engine is None or engine_id(self.legacy_engine) != identifier_engine:
                    if self.legacy_engine is not None and hasattr(self.legacy_engine, "close"):
                        self.legacy_engine.close()
                    self.legacy_engine = self.engine_factories[identifier_engine]()
                engine = self.legacy_engine
            if engine_id(engine) != identifier_engine:
                raise ValueError("模型调度校验失败，已停止任务。")
            if hasattr(engine, "bind_cancel"):
                engine.bind_cancel(cancelled)
            if identifier_engine == INDEX_ENGINE_ID:
                job["segments"] = self.prepare_plan(identifier, engine, cancelled)
                if not complete_plan(self.get(identifier)):
                    raise ValueError("语气分析结果不完整，请重新分析。")
            if cancelled():
                raise Cancelled()
            if job.get("kind") == "analysis":
                self.update(identifier, status="planned", message="语气分析完成，可以逐段调整后生成配音。", elapsed=round(time.monotonic()-start,1))
                return
            self.update(identifier, status="loading", message="正在准备声音模型，首次加载需要一些时间。")
            engine.load()
            self.update(identifier, execution_engine=engine_id(engine), device=getattr(engine, "device", None))
            for index, segment in enumerate(job["segments"]):
                if cancelled():
                    raise Cancelled()
                path = directory / "chunks" / f"{index:04}.wav"
                if path.exists():
                    try:
                        if sf.info(path).duration <= 0:
                            raise ValueError("Empty checkpoint")
                    except (RuntimeError, ValueError):
                        path.unlink(missing_ok=True)
                if not path.exists():
                    delivery = segment.get("delivery", {})
                    label = EMOTIONS.get(delivery.get("emotion"), "参考语气")
                    self.update(identifier, status="generating", completed=index, message=f"正在朗读第 {index + 1} / {job['total']} 段 · {label}")
                    options = {"emotion":delivery, "speed":job["request"]["speed"]} if getattr(engine,"native_speed",False) else {}
                    waveform, rate = engine.synthesize(
                        segment["text"], self.data / "voices" / job["voice"]["id"] / "reference.wav",
                        job["voice"]["transcript"], job["request"]["language"],
                        job["request"]["seed"] + index, cancelled, **options,
                    )
                    pending = path.with_name(path.stem + ".pending.wav")
                    write_segment(waveform, rate, pending, 1.0 if getattr(engine,"native_speed",False) else job["request"]["speed"])
                    pending.replace(path)
                self.update(identifier, completed=index + 1, elapsed=round(time.monotonic() - start, 1))
            if cancelled():
                raise Cancelled()
            self.update(identifier, status="exporting", message="正在合并音频并导出字幕。")
            timeline = assemble(directory, job["segments"], job["request"]["gap"])
            (directory / "captions.srt").write_text(build_srt(timeline), encoding="utf-8")
            (directory / "script.txt").write_text(job["request"]["text"], encoding="utf-8")
            metadata = {"title": job["title"], "voice": job["voice"]["name"], "generated_with": engine_id(engine), "device":getattr(engine,"device",None), "caption_timing": "segment-level, not word-aligned", "timeline": timeline, "emotion_plan":job["segments"], "settings": {k: v for k, v in job["request"].items() if k != "text"}}
            if job.get("provenance_note"):
                metadata.update(provenance_note=job["provenance_note"], requested_engine=job.get("requested_engine"))
            save_json(directory / "metadata.json", metadata)
            with zipfile.ZipFile(directory / "voice-studio.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name in ("narration.wav", "narration.mp3", "captions.srt", "script.txt", "metadata.json"):
                    archive.write(directory / name, name)
            if cancelled():
                raise Cancelled()
            self.update(identifier, status="completed", message=job.get("provenance_note") or "配音已完成，可以试听与下载。", timeline=timeline,
                        duration=round(timeline[-1]["end"], 3), elapsed=round(time.monotonic() - start, 1))
        except Cancelled:
            self.update(identifier, status="cancelled", message="已停止。点击继续可复用已完成的段落。", elapsed=round(time.monotonic() - start, 1))
        except Exception as exc:
            logger.exception("Job %s failed", identifier)
            message = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else "生成失败，请查看服务终端日志后重试。"
            self.update(identifier, status="failed", message=message[:350], elapsed=round(time.monotonic() - start, 1))

    def shutdown(self):
        for event in self.events.values():
            event.set()
        self.executor.shutdown(wait=False, cancel_futures=True)
