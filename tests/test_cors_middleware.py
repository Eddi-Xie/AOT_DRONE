"""CORS middleware is wired only when BACKEND_CORS_ALLOW_ORIGINS is set.

The `app` singleton in src.backend.app is built at import time and cannot be
re-CORS'd later (Starlette freezes the middleware stack on first request, and
reloading the module would orphan the existing singleton's `app.state` from
other tests' TestClient lifespans). So we test the parser directly and the
middleware behaviour against a freshly-constructed FastAPI app.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from src.backend.app import _build_cors_origins


def test_build_cors_origins_unset_returns_empty(monkeypatch) -> None:
    monkeypatch.delenv("BACKEND_CORS_ALLOW_ORIGINS", raising=False)
    assert _build_cors_origins() == []


def test_build_cors_origins_parses_comma_separated(monkeypatch) -> None:
    monkeypatch.setenv(
        "BACKEND_CORS_ALLOW_ORIGINS",
        "http://localhost:5173, http://example.test",
    )
    assert _build_cors_origins() == [
        "http://localhost:5173",
        "http://example.test",
    ]


def test_build_cors_origins_skips_blanks(monkeypatch) -> None:
    monkeypatch.setenv("BACKEND_CORS_ALLOW_ORIGINS", " , ,http://only.test, ")
    assert _build_cors_origins() == ["http://only.test"]


def _build_isolated_cors_app(origins: list[str]) -> FastAPI:
    """Mirror the production wiring on a fresh app — same middleware kwargs."""

    fresh = FastAPI()
    fresh.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @fresh.post("/api/intent")
    def _intent() -> dict[str, bool]:
        return {"ok": True}

    return fresh


def test_cors_allows_configured_origin() -> None:
    fresh = _build_isolated_cors_app(["http://localhost:5173"])
    response = TestClient(fresh).options(
        "/api/intent",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_cors_blocks_other_origin() -> None:
    fresh = _build_isolated_cors_app(["http://localhost:5173"])
    response = TestClient(fresh).options(
        "/api/intent",
        headers={
            "Origin": "http://evil.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    # Disallowed origin: middleware must not echo it back as Allow-Origin.
    assert "access-control-allow-origin" not in {k.lower() for k in response.headers.keys()}
