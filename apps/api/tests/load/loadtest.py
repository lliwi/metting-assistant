"""Prueba de carga de la API (la lanza scripts/loadtest.sh dentro del contenedor del worker).

Cada usuario virtual inicia sesión y, durante DURATION segundos, simula una reunión que se
graba (sube segmentos de audio como el navegador, cada uno ~SEGMENT_INTERVAL s) mientras
consulta la aplicación (lista, búsqueda, detalle, transcripción, audio). Las grabaciones no
se cierran: no se transcribe nada (sin coste). Al terminar, la organización de pruebas se
elimina y con ella todo lo creado.
"""

import asyncio
import base64
import hashlib
import os
import random
import statistics
import time
import uuid
from collections import defaultdict

import httpx

BASE = os.environ.get("BASE_URL", "http://api:8000")
EMAILS = os.environ["EMAILS"].split(",")
PASSWORD = os.environ["PASSWORD"]
ORG_NAME = os.environ["ORG_NAME"]
DURATION = float(os.environ.get("DURATION", "60"))
SEGMENT = base64.b64decode(os.environ["SEGMENT_B64"])
SEGMENT_SHA = hashlib.sha256(SEGMENT).hexdigest()
# El navegador sube un segmento cada 30 s; aquí se comprime el tiempo para meter más carga.
SEGMENT_INTERVAL = float(os.environ.get("SEGMENT_INTERVAL", "2"))
THINK = (0.2, 1.0)  # pausa entre consultas de un usuario (s)

latencies: dict[str, list[float]] = defaultdict(list)
statuses: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
errors: dict[str, int] = defaultdict(int)


async def call(client: httpx.AsyncClient, name: str, method: str, url: str, **kw):  # type: ignore[no-untyped-def]
    started = time.perf_counter()
    try:
        res = await client.request(method, url, **kw)
    except httpx.HTTPError as exc:
        errors[name] += 1
        print(f"  {name}: {type(exc).__name__}")
        return None
    latencies[name].append(time.perf_counter() - started)
    statuses[name][res.status_code] += 1
    return res


async def login(email: str) -> httpx.AsyncClient:
    client = httpx.AsyncClient(base_url=BASE, timeout=30)
    csrf = (await client.get("/v1/auth/csrf")).json()["csrf_token"]
    for _ in range(30):
        res = await client.post(
            "/v1/auth/login",
            json={"email": email, "password": PASSWORD},
            headers={"X-CSRF-Token": csrf},
        )
        if res.status_code != 429:  # el login admite 30 por minuto y por IP
            break
        await asyncio.sleep(5)
    res.raise_for_status()
    client.headers["X-CSRF-Token"] = client.cookies["ma_csrf"]
    return client


async def recorder(client: httpx.AsyncClient, org_id: str, deadline: float) -> None:
    res = await call(
        client, "POST /recordings", "POST", "/v1/recordings",
        json={"organization_id": org_id, "title": "Carga", "consent": True},
    )  # fmt: skip
    if res is None or res.status_code != 201:
        return
    rid = res.json()["id"]
    seq = 0
    while time.monotonic() < deadline:
        await call(
            client, "POST /segments", "POST", f"/v1/recordings/{rid}/segments",
            data={"segment_id": str(uuid.uuid4()), "track": "0", "seq": str(seq),
                  "duration_ms": "30000", "sha256": SEGMENT_SHA},
            files={"file": (f"{seq}.ogg", SEGMENT, "audio/ogg")},
        )  # fmt: skip
        seq += 1
        await asyncio.sleep(SEGMENT_INTERVAL)


async def reader(client: httpx.AsyncClient, deadline: float) -> None:
    while time.monotonic() < deadline:
        res = await call(client, "GET /recordings", "GET", "/v1/recordings")
        ids = [r["id"] for r in res.json()] if res is not None and res.status_code == 200 else []
        await call(client, "GET /recordings?q=", "GET", "/v1/recordings", params={"q": "carga"})
        if ids:
            rid = random.choice(ids)
            await call(client, "GET /recordings/{id}", "GET", f"/v1/recordings/{rid}")
            await call(client, "GET /me", "GET", "/v1/auth/me")
        await asyncio.sleep(random.uniform(*THINK))


async def user(email: str, org_id: str, deadline: float) -> None:
    client = await login(email)
    try:
        await asyncio.gather(recorder(client, org_id, deadline), reader(client, deadline))
    finally:
        await client.aclose()


def pct(values: list[float], p: float) -> float:
    return statistics.quantiles(values, n=100)[int(p) - 1] * 1000 if len(values) > 1 else 0.0


async def main() -> int:
    owner = await login(EMAILS[0])
    orgs = (await owner.get("/v1/organizations")).json()
    org_id = next(o["id"] for o in orgs if o["name"] == ORG_NAME)
    print(f"{len(EMAILS)} usuarios durante {DURATION:.0f} s contra {BASE}")
    started = time.monotonic()
    deadline = started + DURATION
    await asyncio.gather(*(user(e, org_id, deadline) for e in EMAILS))
    elapsed = time.monotonic() - started

    total = sum(len(v) for v in latencies.values())
    print(f"\n{'Petición':<22}{'n':>7}{'p50 ms':>9}{'p95 ms':>9}{'p99 ms':>9}  códigos")
    for name in sorted(latencies):
        values = latencies[name]
        codes = " ".join(f"{c}:{n}" for c, n in sorted(statuses[name].items()))
        print(
            f"{name:<22}{len(values):>7}{statistics.median(values) * 1000:>9.0f}"
            f"{pct(values, 95):>9.0f}{pct(values, 99):>9.0f}  {codes}"
        )
    server_errors = sum(n for s in statuses.values() for c, n in s.items() if c >= 500)
    network = sum(errors.values())
    throttled = sum(n for s in statuses.values() for c, n in s.items() if c == 429)
    print(
        f"\nTotal: {total} peticiones en {elapsed:.0f} s ({total / elapsed:.1f}/s); "
        f"5xx: {server_errors}; errores de red: {network}; 429: {throttled}"
    )

    # Limpieza: la organización de pruebas y todo lo subido.
    res = await owner.request(
        "DELETE", f"/v1/organizations/{org_id}", json={"confirm_name": ORG_NAME}
    )
    print(f"Organización de pruebas eliminada: {res.status_code == 204}")
    await owner.aclose()
    return 1 if server_errors or network else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
