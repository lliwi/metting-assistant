import hashlib
import json
import uuid
from collections.abc import Callable
from typing import Any

import fakeredis
import pytest
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Membership, Role, User
from tests.conftest import ApiClient, MemoryStorage

pytestmark = pytest.mark.db

Login = Callable[[str], ApiClient]
MakeUser = Callable[..., User]

WEBM_HEADER = b"\x1a\x45\xdf\xa3"


def audio(n: int = 0, size: int = 2048) -> bytes:
    """Bytes con cabecera WebM: basta para la validación de contenedor de esta fase."""
    return WEBM_HEADER + bytes([n % 256]) * size


@pytest.fixture
def ana(make_user: MakeUser, login: Login) -> ApiClient:
    make_user("ana@example.com")
    return login("ana@example.com")


def personal_org(c: ApiClient) -> str:
    return str(c.get("/v1/organizations").json()[0]["id"])


def create_recording(c: ApiClient, org_id: str | None = None, **extra: Any) -> dict[str, Any]:
    res = c.post(
        "/v1/recordings",
        json={"organization_id": org_id or personal_org(c), "title": "Reunión", "consent": True}
        | extra,
    )
    assert res.status_code == 201, res.text
    return dict(res.json())


def upload(
    c: ApiClient,
    rec_id: str,
    *,
    seq: int,
    track: int = 0,
    data: bytes | None = None,
    segment_id: str | None = None,
    sha: str | None = None,
    duration_ms: int = 30_000,
):  # type: ignore[no-untyped-def]
    data = audio(seq) if data is None else data
    return c.post(
        f"/v1/recordings/{rec_id}/segments",
        data={
            "segment_id": segment_id or str(uuid.uuid4()),
            "track": str(track),
            "seq": str(seq),
            "duration_ms": str(duration_ms),
            "sha256": sha or hashlib.sha256(data).hexdigest(),
        },
        files={"file": ("segment.webm", data, "audio/webm")},
    )


# --- Creación y permisos -------------------------------------------------------------------


def test_create_requires_consent(ana: ApiClient) -> None:
    res = ana.post(
        "/v1/recordings",
        json={"organization_id": personal_org(ana), "title": "X", "consent": False},
    )
    assert res.status_code == 422


def test_create_recording(ana: ApiClient) -> None:
    rec = create_recording(ana)
    assert rec["status"] == "uploading"
    assert rec["tracks"] == [{"track": 0, "segments": 0, "duration_ms": 0}]


def test_viewer_cannot_record_and_outsider_cannot_see(
    ana: ApiClient, db: Session, make_user: MakeUser, login: Login
) -> None:
    org_id = ana.post("/v1/organizations", json={"name": "Equipo"}).json()["id"]
    rec = create_recording(ana, org_id)
    viewer = make_user("vic@example.com")
    db.add(Membership(user_id=viewer.id, organization_id=uuid.UUID(org_id), role=Role.VIEWER))
    db.commit()
    vic = login("vic@example.com")

    res = vic.post(
        "/v1/recordings", json={"organization_id": org_id, "title": "X", "consent": True}
    )
    assert res.status_code == 403
    assert vic.get(f"/v1/recordings/{rec['id']}").status_code == 200
    assert upload(vic, rec["id"], seq=0).status_code == 403

    make_user("eve@example.com")
    eve = login("eve@example.com")
    assert eve.get(f"/v1/recordings/{rec['id']}").status_code == 404
    assert upload(eve, rec["id"], seq=0).status_code == 404
    assert (
        eve.post(f"/v1/recordings/{rec['id']}/complete", json={"tracks": {"0": 1}}).status_code
        == 404
    )
    assert eve.get(f"/v1/recordings/{rec['id']}/events").status_code == 404
    assert eve.get("/v1/recordings").json() == []
    res = eve.post(
        "/v1/recordings", json={"organization_id": org_id, "title": "X", "consent": True}
    )
    assert res.status_code == 404


def test_owner_demoted_to_viewer_cannot_keep_uploading(
    ana: ApiClient, db: Session, make_user: MakeUser, login: Login
) -> None:
    org_id = ana.post("/v1/organizations", json={"name": "Equipo"}).json()["id"]
    bob = make_user("bob@example.com")
    membership = Membership(user_id=bob.id, organization_id=uuid.UUID(org_id), role=Role.MEMBER)
    db.add(membership)
    db.commit()
    bob_client = login("bob@example.com")
    rec = create_recording(bob_client, org_id)
    membership.role = Role.VIEWER
    db.commit()
    assert upload(bob_client, rec["id"], seq=0).status_code == 403


# --- Segmentos -----------------------------------------------------------------------------


def test_upload_stores_segment(ana: ApiClient, storage: MemoryStorage) -> None:
    rec = create_recording(ana)
    res = upload(ana, rec["id"], seq=0)
    assert res.status_code == 201, res.text
    [(key, (body, content_type))] = storage.objects.items()
    assert key.startswith(f"orgs/{rec['organization_id']}/recordings/{rec['id']}/tracks/0/00000-")
    assert key.endswith(".webm")
    assert (body, content_type) == (audio(0), "audio/webm")

    detail = ana.get(f"/v1/recordings/{rec['id']}").json()
    assert detail["duration_ms"] == 30_000
    assert detail["tracks"][0]["segments"] == 1


def test_retry_is_idempotent(ana: ApiClient, storage: MemoryStorage) -> None:
    rec = create_recording(ana)
    segment_id = str(uuid.uuid4())
    assert upload(ana, rec["id"], seq=0, segment_id=segment_id).status_code == 201
    res = upload(ana, rec["id"], seq=0, segment_id=segment_id)
    assert res.status_code == 200
    assert res.json()["duplicate"] is True
    assert len(storage.objects) == 1
    assert ana.get(f"/v1/recordings/{rec['id']}").json()["duration_ms"] == 30_000


def test_same_id_with_other_content_conflicts(ana: ApiClient) -> None:
    rec = create_recording(ana)
    segment_id = str(uuid.uuid4())
    upload(ana, rec["id"], seq=0, segment_id=segment_id)
    res = upload(ana, rec["id"], seq=0, segment_id=segment_id, data=audio(9))
    assert res.json()["error"]["code"] == "segment_conflict"


def test_same_position_with_other_id_conflicts(ana: ApiClient) -> None:
    rec = create_recording(ana)
    upload(ana, rec["id"], seq=0)
    res = upload(ana, rec["id"], seq=0, data=audio(9))
    assert res.json()["error"]["code"] == "segment_conflict"


def test_checksum_mismatch_is_rejected(ana: ApiClient, storage: MemoryStorage) -> None:
    rec = create_recording(ana)
    res = upload(ana, rec["id"], seq=0, sha="0" * 64)
    assert res.json()["error"]["code"] == "checksum_mismatch"
    assert storage.objects == {}


def test_non_audio_is_rejected(ana: ApiClient) -> None:
    rec = create_recording(ana)
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    res = upload(ana, rec["id"], seq=0, data=png)
    assert res.status_code == 415


def test_oversized_segment_is_rejected(ana: ApiClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "segment_max_bytes", 1000)
    rec = create_recording(ana)
    res = upload(ana, rec["id"], seq=0, data=audio(0, size=2000))
    assert res.status_code == 413


def test_duration_limit(ana: ApiClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "recording_max_duration_minutes", 1)
    rec = create_recording(ana)
    assert upload(ana, rec["id"], seq=0, duration_ms=40_000).status_code == 201
    res = upload(ana, rec["id"], seq=1, duration_ms=40_000)
    assert res.json()["error"]["code"] == "duration_limit"


def test_unknown_track_is_rejected(ana: ApiClient) -> None:
    rec = create_recording(ana)
    assert upload(ana, rec["id"], seq=0, track=1).json()["error"]["code"] == "unknown_track"


# --- Reanudación y cierre ------------------------------------------------------------------


def test_resume_in_new_track_and_complete(ana: ApiClient) -> None:
    rec = create_recording(ana)
    upload(ana, rec["id"], seq=0)
    upload(ana, rec["id"], seq=1)
    track = ana.post(f"/v1/recordings/{rec['id']}/tracks").json()["track"]
    assert track == 1
    upload(ana, rec["id"], seq=0, track=1)

    res = ana.post(f"/v1/recordings/{rec['id']}/complete", json={"tracks": {"0": 2, "1": 1}})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "pending"
    assert [t["segments"] for t in body["tracks"]] == [2, 1]


def test_complete_reports_missing_segments(ana: ApiClient) -> None:
    rec = create_recording(ana)
    upload(ana, rec["id"], seq=0)
    upload(ana, rec["id"], seq=2)
    res = ana.post(f"/v1/recordings/{rec['id']}/complete", json={"tracks": {"0": 4}})
    assert res.status_code == 409
    assert res.json()["error"]["details"] == [{"track": 0, "seq": 1}, {"track": 0, "seq": 3}]
    assert ana.get(f"/v1/recordings/{rec['id']}").json()["status"] == "uploading"


def test_complete_is_idempotent_and_closes_capture(ana: ApiClient) -> None:
    rec = create_recording(ana)
    upload(ana, rec["id"], seq=0)
    first = ana.post(f"/v1/recordings/{rec['id']}/complete", json={"tracks": {"0": 1}})
    again = ana.post(f"/v1/recordings/{rec['id']}/complete", json={"tracks": {"0": 1}})
    assert first.status_code == again.status_code == 200
    assert upload(ana, rec["id"], seq=1).json()["error"]["code"] == "recording_closed"
    # Un reintento del último segmento ya subido sigue siendo válido tras el cierre.


def test_empty_recording_cannot_complete(ana: ApiClient) -> None:
    rec = create_recording(ana)
    res = ana.post(f"/v1/recordings/{rec['id']}/complete", json={"tracks": {"0": 0}})
    assert res.json()["error"]["code"] == "empty_recording"


def test_retry_after_complete_is_still_idempotent(ana: ApiClient) -> None:
    """El cliente puede perder la respuesta del último segmento y reenviarlo tras el cierre."""
    rec = create_recording(ana)
    segment_id = str(uuid.uuid4())
    upload(ana, rec["id"], seq=0, segment_id=segment_id)
    ana.post(f"/v1/recordings/{rec['id']}/complete", json={"tracks": {"0": 1}})
    assert upload(ana, rec["id"], seq=0, segment_id=segment_id).status_code == 200


# --- Eventos -------------------------------------------------------------------------------


def test_upload_publishes_event(ana: ApiClient, redis: fakeredis.FakeRedis) -> None:
    rec = create_recording(ana)
    pubsub = redis.pubsub()
    pubsub.subscribe(f"recording:{rec['id']}")
    pubsub.get_message(timeout=1)  # confirmación de suscripción
    upload(ana, rec["id"], seq=0)
    message = pubsub.get_message(timeout=1)
    assert message is not None
    assert json.loads(message["data"]) == {
        "type": "segment",
        "track": 0,
        "seq": 0,
        "duration_ms": 30_000,
    }


def test_sse_sends_initial_snapshot(ana: ApiClient) -> None:
    rec = create_recording(ana)
    with ana.stream("GET", f"/v1/recordings/{rec['id']}/events") as res:
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("text/event-stream")
        lines = res.iter_lines()
        assert next(lines) == "event: recording"
        payload = json.loads(next(lines).removeprefix("data: "))
        assert payload["id"] == rec["id"]
        assert payload["status"] == "uploading"
