"""Local audio processing. No network calls or external services."""
import subprocess
from pathlib import Path

import imageio_ffmpeg
import numpy as np
import soundfile as sf


def ffmpeg(*args: str) -> None:
    result = subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *map(str, args)],
        capture_output=True, text=True, timeout=180,
    )
    if result.returncode:
        raise ValueError("音频无法读取或转换，请使用正常播放的 WAV、MP3、M4A 或 FLAC 文件。")


def prepare_reference(source: Path, destination: Path) -> dict:
    # Read at most 61 s, so a large compressed upload cannot exhaust memory.
    ffmpeg("-i", str(source), "-t", "61", "-vn", "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", str(destination))
    audio, sr = sf.read(destination, dtype="float32")
    duration = len(audio) / sr
    if not 3 <= duration <= 60:
        raise ValueError("参考录音需要 3–60 秒；建议选取自然、连续的 10–15 秒人声。")
    peak = float(np.max(np.abs(audio)))
    rms = float(np.sqrt(np.mean(audio ** 2)))
    if rms < 0.0005:
        raise ValueError("这段录音几乎没有声音，请检查后重新录制。")
    warnings = []
    if rms < 0.015:
        warnings.append("录音音量偏低，建议靠近麦克风重新录制。")
    if float(np.mean(np.abs(audio) >= 0.995)) > 0.001:
        warnings.append("录音可能有爆音，建议降低录音增益。")
    if duration > 15:
        warnings.append("IndexTTS 使用参考录音开头最多 15 秒，请确保这部分有清晰、连续的人声。")
    return {"duration": round(duration, 2), "sample_rate": sr, "warnings": warnings, "peak": round(peak, 4)}


def write_segment(audio, sample_rate: int, path: Path, speed: float) -> float:
    waveform = np.asarray(audio, dtype=np.float32).reshape(-1)
    if not len(waveform) or not np.isfinite(waveform).all():
        raise ValueError("模型没有返回有效音频，请重试这一任务。")
    if float(np.max(np.abs(waveform))) < 0.0001:
        raise ValueError("模型返回了静音，请检查参考录音后重试。")
    peak = np.max(np.abs(waveform))
    if peak > 0.98:
        waveform = waveform * (0.98 / peak)
    raw = path.with_name(path.stem + ".raw.wav")
    sf.write(raw, waveform, sample_rate, subtype="PCM_16")
    ffmpeg("-i", str(raw), "-af", f"atempo={speed}", "-ac", "1", "-ar", "24000", str(path))
    raw.unlink(missing_ok=True)
    return sf.info(path).duration


def assemble(directory: Path, segments: list[dict], gap: float) -> list[dict]:
    timeline, cursor = [], 0
    with sf.SoundFile(directory / "assembled.wav", "w", samplerate=24000, channels=1, subtype="PCM_16") as output:
        for index, segment in enumerate(segments):
            chunk, sr = sf.read(directory / "chunks" / f"{index:04}.wav", dtype="float32")
            if sr != 24000:
                raise ValueError("音频采样率不一致，请重新生成任务。")
            start = cursor / 24000
            output.write(chunk)
            cursor += len(chunk)
            timeline.append({"text": segment["text"], "start": start, "end": cursor / 24000})
            if index < len(segments) - 1:
                pause = gap * (1.6 if segment["paragraph_end"] else 1)
                silence = np.zeros(round(24000 * pause), dtype=np.float32)
                output.write(silence)
                cursor += len(silence)
    # Output rate is for editor compatibility; resampling does not add source detail.
    ffmpeg("-i", str(directory / "assembled.wav"), "-ar", "48000", "-c:a", "pcm_s24le", str(directory / "narration.wav"))
    ffmpeg("-i", str(directory / "assembled.wav"), "-ar", "48000", "-c:a", "libmp3lame", "-b:a", "192k", str(directory / "narration.mp3"))
    return timeline
