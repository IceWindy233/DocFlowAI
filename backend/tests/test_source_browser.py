from pathlib import Path

import pytest

from docflow.services.source_browser import SourceDirectoryError, browse_source_directories


def test_browse_source_directories_lists_children_and_parent(tmp_path: Path) -> None:
    first = tmp_path / "函件材料"
    second = first / "2026年"
    second.mkdir(parents=True)
    (tmp_path / "请示材料").mkdir()

    root_listing = browse_source_directories(tmp_path)
    assert root_listing["current"] == str(tmp_path.resolve())
    assert root_listing["parent"] is None
    assert [item["name"] for item in root_listing["directories"]] == ["函件材料", "请示材料"]

    nested_listing = browse_source_directories(tmp_path, str(first))
    assert nested_listing["parent"] == str(tmp_path.resolve())
    assert nested_listing["directories"] == [
        {"name": "2026年", "path": str(second.resolve())}
    ]


def test_browse_source_directories_rejects_path_outside_root(tmp_path: Path) -> None:
    outside = tmp_path.parent

    with pytest.raises(SourceDirectoryError, match="之外"):
        browse_source_directories(tmp_path, str(outside))
