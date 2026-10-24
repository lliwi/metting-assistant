"""Métricas Prometheus: peticiones HTTP por ruta y estado del sistema."""

from pathlib import Path

import pytest

from tests.conftest import ApiClient
from tests.test_pipeline import Queue, ana, queue, record  # noqa: F401 - fixtures

pytestmark = pytest.mark.db


def value(text: str, sample: str) -> float:
    """Valor de una muestra exacta (nombre y etiquetas) en el formato de texto de Prometheus."""
    for line in text.splitlines():
        if line.startswith(sample + " "):
            return float(line.split()[-1])
    raise AssertionError(f"Sin muestra {sample}")


def test_metrics_report_http_and_system_state(
    ana: ApiClient,  # noqa: F811
    queue: Queue,  # noqa: F811
    tmp_path: Path,
) -> None:
    record(ana, tmp_path)
    ana.get("/v1/recordings")

    res = ana.get("/metrics")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/plain")
    text = res.text
    # Ruta como plantilla, no con el id concreto (cardinalidad acotada).
    assert value(text, 'http_requests_total{method="GET",route="/v1/recordings",status="200"}') >= 1
    assert 'route="/v1/recordings/{recording_id}/complete"' in text
    assert value(text, 'metting_recordings{status="pending"}') == 1
    assert value(text, "metting_recordings_stuck") == 0
    assert value(text, 'metting_dependency_up{name="database"}') == 1
    assert "metting_ai_cost_month_usd" in text
    assert 'metting_queue_length{queue="asr"}' in text
