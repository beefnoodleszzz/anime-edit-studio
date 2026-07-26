from pathlib import Path

from studio.core.database import connect
from studio.editspec.schema import (
    Canvas,
    Clip,
    EditSpec,
    SourceRange,
    Timebase,
    TimelinePlacement,
)
from studio.growth import (
    GrowthMetrics,
    create_experiment,
    ingest_metrics,
    retention_preferences,
)


def _spec(project: str, shots: tuple[str, str]) -> EditSpec:
    return EditSpec(
        id=project,
        timebase=Timebase(num=24),
        canvas=Canvas(width=1080, height=1350),
        clips=[
            Clip(
                id=f"c{i}", asset_id="a", shot_id=shot,
                source=SourceRange(in_sec=i, out_sec=i + 1),
                timeline=TimelinePlacement(in_sec=i, duration_sec=1),
            )
            for i, shot in enumerate(shots)
        ],
    )


def test_retention_metrics_become_signal_only_preferences(tmp_path: Path):
    conn = connect(tmp_path / "v2.sqlite")
    with conn:
        conn.execute(
            "INSERT INTO assets(id,path,sha256,fps_num,fps_den,duration_sec) "
            "VALUES ('a','x','hash',24,1,4)"
        )
        conn.executemany(
            "INSERT INTO shots(id,asset_id,idx,start_sec,end_sec) VALUES (?,?,?,?,?)",
            [("good", "a", 0, 0, 1), ("bad", "a", 1, 1, 2)],
        )
    experiment_id = create_experiment(
        conn,
        project_id="p",
        name="hook-test",
        base_spec_path=tmp_path / "base.json",
        platform="tiktok",
        variants=[
            ("A", "hook a", "", tmp_path / "a.json"),
            ("B", "hook b", "", tmp_path / "b.json"),
        ],
    )
    variants = conn.execute(
        "SELECT id,label FROM growth_variants WHERE experiment_id=? ORDER BY label",
        (experiment_id,),
    ).fetchall()
    ingest_metrics(
        conn,
        variant_id=variants[0]["id"],
        metrics=GrowthMetrics(
            views=1000,
            retention_curve=[(0, 1), (1, 0.95), (2, 0.9)],
        ),
        spec=_spec("p", ("good", "good")),
    )
    ingest_metrics(
        conn,
        variant_id=variants[1]["id"],
        metrics=GrowthMetrics(
            views=1000,
            retention_curve=[(0, 1), (1, 0.7), (2, 0.4)],
        ),
        spec=_spec("p", ("bad", "bad")),
    )
    assert retention_preferences(conn, experiment_id=experiment_id) == 2
    rows = conn.execute("SELECT * FROM preference_pairs").fetchall()
    assert all(row["winner_shot_id"] == "good" for row in rows)
    assert all("signal_only" in row["context_json"] for row in rows)
