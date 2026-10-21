from collections.abc import Iterator

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app import health
from app.core.correlation import HEADER
from app.core.errors import AppError
from app.main import create_app


class Item(BaseModel):
    count: int


@pytest.fixture
def app() -> FastAPI:
    app = create_app()

    @app.get("/_test/boom")
    def boom() -> None:
        raise RuntimeError("fallo inesperado")

    @app.get("/_test/not-found")
    def not_found() -> None:
        raise HTTPException(status_code=404, detail="No existe")

    @app.get("/_test/domain")
    def domain() -> None:
        raise AppError("quota_exceeded", "Cuota superada", status_code=409)

    @app.post("/_test/validate")
    def validate(item: Item) -> Item:
        return item

    return app


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def test_liveness(client: TestClient) -> None:
    res = client.get("/healthz")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_correlation_id_is_generated(client: TestClient) -> None:
    res = client.get("/healthz")
    assert len(res.headers[HEADER]) == 32


def test_valid_incoming_correlation_id_is_propagated(client: TestClient) -> None:
    res = client.get("/healthz", headers={HEADER: "req-12345678"})
    assert res.headers[HEADER] == "req-12345678"


def test_invalid_incoming_correlation_id_is_replaced(client: TestClient) -> None:
    res = client.get("/healthz", headers={HEADER: "bad id\nwith newline"})
    assert res.headers[HEADER] != "bad id\nwith newline"
    assert len(res.headers[HEADER]) == 32


def test_unhandled_error_uses_common_format_with_correlation_id(client: TestClient) -> None:
    res = client.get("/_test/boom", headers={HEADER: "req-boom-0001"})
    assert res.status_code == 500
    assert res.headers[HEADER] == "req-boom-0001"
    assert res.json() == {
        "error": {
            "code": "internal_error",
            "message": "Error interno",
            "details": None,
            "correlation_id": "req-boom-0001",
        }
    }


def test_http_error_format(client: TestClient) -> None:
    res = client.get("/_test/not-found")
    assert res.status_code == 404
    body = res.json()["error"]
    assert body["code"] == "not_found"
    assert body["message"] == "No existe"
    assert body["correlation_id"] == res.headers[HEADER]


def test_unknown_route_uses_common_format(client: TestClient) -> None:
    res = client.get("/no-such-route")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"


def test_domain_error_format(client: TestClient) -> None:
    res = client.get("/_test/domain")
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "quota_exceeded"


def test_validation_error_format(client: TestClient) -> None:
    res = client.post("/_test/validate", json={"count": "no-es-un-numero"})
    assert res.status_code == 422
    body = res.json()["error"]
    assert body["code"] == "validation_error"
    assert body["details"][0]["loc"] == ["body", "count"]


def test_readiness_ok(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health, "READINESS_CHECKS", {"database": lambda: None})
    res = client.get("/readyz")
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "checks": {"database": "ok"}}


def test_readiness_reports_failing_dependency(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail() -> None:
        raise ConnectionError("sin conexión")

    monkeypatch.setattr(health, "READINESS_CHECKS", {"database": lambda: None, "redis": fail})
    res = client.get("/readyz")
    assert res.status_code == 503
    assert res.json()["checks"] == {"database": "ok", "redis": "error"}
