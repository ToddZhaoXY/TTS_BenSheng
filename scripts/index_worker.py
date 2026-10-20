"""IndexTTS runtime. Only launched locally by studio/index_engine.py."""
import gc
import json
import os
from pathlib import Path
import sys
import traceback
import warnings

ROOT = Path(__file__).resolve().parent.parent
MODEL_DIR = ROOT / "index-tts-main" / "checkpoints"
sys.path.insert(0, str(ROOT / "index-tts-main"))
sys.path.insert(0, str(ROOT))
PROTOCOL = "@@VOICE_STUDIO@@"
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

tts = None
emotion_model = None


def emit(identifier, **payload):
    print("\n" + PROTOCOL + json.dumps({"id":identifier, **payload}, ensure_ascii=False), flush=True)


def load_emotion():
    global emotion_model
    if emotion_model is not None:
        return
    import torch
    from transformers import AutoTokenizer, AutoModelForCausalLM
    from indextts.infer_v2_5 import QwenEmotion
    # Preserve upstream's tested result parser while bounding generation length
    # and selecting CPU explicitly so classification does not compete for MPS memory.
    emotion_model = QwenEmotion.__new__(QwenEmotion)
    emotion_model.tokenizer = AutoTokenizer.from_pretrained(str(MODEL_DIR / "qwen0.6bemo4-merge"), local_files_only=True)
    emotion_model.model = AutoModelForCausalLM.from_pretrained(str(MODEL_DIR / "qwen0.6bemo4-merge"), torch_dtype=torch.float32, local_files_only=True).eval()
    emotion_model.cn_key_to_en = dict(zip(["高兴","愤怒","悲伤","恐惧","反感","低落","惊讶","自然"], ["happy","angry","sad","afraid","disgusted","melancholic","surprised","calm"]))
    emotion_model.desired_vector_order = list(emotion_model.cn_key_to_en)
    emotion_model.min_score, emotion_model.max_score = 0.0, 1.0


def analyze(request):
    global tts
    import torch
    from studio.emotion import normalize_vector
    if tts is not None:
        tts = None
        gc.collect()
        if torch.backends.mps.is_available():
            torch.mps.empty_cache()
    load_emotion()
    parts = []
    if request.get("previous"):
        parts.append("上文（仅供理解）：" + request["previous"][-160:])
    if request.get("following"):
        parts.append("下文（仅供理解）：" + request["following"][:160])
    if request.get("direction"):
        parts.append("朗读情绪提示：" + request["direction"][:300])
    messages = [
        {"role":"system", "content":"文本情感分类。只分类用户消息中的当前段落。背景不属于分类对象，不要把背景情绪套给当前段落。输出JSON，键为高兴、愤怒、悲伤、恐惧、反感、低落、惊讶、自然，数值0到1。不要解释。\n" + "\n".join(parts)},
        {"role":"user", "content":request["text"]},
    ]
    text = emotion_model.tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True, enable_thinking=False)
    inputs = emotion_model.tokenizer([text], return_tensors="pt")
    with torch.inference_mode():
        ids = emotion_model.model.generate(**inputs, max_new_tokens=192, do_sample=False, pad_token_id=emotion_model.tokenizer.eos_token_id)
    output = emotion_model.tokenizer.decode(ids[0,inputs.input_ids.shape[1]:], skip_special_tokens=True)
    import re
    try:
        match = re.search(r"\{[\s\S]*\}", output)
        parsed = json.loads(match.group(0) if match else output)
    except (ValueError, AttributeError):
        parsed = {m.group(1):float(m.group(2)) for m in re.finditer(r'(高兴|愤怒|悲伤|恐惧|反感|低落|惊讶|自然)[\s"：:]+([0-9.]+)',output)}
    if not parsed:
        raise ValueError("情绪分析没有返回有效结果，可重试或改用手动情绪。")
    return normalize_vector(emotion_model.convert(parsed))


def load_tts():
    global tts, emotion_model
    if tts is not None:
        return
    # Classification is finished before speech generation. Reclaim its RAM.
    emotion_model = None
    gc.collect()
    import torch
    torch.set_num_threads(6)
    from indextts.infer_v2_5 import IndexTTS2
    device = os.environ.get("STUDIO_INDEX_DEVICE") or ("mps" if torch.backends.mps.is_available() else "cpu")
    tts = IndexTTS2(cfg_path=str(MODEL_DIR/"config.yaml"), model_dir=str(MODEL_DIR), device=device, use_bf16=False, use_cuda_kernel=False, use_deepspeed=False, use_qwen_emo=False)


def synthesize(request):
    import torch
    import numpy as np
    import random
    load_tts()
    seed = request["seed"]
    torch.manual_seed(seed)
    random.seed(seed)
    np.random.seed(seed % (2**32))
    language = {"Chinese":"ZH", "English":"EN", "auto":"ZH"}.get(request["language"], "ZH")
    if request["language"] == "auto" and not any("\u4e00" <= c <= "\u9fff" for c in request["text"]):
        language = "EN"
    emotion = request.get("emotion") or {}
    # Elevate truncation to a failed resumable segment instead of silently saving it.
    with warnings.catch_warnings():
        warnings.filterwarnings("error", message=".*generation stopped due to exceeding.*", category=RuntimeWarning)
        result = tts.infer(spk_audio_prompt=request["reference"], text=request["text"], lang=language, output_path=request["output"], emo_vector=emotion.get("vector"), emo_alpha=emotion.get("strength",1.0), use_random=False, use_emo_text=False, duration_factor=1/request.get("speed",1.0), max_text_tokens_per_segment=120, max_mel_tokens=1500, num_beams=1, verbose=False)
    if not result or not Path(request["output"]).is_file():
        raise RuntimeError("模型没有生成有效音频。")


def main():
    import torch
    torch.set_num_threads(6)
    for line in sys.stdin:
        identifier = None
        try:
            request = json.loads(line)
            identifier = request["id"]
            operation = request["op"]
            if operation == "analyze":
                vector = analyze(request)
                emit(identifier, type="result", vector=vector, device="CPU（情绪分析）")
            elif operation == "load":
                load_tts()
                emit(identifier, type="result", device=tts.device)
            elif operation == "synthesize":
                synthesize(request)
                emit(identifier, type="result", device=tts.device)
            else:
                raise ValueError("Unknown operation")
        except Exception as exc:
            traceback.print_exc()
            emit(identifier, type="error", message=f"{type(exc).__name__}: {exc}"[:600])


if __name__ == "__main__":
    main()
