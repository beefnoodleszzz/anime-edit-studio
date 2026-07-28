# Migration Plan

> **口径：完全改造**（用户 2026-07-25 决策）。
> 不保留向后兼容，不做适配层，不需要的代码直接删除。
>
> 从"EditSpec v1 + Remotion CLI 剪辑器"**重建**为
> "AI 驱动 / EditSpec 为 IR / DaVinci Resolve 为唯一专业执行引擎"的系统。

## ✅ 已锁定的决策（2026-07-25）

| 决策 | 结论 |
|---|---|
| 兼容性 | **不保留**。旧代码删除，数据 ETL 迁移 |
| 产量空窗 | **接受**。不为保产量牺牲架构 |
| Phase 顺序 | **顺序 A —— 架构优先**：0 → 1 → 2 → 3/4 → 5 → 6 → 7 → 8 → 9 |
| 质量标准 | **极致**。出片质量与效率必须相对旧系统有数量级提升，见 §18 KPI |

## ✅ Phase 0.6 硬门槛已通过（2026-07-25 实测）

Resolve 脚本环境**已验证可用**，整个改造方向的最大不确定性已消除：

```
GetResolve        -> OK       Resolve 21.0.3.7 Studio
venv python       -> 3.11.15
ImportMedia       -> True
CreateEmptyTimeline -> True
AppendToTimeline(带 in/out) -> True，一次放置 2 个片段成功
SetProperty(ZoomX) -> True
AddFusionComp     -> True
AddMarker         -> True
```

探测报告：`resolve_capability_probe.json`
能力矩阵：`config/resolve_capabilities.yaml`（已按实测填写）

### 探测带来的三个计划级变化

**变化 1 —— R1 风险大幅下降。**
原假设"Resolve 21 可能不支持脚本控制关键帧变速"，实测 `TimelineItem.SetSpeedRamp` **方法存在**，
且 `RetimeProcess` / `MotionEstimation` 属性可写。
风险从 🔴 降为 🟡，仍需 Phase 6 实测生效性。

> 2026-07-28 更新：Phase 6 实测已完成，结论与本节原假设**相反**——
> per-clip `RetimeProcess`/`MotionEstimation` 属性虽然可写，但**不驱动真正的插值渲染**
> （只能触达 nearest/frame_blend 两档，见 `retime_interpolation_mapping`）。
> 真正生效的光流控制入口是工程级 `project.SetSetting('imageRetimeInterpolation', 'opticalFlow')`，
> 见 `project_setting_retime_interpolation` 与 `ResolveCompiler._apply_retime_interpolation`。

**变化 2 —— Phase 6A Recipe 库成本下降。**
`ExportFusionComp` 存在，意味着 recipe 可以在 Fusion GUI 里**手工做好后导出 `.comp`**，
代码只负责 Import + 参数注入，不需要用脚本从零搭节点图。
这是 6A 从"逐个用脚本重建"降级为"逐个手工调好后导出"的关键差别。

**变化 3 —— 曾以为发现了两个 Studio 原生大杀器，Phase 1 实测证明都不可用。**

> ⚠️ 下面这段保留原始判断作为教训。Phase 0 只做了「方法是否存在」的探测，
> 由此得出的乐观结论在 Phase 1.12–1.14 的实测中**全部被推翻**。
> 详见下方「Phase 1 实测修正」。

| API | 当时的期待 | 实测结果 |
|---|---|---|
| `SmartReframe` | 横屏→4:5 主体感知自动重构图 | ❌ 返回 True 但**渲染输出逐字节相同**（空转） |
| `CreateMagicMask` | 原生主体跟踪遮罩 | ❌ 所有签名返回 False |
| `SetSpeedRamp` | 真三段变速 | ❌ 属性值为 `None`，方法根本不存在 |

另外还发现 `Stabilize`、`DetectSceneCuts`、`CreateSubtitlesFromAudio`、
`ExportLUT`、`AssignToColorGroup` + `AddVersion`（ColorGroup 架构优于逐 clip 调色）——
这些尚未实测，按同样标准在使用前必须逐一验证。

### ✅ Phase 1 实测修正（2026-07-25）

Phase 1.12–1.14 对三项关键能力做了实跑 + 渲染出帧验证，结论如下。

| 能力 | 判定 | 依据 | 应对 |
|---|---|---|---|
| `SmartReframe` | ❌ 空转 | 返回 True，属性不变，**渲染首帧 md5 与基线相同**；同片段手动 `Pan=400` 则 md5 改变、YAVG 80.6→50.8（对照组证明检测有效） | Phase 3 自研主体检测产出 Pan/ZoomX，经已验证的 `transform` 通道写入 |
| `CreateMagicMask` | ❌ 不可用 | `'F'/'B'/'BI'`/无参/整数全返回 False；切 Color 页后仍失败，节点数不变 | 同上，用自研主体检测 |
| `SetSpeedRamp` | ❌ 方法不存在 | `item.SetSpeedRamp` 的值是 `None`，`callable()` 为 False；`Speed`/`SpeedPercent` 属性均不可写 | 变速走 **Fusion Recipe**（`TimeSpeed` 节点），`add_fusion_comp` 已 verified |

**同时新验证通过 3 项**：

| 能力 | 结论 |
|---|---|
| `render` | ✅ 完整渲染通路打通：`LoadRenderPreset` → `SetRenderSettings` → `AddRenderJob` → `StartRendering` → 轮询。实测 1 秒素材约 0.7–1.2s |
| `retime_interpolation` | ⚠️ `RetimeProcess` / `MotionEstimation` 接受**整数** 0–3（传字符串一律被拒），但 2026-07-28 渲染对照证实这条 per-clip 路径不驱动真实插值 —— 真正生效的是工程级 `project.SetSetting('imageRetimeInterpolation', str)`，见 `project_setting_retime_interpolation` |
| `create_bin` | ✅ |

### 由此产生的两条方法论铁律（已写入 pitfalls P12–P14）

**P12 —— `hasattr` 对 Resolve 远程对象是假阳性。**
远程对象对未知属性返回 `None` 而非抛 `AttributeError`，
所以 `hasattr(item, 'SetSpeedRamp')` 恒为 True。
Phase 0 正是因此把一个不存在的方法记成了「方法存在」。
判定必须用 `callable(getattr(obj, name, None))`。

**P13 —— 「返回 True 但不生效」是最危险的失败模式。**
凡是影响画面的能力，`verified` 必须由**渲染出帧比对**支撑，不能只看返回值。
且必须先跑对照组确认检测手段本身有效。
（P14：对照帧不能取黑场 —— 第一版验证就栽在这里，误取 YAVG≈30 的近黑帧，
连对照组都判为「无差异」。）

能力矩阵因此从三态改为四态：
`verified` / `unavailable`（实测判定不可用）/ `probed_unverified` / `unprobed`。
两条测试守着这个不变量：不可用的能力不得被标 verified，且必须声明 fallback 与 evidence。

### 对「史诗级提升」的影响

竖屏重构图、主体跟踪、变速这三项**仍然可以做到**，但都要自己实现，
不能白拿 Resolve 的原生能力：

- 竖屏重构图 / 主体跟踪 → Phase 3 的自研主体检测 + `transform`（已验证可写）
- 变速 → Phase 6A 的 Fusion Recipe（`add_fusion_comp` 已验证）

代价是 Phase 3 与 Phase 6A 的工作量上升；
好处是这三项都落在我们自己的 Recipe / 分析体系里，可控、可缓存、可复现，
而不是黑箱调用一个连返回值都不可信的 API。

**变化 4 —— 一个必须立刻承认的旧系统设计错误。**
实测素材真实帧率是 **23.976**，而旧 EditSpec 硬编 60fps。
非整数帧率下的帧制 IR 必然产生亚帧漂移累积——这解释了旧系统为什么需要 `fps.py`、
`interpolate.py` 这一堆补丁。
`TARGET_ARCHITECTURE §3.1 A1`（秒制为权威 + 显式 timebase）由此得到实测支持，**不可动摇**。

---

## 0. 与「渐进迁移」的区别

MASTER PLAN §81 要求 "迁移必须 Incremental，禁止一次性推倒整个项目"。
在完全改造口径下，这条规则的落地方式调整为：

| 原则 | 完全改造下的解释 |
|---|---|
| 禁止一次性推倒 | 指**禁止一次性提交一个全新的大爆炸重写**，不是禁止删除旧代码 |
| 每一步必须保持系统可运行 | 指**新系统 `studio/` 的每一个 Phase 结束时可运行**，不再要求旧 Remotion 产线不中断 |
| 增量 | 按 Phase 增量建设新包；旧 `anime/` 在其能力被新包覆盖后**成批删除** |

**实际策略：新包并行生长 + 分批删旧。**
`studio/` 从 Phase 1 开始建，`anime/` 的模块在对应能力被 `studio/` 覆盖后立即删除，
而不是留到最后统一删。每个 Phase 的 DoD 里都有一条"本 Phase 应删除的旧模块"。

**产量断档是可接受的代价**（用户已确认不需兼容）。
预计从 Phase 1 开始到 Phase 6 结束的这段时间，新系统的出片质量低于旧 Remotion 线。
如果这个断档不可接受，需要回头改成双轨策略——这是唯一需要重新确认的点。

---

## 1. Phase 0 — 清场与地基（约 1 天）

> 历史说明：0.4/0.5 的迁移备份曾用于 v2 重建。用户于 2026-07-26 明确要求
> “像没有做过成品一样”重新开始，旧数据库备份与 `projects/_archive/` 随后删除。
> 当前唯一保留的是永久源素材；v2 数据库为空 schema。

| # | 任务 | 验收 |
|---|---|---|
| 0.1 | 提交或 stash 工作区 14 个已修改文件 | `git status` 干净 |
| 0.2 | 打 tag `v1-final`，作为旧系统的永久快照（随时可 checkout 出片） | tag 存在且可运行 |
| 0.3 | 建 `v2` 分支，后续全部工作在此分支 | — |
| 0.4 | **数据备份**：`library/engine.sqlite` 完整备份到 `library/engine.v1.sqlite.bak` | 文件存在，可打开 |
| 0.5 | 归档：`projects/*/editspec*.json`、`beatmap.json` 移入 `projects/_archive/` | 移动完成 |
| 0.6 | ~~验证 Resolve 脚本环境~~ | ✅ **已完成**，见上方 |
| 0.7 | 建立 `AGENTS.md`：本组 4 份文档为最高规范；Resolve 只能经 ResolveAdapter；EditSpec 必过 validator；Recipe 必有 preview + ACCEPTANCE | 文件存在 |
| 0.8 | 新建 `studio/` 骨架 + `pyproject.toml` entrypoint `aes = studio.cli:app` | `aes --help` 可运行 |
| 0.9 | 清理探测残留：删除 Resolve 中的 `_aes_capability_probe` 工程；`probe_resolve_capability.py` 保留至 Phase 1 结束 | — |
| 0.10 | ~~采集旧系统 KPI 基线~~ | ❌ **已放弃**（2026-07-25 决策） |

> **关于 0.10 的取消**：旧系统从未接入 Resolve，不具备真变速曲线、主体跟踪、
> 专业调色与 Fairlight，与 v2 不构成同一量级的可比对象。
> 因此 §18 的 KPI 改为**绝对目标值**，不做相对基线对比。

---

## 2. Phase 1 — Resolve PoC（1–2 周）

> MASTER PLAN §58/§59 最小可验证闭环。**不需要任何 AI。**

### 目标
手写一份最简 EditSpec → 连接 Resolve → 创建工程 → 导入媒体 → 创建 Bin →
创建时间线 → 按 in/out 放置片段 → 渲染预览。用户 **0 次 Resolve 手动操作**。

### 任务

| # | 任务 |
|---|---|
| 1.1 | `studio/core/timecode.py` —— 帧↔秒↔timecode 唯一实现，覆盖 23.976 / 24 / 30 / 60 / drop frame |
| 1.2 | `studio/core/{ids,hashing,cache,contracts}.py` 骨架 |
| 1.3 | `execution/resolve/connection.py` —— 环境变量注入、Python 版本校验、`GetResolve()`、Resolve 未启动时清晰报错、启动等待 |
| 1.4 | `execution/resolve/{project,media_pool,timeline,clips,render}.py` |
| 1.5 | `ResolveAdapter` 统一门面 + CI lint（`execution/resolve/` 之外禁止 import DaVinciResolveScript） |
| 1.6 | `config/resolve_capabilities.yaml` 初版，只把 PoC 中真正验证过的能力标 `verified` |
| 1.7 | **EditSpec schema 最小子集**：asset/shot 引用 + source in/out + timeline in/duration + track + timebase |
| 1.8 | `editspec/validator` 最小版 |
| 1.9 | `ResolveCompiler` 最小版，幂等可重跑 |
| 1.10 | CLI：`aes resolve build <spec>` / `aes resolve preview <spec>` |
| 1.11 | 最简 Revision：改一个 clip 的 in/out，只更新该 clip，时间线其余部分不动 |
| 1.12 | ★ **SmartReframe 实测**：横屏 1920×1080 源 → 4:5 时间线，逐 clip 调用 `SmartReframe`，人工比对与旧 `reframe_x` 的构图差距 |
| 1.13 | ★ **CreateMagicMask 实测**：动作镜头上生成跟踪遮罩，验证跟踪是否稳定 |
| 1.14 | ★ **SetSpeedRamp 实测**：验证三段变速（entry/impact/exit）是否真的生效，配合 `RetimeProcess=optical_flow` 看画质 |
| 1.15 | 探测结论回写 `config/resolve_capabilities.yaml`，对应能力转 `verified` |

> 1.12–1.14 原本属于 Phase 6，**提前到 Phase 1**。
> 理由：这三项决定了「史诗级提升」是否真的成立。
> - `SmartReframe` 若可用 → 竖屏重构图这一整类人工与质量问题直接消失
> - `CreateMagicMask` 若可用 → 主体跟踪从"做不到"变成"原生支持"
> - `SetSpeedRamp` 若可用 → 速度曲线从"平均倍率近似"变成"真正的冲击变速"
>
> 若这三项实测失败，最终成片质量的天花板会明显低于预期，
> 应在 Phase 1 结束时就知道，而不是投入 5 个 Phase 之后才发现。

### Phase 1 成功标准（硬性）

```json
[
  {"asset_id":"A","source":{"in_sec":10,"out_sec":12},"timeline":{"in_sec":0,"track":"V1"}},
  {"asset_id":"B","source":{"in_sec":30,"out_sec":32},"timeline":{"in_sec":2,"track":"V1"}}
]
```
一条命令跑完得到 preview.mp4，重跑幂等，改一个 clip 只更新一个 clip。

### 本 Phase 删除
无（旧系统尚未被覆盖，先保留 `v1-final` tag 之外的工作副本以便对照）。

---

## 3. ✅ Phase 2 — EditSpec 完整化 + 数据地基（2026-07-25 完成）

| # | 任务 |
|---|---|
| 2.1 | ✅ EditSpec 完整 schema（framing / camera / retime / transition / effects / color / audio / decision / markers / captions） |
| 2.2 | ✅ `editspec/validator`：时间轴、track kind、asset/shot、recipe 参数与验收物、capability 门禁 |
| 2.3 | ✅ `editspec/diff`：稳定 clip id；add/remove/replace/patch/shift；locked clip 保护 |
| 2.4 | ✅ `editspec/migrations`：只允许 v2.x forward migration，显式拒绝 v1 |
| 2.5 | ✅ `library/engine.v2.sqlite`：§57 全部实体 + Shot 新字段 + 精确 fps 有理数 |
| 2.6 | ✅ v1 → v2 事务 ETL；逐表与 embedding 计数一致，证据见 `docs/phase2_etl_report.json` |
| 2.7 | ✅ `studio/core/state.py`：合法转换、重试、失败诊断、Revision 回环、持久化 |

### Phase 2 验收
- ✅ ETL：40 assets、15,901 shots、15,901 embeddings 与旧库逐项一致
- ✅ 手写含 effects/color/audio/transition/caption 的完整 EditSpec 在已验收 Recipe 注入下全绿
- ✅ ResolveCompiler 在接触 Resolve 前一次报告所有未注册/未 verified Recipe
  "哪些 recipe 尚未 verified 因此拒绝执行"

### 本 Phase 删除
`anime/editspec.py`、`anime/db.py`、`anime/config.py`、`anime/cache.py`、
`anime/relink.py`、`anime/fps.py`、`scripts/`（全部 5 个）、`config.toml`

✅ 已删除；旧实现只存在于 `v1-final` tag。`config/app.yaml` 与
`config/models.yaml` 已建立。

---

## 4. Phase 3 — Asset Intelligence 重建 + 补齐（2–3 周）

| # | 任务 |
|---|---|
| 3.1 | `asset_intelligence/ingest` `shot_detection`（参照旧 `ingest.py` `shots.py` 参数重写） |
| 3.2 | `asset_intelligence/embeddings` `visual`（CLIP + wdtagger + 美学头） |
| 3.3 | 补齐确定性维度：`shot_scale`（主体框占比）、`subject_motion`（光流）、`compression_score`、`color_palette`、`audio_energy`、`music_presence`、`subtitle_region`（OCR） |
| 3.4 | 补齐模型维度：`pose_quality`、`face_visibility`、`eye_visibility`、`visual_energy` |
| 3.5 | `cutability`（首尾稳定度） |
| 3.6 | Cache key 含 model + model_version + pipeline_version（§51） |
| 3.7 | `characters` 表（`kit/aliases.json` 迁入） |
| 3.8 | 存量素材增量重跑，只补新字段 |

### 验收（§60）
一集动漫入库后，可用 `aes search` 按 character / action / motion / subtitle / face_quality
组合条件检索出正确 Shot；40 个已有素材全部完成新维度分析。

### 本 Phase 删除
`anime/ingest.py` `shots.py` `analyze.py` `embed.py` `tag.py` `aesthetic.py`
`acquire.py` `library.py` `reframe.py` `matte.py`

---

## 5. Phase 4 — Candidate Engine + Pairwise（2 周）

| # | 任务 |
|---|---|
| 4.1 | `editing/retrieval`（召回 100–300）与 `editing/ranking`（精排）两阶段 |
| 4.2 | `shot_scores`（intrinsic，跨项目缓存）与 `candidate_scores`（contextual，项目内）分表 |
| 4.3 | Contextual 信号：sequence_fit / music_fit / reference_fit / continuity / novelty |
| 4.4 | 按 narrative role 分组产出 A/B/C（`candidate_groups` 表） |
| 4.5 | `preference_pairs` 表 + 每次选择写入 winner/loser/context（§38） |
| 4.6 | Contact sheet + 视频预览生成（ffmpeg 工具级） |
| 4.7 | `studio/review` 后端 A/B/C 路由 |

### 验收（§61）
数百镜头 → 30–50 个真正值得看的候选，按 role 分组，用户候选接受率可度量。

### 本 Phase 删除
`anime/search.py` `candidates.py` `roughcut.py`、`decision_loop.py` 的评分与候选部分

---

## 6. Phase 5 — MusicMap + StyleFingerprint + DirectorPlan（2–3 周）

| # | 任务 |
|---|---|
| 5.1 | MusicMap：sections（自相似矩阵分段）/ drops / risers / breaks / silences / spectral changes |
| 5.2 | StyleFingerprint 批 1（10 项确定性）+ 批 2（3 项，依赖 Phase 3） |
| 5.3 | DirectorPlan 生成器 + `director_plans` 表 + `director_plan.yaml` 制品 |
| 5.4 | `creative/director`（产 plan）与 `editing/sequence`（产 spec）严格分离 |
| 5.5 | Sequence Planner：约束求解，纳入 continuity / motion direction / character continuity / visual phrase / shot length variation（§23） |
| 5.6 | `impact_budget` 密度控制（§32 反机械化） |
| 5.7 | StyleFingerprint → 版本化 EditingStyleProfile；支持多参考风格复用，不在 Planner 硬编码样片 |
| 5.8 | RhythmQA：cut density / median shot length / beat-sync 三项确定性验收 |

### 验收（§62/§63）
给定 reference.mp4，产出的 DirectorPlan 结构与参考片的段落/ASL/energy 曲线可量化对比，
偏差在阈值内；首剪输出 `editing_style_profile.json` 与 `rhythm_qa.json`；
用 `_archive/` 中的 4 份旧 beatmap 做 MusicMap 回归对照。

### 本 Phase 删除
`anime/beat.py` `reference.py` `director.py`、`decision_loop.py` 剩余部分（含 brief/gap/blueprint）

---

## 7. Phase 6 — Resolve 专业执行 + Recipe 库（3–5 周，风险最高）

> **整个改造中最容易被低估的部分**（见 GAP G3）。
> 完全改造口径下这里风险更高：没有 Remotion 兜底，Recipe 库不建好就完全出不了成片。

### 6A — Recipe 库建设（人工密集，可从 Phase 2 起穿插进行）

| # | 任务 |
|---|---|
| 6A.1 | 盘点旧 `renderer/src/effects/` 的全部特效，**只记录视觉意图**（代码不迁移） |
| 6A.2 | Fusion 内逐个重建：`impact_shake_v1` `anime_glow_v1` `white_flash_v1` `rgb_split_impact_v1` `speed_flash_v1` `camera_punch_v1` `motion_blur_transition_v1` `eye_focus_v1` |
| 6A.3 | 每个产出 `recipe.yaml` + `.setting` + `preview.mp4` + `ACCEPTANCE.md` |
| 6A.4 | Color Recipe：2 个现有 .cube → PowerGrade/DRX，另建 `anime_clean_v1` `anime_high_contrast_v1` `anime_cold_v1` `anime_fire_v1` `anime_night_blue_v1` `red_impact_v1` |
| 6A.5 | Sound Recipe：旧 `sound.py` 的 4 类程序合成 + `kit/sfx/` 归入 recipe 结构；建动作↔声音映射表 |

**硬规则**：无 `preview.mp4` + 人工签字的 `ACCEPTANCE.md` 的 recipe，
在 `resolve_capabilities.yaml` 中保持 `unverified`，AI 不得在 EditSpec 中生成它。

### 6B — Execution 接线

| # | 任务 |
|---|---|
| 6B.1 | `execution/resolve/fusion.py` `color.py` `fairlight.py` |
| 6B.2 | Reframe / Subject Tracking（Resolve 跟踪器；不可用退回静态 reframe） |
| 6B.3 | Retime / Speed Ramp（验证关键帧变速；不可用退回分段常速 + optical flow） |
| 6B.4 | Transition |
| 6B.5 | Preview 预设 与 Master 预设（同一条链路，不同预设） |
| 6B.6 | `critic/technical` 补齐至 §75 的 13 项 |
| 6B.7 | `execution/external_ai`：RIFE / Real-ESRGAN / rembg / whisper 作为按需工具，只在 Lock Picture 后执行（§33/§50） |
| 6B.8 | 逐项把 capability 从 `unverified` 转 `verified`，每项配一条测试 |

### 验收
用 Phase 4/5 产出的 EditSpec，走完整 Resolve 链路产出一条 25 秒成片，
Technical QA 13 项全绿，视觉质量经人工确认**不低于**旧 Remotion 线。

### 本 Phase 删除
**`renderer/` 整个目录**、`anime/render.py` `slowmo.py` `superres.py` `restore.py`
`interpolate.py` `enhance.py` `finalize.py` `master.py` `sound.py` `qa.py`
`quality_gate.py` `extreme.py` `sideways.py` `endcard.py`

> 到此为止 `anime/` 应仅剩 `cli.py` `experiment.py` `risk.py` `rights.py` `review_api.py`。

---

## 8. Phase 7 — Critic + Revision Loop（2–3 周）

| # | 任务 |
|---|---|
| 7.1 | `studio/agents/` LLM Provider 抽象（Claude / GPT / 本地），JSON schema 强约束输出 |
| 7.2 | `critic/creative`：Preview + DirectorPlan + StyleFingerprint → 结构化 issues |
| 7.3 | 自然语言反馈解析 → 与 Critic 同构的 Revision 提案 |
| 7.4 | Revision → EditSpec Diff → **选择性 Resolve 更新** |
| 7.5 | Technical QA 与 Creative Critic 在代码与数据结构上严格分离（§76）；`DELIVERED` 只由 Technical QA 把关 |
| 7.6 | Failure Recovery：每个 step 可重试 / 可恢复 / 可记录状态（§55） |

### 验收（§65）
"第 8 秒不够炸，结尾那个镜头换掉" → 系统只更新 2 个 clip → V2 预览。

### 本 Phase 删除
`anime/risk.py` `rights.py`

---

## 9. Phase 8 — Preference Learning（1–2 周）

| # | 任务 |
|---|---|
| 8.1 | Pairwise Ranker（数据来自 Phase 4 采集的 `preference_pairs`） |
| 8.2 | 记录 replacement / timing change / effect feedback / final survival |
| 8.3 | `studio/growth` 重建：Hook A/B + 发布指标 + `shot_outcomes` |
| 8.4 | **增长数据回流偏好**：留存跌幅作为负信号，与主观选择合并成 ranking signal |
| 8.5 | Personal Preference Profile 可视化；只作 signal 不作硬规则（§39） |

> 8.4 是本仓相对 MASTER PLAN 的增量，直接服务变现目标：
> 偏好不只来自主观点击，还来自真实播放数据。

### 本 Phase 删除
`anime/experiment.py`

---

## 10. Phase 9 — Review UI（2 周）

Project / Reference / Candidates(A|B|C) / First Cut / Revision(自然语言) / Final 六页面。
用户完全不需要 CLI。排在最后，符合 §69。

### 本 Phase 删除
`anime/review_api.py` `anime/cli.py` → **`anime/` 目录清空删除**；
`review-web/src/` 旧前端删除；`docs/` 7 篇旧文档删除并重写；README 重写。

---

## 11. Phase 依赖图

```
Phase 0 ──> Phase 1 ──> Phase 2 ──┬──> Phase 5 ──> Phase 6B ──> Phase 7 ──> Phase 8 ──> Phase 9
                                   │                 ▲
Phase 3 ───────────────────────────┤                 │
   │                                │                 │
   └──> Phase 4 ────────────────────┘                 │
                                                      │
Phase 6A (Recipe 库) ── 从 Phase 2 起穿插进行 ────────┘
```

- **Phase 3、4 可与 Phase 1、2 并行**（不碰 Resolve）
- **Phase 6A 必须尽早启动**：它是人工密集型且是 Phase 6B 的硬前置，
  在完全改造口径下没有 Remotion 兜底，6A 拖延 = 长期无成片

### 顺序已锁定：顺序 A（架构优先）

```
Phase 0 → 1 → 2 → 3/4(并行) → 5 → 6 → 7 → 8 → 9
                                ↑
                      Phase 6A 从 Phase 2 起穿插
```

不为缩短产量空窗而调整顺序。**架构干净是第一优先级**，
任何"先凑合出片"的临时通路都会污染 IR 并在后面加倍偿还。

代价与接受：Phase 1–6 期间新系统出片能力低于旧线，
`v1-final` tag 保留为纯应急手段（checkout 出片后立即切回，不在 v2 分支引入任何旧代码）。

---

## 12. 开发优先级（对齐 §68）

| 优先级 | 项目 | Phase |
|---|---|---|
| 最高 | EditSpec、ResolveAdapter、ResolveCompiler、Asset DB、Shot 模型、Candidate Retrieval | 1–4 |
| 第二 | Ranking、MusicMap、DirectorPlan、Sequence Planner | 4–5 |
| 第三 | Fusion / Color / Sound Recipes | 6A |
| 第四 | Critic、Preference Learning、Review UI | 7–9 |

---

## 13. 明令禁止（§69）

- ❌ 自研复杂 FFmpeg 转场 / 自研 GPU Renderer
- ❌ 未 recipe 化的一次性视觉效果
- ❌ 复杂 Web UI（Phase 9 之前）
- ❌ Fine-tune 大模型
- ❌ 100% Autonomous Agent
- ❌ 鼠标自动点击 Resolve（GUI fallback 默认禁用，需显式开关 + 记录）
- ❌ 分布式 / 云部署优化
- ❌ **为兼容任何旧概念而污染新 EditSpec**
- ❌ **在 `studio/` 中 import 任何 `anime/` 模块**（CI 强制）

---

## 14. 风险登记册

| ID | 风险 | 等级 | 触发信号 | 缓解 |
|---|---|---|---|---|
| R1 | Resolve 脚本 API 不支持逐片段关键帧变速 | 🔴 已触发 | `SetSpeedRamp` 不可调用 | Phase 6A 用 Fusion `TimeSpeed` Recipe；Phase 2.0 先验证 Export/Import/注参/渲染闭环 |
| R2 | **Fusion Recipe 库建设拖延导致长期无成片**（v2 无运行时兜底） | 🔴 | Phase 6A 超期 | 6A 从 Phase 2 起穿插；先用 Phase 2.0 验证 Recipe 技术闭环；`v1-final` 只供应急出片，不得复制代码回 v2 |
| R3 | 产量空窗期过长影响账号运营 | 🟠 ↓ | 连续数周无新片 | **用户已接受**；`v1-final` tag 为应急手段 |
| R4 | Resolve 必须前台运行 | 🟠 | 已确认 | 接受；启动等待 + `pgrep -x Resolve` 健康检查；渲染串行排队 |
| ~~R5~~ | ~~Python 版本与 fusionscript 绑定~~ | ✅ | — | **已解决**：venv 3.11.15 实测可用；`connection.py` 加版本校验 |
| R11 | `SmartReframe` / `CreateMagicMask` 不可用 | 🔴 已触发 | Phase 1.12/1.13 已证伪 | Phase 3 自研主体检测与逐帧跟踪，结果经已验证的 `transform` 通道写入 |
| R12 | 素材 23.976fps 与 4:5/60fps 交付的帧率换算 | 🟠 新增 | 亚帧漂移 | EditSpec 秒制为权威；`timecode.py` 单一实现 + 边界测试；Resolve 时间线帧率显式设定 |
| R13 | Resolve 转场无直接 TimelineItem API | 🟡 新增 | Phase 6B.4 | 探测中未见；需研究 Timeline 层方案或用 Fusion 转场 comp 替代 |
| R6 | ETL 数据丢失 | 🟠 | 新库计数不符 | Phase 0.4 已备份；ETL 后逐表计数比对 |
| R7 | 新架构重写引入的 bug 无旧测试覆盖 | 🟠 | — | 每个 Phase DoD 强制含测试；capability 转 verified 必须配测试 |
| R8 | LLM 接入的不确定性与成本 | 🟡 | Phase 7 | 结构化输出强约束；可关闭降级到规则 |
| R9 | 3072×3840@60 时间线性能 | 🟡 | Resolve 卡顿 | 代理时间线剪辑，母版分辨率只在 Master 阶段 |
| R10 | 素材版权（`source_records` 已降级为 provenance） | 🟡 | 平台下架 | 变现目标下应重新评估是否恢复导出门禁 |

---

## 15. 每个 Phase 的 Definition of Done（§90）

- [ ] Schema 已定义并纳入版本
- [ ] 实现完成
- [ ] 测试完成
- [ ] 结构化日志完成
- [ ] 错误处理与重试完成
- [ ] 版本兼容性已考虑（EditSpec / Recipe / Model / Analysis 版本）
- [ ] 文档更新（本组 4 份文档中受影响部分）
- [ ] **端到端路径已验证**：`aes` 一条命令跑通本 Phase 主用例
- [ ] **本 Phase 应删除的旧模块已删除**，且 CI 通过（无残留 import）
- [ ] `resolve_capabilities.yaml` 中新验证能力已转 `verified` 并配测试

---

## 16. 每次新增 Feature 前必须回答（§89）

1. 它属于哪一层？
2. 输入是什么？输出是什么？
3. 是否进入 EditSpec？
4. 是否需要 Resolve？对应 capability 是否 `verified`？
5. 是否应该 Recipe 化？
6. 是否能够 Cache？Cache key 含哪些版本？
7. 是否可测试？
8. 是否影响 Determinism？（涉及 LLM 是否已隔离在 `agents/`）
9. 是否真的降低用户人工操作？
10. 对出片效率 / 变现目标的贡献是什么？

回答不出来，就不要实现。

---

## 18. KPI —— 「史诗级提升」的定义（对齐 §44）

「极致」如果不能被测量就是口号。以下为**绝对目标值**，不做旧系统对比
（旧系统无 Resolve，不构成同一量级的可比对象）。
每个 Phase 结束时复测，**没有达到目标值的 Phase 不算完成**。

### 效率类

| 指标 | 定义 | 目标 | 何时兑现 |
|---|---|---|---|
| **Human Effort / Finished Video** | 一条 25s 成片的总人工时间 | **≤ 10 分钟** | Phase 7 |
| **Time To First Preview** | 素材已入库后，从下指令到看到第一版 | **≤ 30 分钟** | Phase 6 |
| **Manual Resolve Operations** | 用户手动操作 Resolve 的次数 | **0** | Phase 1 |
| **Revision Count to Lock** | 达到 Lock Picture 需要几轮 | **≤ 2 轮** | Phase 7 |
| **候选选择次数 / 片** | 用户需要做的 A/B/C 判断次数 | **≤ 15 次** | Phase 4 |

### 质量类

| 指标 | 定义 | 目标 | 何时兑现 |
|---|---|---|---|
| **Candidate Precision** | 用户接受的候选比例 | **≥ 50%** | Phase 4 |
| **First Cut Survival Rate** | AI 第一版镜头最终保留比例 | **60–80%** | Phase 5 |
| **Sequence Preservation** | 镜头顺序最终保留比例 | **≥ 70%** | Phase 5 |
| **Timing Delta** | 用户最终改动的镜头时长比例 | **≤ 20%** | Phase 5 |
| **Technical QA Pass Rate** | 首次渲染通过 13 项技术 QA 的比例 | **≥ 95%** | Phase 6 |

> **反作弊条款**（§44 原文要求）：
> First Cut Survival Rate 不得通过"过度保守剪辑"刷高——
> 若同期 Candidate Precision 下降或成片 `retention_3s` 走低，该指标作废。

### 能力类 —— 旧系统根本做不到的事

这一组不是"提升百分比"，而是**从 0 到 1**。全部依赖 Resolve Studio：

| 能力 | 旧系统 | v2 实现路径（Phase 1 实测后修正） | 兑现 Phase |
|---|---|---|---|
| 竖屏主体感知重构图 | `reframe_x` 单轴手搓偏移 | 自研主体检测 → `transform`（`SmartReframe` 实测空转） | Phase 3 |
| 主体跟踪 | rembg 静态遮罩，无跟踪 | 自研逐帧主体框 → 关键帧 Pan/Zoom（`CreateMagicMask` 不可用） | Phase 3 |
| 速度曲线 | `speed` 平均倍率近似 | Fusion `TimeSpeed` Recipe（无逐片段变速 API） | Phase 6A |
| 变速插值 | RIFE 外挂 + 额外转码 | ✅ 工程级 `imageRetimeInterpolation='opticalFlow'` 已验证生效（`ResolveCompiler._apply_retime_interpolation`）；RIFE 仍保留为需要逐镜切点保护/自定义模型时的备选 | Phase 6 |
| 调色 | 单条 LUT 文件 | ColorGroup + Version + 节点图 | Phase 6 |
| 音频 | ffmpeg 音床后处理 | Fairlight 多轨 + 音量自动化 | Phase 6 |
| 视觉特效 | Remotion CSS/SVG | Fusion 节点图 Recipe | Phase 6A |
| 渲染 | Remotion + ffmpeg concat | ✅ Resolve 渲染队列已验证 | Phase 1 ✅ |

### 变现类（Layer 7）

| 指标 | 定义 | 目标 |
|---|---|---|
| **出片吞吐** | 每周可交付成片数 | **≥ 5 条/周**（Phase 7 后） |
| **retention_3s** | 发布后 3 秒留存 | 建立采集，Phase 8 后进入偏好回流 |
| **Hook 实验周转** | 一组 A/B 从生成到有结论 | **≤ 3 天** |

### 「史诗级」的验收口径

同时满足以下三条，才算达成：

1. **一条 25s 成片的人工时间 ≤ 10 分钟，Resolve 手动操作 = 0**
2. **上表 7 项能力全部从"做不到"变为"原生支持且已 verified"**
3. **每周吞吐 ≥ 5 条，且技术 QA 首过率 ≥ 95%**

只提质量不提效率、或只提效率而质量下滑，都不算。

---

## 17. 下一步

截至 2026-07-25，Phase 3–9 的确定性实现、自动化测试、Resolve 技术 E2E
和六页面 Review UI 已落地。当前不得绕过的最后门槛：

2026-07-26 补充：`MotionPhrase`、单 comp Motion compositor 与 Motion QA 已完成
Resolve 真机验证并进入自动链。炼狱 V3 产品级预览已通过 Rhythm QA、Motion QA
与 13 项 Technical QA。所有者随后锁定 V3：40 个镜头固化为 revision 6，
最终 Color/Sound Recipe、`realesr-animevideov3-x3` 逐帧超分、3072×3840
Resolve H.265 delivery 均已跑通；645/645 帧完整，响度 -14.1 LUFS，最终
Technical QA 13/13 通过。自动指标不得替代后续作品的人工审美确认。

1. 所有者集中观看 21 个 Recipe 的 `preview.mp4`，逐项给出通过/拒绝和备注。
2. 只有人工通过项才可把 `config/recipes.yaml` 的 `verified` 转为 true；拒绝项继续迭代。
3. 使用单一主题素材从 Review UI 跑一次产品级 25 秒 E2E，确认创意质量而不只是 13 项技术 QA。
4. 完成后按 `docs/V2_COMPLETION_AUDIT.md` 再做一次逐项证据审计。

2026-07-26 产品验收补强：Motion System V2 与 Graphic Match V2 已进入自动链。
StyleFingerprint 1.5 以 10Hz 保存 velocity curve、运动峰值、零速点、方向反转和
cut-carry vector；Sequence Planner 2.3 删除固定 `index % 6` Hold 模板，并使用
主体中心、亮度中心、边缘方向区分 Carry/Reverse/Impact Cut。Motion QA 1.1
分别验收跨 Cut carry 与 phrase 内 reverse。炼狱 V6 Resolve preview 已通过
Rhythm、Motion 与 Technical QA；自动验收仍不得替代所有者观看确认。

### 产品验收补强：Production Readiness Gate

当前缺口：`first-cut` 会把“资产已登记”误当成“素材已准备”，无法阻止目标剧集
`shots=0` 或分析覆盖不足的项目进入选片。

| # | 任务 |
|---|---|
| PR.1 | 定义 versioned `SourceScope`、`CharacterAppearance`、`ProductionReadinessReport` schema |
| PR.2 | 新增 `aes prepare-production`：检查 asset/proxy/shots/analysis/candidate coverage |
| PR.3 | 只对 report blocker 指向的 asset 幂等执行 shots/analyze，并支持恢复 |
| PR.4 | `create_first_cut()` 强制验证最新 ready report 与 scope/cache versions |
| PR.5 | Review UI 展示缺失剧集、分析进度、候选分布与可执行修复 |
| PR.6 | E2E：高频剧集已入库但 `shots=0` 时首剪必须失败，补齐后通过 |

验收 KPI：未准备素材进入首剪次数为 0；定向补齐不得退化为全库扫描；
同作品同角色 Appearance Catalog 可跨项目复用；Time To First Preview 只从
Readiness Gate 通过后开始计算。

### 产品验收补强：Editorial Grammar

| # | 任务 |
|---|---|
| EG.1 | EditSpec 2.2 增加 `CutRelation` 与 `SourceSelection`，旧 2.1 制品前向迁移 |
| EG.2 | Sequence Planner 根据动作、运动、景别、亮度、情绪和共享语义解释相邻镜头 |
| EG.3 | 源区间从「代表帧居中」升级为带置信度的动作相位取点 |
| EG.4 | Recipe Planner 按镜头语义选择已验收 Effect/Color/Sound Recipe |
| EG.5 | 新增独立 `edit_grammar_qa.json`，不与 Rhythm/Motion/Technical QA 混分 |

该补强属于既有 Phase 5–7 产品级验收，不新增 Resolve capability，也不引入
LLM 时间码逻辑。真实动作 landmark 尚未落库时，Schema 必须保留低置信度证据，
禁止把 shot-level 估计写成逐帧检测结论。
