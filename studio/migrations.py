"""Recover the display-name dispatch regression without changing audio samples."""
import copy
import json
import shutil
import zipfile

from .emotion import complete_plan
from .model_ids import INDEX_ENGINE_ID, QWEN_ENGINE_ID

MIGRATION = "engine-dispatch-v1"


def repair_job(path, job, save_json):
    malformed = job.get("engine") == "indextts-2.5"
    empty_plan = job.get("kind") == "analysis" and job.get("status") == "planned" and not complete_plan(job)
    if not malformed and not empty_plan:
        return job
    # Back up all affected files before the first write. Existing backups are immutable.
    backup = path.parents[2] / "backups" / MIGRATION / path.parent.name
    backup.mkdir(parents=True, exist_ok=True)
    for name in ("job.json", "metadata.json", "voice-studio.zip"):
        source = path.parent / name
        if source.exists() and not (backup / name).exists():
            shutil.copy2(source, backup / name)
    repaired = copy.deepcopy(job)
    repaired["repair"] = {"version": MIGRATION, "original_engine": job.get("engine"),
                          "original_status": job.get("status"), "backup": str(backup)}
    if job.get("kind") == "analysis":
        # The malformed production branch never called analyze. Do not trust its results.
        if malformed:
            for segment in repaired.get("segments", []):
                segment.pop("delivery", None)
            repaired["analyzed"] = 0
            repaired["engine"] = INDEX_ENGINE_ID
        repaired.update(status="failed", message="旧版调度错误导致语气分析未完成；记录已修正，请点击「重新分析」。")
    else:
        has_audio = (job.get("completed", 0) > 0 or any((path.parent / "chunks").glob("*.wav"))
                     or (path.parent / "narration.wav").exists() or (path.parent / "metadata.json").exists())
        if not has_audio:
            # Nothing to preserve or mix: restart this ungenerated task on the intended model.
            for segment in repaired.get("segments", []):
                segment.pop("delivery", None)
            repaired.update(engine=INDEX_ENGINE_ID, status="failed", analyzed=0,
                            message="旧版调度错误已修正；此任务尚无音频，继续后将使用 IndexTTS-2.5。")
            save_json(path, repaired)
            return repaired
        # That branch always selected Qwen, including partially generated checkpoints.
        # Keep it on Qwen on resume: mixing old chunks with Index would corrupt provenance.
        repaired.update(engine=QWEN_ENGINE_ID, requested_engine=INDEX_ENGINE_ID,
                        provenance_note="旧版调度错误：此任务实际使用旧 Qwen 模型，未执行 IndexTTS 语气分析。原音频已保留。")
        repaired["message"] = repaired["provenance_note"]
        metadata_path = path.parent / "metadata.json"
        if metadata_path.exists():
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata.update(generated_with=QWEN_ENGINE_ID, requested_engine=INDEX_ENGINE_ID,
                            provenance_note=repaired["provenance_note"])
            save_json(metadata_path, metadata)
            archive_path = path.parent / "voice-studio.zip"
            if archive_path.exists():
                pending = archive_path.with_suffix(".repair.tmp")
                with zipfile.ZipFile(archive_path) as source, zipfile.ZipFile(pending, "w") as target:
                    for info in source.infolist():
                        content = metadata_path.read_bytes() if info.filename == "metadata.json" else source.read(info)
                        target.writestr(info, content)
                pending.replace(archive_path)
    save_json(path, repaired)
    return repaired
