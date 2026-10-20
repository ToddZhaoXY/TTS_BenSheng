import hashlib
import json
from types import SimpleNamespace

import pytest
from scripts import download_index25 as downloader


def model_fixture(tmp_path, monkeypatch):
    destination = tmp_path / "checkpoints"
    destination.mkdir()
    payload = b"verified model fixture"
    item = {"path":"model.pth", "size":len(payload), "checksum":hashlib.sha256(payload).hexdigest(),
            "algorithm":"sha256", "urls":["https://example.invalid/model.pth"]}
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"files":[item]}))
    monkeypatch.setattr(downloader, "DEST", destination)
    monkeypatch.setattr(downloader, "MANIFEST", manifest)
    monkeypatch.setattr(downloader, "TRANSFER", tmp_path / "transfers")
    monkeypatch.setattr(downloader, "PROGRESS", tmp_path / "progress.json")
    return destination, payload, item


def test_offline_verification_rejects_corruption_and_removes_stale_marker(tmp_path, monkeypatch):
    destination, payload, _ = model_fixture(tmp_path, monkeypatch)
    def forbidden_network(*args, **kwargs):
        raise AssertionError("Verification must not download")
    monkeypatch.setattr(downloader.subprocess, "run", forbidden_network)
    path = destination / "model.pth"
    path.write_bytes(payload)
    result = downloader.verify_local()
    assert result["total_bytes"] == len(payload)
    marker = destination / ".download-complete.json"
    assert marker.exists()
    damaged = b"x" * len(payload)
    path.write_bytes(damaged)
    with pytest.raises(RuntimeError, match="model.pth"):
        downloader.verify_local()
    assert path.read_bytes() == damaged
    assert not marker.exists()


def test_existing_wrong_model_is_preserved_without_download(tmp_path, monkeypatch):
    destination, _, _ = model_fixture(tmp_path, monkeypatch)
    path = destination / "model.pth"
    path.write_bytes(b"existing user file")
    with pytest.raises(RuntimeError, match="preserved"):
        downloader.run(1)
    assert path.read_bytes() == b"existing user file"


def test_model_manifest_cannot_escape_destination(tmp_path, monkeypatch):
    model_fixture(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="escapes"):
        downloader.target({"path":"../outside.pth"})


def test_wrong_http_range_is_not_accepted_as_a_valid_block(tmp_path, monkeypatch):
    _, payload, item = model_fixture(tmp_path, monkeypatch)
    def wrong_range(command, **kwargs):
        from pathlib import Path
        Path(command[command.index("--output")+1]).write_bytes(payload)
        Path(command[command.index("--dump-header")+1]).write_text("Content-Range: bytes 1-22/22\n")
        return SimpleNamespace(returncode=0, stdout="206")
    monkeypatch.setattr(downloader.subprocess, "run", wrong_range)
    stop = SimpleNamespace(is_set=lambda:False, wait=lambda _:False)
    with pytest.raises(RuntimeError, match="model.pth"):
        downloader.transfer(item, 0, stop)
    assert not downloader.chunk_path(item, 0).exists()
