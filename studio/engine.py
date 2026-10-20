"""Lazy MLX adapter. Imported on the single inference worker only."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODEL_ID = "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-bf16"
MODEL_PATH = ROOT / "models" / "qwen3-tts-0.6b"
MODEL_NAME = "Qwen3-TTS 0.6B"
os.environ.setdefault("HF_HOME", str(ROOT / ".cache" / "huggingface"))
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


from .errors import Cancelled
from .model_ids import QWEN_ENGINE_ID


def readiness():
    return {"model_downloaded": (MODEL_PATH / ".ready").is_file(), "runtime_ready": True}


class QwenEngine:
    name = "qwen3-tts"
    engine_id = QWEN_ENGINE_ID
    native_speed = False

    def __init__(self):
        self.model = None
        self.state = "not_loaded"
        self.error = None

    def load(self):
        if self.model is not None:
            return
        if not (MODEL_PATH / ".ready").exists():
            raise RuntimeError("模型尚未下载完整。请先双击「安装环境.command」，或运行 .venv/bin/python scripts/download_model.py。")
        self.state = "loading"
        try:
            from mlx_audio.tts.utils import load_model
            self.model = load_model(str(MODEL_PATH))
            self.state = "ready"
            self.error = None
        except Exception:
            self.state = "error"
            self.error = "模型加载失败，请查看本地服务日志。"
            raise

    def close(self):
        if self.model is not None:
            self.model = None
            import gc
            import mlx.core as mx
            gc.collect()
            mx.clear_cache()
        self.state = "not_loaded"

    def synthesize(self, text, reference, transcript, language, seed, cancelled):
        import mlx.core as mx
        import numpy as np
        self.load()
        mx.random.seed(seed)
        pieces = []
        sample_rate = 24000
        # Streaming yields checkpoints at which cancellation can safely release the GPU.
        for result in self.model.generate(
            text=text, ref_audio=str(reference), ref_text=transcript,
            lang_code=language, temperature=0.7, top_p=0.9,
            max_tokens=2048, stream=True, streaming_interval=1.5,
        ):
            if cancelled():
                raise Cancelled()
            pieces.append(np.asarray(result.audio, dtype=np.float32))
            sample_rate = result.sample_rate
        if cancelled():
            raise Cancelled()
        if not pieces:
            raise ValueError("模型没有返回音频。")
        audio = np.concatenate(pieces)
        # Reaching the generation ceiling usually means repetition or truncation.
        if len(audio) / sample_rate > 155:
            raise ValueError("这一段生成异常长，请缩短分段或换一段更清晰的参考录音。")
        mx.clear_cache()
        return audio, sample_rate


# New jobs use IndexTTS; the old adapter remains available for old checkpoints.
from .index_engine import IndexEngine as VoiceEngine
