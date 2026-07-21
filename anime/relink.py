"""Create a derived EditSpec that points to upgraded local source files."""
from __future__ import annotations

import json
from pathlib import Path


def relink_editspec(editspec_path: str, sources: list[str]) -> dict:
    """Replace source paths by stable source-id mappings (``id=/absolute/file``)."""
    mapping: dict[str, str] = {}
    for item in sources:
        key, sep, value = item.partition("=")
        if not sep or not key or not value:
            raise ValueError("每个 --source 必须是 source-id=/absolute/path")
        path = Path(value).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        mapping[key] = str(path)

    spec = json.loads(Path(editspec_path).read_text())
    changed = 0
    for shot in spec["shots"]:
        for key, path in mapping.items():
            if key in Path(shot["src"]).name:
                shot["src"] = path
                changed += 1
                break
    if changed != len(spec["shots"]):
        raise ValueError(f"只重连了 {changed}/{len(spec['shots'])} 个镜头；检查 source-id")

    p = Path(editspec_path)
    out_path = p.with_name(p.name[: -len(".json")] + ".relinked.json")
    out_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2))
    return {"editspec": str(out_path), "relinked_shots": changed,
            "source_count": len(mapping)}
