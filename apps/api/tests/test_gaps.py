"""Huecos de audio: dónde faltó audio y cómo se avisa (sin inventar lo que no se grabó)."""

from app.ai.documents import GAP_IDX, Line, format_lines, with_gaps
from app.exports.builders import ExportContext, Segment, to_markdown, to_srt, to_text
from app.media.gaps import AudioGap, Piece, describe, locate, located_gaps


def test_missing_segments_in_the_middle_and_at_the_end() -> None:
    pieces = [
        Piece(0, 0, 30_000, 30_000),
        Piece(0, 1, None),
        Piece(0, 2, None),
        Piece(0, 3, 30_000, 30_000),
        Piece(0, 4, None),
    ]
    assert locate(pieces, 60_000) == [
        AudioGap(30_000, "missing", lost_ms=60_000, segments=2),
        AudioGap(60_000, "missing", lost_ms=30_000, segments=1),
    ]


def test_resumed_track_is_an_interruption() -> None:
    pieces = [Piece(0, 0, 30_000, 30_000), Piece(1, 0, 20_000, 20_000)]
    assert locate(pieces, 50_000) == [AudioGap(30_000, "interruption")]


def test_dropped_samples_in_consecutive_segments_are_one_warning() -> None:
    pieces = [
        Piece(0, 0, 30_000, 30_000),
        Piece(0, 1, 10_000, 30_000),  # el navegador perdió 20 s
        Piece(0, 2, 15_000, 30_000),  # y 15 s más, seguidos
        Piece(0, 3, 29_500, 30_000),  # medio segundo: normal, no es pérdida
    ]
    assert locate(pieces, 84_500) == [AudioGap(30_000, "dropped", lost_ms=35_000)]


def test_positions_are_scaled_to_the_joined_audio() -> None:
    # Al unir, FFmpeg rellena con silencio: el audio final dura más que la suma medida.
    pieces = [Piece(0, 0, 30_000, 30_000), Piece(0, 1, None), Piece(0, 2, 30_000, 30_000)]
    [gap] = locate(pieces, 66_000)
    assert gap.start_ms == 33_000


def test_unlocated_entries_are_not_shown() -> None:
    # Antes de unir el audio solo se sabe qué segmentos faltaban, no dónde.
    stored = [{"track": 0, "seq": 3}, {"start_ms": 5_000, "kind": "interruption"}]
    assert located_gaps(stored) == [AudioGap(5_000, "interruption")]


def test_exports_mark_the_gap_where_it_happened() -> None:
    from datetime import datetime

    ctx = ExportContext(
        title="Reunión",
        created_at=datetime(2026, 11, 7, 10, 0),
        duration_ms=90_000,
        locale="es",
        segments=[
            Segment(0, 5_000, "H1", "Empezamos."),
            Segment(65_000, 70_000, "H1", "Seguimos."),
        ],
        gaps=[AudioGap(30_000, "missing", lost_ms=30_000, segments=1)],
    )
    text = to_text(ctx).decode()
    assert text.index("Empezamos.") < text.index("Audio perdido") < text.index("Seguimos.")
    assert "[00:30] ⚠ Audio perdido: faltan 1 fragmento(s), unos 30 s" in text
    assert "Audio perdido" in to_markdown(ctx).decode()
    # En los subtítulos no: solo llevan lo que se dijo.
    assert "Audio perdido" not in to_srt(ctx).decode()


def test_llm_sees_gaps_as_lines_it_cannot_cite() -> None:
    lines = with_gaps(
        [Line(0, 0, "H1", "Empezamos."), Line(1, 65_000, "H1", "Seguimos.")],
        [AudioGap(30_000, "dropped", lost_ms=20_000)],
    )
    assert [line.idx for line in lines] == [0, GAP_IDX, 1]
    text = format_lines(lines, {})
    assert "(00:30) [AUDIO PERDIDO] El navegador perdió unos 20 s" in text
    assert "[-1]" not in text


def test_catalan_description() -> None:
    assert describe(AudioGap(0, "interruption"), "ca").startswith("L'enregistrament")
