from pathlib import Path

from studio.asset_intelligence.character import infer_character_evidence
from studio.asset_intelligence.visual.tagger import TagResult


def test_character_evidence_requires_thresholded_multi_frame_match():
    frames = {
        "a": [Path("a0.jpg"), Path("a1.jpg")],
        "b": [Path("b0.jpg")],
    }

    def tag(paths):
        scores = {"a0.jpg": .4, "a1.jpg": .96, "b0.jpg": .89}
        return [
            TagResult(
                characters={"agatsuma_zenitsu": scores[path.name]},
                general={},
                rating={},
            )
            for path in paths
        ]

    result = infer_character_evidence(
        frames, character="agatsuma_zenitsu", tag=tag, batch_size=2
    )
    assert len(result) == 1
    assert result[0].shot_id == "a"
    assert result[0].matching_frames == 1
    assert result[0].sampled_frames == 2
    assert result[0].representative_frame == "a1.jpg"
