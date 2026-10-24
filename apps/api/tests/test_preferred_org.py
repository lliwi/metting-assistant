"""Organización preferida: primera de la lista y una sola por usuario."""

from collections.abc import Callable

import pytest

from app.models import User
from tests.conftest import ApiClient

pytestmark = pytest.mark.db


@pytest.fixture
def ana(make_user: Callable[..., User], login: Callable[[str], ApiClient]) -> ApiClient:
    make_user("ana@example.com")
    return login("ana@example.com")


def names(c: ApiClient) -> list[tuple[str, bool]]:
    return [(o["name"], o["is_preferred"]) for o in c.get("/v1/organizations").json()]


def test_preferred_goes_first_and_is_unique(ana: ApiClient) -> None:
    ids = {
        name: ana.post("/v1/organizations", json={"name": name}).json()["id"]
        for name in ("Alfa", "Beta")
    }
    personal = names(ana)[0][0]
    assert names(ana) == [(personal, False), ("Alfa", False), ("Beta", False)]

    assert ana.put(f"/v1/organizations/{ids['Beta']}/preferred").status_code == 204
    assert names(ana) == [("Beta", True), (personal, False), ("Alfa", False)]
    # Marcar otra desmarca la anterior; repetir no cambia nada.
    assert ana.put(f"/v1/organizations/{ids['Alfa']}/preferred").status_code == 204
    assert ana.put(f"/v1/organizations/{ids['Alfa']}/preferred").status_code == 204
    assert names(ana) == [("Alfa", True), (personal, False), ("Beta", False)]

    assert ana.delete(f"/v1/organizations/{ids['Alfa']}/preferred").status_code == 204
    assert names(ana) == [(personal, False), ("Alfa", False), ("Beta", False)]


def test_preference_is_per_user_and_members_only(
    ana: ApiClient, make_user: Callable[..., User], login: Callable[[str], ApiClient]
) -> None:
    org = ana.post("/v1/organizations", json={"name": "Alfa"}).json()["id"]
    make_user("eve@example.com")
    eve = login("eve@example.com")
    assert eve.put(f"/v1/organizations/{org}/preferred").status_code == 404
    ana.put(f"/v1/organizations/{org}/preferred")
    assert all(not preferred for _, preferred in names(eve))
