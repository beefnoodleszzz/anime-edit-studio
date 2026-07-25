# MIGRATION_PLAN.md

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
且 `RetimeProcess` / `MotionEstimation` 属性可写（光流变速质量可编程控制）。
风险从 🔴 降为 🟡，仍需 Phase 6 实测生效性。

**变化 2 —— Phase 6A Recipe 库成本下降。**
`ExportFusionComp` 存在，意味着 recipe 可以在 Fusion GUI 里**手工做好后导出 `.comp`**，
代码只负责 Import + 参数注入，不需要用脚本从零搭节点图。
这是 6A 从"逐个用脚本重建"降级为"逐个手工调好后导出"的关键差别。

**变化 3 —— 发现两个被低估的 Studio 原生能力，直接改写效率预期。**

| API | 旧系统的做法 | 意义 |
|---|---|---|
| `SmartReframe` | `reframe_x` 单轴偏移手搓 | **横屏动漫 → 4:5 竖屏的主体感知自动重构图**。这是每条片子都要做的事，也是旧系统画质与人工的主要痛点。提到 **Phase 1 就验证**，不等 Phase 6 |
| `CreateMagicMask` / `RegenerateMagicMask` | rembg 出**静态**遮罩，无跟踪 | 原生主体跟踪遮罩，直接实现 §5 的 `subject tracking` |

另外还发现 `Stabilize`、`DetectSceneCuts`、`CreateSubtitlesFromAudio`、
`ExportLUT`、`AssignToColorGroup` + `AddVersion`（ColorGroup 架构优于逐 clip 调色）。

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
| 0.10 | 建立 KPI 基线：用旧系统最近一条片子实测 §18 的 6 项指标，作为「史诗级提升」的对照基准 | `docs/kpi_baseline.md` |

> **0.10 不能跳过。** 「出片质量和效率史诗级提升」若没有改造前的基线数字，
> 就无法证明也无法证伪。基线必须在删旧代码之前采集。

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

## 3. Phase 2 — EditSpec 完整化 + 数据地基（1–2 周）

| # | 任务 |
|---|---|
| 2.1 | EditSpec 完整 schema（framing / camera / retime / transition / effects / color / audio / decision / markers / captions） |
| 2.2 | `editspec/validator` 完整：时间轴自洽、asset 可达、recipe 已注册、capability 可执行、无亚帧漂移 |
| 2.3 | `editspec/diff`：diff 生成 + patch 应用 |
| 2.4 | `editspec/migrations`：**只服务 v2.x 内部演进**，不含 v1→v2（v1 已弃） |
| 2.5 | 新 SQLite schema（含 §57 全部实体 + Shot 的 §11 全部字段） |
| 2.6 | **ETL：旧库 → 新库**（assets / shots / embedding / review_decisions / creative_briefs / preference_models / growth_* / shot_outcomes / source_records） |
| 2.7 | `studio/core/state.py` Workflow State Machine + `workflow_states` 表 |

### Phase 2 验收
- ETL 后，新库中素材数、镜头数、嵌入数与旧库一致
- 手写一份含 effects/color/audio 的完整 EditSpec，validator 全绿，ResolveCompiler 能报出
  "哪些 recipe 尚未 verified 因此拒绝执行"

### 本 Phase 删除
`anime/editspec.py`、`anime/db.py`、`anime/config.py`、`anime/cache.py`、
`anime/relink.py`、`anime/fps.py`、`scripts/`（全部 5 个）、`config.toml`

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

### 验收（§62/§63）
给定 reference.mp4，产出的 DirectorPlan 结构与参考片的段落/ASL/energy 曲线可量化对比，
偏差在阈值内；用 `_archive/` 中的 4 份旧 beatmap 做 MusicMap 回归对照。

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
| ~~R1~~ | ~~Resolve 脚本 API 不支持关键帧变速~~ | 🟡 ↓ | — | **已探测**：`SetSpeedRamp` 方法存在，`RetimeProcess`/`MotionEstimation` 可写。降级为"待 Phase 1.14 验证生效性" |
| R2 | **Fusion Recipe 库建设拖延导致长期无成片**（完全改造下无兜底） | 🔴 | Phase 6A 超期 | 6A 从 Phase 2 起穿插；**`ExportFusionComp` 已确认存在 → 手工做好导出而非脚本搭图，成本已下降**；必要时 checkout `v1-final` 应急 |
| R3 | 产量空窗期过长影响账号运营 | 🟠 ↓ | 连续数周无新片 | **用户已接受**；`v1-final` tag 为应急手段 |
| R4 | Resolve 必须前台运行 | 🟠 | 已确认 | 接受；启动等待 + `pgrep -x Resolve` 健康检查；渲染串行排队 |
| ~~R5~~ | ~~Python 版本与 fusionscript 绑定~~ | ✅ | — | **已解决**：venv 3.11.15 实测可用；`connection.py` 加版本校验 |
| R11 | `SmartReframe` / `CreateMagicMask` 实际效果不达预期 | 🟠 新增 | Phase 1.12/1.13 | 若失败，竖屏重构图与主体跟踪退回旧方案，"史诗级提升"的幅度需下调预期。**必须在 Phase 1 就知道** |
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

「极致」如果不能被测量就是口号。以下 6 项在 Phase 0.10 采集旧系统基线，
每个 Phase 结束时复测。**没有达到目标值的 Phase 不算完成。**

### 效率类

| 指标 | 定义 | 旧系统基线 | 目标 | 何时兑现 |
|---|---|---|---|---|
| **Human Effort / Finished Video** | 一条 25s 成片的总人工时间 | 待 0.10 采集 | **≤ 10 分钟** | Phase 7 |
| **Time To First Preview** | 素材已入库后，从下指令到看到第一版 | 待采集 | **≤ 30 分钟** | Phase 6 |
| **Manual Resolve Operations** | 用户手动操作 Resolve 的次数 | N/A（旧系统无 Resolve） | **0** | Phase 1 |
| **Revision Count to Lock** | 达到 Lock Picture 需要几轮 | 待采集 | **≤ 2 轮** | Phase 7 |

### 质量类

| 指标 | 定义 | 旧系统基线 | 目标 | 何时兑现 |
|---|---|---|---|---|
| **Candidate Precision** | 用户接受的候选比例 | 待采集 | **≥ 50%** | Phase 4 |
| **First Cut Survival Rate** | AI 第一版镜头最终保留比例 | 待采集 | **60–80%** | Phase 5 |
| **Sequence Preservation** | 镜头顺序最终保留比例 | 待采集 | **≥ 70%** | Phase 5 |
| **Timing Delta** | 用户最终改动的镜头时长比例 | 待采集 | **≤ 20%** | Phase 5 |
| **Technical QA Pass Rate** | 首次渲染通过 13 项技术 QA 的比例 | 待采集 | **≥ 95%** | Phase 6 |

> **反作弊条款**（§44 原文要求）：
> First Cut Survival Rate 不得通过"过度保守剪辑"刷高——
> 若同期 Candidate Precision 或成片的 `retention_3s` 下降，该指标作废。

### 变现类（本仓特有，Layer 7）

| 指标 | 定义 | 目标 |
|---|---|---|
| **出片吞吐** | 每周可交付成片数 | 相对基线 **≥ 3×** |
| **retention_3s** | 发布后 3 秒留存 | 相对基线**不下降**，Phase 8 后应上升 |
| **Hook 实验周转** | 一组 A/B 从生成到有结论的天数 | 缩短至基线的 1/2 |

### 「史诗级」的验收口径

同时满足以下三条，才算达成：

1. **人工时间下降一个数量级**（Human Effort / Finished Video）
2. **Resolve 手动操作 = 0**，且成片具备旧系统做不到的能力
   （真变速曲线 / 主体跟踪 / 专业调色 / Fairlight）
3. **出片吞吐 ≥ 3×，且留存指标不下降**

只提升质量不提升效率、或只提升效率而质量下滑，都不算。

---

## 17. 下一步

架构分析完成，决策已锁定，Phase 0.6 硬门槛已通过。

**剩余的 Phase 0 任务（0.1–0.5、0.7–0.10）可以开始执行。**
这些都是清场与地基工作，不写业务逻辑，风险低且可回退：

| # | 动作 | 是否改动你的仓库 |
|---|---|---|
| 0.1 | 提交/stash 工作区 14 个已改文件 | ✏️ git |
| 0.2 | 打 tag `v1-final` | ✏️ git |
| 0.3 | 建 `v2` 分支 | ✏️ git |
| 0.4 | 备份 `library/engine.sqlite` | ✏️ 新增文件 |
| 0.5 | 旧 editspec/beatmap 移入 `projects/_archive/` | ✏️ 移动文件 |
| 0.7 | 建 `AGENTS.md` | ✏️ 新增文件 |
| 0.8 | 建 `studio/` 骨架 + entrypoint | ✏️ 新增文件 |
| 0.9 | 删 Resolve 中的 `_aes_capability_probe` 工程 | ✏️ Resolve |
| 0.10 | 采集 KPI 基线 → `docs/kpi_baseline.md` | ✏️ 新增文件 |

0.10 需要你提供数据（最近一条片子花了多久、审了多少候选、改了几轮），
其余我可以直接做。

之后进入 Phase 1，第一个里程碑是 §2 的成功标准 + 1.12–1.14 三项能力实测。
