"""Real offline HTTP/default-factory regression, isolated from user voices/jobs.

Requires the installed Index runtime and the existing synthetic smoke-test reference.
Run with .venv/bin/python scripts/verify_dispatch.py while other inference is idle.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import zipfile

import httpx
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from studio.emotion import complete_plan
from studio.jobs import save_json
from studio.model_ids import INDEX_ENGINE_ID


def wait_job(client, identifier):
    last = None
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        response = client.get(f"/api/jobs/{identifier}")
        response.raise_for_status()
        job = response.json()
        progress = (job["status"], job["analyzed"], job["completed"])
        if progress != last:
            print(json.dumps({"id": identifier, "status": progress[0], "analyzed": progress[1], "completed": progress[2]}, ensure_ascii=False), flush=True)
            last = progress
        if job["status"] in {"planned", "completed", "failed", "cancelled"}:
            return job
        time.sleep(1)
    raise TimeoutError("Real-model verification exceeded 15 minutes")


def main():
    reference = ROOT / "data/validation/voices" / ("0" * 32) / "reference.wav"
    if not reference.is_file():
        raise RuntimeError("Missing synthetic fixture; run scripts/smoke_test.py first")
    data = ROOT / "data" / "validation-dispatch" / time.strftime("%Y%m%d-%H%M%S")
    data.mkdir(parents=True)
    env = {**os.environ, "STUDIO_DATA_DIR": str(data), "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
    with (data / "server.log").open("w") as log:
        server = subprocess.Popen([sys.executable, "-m", "uvicorn", "studio.server:create_app", "--factory", "--host", "127.0.0.1", "--port", "8766"], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            with httpx.Client(base_url="http://127.0.0.1:8766", timeout=30) as client:
                for _ in range(60):
                    if server.poll() is not None:
                        raise RuntimeError(f"Test server exited; see {data / 'server.log'}")
                    try:
                        response = client.get("/api/status")
                        response.raise_for_status()
                        break
                    except httpx.ConnectError:
                        time.sleep(.5)
                else:
                    raise TimeoutError("Test server did not start")
                assert response.json()["runtime_ready"] and response.json()["model_downloaded"]
                response = client.post("/api/plans", json={"text": "阳光照进窗台，他静静地翻着书。\n你竟然骗我！我再也不会相信你了！\n别过来！救命啊，我害怕极了！\n太好了，我们终于成功了！我真开心！", "emotion_mode": "auto", "segment_length": 80})
                response.raise_for_status()
                plan = wait_job(client, response.json()["id"])
                assert plan["status"] == "planned" and complete_plan(plan), plan.get("message")
                assert plan["engine"] == INDEX_ENGINE_ID and plan["analyzed"] == 4
                assert plan["analysis_model"] == "qwen0.6bemo4-merge"
                assert plan["analysis_device"] == "CPU（情绪分析）"
                with reference.open("rb") as audio:
                    response = client.post("/api/voices", data={"name": "调度回归：系统合成测试声，非用户声音"}, files={"audio": ("sample.wav", audio, "audio/wav")})
                response.raise_for_status()
                voice_id = response.json()["id"]
                response = client.post("/api/jobs", json={"voice_id": voice_id, "title": "正式调度路径真实 Index 合成", "text": "你终于来了，我一直在等你。", "emotion_mode": "manual", "emotion": "angry", "emotion_strength": .4})
                response.raise_for_status()
                speech = wait_job(client, response.json()["id"])
                assert speech["status"] == "completed", speech.get("message")
                assert speech["engine"] == speech["execution_engine"] == INDEX_ENGINE_ID
                assert speech["device"] == "mps", speech["device"]
                assert complete_plan(speech)
                contents = {}
                for name in ("narration.wav", "narration.mp3", "captions.srt", "script.txt", "metadata.json", "voice-studio.zip"):
                    response = client.get(f"/api/jobs/{speech['id']}/files/{name}")
                    response.raise_for_status()
                    assert response.content
                    contents[name] = response.content
                metadata = json.loads(contents["metadata.json"])
                assert metadata["generated_with"] == INDEX_ENGINE_ID and metadata["device"] == "mps"
                delivery = metadata["emotion_plan"][0]["delivery"]
                assert delivery["emotion"] == "angry" and delivery["strength"] == .4
                directory = data / "jobs" / speech["id"]
                info = sf.info(directory / "narration.wav")
                assert info.samplerate == 48000 and info.subtype == "PCM_24" and info.duration > 0
                assert abs(info.duration - speech["duration"]) < .002
                with zipfile.ZipFile(directory / "voice-studio.zip") as archive:
                    for name in archive.namelist():
                        assert archive.read(name) == contents[name]
                report = {"sample_type": "macOS synthetic reference; not user voice", "default_factory": True,
                          "plan_id": plan["id"], "analyzed": plan["analyzed"], "analysis_seconds": plan["elapsed"],
                          "analysis_model": plan["analysis_model"], "analysis_device": plan["analysis_device"],
                          "emotions": [s["delivery"] for s in plan["segments"]], "speech_id": speech["id"],
                          "engine": speech["execution_engine"], "device": speech["device"],
                          "speech_seconds": speech["duration"], "generation_seconds": speech["elapsed"],
                          "exports_verified": list(contents), "data_dir": str(data)}
                save_json(data / "report.json", report)
                print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
                client.post("/api/engine/unload").raise_for_status()
        finally:
            server.terminate()
            try:
                server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()


if __name__ == "__main__":
    main()
