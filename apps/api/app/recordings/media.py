"""Identificación del contenedor de audio por sus primeros bytes (no por el Content-Type
del cliente, que es manipulable)."""

# Contenedores que produce MediaRecorder según el navegador.
SEGMENT_CONTAINERS = {"webm", "ogg", "mp4"}

EXTENSIONS = {"webm": "webm", "ogg": "ogg", "mp4": "m4a", "wav": "wav", "mp3": "mp3", "aac": "aac"}
MIME_TYPES = {
    "webm": "audio/webm",
    "ogg": "audio/ogg",
    "mp4": "audio/mp4",
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "aac": "audio/aac",
}


def sniff_container(head: bytes) -> str | None:
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "webm"  # EBML (WebM / Matroska)
    if head.startswith(b"OggS"):
        return "ogg"
    if len(head) >= 12 and head[4:8] == b"ftyp":
        return "mp4"
    if head.startswith(b"RIFF") and head[8:12] == b"WAVE":
        return "wav"
    if head.startswith(b"ID3") or (len(head) >= 2 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0):
        # 0xFFF1/0xFFF9 es ADTS (AAC); el resto de sincronismos 0xFFEx, MPEG audio.
        if len(head) >= 2 and head[1] & 0xF6 == 0xF0:
            return "aac"
        return "mp3"
    return None
