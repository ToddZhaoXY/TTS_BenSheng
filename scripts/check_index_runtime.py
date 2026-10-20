"""Read-only dependency check, then record successful setup for the web app."""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / ".cache" / "matplotlib"))
os.environ.setdefault("NUMBA_CACHE_DIR", str(ROOT / ".cache" / "numba"))
os.environ.setdefault("NLTK_DATA", str(ROOT / ".cache" / "nltk"))
os.environ.setdefault("HF_HUB_OFFLINE", "1")
sys.path.insert(0, str(ROOT / "index-tts-main"))

if __name__ == "__main__":
    import torch
    import torchaudio
    import transformers
    from indextts.infer_v2_5 import IndexTTS2
    from indextts.s2mel.modules.length_regulator import InterpolateRegulator
    from wetext import Normalizer
    from indextts.utils.ja_g2p import JapaneseG2PProcessor
    JapaneseG2PProcessor(g2p_ratio=0)
    zh = Normalizer(remove_erhua=False, lang="zh", operator="tn")
    en = Normalizer(lang="en", operator="tn")
    assert zh.normalize("今天是2026年。"), "中文正规化器未返回结果"
    assert en.normalize("This is a local test."), "英文正规化器未返回结果"
    result = {"python":sys.version.split()[0], "torch":torch.__version__, "transformers":transformers.__version__, "mps_available":torch.backends.mps.is_available(), "text_normalization_ready":True}
    (ROOT / ".venv-index" / "runtime-ready.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
