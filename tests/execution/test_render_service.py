from pathlib import Path

from studio.core.database import connect
from studio.editspec.schema import Canvas, EditSpec, Timebase
from studio.execution.render import render_spec
from studio.execution.resolve import RenderResult


class FakeAdapter:
    def render(self, **kwargs):
        output = kwargs["output_dir"] / f"{kwargs['name']}.mov"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"video")
        return RenderResult("job", output, {"CompletionPercentage": 100})


def test_render_service_is_resolve_only_and_persistent(tmp_path):
    conn = connect(tmp_path / "v2.sqlite")
    spec = EditSpec(
        id="p",
        revision=1,
        timebase=Timebase(num=24),
        canvas=Canvas(width=1080, height=1350),
    )
    render_id, result = render_spec(
        FakeAdapter(), conn, spec,
        kind="preview", output_dir=tmp_path / "renders",
    )
    row = conn.execute("SELECT * FROM renders WHERE id=?", (render_id,)).fetchone()
    assert row["backend"] == "resolve"
    assert row["status"] == "complete"
    assert Path(row["output_path"]) == result.output
