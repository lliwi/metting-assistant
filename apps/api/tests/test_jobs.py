"""Fiabilidad de los trabajos: cerrojos, marca de cola y barrido de trabajos perdidos."""

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
from app.worker.jobs import exclusive, is_busy, mark_queued
from app.worker.sweeper import sweep


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
