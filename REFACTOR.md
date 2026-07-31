# Anime Edit Studio 全面重构执行指令

仓库：

```text
https://github.com/beefnoodleszzz/anime-edit-studio.git
```

你是本项目的首席架构师、Python 工程师、计算机视觉工程师、音乐信息检索工程师和 DaVinci Resolve/Fusion 自动化工程师。

这不是一次方案讨论，也不是增量补丁任务。

你的任务是直接修改仓库，将其重构为：

> 输入 Demo 视频、目标音乐和大量动漫素材，系统自动分析 Demo 的剪辑语言，根据目标音乐重新计算节奏结构，自动选择素材，在 DaVinci Resolve 中生成具有连续音乐律动和跨镜头后期运镜的 AMV。

必须完成实际代码、测试、清理、CLI 和本机可执行闭环。

不要只输出设计文档，不要留下空接口，不要用 TODO 代替实现。

---

# 0. 最重要的原则

## 0.1 最终不兼容旧系统

最终代码不保留：

* 旧 EditSpec 兼容；
* 旧数据库运行时兼容；
* 旧 CLI 兼容；
* 旧 Review UI；
* 旧 Recipe Review；
* 旧项目恢复接口；
* 旧 Candidate A/B/C 人工工作流；
* 旧 Growth/Preference 工作流。

但不得采用：

```text
先删除所有旧实现
→ 再从零重写
```

这种高风险方式。

正确顺序是：

```text
建立新主链
→ 新主链测试通过
→ Resolve 实渲通过
→ 切换唯一入口
→ 删除旧主链
→ 删除兼容代码
```

暂时保留旧代码用于对照，不等于最终保留兼容层。

---

## 0.2 不破坏有效底层资产

下列能力已经积累了大量 Resolve 真机经验，优先复用其有效逻辑：

* `studio/core/timecode.py`；
* 素材哈希与缓存；
* 素材 ingest、proxy、shot detection；
* 素材数据库中的 assets、shots、embeddings；
* Resolve 连接逻辑；
* ResolveAdapter 中工程、时间线、媒体导入、帧率换算、放置和渲染能力；
* Fusion BezierSpline 写入；
* Transform Native Motion Blur；
* FFmpeg 探测、抽帧、音频分析和技术 QA；
* 时间线全量重建策略。

必须删除的是旧产品逻辑，不是经过验证的底层工程知识。

---

## 0.3 不允许项目特化补丁

生产代码中禁止出现：

* 固定 Demo 时间点数组；
* 固定 shot ID；
* 固定动漫名称；
* 固定角色名称；
* 针对祢豆子、善逸、猗窝座、炼狱、炭治郎等人物的分支；
* `tone == 某项目词` 才改变关键算法；
* 固定 18 秒；
* 固定 3–5 秒 Hook；
* 固定每拍切镜；
* 固定 30fps；
* 固定 1:1；
* 固定四镜 MotionPhrase；
* 固定左右交替；
* 固定 Zoom Punch；
* 为某个失败成片增加特殊 `if`。

项目差异只能来自：

```text
输入文件
用户参数
分析结果
结构化配置
```

不能来自修改业务代码。

---

## 0.4 不得虚假验收

以下情况不能声称“完成”：

* 单元测试通过，但未执行 Resolve；
* Resolve API 返回成功，但未检查输出视频；
* Technical QA 通过，但视觉律动明显不成立；
* Fusion Composition 存在，但关键曲线没有实际生效；
* 输出文件存在，但仍是旧主链生成；
* 自动指标接近 Demo，但人工观看明显不接近。

如果当前环境无法运行 Resolve，必须明确报告：

```text
Resolve 真机验收未执行，不能宣称完整完成。
```

---

# 1. 开始前先建立事实基线

先执行：

```bash
git status --short
git branch --show-current
git log -10 --oneline
python --version
pytest -q
```

记录：

* 当前 commit；
* 当前测试通过/失败数量；
* 当前未提交修改；
* Python 版本；
* Resolve 是否运行；
* 当前数据库路径和大小；
* 当前素材、shots、embeddings 数量。

不得覆盖用户未提交修改。

创建分支：

```text
refactor/demo-driven-amv-engine
```

数据库破坏性修改前，创建：

```text
library/backups/engine-<timestamp>.sqlite
```

并验证备份可以打开。

禁止删除：

* 外部动漫源文件；
* `library/proxies/`；
* 已生成关键帧；
* 有效 embeddings；
* 用户上传的 Demo 或音乐。

---

# 2. 先阅读真实主链

至少阅读并理解以下文件后再修改：

```text
README.md
AGENTS.md
pyproject.toml
config/resolve_capabilities.yaml

studio/workflows/first_cut.py
studio/creative/director/plan.py
studio/creative/reference/fingerprint.py
studio/creative/reference/style_profile.py
studio/editing/music/map.py
studio/editing/music/motion.py
studio/editing/sequence/planner.py
studio/editing/sequence/recipe_plan.py
studio/editspec/schema/spec.py
studio/execution/compiler.py
studio/execution/resolve/adapter.py
studio/execution/resolve/fusion.py
studio/critic/creative/motion.py
```

先生成一份内部依赖清单，回答：

* 哪些模块是纯底层能力；
* 哪些模块包含产品决策；
* 哪些模块包含项目特化；
* 哪些模块生成 Fusion；
* 哪些模块会删除已有 Fusion Composition；
* 哪些数据库表只服务旧 UI/偏好/增长系统；
* 哪些测试是真实能力测试；
* 哪些测试只保护旧产品行为。

不要把这份清单扩展成大型文档，只用于指导改造。

---

# 3. 最终产品契约

系统只保留一个主要用例：

```text
Demo
+ 目标音乐
+ 大量动漫素材
+ 可选创作约束
→ 自动 AMV Preview
→ QA
→ 用户确认
→ Release
```

输入：

```text
demo: 必填
materials: 必填
music: 可选；缺省时使用 Demo 音频
focus: 可选人物或主体
series_scope: 可选作品范围
duration: auto 或明确秒数
aspect: auto 或明确比例
fps: auto 或明确帧率
seed_images: 可选人物参考图
```

输出目录严格限制为：

```text
projects/<project-id>/
├── project.json
├── reference_blueprint.json
├── music_timeline.json
├── amv_spec.json
├── preview.mov
├── qa.json
└── release.mov
```

`release.mov` 只有用户显式执行 release 后才允许存在。

重复运行必须覆盖：

* `amv_spec.json`；
* `preview.mov`；
* `qa.json`。

不得生成：

```text
r1
r2
v3
final-final
staged
restore
candidate-groups
contact-sheet
recipe-review
preview-2
```

运行历史可以存数据库或日志，但不能污染用户输出目录。

---

# 4. 新主链架构

最终核心数据流：

```text
素材索引
    ↓
ReferenceAnalyzer
    ↓
ReferenceBlueprint

目标音乐
    ↓
MusicAnalyzer
    ↓
MusicTimeline

ReferenceBlueprint + MusicTimeline
    ↓
RhythmStyleMapper
    ↓
TimelineSlots + MotionGrammar

素材数据库 + TimelineSlots
    ↓
GlobalSequencePlanner
    ↓
AMVSpec

AMVSpec
    ↓
ResolveCompiler
    ↓
单 Clip 单 Fusion 图
    ↓
测试区间 Render

测试区间
    ↓
RenderedQA + ParameterOptimizer
    ↓
完整 Preview
```

建议最终目录：

```text
studio/
├── core/
├── asset_intelligence/
├── analysis/
├── planning/
├── spec/
├── execution/
│   └── resolve/
├── qa/
├── workflows/
└── cli.py
```

目录名称可以根据现有代码适度调整。

不要为了满足目录图而搬动完全无关、已经职责清晰的底层文件。

最终必须满足：

* 一个明确的创建 AMV 工作流；
* 一个明确的 AMVSpec；
* 一个统一 Resolve 编译入口；
* 一个统一 Fusion 图构建器；
* 一个渲染后 QA 闭环。

---

# 5. 新数据结构

新 Spec 版本：

```text
3.0.0
```

不支持旧 Spec 加载。

---

## 5.1 ReferenceBlueprint

创建严格 Pydantic Schema，`extra="forbid"`。

至少包含：

```yaml
version:
source_hash:

technical:
  width:
  height:
  fps_num:
  fps_den:
  duration_sec:
  aspect:

audio:
  music_timeline:

shots:
  - index:
    start_sec:
    end_sec:
    duration_sec:
    visual_energy:
    brightness:
    subject_scale:
    subject_center:
    native_motion_estimate:
    global_motion_estimate:
    motion_confidence:

cuts:
  - sec:
    type:
    confidence:
    nearest_music_event:
    music_offset_sec:
    outgoing_motion:
    incoming_motion:
    relation:
    visual_peak_offset_sec:
    settle_offset_sec:

motion_curve:
  - sec:
    tx:
    ty:
    log_scale:
    rotation:
    velocity:
    acceleration:
    confidence:

transition_pairs:
  - cut_sec:
    relation:
    direction:
    anticipation_sec:
    release_sec:
    outgoing_envelope:
    incoming_envelope:
    overshoot:
    blur_envelope:
    confidence:

style_summary:
  cut_density:
  shot_duration_distribution:
  music_sync_distribution:
  motion_coverage:
  hold_ratio:
  direction_distribution:
  reversal_ratio:
  scale_motion_ratio:
  blur_usage:
  visual_peak_delay_distribution:
  settle_delay_distribution:
```

注意：

压平后的 Demo 通常无法绝对区分：

```text
素材原生摄影运动
与
后期添加的虚拟运镜
```

因此不得输出伪精确结论。

输出必须包含：

```text
estimate
confidence
evidence
```

低置信度时，由 Planner 使用保守策略。

---

## 5.2 MusicTimeline

至少包含：

```yaml
version:
source_hash:
duration_sec:
tempo_candidates:
selected_tempo:
tempo_confidence:
beats:
downbeats:
bars:
onsets:
sections:
energy_curve:
spectral_novelty:
silences:
breaks:
risers:
phrases:
accents:
```

Accent 类型至少包括：

```text
beat
downbeat
impact
section_boundary
break_entry
break_exit
riser_peak
silence_hit
```

每个 Accent：

```yaml
sec:
kind:
strength:
confidence:
anticipation_sec:
release_sec:
```

不得把全部 onset 都变成视觉指令。

---

## 5.3 AMVSpec

顶层只包含实际执行需要的信息：

```yaml
version:
id:
input_hashes:
timebase:
canvas:
duration_sec:
music:
clips:
transition_pairs:
global_color:
render:
```

Clip：

```yaml
id:
asset_id:
shot_id:

source:
  in_sec:
  out_sec:

timeline:
  in_sec:
  duration_sec:
  track:

framing:
  scale:
  center_x:
  center_y:
  rotation:

retime:
  mode:
  interpolation:
  speed_keyframes:

motion:
  transform_keyframes:
  native_motion_blur_keyframes:
  directional_blur_keyframes:
  exposure_keyframes:
  optional_effect_events:
```

TransitionPair：

```yaml
id:
cut_sec:
outgoing_clip_id:
incoming_clip_id:
direction:
outgoing_keyframes:
incoming_keyframes:
blur_keyframes:
safe_scale:
overshoot:
confidence:
```

删除旧 Spec 中与当前目标无关的：

* Candidate alternatives；
* Preference metadata；
* Narrative role 固定枚举；
* Caption；
* Generic renderer；
* RecipeRef；
* Migration metadata；
* Growth metadata；
* Revision lock；
* CreatedFrom 旧链路。

---

# 6. 重写 Demo 分析

## 6.1 Cut 检测

不得只使用一个固定 PySceneDetect 阈值。

组合：

* PySceneDetect；
* HSV/灰度直方图差；
* 边缘差；
* 逐帧平均差；
* Sharpness 突变；
* 全局运动不连续；
* Flash 检测；
* Blur 峰值检测。

区分：

```text
hard_cut
flash_cut
whip_hidden_cut
dissolve
unknown
```

所有时间必须映射到准确帧号。

---

## 6.2 全局运动估计

实现至少两级方法：

一级：

```text
特征点
→ LK Flow
→ RANSAC
→ estimateAffinePartial2D
```

二级 fallback：

```text
ECC
```

提取：

* translation；
* scale；
* rotation；
* inlier ratio；
* residual motion；
* confidence。

不能只使用全帧中值 Farneback Flow 作为唯一事实。

可以保留 Farneback 用于辅助能量和局部运动估计。

---

## 6.3 Cut 两侧运动

对每个 Cut 分析至少：

```text
T-8 帧到 T+8 帧
```

判断：

* outgoing 何时开始加速；
* Cut 是否位于高速运动中；
* incoming 初始速度；
* incoming 是否延续方向；
* 是否反向；
* 是否 reset；
* incoming 何时减速；
* 是否过冲；
* 第几帧落地；
* 模糊峰值位于何处。

Relation：

```text
carry
reverse
reset
unknown
```

低置信度不得强行判断 carry。

---

## 6.4 模糊估计

结合：

* Laplacian；
* 边缘方向；
* 各向异性；
* 速度；
* Cut 附近 sharpness drop。

输出相对模糊包络。

不要试图声称恢复了原作者的精确插件参数。

---

## 6.5 音画关系

对每个：

* Cut；
* 运动启动；
* 运动速度峰值；
* Blur 峰值；
* 落地；

记录其与最近：

* Beat；
* Downbeat；
* Impact；
* Section Boundary；
* Break；

之间的偏移。

系统学习的是：

```text
Demo 对不同音乐事件如何响应
```

而不是：

```text
每个鼓点都切镜头
```

---

# 7. 不同音乐的映射

支持两个模式。

## 7.1 Exact Replica

条件：

* 未提供独立音乐；
* 或目标音乐与 Demo 音频相同。

行为：

* 使用 Demo 的真实 Cut 结构；
* 使用 Demo 的运动响应；
* 使用 Demo 的真实时长或用户显式裁剪；
* 仅替换素材和重新编译运动。

不得硬编码 Demo 时间。

---

## 7.2 Style Transfer

条件：

* 提供不同目标音乐。

不能按总时长直接线性拉伸 Demo Cut。

执行：

1. 分析 Demo 音乐和目标音乐；
2. 匹配 section；
3. 在 section 内执行单调、受约束的事件映射；
4. 映射 Cut 密度、视觉响应延迟和运动语法；
5. 实际事件时间由目标音乐决定。

允许使用：

```text
section-aware constrained DTW
```

但必须具备置信度和 fallback。

如果段落匹配置信度低：

* 不强行扭曲 Demo 时间轴；
* 使用目标音乐的 Beat/Accent 建立时间槽；
* 只迁移 Demo 的统计语法，例如：

  * Cut 响应概率；
  * 运动提前量分布；
  * 视觉峰值延迟；
  * Carry/Reverse 比例；
  * Motion/Hold 比例；
  * 景别变化模式。

换音乐只能改变分析结果和 AMVSpec。

不得修改系统代码。

---

# 8. 素材索引与自动选镜

## 8.1 保留有效素材资产

复用：

* assets；
* shots；
* proxies；
* keyframes；
* embeddings；
* motion analysis；
* visual analysis。

缺失字段通过增量分析补齐。

不要每个项目重新分析完整素材库。

---

## 8.2 Shot 特征

至少提供：

```text
asset_id
shot_id
series_scope
start_sec
end_sec
duration_sec

subject_bbox
subject_scale
subject_center
face_visibility
identity_cluster

global_motion
residual_motion
motion_direction
motion_magnitude

visual_energy
sharpness
compression
black_clip_ratio
white_clip_ratio
subtitle_probability
watermark_probability
background_complexity

color_embedding
visual_embedding
hero_frame_sec
hero_frame_score
```

技术质量不得用“平均亮度接近中灰”衡量。

暗场动漫镜头可以是高质量镜头。

---

## 8.3 身份约束

生产代码不得写具体角色分支。

用户指定 `focus` 时，依次使用：

1. 可靠身份标签；
2. 用户种子图；
3. identity embedding；
4. cluster；
5. 素材作用域。

未指定 focus 时：

* 自动寻找素材库中的稳定主体 cluster；
* 默认保持主要 identity 和作品范围一致；
* 除非显式允许混合作品。

任何相似度都不得越过作品范围硬门禁。

---

## 8.4 Slot 需求

每个时间槽包含：

```text
duration
visual_energy
subject_scale
subject_position
shot_scale
entry_motion
exit_motion
native_motion_preference
brightness
color
identity
hero_frame_requirement
```

候选评分包含：

```text
technical_quality
identity_fit
composition_fit
scale_fit
motion_fit
energy_fit
color_fit
duration_fit
hero_frame_fit
sequence_continuity
novelty
reuse_penalty
```

硬门禁失败镜头不能靠软分数救回。

---

## 8.5 全序列规划

不得逐槽位独立取第一名。

使用：

```text
Beam Search
或
动态规划
```

全局优化：

* identity 一致；
* 作品范围；
* 景别连续；
* 主体位置；
* 运动方向；
* 颜色；
* 情绪/能量；
* 重复镜头；
* 重复源区间；
* Demo Slot 适配度。

相同输入必须得到稳定结果。

随机 seed 来自：

```text
hash(demo + music + materials index + user constraints)
```

---

# 9. 统一 Motion Planner

删除旧的：

* 固定四镜分组；
* 固定 Zoom-in；
* 固定方向轮换；
* 独立逐镜 Camera Move；
* 切点后独立 Zoom Punch；
* 固定像素位移；
* 固定模糊曲线。

Motion Planner 生成连续运动事件。

每个 Cut 的 outgoing 和 incoming 必须由同一个 TransitionPair 产生。

例如向左 carry：

```text
旧镜头在 Cut 前加速向左
→ Cut 处仍处于运动
→ 新镜头从右侧进入
→ 继续向左
→ 减速
→ 轻微过冲
→ 落地
```

不能变成：

```text
旧镜头静止
→ 硬切
→ 新镜头自己放大或模糊
```

所有曲线使用逐帧 keyframe 或 BezierSpline。

---

## 9.1 安全缩放

对平移、旋转和 Blur 扩散计算覆盖范围。

基础估算：

```text
required_scale_x = 1 + 2 * abs(dx) / width
required_scale_y = 1 + 2 * abs(dy) / height
```

旋转时还要计算变换后角点。

`safe_scale` 必须包含安全余量。

逐帧检测四边：

* 不得露黑；
* 不得透明；
* 不得出现空白画布；
* 不得依赖明显的镜像接缝遮盖。

---

## 9.2 Motion Blur

优先使用经过真机验证的：

```text
Fusion Transform Native Motion Blur
```

Shutter Angle 随速度变化。

Directional Blur 只作为辅助。

不得用大量 Directional Blur 冒充画面运动。

---

# 10. 统一 FusionClipProgram

先实现新统一编译器，再删除旧 Recipe/Fusion 路径。

创建单一入口，例如：

```python
build_fusion_clip_program(
    clip_plan,
    transition_in,
    transition_out,
    canvas,
    timebase,
) -> FusionProgram
```

每个 TimelineItem 最多由本系统建立一个 Fusion Composition。

统一节点图负责：

```text
MediaIn
→ BaseTransform
→ ExposureProtection
→ OptionalTimeSpeed
→ MotionTransform
→ NativeMotionBlur
→ OptionalDirectionalBlur
→ OptionalImpactEffects
→ PostColor
→ MediaOut
```

规则：

1. Base Framing 和 Motion Transform 职责分离。
2. Camera、MotionPhrase、Retime、Transition 不得分别创建相互覆盖的 Comp。
3. 不允许通过“删除现有全部 Comp”解决冲突。
4. 节点名称必须稳定。
5. Center、Size、Angle 和 Motion Blur 必须共享时间基准。
6. 编译完成后回读节点和曲线。
7. 回读不一致立即失败。
8. 一个效果关闭后，基础运动仍然成立。

---

# 11. Recipe 系统的正确处置

不要在重构第一步直接删除所有 Recipe 代码。

执行顺序：

1. 识别现有 Recipe 中真正有用的节点构建逻辑；
2. 将其移入统一 FusionClipProgram 的内部 primitive；
3. 为新 primitive 写测试；
4. 使用 Resolve 实渲验证；
5. 新编译器达到功能对等后；
6. 删除：

   * `config/recipes.yaml`；
   * RecipeRegistry；
   * Recipe Review；
   * 每项 Recipe 的人工签字机制；
   * 独立 Comp 文件；
   * 旧 Recipe Planner；
   * 旧 Recipe 测试。

最终状态不再有 Recipe Zoo。

可以保留少数代码级 primitive：

```text
flash
rgb_split
glow
directional_blur
native_motion_blur
color_adjust
```

但它们是统一编译器内部能力，不是用户或 Planner 选择的 Recipe ID。

---

# 12. 渲染后 QA

## 12.1 技术硬门禁

检查：

* 输出存在；
* 可解码；
* 帧数；
* 时长；
* 分辨率；
* 帧率；
* 音频；
* 黑帧；
* 白帧；
* 黑边；
* 空白画布；
* 主体完全离屏；
* 异常长冻结；
* 模糊持续过长；
* 响度；
* Resolve Fusion 图是否真实存在；
* 曲线是否回读一致。

任何硬门禁失败，不得发布。

---

## 12.2 Demo 相对指标

比较 Demo 与输出：

* Cut 密度；
* Shot Duration 分布；
* Cut 到音乐事件的偏移；
* 运动启动提前量；
* 视觉峰值延迟；
* 落地延迟；
* Motion Coverage；
* Hold Ratio；
* Motion Median/P75；
* Dynamic Range；
* Carry；
* Reverse；
* Reset；
* 方向分布；
* Scale Motion；
* Blur Envelope；
* 主体面积序列；
* 主体位置序列；
* 景别变化；
* 亮度和视觉能量曲线。

不得用一个综合分数隐藏具体失败项。

输出 `qa.json` 必须展示每项：

```text
reference
actual
difference
tolerance
passed
confidence
```

---

## 12.3 自动优化

先选择 3–5 秒代表区间。

必须包含：

* 至少两个 Cut；
* 至少一个强 Accent；
* 至少一个跨镜头运动；
* 至少一个稳定落地。

优化参数只允许包括：

```text
translation_gain
zoom_gain
rotation_gain
motion_blur_gain
directional_blur_gain
anticipation_scale
release_scale
overshoot_gain
hold_motion_gain
```

最多四轮。

每轮：

```text
修改参数
→ 重建测试时间线
→ Resolve 实渲
→ QA
```

不得在优化过程中：

* 修改分析算法；
* 增加人物专用条件；
* 随意增加 Flash；
* 随意增加 Shake；
* 随意加大 Blur；
* 改动 Cut 来掩盖运镜失败。

四轮后仍失败：

1. 识别最差 Slot；
2. 更换素材；
3. 重新生成该测试区间；
4. 再检查。

不能通过继续堆特效解决。

---

# 13. 删除的产品功能

新主链通过测试后，删除以下旧产品功能及依赖。

## 13.1 删除人工 Candidate 工作流

删除：

* CandidateGroup；
* 固定 A/B/C；
* Candidate Review；
* Contact Sheet；
* Candidate Precision；
* candidate CLI；
* candidate UI/API；
* `candidate_groups` 表。

保留并重构：

* retrieval；
* ranking；
* sequence scoring。

它们成为自动 Planner 的内部模块。

---

## 13.2 删除 Preference 和 Growth

删除：

```text
studio/creative/preference/
studio/growth/
```

以及：

* Pairwise 模型；
* retention；
* Hook A/B；
* survival；
* metrics 回流；
* 对应 CLI、API、表和测试。

---

## 13.3 删除 Review UI

删除：

```text
review-web/
studio/review/
```

以及：

* FastAPI Review；
* Recipe Review；
* 六页面；
* 前端依赖；
* `aes review`。

本轮不重写新 UI。

---

## 13.4 删除旧 Revision、Diff 和 Migration

新 Spec 和新数据库闭环通过后，删除：

```text
studio/editspec/migrations/
studio/editspec/diff/
```

以及：

* 旧 Spec loader；
* 旧 database migration CLI；
* runtime compatibility；
* revision chain；
* 对应测试。

允许保留一次性离线数据导出脚本。

完成数据转换后：

* 将脚本移出运行包；
* 或删除；
* 生产代码不得调用。

---

## 13.5 删除旧硬编码工作流

删除：

```text
studio/workflows/demo_replica.py
studio/critic/creative/demo_replica.py
```

全仓检查并清除：

```text
demo_replica
MAIN_MARKERS
HERO_SHOTS
SETUP_SHOTS
HOUSE_DURATION
HOOK_RANGE
nezuko
zenitsu
akaza
rengoku
tanjiro
```

测试 fixture 文件名和测试描述允许使用虚构测试名。

生产代码不得出现真实项目角色分支。

---

# 14. 数据库重建

最终运行时只需要：

```text
assets
shots
shot_embeddings
shot_analysis
reference_blueprints
music_timelines
projects
runs
```

表名可根据规范化需要调整，但不得保留旧产品表。

迁移步骤：

1. 备份旧数据库；
2. 导出 assets、shots、embedding 等长期资产；
3. 创建全新数据库；
4. 导入长期资产；
5. 校验行数；
6. 随机抽查路径、代理、关键帧和 embedding；
7. 切换默认数据库；
8. 删除 runtime migration；
9. 删除无用旧表代码。

导入后必须证明：

```text
assets 数量一致
shots 数量一致
embedding 数量一致
proxy 路径可达率一致
keyframe 路径可达率一致
```

如果旧数据存在不一致，明确报告，不得静默跳过。

---

# 15. CLI

最终只保留必要命令：

```bash
aes doctor
aes library index <materials-dir>
aes amv create ...
aes amv release --project <id>
```

创建：

```bash
aes amv create \
  --project demo-test \
  --demo /path/demo.mp4 \
  --materials /path/materials \
  --music /path/music.wav \
  --focus optional-character \
  --duration auto \
  --aspect auto \
  --fps auto
```

行为：

```text
检查环境
→ 增量索引素材
→ 分析 Demo
→ 分析目标音乐
→ 生成 Rhythm Mapping
→ 生成 Slots
→ 全局选镜
→ 生成 AMVSpec
→ 渲染代表区间
→ 自动优化
→ 构建完整时间线
→ 渲染 preview.mov
→ 生成 qa.json
```

未传 `--music` 时，提取并使用 Demo 音频。

Release：

```bash
aes amv release --project demo-test
```

前提：

* Preview 存在；
* AMVSpec 有效；
* Hard Gates 全部通过；
* 用户显式执行。

---

# 16. 测试

## 16.1 静态架构测试

断言：

* 生产代码无具体动漫人物名称；
* 无固定 Demo 时间数组；
* 无固定 shot ID；
* 无旧 Review；
* 无 Growth；
* 无 Preference；
* 无 CandidateGroup；
* 无 RecipeRegistry；
* 无运行时 Migration；
* 只有 ResolveAdapter/connection 可以导入 Resolve Script；
* Git 未跟踪生成视频。

---

## 16.2 ReferenceAnalyzer 合成测试

测试时动态生成视频，不提交大型 fixture。

至少生成：

* 水平平移；
* 垂直平移；
* Zoom；
* 旋转；
* Hard Cut；
* Cut 前加速；
* Cut 后减速；
* Carry；
* Reverse；
* Overshoot；
* Blur Peak。

断言：

* Cut 误差在允许帧数内；
* 方向正确；
* Scale/Rotation 符号正确；
* Carry/Reverse 判断正确；
* 低纹理视频返回低置信度，而不是伪精确结果。

---

## 16.3 不同音乐测试

同一 Demo 配两段不同测试音乐。

断言：

* Cut 时间不同；
* Accent 时间不同；
* Motion 时间不同；
* Demo Style Summary 保持一致；
* 没有固定时间泄漏；
* 相同输入重复运行 Plan hash 相同。

---

## 16.4 全局选镜测试

构造：

* 好构图；
* 错景别；
* 运动冲突；
* 字幕；
* 水印；
* 低质量；
* 重复；
* 跨作品污染。

断言：

* 硬门禁失败候选永不进入成片；
* 不跨作品污染；
* 不连续重复；
* 序列整体优于逐槽贪心；
* 结果确定性。

---

## 16.5 Fusion 编译测试

Fake Resolve 断言：

* 一个 Clip 只有一个本系统 Fusion Comp；
* BaseTransform 未被 Motion 覆盖；
* Retime 与 Motion 共存；
* TransitionPair 同时控制 outgoing/incoming；
* 写入 BezierSpline；
* Native Motion Blur 已启用；
* 安全缩放足够；
* 不调用删除所有 Comp 的旧逻辑；
* 编译后完成回读。

---

## 16.6 Resolve 真机测试

标记：

```python
@pytest.mark.requires_resolve
```

至少执行：

1. 创建工程；
2. 导入两段真实素材；
3. 创建两镜时间线；
4. 创建跨 Cut Motion；
5. 同一 Clip 同时使用 framing、motion 和 blur；
6. 渲染测试窗口；
7. 检查帧数；
8. 检查黑边；
9. 检查运动方向；
10. 检查 Fusion 节点和 Spline；
11. 重复执行验证幂等。

---

# 17. 文档清理

最终只保留必要手写文档：

```text
README.md
AGENTS.md
docs/ARCHITECTURE.md
docs/OPERATIONS.md
```

但不要直接删除所有旧 Probe 证据。

先：

1. 将仍然有效的 Resolve 坑位转化为测试或简短能力说明；
2. 确认新真机测试覆盖；
3. 再删除历史 Probe 视频、Phase 报告和旧 Acceptance。

删除：

* Phase 0–9；
* V1/V2 Migration 文档；
* 完成审计；
* 旧 Requirements Matrix；
* 旧项目路径；
* Recipe 人工签字；
* 历史 Preview；
* 过期研究叙述。

`AGENTS.md` 只保留：

* 无旧兼容；
* ResolveAdapter 边界；
* 时间码唯一实现；
* 禁止硬编码；
* 禁止提交产物；
* 必须真实测试；
* 不得虚假声明能力。

---

# 18. 执行顺序

使用原子提交，顺序固定：

## Commit 1：基线与新 Schema

* 建新 Schema；
* 新数据库定义；
* 不切换主入口。

## Commit 2：Reference 和 Music 分析

* ReferenceBlueprint；
* MusicTimeline；
* 合成测试。

## Commit 3：自动 Planner

* Slot；
* Retrieval；
* Ranking；
* Global Sequence；
* Motion Planner。

## Commit 4：统一 Fusion 编译器

* FusionClipProgram；
* ResolveCompiler 接入；
* Fake Resolve 测试。

## Commit 5：QA 和测试区间优化

* Rendered QA；
* Optimizer；
* 测试区间闭环。

## Commit 6：切换 CLI

* `aes amv create`；
* Preview；
* QA；
* Release。

## Commit 7：真实 Resolve 验收

* 真机测试；
* 输出检查；
* 修复。

## Commit 8：删除旧系统

* Review；
* Candidate A/B/C；
* Preference；
* Growth；
* Recipe Zoo；
* Migration；
* Demo 硬编码；
* 旧文档。

## Commit 9：最终清理

* 未使用依赖；
* 死代码；
* 旧 import；
* 文档；
* 全套测试。

如果某个阶段失败，先修复，不要继续叠加后续改造。

不需要向用户逐阶段请求批准。

---

# 19. 最终验收命令

必须实际运行：

```bash
pytest -q
python -m compileall studio
git grep -nE \
'demo_replica|HERO_SHOTS|MAIN_MARKERS|HOUSE_DURATION|HOOK_RANGE'

git grep -nE \
'candidate_groups|preference_pairs|growth_metrics|RecipeRegistry'

git grep -nEi \
'kamado_nezuko|agatsuma_zenitsu|akaza|rengoku|tanjiro'

git status --short
```

生产代码中上述 grep 应无结果。

测试描述或测试数据如有匹配，必须人工确认不是业务硬编码。

运行完整 fixture：

```bash
aes amv create \
  --project test-amv \
  --demo tests/runtime/reference.mp4 \
  --materials tests/runtime/materials \
  --music tests/runtime/target.wav \
  --duration auto
```

检查输出目录只包含允许文件。

有 Resolve 时必须继续运行真机 E2E。

---

# 20. 完成标准

必须全部满足：

* 新 ReferenceBlueprint 可用；
* 新 MusicTimeline 可用；
* Exact Replica 可用；
* 不同音乐 Style Transfer 可用；
* 自动选镜可用；
* 全序列规划可用；
* TransitionPair 可用；
* 单 Clip 单 Fusion 图可用；
* Retime、Transform 和 Blur 不互相覆盖；
* 测试区间自动优化可用；
* Preview 可真实生成；
* QA 可真实分析；
* Release 由显式命令生成；
* 旧 Candidate UI 已删除；
* Preference/Growth 已删除；
* Recipe Zoo 已删除；
* Migration/Compat 已删除；
* Demo/角色硬编码已删除；
* 旧文档已删除；
* 数据资产未丢失；
* 单元测试通过；
* Resolve 真机测试已如实报告。

“代码大部分完成”不算完成。

“技术架构完成但无法出 Preview”不算完成。

“Preview 能出但运镜仍在每个新镜头独立启动”不算完成。

“QA 通过但没有与音乐形成跨镜头运动”不算完成。

---

# 21. 最终回复格式

最终只报告以下内容。

## 基线

* 起始 commit；
* 起始测试结果。

## 已删除

列出目录和旧能力。

## 已保留

列出复用的底层能力。

## 已实现

列出：

* Reference；
* Music；
* Planner；
* Fusion；
* Resolve；
* QA；
* CLI。

## 数据迁移

列出迁移前后：

* assets；
* shots；
* embeddings；
* proxy 可达率；
* keyframe 可达率。

## 测试

列出实际执行命令和结果。

## Resolve 真机验收

只能写：

```text
已实际执行并通过
```

或：

```text
当前环境未执行，不能宣称通过
```

## 可运行命令

给出真实命令。

## 剩余限制

只列仍然存在、已经验证的限制。

不要写新的宏大计划，不要宣称未来能力，不要用“理论上”冒充实际完成。
