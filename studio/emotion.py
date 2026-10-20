"""Transparent, bounded emotion plans. No keyword-based pretend analysis."""
import hashlib
import json
import math

EMOTIONS = {
    "happy": "高兴", "angry": "愤怒", "sad": "悲伤", "afraid": "恐惧",
    "disgusted": "反感", "melancholic": "低落", "surprised": "惊讶", "calm": "平静",
}
PLAN_FIELDS = ("text", "segment_length", "emotion_mode", "emotion", "emotion_strength", "direction", "contextual")


def valid_delivery(delivery):
    if not isinstance(delivery, dict):
        return False
    strength = delivery.get("strength")
    if not isinstance(strength, (int, float)) or not math.isfinite(strength) or not 0 <= strength <= 1:
        return False
    if delivery.get("source") not in {"auto", "manual", "override", "reference"}:
        return False
    vector = delivery.get("vector")
    if delivery.get("emotion") == "reference":
        return vector is None and strength == 0
    return (delivery.get("emotion") in EMOTIONS and isinstance(vector, list) and len(vector) == 8
            and all(isinstance(v, (int, float)) and math.isfinite(v) and 0 <= v <= 1 for v in vector)
            and 0 < sum(vector) <= 1.0001)


def complete_plan(job):
    segments = job.get("segments", [])
    return (isinstance(segments, list) and bool(segments)
            and job.get("analyzed") == job.get("total") == len(segments)
            and all(isinstance(s, dict) and valid_delivery(s.get("delivery")) for s in segments))


def fingerprint(request):
    defaults = {"segment_length":80, "emotion_mode":"auto", "emotion":"calm", "emotion_strength":0.6, "direction":"", "contextual":True}
    data = {key: request.get(key, defaults.get(key)) for key in PLAN_FIELDS}
    data["planner_version"] = "index25-system-context-v1"
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def normalize_vector(values):
    if isinstance(values, dict):
        values = [values.get(key, 0) for key in EMOTIONS]
    if len(values) != 8:
        raise ValueError("情绪模型返回了无效的结果，请重新分析。")
    vector = []
    for value in values:
        value = float(value)
        if not math.isfinite(value):
            raise ValueError("情绪模型返回了无效的强度，请重新分析。")
        vector.append(max(0.0, min(1.0, value)))
    total = sum(vector)
    if total == 0:
        vector[-1] = 1.0
    elif total > 1:
        vector = [v / total for v in vector]
    return [round(v, 5) for v in vector]


def preset(emotion, strength, source="manual"):
    if emotion == "reference":
        return {"emotion":"reference", "vector":None, "strength":0.0, "source":source}
    if emotion not in EMOTIONS:
        raise ValueError("不支持的情绪。")
    vector = [float(key == emotion) for key in EMOTIONS]
    return {"emotion":emotion, "vector":vector, "strength":strength, "source":source}


def automatic(values, strength):
    vector = normalize_vector(values)
    emotion = list(EMOTIONS)[max(range(8), key=lambda i: vector[i])]
    return {"emotion":emotion, "vector":vector, "strength":strength, "source":"auto"}
