import io
import json
import re
import time
import zipfile

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from studio.jobs import JobManager
from studio.server import create_app
from studio.text import build_srt, split_text, timestamp


@pytest.mark.parametrize("text", [
    "第一段，轻轻地说。第二句：“今天真好！”\n\n第三句还没有句号",
    "Hello world. A price of 3.14 is correct! Don't split 2026.09.27 incorrectly.",
    "没有标点的中文长段落" * 100,
    "中文 English mixed，标点、引号“你好”。\nNext paragraph with multiple words.",
])
def test_segmentation_preserves_content(text):
    segments = split_text(text, 24)
    compact = lambda value: re.sub(r"\s", "", value)
    assert compact("".join(s["text"] for s in segments)) == compact(text)
    assert all(0 < len(s["text"]) <= 24 for s in segments)
    assert segments[-1]["paragraph_end"]


def test_limits_and_subtitle_rounding():
    with pytest.raises(ValueError):
        split_text(" " * 10)
    with pytest.raises(ValueError):
        split_text("字" * 50001)
    assert timestamp(59.9999) == "00:01:00,000"
    assert timestamp(3661.001) == "01:01:01,001"
    assert "00:00:00,000 --> 00:00:01,250" in build_srt([{"text":"你好", "start":0, "end":1.25}])


def wav_bytes(seconds=4, rate=24000):
    buf = io.BytesIO()
    wave = 0.2 * np.sin(2 * np.pi * 220 * np.arange(int(rate * seconds)) / rate)
    sf.write(buf, wave, rate, format="WAV")
    return buf.getvalue()


class TestEngine:
    __test__ = False
    engine_id = "index-tts-2.5"
    state = "not_loaded"
    error = None

    def __init__(self, fail_second=False):
        self.calls = []
        self.fail_second = fail_second

    def load(self):
        self.state = "ready"

    def analyze(self, text, previous="", following="", direction=""):
        return {"calm":1.0}

    def synthesize(self, text, reference, transcript, language, seed, cancelled):
        self.calls.append(text)
        if self.fail_second and len(self.calls) == 2:
            raise ValueError("Intentional failure to verify resume")
        return 0.2 * np.sin(2 * np.pi * 220 * np.arange(12000) / 24000), 24000


def wait_job(client, identifier):
    for _ in range(200):
        job = client.get(f"/api/jobs/{identifier}").json()
        if job["status"] in {"completed", "planned", "failed", "cancelled"}:
            return job
        time.sleep(0.025)
    pytest.fail("Job did not terminate")


def upload(client, seconds=4):
    return client.post("/api/voices", data={"name":"测试用合成音", "transcript":"这是一段测试音频。"}, files={"audio":("sample.wav",wav_bytes(seconds),"audio/wav")})


def test_audio_pipeline_export_and_resume(tmp_path):
    engine = TestEngine(fail_second=True)
    with TestClient(create_app(tmp_path, engine)) as client:
        voice = upload(client)
        assert voice.status_code == 201, voice.text
        voice_id = voice.json()["id"]
        request = {"voice_id":voice_id, "text":"第一段测试文字。\n第二段测试文字。\n最后一段测试文字。", "gap":0.3, "speed":1.2, "segment_length":24}
        created = client.post("/api/jobs", json=request)
        assert created.status_code == 201, created.text
        identifier = created.json()["id"]
        failed = wait_job(client, identifier)
        assert failed["status"] == "failed" and failed["completed"] == 1
        first = tmp_path / "jobs" / identifier / "chunks" / "0000.wav"
        original_mtime = first.stat().st_mtime_ns
        assert client.post(f"/api/jobs/{identifier}/resume").status_code == 200
        completed = wait_job(client, identifier)
        assert completed["status"] == "completed", completed
        assert completed["completed"] == 3
        assert len(engine.calls) == 4  # First completed segment was NOT regenerated.
        assert first.stat().st_mtime_ns == original_mtime
        assert completed["timeline"][1]["start"] - completed["timeline"][0]["end"] == pytest.approx(.48)
        audio = client.get(f"/api/jobs/{identifier}/files/narration.wav")
        info = sf.info(io.BytesIO(audio.content))
        assert info.samplerate == 48000 and info.subtype == "PCM_24"
        assert info.duration == pytest.approx(completed["duration"], abs=.002)
        assert client.get(f"/api/jobs/{identifier}/files/narration.mp3").content[:3] == b"ID3"
        archive = zipfile.ZipFile(io.BytesIO(client.get(f"/api/jobs/{identifier}/files/voice-studio.zip").content))
        assert set(archive.namelist()) == {"narration.wav","narration.mp3","captions.srt","script.txt","metadata.json"}
        assert archive.read("script.txt").decode() == request["text"]
        assert "00:00:00,000 -->" in archive.read("captions.srt").decode()


def test_upload_validation_and_local_request_boundaries(tmp_path):
    with TestClient(create_app(tmp_path, TestEngine())) as client:
        assert upload(client, 1).status_code == 400
        assert list((tmp_path / "voices").iterdir()) == []
        assert client.post("/api/voices", data={"name":"x","transcript":"a"}, files={"audio":("a.exe",b"x","application/octet-stream")}).status_code == 400
        assert client.post("/api/preview", json={"text":"你好"}, headers={"Origin":"https://example.com"}).status_code == 403
        assert client.get("/api/voices", headers={"Host":"malicious.example"}).status_code == 400
        assert client.get("/api/voices/not-a-voice/audio").status_code == 404
        assert client.get("/api/jobs/missing/files/narration.wav").status_code == 404
        assert client.post("/api/preview", json={"text":"   "}).status_code == 400
        assert client.post("/api/jobs", json={"voice_id":"x", "text":"你好"}).status_code == 422


def test_restart_marks_unfinished_work_recoverable(tmp_path):
    path = tmp_path / "jobs" / ("a" * 32)
    path.mkdir(parents=True)
    (path / "job.json").write_text(json.dumps({"id":"a" * 32,"status":"generating"}))
    manager = JobManager(tmp_path, TestEngine())
    try:
        assert manager.get("a" * 32)["status"] == "interrupted"
    finally:
        manager.shutdown()


def test_emotion_plan_edits_and_stale_rejection(tmp_path):
    engine = TestEngine()
    with TestClient(create_app(tmp_path, engine)) as client:
        voice_id = upload(client).json()["id"]
        text = "门外的脚步声，突然停了。\n她终于松了一口气。"
        plan_input = {"text":text,"segment_length":40,"emotion_mode":"auto","emotion_strength":0.6}
        response = client.post("/api/plans",json=plan_input)
        assert response.status_code == 201
        plan_id = response.json()["id"]
        plan = wait_job(client, plan_id)
        assert plan["status"] == "planned", plan
        assert not engine.calls  # Analysis does not generate audio or need a voice.
        assert plan["segments"][0]["delivery"]["source"] == "auto"
        request = {**plan_input,"voice_id":voice_id,"plan_id":plan_id,"segment_overrides":[{"index":0,"emotion":"afraid","strength":0.4}]}
        created = client.post("/api/jobs",json=request)
        assert created.status_code == 201, created.text
        job = wait_job(client,created.json()["id"])
        assert job["status"] == "completed",job
        assert job["segments"][0]["delivery"]["emotion"] == "afraid"
        assert job["segments"][0]["delivery"]["strength"] == .4
        assert job["segments"][1]["delivery"]["emotion"] == "calm"
        assert client.post("/api/jobs",json={**request,"text":text+"改变"}).status_code == 400
        assert client.post("/api/jobs",json={**request,"emotion_strength":0.9}).status_code == 400
        assert client.post("/api/jobs",json={**request,"segment_overrides":[{"index":20,"emotion":"calm","strength":.5}]}).status_code == 400
        assert client.post("/api/jobs",json={**request,"emotion_strength":2}).status_code == 422


def test_emotion_vector_is_bounded():
    from studio.emotion import normalize_vector
    v=normalize_vector([1.2,0.8,0,0,0,0,0,0])
    assert sum(v) == pytest.approx(1,abs=1e-5)
    assert normalize_vector([0]*8)[-1] == 1
    with pytest.raises(ValueError):
        normalize_vector([float('nan')]*8)


def test_native_speed_and_emotion_reach_engine(tmp_path):
    class NativeEngine(TestEngine):
        native_speed=True
        received=None

        def synthesize(self,*args,emotion=None,speed=1):
            self.received={"emotion":emotion,"speed":speed}
            return super().synthesize(*args)

    engine=NativeEngine()
    with TestClient(create_app(tmp_path,engine)) as client:
        voice=client.post('/api/voices',data={'name':'无需逐字稿'},files={'audio':('voice.wav',wav_bytes(),'audio/wav')})
        assert voice.status_code == 201,voice.text
        job=client.post('/api/jobs',json={'voice_id':voice.json()['id'],'text':'一句测试文字。','speed':1.25,'emotion_mode':'manual','emotion':'angry','emotion_strength':.7}).json()
        completed=wait_job(client,job['id'])
        assert completed['status']=='completed',completed
        assert engine.received['speed']==1.25
        assert engine.received['emotion']['vector'][1]==1
        assert engine.received['emotion']['strength']==.7
        # Native output is not time-stretched a second time by the post-processor.
        assert completed['duration']==pytest.approx(.5,abs=.002)
        metadata=client.get(f"/api/jobs/{job['id']}/files/metadata.json").json()
        assert metadata['generated_with']=='index-tts-2.5'


def test_cancel_during_analysis_preserves_resumable_job(tmp_path):
    import threading
    from studio.errors import Cancelled
    class SlowEngine(TestEngine):
        started=threading.Event()
        cancelled=lambda self:False

        def bind_cancel(self,callback):
            self.cancelled=callback

        def analyze(self,*args):
            self.started.set()
            for _ in range(100):
                if self.cancelled():
                    raise Cancelled()
                time.sleep(.01)
            return {'calm':1}

    engine=SlowEngine()
    with TestClient(create_app(tmp_path,engine)) as client:
        job=client.post('/api/plans',json={'text':'这是一段需要分析的测试。'}).json()
        assert engine.started.wait(2)
        assert client.post('/api/engine/unload').status_code==409
        assert client.post(f"/api/jobs/{job['id']}/cancel").status_code==200
        assert wait_job(client,job['id'])['status']=='cancelled'


class DispatchSpy(TestEngine):
    # Copy the real adapter identity so the historical display-name bug is exercised.
    from studio.index_engine import IndexEngine
    name = IndexEngine.name
    engine_id = IndexEngine.engine_id
    native_speed = True
    analysis_model = IndexEngine.analysis_model
    device = "test-device"

    def __init__(self):
        super().__init__()
        self.analysis_calls = []
        self.options = []
        self.closed = False

    def analyze(self, *args):
        self.analysis_calls.append(args)
        return {"angry": 1}

    def synthesize(self, *args, **kwargs):
        self.options.append(kwargs)
        return super().synthesize(*args)

    def close(self):
        self.closed = True


def default_factory(monkeypatch, spy):
    import studio.jobs as jobs
    import studio.server as server
    monkeypatch.setattr(jobs, "VoiceEngine", lambda: spy)
    def forbidden_fallback():
        pytest.fail("New Index tasks must never fall back to Qwen")
    monkeypatch.setattr(jobs, "QwenEngine", forbidden_fallback)
    monkeypatch.setattr(server, "readiness", lambda: {"model_downloaded": True, "runtime_ready": True})


def test_default_factory_dispatch_analysis_and_speech(tmp_path, monkeypatch):
    spy = DispatchSpy()
    default_factory(monkeypatch, spy)
    with TestClient(create_app(tmp_path)) as client:  # No injected-engine bypass.
        voice = upload(client).json()["id"]
        request = {"text": "第一段很平静。\n第二段真生气。\n第三段好开心。\n第四段有点害怕。", "emotion_mode": "auto"}
        plan_id = client.post("/api/plans", json=request).json()["id"]
        plan = wait_job(client, plan_id)
        assert plan["status"] == "planned", plan
        assert plan["engine"] == "index-tts-2.5"
        assert plan["analyzed"] == plan["total"] == len(spy.analysis_calls) == 4
        assert all(s["delivery"]["source"] == "auto" for s in plan["segments"])
        assert plan["analysis_model"] == "qwen0.6bemo4-merge"
        assert spy.analysis_calls[0][2] == plan["segments"][1]["text"]
        assert not spy.calls
        created = client.post("/api/jobs", json={**request, "voice_id": voice, "plan_id": plan_id, "speed": 1.2})
        assert created.status_code == 201, created.text
        speech = wait_job(client, created.json()["id"])
        assert speech["status"] == "completed", speech
        assert speech["execution_engine"] == "index-tts-2.5"
        assert speech["analyzed"] == speech["completed"] == 4
        assert len(spy.analysis_calls) == 4  # Reuses only a complete plan.
        assert len(spy.options) == 4
        assert all(o["emotion"]["emotion"] == "angry" and o["speed"] == 1.2 for o in spy.options)
        metadata = client.get(f"/api/jobs/{speech['id']}/files/metadata.json").json()
        assert metadata["generated_with"] == "index-tts-2.5"
        assert metadata["device"] == spy.device
        # Generating directly must perform analysis too.
        direct = client.post("/api/jobs", json={**request, "voice_id": voice}).json()
        assert wait_job(client, direct["id"])["status"] == "completed"
        assert len(spy.analysis_calls) == 8


@pytest.mark.parametrize("corruption", ["count", "missing", "vector", "strength", "text"])
def test_incomplete_or_corrupt_plan_cannot_be_reused(tmp_path, corruption):
    app = create_app(tmp_path, TestEngine())
    with TestClient(app) as client:
        voice = upload(client).json()["id"]
        request = {"text": "第一段。\n第二段。"}
        plan_id = client.post("/api/plans", json=request).json()["id"]
        plan = wait_job(client, plan_id)
        if corruption == "count":
            plan["analyzed"] = 0
        elif corruption == "missing":
            plan["segments"][0].pop("delivery")
        elif corruption == "vector":
            plan["segments"][0]["delivery"]["vector"] = [1]
        elif corruption == "strength":
            plan["segments"][0]["delivery"]["strength"] = 2
        else:
            plan["segments"][0]["text"] = "无关文稿"
        app.state.manager.update(plan_id, **plan)
        response = client.post("/api/jobs", json={**request, "voice_id": voice, "plan_id": plan_id})
        assert response.status_code == 400, response.text


def test_skipped_planner_cannot_claim_success(tmp_path, monkeypatch):
    spy = DispatchSpy()
    default_factory(monkeypatch, spy)
    app = create_app(tmp_path)
    manager = app.state.manager
    monkeypatch.setattr(manager, "prepare_plan", lambda identifier, *_: manager.get(identifier)["segments"])
    with TestClient(app) as client:
        job = client.post("/api/plans", json={"text": "这一段不能漏掉。"}).json()
        result = wait_job(client, job["id"])
        assert result["status"] == "failed"
        assert result["analyzed"] == 0


@pytest.mark.parametrize("unknown", ["future-model", "", None])
def test_unknown_explicit_engine_fails_closed(tmp_path, monkeypatch, unknown):
    spy = DispatchSpy()
    default_factory(monkeypatch, spy)
    path = tmp_path / "jobs" / ("b" * 32)
    path.mkdir(parents=True)
    (path / "job.json").write_text(json.dumps({"id": "b" * 32, "engine": unknown, "status": "failed", "kind": "speech"}))
    with TestClient(create_app(tmp_path)) as client:
        assert client.post(f"/api/jobs/{'b' * 32}/resume").status_code == 200
        result = wait_job(client, "b" * 32)
        assert result["status"] == "failed"
        assert "模型标识" in result["message"]
        assert not spy.calls and not spy.analysis_calls


def test_engine_requires_stable_id(tmp_path):
    class DisplayNameOnly:
        name = "IndexTTS-2.5"
    with pytest.raises(ValueError, match="模型标识"):
        JobManager(tmp_path, DisplayNameOnly())


def test_unstarted_malformed_job_can_restart_on_index(tmp_path):
    from studio.jobs import save_json
    identifier = "e" * 32
    directory = tmp_path / "jobs" / identifier
    directory.mkdir(parents=True)
    save_json(directory / "job.json", {"id": identifier, "engine": "indextts-2.5", "status": "queued",
                                      "kind": "speech", "completed": 0, "segments": split_text("尚未配音。", 80)})
    manager = JobManager(tmp_path, TestEngine())
    try:
        job = manager.get(identifier)
        assert job["engine"] == "index-tts-2.5" and job["status"] == "failed"
        assert job["analyzed"] == 0
    finally:
        manager.shutdown()


def test_migration_backs_up_and_preserves_audio_and_is_idempotent(tmp_path):
    from studio.jobs import save_json
    request = {"text": "测试段落。", "segment_length": 80}
    for number, kind in ((1, "analysis"), (2, "speech")):
        identifier = f"{number:032x}"
        directory = tmp_path / "jobs" / identifier
        directory.mkdir(parents=True)
        record = {"id": identifier, "engine": "indextts-2.5", "kind": kind,
                  "status": "planned" if kind == "analysis" else "completed", "analyzed": 0,
                  "total": 1, "segments": split_text(request["text"], 80), "request": request}
        save_json(directory / "job.json", record)
        if kind == "speech":
            (directory / "narration.wav").write_bytes(wav_bytes())
            save_json(directory / "metadata.json", {"generated_with": "indextts-2.5"})
            with zipfile.ZipFile(directory / "voice-studio.zip", "w") as archive:
                for name in ("narration.wav", "metadata.json"):
                    archive.write(directory / name, name)
    manager = JobManager(tmp_path, TestEngine())
    try:
        plan = manager.get(f"{1:032x}")
        assert plan["status"] == "failed" and plan["analyzed"] == 0
        assert plan["engine"] == "index-tts-2.5"
        assert plan["request"] == request
        speech = manager.get(f"{2:032x}")
        assert speech["status"] == "completed" and speech["engine"] == "qwen3-tts"
        assert "旧 Qwen" in speech["message"]
        speech_dir = tmp_path / "jobs" / speech["id"]
        backup = tmp_path / "backups" / "engine-dispatch-v1" / speech["id"]
        assert json.loads((backup / "job.json").read_text())["engine"] == "indextts-2.5"
        assert json.loads((speech_dir / "metadata.json").read_text())["generated_with"] == "qwen3-tts"
        with zipfile.ZipFile(speech_dir / "voice-studio.zip") as current, zipfile.ZipFile(backup / "voice-studio.zip") as old:
            assert current.read("narration.wav") == old.read("narration.wav") == (speech_dir / "narration.wav").read_bytes()
            assert json.loads(current.read("metadata.json"))["generated_with"] == "qwen3-tts"
        before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    finally:
        manager.shutdown()
    restarted = JobManager(tmp_path, TestEngine())
    restarted.shutdown()
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


@pytest.mark.parametrize("stored_id", ["missing", "qwen3-tts", "indextts-2.5"])
def test_legacy_or_misrouted_chunks_resume_only_on_qwen(tmp_path, monkeypatch, stored_id):
    from studio.jobs import save_json
    import studio.jobs as jobs
    class LegacySpy(TestEngine):
        engine_id = "qwen3-tts"
        native_speed = False
        def close(self):
            pass
    legacy = LegacySpy()
    index = DispatchSpy()
    default_factory(monkeypatch, index)
    monkeypatch.setattr(jobs, "QwenEngine", lambda: legacy)
    identifier = "c" * 32
    directory = tmp_path / "jobs" / identifier
    (directory / "chunks").mkdir(parents=True)
    first_chunk = directory / "chunks" / "0000.wav"
    first_chunk.write_bytes(wav_bytes())
    original = first_chunk.read_bytes()
    record = {"id": identifier, "status": "failed", "kind": "speech", "title": "旧任务",
              "voice": {"id": "d" * 32, "name": "测试", "transcript": "测试"},
              "request": {"text": "第一段。\n第二段。", "speed": 1, "gap": .3, "language": "Chinese", "seed": 42},
              "completed": 1, "total": 2, "segments": split_text("第一段。\n第二段。", 80)}
    if stored_id != "missing":
        record["engine"] = stored_id
    save_json(directory / "job.json", record)
    with TestClient(create_app(tmp_path)) as client:
        assert client.post(f"/api/jobs/{identifier}/resume").status_code == 200
        result = wait_job(client, identifier)
        assert result["status"] == "completed", result
        assert result["execution_engine"] == "qwen3-tts"
        assert legacy.calls == ["第二段。"] and not index.calls and not index.analysis_calls
        assert first_chunk.read_bytes() == original
        assert json.loads((directory / "metadata.json").read_text())["generated_with"] == "qwen3-tts"


class QwenTestEngine(TestEngine):
    engine_id = "qwen3-tts"
    name = "Qwen3-TTS 0.6B"
    native_speed = False

    def close(self):
        self.state = "not_loaded"


def test_model_switch_persists_and_new_jobs_use_selected_engine(tmp_path):
    index = TestEngine()
    qwen_instances = []
    def make_qwen():
        instance = QwenTestEngine()
        qwen_instances.append(instance)
        return instance
    factories = {"index-tts-2.5": TestEngine, "qwen3-tts": make_qwen}
    with TestClient(create_app(tmp_path, index, factories)) as client:
        status = client.get("/api/status").json()
        assert status["engine"] == "index-tts-2.5"
        assert {model["id"] for model in status["models"]} == {"index-tts-2.5", "qwen3-tts"}
        switched = client.post("/api/engine/select", json={"engine":"qwen3-tts"})
        assert switched.status_code == 200, switched.text
        assert switched.json()["engine"] == "qwen3-tts"
        assert switched.json()["supports_emotion"] is False
        assert json.loads((tmp_path / "settings.json").read_text())["engine"] == "qwen3-tts"
        assert client.post("/api/plans", json={"text":"Qwen 不应伪装分析。"}).status_code == 400
        voice_id = upload(client).json()["id"]
        rejected = client.post("/api/jobs", json={"voice_id":voice_id, "text":"不能静默忽略语气。"})
        assert rejected.status_code == 400
        created = client.post("/api/jobs", json={"voice_id":voice_id, "text":"使用快速模型。", "emotion_mode":"reference"})
        assert created.status_code == 201, created.text
        job = wait_job(client, created.json()["id"])
        assert job["status"] == "completed" and job["engine"] == "qwen3-tts"
        assert job["execution_engine"] == "qwen3-tts"
        assert qwen_instances[-1].calls == ["使用快速模型。"]
    restored = JobManager(tmp_path, engine_factories=factories)
    try:
        assert restored.selected_engine_id == "qwen3-tts"
    finally:
        restored.shutdown()


def test_switch_and_restart_are_blocked_by_active_work(tmp_path):
    restarted = []
    factories = {"index-tts-2.5": TestEngine, "qwen3-tts": QwenTestEngine}
    app = create_app(tmp_path, TestEngine(), factories, restart_callback=lambda: restarted.append(True))
    with TestClient(app) as client:
        manager = app.state.manager
        manager.jobs["active"] = {"status":"generating", "created_at":"2026-01-01"}
        assert client.post("/api/engine/select", json={"engine":"qwen3-tts"}).status_code == 409
        assert client.post("/api/service/restart").status_code == 409
        assert restarted == []
        manager.jobs["active"]["status"] = "completed"
        before = client.get("/api/status").json()["service_instance"]
        response = client.post("/api/service/restart")
        assert response.status_code == 202
        assert response.json() == {"status":"restarting", "service_instance":before}
        assert restarted == [True]


def test_invalid_model_selection_fails_without_changing_setting(tmp_path):
    factories = {"index-tts-2.5": TestEngine, "qwen3-tts": QwenTestEngine}
    with TestClient(create_app(tmp_path, TestEngine(), factories)) as client:
        assert client.post("/api/engine/select", json={"engine":"unknown"}).status_code == 422
        assert client.get("/api/status").json()["engine"] == "index-tts-2.5"
        assert not (tmp_path / "settings.json").exists()
