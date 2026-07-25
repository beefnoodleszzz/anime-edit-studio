"""本地素材库入库与磁盘回收。

- `add`:  给一个视频 → ffprobe 探测 → 规范化成纯 ASCII 文件名 → 归位到
          sources_root/<series>/<season>/ → 可选直接 ingest + 登记来源。
- `clean`: 交付后回收可再生的中间件/缓存(渲染分段、RIFE/超分/ProRes 缓存、
          Remotion 临时包、项目预览),永不动源文件、engine.sqlite、母版与关键帧。

注意:ingest 只记录路径不复制,assets.path 永久指向 sources_root 里的文件——
入库后不要再移动或改名,否则回源渲染/relink/candidates 会断链。
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from . import cache, candidates, config, ingest as ingest_mod

# 文件名字段一律小写 ASCII(禁止中文/空格/大写)。
_SLUG_RE = re.compile(r"^[a-z0-9]+$")
_KIND_RE = re.compile(r"^(?:ep|ncop|nced|op|ed|pv|cm|clip)\d*$")
_TIERS = {"bd", "raw", "web", "clip"}
_CODEC_SHORT = {"hevc": "hevc", "h265": "hevc", "x265": "hevc",
                "h264": "avc", "avc": "avc", "x264": "avc",
                "av1": "av1", "vp9": "vp9"}
_CLEAN_KINDS = {"ncop", "nced", "op", "ed", "pv"}  # 天然无字幕/无字的干净片源类型


def sources_root() -> Path:
    """素材库根目录。优先 ANIME_MATERIAL_ROOT 环境变量,其次 config [library] sources_root。"""
    env = os.environ.get("ANIME_MATERIAL_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    raw = config.get("library", "sources_root", None)
    if raw:
        return Path(str(raw)).expanduser().resolve()
    return (config.ROOT / "material-library").resolve()


def _codec_short(name: str | None) -> str:
    return _CODEC_SHORT.get((name or "").lower(), re.sub(r"[^a-z0-9]", "", (name or "src").lower()) or "src")


def _fps_token(fps: float | None) -> str:
    value = round(float(fps or 0))
    return f"{value or 'na'}fps"


def _validate_fields(series: str, season: str, kind: str, tier: str) -> None:
    for label, value in (("series", series), ("season", season)):
        if not _SLUG_RE.match(value or ""):
            raise ValueError(f"{label} 必须是小写 ASCII 字母/数字(如 frieren、s2),收到: {value!r}")
    if not _KIND_RE.match(kind or ""):
        raise ValueError(f"kind 必须是 ep/ncop/nced/op/ed/pv/cm/clip(可带序号,如 ep05),收到: {kind!r}")
    if tier not in _TIERS:
        raise ValueError(f"tier 必须是 {sorted(_TIERS)} 之一,收到: {tier!r}")


def _probe(path: Path) -> dict:
    meta = ingest_mod._ffprobe(str(path))
    meta["subtitle_tracks"] = candidates._subtitle_streams(path) if path.exists() else 0
    return meta


def normalized_name(series: str, season: str, kind: str, tier: str, meta: dict, ext: str) -> str:
    height = meta.get("height") or 0
    res = f"{height}p" if height else "src"
    codec = _codec_short(meta.get("codec"))
    ext = (ext or "").lower().lstrip(".") or "mp4"
    return f"{series}_{season}_{kind}_{tier}_{res}_{_fps_token(meta.get('fps'))}_{codec}.{ext}"


def _warnings(meta: dict, kind: str) -> list[str]:
    out = []
    short_edge = min(meta.get("width") or 0, meta.get("height") or 0)
    if short_edge and short_edge < 1080:
        out.append(f"短边 {short_edge}p 低于 1080,不建议进入正式发布")
    elif (meta.get("height") or 0) < 2160:
        out.append("非 4K 源;可先占位,后续用 relink 换 4K 母版")
    if meta.get("subtitle_tracks"):
        out.append(f"存在 {meta['subtitle_tracks']} 条软字幕轨(入库/剪辑前可无损移除)")
    if kind not in _CLEAN_KINDS and not meta.get("subtitle_tracks"):
        out.append("无软字幕轨:抽帧确认是否有硬字幕后再定级")
    return out


def add(video: str, *, series: str, season: str, kind: str, tier: str,
        source_url: str | None = None, title: str | None = None,
        creator: str | None = None, notes: str | None = None,
        copy: bool = False, do_ingest: bool = False, force: bool = False,
        dry_run: bool = False) -> dict:
    src = Path(video).expanduser().resolve()
    if not src.exists():
        raise FileNotFoundError(str(src))
    _validate_fields(series, season, kind, tier)
    meta = _probe(src)
    filename = normalized_name(series, season, kind, tier, meta, src.suffix)
    dest = sources_root() / series / season / filename
    plan = {
        "source": str(src),
        "dest": str(dest),
        "filename": filename,
        "meta": {k: meta.get(k) for k in ("width", "height", "fps", "codec", "duration", "subtitle_tracks")},
        "action": "copy" if copy else "move",
        "warnings": _warnings(meta, kind),
    }
    if dry_run:
        plan["dry_run"] = True
        return plan
    if dest.exists() and not force:
        raise ValueError(f"目标已存在: {dest}。换命名字段,或用 --force 覆盖。")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if copy:
        shutil.copy2(src, dest)
    else:
        shutil.move(str(src), str(dest))
    result = {**plan, "placed": str(dest)}
    if do_ingest:
        from . import decision_loop
        asset = ingest_mod.ingest(str(dest))
        result["asset_id"] = asset["id"]
        result["source_record"] = decision_loop.upsert_source_record(asset["id"], {
            "source_url": source_url,
            "creator": creator,
            "title": title,
            "notes": notes,
            "status": "review",
        })
    return result


def _du(path: Path) -> int:
    if path.is_file() or path.is_symlink():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    for child in path.rglob("*"):
        if child.is_file() and not child.is_symlink():
            try:
                total += child.stat().st_size
            except OSError:
                pass
    return total


def _human(num: int) -> str:
    value = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}TB"


def clean(project_id: str | None = None, *, apply: bool = False) -> dict:
    """回收可再生磁盘占用。默认只报告(dry-run),--apply 才真正删除。

    永不触碰:sources_root、engine.sqlite、library/keyframes、母版成片、来源记录。
    """
    targets: list[tuple[str, Path]] = []
    tmp = Path(os.environ.get("TMPDIR", "/tmp"))
    for bundle in tmp.glob("remotion-webpack-bundle-*"):
        targets.append(("remotion_temp", bundle))
    # 渲染分段 + RIFE/超分/ProRes 缓存,全部可再生。
    for name in ("rendersegs", "rife", "realesrgan", "srseg", "restore", "slowmo"):
        d = config.CACHE / name
        if d.exists():
            targets.append(("render_cache", d))
    # 项目中间件(保留 master*.mp4 / legacy platform_*.mp4 / final editspec)。
    if project_id:
        pdir = config.PROJECTS / project_id
        for pattern in ("*.staged.json", "*.smooth.json"):
            targets += [("project_intermediate", p) for p in pdir.glob(pattern)]
        out = pdir / "outputs"
        if out.exists():
            for pattern in (".seg_*", "*.preview.mp4", "blueprint.*.preview.mp4"):
                targets += [("project_intermediate", p) for p in out.glob(pattern)]

    categories: dict[str, dict] = {}
    for category, path in targets:
        size = _du(path)
        entry = categories.setdefault(category, {"items": [], "bytes": 0})
        entry["items"].append({"path": str(path), "bytes": size, "size": _human(size)})
        entry["bytes"] += size
        if apply:
            try:
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path)
                else:
                    path.unlink()
            except OSError as exc:
                entry["items"][-1]["error"] = str(exc)

    total = sum(entry["bytes"] for entry in categories.values())
    return {
        "project_id": project_id,
        "applied": apply,
        "reclaimable_bytes": total,
        "reclaimable": _human(total),
        "categories": {k: {**v, "size": _human(v["bytes"])} for k, v in categories.items()},
        "preserved": ["sources_root", "engine.sqlite", "library/keyframes", "outputs/master*.mp4"],
    }
