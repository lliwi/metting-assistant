"""Cliente mínimo de clamd (protocolo INSTREAM por TCP)."""

import socket
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.core.config import get_settings

CHUNK = 64 * 1024


class ScannerUnavailable(Exception):
    pass


@dataclass(frozen=True)
class ScanResult:
    clean: bool
    signature: str | None = None


class Scanner(Protocol):
    def scan(self, path: Path) -> ScanResult: ...


class ClamdScanner:
    def __init__(self, host: str, port: int, timeout: float = 120.0) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout

    def scan(self, path: Path) -> ScanResult:
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
                sock.sendall(b"zINSTREAM\0")
                with path.open("rb") as f:
                    while chunk := f.read(CHUNK):
                        sock.sendall(struct.pack("!L", len(chunk)) + chunk)
                sock.sendall(struct.pack("!L", 0))
                reply = b""
                while not reply.endswith(b"\0"):
                    data = sock.recv(4096)
                    if not data:
                        break
                    reply += data
        except OSError as exc:
            raise ScannerUnavailable(str(exc)) from exc

        text = reply.rstrip(b"\0").decode(errors="replace")
        # "stream: OK" | "stream: <firma> FOUND" | "INSTREAM size limit exceeded. ERROR"
        if text.endswith(" OK"):
            return ScanResult(clean=True)
        if text.endswith(" FOUND"):
            return ScanResult(
                clean=False, signature=text.removeprefix("stream: ").removesuffix(" FOUND")
            )
        raise ScannerUnavailable(text or "respuesta vacía")


class DisabledScanner:
    """Solo para desarrollo: no analiza nada."""

    def scan(self, path: Path) -> ScanResult:
        return ScanResult(clean=True)


def get_scanner() -> Scanner:
    settings = get_settings()
    if settings.antivirus_mode == "disabled":
        if settings.is_production:
            raise ScannerUnavailable("El antivirus no puede desactivarse en producción")
        return DisabledScanner()
    return ClamdScanner(settings.clamd_host, settings.clamd_port)
