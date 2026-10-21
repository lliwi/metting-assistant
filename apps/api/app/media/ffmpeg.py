"""Llamadas a ffprobe/ffmpeg sobre ficheros locales de un usuario: entrada no confiable.

Se restringen los protocolos a `file` (un fichero manipulado no puede hacer que FFmpeg
abra URLs ni otros ficheros) y cada proceso tiene un tiempo máximo.
"""

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

SAFE_INPUT = ["-nostdin", "-protocol_whitelist", "file"]


class MediaError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code


@dataclass(frozen=True)
class ProbeResult:
    format_name: str
    duration_ms: int
    has_audio: bool


def probe(path: Path, timeout: int = 120) -> ProbeResult:
    try:
        out = subprocess.run(  # noqa: S603 - argumentos fijos, sin shell
            [
                "ffprobe",
                "-v",
                "error",
                "-protocol_whitelist",
                "file",
                "-show_entries",
                "format=format_name,duration:stream=codec_type",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise MediaError("unreadable_media", str(exc)) from exc
    info = json.loads(out.stdout or "{}")
    fmt = info.get("format") or {}
    streams = info.get("streams") or []
    try:
        duration_ms = int(float(fmt.get("duration", 0)) * 1000)
    except (TypeError, ValueError):
        duration_ms = 0
    return ProbeResult(
        format_name=str(fmt.get("format_name", "")),
        duration_ms=duration_ms,
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
    )


def extract_audio(source: Path, target: Path, timeout: int = 3600) -> None:
    """Primera pista de audio → Opus mono 48 kbit/s en Ogg (suficiente para voz)."""
    try:
        subprocess.run(  # noqa: S603 - argumentos fijos, sin shell
            [
                "ffmpeg",
                "-v",
                "error",
                *SAFE_INPUT,
                "-i",
                str(source),
                "-map",
                "0:a:0",
                "-vn",
                "-ac",
                "1",
                "-c:a",
                "libopus",
                "-b:a",
                "48k",
                "-y",
                str(target),
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise MediaError("unreadable_media", str(exc)) from exc
