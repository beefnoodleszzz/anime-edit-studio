# Anime Edit Studio

Demo 驱动的 AMV 生成引擎。只做一件事：给定一个 Demo 参考视频、一批已经在
[anime-shot-library](../anime-shot-library) 里挑选好的镜头 ID，以及可选的目标
音乐，产出一条节奏、运镜与 Demo 语言对齐的 AMV。

镜头的切分、打标签、质量/角色筛选全部发生在 anime-shot-library 里；本项目不
再扫描原始素材、不做候选镜头发现，只负责编排（时长匹配、节奏卡点、跨镜头运动
连续性、转场）与渲染（DaVinci Resolve + Fusion）、渲染后 QA。

## 工作流

1. 在 anime-shot-library 里挑好这次要用的镜头，拿到一批 shot_id。
2. 把 shot_id 写成一个文件（一行一个，或 JSON 数组）。
3. 跑：

```bash
uv run aes amv create \
  --project my-amv \
  --demo /path/to/demo.mp4 \
  --shots shot_ids.txt \
  --music /path/to/music.wav \
  --launch
```

产出在 `projects/my-amv/`：

```text
project.json             # 本次用的 demo/shot_ids/music/focus
reference_blueprint.json # Demo 的节奏/运镜/风格测量结果
music_timeline.json      # 音乐的节拍/分段/重音
amv_spec.json            # 最终编排结果（clip 序列、运动曲线、转场）
preview.mov              # Resolve 渲染出的预览
qa.json                  # 渲染后硬门禁 QA 报告
```

QA 通过后，显式发布：

```bash
uv run aes amv release --project my-amv
```

## 环境自检

```bash
uv run aes doctor env            # Python / Resolve / ffmpeg 是否就绪
uv run aes doctor capabilities   # Resolve 能力矩阵
uv run aes doctor assets         # 当前可解析的本地 asset_id
```

## 素材来源配置

`--shots` 里的 ID 会去 anime-shot-library 的 `catalog.sqlite` 里查询。默认按
兄弟目录 `~/Desktop/anime-shot-library/data/catalog.sqlite` 查找，也可以用环境
变量覆盖：

```bash
export ANIME_SHOT_LIBRARY_DB=/path/to/catalog.sqlite
```

## 架构

```text
shot_ids ──▶ studio.planning.shot_library_import   （解析/导入镜头）
              │
              ▼
studio.analysis          Demo/音乐测量（reference_analyzer / music_analyzer）
studio.planning           节奏映射 → 候选打分 → Beam Search → AMVSpec
                          (rhythm_style_mapper / candidates / global_sequence_planner
                           / motion_planner / amv_spec_builder)
studio.execution          AMVSpec → Resolve 时间线 → Fusion → 渲染
studio.qa / studio.critic  渲染后硬门禁与创作向 QA
```

## 测试

```bash
uv run pytest -q -m "not requires_resolve"
uv run pytest -q -m requires_resolve   # 需要本机运行 DaVinci Resolve
```
