# Target Architecture

> 依据 `docs/product/WANT.md` 定义的目标架构，结合本仓既有资产做的落地化设计。
> 与 MASTER PLAN 有出入之处均在文中显式标注理由。

---

## 0. 设计约束

> **本文档采用「完全改造」口径**（用户 2026-07-25 决策）：
> 不保留向后兼容，不为旧结构做适配层，不需要的代码直接删除。
> 现有代码只有两种命运：**按新架构重写并归位**，或**删除**。

1. **不保留兼容层**。EditSpec v1 不迁移、不适配、不双写。旧项目及
   `projects/_archive/` 已按 2026-07-26 的归零决定删除；新制作不读取任何旧项目制品。
2. **单一执行后端：DaVinci Resolve**。Remotion 整体删除。FFmpeg 只作为工具
   （proxy / 抽帧 / 探测 / 音频分析 / 技术 QA），不承担成片渲染。
3. **变现目标优先**（自用产片、账号变现）。`experiment.py` 的增长闭环
   （Hook A/B → 发布指标 → 逐镜留存 → 下一条片子）虽然 MASTER PLAN 未提，
   在本架构中重写为 **Layer 7 Growth Intelligence** 保留——这是本仓相对 MASTER PLAN 的独有资产。
4. **确定性优先**。现状 §73 做得好，目标架构继续把时间码/帧换算/媒体查找/缓存/校验
   全部留在确定性代码里，LLM 只做语义判断。
5. **源素材保留，制作状态归零**。永久源素材留在外部素材库；旧 v1/v2 数据库、
   proxies、keyframes、cache 与项目制品已于 2026-07-26 清空。新制作从空白 v2 schema
   重建派生数据；`anime/` 下的实现代码不再使用。

---

## 1. 目标分层

```
┌────────────────────────────────────────────────────────┐
│  Review UI  (FastAPI + React)                          │
│  Project / Reference / Candidates(A|B|C) / Cut /        │
│  Revision(自然语言) / Final                             │
└───────────────────────┬────────────────────────────────┘
                        │
┌───────────────────────▼────────────────────────────────┐
│  Layer 2  Creative Intelligence                        │
│  Intent → Brief(已有) → StyleFingerprint → DirectorPlan │
│  + Preference Profile                                  │
└───────────────────────┬────────────────────────────────┘
                        │
┌───────────────────────▼────────────────────────────────┐
│  Layer 1  Asset Intelligence                           │
│  Ingest / ShotDetect / Visual / Character / Motion /    │
│  Audio / Embedding / Index      (全局，跨项目复用)      │
└───────────────────────┬────────────────────────────────┘
                        │
┌───────────────────────▼────────────────────────────────┐
│  Layer 3  Editing Intelligence                         │
│  Retrieval → Ranking → Candidates(A/B/C) →              │
│  MusicMap → Sequence Planner → Timing                  │
└───────────────────────┬────────────────────────────────┘
                        │
┌───────────────────────▼────────────────────────────────┐
│  Layer 4  EditSpec v2   ★ Stable Editing IR ★          │
│  schema / validator / migrations / diff                │
└───────────────────────┬────────────────────────────────┘
                        │
┌───────────────────────▼────────────────────────────────┐
│  Layer 5  Execution Engine                             │
│  ResolveCompiler → ResolveAdapter → Resolve/Fusion     │
│  Resolve Preview/Master / FFmpeg(tool) / External AI   │
└───────────────────────┬────────────────────────────────┘
                        │
┌───────────────────────▼────────────────────────────────┐
│  Layer 6  Critic / QA                                  │
│  Technical QA(确定性) ‖ Creative Critic(AI)  —— 严格分离 │
└───────────────────────┬────────────────────────────────┘
                        │
┌───────────────────────▼────────────────────────────────┐
│  Layer 7  Growth Intelligence  ★ 本仓特有 ★            │
│  Hook A/B → 发布指标 → shot_outcomes → 下一条片子偏好   │
└────────────────────────────────────────────────────────┘
```

---

## 2. 目标目录结构

按 MASTER PLAN §46 重建，一处新增（标 ★）：

```text
anime-edit-studio/
├── AGENTS.md
├── v1-final tag / docs/architecture/ARCHITECTURE_GAP_ANALYSIS.md
├── docs/architecture/TARGET_ARCHITECTURE.md / docs/planning/MIGRATION_PLAN.md
├── pyproject.toml                  entrypoint: aes = studio.cli:app
├── config/
│   ├── app.yaml                    ← 由 config.toml 重写
│   ├── models.yaml
│   ├── resolve_capabilities.yaml
│   └── recipes.yaml
│
├── studio/                         新包名，与旧 anime/ 完全区隔
│   ├── core/                       契约与确定性内核
│   │   ├── contracts.py            Asset / Shot / DirectorPlan / Recipe / Revision / QAResult
│   │   ├── timecode.py             帧↔秒↔timecode 唯一实现
│   │   ├── ids.py  hashing.py  cache.py  versions.py
│   │   └── state.py                Workflow State Machine
│   │
│   ├── asset_intelligence/
│   │   ├── ingest/  shot_detection/  visual/  character/
│   │   ├── motion/  audio/  embeddings/  indexing/
│   ├── creative/
│   │   ├── intent/                 Brief / 导演合约
│   │   ├── reference/              StyleFingerprint
│   │   ├── director/               DirectorPlan
│   │   └── preference/             Preference Memory + Pairwise
│   ├── editing/
│   │   ├── music/                  MusicMap
│   │   ├── retrieval/  ranking/  candidates/  sequence/  timing/
│   ├── editspec/
│   │   ├── schema/  validator/  migrations/  diff/
│   ├── execution/
│   │   ├── resolve/                ★ 唯一成片执行后端
│   │   ├── fusion/  color/  audio/  recipes/
│   │   ├── ffmpeg/                 工具级（proxy/抽帧/探测/QA），非渲染器
│   │   ├── external_ai/            RIFE / Real-ESRGAN / rembg / whisper
│   │   └── gui_fallback/           默认禁用
│   ├── critic/
│   │   ├── technical/              确定性
│   │   └── creative/               AI
│   ├── growth/                     ★ Hook A/B + 发布指标 + 逐镜留存
│   ├── agents/                     LLM/VLM Provider 抽象 + tools 注册
│   ├── review/                     FastAPI 后端
│   └── cli.py
│
├── review-web/                     重写前端（Vite + React，六页面）
├── library/  kit/  refs/
├── projects/
│   ├── _archive/                   ← 旧 6 个项目的 editspec/beatmap，只读参考
│   └── <project_id>/
│       ├── project.yaml  director_plan.yaml  style_fingerprint.json
│       ├── music_map.json  candidates.json  editspec.json
│       └── revisions/  previews/  output/
└── tests/
```

### 删除清单（不保留、不迁移）

| 删除项 | 理由 |
|---|---|
| `renderer/`（整个 Remotion 工程 + node_modules + render-shots.mjs + src/） | Resolve 是唯一成片后端；Remotion 的 CSS/SVG/GLSL 特效在新架构中无位置 |
| `anime/`（42 个模块全部） | 按新分层重写；能力逻辑参考旧实现，但不做代码搬运 |
| `anime/editspec.py`（EditSpec v1） | 渲染器私有镜像，违反 §6，无迁移价值 |
| `anime/render.py` / `slowmo.py` 中的 Remotion 桥接 | 后端已删 |
| `anime/sideways.py` / `endcard.py` / `matte.py` | 低价值一次性能力，Resolve 有原生等价物 |
| `scripts/`（5 个脚本） | 绕过 CLI 直接改 EditSpec，与新 IR 不兼容 |
| `review-web/src/`（现有前端） | 候选审片形态已变（A/B/C + 自然语言），重写 |
| `config.toml` | 重写为 `config/app.yaml` |
| `projects/*/editspec.*.json`（含 .staged / .v2 / .restore.rife.sr） | 移入 `_archive/`，不参与新链路 |
| `docs/` 中描述旧闭环的 7 篇 | 重写；`reference-dna.md` / `preference-learning.md` 的**思路**保留到新文档 |
| `tests/`（3 个文件） | 测的是被删模块，随之删除；新架构重建测试 |

### 保留清单（数据与资产，非代码）

| 保留项 | 处理 |
|---|---|
| `library/engine.sqlite` | **ETL 迁入新 schema**：assets / shots / embedding / review_decisions / growth_* / shot_outcomes 全部保留 |
| `library/proxies/` `keyframes/` `cache/` | 直接复用，路径不变 |
| `~/Desktop/anime-material-library/sources/` | 源片不动 |
| `kit/`（luts / sfx / bgm / models / aliases.json） | 保留；LUT 与 SFX 会被重新包装进 Recipe 结构 |
| `refs/` | 保留 |
| `projects/*/beatmap.json` | 移入 `_archive/`，作为 MusicMap 实现的回归对照数据 |

**★ 新增 `growth/` 层**。理由见 §0.3。

---

## 3. EditSpec v2 —— 核心 IR 设计

### 3.1 设计原则

1. **秒制为权威，帧制为派生**。渲染后端 fps 不同（Resolve 时间线 fps ≠ Remotion fps ≠ 源片 fps），
   IR 必须用秒 + 显式 `timebase` 表达，帧数由 compiler 换算。
   → 修正现状 v1 全帧制、绑死 60fps 的问题。
2. **引用而非路径**。clip 引用 `asset_id` + `shot_id`，媒体路径由 Execution 层解析。
   → 修正现状把绝对路径写进 EditSpec 的问题（素材移动即失效）。
3. **Recipe 而非实现**。效果/调色/音效只写 `recipe_id + 参数`，永不写节点图或 CSS。
4. **决策元数据必须随行**。`confidence` / `reasoning` / `source`(ai|user|rule) 字段，
   支撑 Critic、Revision 与 Preference 学习。
5. **可 Diff**。稳定 `clip.id`，修订以 patch 表达。

### 3.2 顶层结构

```jsonc
{
  "spec_version": "2.0.0",
  "id": "kimetsu-rengoku-arc",
  "created_from": {
    "director_plan": "dp_003",
    "style_fingerprint": "sf_a_mp4_001",
    "music_map": "mm_001",
    "preference_profile": "pp_global_v4"
  },
  "timebase": { "fps": 24, "drop_frame": false },
  "canvas": { "width": 3072, "height": 3072, "aspect": "1:1" },
  "duration_sec": 25.0,

  "tracks": [
    { "id": "V1", "kind": "video" },
    { "id": "V2", "kind": "video", "role": "overlay" },
    { "id": "A1", "kind": "audio", "role": "music" },
    { "id": "A2", "kind": "audio", "role": "sfx" },
    { "id": "A3", "kind": "audio", "role": "source" }
  ],

  "clips":   [ /* 见 3.3 */ ],
  "audio":   [ /* 音乐/音效/原音层 */ ],
  "markers": [ { "sec": 8.12, "kind": "drop", "note": "music impact" } ],
  "captions":[ ],

  "meta": {
    "recipe_versions": { "impact_shake": "v3", "anime_fire_contrast": "v2" },
    "model_versions":  { "clip": "ViT-B-32", "ranker": "logistic_v4" },
    "pipeline_version": "2.0.0"
  }
}
```

### 3.3 Clip 结构

```jsonc
{
  "id": "clip_018",                    // 稳定 ID，diff 的锚点
  "asset_id": "82991e1ebe6d",
  "shot_id":  "82991e1ebe6d-405",

  "source":   { "in_sec": 142.31, "out_sec": 143.82 },
  "timeline": { "in_sec": 8.40, "duration_sec": 1.51, "track": "V1" },

  "role": "impact",                    // opening|character_intro|build|pre_drop|impact|release|ending

  "framing": {
    "mode": "smart_reframe",           // fit|crop|smart_reframe|magic_mask_track|manual
    "subject": "tanjiro",              // magic_mask_track 时用于选定主体
    "offset_x": 0.0, "offset_y": 0.0,  // manual 时的手动微调
    "scale": 1.0
  },
  "camera": {                          // 虚拟运镜（不同于源片摄影机运动）
    "move": "push_in", "from_scale": 1.0, "to_scale": 1.12, "curve": "ease_out"
  },
  "retime": {
    "type": "speed_ramp",              // constant|speed_ramp
    "entry_speed": 1.0, "impact_speed": 0.35, "exit_speed": 1.4,
    "impact_at_sec": 0.42,
    "interpolation": "optical_flow"    // nearest|frame_blend|optical_flow
  },
  "transition": {
    "in":  { "recipe": "hard_cut" },
    "out": { "recipe": "flash_impact", "duration_sec": 0.10, "strength": 0.8 }
  },
  "effects": [
    { "recipe": "impact_shake_v3", "params": { "strength": 0.72, "duration_frames": 8, "motion_blur": 0.32 } },
    { "recipe": "anime_glow_v2",   "params": { "strength": 0.35 } }
  ],
  "color":  { "recipe": "anime_fire_contrast_v2", "params": { "intensity": 0.8 } },
  "audio":  {
    "sfx": [ { "recipe": "sword_whoosh", "at_sec": 0.02, "gain_db": -3 },
             { "recipe": "impact_low",   "at_sec": 0.42, "gain_db": 0 } ],
    "source_gain_db": -12,
    "volume_automation": [ { "sec": 0.0, "db": -12 }, { "sec": 0.4, "db": -3 } ]
  },

  "decision": {
    "source": "ai",                    // ai | user | rule
    "confidence": 0.78,
    "reasoning": "drop 前 0.4s 需要眼部特写建立张力；该镜 pose_quality 0.91、无字幕",
    "alternatives": ["shot_01455", "shot_00987"],   // 供 Pairwise Preference 使用
    "locked": false                    // 用户锁定后 Revision 不得改动
  }
}
```

### 3.4 校验器（`editspec/validator`）

确定性检查，失败即拒绝进入 Execution：
- 时间轴无重叠/无空洞（同轨）；`duration_sec` 与 clips 覆盖一致
- `source.out_sec - in_sec` 与 `timeline.duration_sec × 平均速度` 自洽
- `asset_id` / `shot_id` 在 DB 中存在，且源文件可达
- 所有 `recipe` 在 recipe registry 中注册且参数在合法域内
- 所有 `recipe` 在目标渲染后端的 capability matrix 中可执行（否则给出 fallback 或报错）
- `timebase.fps` 与源素材 fps 的换算不产生亚帧漂移

### 3.5 Diff（`editspec/diff`）

```jsonc
{
  "from_version": 1, "to_version": 2,
  "ops": [
    { "op": "replace_clip", "clip_id": "clip_017", "new": { /* clip */ } },
    { "op": "patch_clip",   "clip_id": "clip_018", "path": "retime.impact_speed", "value": 0.28 },
    { "op": "shift_after",  "from_clip": "clip_018", "delta_sec": -0.12 }
  ]
}
```

Execution 层据此做 **选择性 Resolve 更新**（只重建受影响的 clip 与其邻接转场），
不重建整条时间线。这是 §35 / §54 的落地方式。

---

## 4. Execution Engine：Resolve 集成设计

### 4.1 模块划分（`studio/execution/resolve/`）

| 文件 | 职责 |
|---|---|
| `connection.py` | 环境变量注入（`RESOLVE_SCRIPT_API` / `RESOLVE_SCRIPT_LIB` / `PYTHONPATH`）、`GetResolve()`、健康检查、重连 |
| `capability.py` | 加载 `resolve_capabilities.yaml`，回答"这个能力用哪条路径执行" |
| `project.py` | 创建/打开工程、工程设置（分辨率/fps/色彩空间） |
| `media_pool.py` | 导入媒体、Bin 组织、MediaPoolItem 缓存与查找 |
| `timeline.py` | 创建时间线、轨道管理、`AppendToTimeline` 批量放置 |
| `clips.py` | 单 clip 的 in/out、位置、变换、retime |
| `markers.py` | 时间线标记（drop / impact / 用户反馈位置） |
| `fusion.py` | Fusion comp 加载与参数注入（Recipe 执行） |
| `color.py` | Color page 节点图/PowerGrade 应用（Recipe 执行） |
| `fairlight.py` | 音轨、SFX 放置、音量自动化 |
| `render.py` | 渲染预设、渲染队列、渲染状态轮询 |
| `recipes.py` | Recipe → Resolve/Fusion 具体调用的分发 |
| `gui_fallback.py` | 最后手段，**默认禁用**，需显式开关 |

**唯一入口**：`ResolveAdapter`。上层任何模块不得 `import DaVinciResolveScript`。
以 lint 规则强制（CI 检查 `execution/resolve/` 之外不得出现该 import）。

### 4.2 编译器

```
EditSpec ──> ResolveCompiler ──> 一系列 ResolveAdapter 调用（有序、幂等、可重试）
        └──> FFmpegRoughCompiler ──> 结构粗预览（只有硬切与时长，无特效/调色）
```

**只有两个编译器。** `ResolveCompiler` 产出一切正式内容（含 Preview Render 与 Master Render，
区别只在渲染预设：Preview 走代理 + 1080p + 快速预设，Master 走母版 + 全分辨率）。

`FFmpegRoughCompiler` 只服务一个场景：Sequence Planner 迭代时快速看结构是否成立（几秒出片），
不进入用户审核流程。Resolve 不可用时它**不是 fallback**——Resolve 不可用就是失败，明确报错。

> §33 的 "Preview First" 在新架构中由 **Resolve 渲染预设**实现，而不是由第二个渲染器实现。
> 这样预览与成片的视觉是同一条链路，不存在"预览好看成片不一样"的问题。

### 4.3 `resolve_capabilities.yaml`

**已落地为真实文件**：`config/resolve_capabilities.yaml`，内容由
`probe_resolve_capability.py` 于 2026-07-25 实测生成，非推断。

**规则**：任何 `verified: false` 的能力，EditSpec 生成器禁止产出对应指令。
`probed: true`（方法存在）≠ `verified: true`（实测生效）——两者必须分开记录。
每个能力从 `false` → `true` 必须由一次真实调用 + 一条测试证明。
沿用旧 `extreme.capabilities()` 的"显式声明 ready/unavailable/fallback、绝不假称已执行"原则。

### 4.4 实测确认的关键能力（Phase 1 后修正）

| 能力 | API | 对架构的影响 |
|---|---|---|
| **主体感知竖屏重构图** | `TimelineItem.SmartReframe` | ❌ 实测返回 `True` 但属性与渲染输出均不变。Phase 3 改为自研主体检测，输出 Pan/Zoom 参数，经已验证的 `transform` 通道写入 |
| **主体跟踪遮罩** | `CreateMagicMask` / `RegenerateMagicMask` | ❌ 所有实测签名均返回 `False`。Phase 3 改为自研逐帧主体检测，不依赖 Resolve Magic Mask |
| **三段变速** | `SetSpeedRamp` | ❌ Resolve 21.0.3 中属性值为 `None`，不可调用。速度曲线改走 Fusion `TimeSpeed` Recipe |
| **插帧方式（nearest/frame_blend/optical_flow）** | `project.SetSetting('imageRetimeInterpolation', str)` | ✅ 2026-07-28 渲染对照实测确认。旧结论「`RetimeProcess`/`MotionEstimation` 整数属性可编程」已证伪 —— 那条 per-clip 路径只能触达 nearest/frame_blend 两档，摸不到真光流；真正生效的是这条工程级字符串设置（`nearest`\|`frameBlend`\|`opticalFlow`，无 `speedWarp`）。工程级 = 同一工程内所有非 nearest 的 clip 只能统一成一种插值，见 `ResolveCompiler._apply_retime_interpolation` |
| **Fusion Comp 创建** | `AddFusionComp` | ✅ 已验证能返回 comp 对象。`ImportFusionComp` / `ExportFusionComp` / 参数注入的完整 Recipe 闭环仍须 Phase 2.0 实测 |
| **调色分组候选路径** | `AssignToColorGroup` / `AddVersion` / `CopyGrades` / `ExportLUT` | ⚠️ 方法已发现但尚未验证。Color Recipe 是否采用 ColorGroup，等待 Phase 2.0 渲染对照后冻结 |
| **26 个可写变换属性** | `SetProperty` | Zoom/Pan/Tilt/Crop/Opacity/CompositeMode/ResizeFilter 全可编程 |
| **标记携带自定义数据** | `AddMarker(..., customData)` | **把 EditSpec `clip_id` 写进时间线标记**，实现 IR ↔ Resolve 双向定位，是选择性更新（§54）的实现基础 |
| 附带发现 | `Stabilize` / `DetectSceneCuts` / `CreateSubtitlesFromAudio` | ⚠️ 仅发现方法，未验证；在输出效果通过对照测试前不得进入生成链 |

### 4.5 实测踩到的坑（已写入 capabilities.yaml `pitfalls`）

| # | 坑 |
|---|---|
| P1 | 进程名是 `Resolve` 不是 `DaVinci Resolve`，`pgrep -x` 会误判 |
| P2 | Resolve 未运行时 `scriptapp()` **静默返回 None 不抛异常** |
| P3 | 系统 Python 3.14 与 fusionscript 不兼容，必须 venv 3.11 |
| P4 | **素材真实帧率 23.976**，旧 EditSpec 硬编 60fps 是错的 —— 直接印证 §3.1 A1「秒制为权威」的决策 |
| P5 | Resolve 必须前台运行，无真正 headless |

### 4.4 Recipe 库

```
config/recipes.yaml            索引与版本
execution/recipes/
  effects/
    impact_shake_v3/{recipe.yaml, comp.setting, preview.mp4, ACCEPTANCE.md}
    anime_glow_v2/...
    white_flash_v1/  rgb_split_impact_v1/  speed_flash_v1/  camera_punch_v1/
  color/
    anime_clean_v1/{recipe.yaml, grade.drx, reference.png}
    anime_fire_contrast_v2/  anime_cold_v3/  anime_night_blue_v1/
  sound/
    sword_whoosh/{recipe.yaml, asset.wav}   impact_low/  sub_impact/  riser/
```

每个 recipe 目录必须含：
- `recipe.yaml`：id / engine / 参数 schema（type/min/max/default）/ version / 依赖能力
- 实现产物：Fusion `.setting` / Color `.drx` / 音频 `.wav`
- **`preview.mp4` 与 `ACCEPTANCE.md`**：视觉验收基准。
  这是应对 G3（Remotion 特效无 Fusion 对应物）的强制手段——
  每个 recipe 必须有人工确认过的视觉样张，否则不得标记可用。

AI 的输出只允许是 `{"recipe": "<id>", "params": {...}}`，永不生成节点图。

---

## 5. Creative Intelligence 设计

### 5.1 DirectorPlan（新制品）

`projects/<id>/director_plan.yaml`，由 Brief + StyleFingerprint + MusicMap + Preference 生成：

```yaml
version: 1
duration_sec: 25
primary_characters: [tanjiro]
tone: [aggressive, cinematic, clean]

structure:                      # 与 MusicMap.sections 对齐，不得自由发挥
  - { role: opening,  start: 0,    end: 3.2,  energy: low,       asl: 1.5 }
  - { role: buildup,  start: 3.2,  end: 8.1,  energy: medium,    asl: 0.9 }
  - { role: drop,     start: 8.1,  end: 16.4, energy: very_high, asl: 0.45 }
  - { role: release,  start: 16.4, end: 21.0, energy: high,      asl: 0.7 }
  - { role: ending,   start: 21.0, end: 25.0, energy: medium,    asl: 1.8 }

visual_rules:
  prefer: [sword, eye_closeup, fire, dynamic_pose]
  avoid:  [static_dialogue, subtitles, bad_composition]

sound_strategy: "低声原音 → 音乐与冲击爆发"
impact_budget: { sfx_max: 9, flash_max: 3 }   # 防止"每个动作都机械加音效"(§32)
```

### 5.2 StyleFingerprint（`reference.py` 扩展）

在现有节奏语法（6 项）基础上补齐 §19 的其余 16 项。分三批实现：

- **批 1（可由现有 ffmpeg/PySceneDetect/librosa 直接算）**：`hard_cut_ratio`、`beat_sync_ratio`、
  `music_structure`、`energy_curve`、`brightness_curve`、`color_progression`、
  `impact_points`、`silence_usage`、`sound_effect_density`、`slow_motion_locations`
- **批 2（需 Shot 维度补齐后才能算）**：`shot_scale_sequence`、`motion_direction_sequence`、`camera_motion`
- **批 3（需 VLM/更复杂分析）**：`transition_types`、`speed_ramp_locations`、`visual_rhyme`、`motion_rhyme`

`StyleFingerprint` 是测量结果，不直接作为 Planner 的长期配置。它必须先编译成
带版本的 `EditingStyleProfile`，保存参考片归一化切点、镜头时长分布、鼓点贴合目标、
景别/运动变化和效果密度。这样新增参考片只会新增风格档案，不会把某条样片的常量
写进 Sequence Planner；同一风格也能跨音乐时长复用。

### 5.3 MusicMap（`beat.py` 扩展）

现有 `beatmap.json` 保留为子集，新增：

```jsonc
{
  "bpm": 158, "beats": [...], "downbeats": [...], "onsets": [...], "beat_energy": [...],
  "sections": [ {"type":"intro","start":0,"end":3.2}, {"type":"build",...}, {"type":"drop",...} ],
  "impact_points": [8.12, 9.02, 10.44],
  "risers":  [ {"start": 6.4, "end": 8.1} ],
  "breaks":  [ ], "silences": [ {"start": 8.05, "end": 8.12} ],
  "spectral_change_points": [...]
}
```

`sections` 用能量包络 + 自相似矩阵（librosa `segment`）分段，
`silences` 用于 §19 的"drop 前 3 帧静音"这类剪辑语法。

---

## 6. Editing Intelligence 设计

### 6.1 两阶段检索

```
Retrieval (确定性，SQL + FTS + 向量)
  过滤：character / action / motion>x / subtitle=false / face_quality>y
  召回：100–300 shots，<1s
        ↓
Ranking (多信号)
  Intrinsic Quality  ── 镜头自身（现有 13 维中的 technical/composition/aesthetic）
  Contextual Suitability ── 新建：
      sequence_fit (与 role 的匹配)
      music_fit    (与该时间点 energy/section 的匹配)
      reference_fit(与 StyleFingerprint 的 shot_scale/motion 序列匹配)
      continuity   (与 prev/next 的 motion_direction / character / scale 连续性)
      novelty      (与已选镜头的 CLIP 距离)
      preference   (个人偏好 signal，不作硬规则)
        ↓
  30–50 候选，按 role 分组为 A/B/C
```

**关键设计**：Intrinsic 与 Contextual 必须是两个独立分数，
在 `shot_scores`（intrinsic，可缓存跨项目）与 `candidate_scores`（contextual，项目内）两张表分离存储。
这直接解决 §12 的核心诉求，也让 intrinsic 分数可全局复用不必每项目重算。

### 6.2 Sequence Planner

输入：DirectorPlan（含 EditingStyleProfile）+ MusicMap + Candidates + Preference
输出：EditSpec v2 draft

约束求解而非贪心（§23）：
- 硬约束：段落时长、每段 ASL、必含/禁含、无重复镜头、无字幕
- 软约束（加权）：beat 对齐、motion direction 不冲突、character continuity、
  shot scale 变化节奏、energy 曲线贴合、visual phrase 完整性
- LLM 只在**候选并列时**介入做语义判断（"这两个镜头视觉上是否连续"），
  不负责生成整条时间线
- 切点先按风格的归一化节奏/时长 pattern 生成，再按目标比例吸附 beat/impact；
  禁止机械地逐拍切，也禁止为单个参考片硬编码时间点

### 6.3 Motion Choreography（2026-07-26 实测冻结）

单镜头 `CameraMove` 不足以表达参考漫剪的运动语法。EditSpec 顶层
`motion_phrases` 以 2–4 个相邻 clip 为一组，显式描述 direction、
`hold/accelerate/whip/carry/settle/reverse` 阶段、强度、位移、缩放、旋转与
切点 Blur 窗口。

Planner 联合设计相邻镜头，并从选片阶段为 Hold 槽位偏好低 `motion_mag` 素材。
执行层将 TimeStretcher、Transform、DirectionalBlur 合成在同一个 Fusion comp，
解决 Resolve 多 comp 仅为版本、不可保证串联的问题。Resolve 21.0.3.7 实渲证据
见 `docs/probes/motion_phrase_acceptance.json`：144 帧完整，跨 Cut 连续性
0.00→0.90。

确定性 Motion QA 独立检查 median/P75、动态范围、Hold 比例、方向平衡、方向
反转率和短语内部跨 Cut 连续性。光流采样必须避开 Whip/Blur 峰值窗口（P21）。

### 6.4 Editorial Grammar（产品验收补强）

Sequence Planner 不能只回答「何时切」和「镜头运动多强」，还必须为每个相邻镜头
生成结构化 `CutRelation`：`continuation / match_action / graphic_match /
contrast / reaction / parallel / reveal / ellipsis`。该字段描述剪辑语义，不等同于
Resolve transition；例如 match-action 默认仍可执行为 hard cut。

每个 clip 同时携带 `SourceSelection`，记录实际源区间围绕
`anticipation / action / impact / reaction / settle` 哪个动作相位取点、锚点秒数、
证据和置信度。没有逐帧动作 landmark 时只能使用有界的 shot-level 语义估计，
不得伪称为精确命中帧；后续分析升级可替换估计而不改变 Compiler。

`EditGrammarQA` 与 Rhythm/Motion/Technical QA 分离，确定性检查有动机切镜覆盖率、
源相位覆盖率、关系多样性和单一关系的连续重复。它是首剪创意诊断证据，不代替
所有者观看确认。

---

## 7. Critic / QA 设计（§76 严格分离）

### Technical QA（确定性，`critic/technical/`）
在现有 `qa.py` 7 项基础上补齐至 §75 的 13 项：
存在 / 时长 / 分辨率 / fps / codec / 音轨 / 响度 / 黑帧 / **冻结帧 / 丢帧 / 损坏 / 意外静音 / 画幅比**。
输出 `QAResult{passed: bool, checks: [...]}`。**失败绝不标记 DELIVERED。**

### Creative Critic（AI，`critic/creative/`）
输入：Preview 视频 + DirectorPlan + StyleFingerprint + EditSpec
输出结构化 issue 列表：

Creative Critic 前增加确定性的 `RhythmQA`：比较实际 cut density、median shot length
与 beat-sync ratio 是否达到 `EditingStyleProfile` 的目标。它不替代 Technical QA，
而是阻止“技术上成功、节奏上失真”的首剪被误判为风格达标。

```jsonc
[{ "kind": "weak_impact", "timeline_sec": [8.1, 9.0], "severity": "high",
   "reason": "drop 首镜为中景静态，与 reference 的 ECU→action 语法不符",
   "suggested_fix": { "op": "replace_clip", "clip_id": "clip_018",
                      "requirements": { "shot_scale": "extreme_close_up", "visual_energy": "> 0.8" } } }]
```

**两者永不合并为一个 score。** `release_ready` 由 Technical QA 单独把关，
Creative Critic 只产出建议与 Revision 提案。

### 用户自然语言反馈 → Revision
"7 秒那里不够帅" → LLM 解析 → 定位 clip → 生成与 Creative Critic 同构的 `suggested_fix`
→ 转 EditSpec Diff → 选择性 Resolve 更新。
**同一套 Revision 数据结构服务 AI Critic 与用户反馈两个来源。**

---

## 8. 数据模型（§57）

新库从零建 schema（迁移框架思路沿用旧 `db.MIGRATIONS` 的版本化幂等模式，代码重写）。
旧库 14 张表中，`assets` / `shots` / `shots_fts` / `review_decisions` / `creative_briefs` /
`preference_models` / `project_assets` / `growth_experiments` / `growth_variants` /
`shot_outcomes` / `source_records` 通过 ETL 迁入；
`shot_scores` / `cut_variants` / `enhancement_reviews` 因语义已变，不迁移（重算）。

新增表：

| 新表 | 用途 |
|---|---|
| `characters` | 角色注册表 + 参考图 + 别名（现状 `kit/aliases.json` 迁入） |
| `music_tracks` | 音乐资产 + MusicMap 缓存 |
| `references` | 参考片 + StyleFingerprint 缓存 |
| `director_plans` | project + version + yaml + 生成参数 |
| `edit_specs` | project + version + spec_json + parent_version + created_by |
| `edit_spec_diffs` | from_version / to_version / ops_json / source(ai\|user\|critic) |
| `candidate_groups` | project + role + [A,B,C] shot_ids + 用户选择 |
| `preference_pairs` | **winner / loser / context / project_style** ← §38 核心 |
| `recipes` | id / version / engine / params_schema / verified / preview_path |
| `renders` | spec_version / backend / preset / output_path / duration / status |
| `qa_results` | render_id / kind(technical\|creative) / passed / checks_json |
| `workflow_states` | project + state + entered_at + payload（§56 状态机持久化） |
| `production_readiness_reports` | project + version + source_scope + coverage + blockers + ready |
| `character_appearances` | work + character + season/arc/episode + provenance + confidence + version |

`shots` 表补齐字段（§11 缺失项）：
`shot_scale, subject_motion, pose_quality, face_visibility, eye_visibility,
visual_energy, compression_score, subtitle_region, color_palette,
audio_energy, music_presence, cutability`，以及各自的 `*_confidence` 与 `analysis_version`。

---

## 9. Workflow State Machine（§56）

```
CREATED → SCOPED → READINESS_CHECK → ASSETS_READY → DIRECTING → CANDIDATES_READY → USER_SELECTION
→ EDIT_PLANNING → RESOLVE_BUILD → PREVIEW_RENDER → AI_REVIEW → USER_REVIEW
→ REVISION ⟲ → LOCKED → MASTER_RENDER → FINAL_QA → DELIVERED
                                                        ↓
                                                   PUBLISHED → METRICS_COLLECTED  ★Layer 7
```

持久化在 `workflow_states`。每个 step 必须：可重试（幂等）、可恢复（从上一个成功状态续跑）、
可记录（结构化日志 + 耗时）。任何 step 失败进入 `FAILED_<state>`，保留 payload 供诊断。

`READINESS_CHECK → ASSETS_READY` 是硬门禁。报告必须由确定性代码计算：
资产/代理/分镜存在性、分析覆盖率、候选计数和时长匹配不可交给 LLM。
LLM 只可辅助生成 Source Scope 与 Appearance Catalog 候选，且输出 schema 强约束；
外部资料必须保存 provenance。`create_first_cut()` 必须验证最新报告的 scope hash、
asset hashes、analysis versions 与 planner version，任何变化都使旧报告失效。

---

## 10. Agent 抽象（§47–§48）

**Tools 层**（确定性 Python 函数，任何 LLM 都能调）：

```
analyze_asset  search_shots  rank_shots  analyze_reference  analyze_music
create_director_plan  create_edit_spec  validate_edit_spec  diff_edit_spec
create_resolve_project  sync_timeline  apply_effect_recipe  apply_color_recipe
render_preview  run_technical_qa  run_creative_critic  apply_revision
```

**Agent 层**（`studio/agents/`）：Provider 抽象（Claude / GPT / Codex / 本地模型），
统一结构化输出（JSON schema 强约束），可通过 `config/models.yaml` 切换。
系统资产是 Tools + 数据，不是某个 LLM。

**LLM 的职责边界**（§73/§74）：只做语义与审美判断，
不做时间码换算、不做媒体查找、不做渲染参数决定、不做技术校验。

---

## 11. 关键设计决策记录

| # | 决策 | 理由 |
|---|---|---|
| A1 | EditSpec v2 用秒制 + timebase | 支持多后端不同 fps；v1 帧制绑死 60fps |
| A2 | Clip 引用 asset_id/shot_id 而非路径 | 素材移动不失效；路径解析交给 Execution |
| A3 | 新包名 `studio/`，旧 `anime/` 整体删除 | 完全改造口径；新旧包名区隔可避免半迁移状态下的导入混乱 |
| A4 | **Remotion 整体删除**，不保留为 Preview Renderer | 单一执行后端；预览与成片走同一条 Resolve 链路，避免视觉不一致；维护两套特效实现是纯负担 |
| A5 | Intrinsic / Contextual 分两张表 | intrinsic 可跨项目缓存，contextual 项目内计算 |
| A6 | 每个 Recipe 必须有 preview.mp4 + ACCEPTANCE.md | 应对 Fusion 无法自动复现 Remotion 视觉的风险 |
| A7 | capability 未验证则禁止生成对应指令 | 沿用 `extreme.capabilities()` "绝不假称已执行"原则 |
| A8 | 新增 Layer 7 Growth | 直接服务变现目标，是本仓相对 MASTER PLAN 的增量资产 |
| A9 | Creative Critic 与用户反馈共用 Revision 结构 | 一套 diff 应用逻辑服务两个来源 |
| A10 | Technical QA 单独把关 DELIVERED | §76 分离；创意判断不得阻塞或放行技术交付 |
| A11 | 竖屏重构图走自研主体检测 → `transform` | `SmartReframe` 实测空转；Pan/Zoom 写入已验证且可控、可缓存、可复现 |
| A12 | 主体跟踪走自研逐帧主体检测 | `CreateMagicMask` 实测不可用；不得把未生效的 Resolve 黑箱能力放入主链 |
| A13 | Fusion Recipe 候选路径为 GUI 制作后 Export，代码 Import+注参 | `AddFusionComp` 已验证；Export/Import/注参闭环须通过 Phase 2.0 后才能定案 |
| A14 | Color Recipe 优先验证 ColorGroup 架构 | `AssignToColorGroup` + `AddVersion` 理论上适合整体替换，但尚未 verified，Phase 2.0 结论优先 |
| A15 | 用 marker 的 customData 存 EditSpec `clip_id` | IR ↔ Resolve 双向定位，是选择性更新的实现基础 |
| A16 | Resolve 原生光流（工程级 `imageRetimeInterpolation='opticalFlow'`）作为首选，RIFE 保留为按需工具 | 2026-07-28 渲染对照已验证生效且档位语义明确（见 `project_setting_retime_interpolation`）；RIFE 额外做了逐镜切点保护，Resolve 原生路径靠「每个 clip 是独立 TimelineItem、conform 只在各自 source range 内进行」天然规避跨切点糊帧，但尚未有对照渲染验证这一点在生产素材上成立 |
| A17 | 首剪前引入 Production Readiness Gate | 防止已入库但 `shots=0`、分析缺失或候选不可行时仍进入选片；适用于所有作品 |
