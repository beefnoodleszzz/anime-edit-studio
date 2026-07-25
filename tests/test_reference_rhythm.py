from anime import director, reference


def test_duration_clusters_preserve_burst_and_breath_phrases():
    clusters = reference._duration_clusters([1.0, 0.5, 2.0, 4.0, 1.0])
    assert [item["kind"] for item in clusters] == ["burst", "breath", "burst"]
    assert clusters[1]["shot_count"] == 2
    assert clusters[1]["total_beats"] == 6.0


def test_reference_steps_map_to_target_beat_grid():
    assert director._cuts_from_reference(12, [4.0, 2.0, 1.0, 1.0]) == [0, 4, 6, 7, 8]


def test_reference_steps_ignore_missing_pattern():
    assert director._cuts_from_reference(12, []) == []


def test_showcase_can_offset_reference_phrase_after_its_intro():
    body = [index + 1 for index in director._cuts_from_reference(11, [4.0, 2.0, 1.0])]
    assert body[:4] == [1, 5, 7, 8]
