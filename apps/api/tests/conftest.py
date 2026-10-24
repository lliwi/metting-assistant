"""Fixtures comunes.

Los tests marcados con `db` necesitan TEST_DATABASE_URL: una base de datos desechable
(se borra y recrea el esquema). Redis se sustituye por fakeredis y el email por un buzón
en memoria.
"""

import os
from collections.abc import Callable, Iterator
from pathlib import Path

from cryptography.fernet import Fernet

# Antes de importar la app: la configuración se cachea en el primer uso.
os.environ.update(
    {
        "ENVIRONMENT": "test",
        "APP_SECRET_KEY": "test-app-secret-key-0123456789abcdef",
        "DATA_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "COOKIE_SECURE": "false",
        "PUBLIC_APP_URL": "http://testserver",
        "ROOT_PATH": "",
        "GOOGLE_CLIENT_ID": "google-client-id",
        "GOOGLE_CLIENT_SECRET": "google-secret",
        "MICROSOFT_CLIENT_ID": "microsoft-client-id",
        "MICROSOFT_CLIENT_SECRET": "microsoft-secret",
    }
)
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
if TEST_DATABASE_URL:
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL

import fakeredis  # noqa: E402
import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.auth.dependencies import CSRF_HEADER  # noqa: E402
from app.core.rate_limit import RateLimiter, get_rate_limiter  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db import get_engine, get_sessionmaker  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import User, UserStatus  # noqa: E402
from app.notifications.email import Email, get_email_outbox  # noqa: E402
from app.orgs.service import create_personal_organization  # noqa: E402
from app.recordings.events import get_async_redis, get_event_redis  # noqa: E402
from app.recordings.router import get_task_queue  # noqa: E402
from app.storage import get_storage  # noqa: E402


class _NoTasks:
    def process(self, recording_id: object) -> None:
        pass


API_ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "correct horse battery staple"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if TEST_DATABASE_URL:
        return
    skip = pytest.mark.skip(reason="TEST_DATABASE_URL no definida")
    for item in items:
        if "db" in item.keywords:
            item.add_marker(skip)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "db: requiere PostgreSQL (TEST_DATABASE_URL)")


def alembic_config() -> Config:
    return Config(str(API_ROOT / "alembic.ini"))


@pytest.fixture(scope="session")
def migrated_db() -> Iterator[None]:
    cfg = alembic_config()
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield
    get_engine().dispose()


@pytest.fixture
def db(migrated_db: None) -> Iterator[Session]:
    engine = get_engine()
    with engine.begin() as conn:
        tables = conn.execute(
            text(
                "SELECT string_agg(quote_ident(tablename), ', ') FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
            )
        ).scalar_one()
        conn.execute(text(f"TRUNCATE {tables} CASCADE"))
    with get_sessionmaker()() as session:
        yield session


class MemoryOutbox:
    def __init__(self) -> None:
        self.sent: list[Email] = []

    def send(self, email: Email) -> None:
        self.sent.append(email)

    def last_to(self, address: str) -> Email:
        return next(e for e in reversed(self.sent) if e.to == address)

    def token_for(self, address: str) -> str:
        body = self.last_to(address).body
        return body.split("token=")[1].split()[0]


@pytest.fixture
def outbox() -> MemoryOutbox:
    return MemoryOutbox()


@pytest.fixture
def fake_redis_server() -> fakeredis.FakeServer:
    return fakeredis.FakeServer()


@pytest.fixture
def redis(fake_redis_server: fakeredis.FakeServer) -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis(server=fake_redis_server)


class MemoryStorage:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    def put(self, key: str, body: bytes, content_type: str) -> None:
        self.objects[key] = (body, content_type)

    def get(self, key: str) -> bytes:
        return self.objects[key][0]

    def put_file(self, key: str, path: str, content_type: str) -> None:
        with open(path, "rb") as f:
            self.objects[key] = (f.read(), content_type)

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    def delete_prefix(self, prefix: str) -> int:
        keys = [k for k in self.objects if k.startswith(prefix)]
        for key in keys:
            del self.objects[key]
        return len(keys)

    def head(self, key: str) -> tuple[int, str]:
        body, content_type = self.objects[key]
        return len(body), content_type

    def iter_range(self, key: str, start: int, end: int, chunk: int = 64 * 1024):  # type: ignore[no-untyped-def]
        body = self.objects[key][0][start : end + 1]
        for i in range(0, len(body), chunk):
            yield body[i : i + chunk]

    def exists(self, key: str) -> bool:
        return key in self.objects

    def check(self) -> None:
        pass


@pytest.fixture
def storage() -> MemoryStorage:
    return MemoryStorage()


@pytest.fixture
def app(
    outbox: MemoryOutbox,
    redis: fakeredis.FakeRedis,
    fake_redis_server: fakeredis.FakeServer,
    storage: MemoryStorage,
) -> FastAPI:
    app = create_app()
    app.dependency_overrides[get_email_outbox] = lambda: outbox
    app.dependency_overrides[get_rate_limiter] = lambda: RateLimiter(redis)
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_task_queue] = lambda: _NoTasks()
    app.dependency_overrides[get_event_redis] = lambda: redis
    app.dependency_overrides[get_async_redis] = lambda: fakeredis.aioredis.FakeRedis(
        server=fake_redis_server
    )
    return app


class ApiClient(TestClient):
    """TestClient que obtiene y envía el token CSRF como haría el frontend."""

    def csrf(self) -> str:
        token = self.cookies.get("ma_csrf")
        if not token:
            token = self.get("/v1/auth/csrf").json()["csrf_token"]
        return str(token)

    def request(self, method: str, url: str, **kwargs):  # type: ignore[no-untyped-def,override]
        if method.upper() not in ("GET", "HEAD", "OPTIONS") and kwargs.pop("csrf", True):
            headers = dict(kwargs.pop("headers", None) or {})
            headers.setdefault(CSRF_HEADER, self.csrf())
            kwargs["headers"] = headers
        return super().request(method, url, **kwargs)


@pytest.fixture
def make_client(app: FastAPI) -> Iterator[Callable[[], ApiClient]]:
    clients: list[ApiClient] = []

    def factory() -> ApiClient:
        c = ApiClient(app, raise_server_exceptions=True)
        clients.append(c)
        return c

    yield factory
    for c in clients:
        c.close()


@pytest.fixture
def client(make_client: Callable[[], ApiClient]) -> ApiClient:
    return make_client()


@pytest.fixture
def make_user(db: Session) -> Callable[..., User]:
    def factory(
        email: str, *, name: str | None = None, password: str = PASSWORD, verified: bool = True
    ) -> User:
        user = User(
            email=email,
            name=name or email.split("@")[0].title(),
            status=UserStatus.ACTIVE if verified else UserStatus.PENDING,
            password_hash=hash_password(password),
        )
        if verified:
            from app.auth.sessions import now

            user.email_verified_at = now()
        db.add(user)
        db.flush()
        create_personal_organization(db, user)
        db.commit()
        return user

    return factory


@pytest.fixture
def login(make_client: Callable[[], ApiClient]) -> Callable[[str], ApiClient]:
    """Devuelve un cliente nuevo con sesión iniciada para ese email."""

    def do_login(email: str, password: str = PASSWORD) -> ApiClient:
        c = make_client()
        res = c.post("/v1/auth/login", json={"email": email, "password": password})
        assert res.status_code == 200, res.text
        assert res.json()["status"] == "authenticated"
        return c

    return do_login
