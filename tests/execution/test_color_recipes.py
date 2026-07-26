from pathlib import Path

from studio.execution.color import build_color_recipe_library


def test_color_recipe_library_has_bounded_17_cube(tmp_path: Path):
    paths = build_color_recipe_library(tmp_path)
    assert len(paths) == 6
    for path in paths:
        lines = path.read_text().splitlines()
        assert "LUT_3D_SIZE 17" in lines
        data = [line for line in lines if line and line[0].isdigit()]
        assert len(data) == 17**3
        values = [float(value) for line in data for value in line.split()]
        assert min(values) >= 0.0
        assert max(values) <= 1.0


def test_color_recipe_outputs_are_not_identical(tmp_path: Path):
    paths = build_color_recipe_library(tmp_path)
    assert len({path.read_bytes() for path in paths}) == 6
