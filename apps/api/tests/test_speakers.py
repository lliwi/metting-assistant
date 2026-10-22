from app.ai.asr import AsrSegment
from app.ai.speakers import SpeakerTurn, assign_speakers, merge_turns, prepare_speakers


def seg(start: int, end: int, text: str, speaker: str | None = None) -> AsrSegment:
    return AsrSegment(start, end, text, 0.9, speaker)


def test_assign_by_largest_overlap() -> None:
    turns = [SpeakerTurn(0, 5_000, "SPEAKER_01"), SpeakerTurn(5_000, 12_000, "SPEAKER_00")]
    result = assign_speakers(
        [seg(0, 4_000, "a"), seg(4_000, 9_000, "b"), seg(20_000, 21_000, "c")], turns
    )
    assert [s.speaker for s in result] == ["SPEAKER_01", "SPEAKER_00", None]


def test_labels_follow_order_of_appearance() -> None:
    result = prepare_speakers(
        [
            seg(0, 1_000, "Hola", "B"),
            seg(1_000, 2_000, "Hola", "A"),
            seg(9_000, 10_000, "¿Sí?", "B"),
        ]
    )
    assert [s.speaker for s in result] == ["H1", "H2", "H1"]


def test_consecutive_fragments_of_same_speaker_are_merged() -> None:
    # Caso real de gpt-4o-transcribe-diarize: frases partidas ("Laura," suelto).
    result = merge_turns(
        [
            seg(5_200, 5_550, "Laura,", "H1"),
            seg(5_850, 8_650, "¿puedes preparar el presupuesto?", "H1"),
            seg(10_050, 10_300, "Sí,", "H2"),
            seg(10_550, 12_450, "lo envío el viernes.", "H2"),
        ]
    )
    assert [(s.speaker, s.text, s.start_ms, s.end_ms) for s in result] == [
        ("H1", "Laura, ¿puedes preparar el presupuesto?", 5_200, 8_650),
        ("H2", "Sí, lo envío el viernes.", 10_050, 12_450),
    ]


def test_long_pause_or_long_turn_starts_new_paragraph() -> None:
    long_pause = merge_turns([seg(0, 1_000, "a", "H1"), seg(5_000, 6_000, "b", "H1")])
    too_long = merge_turns([seg(0, 20_000, "a", "H1"), seg(20_100, 35_000, "b", "H1")])
    assert len(long_pause) == len(too_long) == 2


def test_without_speakers_nothing_changes() -> None:
    segments = [seg(0, 1_000, "a"), seg(1_000, 2_000, "b")]
    assert prepare_speakers(segments) == segments
