from __future__ import annotations

from docflow.core.settings import Settings


def test_comma_separated_cors_origins(monkeypatch) -> None:
    monkeypatch.setenv(
        "DOCFLOW_CORS_ORIGINS",
        "http://127.0.0.1:5173,http://localhost:5173",
    )
    settings = Settings(_env_file=None)
    assert settings.cors_origins == [
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ]


def test_json_cors_origins_are_also_supported(monkeypatch) -> None:
    monkeypatch.setenv(
        "DOCFLOW_CORS_ORIGINS",
        '["http://127.0.0.1:5173", "http://localhost:5173"]',
    )
    settings = Settings(_env_file=None)
    assert settings.cors_origins == [
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ]
