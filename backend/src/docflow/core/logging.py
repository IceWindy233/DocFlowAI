import logging
import re
from collections.abc import Mapping
from typing import Any

SECRET_PATTERNS = (
    re.compile(r"(?i)(api[-_ ]?key|authorization|token|secret)(\s*[:=]\s*)([^\s,;}]+)"),
    re.compile(r"sk-[A-Za-z0-9_-]{12,}"),
)


def redact(value: Any) -> Any:
    """Best-effort redaction used before structured values reach logs or APIs."""
    if isinstance(value, Mapping):
        return {
            key: "***"
            if any(word in key.lower() for word in ("secret", "token", "key"))
            else redact(val)
            for key, val in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if not isinstance(value, str):
        return value
    output = value
    for pattern in SECRET_PATTERNS:
        if pattern.groups >= 3:
            output = pattern.sub(r"\1\2***", output)
        else:
            output = pattern.sub("***", output)
    return output


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
