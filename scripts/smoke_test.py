"""Real model smoke test, using an explicitly labelled macOS synthetic voice."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["HF_HUB_OFFLINE"] = "1"

from studio.audio import prepare_reference
from studio.engine import QwenEngine as VoiceEngine
from studio.jobs import JobManager, save_json

if __name__ == "__main__":
    data = ROOT / "data" / "validation"
    voice_dir = data / "voices" / ("0" * 32)
    voice_dir.mkdir(parents=True, exist_ok=True)
    transcript = "你好，这是一次声音合成测试。窗外的阳光很好，我们用自然的语气，慢慢讲述今天的故事。"
    subprocess.run(["/usr/bin/say", "-v", "Tingting", "-r", "170", "-o", str(voice_dir / "reference.aiff"), transcript], check=True)
    info = prepare_reference(voice_dir / "reference.aiff", voice_dir / "reference.wav")
    voice = {"id":"0" * 32, "name":"功能验证：macOS 系统示例声（非用户声音）", "transcript":transcript, **info}
    engine = VoiceEngine()
    manager = JobManager(data, engine)
    job = manager.create({"title":"真实模型 · 中文三段配音验证", "text":"你好，欢迎来到本声录音棚。\n今天，让文字带上你的声音。\n这是一段视频旁白测试，谢谢收听。", "segment_length":42, "speed":1.0, "gap":0.3, "language":"Chinese", "seed":42, "voice_id":voice["id"], "mode":"video"}, voice)
    last = None
    try:
        while True:
            result = manager.get(job["id"])
            key = (result["status"],result["completed"])
            if key != last:
                print(result["message"], flush=True)
                last = key
            if result["status"] in {"completed", "failed", "cancelled"}:
                break
            time.sleep(1)
        if result["status"] != "completed":
            raise RuntimeError(result["message"])
        summary = {"job_id":job["id"], "sample_type":"macOS synthetic reference; not the user's voice", "audio_seconds":result["duration"], "generation_seconds":result["elapsed"], "segments":result["total"], "wav":str(manager.directory / job["id"] / "narration.wav")}
        save_json(data / "smoke-result.json", summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    finally:
        manager.shutdown()
