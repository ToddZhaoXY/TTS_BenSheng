"""Persisted identifiers, deliberately independent of UI display names."""

INDEX_ENGINE_ID = "index-tts-2.5"
QWEN_ENGINE_ID = "qwen3-tts"


def engine_id(engine):
    identifier = getattr(engine, "engine_id", None)
    if identifier not in {INDEX_ENGINE_ID, QWEN_ENGINE_ID}:
        raise ValueError("声音引擎缺少有效的模型标识，已停止任务，避免使用错误模型。")
    return identifier
