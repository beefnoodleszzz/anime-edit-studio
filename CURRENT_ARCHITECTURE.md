# CURRENT_ARCHITECTURE.md

> 本文档描述 anime-edit-studio 在改造开始前的**真实现状**，不含任何目标设计。
> 审计基准：git HEAD `6ca536c`（Add pre-production director contract），工作区含 14 个未提交修改文件。
> 审计日期：2026-07-25

---

## 0. 一句话现状

当前系统是一个**本地优先、无 LLM、以启发式规则 + CLIP 嵌入驱动的"选片-评分-蓝图-Remotion 渲染"工作站**，
主链路完整可跑通并已产出 6 个真实项目，但**完全没有 DaVinci Resolve 相关代码**，
EditSpec 是 Remotion 的私有数据镜像，"AI 导演"实际是确定性启发式而非模型推理。

---

## 1. 仓库物理结构

```text
anime-edit-studio/
├── anime/              42 个 Python 模块，7565 行 —— 全部业务逻辑
├── renderer/           Remotion (React/TS) 合成器 + render-shots.mjs 渲染驱动
├── review-web/         Vite + React 审片前端（单文件 review-app.tsx）
├── library/            全局素材库（git 忽略）：engine.sqlite / proxies / keyframes / cache
├── projects/<id>/      每作品一目录：beatmap.json / editspec.*.json / outputs/
├── kit/                签名资产：luts(2) / sfx(4) / bgm(3) / models(LAION 美学头)
├── docs/               7 篇设计说明
├── scripts/            5 个一次性脚本（4 py + 1 sh）
├── tests/              3 个测试文件，769 行
├── refs/               竞品参考
├── config.toml         工具路径 + 交付规格 + 评分权重
└── pyproject.toml      CLI 包定义（入口 `anime`）
```

**代码量分布**（Python，行）：

| 模块 | 行数 | 职责 |
|---|---|---|
| decision_loop.py | 1274 | 评分 / brief / 审片 / 缺口 / 蓝图 / 偏好学习（**上帝模块**） |
| cli.py | 843 | Typer 入口，63 个命令 + 10 个子命令组 |
| director.py | 694 | slot 分类 + 能量驱动情绪弧装配 |
| db.py | 468 | SQLite schema + 7 个迁移 |
| experiment.py | 419 | Hook A/B + 发布数据回灌 |
| quality_gate.py | 281 | 结构审计 + 增强 A/B 门禁 |
| sound.py | 249 | SFX 合成 + 音床 |
| render.py / reference.py | 210 / 210 | Remotion 渲染桥 / 参考片节奏分析 |
| library.py | 205 | 素材库入库与磁盘回收 |
| editspec.py | 197 | **EditSpec v1 数据契约** |
| 其余 32 个模块 | ~1700 | ingest/shots/analyze/embed/search/beat/roughcut/qa/master/... |

---

## 2. 现有分层（事实上的，非设计上的）

```
CLI (typer)  ──┐
Review API ────┼──> decision_loop.py ──> db.py (SQLite)
Review Web  ───┘         │
                         ├──> director.py ──> editspec.py ──> render.py ──> render-shots.mjs
                         │                                                      │
                         └──> reference.py / beat.py                        Remotion
                                                                                │
                                                          finalize / sound / master / qa (ffmpeg)
```

关键事实：**没有明确的层边界**。`decision_loop.py` 同时承担评分、数据访问、
EditSpec 构造、Remotion 预览渲染调用（`_render_preview`）与偏好模型训练。

---

## 3. 数据模型（SQLite，`library/engine.sqlite`）

迁移框架成熟：`schema_migrations` 版本表 + 7 个幂等迁移函数（`db.MIGRATIONS`）。

| 表 | 用途 |
|---|---|
| `assets` | id / path / sha256 / width / height / fps / duration / codec / proxy_path |
| `shots` | 见下方详表 |
| `shots_fts` | FTS5 虚表：character / action / emotion / dialogue / tags |
| `review_decisions` | project+shot 唯一；decision ∈ use/alternate/reject，rating 1-5，trim in/out，preferred_role |
| `creative_briefs` | 角色/主题/情绪/时长/画幅/平台/结构 JSON/参考片路径/creative_contract_json |
| `shot_scores` | 13 个维度 + final_score + score_version + explanation_json |
| `source_records` | 素材来源与授权记录（provenance，不再作为导出门禁） |
| `cut_variants` | 蓝图变体：editspec_path / preview_path / score / selected |
| `preference_models` | scope 唯一；model_type / version / features_json / model_json |
| `project_assets` | 项目与素材的绑定关系 |
| `growth_experiments` / `growth_variants` | Hook A/B 实验与发布指标（views/retention/completion） |
| `shot_outcomes` | 每个变体每个镜头的留存进出与跌幅 |
| `enhancement_reviews` | restore/rife/superres/matte 阶段的前后对比与人工裁决 |

### `shots` 表实际字段

```
id, asset_id, idx, start_sec, end_sec, keyframe,
brightness, sharpness, motion_dir, motion_mag,
character, action, emotion, camera, dialogue, tags,
slot, picked, embedding(BLOB), reframe_x, min_brightness,
fill_mode, aesthetic, growth_score
```

### `shot_scores` 实际 13 维

```
technical_quality, composition_quality, character_salience, emotion_intensity,
action_intensity, hook_potential, climax_potential, ending_potential,
vertical_crop_score, subtitle_risk, watermark_risk, diversity_score,
preference_score  →  final_score
```

权重来自 `config.toml [scoring.weights]`（technical 0.15 / composition 0.15 /
brief_match 0.20 / structure 0.15 / preference 0.20 / diversity 0.10 / risk_penalty 0.05）。

---

## 4. EditSpec v1（`anime/editspec.py`）

pydantic 模型，**明确声明为"Python 与 Remotion 之间的剪辑数据契约"**，
文件头注释写着"字段需与 `renderer/src/schema.ts` 保持一致"。

```python
EditSpec: id, fps=60, width, height, duration_in_frames, shots[], audio[], overlays[]

Shot: id, src, source_in_sec, start_frame, duration_in_frames, speed,
      transform{scale,x,y,rotate}, effects[{type,intensity,params}], lut, matte,
      camera_move, camera_amount, camera_from, camera_to,
      transition, transition_intensity, exit_transition, exit_intensity,
      ramp, reframe_x, fill_mode

AudioLayer: id, src, start_frame, trim_start_frames, gain_db
TextOverlay: text, sub, start_frame, duration_in_frames, style, anchor
```

真实产物样例（`projects/jjk-sukuna-arc/editspec.arc.json`）：
23 个镜头，3072×3840 @ 60fps，1401 帧，1 条 BGM 音轨，0 个 overlay。
`src` 为素材库母版绝对路径，`effects` 为 `{"type":"glow","intensity":0.2}` 形态。

**特征**：
- 全帧制（`start_frame` / `duration_in_frames`），无秒制表达
- `effects.type` 是渲染器私有字符串枚举（glow / rgbSplit / shake / vignette / flash）
- 无 shot role、无 confidence、无 reasoning、无 version 字段
- 无 color recipe（只有单条 `lut` 文件路径）
- 无 per-clip SFX、无音量自动化、无 marker、无 caption
- 无 subject tracking / 无 speed ramp 参数化（只有 `ramp` 字符串 + `speed` 平均倍率）
- 修订通过**新文件名**表达：`editspec.arc.json` → `editspec.arc.v2.json` → `.restore.rife.sr.json`

---

## 5. 渲染与后期链路

**主渲染器：Remotion**（不是 FFmpeg）。README 关于"FFmpeg 渲染"的旧描述已不准确。

`anime/render.py` 的执行方式：
1. `_stage_sources()` 把源文件硬链/复制到 `renderer/public/sources/`（Remotion `staticFile` 限制）
2. 非 preview 时调 `decision_loop.resolve_master_sources()` 从 DB 回链母版路径
3. 有真慢镜（speed<1）则先经 `slowmo.smooth_spec()` 做 RIFE 光流平滑（带缓存）
4. 写 `<stem>.staged.json`，调 `renderer/render-shots.mjs`
5. **逐镜按内容 hash 缓存渲染成段**，只重渲变化的镜头 → concat → 加 BGM
6. preview 走 0.5 缩放

FFmpeg 承担的角色（**非主渲染**）：proxy 生成、段落 concat、音床合成、
master 阶段 loudnorm + lut3d、endcard、技术 QA 探测。

后期能力（均为独立 CLI 命令，作用于 EditSpec 文件并产出新 EditSpec 文件）：
`slowmo` / `superres`(Real-ESRGAN) / `restore` / `interpolate`(RIFE) / `fps` /
`sideways` / `matte`(rembg) / `reframe` / `enhance` / `sound` / `master` / `endcard` / `qa`

---

## 6. 素材智能现状

**入库链路**（可用）：
`acquire/fetch` → `ingest`(ffprobe + sha256 + 1080p VideoToolbox 代理) →
`shots`(PySceneDetect + 关键帧 + contact sheet) → `analyze`(亮度/清晰度/运动方向 + 可选 whisper 台词) →
`embed`(open-clip ViT-B-32, MPS) → `tag`(wdtagger Danbooru v3) → `slots`(CLIP 提示词分类)

**检索**：`search.py` —— FTS5 全文 + 过滤 + 综合排序，输出带时间码候选。
`config.toml [models] embedding = "none"` 说明默认走纯文本 FTS，向量检索为可选升级项。

**美学**：`aesthetic.py` 用 LAION `sa_0_4_vit_b_32_linear.pth` 线性头，缺失则降级为清晰度启发式。

素材库现状：40 个代理文件、30 个关键帧目录，源片在
`~/Desktop/anime-material-library/sources/`（鬼灭 / 咒术 / 死神）。

---

## 7. 创意与剪辑决策现状

**Brief（导演合约）** —— `decision_loop.upsert_brief` / `validate_brief`
定义角色、主题、情绪、时长、画幅、平台，以及扩展的 creative contract
（目标观众 / 前三秒承诺 / 高潮兑现 / 结尾余味 / 视觉母题 / 声音策略 / 必须与禁止 / 成功标准）。
`brief validate` 返回 `ready:true` 才允许进入后续流程。

**Gap Analysis** —— `decision_loop.gap_analysis`：各结构段所需镜头类型 vs 当前可用数量、
高质量数量、无字幕数量、匹配率、缺失类型与建议关键词。

**Blueprint / Variant** —— `generate_blueprints` 生成多个 cut variant，
每个写出 EditSpec + Remotion 预览，`variant select` 选定。

**Director（情绪弧装配）** —— `director.py`：
- `classify_slots` 用 4 条 CLIP 提示词把镜头分入 opening / build / climax / ending
- `direct()` 由 BGM 逐拍能量驱动切点疏密（高能每拍切、低能长握）
- `_anchor_score` 追求"正脸可读 + 主体居中"的顺滑硬切
- `_apply_motion_whips` / `_apply_exit_smears` / `_apply_pushpull_hook` 分配转场与运镜
- CLIP 近重复去重

**音乐** —— `beat.py`(librosa) 产出 `beatmap.json`：
`bpm / duration / beats[] / downbeats[] / onsets[] / beat_energy[]`。
实测样例：123.05 BPM，103 拍，25 个 downbeat，217 个 onset。

**参考片** —— `reference.py::analyze_reference` 产出**节奏语法**：
切点步长、时长聚类、交替性评分、规律性评分，失败时回退场景统计。

---

## 8. 审片与反馈现状

- **后端**：`anime/review_api.py`，FastAPI，18 条路由
  （project / shots / reviews / brief / gap-analysis / blueprints / variants / experiments / quality）
- **前端**：`review-web/`，Vite + React，单文件 `review-app.tsx` + 3 个 css
- **反馈形态**：结构化点击（use / alternate / reject + rating 1-5 + trim in/out + preferred_role），
  **无自然语言反馈通道**
- **偏好学习**：`train_preference` 支持规则型与逻辑回归两种模型，
  写入 `preference_models` 表，`explain_preference` 可解释单镜头得分

---

## 9. 质量与增长现状

- `qa.py` —— 技术 QA：规格 / 黑帧 / 响度校验 → report.json
- `quality_gate.py` —— 结构审计（dhash 重复检测、视觉审计）+ 增强前后 A/B 与人工裁决
- `extreme.py` —— 能力矩阵雏形：`capabilities()` 显式列出 ready / unavailable / fallbacks
  （vapoursynth / rubberband / demucs / linear_light_compositor / tracked_video_matte / platform_private_api）
  并给出 `release_ready` 门禁
- `experiment.py` —— Hook A/B 矩阵、发布指标回灌（views / retention_2s / retention_3s / completion）、
  `shot_outcomes` 逐镜留存跌幅、`experiment learn` 回流到下一条片子

> **`extreme.capabilities()` 是全仓最接近 MASTER PLAN §27 Capability Matrix 思想的既有实现，
> 可以直接作为 `resolve_capabilities.yaml` 的设计蓝本。**

---

## 10. DaVinci Resolve 集成现状

**零。**

全仓 `.py` / `.toml` / `.md` / `.yaml` 中对 resolve / davinci / fusion 的匹配，
100% 来自 `renderer/node_modules/`（`enhanced-resolve` 等 npm 包的 README），
无一处业务代码。

宿主机环境（已实测）：

| 项 | 状态 |
|---|---|
| DaVinci Resolve | `/Applications/DaVinci Resolve`，版本 **21.0.3** |
| Scripting Modules | `/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules/DaVinciResolveScript.py` ✅ |
| fusionscript.so | `.../Contents/Libraries/Fusion/fusionscript.so` ✅ |
| 项目 Python | venv 3.11（`requires-python >=3.11`） |
| 系统 Python | 3.14.6 —— **Resolve 21 不支持，必须走 venv** |
| ffmpeg | 8.1.1 |

---

## 11. 测试与 CI

- `tests/test_decision_loop.py` 655 行（占 85%）
- `tests/test_micro_pushpull.py` 93 行
- `tests/test_reference_rhythm.py` 21 行
- **总计 769 行，覆盖 42 个模块中的约 3 个**
- CI（GitHub Actions）：ubuntu + ffmpeg + pytest；review-web `npm run build`；renderer typecheck

---

## 12. 技术债清单

| # | 债务 | 影响 |
|---|---|---|
| D1 | `decision_loop.py` 1274 行上帝模块，混合评分/数据访问/EditSpec 构造/渲染调用/模型训练 | 任何分层重构都必须先拆它 |
| D2 | EditSpec v1 是 Remotion 私有结构镜像，字段语义绑死渲染器 | 换后端 = 换 IR |
| D3 | 无 EditSpec 版本号、无 schema 校验器、无 diff、无迁移 | 修订只能整份重写文件 |
| D4 | 修订用文件名表达（`.v2` / `.restore.rife.sr`），链路不可追溯 | 无法做增量更新 |
| D5 | 测试覆盖约 7%，42 模块只测 3 个 | 重构无安全网 |
| D6 | 工作区 14 个未提交修改文件 | 迁移起点不干净 |
| D7 | 特效实现在 Remotion TSX 内（CSS/SVG/GLSL），无参数化 recipe 定义 | 迁移到 Fusion 需完全重写，无参照 |
| D8 | 无显式工作流状态机，状态隐含在"文件是否存在"中 | 无法重试/恢复 |
| D9 | README 描述与实现已漂移（称主渲染为 FFmpeg，实为 Remotion） | 文档不可信 |
| D10 | 无任何 LLM/VLM 调用，"AI 导演"实为确定性启发式 | MASTER PLAN 的语义推理层完全缺位 |
| D11 | `scripts/` 内 4 个一次性脚本绕过 CLI 直接改 EditSpec | 破坏单一入口 |
| D12 | 交付画布硬编码 3072×3840 @60fps | Resolve 时间线性能风险 |

---

## 13. 现有能力资产盘点（迁移时须保护）

高价值、与 MASTER PLAN 方向一致、**不应推倒**：

1. SQLite 迁移框架（版本化、幂等）—— 直接可承载 Asset/Shot 模型扩展
2. Ingest → SceneDetect → Keyframe → Proxy 全链路 —— MASTER PLAN §10 已实现约 60%
3. CLIP 嵌入 + wdtagger 打标 + LAION 美学头 —— Asset Intelligence 的既有底座
4. `beat.py` librosa 音乐分析 —— MusicMap 的一半
5. `reference.py` 节奏语法分析 —— StyleFingerprint 的起点
6. 13 维评分 + 可配置权重 + `explanation_json` —— Ranking 的骨架，且已避开"单一美学分"
7. 偏好学习（规则 + 逻辑回归 + 可解释）—— Preference Memory 的雏形
8. FastAPI + React 审片闭环 —— Review UI 已有真实后端
9. `qa.py` 技术 QA —— 与 MASTER PLAN §75 高度吻合
10. `extreme.capabilities()` —— Capability Matrix 的设计范本
11. **`experiment.py` 增长实验闭环** —— MASTER PLAN 中完全没提，但直接服务变现目标，属于本仓独有资产
12. 逐镜内容 hash 缓存渲染 —— 增量渲染思想已验证，可移植到 Resolve 增量更新
