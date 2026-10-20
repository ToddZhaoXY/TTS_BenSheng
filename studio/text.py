"""Deterministic, lossless text segmentation and segment-level captions."""
import re
import textwrap

MAX_TEXT = 50000


def split_text(text: str, limit: int = 100) -> list[dict]:
    if not text or not text.strip():
        raise ValueError("请先输入需要朗读的文字。")
    if len(text) > MAX_TEXT:
        raise ValueError("单次最多 50,000 字，请将更长的书稿分成章节。")
    if not 24 <= limit <= 160:
        raise ValueError("分段长度应在 24–160 字之间。")
    segments = []
    for paragraph in re.split(r"\n+", text.replace("\r\n", "\n").strip()):
        paragraph = re.sub(r"[\t \u3000]+", " ", paragraph).strip()
        if not paragraph:
            continue
        sentences = re.findall(r'.+?(?:[。！？!?；;]+[”’」』）)]*|\.(?=\s|$)|$)', paragraph)
        current = ""
        for sentence in sentences:
            if current and len(current + sentence) > limit:
                segments.append({"text": current.strip(), "paragraph_end": False})
                current = ""
            while len(sentence) > limit:
                if current:
                    segments.append({"text": current.strip(), "paragraph_end": False})
                    current = ""
                # Keep words intact where possible; unpunctuated Chinese still progresses.
                candidates = [m.end() for m in re.finditer(r"[，,、：:\s]", sentence[:limit])]
                cut = max((p for p in candidates if p >= limit // 3), default=limit)
                piece, sentence = sentence[:cut], sentence[cut:]
                if piece.strip():
                    segments.append({"text": piece.strip(), "paragraph_end": False})
            current += sentence
        if current.strip():
            segments.append({"text": current.strip(), "paragraph_end": False})
        if segments:
            segments[-1]["paragraph_end"] = True
    return segments


def timestamp(seconds: float) -> str:
    ms = max(0, round(seconds * 1000))
    hours, ms = divmod(ms, 3600000)
    minutes, ms = divmod(ms, 60000)
    secs, ms = divmod(ms, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{ms:03}"


def build_srt(timeline: list[dict]) -> str:
    entries = []
    for i, segment in enumerate(timeline, 1):
        caption = "\n".join(textwrap.wrap(segment["text"], width=28, break_long_words=True, break_on_hyphens=False))
        entries.append(f"{i}\n{timestamp(segment['start'])} --> {timestamp(segment['end'])}\n{caption}\n")
    return "\n".join(entries)
