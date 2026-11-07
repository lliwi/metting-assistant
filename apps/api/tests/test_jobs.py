"""Fiabilidad de los trabajos: cerrojos, marca de cola y barrido de trabajos perdidos."""

import time
import uuid
from collections.abc import Callable
from datetime import timedelta

import fakeredis
import pytest
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.auth.sessions import now
from app.models import (
    AiOutput,
    AiOutputKind,
    AiOutputStatus,
    Organization,
    Recording,
    RecordingStatus,
    Transcript,
    User,
)
from app.worker import tasks
from app.worker.jobs import LOCK_TTL, exclusive, is_busy, mark_queued
from app.worker.sweeper import MAX_REQUEUES, sweep


def test_exclusive_allows_a_single_runner() -> None:
    redis = fakeredis.FakeRedis()
    job = uuid.uuid4()
    with exclusive(redis, "recording", job, ttl=60) as first:
        assert first
        with exclusive(redis, "recording", job, ttl=60) as second:
            assert not second
    with exclusive(redis, "recording", job, ttl=60) as again:
        assert again


def test_starting_clears_the_queued_mark() -> None:
    redis = fakeredis.FakeRedis()
    job = uuid.uuid4()
    mark_queued(redis, "recording", job)
    assert is_busy(redis, "recording", job)
    with exclusive(redis, "recording", job, ttl=60):
        assert is_busy(redis, "recording", job)  # ahora por el cerrojo
    assert not is_busy(redis, "recording", job)


@pytest.mark.db
def test_sweeper_requeues_only_lost_jobs(db: Session, make_user: Callable[..., User]) -> None:
    redis = fakeredis.FakeRedis()
    owner = make_user("ana@example.com")
    org = db.query(Organization).first()
    assert org

    def recording(status: RecordingStatus, minutes_ago: int) -> Recording:
        rec = Recording(
            organization_id=org.id,
            owner_id=owner.id,
            title="R",
            status=status,
            consent_confirmed_at=now(),
        )
        db.add(rec)
        db.flush()
        db.execute(
            update(Recording)
            .where(Recording.id == rec.id)
            .values(updated_at=now() - timedelta(minutes=minutes_ago))
        )
        return rec

    lost = recording(RecordingStatus.PENDING, 30)
    crashed = recording(RecordingStatus.PROCESSING, 60)
    recent = recording(RecordingStatus.PENDING, 1)
    queued = recording(RecordingStatus.PENDING, 30)
    running = recording(RecordingStatus.PROCESSING, 60)
    done = recording(RecordingStatus.TRANSCRIBED, 60)
    transcript = Transcript(
        recording_id=done.id, version=1, language="es", provider="x", model="x",
        content="", processing_ms=0,
    )  # fmt: skip
    db.add(transcript)
    db.flush()
    output = AiOutput(
        recording_id=done.id,
        transcript_id=transcript.id,
        kind=AiOutputKind.SUMMARY,
        status=AiOutputStatus.PENDING,
        prompt_version="v",
        content={},
    )
    db.add(output)
    db.flush()
    db.execute(
        update(AiOutput)
        .where(AiOutput.id == output.id)
        .values(updated_at=now() - timedelta(minutes=30))
    )
    db.commit()

    mark_queued(redis, "recording", queued.id)
    redis.set(f"lock:recording:{running.id}", "token")

    requeued: list[uuid.UUID] = []
    outputs: list[uuid.UUID] = []
    assert sweep(db, redis, requeued.append, outputs.append) == (2, 1)
    assert set(requeued) == {lost.id, crashed.id}
    assert outputs == [output.id]
    assert recent.id not in requeued


def test_lock_is_renewed_while_the_job_runs() -> None:
    """El cerrojo dura poco, pero no caduca mientras el trabajo sigue vivo."""
    redis = fakeredis.FakeRedis()
    job = uuid.uuid4()
    with exclusive(redis, "recording", job, ttl=1) as owned:
        assert owned
        time.sleep(2.5)  # más del doble de su vida sin renovar
        assert redis.exists(f"lock:recording:{job}")
        with exclusive(redis, "recording", job, ttl=1) as other:
            assert not other
    assert not redis.exists(f"lock:recording:{job}")


def test_lock_of_a_dead_worker_expires_soon() -> None:
    redis = fakeredis.FakeRedis()
    job = uuid.uuid4()
    # Un worker que muere no libera ni renueva: solo queda el cerrojo con su vida corta.
    redis.lock(f"lock:recording:{job}", timeout=LOCK_TTL).acquire(blocking=False)
    ttl = redis.ttl(f"lock:recording:{job}")
    assert 0 < ttl <= 300


def _stuck_recording(db: Session, owner: User, status: RecordingStatus) -> Recording:
    org = db.query(Organization).first()
    assert org
    rec = Recording(
        organization_id=org.id,
        owner_id=owner.id,
        title="R",
        status=status,
        consent_confirmed_at=now(),
    )
    db.add(rec)
    db.flush()
    db.execute(
        update(Recording)
        .where(Recording.id == rec.id)
        .values(updated_at=now() - timedelta(minutes=30))
    )
    db.commit()
    return rec


@pytest.mark.db
def test_sweeper_gives_up_on_a_job_that_never_finishes(
    db: Session, make_user: Callable[..., User]
) -> None:
    redis = fakeredis.FakeRedis()
    rec = _stuck_recording(db, make_user("ana@example.com"), RecordingStatus.PROCESSING)

    requeued: list[uuid.UUID] = []
    for _ in range(MAX_REQUEUES):
        sweep(db, redis, requeued.append, lambda _: None)
    assert requeued == [rec.id] * MAX_REQUEUES

    sweep(db, redis, requeued.append, lambda _: None)
    assert len(requeued) == MAX_REQUEUES
    db.expire_all()
    failed = db.get_one(Recording, rec.id)
    assert (failed.status, failed.error_code) == (RecordingStatus.FAILED, "processing_stuck")


@pytest.mark.db
def test_unexpected_error_is_retried_then_marked_failed(
    db: Session,
    make_user: Callable[..., User],
    redis: fakeredis.FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rec = _stuck_recording(db, make_user("ana@example.com"), RecordingStatus.PROCESSING)

    attempts: list[int] = []

    def broken(*_: object, **__: object) -> None:
        attempts.append(1)
        raise RuntimeError("fallo inesperado")

    monkeypatch.setattr(tasks, "process_recording", broken)
    monkeypatch.setattr(tasks, "get_redis", lambda: redis)
    for name in ("get_storage", "get_scanner", "get_asr_provider", "get_diarizer"):
        monkeypatch.setattr(tasks, name, lambda *_: None)

    # En local, Celery encadena los reintentos en el acto: primer intento y sus reintentos.
    tasks.process_recording_task.apply(args=[str(rec.id)])

    assert len(attempts) == 1 + tasks.UNEXPECTED_RETRIES
    db.expire_all()
    failed = db.get_one(Recording, rec.id)
    # Fallida, con un motivo que la interfaz sabe mostrar (antes: "procesando" para siempre).
    assert (failed.status, failed.error_code) == (RecordingStatus.FAILED, "processing_failed")
    assert not redis.exists(f"lock:recording:{rec.id}")
