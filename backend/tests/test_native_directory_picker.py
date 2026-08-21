from __future__ import annotations

from subprocess import CompletedProcess

from docflow.services import native_directory_picker


def test_native_picker_returns_multiple_directories(monkeypatch, tmp_path) -> None:
    first = tmp_path / "第一批"
    second = tmp_path / "第二批"
    first.mkdir()
    second.mkdir()
    monkeypatch.setattr(native_directory_picker.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(native_directory_picker.shutil, "which", lambda _: "/usr/bin/osascript")
    monkeypatch.setattr(
        native_directory_picker.subprocess,
        "run",
        lambda *args, **kwargs: CompletedProcess(args[0], 0, f"{first}\n{second}\n", ""),
    )

    result = native_directory_picker.pick_source_directories()

    assert result == {"paths": [str(first), str(second)], "cancelled": False}


def test_native_picker_treats_user_cancel_as_normal(monkeypatch) -> None:
    monkeypatch.setattr(native_directory_picker.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(native_directory_picker.shutil, "which", lambda _: "/usr/bin/osascript")
    monkeypatch.setattr(
        native_directory_picker.subprocess,
        "run",
        lambda *args, **kwargs: CompletedProcess(
            args[0], 1, "", "execution error: User canceled. (-128)"
        ),
    )

    assert native_directory_picker.pick_source_directories() == {
        "paths": [],
        "cancelled": True,
    }
