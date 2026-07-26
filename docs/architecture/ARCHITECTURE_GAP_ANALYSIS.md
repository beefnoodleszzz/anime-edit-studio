# Architecture Gap Analysis

> 对照 `docs/product/WANT.md`（ANIME_EDIT_STUDIO_MASTER_PLAN）逐条判断现状差距。
> 判定口径：**EXISTS** 已具备 / **PARTIAL** 部分具备 / **MISSING** 完全缺失 /
> **REFACTOR** 已有但架构错误须改造 / **DEMOTE** 保留但降级 / **REMOVE** 应废弃 / **REUSE** 直接复用

---

## 0. 总体判定

| 层 | 完成度 | 判定 |
|---|---|---|
| Layer 1 Asset Intelligence | ~45% | PARTIAL — 管线在，维度缺一半 |
| Layer 2 Creative Intelligence | ~30% | PARTIAL — 有 Brief，无 DirectorPlan / 无 StyleFingerprint |
| Layer 3 Editing Intelligence | ~40% | PARTIAL — 有检索排序，Sequence Planner 是启发式而非规划器 |
| Layer 4 EditSpec（IR） | ~25% | REFACTOR — 存在但是渲染器私有镜像，须整体重设计 |
| Layer 5 Execution Engine | **0%** | MISSING — 无任何 Resolve 代码 |
| Layer 6 Critic / QA | ~35% | PARTIAL — Technical QA 好，Creative Critic 完全没有 |

**结论：主链 `Asset → Director → Candidate → Sequence → EditSpec → Resolve → Preview → Critic → Feedback → Revision`
在 "EditSpec → Resolve" 处完全断裂，且 EditSpec 本身不具备承担 IR 的能力。**

---

## 1. 产品定位与用户体验（MASTER PLAN §1–§3）

| 要求 | 判定 | 说明 |
|---|---|---|
| 不再是"EditSpec + FFmpeg CLI 剪辑器" | REFACTOR | 现状是"EditSpec + **Remotion** CLI 剪辑器"，换了后端但形态未变 |
| DaVinci Resolve 作为专业执行引擎 | MISSING | 0 行代码 |
| 用户不操作 Resolve | N/A | 尚无 Resolve 可操作 |
| 用户只做候选选择 + 自然语言反馈 | PARTIAL | 候选选择已有（use/alternate/reject + rating + trim）；**自然语言反馈完全缺失** |
| AI 决策与执行分离 | PARTIAL | 决策确实在 Python 侧完成，方向正确；但决策与执行的边界未收敛为 IR |
| 多 AI 角色（素材/选片/Director/VFX/Color/Sound/Critic/QA） | MISSING | 无 Agent 概念，全部为确定性函数 |

**重要发现**：MASTER PLAN 全篇假设系统由 LLM/VLM 驱动，但**现仓库没有任何 LLM/VLM 调用**。
`director.py` 的"AI 导演"是 CLIP 提示词分类 + 能量阈值规则。
这意味着 MASTER PLAN §74（"哪个镜头更帅""用户说不够帅是什么意思"）所需的语义推理层是**从零开始**，
不是"改造"而是"新建"。

---

## 2. EditSpec（§4–§6）

| 要求 | 判定 | 现状 |
|---|---|---|
| EditSpec 作为最重要 IR | REFACTOR | 现为 Remotion 契约，文件头明写"需与 renderer/src/schema.ts 保持一致" |
| 禁止设计成渲染后端私有结构的镜像 | **违反** | 帧制、`effects.type` 枚举、`camera_move`、`fill_mode` 全部是 Remotion 概念 |
| 可序列化 | EXISTS | pydantic + JSON |
| 可验证 | MISSING | 无 validator、无约束检查 |
| 可版本控制 | MISSING | 无 `version` 字段、无 migrations 目录 |
| 可 Diff | MISSING | 修订靠新文件名（`.v2` / `.restore.rife.sr`） |
| 可重新执行 | PARTIAL | Remotion 可重跑；跨渲染器不可 |
| 可部分修改 | MISSING | 无 diff → 只能整份重写 |
| 可生成 Resolve 工程 | MISSING | — |
| 多 Renderer 支持 | MISSING | 单后端绑定 |

### §5 字段级差距

| 要求字段 | 现状 |
|---|---|
| source asset / in / out | PARTIAL — 有 `src`(路径) + `source_in_sec`，**无 out，无 asset_id/shot_id 引用** |
| timeline in / duration | EXISTS（帧制） |
| track | MISSING — 无多轨概念 |
| shot role | MISSING — `slot` 存在 DB 但不进 EditSpec |
| crop / reframing | PARTIAL — 有 `reframe_x` 单轴偏移 + `fill_mode` |
| subject tracking | MISSING — `matte` 是静态遮罩，非跟踪 |
| retiming / speed ramp | PARTIAL — `speed` + `ramp` 字符串，无 entry/impact/exit 三段参数 |
| transition | PARTIAL — 有 in/out 字符串 + intensity，非结构化 |
| VFX | REFACTOR — `effects[{type,intensity}]`，type 是渲染器私有枚举，非 recipe |
| color profile | REFACTOR — 只有单条 `lut` 文件路径，无 recipe 概念 |
| sound design | MISSING — EditSpec 内无 per-clip SFX，音效在 `sound.py` 后处理阶段外挂 |
| volume automation | MISSING — 只有 `AudioLayer.gain_db` 常量 |
| markers | MISSING |
| captions | PARTIAL — `TextOverlay` 存在但为 hook 文案，非字幕 |
| metadata / confidence / reasoning | MISSING |
| version | MISSING |

**判定：EditSpec v1 必须整体重设计为 v2，不能增量打补丁。** 这是全部改造的第一优先级。

---

## 3. Asset Intelligence（§9–§12）

### 入库流程（§10）

| 步骤 | 判定 |
|---|---|
| Technical Probe | EXISTS — `ingest.py` ffprobe + sha256 |
| Scene Detection | EXISTS — `shots.py` PySceneDetect |
| Shot Detection | EXISTS |
| Keyframes / Proxy / Thumbnail / Contact Sheet | EXISTS |
| Multimodal Analysis | PARTIAL — CLIP 嵌入 + wdtagger 标签 + 亮度/清晰度/运动，无 VLM 语义理解 |
| 一次分析、跨项目复用 | EXISTS — 全局 `library/engine.sqlite`，符合 §9 要求 |

### Shot 字段（§11）—— 逐项对照

| 要求 | 现状 | 判定 |
|---|---|---|
| shot_id / asset_id / start / end / duration | ✅ | EXISTS |
| characters + confidence | `character` 文本 | PARTIAL — 无置信度 |
| action + confidence | `action` 文本 | PARTIAL — 无置信度 |
| shot_scale | — | MISSING |
| camera_motion | `camera` 文本 | PARTIAL |
| subject_motion | — | MISSING |
| motion_direction | `motion_dir` | EXISTS |
| motion_intensity | `motion_mag` | EXISTS |
| composition | `shot_scores.composition_quality` | PARTIAL — 是评分不是描述 |
| pose_quality | — | MISSING |
| face_visibility / eye_visibility | — | MISSING（`director._anchor_score` 用 wd 标签近似） |
| visual_energy | — | MISSING |
| image_quality | `sharpness` + `aesthetic` | PARTIAL |
| blur_score | `sharpness` 反向近似 | PARTIAL |
| compression_score | — | MISSING |
| subtitle_presence | `shot_scores.subtitle_risk` | PARTIAL |
| subtitle_region | — | MISSING |
| color_palette | — | MISSING |
| brightness | `brightness` + `min_brightness` | EXISTS |
| emotion | `emotion` | EXISTS |
| semantic_tags | `tags`（wdtagger） | EXISTS |
| dialogue | `dialogue`（whisper） | EXISTS |
| audio_energy | — | MISSING |
| music_presence | — | MISSING |
| cutability | — | MISSING |
| embedding | `embedding` BLOB | EXISTS |

**覆盖率约 45%。缺失集中在"镜头语言"维度（景别、构图描述、姿态、面部、视觉能量、可切性）
和"音频"维度——恰恰是 Ranking 与 Sequence Planning 最需要的信号。**

### §12 禁止单一 aesthetic_score

| 判定 | PARTIAL / 部分违反 |
|---|---|

好的一面：`shot_scores` 已是 13 维 + 可配置权重 + `explanation_json`，符合"多维"精神。
问题：
1. 仍然收敛到单一 `final_score` 用于排序
2. `shots.aesthetic` 列（LAION 美学头）被用作独立筛选阈值
3. **完全没有 Contextual Suitability** —— 评分不考虑 previous/next shot、
   music position、sequence role 之间的交互，只在 `_structure_match` 里做了粗粒度 role 匹配

**MASTER PLAN 要求的 Intrinsic Quality / Contextual Suitability 分离尚未建立。**

---

## 4. Candidate 系统（§13–§15）

| 要求 | 判定 |
|---|---|
| 第一阶段快速召回 100–300 shots | PARTIAL — `search.py` FTS + 过滤可做，但无显式两阶段设计 |
| 多维 Ranking → 30–50 候选 | PARTIAL — `score_project_shots` 有 13 维，但缺 sequence fit / music fit / reference fit / novelty |
| 候选按 Narrative Role 分组 A/B/C | MISSING — `review-web` 是平铺列表；`director.classify_slots` 有 slot 但未用于候选分组 |
| UI 提供 [选A][选B][选C][AI决定] | MISSING |

---

## 5. Creative Intelligence（§16–§19）

| 要求 | 判定 | 说明 |
|---|---|---|
| 独立 Creative Director 角色 | PARTIAL | `director.py` 存在但直接做时间线装配，越层了 |
| DirectorPlan 作为独立产物 | **MISSING** | `brief` → 直接 → `editspec`，中间无 plan 制品 |
| DirectorPlan 含 structure/energy 曲线 | PARTIAL | `creative_briefs.structure_json` 有结构，无 energy 曲线、无 visual_rules、无 editing_rules |
| Reference Video Intelligence | PARTIAL | `reference.py` 有节奏语法 |
| 禁止只提取 BPM/LUT/转场数量 | **合规** | 现有实现已超出这个层次（时长聚类、交替性、规律性） |
| StyleFingerprint（§19 共 22 项） | MISSING | 现有覆盖约 6 项：shot_count / shot_length_distribution / mean / median / cut_density / 规律性 |

### §19 StyleFingerprint 逐项

已有：`shot_count`、`shot_length_distribution`、`mean_shot_length`、`median_shot_length`、`cut_density`、`duration`

缺失：`hard_cut_ratio`、`transition_types`、`beat_sync_ratio`、`music_structure`、`energy_curve`、
`shot_scale_sequence`、`motion_direction_sequence`、`speed_ramp_locations`、`slow_motion_locations`、
`camera_motion`、`color_progression`、`brightness_curve`、`sound_effect_density`、`impact_points`、
`silence_usage`、`visual_rhyme`、`motion_rhyme`

**约 27% 覆盖。"像 a.mp4"这个核心功能目前只能像它的切点密度，不能像它的镜头语法。**

---

## 6. 音乐分析（§22）

| 要求 | 现状 | 判定 |
|---|---|---|
| BPM / beats / onset | `beatmap.json` ✅ | EXISTS |
| bars / downbeats | `downbeats[]` ✅ | EXISTS |
| energy | `beat_energy[]` ✅ | EXISTS |
| sections（intro/build/drop） | — | **MISSING** |
| drops / riser / break / silence | — | **MISSING** |
| spectral changes | — | MISSING |
| MusicMap 制品 | — | MISSING（现有 `beatmap.json` 是其子集） |

**§23 "Sequence Planning ≠ Beat Cut"**：现状 `director.direct()` 正是"按拍能量决定切点疏密"——
比纯 beat cut 好，但仍主要是节奏驱动，**未考虑 action continuity、motion direction 冲突、
character continuity、visual phrase**。判定 PARTIAL / 部分违反。

---

## 7. Resolve 执行层（§24–§30）

| 要求 | 判定 |
|---|---|
| ResolveAdapter 独立模块（12 个文件） | MISSING（全部） |
| 执行优先级 API > Fusion API > Template > Interchange > GUI | MISSING |
| `resolve_capabilities.yaml` | MISSING —— 但 `extreme.capabilities()` 提供了可直接借鉴的模式 |
| Effect Recipe Library | MISSING —— 特效硬编码在 `renderer/src/effects/` TSX 内 |
| Color Recipe | MISSING —— 只有 2 个 .cube 文件（signature_teal_orange / cinematic_strong）|
| Sound Recipe + SFX 分类 | PARTIAL —— `sound.py` 已有 impact/whoosh/riser/subdrop 4 类程序合成，kit/sfx/ 有 4 个 wav |
| 动作↔声音映射（§32） | MISSING |
| 上层禁止散落调用 Resolve API | N/A |

**这是全仓最大的单块缺口，且是 Phase 1 唯一必须验证的东西。**

### 隐藏成本警告

`renderer/src/effects/` 内的 CSS/SVG/GLSL 特效与 Fusion 节点图**没有任何映射关系**。
迁移到 Resolve 后，现有 6 个项目的视觉风格**无法自动复现**。
Effect/Color Recipe 库必须在 Fusion 内从零重建并逐个视觉验收——
这是 MIGRATION_PLAN 中最容易被低估的工作量。

---

## 8. Preview / Critic / Revision（§33–§36）

| 要求 | 判定 |
|---|---|
| Preview First（低成本预览优先） | EXISTS — `render --preview` 0.5 缩放；`finalize` 才跑重处理。**架构思想已正确** |
| Lock Picture 后才跑重处理 | PARTIAL — 有 `finalize` 阶段，但无显式 Lock 状态 |
| Critic Agent（Creative/Editing/Visual/Audio/Technical 五类） | PARTIAL |
| ├ Technical | EXISTS — `qa.py` 规格/黑帧/响度 |
| ├ Editing（重复镜头） | PARTIAL — `quality_gate.py` dhash 重复检测 |
| ├ Visual（切脸/出框/字幕残留/模糊） | MISSING |
| ├ Audio（SFX 过多/Impact 对齐/clipping） | PARTIAL — master 阶段有响度，无对齐检查 |
| └ Creative（是否符合 DirectorPlan/参考风格/Drop 冲击） | **MISSING** |
| Revision Loop（Diff → 选择性更新） | MISSING — 现状整份重生成 |
| 自然语言反馈 → 结构化 Revision | **MISSING** |

**§76 Creative QA 与 Technical QA 必须分离**：现状 `quality_gate.status()` 把结构审计与增强裁决
混在一个 `pass` 布尔里，判定 **部分违反 / REFACTOR**。

---

## 9. 偏好学习（§37–§39）

| 要求 | 判定 |
|---|---|
| Preference Memory | EXISTS — `review_decisions` + `preference_models` |
| 禁止第一阶段 Fine-tune | **合规** — 现用规则 + 逻辑回归 |
| 记录 selected / rejected / context / role / music_position / alternatives | PARTIAL — 有 decision/rating/preferred_role，**无 music_position、无 alternative_shots** |
| **Pairwise Preference（A vs B）** | **MISSING** — 现为逐镜头 pointwise 打标 |
| Personal Preference Profile 作为 Ranking Signal | EXISTS — `preference_score` 进权重，不作硬规则，合规 |

**§38 明确说"最重要的数据不是用户喜欢 shot_01，而是在 A 和 B 中选了 A"——
这正是现状缺的那一半，且直接依赖 §15 的 A/B/C 候选 UI。两者必须一起做。**

---

## 10. Review UI（§40–§42）

| 要求 | 判定 |
|---|---|
| 独立 Review UI（不审 Resolve 时间线） | EXISTS — FastAPI + React 已有真实闭环 |
| 页面：Project/Reference/Candidates/First Cut/Revision/Final | PARTIAL — 有 Project/Candidates/Variants，无 Reference/Revision/Final |
| A/B/C 候选选择 | MISSING |
| Preview 播放 + 自然语言输入 | MISSING |

---

## 11. 工程原则（§51–§57, §70–§76）

| 要求 | 判定 |
|---|---|
| 缓存可复用（§51） | EXISTS — `cache.py` 内容哈希；渲染逐镜缓存；增强带缓存 |
| Cache Key 含 model / model version / pipeline version | PARTIAL — 现主要基于内容 hash + 参数 |
| 可重复性（§52） | PARTIAL — EditSpec 可重跑，但无 recipe/model version 记录 |
| Versioning（§53，6 类版本） | MISSING — 只有 `shot_scores.score_version` 和 `preference_models.version` |
| Diff 优先修订（§54） | MISSING |
| Failure Recovery（§55） | MISSING — 无重试/恢复/状态记录机制 |
| Workflow State Machine（§56，14 状态） | MISSING — 状态隐含在文件存在性中 |
| 数据库实体（§57，15 类） | PARTIAL — 已有 Asset/Shot/Project/Reference(部分)/Candidate/UserFeedback/Preference/Render(部分)/QAResult(部分)；**缺 Character / MusicTrack / DirectorPlan / EditSpec / EditVersion / Recipe** |
| typed / modular / testable / observable / versioned / recoverable（§70） | PARTIAL — typed ✅(pydantic)，modular ⚠️(上帝模块)，testable ❌(7% 覆盖)，observable ❌，versioned ❌，recoverable ❌ |
| 禁止巨型脚本 / 隐式全局状态 | 部分违反 — `decision_loop.py` 1274 行；`scripts/` 绕过 CLI |
| Prompt 不得成为业务逻辑（§71） | **合规**（因为根本没有 prompt） |
| AI 输出必须结构化（§72） | N/A |
| Deterministic Core（§73） | EXISTS — 时间码/帧转换/媒体查找/缓存/渲染参数/hash/校验全部确定性实现，**这一条现状做得很好** |
| 技术 QA 自动化（§75，13 项） | PARTIAL — `qa.py` 覆盖约 7 项（存在/时长/分辨率/fps/编码/黑帧/响度），缺冻结帧/丢帧/损坏/意外静音/画幅比 |
| §90 Definition of Done | MISSING — 无此约定 |

---

## 12. 模块级处置决定

> **口径：完全改造**（用户 2026-07-25 决策）。不保留兼容层，不做代码搬运，
> 不需要的直接删除。旧代码的价值在于**算法思路与踩过的坑**，不在于代码本身。

### 判定分三类

| 类别 | 含义 |
|---|---|
| **PORT-LOGIC** | 算法/参数/经验有价值 → 在新分层下**重写**，参照旧实现但不复制文件 |
| **DELETE** | 无价值或与新架构冲突 → 直接删除 |
| **NEW** | 全新建设 |

### PORT-LOGIC — 重写时参照旧实现

| 旧模块 | 新位置 | 需要带走的东西 |
|---|---|---|
| `ingest.py` `shots.py` `acquire.py` `library.py` | `studio/asset_intelligence/ingest`、`shot_detection` | ffprobe 参数、sha256 策略、VideoToolbox 代理参数、PySceneDetect 阈值 27.0 |
| `embed.py` `tag.py` `aesthetic.py` | `asset_intelligence/embeddings`、`visual` | open-clip ViT-B-32 + MPS 配置、wdtagger 用法、LAION 头的降级策略 |
| `analyze.py` | `asset_intelligence/{visual,motion}` | 亮度/清晰度/运动方向的实现（并大幅扩展） |
| `beat.py` | `editing/music` | librosa 参数（现有 beatmap 输出质量已验证：123BPM/103拍/25 downbeat 可信） |
| `reference.py` | `creative/reference` | 节奏语法算法：时长聚类、交替性、规律性评分——**这是全仓最有价值的原创逻辑之一** |
| `decision_loop.py` 的评分部分 | `editing/ranking` | 13 维定义、权重配置模式、`explanation_json` 可解释性设计 |
| `decision_loop.py` 的偏好部分 | `creative/preference` | 规则 + 逻辑回归双模型、`explain_preference` |
| `decision_loop.py` 的 brief/gap 部分 | `creative/intent` | 导演合约字段设计（观众/承诺/兑现/余味/母题/必含禁含/成功标准）——设计本身很好 |
| `director.py` | `creative/director` + `editing/sequence` | 能量驱动切点疏密、`_anchor_score` 正脸+居中、CLIP 去重、whip/smear/pushpull 的**创意意图**（实现要换成 Fusion recipe） |
| `search.py` | `editing/retrieval` + `ranking` | FTS5 查询构造 |
| `qa.py` | `critic/technical` | ffmpeg 探测命令（规格/黑帧/响度），补齐至 §75 的 13 项 |
| `sound.py` | `execution/recipes/sound` | impact/whoosh/riser/subdrop 的程序合成算法 → 转为 Sound Recipe 资产 |
| `extreme.py` `capabilities()` | `execution/resolve/capability.py` | **"显式声明 ready/unavailable/fallback、绝不假称已执行"的设计模式** |
| `experiment.py` | `studio/growth` | Hook A/B 矩阵、发布指标字段、`shot_outcomes` 留存跌幅模型 |
| `cache.py` `config.py` | `studio/core` | 内容哈希缓存思路（Cache key 需补 model/pipeline version） |
| `db.py` | `studio/core` + 各层 repository | **版本化幂等迁移框架的模式**（代码重写，模式保留） |
| `review_api.py` `review-web/` | `studio/review` + 新前端 | 路由划分方式；UI 形态因 A/B/C 与自然语言反馈而重做 |
| `render.py` 的**缓存思想** | `execution/resolve/timeline.py` | 逐镜内容 hash → 只重建变化片段。这个思想直接对应 §54 选择性 Resolve 更新 |

### DELETE — 直接删除

| 删除项 | 理由 |
|---|---|
| `renderer/` 整个 Remotion 工程 | Resolve 为唯一成片后端 |
| `anime/editspec.py` | Remotion 私有镜像，违反 §6 |
| `anime/render.py` `slowmo.py` 的 Remotion 桥接 | 后端已删 |
| `anime/sideways.py` `endcard.py` `matte.py` | 低价值；Resolve 有原生等价物 |
| `anime/roughcut.py` | 被 Sequence Planner 取代 |
| `anime/finalize.py` `enhance.py` `restore.py` `superres.py` `interpolate.py` `fps.py` `relink.py` `master.py` | 「作用于 EditSpec 文件产出新 EditSpec 文件」的链式后处理范式与 v2 IR + Resolve 执行冲突；能力改由 Resolve 节点 / external_ai 工具在执行期完成 |
| `anime/quality_gate.py` | 混合了 Technical 与 Creative 判定（违反 §76），拆开重写 |
| `anime/risk.py` `rights.py` `experiment.py` 中的权利门禁残留 | 已被降级为 provenance，新架构中归入 `growth` 与素材元数据 |
| `scripts/` 全部 5 个 | 绕过 CLI 直改 EditSpec |
| `review-web/src/` | 重写 |
| `config.toml` | → `config/app.yaml` |
| `tests/` 全部 3 个 | 测的是被删模块 |
| `docs/` 7 篇 | 重写（`reference-dna.md` / `preference-learning.md` 的思路已吸收进本组文档） |
| `projects/*/editspec.*.json` `*.staged.json` | 移入 `_archive/` 只读 |
| README 现有架构描述 | 重写 |

### NEW — 全新建设

- `studio/execution/resolve/*`（13 个模块 + ResolveAdapter）
- `config/resolve_capabilities.yaml`
- `studio/execution/recipes/`（Effect / Color / Sound，每个含 preview.mp4 + ACCEPTANCE.md）
- `studio/editspec/{schema,validator,migrations,diff}`
- `studio/creative/director` DirectorPlan 制品
- StyleFingerprint 的 §19 剩余 16 项
- MusicMap 的 sections / drops / risers / silences
- `studio/critic/creative`（Creative Critic + 自然语言 Revision 解析）
- `studio/agents/`（LLM/VLM Provider 抽象，§48）
- `studio/core/state.py` Workflow State Machine
- `preference_pairs` Pairwise 采集
- A/B/C 候选 UI + 自然语言反馈通道
- Shot 模型的 12 个缺失维度

### 数据归零（2026-07-26 用户决定）

旧项目、v1/v2 数据库与 `library/proxies`、`keyframes`、`cache` 已清空。
仅永久源素材目录保留。新制作从空白 v2 schema 重新 ingest 与分析，不再执行旧库 ETL。

---

## 13. 差距总结（按风险排序）

| # | 差距 | 风险等级 | 说明 |
|---|---|---|---|
| G1 | 无 Resolve 集成 | 🔴 极高 | 整个 Execution Engine 层从零建 |
| G2 | EditSpec 是渲染器私有镜像 | 🔴 极高 | 违反 §6 核心禁令，所有下游依赖它 |
| G3 | Effect/Color Recipe 无 Fusion 对应物 | 🔴 极高 | 视觉风格无法自动迁移，需人工逐个重建验收 |
| G4 | 无 LLM/VLM，语义推理层为零 | 🟠 高 | MASTER PLAN 大部分"AI"能力实为新建 |
| G5 | 无 Diff / 版本 / 状态机 / 恢复 | 🟠 高 | Revision Loop 无法实现 |
| G6 | 测试覆盖 7% | 🟠 高 | 重构无安全网，`decision_loop` 拆分风险大 |
| G7 | Shot 维度缺一半 | 🟡 中 | Ranking 与 Sequence 质量天花板受限 |
| G8 | 无 Pairwise Preference | 🟡 中 | 偏好学习信号质量受限 |
| G9 | StyleFingerprint 覆盖 27% | 🟡 中 | "像 a.mp4"是核心卖点，当前只像节奏 |
| G10 | MusicMap 无 sections/drops | 🟡 中 | DirectorPlan 的结构无法与音乐结构对齐 |
| G11 | Creative QA 与 Technical QA 混一 | 🟢 低 | 拆分成本不高 |
| G12 | 工作区 14 个未提交文件 | 🟢 低 | 迁移前必须先落地 |

> 完全改造口径下，G2（EditSpec 是渲染器镜像）与 G6（测试覆盖 7%）风险实际下降——
> 前者直接弃用重写，后者旧测试随旧代码一起删。
> 但 **G3 风险上升**：删除 Remotion 后没有兜底渲染器，
> Fusion Recipe 库不建好就完全出不了成片。详见 MIGRATION_PLAN R2/R3。
