from app.storage import get_storage


def test_client_only_uses_checksums_when_required() -> None:
    """Regresión: con la validación por defecto, leer de Garage un objeto subido por partes
    fallaba con FlexibleChecksumError y las grabaciones largas no se transcribían."""
    config = get_storage().client.meta.config
    assert config.request_checksum_calculation == "when_required"
    assert config.response_checksum_validation == "when_required"
