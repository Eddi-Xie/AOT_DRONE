from pathlib import Path

import pytest

from src.vision.yolo_detector import _resolve_model_spec


def test_resolve_model_spec_prefers_local_cwd_file(tmp_path: Path) -> None:
    cwd = tmp_path / "cwd"
    repo_root = tmp_path / "repo"
    cwd.mkdir()
    repo_root.mkdir()

    model_file = cwd / "yolov8n.pt"
    model_file.write_text("x", encoding="utf-8")

    resolved, source = _resolve_model_spec(
        "yolov8n.pt",
        cwd=cwd,
        repo_root=repo_root,
    )

    assert resolved == str(model_file.resolve())
    assert source == str(model_file.resolve())


def test_resolve_model_spec_falls_back_to_repo_root(tmp_path: Path) -> None:
    cwd = tmp_path / "cwd"
    repo_root = tmp_path / "repo"
    cwd.mkdir()
    repo_root.mkdir()

    model_file = repo_root / "models" / "detector.pt"
    model_file.parent.mkdir(parents=True, exist_ok=True)
    model_file.write_text("x", encoding="utf-8")

    resolved, source = _resolve_model_spec(
        "models/detector.pt",
        cwd=cwd,
        repo_root=repo_root,
    )

    assert resolved == str(model_file.resolve())
    assert source == str(model_file.resolve())


def test_resolve_model_spec_errors_for_missing_explicit_path(tmp_path: Path) -> None:
    cwd = tmp_path / "cwd"
    repo_root = tmp_path / "repo"
    cwd.mkdir()
    repo_root.mkdir()

    with pytest.raises(FileNotFoundError, match="YOLO model file not found"):
        _resolve_model_spec(
            "models/missing.pt",
            cwd=cwd,
            repo_root=repo_root,
        )


def test_resolve_model_spec_allows_non_file_model_spec(tmp_path: Path) -> None:
    cwd = tmp_path / "cwd"
    repo_root = tmp_path / "repo"
    cwd.mkdir()
    repo_root.mkdir()

    resolved, source = _resolve_model_spec(
        "yolov8n.pt",
        cwd=cwd,
        repo_root=repo_root,
    )

    assert resolved == "yolov8n.pt"
    assert "delegating to ultralytics" in source
