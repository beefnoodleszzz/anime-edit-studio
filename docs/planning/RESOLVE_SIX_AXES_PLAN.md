# Resolve 六大增强方向 —— 差距分析与落地计划

> 分支：`feature/resolve-six-axes` · 创建于 2026-07-28
> 上游依据：owner 战略备忘录（Edit 节奏 / Fusion 分层 / Color 匹配 / Fairlight 声音 / Retime 变速 / 自动化）。
> 本文把备忘录映射到 v2 现有架构，**先说清哪些已经有了，再定义真正要补的能力**。
> 权威级别低于 `WANT.md` / `TARGET_ARCHITECTURE.md` / `MIGRATION_PLAN.md`；冲突时以它们为准。
> 所有新能力遵守 AGENTS.md 全部规则：capability 未 verified 不进 EditSpec（R3）、
> Recipe 必须有验收物（R4）、确定性逻辑不交给 LLM（R6）。

---

## 交付状态（2026-07-28 更新）

W1–W4 的**确定性代码层已全部落地并测试通过**（新增 37 项测试，全套 237 通过）。
需要实机 Resolve 探测 / GUI 搭 comp / 人工验收的部分，按 R3/R4 保持 unverified，
这是规则下的正确完成态，不是遗漏。

| 工作项 | 已交付（可测试/已测试） | 仍 gated（需实机/人工） |
|---|---|---|
| **W1 Action Sync** | action-peak 检测（10Hz 光流，migration 010）、speed-ramp 解算器（复用 compiler 源时曲线）、apply_action_sync 后处理 pass、`cut_accuracy_report.json` 验收表 | 无——全部走已 verified 的 timespeed 通道 |
| **W2 Sound Design** | 三件套 SoundDesign 生成器（budget/silence 约束）、per-clip at_sec 映射、`evaluate_sound_design` QA、first-cut 接线 | sfx Recipe 已 verified；无新 gated 项 |
| **W3 Color Shot-Match** | 逐镜测量、bounded 校正解算、.cube LUT 烘焙、`evaluate_color_match` QA | `per_clip_color_match` 逐镜 SetLUT 渲染对照未做 → 不进 EditSpec |
| **W4 Fusion 分层** | W4a rembg 前景遮罩分析（migration 011）、`detect_layering_opportunities` 动机检测 | `parallax_25d_v1` / `occlusion_cut_v1` 需 GUI 搭 comp + 渲染对照 → 保持 verified:false |

落地模块：`studio/editing/timing/`（W1）、`studio/editing/sound/` + `studio/critic/creative/sound.py`（W2）、
`studio/editing/color/`（W3）、`studio/execution/external_ai/subject_mask.py` + `studio/editing/sequence/layering.py`（W4）。

---

## 目的收敛（2026-07-29）

分支立项时的目标是备忘录的六个方向。用猗窝座实战之后，真正的验收目标收敛为一句：
**让系统自动剪出 a.mp4 那个水平的踩鼓点甩镜**——每个鼓点换新镜、切点踩准鼓点、
镜头之间用运动曲线连成"被拖向下一镜"的甩感。猗窝座不是产品，是试金石。

W1–W4 是骨架；此后逐项补的是骨架之外仍与 a.mp4 有差的地方（幻灯片式大长镜、
30fps 交付、光流补帧、whip 短语铺满、每镜运镜曲线）。**W5 是其中最后一块：
运镜方向的智能配向。**

### W5 · Camera Assignment —— 每镜配向（2026-07-29 交付）

上一步（camera-curve commit）打通了"能不能动"，但没人写 `clip.camera`，
first-cut 出来的 spec 全是 `move=none`，能力只在探针手灌统一 pan 时才亮。

先量后改。新增 `studio/critic/technical/camera_flow.py`：对任意成片测逐镜光流方向、
跨切点方向延续率、方向熵。**a.mp4 实测结果推翻了原设计假设**：

| 指标 | a.mp4 | b.mp4 | 统一 pan |
|---|---|---|---|
| carry_rate（方向跨切点延续） | 0.175 | 0.241 | 1.000 |
| reversal_rate（跨切点反向） | 0.450 | 0.138 | 0.000 |
| direction_entropy（8 向归一化熵） | 0.909 | 0.546 | 0.000 |
| directional_shot_frac | 0.829 | 0.633 | 1.000 |
| median_magnitude | 1.647 | 0.848 | — |

a.mp4 的"拖拽感"**不是**每镜同向滑动堆出来的：它只有 17.5% 的切点延续方向，
45% 直接反向，方向近乎均匀铺满八个象限。真正的来源是**每镜运动都强、方向变化大、
在切点上硬翻**。所以配向规则是"顺素材自身实测运动而动"，同向承接只在素材本身
无方向可骑时才由 CutRelation 决定——**恰好与直觉相反**。

落地：`studio/editing/camera.py::assign_camera_moves`，接在 first-cut 的
Action Sync 之后（这样能看见哪些 clip 的 Fusion 槽位已被 effect/ramp/transition 占用，
跳过而不是写一个渲染时静默失效的假值），产出 `camera_assignment.json`。
`CameraMove.move` 补 `pan_up` / `pan_down`（Transform 纵轴分支本已支持，缺的是 IR 枚举；
素材中约 19% 主导运动是上下向）。

验收门 `compare_camera_flow` 对 entropy / reversal / carry / directional_frac / magnitude
五项与 a.mp4 基线比对，**统一 pan 会在其中三项同时判负**——这正是这道门存在的理由。
基线落盘 `docs/probes/camera_flow_reference_a.json`（+ `_b.json`）。

猗窝座 33 镜实跑：全部配到位，方向分布 pan_right 10 / pan_up 8 / pan_left 6 /
pan_down 5 / push_in 4，18 镜顺素材实测方向、8 镜承接、3 镜反向、4 镜推镜。

仍 gated：成片渲染后的 `measure_camera_flow` 对照需实机 Resolve 出片，未做不得声称达标。

---

## 0. 结论先行

| 备忘录方向 | v2 现状 | 差距定性 | 优先级 |
|---|---|---|---|
| 一、节奏系统（音乐结构 + 切点） | **大部分已建成** | 缺 Action Sync（动作峰值对拍） | S |
| 五、Retime 变速 | **已建成核心**（timespeed_recipe verified，retime mapping probes 刚完成） | 缺动作相位驱动的分段变速 | S（与方向一合并） |
| 四、Fairlight 声音设计 | 仅有 prebake 通道，**无声音设计层** | 全新子系统 | S |
| 二、Fusion 人物分层 / 遮挡转场 | **几乎空白**（Magic Mask 实测不可用，subject_tracking unverified） | 全新 Recipe 族 + 自研主体检测前置 | S/A |
| 三、Color 镜头匹配 | 有 color_recipe（整体 Look），**无逐镜匹配** | 新增确定性 shot-match 层 | A |
| 六、自动化素材系统 | **这就是本仓库本身** | 无新方向，仅补验收报告 | 持续 |

已覆盖的部分不重做。本计划只立项四件事：
**W1 Action Sync**、**W2 Sound Design 层**、**W3 Shot Match 调色层**、**W4 Fusion 分层 Recipe 族**。

---

## 1. 方向一 + 五：节奏系统与 Retime —— 现状与 W1

### 已建成（不重做）

- 音乐结构：`MusicMap`（bpm/beats/downbeats/sections/impact_points/risers/silences）+
  `studio/editing/music/segments.py` 的可剪段落打分（beat_clarity / phrase_clarity / dynamic_arc）。
- 鼓点锁切：strict drum-locked 工作流（Timeline Marker 权威、每 marker 一次真实换源、
  ≤1 帧误差验收表）已是产品制度。
- 变速：`timespeed_recipe`（verified）+ `motion_phrase_compositor`（verified）+
  工程级 `imageRetimeInterpolation='opticalFlow'`（verified）。
- 切分语法：`CutRelation` / `SourceSelection`（anticipation/action/impact/reaction/settle 相位）
  + `EditGrammarQA`。

### 差距：Cut Sync 已达标，Action Sync 未达标

`SourceSelection` 目前只有 shot-level 语义估计的相位锚点，没有帧级动作峰值测量。
所以切点准（Cut Sync），但「刀落下恰好在鼓点」（Action Sync）只能靠估。

### W1 · Action Sync（动作峰值对拍）

1. **测量**：`asset_intelligence/motion/` 新增确定性 action-peak 检测 ——
   对候选源区间按 10 Hz 光流幅值序列（与 StyleFingerprint 同基准）求局部峰值，
   输出 `action_peaks: [{sec, magnitude, confidence}]`，写入 shots 分析并入缓存版本。
   不使用 LLM；VLM 语义标注（这是"挥刀"还是"抬眼"）后置，非本期。
2. **对齐**：Sequence Planner 在 `SourceSelection` 已有相位估计之上，
   若存在测量峰值，则解算 `retime` 分段速度使峰值落到目标 marker
   （准备段加速、命中段 hold/freeze、恢复段快出），全部经既有
   `timespeed_recipe` 路径编译，SourceTime 归一化守既有约束。
3. **验收**：鼓点验收表扩一列 `action_peak_error_frames`；无测量峰值的 clip
   显式标 `estimated`，禁止伪称帧级命中（沿用既有规则）。

不新增 Resolve 能力探测；纯分析层 + Planner + 既有 verified 通道。

---

## 2. 方向四：Fairlight 声音设计 —— W2（回报最高）

### 现状

- `fairlight_automation` verified: **false**（公开 API 无音量自动化）。
- 可用的 verified 通道：`sound_recipe_prebake`（离线合成 wav 后整轨挂入）+
  `audio_track_management`。EditSpec schema 已预留 `audio.sfx` / `volume_automation` 结构。
- `kit/sfx` 有素材但未 Recipe 化（无 recipe.yaml / ACCEPTANCE.md → AI 不得使用）。

### W2 · Sound Design 层

1. **Sound Recipe 化 kit/sfx**：按 R4 为 impact_low / whoosh / riser / sub_impact 等
   建 `execution/recipes/sound/<id>/{recipe.yaml, asset.wav, preview.mp4, ACCEPTANCE.md}`。
   人工验收，不可跳过。
2. **SoundPlan 生成器**（`studio/editing/` 新模块，确定性规则 + 预算约束）：
   对每个目标 marker 生成三件套 —— marker 前 4–8 帧 riser/reverse-whoosh、
   marker 帧 kick+impact、marker 后短衰减尾。受 DirectorPlan `impact_budget`
   约束（防"每个动作都机械加音效"），并避开 MusicMap `silences`。
3. **执行**：SoundPlan → 离线混合为分轨 wav（音量包络在离线阶段烘焙，
   绕过不可用的 fairlight_automation）→ `sound_recipe_prebake` 挂入
   A2–A5 轨。轨道语义沿 EditSpec `tracks.role`。
4. **QA**：Technical QA 增加声轨对齐检查（每个 marker ±1 帧内存在 impact 能量峰）；
   Rhythm QA 报告 SFX 密度 vs 风格档案。
5. **验收物**：一条 12–15s 试产片，"闭眼听"验收由 owner 完成并记录。

---

## 3. 方向三：Color 镜头匹配 —— W3

### 现状

- `color_recipe` verified（整体 Look 经 LUT/ColorGroup 通道）；
  但**没有逐镜匹配**：不同集数/场景素材黑位、白平衡、饱和度不一致的问题未处理。
- `export_lut` 与 ColorGroup 细分路径 unverified。

### W3 · Shot Match 层（两段式：先测量，后执行）

1. **测量（纯 FFmpeg/numpy，确定性）**：对入选 clip 的源区间统计
   黑位 / 白位 / 平均色温偏移 / 饱和度 / 对比度，存入分析缓存（带版本）。
2. **匹配方案生成**：以 DirectorPlan 指定的"锚定镜头"或角色主色规范为基准，
   为每个 clip 算出有界校正参数（lift/gain/offset/sat，参数域写入 recipe schema）。
3. **执行路径探测（必须实测，不得推断）**：优先探测
   `TimelineItem` 级 CDL/`SetCDL` 或 per-clip LUT 应用；渲染对照（md5 + 直方图）
   证明生效后才转 verified。若 per-clip 通道全部不可用，回退方案是
   测量结果生成 per-clip `.cube` 并走已 verified 的 LUT 挂载路径。
4. **顺序**：校正节点在前、整体 Look（既有 color_recipe）在后，
   对应备忘录 Node01–03 → Node06 的结构。
5. **QA**：新增确定性 ColorMatch QA —— 相邻 clip 黑位/饱和度跳变超阈值即报告。

---

## 4. 方向二：Fusion 分层 / 遮挡转场 / 2.5D —— W4

### 现状与硬约束

- `CreateMagicMask` / `SmartReframe` 实测不可用（架构决策 A11/A12：自研主体检测）。
- `subject_tracking` / `camera_move_recipe` unverified。
- 已 verified 的地基：`add_fusion_comp`、`motion_phrase_compositor`（TimeStretcher +
  Transform + DirectionalBlur 单 comp 合成）、`transition`（两侧桥接模式）。

### W4 · 分层 Recipe 族（依赖自研主体检测，分三步走）

1. **W4a 主体检测前置**（`external_ai/`）：逐帧主体 mask（rembg/分割模型），
   输出带版本缓存的 mask 序列 + 主体 bbox 轨迹。这是 2.5D 与遮挡转场的共同前置，
   属于 Phase 3 既定方向（A12），不是新架构。
2. **W4b `parallax_25d_v1` Recipe**：背景层慢速 + 人物层快速的双层 Transform，
   mask 由 W4a 提供，封装为单 Fusion comp（守 motion_phrase_compositor 的
   "多 comp 仅为版本"教训）。验收：视差可见、无边缘撕裂、时长帧数不变。
3. **W4c `occlusion_cut_v1` Recipe**：主体前景经过画面时完成换源的两侧桥接
   （复用 transition 的 out/in 协同模式，不用 CreateFusionClip）。
   仅当 CutRelation 证据支持时由 Planner 采用，禁止按参考峰值滥铺（沿用 MotionPhrase 准入规则）。
4. **特效跟随（刀光/雷电贴合 bbox 轨迹）后置**：依赖 W4a 轨迹质量验证后再立项。

每个 Recipe 从 GUI 制作 → Export → 代码 Import + 注参（A13 路径），
渲染对照验证"局部变化 + 远处帧不受影响"后才转 verified。

---

## 5. 方向六：自动化 —— 无新立项

备忘录描述的"素材生产系统"（自动切分 → 识别 → 标签 → 鼓点表 → 自动 Marker →
候选入线 → 人工审核）就是 v2 的 Layer 1–6 + Review UI 本身。仅补一项：

- **切点误差报告产品化**：把鼓点验收表（含 W1 的 action_peak_error）作为
  first-cut 的标准输出制品 `cut_accuracy_report.json`，Review UI 可读。

---

## 6. 执行顺序与门禁

```
W1 Action Sync ──────────────┐（纯分析+Planner，无新 Resolve 探测，先做）
W2 Sound Design ─────────────┤（verified 通道已备，Recipe 验收是人工瓶颈，尽早启动）
W3 Shot Match  ── 探测→实现 ──┤（执行路径需实测，探测失败有 LUT 回退）
W4a 主体检测 ──> W4b/W4c ────┘（最长链路，W4a 先行）
```

- 每项立项前回答 AGENTS.md §3 十问；每项完成按 §4 DoD 收口。
- 所有新能力探测结果写回 `config/resolve_capabilities.yaml`，probed ≠ verified。
- 每个 W 交付一条 owner 实看/实听验收的试产片段，自动证据不替代人看。
