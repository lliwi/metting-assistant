"""Importación de archivos: subida por bloques (API) y preparación en el worker.

Los tests del worker usan FFmpeg real para generar y procesar los medios.
"""

import hashlib
import shutil
import socket
import struct
import subprocess
import threading
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import fakeredis
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.media.antivirus import ClamdScanner, ScannerUnavailable, ScanResult
from app.media.ffmpeg import MediaError, probe
from app.media.imports import prepare_import
from app.models import AudioSegment, AuditEvent, Recording, RecordingStatus, User
from app.recordings.router import get_task_queue
from tests.conftest import ApiClient, MemoryStorage

pytestmark = pytest.mark.db

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="FFmpeg no instalado",
)

CHUNK = 64 * 1024


class RecordingTasks:
    def __init__(self) -> None:
        self.imports: list[uuid.UUID] = []

    def prepare_import(self, recording_id: uuid.UUID) -> None:
        self.imports.append(recording_id)


@pytest.fixture
def tasks(app: FastAPI) -> RecordingTasks:
    queue = RecordingTasks()
    app.dependency_overrides[get_task_queue] = lambda: queue
    return queue


@pytest.fixture
def ana(make_user: Callable[..., User], login: Callable[[str], ApiClient]) -> ApiClient:
    make_user("ana@example.com")
    return login("ana@example.com")


def make_media(tmp_path: Path, name: str, *args: str) -> bytes:
    path = tmp_path / name
    subprocess.run(  # noqa: S603
        ["ffmpeg", "-v", "error", "-y", *args, str(path)],  # noqa: S607
        check=True,
    )
    return path.read_bytes()


def create_import(c: ApiClient, filename: str = "reunión.mp3") -> dict[str, Any]:
    org_id = c.get("/v1/organizations").json()[0]["id"]
    res = c.post(
        "/v1/recordings",
        json={
            "organization_id": org_id,
            "title": "Importada",
            "consent": True,
            "source": "import",
            "filename": filename,
        },
    )
    assert res.status_code == 201, res.text
    return dict(res.json())


def upload_chunks(c: ApiClient, rec_id: str, data: bytes, size: int = CHUNK) -> int:
    chunks = [data[i : i + size] for i in range(0, len(data), size)]
    for seq, chunk in enumerate(chunks):
        res = c.post(
            f"/v1/recordings/{rec_id}/segments",
            data={
                "segment_id": str(uuid.uuid4()),
                "track": "0",
                "seq": str(seq),
                "duration_ms": "0",
                "sha256": hashlib.sha256(chunk).hexdigest(),
            },
            files={"file": ("chunk", chunk, "application/octet-stream")},
        )
        assert res.status_code == 201, res.text
    return len(chunks)


def complete(c: ApiClient, rec_id: str, chunks: int) -> None:
    res = c.post(f"/v1/recordings/{rec_id}/complete", json={"tracks": {"0": chunks}})
    assert res.status_code == 200, res.text


class FakeScanner:
    def __init__(self, result: ScanResult | Exception) -> None:
        self.result = result
        self.scanned: list[bytes] = []

    def scan(self, path: Path) -> ScanResult:
        self.scanned.append(path.read_bytes())
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def run_worker(
    db: Session,
    storage: MemoryStorage,
    redis: fakeredis.FakeRedis,
    rec_id: str,
    scanner: FakeScanner | None = None,
) -> Recording:
    prepare_import(
        db, storage, scanner or FakeScanner(ScanResult(clean=True)), redis, uuid.UUID(rec_id)
    )  # type: ignore[arg-type]
    db.expire_all()
    return db.get_one(Recording, uuid.UUID(rec_id))


# --- API -----------------------------------------------------------------------------------


def test_filename_is_reduced_to_basename(ana: ApiClient) -> None:
    rec = create_import(ana, filename="C:\\Users\\ana\\Escritorio\\../reunión.mp3")
    assert rec["source"] == "import"
    assert rec["source_filename"] == "reunión.mp3"


def test_first_chunk_must_be_media(ana: ApiClient, tasks: RecordingTasks) -> None:
    rec = create_import(ana)
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    res = ana.post(
        f"/v1/recordings/{rec['id']}/segments",
        data={
            "segment_id": str(uuid.uuid4()),
            "track": "0",
            "seq": "0",
            "duration_ms": "0",
            "sha256": hashlib.sha256(png).hexdigest(),
        },
        files={"file": ("x", png, "image/png")},
    )
    assert res.status_code == 415


def test_import_size_limit(
    ana: ApiClient, tasks: RecordingTasks, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "import_max_bytes", 100)
    rec = create_import(ana)
    data = b"ID3" + b"\x00" * 200
    res = ana.post(
        f"/v1/recordings/{rec['id']}/segments",
        data={
            "segment_id": str(uuid.uuid4()),
            "track": "0",
            "seq": "0",
            "duration_ms": "0",
            "sha256": hashlib.sha256(data).hexdigest(),
        },
        files={"file": ("x", data, "audio/mpeg")},
    )
    assert res.json()["error"]["code"] == "import_too_large"


@needs_ffmpeg
def test_complete_enqueues_preparation(
    ana: ApiClient, tasks: RecordingTasks, tmp_path: Path
) -> None:
    mp3 = make_media(tmp_path, "a.mp3", "-f", "lavfi", "-i", "sine=frequency=440:duration=2")
    rec = create_import(ana)
    complete(ana, rec["id"], upload_chunks(ana, rec["id"], mp3))
    assert tasks.imports == [uuid.UUID(rec["id"])]
    detail = ana.get(f"/v1/recordings/{rec['id']}").json()
    assert detail["status"] == "pending"
    assert detail["size_bytes"] == len(mp3)
    assert detail["audio_ready"] is False


# --- Worker --------------------------------------------------------------------------------


@needs_ffmpeg
@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("a.mp3", ["-f", "lavfi", "-i", "sine=frequency=440:duration=3"]),
        ("a.wav", ["-f", "lavfi", "-i", "sine=frequency=440:duration=3"]),
        (
            "v.mp4",
            [
                "-f",
                "lavfi",
                "-i",
                "testsrc=size=160x120:rate=10:duration=3",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=3",
                "-c:v",
                "libx264",
                "-c:a",
                "aac",
                "-shortest",
            ],
        ),
    ],
)
def test_worker_extracts_audio(
    ana: ApiClient,
    tasks: RecordingTasks,
    db: Session,
    storage: MemoryStorage,
    redis: fakeredis.FakeRedis,
    tmp_path: Path,
    name: str,
    args: list[str],
) -> None:
    media = make_media(tmp_path, name, *args)
    rec = create_import(ana, filename=name)
    chunks = upload_chunks(ana, rec["id"], media, size=max(1024, len(media) // 3))
    assert chunks >= 2
    complete(ana, rec["id"], chunks)

    scanner = FakeScanner(ScanResult(clean=True))
    result = run_worker(db, storage, redis, rec["id"], scanner)

    # El antivirus recibió el archivo completo, reconstruido en orden.
    assert scanner.scanned == [media]
    assert result.status is RecordingStatus.PENDING
    assert (
        result.audio_object_key
        == f"orgs/{rec['organization_id']}/recordings/{rec['id']}/audio/source.ogg"
    )
    assert 2_800 <= result.duration_ms <= 3_200
    # Solo queda el audio extraído; los bloques originales se borran.
    assert list(storage.objects) == [result.audio_object_key]
    assert db.scalars(select(AudioSegment)).all() == []

    out = tmp_path / "out.ogg"
    out.write_bytes(storage.get(result.audio_object_key))
    info = probe(out)
    assert info.has_audio and "ogg" in info.format_name

    detail = ana.get(f"/v1/recordings/{rec['id']}").json()
    assert detail["audio_ready"] is True
    # Idempotente: una segunda ejecución no hace nada.
    run_worker(db, storage, redis, rec["id"], scanner)
    assert len(scanner.scanned) == 1


@needs_ffmpeg
def test_malware_is_rejected_and_audited(
    ana: ApiClient,
    tasks: RecordingTasks,
    db: Session,
    storage: MemoryStorage,
    redis: fakeredis.FakeRedis,
    tmp_path: Path,
) -> None:
    mp3 = make_media(tmp_path, "a.mp3", "-f", "lavfi", "-i", "sine=duration=1")
    rec = create_import(ana)
    complete(ana, rec["id"], upload_chunks(ana, rec["id"], mp3))

    result = run_worker(
        db, storage, redis, rec["id"], FakeScanner(ScanResult(clean=False, signature="Eicar"))
    )
    assert result.status is RecordingStatus.FAILED
    assert result.error_code == "malware_detected"
    assert result.audio_object_key is None
    event = db.scalars(
        select(AuditEvent).where(AuditEvent.action == "recording.import_rejected")
    ).one()
    assert event.event_metadata == {"reason": "malware_detected"}


@needs_ffmpeg
def test_scanner_unavailable_is_not_silently_accepted(
    ana: ApiClient,
    tasks: RecordingTasks,
    db: Session,
    storage: MemoryStorage,
    redis: fakeredis.FakeRedis,
    tmp_path: Path,
) -> None:
    mp3 = make_media(tmp_path, "a.mp3", "-f", "lavfi", "-i", "sine=duration=1")
    rec = create_import(ana)
    complete(ana, rec["id"], upload_chunks(ana, rec["id"], mp3))
    with pytest.raises(ScannerUnavailable):
        run_worker(db, storage, redis, rec["id"], FakeScanner(ScannerUnavailable("down")))
    assert db.get_one(Recording, uuid.UUID(rec["id"])).audio_object_key is None


@needs_ffmpeg
def test_video_without_audio_is_rejected(
    ana: ApiClient,
    tasks: RecordingTasks,
    db: Session,
    storage: MemoryStorage,
    redis: fakeredis.FakeRedis,
    tmp_path: Path,
) -> None:
    mp4 = make_media(
        tmp_path,
        "v.mp4",
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=160x120:rate=10:duration=1",
        "-c:v",
        "libx264",
    )
    rec = create_import(ana, filename="v.mp4")
    complete(ana, rec["id"], upload_chunks(ana, rec["id"], mp4))
    result = run_worker(db, storage, redis, rec["id"])
    assert (result.status, result.error_code) == (RecordingStatus.FAILED, "no_audio")


@needs_ffmpeg
def test_corrupted_chunk_is_detected(
    ana: ApiClient,
    tasks: RecordingTasks,
    db: Session,
    storage: MemoryStorage,
    redis: fakeredis.FakeRedis,
    tmp_path: Path,
) -> None:
    mp3 = make_media(tmp_path, "a.mp3", "-f", "lavfi", "-i", "sine=duration=2")
    rec = create_import(ana)
    complete(ana, rec["id"], upload_chunks(ana, rec["id"], mp3, size=4096))
    key = next(k for k in storage.objects if k.endswith(".part"))
    body, content_type = storage.objects[key]
    storage.objects[key] = (b"X" + body[1:], content_type)

    with pytest.raises(MediaError) as exc:
        run_worker(db, storage, redis, rec["id"])
    assert exc.value.code == "corrupted_upload"


# --- Cliente clamd -------------------------------------------------------------------------


@pytest.fixture
def fake_clamd() -> Iterator[Callable[[bytes], tuple[int, list[bytes]]]]:
    """Servidor clamd de una conexión: registra lo recibido y responde lo indicado."""
    servers: list[socket.socket] = []

    def start(reply: bytes) -> tuple[int, list[bytes]]:
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        servers.append(server)
        received: list[bytes] = []

        def serve() -> None:
            conn, _ = server.accept()
            with conn:
                assert conn.recv(10) == b"zINSTREAM\0"
                while True:
                    (size,) = struct.unpack("!L", conn.recv(4, socket.MSG_WAITALL))
                    if size == 0:
                        break
                    received.append(conn.recv(size, socket.MSG_WAITALL))
                conn.sendall(reply)

        threading.Thread(target=serve, daemon=True).start()
        return server.getsockname()[1], received

    yield start
    for s in servers:
        s.close()


def test_clamd_clean(
    fake_clamd: Callable[[bytes], tuple[int, list[bytes]]], tmp_path: Path
) -> None:
    port, received = fake_clamd(b"stream: OK\0")
    f = tmp_path / "f"
    f.write_bytes(b"a" * 200_000)
    assert ClamdScanner("127.0.0.1", port).scan(f) == ScanResult(clean=True)
    assert b"".join(received) == b"a" * 200_000


def test_clamd_detects_malware(
    fake_clamd: Callable[[bytes], tuple[int, list[bytes]]], tmp_path: Path
) -> None:
    port, _ = fake_clamd(b"stream: Win.Test.EICAR_HDB-1 FOUND\0")
    f = tmp_path / "f"
    f.write_bytes(b"x")
    assert ClamdScanner("127.0.0.1", port).scan(f) == ScanResult(
        clean=False, signature="Win.Test.EICAR_HDB-1"
    )


def test_clamd_unreachable(tmp_path: Path) -> None:
    f = tmp_path / "f"
    f.write_bytes(b"x")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    with pytest.raises(ScannerUnavailable):
        ClamdScanner("127.0.0.1", port, timeout=2).scan(f)
