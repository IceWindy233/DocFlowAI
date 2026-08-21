from __future__ import annotations

import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any


class NativeDirectoryPickerError(RuntimeError):
    """Raised when the local operating-system directory picker is unavailable."""


_MACOS_PICKER_SCRIPT = '''
set selectedFolders to choose folder with prompt "选择一个或多个数据源目录" ¬
    with multiple selections allowed
set outputText to ""
repeat with selectedFolder in selectedFolders
    set outputText to outputText & POSIX path of selectedFolder & (ASCII character 10)
end repeat
return outputText
'''


def pick_source_directories() -> dict[str, Any]:
    if platform.system() != "Darwin" or not shutil.which("osascript"):
        raise NativeDirectoryPickerError("当前系统不支持原生目录选择器")
    result = subprocess.run(
        ["osascript", "-e", _MACOS_PICKER_SCRIPT],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.strip()
        if "(-128)" in message or "User canceled" in message or "用户已取消" in message:
            return {"paths": [], "cancelled": True}
        raise NativeDirectoryPickerError(message or "无法打开系统目录选择器")

    paths: list[str] = []
    for raw_path in result.stdout.splitlines():
        value = raw_path.strip()
        if not value:
            continue
        path = Path(value).expanduser().resolve()
        if path.is_dir() and str(path) not in paths:
            paths.append(str(path))
    return {"paths": paths, "cancelled": False}
