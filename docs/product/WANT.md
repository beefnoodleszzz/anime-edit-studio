# Anime Edit Studio — AI 原生动漫短视频生产系统总体改造规范

> 文档级别：最高优先级项目规范  
> 适用对象：Codex、Claude Code、GPT、项目开发 Agent、代码审查 Agent  
> 项目定位：AI 驱动、用户轻审核、DaVinci Resolve 专业执行的动漫剪辑代理系统  
> 状态：Architecture Migration / Major Refactor

---

# 0. 本文档的权威级别

本文档定义 Anime Edit Studio 后续开发的：

- 产品定位
- 总体架构
- 技术边界
- 数据流
- Agent 职责
- EditSpec 设计
- DaVinci Resolve 集成原则
- 素材智能系统
- AI 导演系统
- 剪辑决策系统
- Fusion / 调色 / 音频自动化策略
- 用户审核机制
- 反馈学习机制
- 渲染与 QA
- 项目迁移顺序
- 开发优先级
- 验收标准

后续所有功能设计和代码实现均应优先遵循本文档。

如果现有代码结构与本文档冲突：

> 应优先逐步迁移现有架构，而不是为了兼容旧设计而破坏新架构。

禁止因为历史代码已经存在，就继续强化已经被废弃的产品方向。

---

# 1. 产品重新定义

Anime Edit Studio 不再被定义为：

> 一个自动生成 EditSpec，然后通过 FFmpeg 自行渲染视频的命令行自动剪辑器。

新的产品定义为：

> Anime Edit Studio 是一个以 AI 导演为核心、以结构化 EditSpec 为中间语言、以 DaVinci Resolve / Fusion 为专业后期执行后端，并通过少量用户创意审核持续学习用户审美偏好的动漫短视频生产系统。

进一步理解：

Anime Edit Studio 不是单纯的“自动剪辑软件”。

它应该被设计成：

> 一个 AI 剪辑团队。

系统内部应逐渐形成多个专业角色：

- 素材管理员 AI
- 素材理解 AI
- 选片 AI
- Creative Director AI
- Sequence Editor AI
- VFX AI
- Color AI
- Sound Design AI
- Critic AI
- Technical QA AI

DaVinci Resolve 不再被视为用户需要学习和操作的剪辑软件。

它的定位变为：

> AI 可以控制的专业后期执行引擎。

用户原则上不直接操作 Resolve。

---

# 2. 最终用户体验

最终目标工作流如下。

用户只需要：

1. 提供素材。
2. 提供题材或角色。
3. 提供音乐。
4. 可选提供参考视频。
5. 描述想要的大致感觉。
6. 从少量候选镜头中进行选择。
7. 查看 AI 第一版。
8. 用自然语言反馈。
9. 确认最终成片。

例如：

用户输入：

> 用这些鬼灭之刃素材做一支 25 秒混剪。
> 风格参考 a.mp4。
> 炭治郎为主。
> 要帅、干净、速度感强。
> Drop 的时候必须有强烈冲击。

系统应自动完成：

素材检索
→ 镜头理解
→ 候选选片
→ 参考片分析
→ 音乐分析
→ 镜头编排
→ EditSpec
→ Resolve Timeline
→ 构图
→ 变速
→ 转场
→ Fusion
→ 调色
→ Sound Design
→ Fairlight
→ Preview Render
→ AI Critic
→ 用户审核
→ 自动 Revision
→ Master Render
→ Technical QA
→ Delivery

用户不应该：

- 手动导入 Resolve
- 手动拖时间线
- 手动切片
- 手动调 Fusion Node
- 手动调 Color Node
- 手动设置渲染参数
- 学习 Resolve 工作流

---

# 3. 核心原则

整个系统必须遵循以下原则。

## 3.1 AI 决策与执行必须分离

禁止：

AI
→ 直接胡乱操作 Resolve
→ 根据当前 UI 状态继续猜下一步

必须：

User Intent
→ Director Plan
→ EditSpec
→ Execution Engine
→ Resolve
→ Preview
→ Critic
→ Revision

AI 首先生成明确的结构化决策。

然后执行层负责执行。

---

# 4. EditSpec 不得废弃

EditSpec 不再是最终产品。

但是：

> EditSpec 必须成为整个系统最重要的 Intermediate Representation（IR）。

类似于：

源代码
→ AST / IR
→ Compiler
→ Machine Code

Anime Edit Studio 应该是：

User Intent
→ DirectorPlan
→ EditSpec
→ Resolve Compiler
→ DaVinci Resolve

因此：

Resolve 不应该成为项目状态的唯一真相来源。

真正的 Source of Truth 应该是：

- Asset Database
- DirectorPlan
- EditSpec
- User Feedback
- Project Metadata

Resolve 是执行结果。

---

# 5. EditSpec 必须具备的能力

EditSpec 至少需要表达：

- source asset
- source in
- source out
- timeline in
- timeline duration
- track
- shot role
- crop
- reframing
- subject tracking
- retiming
- speed ramp
- transition
- VFX
- color profile
- sound design
- volume automation
- markers
- captions
- metadata
- confidence
- reasoning metadata
- version

示例：

```json
{
  "id": "clip_018",
  "asset_id": "kimetsu_ep19",
  "shot_id": "shot_01832",

  "source": {
    "in": 142.31,
    "out": 143.82
  },

  "timeline": {
    "in": 8.4,
    "track": "V1"
  },

  "role": "impact",

  "crop": {
    "mode": "portrait_subject_track",
    "subject": "tanjiro"
  },

  "retime": {
    "type": "speed_ramp",
    "entry_speed": 1.0,
    "impact_speed": 0.35,
    "exit_speed": 1.4
  },

  "transition": {
    "in": "hard_cut",
    "out": "flash_impact"
  },

  "effects": [
    {
      "recipe": "impact_shake_v3",
      "strength": 0.72
    },
    {
      "recipe": "anime_glow_v2",
      "strength": 0.35
    }
  ],

  "color": {
    "recipe": "anime_fire_contrast_v2"
  },

  "audio": {
    "sfx": [
      "sword_whoosh",
      "impact_low"
    ]
  }
}
6. EditSpec 的设计要求

EditSpec 必须：

可序列化。
可验证。
可版本控制。
可 Diff。
可重新执行。
可部分修改。
可生成 Resolve 工程。
可生成 Preview。
理论上可支持多个 Renderer。

未来：

EditSpec
├── Resolve Renderer
├── FFmpeg Preview Renderer
├── Premiere Renderer
└── Custom Renderer

因此：

禁止将 EditSpec 设计成 Resolve 私有数据结构的简单镜像。

7. 系统总体架构

目标架构：

┌─────────────────────────────────────────┐
│             Anime Edit Studio           │
│              Review UI                  │
└────────────────────┬────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────┐
│          Creative Intelligence          │
│                                         │
│ Intent Understanding                    │
│ Reference Analysis                      │
│ Personal Preference                     │
│ Creative Director                       │
└────────────────────┬────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────┐
│            Asset Intelligence           │
│                                         │
│ Shot Detection                          │
│ Visual Embeddings                       │
│ Character                               │
│ Action                                  │
│ Motion                                  │
│ Composition                             │
│ Quality                                 │
│ Subtitle                                │
│ Audio                                   │
└────────────────────┬────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────┐
│           Editing Intelligence          │
│                                         │
│ Retrieval                               │
│ Ranking                                 │
│ Candidate Generation                    │
│ Sequence Planning                       │
│ Timing                                  │
│ Beat Alignment                          │
└────────────────────┬────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────┐
│                 EditSpec                │
│             Stable Editing IR           │
└────────────────────┬────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────┐
│            Execution Engine             │
│                                         │
│ Resolve API                             │
│ Fusion API                              │
│ Recipes / Templates                     │
│ FFmpeg                                  │
│ External AI                             │
│ GUI Fallback                            │
└────────────────────┬────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────┐
│                Preview                  │
└────────────────────┬────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────┐
│             Critic / QA                 │
│                                         │
│ Creative QA                             │
│ Visual QA                               │
│ Audio QA                                │
│ Technical QA                            │
└────────────────────┬────────────────────┘
                     │
              Revision Loop
8. 系统必须拆分成六大领域
Layer 1 — Asset Intelligence

负责回答：

我拥有什么素材？

Layer 2 — Creative Intelligence

负责回答：

这次作品应该是什么感觉？

Layer 3 — Editing Intelligence

负责回答：

应该使用哪些镜头，以及怎样组合？

Layer 4 — EditSpec

负责回答：

最终剪辑决策具体是什么？

Layer 5 — Execution Engine

负责：

精确执行 EditSpec。

Layer 6 — Critic / QA

负责：

判断结果是否符合创意与技术要求。

9. Asset Intelligence

这是整个项目最重要的基础设施之一。

禁止每次创建项目都重新理解整部动漫。

素材第一次进入系统时进行一次完整分析。

之后：

所有项目优先查询 Asset Database。

10. 素材入库流程
Anime Episode
     │
     ▼
Technical Probe
     │
     ▼
Scene Detection
     │
     ▼
Shot Detection
     │
     ├── Keyframes
     ├── Proxy
     ├── Thumbnail
     └── Contact Sheet
     │
     ▼
Multimodal Analysis
11. 每个 Shot 至少保存以下信息
shot_id
asset_id

start_time
end_time
duration

characters
character_confidence

action
action_confidence

shot_scale
camera_motion
subject_motion

motion_direction

motion_intensity

composition

pose_quality

face_visibility
eye_visibility

visual_energy

image_quality

blur_score

compression_score

subtitle_presence

subtitle_region

color_palette

brightness

emotion

semantic_tags

dialogue

audio_energy

music_presence

cutability

embedding
12. 不允许使用单一 aesthetic_score 决定镜头好坏

禁止建立这种简单逻辑：

shot_01 = 0.93
shot_02 = 0.89
shot_03 = 0.82

原因：

Shot Quality != Shot Suitability

一个镜头是否适合，取决于：

Shot
+
Previous Shot
+
Next Shot
+
Music Position
+
Sequence Role
+
Reference Style
+
User Preference

因此必须区分：

Intrinsic Quality

镜头本身是否高质量。

Contextual Suitability

镜头是否适合当前时间点和上下文。

13. Candidate Retrieval

第一阶段必须从数百或数千镜头中快速召回候选。

例如：

角色 = Tanjiro
动作 = Sword
Motion > 0.7
Subtitle = false
FaceQuality > 0.8

得到约：

100–300 shots

然后进行高级 Ranking。

14. Candidate Ranking

Ranking 必须考虑多维指标。

例如：

character relevance
action relevance
composition
motion
pose
face
image quality
subtitle penalty
sequence fit
music fit
reference fit
user preference
novelty
shot diversity

输出：

30–50 个真正值得审核的候选。

15. 用户不应该一次浏览 50 个无结构镜头

候选必须按 Narrative Role 分类。

例如：

Opening
A / B / C

Character Intro
A / B / C

Build-up
A / B / C

Pre-Drop
A / B / C

Impact
A / B / C

Ending
A / B / C

UI 应允许：

[选择 A]
[选择 B]
[选择 C]
[AI 决定]

目标：

用户只解决真正有审美价值的选择。

16. Creative Intelligence

必须增加独立的 Creative Director。

Creative Director 的任务不是剪时间线。

它负责理解：

用户意图
题材
主角
情绪
音乐
参考片
平台
时长
目标受众
节奏要求
强度曲线

然后输出：

DirectorPlan。

17. DirectorPlan

示例：

duration: 25

primary_character:
  - tanjiro

tone:
  - aggressive
  - cinematic
  - clean

structure:

  opening:
    start: 0
    end: 3
    energy: low

  buildup:
    start: 3
    end: 8
    energy: medium

  drop:
    start: 8
    end: 15
    energy: very_high

  release:
    start: 15
    end: 21
    energy: high

  ending:
    start: 21
    end: 25
    energy: medium

visual_rules:

  prefer:
    - sword
    - eye_closeup
    - fire
    - dynamic_pose

  avoid:
    - static_dialogue
    - subtitles
    - bad_composition

editing_rules:

  opening:
    average_shot_length: 1.5

  drop:
    average_shot_length: 0.45
18. Reference Video Intelligence

“像 a.mp4”必须成为核心功能。

禁止只提取：

BPM
LUT
转场数量

系统必须尝试生成：

Style Fingerprint

19. Style Fingerprint

至少包括：

duration

shot_count

shot_length_distribution

mean_shot_length

median_shot_length

cut_density

hard_cut_ratio

transition_types

beat_sync_ratio

music_structure

energy_curve

shot_scale_sequence

motion_direction_sequence

speed_ramp_locations

slow_motion_locations

camera_motion

color_progression

brightness_curve

sound_effect_density

impact_points

silence_usage

visual_rhyme

motion_rhyme

示例：

Opening:
CU → ECU → Medium

Build:
slow → medium → fast

Drop:
3-frame silence
→ impact
→ extreme close-up
→ action
→ wide explosion

Motion:
L→R
L→R
L→R
Drop:
R→L reversal

Color:
cold
→ neutral
→ warm red

系统应该学习：

参考片的剪辑语法。

不是简单复制效果。

20. Editing Intelligence

必须独立于 Creative Director。

Director 决定：

要表达什么。

Editor 决定：

哪些镜头在什么时间出现。

21. Sequence Planner

Sequence Planner 必须综合：

DirectorPlan
StyleFingerprint
MusicStructure
CandidateShots
UserPreference

输出：

EditSpec Draft。

22. 音乐分析

音乐必须至少分析：

BPM
beats
bars
sections
drops
riser
break
silence
energy
onset
spectral changes

生成：

MusicMap。

示例：

{
  "bpm": 158,

  "sections": [
    {
      "type": "intro",
      "start": 0,
      "end": 3.2
    },
    {
      "type": "build",
      "start": 3.2,
      "end": 8.1
    },
    {
      "type": "drop",
      "start": 8.1,
      "end": 16.4
    }
  ],

  "impact_points": [
    8.12,
    9.02,
    10.44
  ]
}
23. Sequence Planning 不等于 Beat Cut

禁止：

每个 Beat
→ 换一个 Shot

必须同时考虑：

Musical Phrase
Visual Phrase
Action Continuity
Motion Direction
Character Continuity
Emotional Progression
Visual Energy
Shot Length Variation
24. Resolve 的正确定位

DaVinci Resolve 是：

Professional Execution Backend

不是：

系统的大脑。

AI 的决策必须在 Resolve 之外完成。

Resolve 负责：

Project
Media Pool
Bin
Timeline
Tracks
Clips
Reframe
Retime
Fusion
Color
Fairlight
Render
Export
25. Resolve Execution Engine

执行优先级必须固定：

1. Official Resolve API

2. Fusion Python / Lua API

3. Template / Macro / Preset

4. Project / Timeline interchange

5. GUI Automation

GUI Automation 必须是最后 fallback。

禁止把鼠标坐标脚本设计成核心能力。

26. Resolve Adapter

必须建立独立模块：

resolve/

推荐：

resolve/

  connection.py

  project.py

  media_pool.py

  timeline.py

  clips.py

  markers.py

  render.py

  fusion.py

  color.py

  fairlight.py

  recipes.py

  capability.py

上层代码不允许直接散落调用 Resolve API。

必须通过统一：

ResolveAdapter

进行访问。

27. Capability Matrix

必须维护：

resolve_capabilities.yaml

记录：

create_project
import_media
create_bin
create_timeline
append_clip
set_clip_position
set_marker
render

fusion_comp
fusion_node

color_recipe

speed_ramp

transition

fairlight

etc.

每个能力标记：

native_api
fusion_api
template
interchange
gui_fallback
unsupported

这样 AI 不需要猜。

28. Fusion 不允许完全由 AI 临时生成

必须建立：

Effect Recipe Library

例如：

impact_shake_v1
impact_shake_v2
impact_shake_v3

anime_glow_v1

rgb_split_impact

speed_flash

eye_focus

character_intro

motion_blur_transition

white_flash

camera_punch

AI 的职责：

选择 Recipe + 参数。

而不是：

每次重新设计 Node Graph。

29. Effect Recipe

示例：

id: impact_shake_v3

engine: fusion

parameters:

  strength:
    type: float
    min: 0
    max: 1

  duration:
    type: integer
    min: 3
    max: 20

  motion_blur:
    type: float
    min: 0
    max: 1

AI 输出：

{
  "recipe": "impact_shake_v3",
  "strength": 0.72,
  "duration": 8,
  "motion_blur": 0.32
}
30. Color 必须 Recipe 化

禁止让 LLM 每次随意调整：

Lift
Gamma
Gain
Saturation

必须建立 Color Recipe。

例如：

anime_clean_v1

anime_high_contrast_v2

anime_cold_v3

anime_fire_v2

anime_night_blue_v1

red_impact_v2

soft_flashback_v1

每个 Recipe 背后对应经过验证的 Color Node Graph。

AI：

理解镜头
→ 选择 Recipe
→ 微调有限参数

31. Sound Design 必须是一等公民

不要把声音作为最后补充。

系统必须拥有：

Sound Intelligence + Sound Recipe。

SFX 分类：

whoosh

sword

impact

sub_impact

riser

glitch

explosion

transition

bass_hit

reverse

ambient

texture
32. 动作与声音映射

例如：

拔刀
→ sword_draw
→ metal
→ whoosh

挥刀
→ directional_whoosh

命中
→ impact

爆炸
→ explosion
→ low_frequency_hit

Drop
→ sub_impact

快速转场
→ whoosh

但必须避免：

每一个动作都机械加音效。

应根据 Sound Density 和音乐结构决定。

33. Preview First

系统必须优先生成：

Low Cost Review Preview

在用户 Lock Picture 之前禁止执行不必要的高成本处理。

例如：

Preview：

proxy
720p / 1080p
low-cost effects
temporary grade
temporary sound
fast render

Lock Picture 后：

full resolution
heavy denoise
super resolution
high quality optical flow
final Fusion
final grade
final Fairlight
master render

核心原则：

重处理尽量只执行一次。

34. Critic Agent

生成第一版之后不得直接交付。

必须经过：

Critic。

Critic 至少检查：

Creative
是否符合 DirectorPlan
是否符合参考片风格
是否有明显无聊镜头
Drop 是否足够有冲击
Opening 是否能吸引注意
Editing
是否节奏过满
是否存在重复镜头
是否存在动作方向冲突
是否存在不自然跳切
是否存在不合理镜头长度
Visual
Crop 是否切脸
主体是否出框
字幕是否残留
Blur 是否严重
Fusion 是否过度
Audio
SFX 是否过多
Impact 是否对齐
音乐是否 clipping
音量是否异常
Technical
Resolution
FPS
Duration
Codec
Audio
Black Frames
Frozen Frames
Missing Media
35. Revision Loop

必须支持：

Draft V1
↓
Critic
↓
Automatic Fix
↓
Preview V2
↓
User Feedback
↓
Revision Plan
↓
EditSpec Diff
↓
Selective Resolve Update
↓
Preview V3

禁止每次修改都重新生成整个工程。

36. 用户反馈必须转换成结构化 Revision

例如用户说：

7 秒那里不够帅，镜头太普通。

AI 应转换：

{
  "type": "replace_shot",

  "timeline_region": {
    "start": 6.6,
    "end": 7.8
  },

  "requirements": {
    "visual_energy": "> current",
    "pose_quality": "high",
    "motion": "high",
    "character": "tanjiro"
  }
}
37. 用户审美学习

禁止第一阶段直接 Fine-tune 大模型。

首先建立：

Preference Memory。

每次用户选择都保存：

selected
rejected
context
sequence_role
music_position
alternative_shots
38. Pairwise Preference

最重要的数据不是：

用户喜欢 shot_01。

而是：

在 A 和 B 中用户选择了 A。

记录：

{
  "winner": "shot_A",
  "loser": "shot_B",
  "context": "drop",
  "project_style": "high_energy"
}

未来可以训练：

Preference Ranker。

39. Personal Preference Profile

系统逐渐维护：

Close Up           +0.18

Eye Visibility     +0.22

Sword              +0.31

Fast Motion        +0.27

Dynamic Pose       +0.35

Static Dialogue    -0.40

Subtitle           -0.51

Wide Shot          -0.10

这些只能作为 Ranking Signal。

不能变成绝对规则。

40. 用户审核 UI

用户不应该审核 Resolve Timeline。

必须建立独立 Review UI。

核心页面：

Project

Reference

Candidates

First Cut

Revision

Final
41. Candidate Review

建议：

Impact Shot

[A] Preview
[B] Preview
[C] Preview

[Choose A]
[Choose B]
[Choose C]

[AI Decide]
42. Preview Review

用户只需要：

播放视频

然后输入：

这里不够帅

第二段太快

开头太拖

Drop 再狠一点

这个角色镜头换掉

结尾不好看

AI 自动解析。

43. Human-in-the-loop 原则

项目不追求：

100% AI autonomous.

目标：

95% automation + 5% high-value human judgment

应该自动化：

ingest
shot detection
indexing
retrieval
ranking
project creation
timeline creation
rough cut
retime
effects
sound
color
rendering
QA

用户保留：

A / B 判断
创意偏好
情绪判断
最终确认
44. 项目核心 KPI

不要只看：

“AI 有没有生成视频”。

必须统计：

Candidate Precision

用户接受的候选比例。

First Cut Survival Rate

AI 第一版镜头最终保留比例。

成熟目标：

60%–80%

但不得通过过度保守剪辑作弊。

Sequence Preservation

镜头顺序最终保留比例。

Timing Delta

用户最终修改的镜头时长比例。

Revision Count

达到 Lock Picture 需要多少轮。

Time To First Preview

素材已完成预分析后：

目标：

30–60 分钟以内。

Manual Intervention

用户手工操作次数。

Resolve Manual Operations

目标：

0。

Human Effort / Finished Video

最终最重要指标。

例如：

30 秒成片

人工审核时间：
4m12s

候选选择：
14 次

文字反馈：
2 条

Resolve 操作：
0
45. 时间指标的前提

“30–60 分钟生成第一版”只适用于：

素材已经完成 Ingest 和 Asset Intelligence。

已入库意味着至少：

Proxy                 DONE
Shot Detection        DONE
Keyframe Extraction   DONE
Embedding             DONE
Character Index       DONE
Motion Analysis       DONE
Audio Analysis        DONE
Technical Metadata    DONE

禁止把完整素材初次分析时间计入正常剪辑 SLA。

46. 推荐项目目录
anime-edit-studio/

├── AGENTS.md
├── README.md
├── pyproject.toml
│
├── config/
│
│   ├── app.yaml
│   ├── models.yaml
│   ├── resolve_capabilities.yaml
│   └── recipes.yaml
│
├── assets/
│
│   ├── source/
│   ├── proxy/
│   ├── keyframes/
│   ├── thumbnails/
│   └── cache/
│
├── asset_intelligence/
│
│   ├── ingest/
│   ├── shot_detection/
│   ├── visual/
│   ├── character/
│   ├── motion/
│   ├── audio/
│   ├── embeddings/
│   └── indexing/
│
├── creative/
│
│   ├── intent/
│   ├── reference/
│   ├── director/
│   └── preference/
│
├── editing/
│
│   ├── retrieval/
│   ├── ranking/
│   ├── candidates/
│   ├── sequence/
│   ├── timing/
│   └── music/
│
├── editspec/
│
│   ├── schema/
│   ├── validator/
│   ├── migrations/
│   └── diff/
│
├── execution/
│
│   ├── resolve/
│   ├── fusion/
│   ├── color/
│   ├── audio/
│   ├── recipes/
│   ├── ffmpeg/
│   └── gui_fallback/
│
├── critic/
│
│   ├── creative/
│   ├── visual/
│   ├── audio/
│   └── technical/
│
├── review/
│
│   ├── backend/
│   └── frontend/
│
├── projects/
│
│   └── <project_id>/
│
│       ├── project.yaml
│       ├── director_plan.yaml
│       ├── style_fingerprint.json
│       ├── music_map.json
│       ├── candidates.json
│       ├── editspec.json
│       ├── revisions/
│       ├── previews/
│       └── output/
│
└── tests/
47. AI Agent 不应该直接拥有底层实现细节

必须逐步抽象为 Tool。

例如：

analyze_asset()

search_shots()

rank_shots()

analyze_reference()

analyze_music()

create_director_plan()

create_edit_spec()

validate_edit_spec()

create_resolve_project()

sync_timeline()

apply_effect_recipe()

apply_color_recipe()

render_preview()

run_qa()

未来 Codex / Claude / GPT 都可以调用同一套工具。

48. Agent 必须可替换

禁止：

Anime Edit Studio = Claude

或者：

Anime Edit Studio = Codex

正确：

              Anime Edit Studio
                      │
                   Agent
               ┌──────┴──────┐
               │             │
             Codex         Claude
               │             │
               └──────┬──────┘
                      │
                     Tools

真正的系统资产是：

Asset DB
EditSpec
Recipes
Resolve Adapter
Ranking
Director
Preference Data

而不是具体某一个 LLM。

49. FFmpeg 的新定位

现有 FFmpeg Renderer 不应删除。

但必须降级为：

Preview Renderer

Fallback Renderer

Technical Tool

Media Processing Tool

Proxy Generator

Frame Extractor

QA Tool

不再承担：

整个产品最终专业后期执行引擎。

正式 Master 优先：

DaVinci Resolve。

50. External AI 的定位

可以使用专门 AI 处理：

Upscaling
Denoising
Frame Interpolation
Subtitle Removal
Inpainting
Segmentation
Optical Flow
Speech
Music Analysis

但是：

重型模型只在确实需要时执行。

避免所有镜头默认执行昂贵模型。

51. 缓存原则

任何昂贵分析必须：

Cacheable。

包括：

embedding
character detection
motion
shot boundaries
keyframes
OCR
audio features
reference analysis

Cache Key 至少考虑：

asset hash
model
model version
pipeline version
parameters
52. 可重复性

任何 Project 必须能够：

Project Metadata
+
DirectorPlan
+
EditSpec
+
Recipe Versions
+
Asset IDs

↓

重新构建

避免 Resolve Project 成为无法解释的黑箱。

53. Versioning

至少需要：

EditSpec Version

DirectorPlan Version

Recipe Version

Asset Analysis Version

Model Version

Project Version
54. 每次 Revision 应优先使用 Diff

例如：

V1：

clip17 = shot_A

用户：

换掉这一镜头。

V2：

clip17 = shot_B

系统应只更新：

clip17。

不要重新构建整个 Timeline。

55. Failure Recovery

系统必须考虑：

Resolve 未启动

Resolve 无响应

媒体丢失

API 不支持

模型失败

GPU OOM

Render Failed

Fusion Failed

Proxy Missing

Asset Moved

每一个 Workflow Step 都必须：

可重试
可恢复
可记录状态
56. Workflow State Machine

建议：

CREATED

↓

INGESTING

↓

ANALYZED

↓

DIRECTING

↓

CANDIDATES_READY

↓

USER_SELECTION

↓

EDIT_PLANNING

↓

RESOLVE_BUILD

↓

PREVIEW_RENDER

↓

AI_REVIEW

↓

USER_REVIEW

↓

REVISION

↓

LOCKED

↓

MASTER_RENDER

↓

FINAL_QA

↓

DELIVERED

禁止依赖隐式状态。

57. 数据库建议

至少抽象实体：

Asset

Shot

Character

Project

Reference

MusicTrack

Candidate

DirectorPlan

EditSpec

EditVersion

UserFeedback

Preference

Recipe

Render

QAResult
58. Phase 1 开发目标

不要立刻做全部系统。

第一阶段只验证：

AI 是否真的能稳定把剪辑决策送进 Resolve。

必须完成：

Resolve Connection。
创建 Project。
创建 Bin。
Import Media。
创建 Timeline。
根据 EditSpec 放置 Clip。
设置 source in/out。
Preview Render。
EditSpec → Resolve 可重复执行。
简单 Revision。

Phase 1 不需要复杂 AI。

59. Phase 1 成功标准

给出：

[
  {
    "source": "A.mp4",
    "in": 10,
    "out": 12,
    "timeline": 0
  },
  {
    "source": "B.mp4",
    "in": 30,
    "out": 32,
    "timeline": 2
  }
]

系统能自动：

启动/连接 Resolve
→ 创建项目
→ 导入素材
→ 创建 Timeline
→ 放置正确片段
→ Render Preview

即为成功。

60. Phase 2

目标：

Asset Intelligence MVP。

实现：

Ingest

Scene Detection

Shot Detection

Keyframes

Proxy

Basic Visual Embedding

Motion Score

Subtitle Detection

Image Quality

Search

目标：

一集动漫进入系统以后：

可以通过条件检索 Shot。

61. Phase 3

Candidate Engine。

实现：

Retrieval

Multi-signal Ranking

Candidate Groups

Contact Sheet

Video Preview

Human Selection

此阶段重点验证：

AI 能否把几百个镜头压缩成几十个真正值得看的镜头。

62. Phase 4

Reference Intelligence。

实现：

Shot Timing Analysis

Music Structure

Cut Pattern

Energy Curve

Motion Pattern

StyleFingerprint
63. Phase 5

Director + Sequence Planner。

输入：

User Intent
Reference
Music
Candidates

输出：

DirectorPlan
EditSpec
64. Phase 6

Resolve Professional Execution。

逐步增加：

Reframe

Tracking

Retime

Transition

Fusion Recipe

Color Recipe

Sound Recipe

Fairlight

Render
65. Phase 7

Critic + Revision。

完成：

AI Review

Structured Issues

Automatic EditSpec Revision

Selective Resolve Update
66. Phase 8

Preference Learning。

记录：

Selection

Rejection

Replacement

Timing Change

Effect Feedback

Final Survival

形成用户个性化 Ranking。

67. Phase 9

Review UI。

用户最终不需要 CLI。

建立：

Anime Edit Studio UI。

核心交互：

Create Project

Upload / Select Assets

Reference

Music

Candidate Review

Preview

Feedback

Lock Picture

Final
68. 开发优先级

最高：

EditSpec v2
Resolve Adapter
Resolve Compiler
Asset DB
Shot Model
Candidate Retrieval

第二：

Ranking
MusicMap
DirectorPlan
Sequence Planner

第三：

Fusion Recipes
Color Recipes
Sound Recipes

第四：

Critic
Preference Learning
Review UI
69. 当前阶段禁止投入大量时间的项目

暂时禁止过度投入：

自研复杂 FFmpeg 转场
自研完整 GPU Renderer
大量一次性视觉效果
复杂 Web UI
Fine-tune 大模型
100% Autonomous Agent
鼠标自动点击 Resolve
过早优化分布式计算
过早优化云部署

首先证明：

AI → EditSpec → Resolve → Preview

这条主链稳定工作。

70. 工程原则

所有开发必须：

typed
modular
testable
observable
versioned
recoverable

禁止：

巨型脚本
巨型 Prompt
隐式全局状态
到处 subprocess
到处直接调用 Resolve API
JSON 字段随意变化
无 Schema 数据
无版本缓存
无日志执行
71. Prompt 不得成为业务逻辑

例如：

不要把所有规则写成：

你是一名专业剪辑师……

然后希望 LLM 自动正确。

应该：

代码负责：

constraint
validation
timing
schema
data
capability
execution

LLM 负责：

semantic reasoning
creative decisions
ranking
style understanding
revision interpretation
72. AI 输出必须结构化

涉及执行的 Agent 输出必须优先：

JSON / YAML / Typed Object

避免：

自然语言
→ 正则解析
→ 执行

73. Deterministic Core

以下逻辑优先确定性实现：

Timecode

Frame Conversion

Timeline Position

Media Lookup

Cache

Render Settings

Asset Hash

Version

Validation

Technical QA

不要交给 LLM。

74. AI 应处理的问题

LLM / VLM 应重点处理：

哪个镜头更帅？

这个镜头适不适合 Drop？

两个镜头视觉上是否连续？

用户所谓“不够帅”可能是什么意思？

参考片的节奏语言是什么？

哪种镜头组合更有效？
75. 技术 QA 必须自动化

最终 Render 必须检查：

File Exists

Duration

Resolution

FPS

Codec

Audio Track

Loudness

Black Frames

Freeze Frames

Missing Frames

Corruption

Unexpected Silence

Aspect Ratio

失败：

不得标记 DELIVERED。

76. Creative QA 与 Technical QA 必须分离

Technical QA：

确定性。

Creative QA：

AI 判断。

不得混为一个 score。

77. 项目最终目标

最终用户体验应该接近：

用户：

用这些素材按照 reference.mp4 的感觉，
做一个 25 秒炭治郎高燃混剪。

Anime Edit Studio：

正在分析已有素材索引……

找到 186 个相关镜头。

已筛选 34 个高质量候选。

已生成 5 个关键位置的 A/B/C 候选。

用户：

选择其中几个。

系统：

正在生成 V1……

DirectorPlan complete
EditSpec complete
Resolve timeline complete
Preview complete
QA passed

用户播放：

第 8 秒还是不够炸。
结尾那个镜头换掉。

系统：

Revision understood.

Replacing impact shot.
Increasing pre-drop tension.
Replacing final shot.

Rendering V2.

用户：

可以。

系统：

Picture locked.

Running:
high quality retime
final Fusion
color
sound
Fairlight
master render
technical QA

DELIVERED

整个过程中：

用户 Resolve 操作次数：

0

78. 项目真正的竞争壁垒

不是：

Resolve Automation。

不是：

FFmpeg。

不是：

一个大模型。

真正壁垒应逐渐形成于：

1. Anime Asset Intelligence

对动漫镜头的理解能力。

2. Shot Ranking

什么镜头真正具有表现力。

3. Sequence Intelligence

如何组合镜头。

4. Reference Style Understanding

如何理解优秀作品的剪辑语言。

5. Editing Recipes

积累的 VFX / Color / Sound 专业经验。

6. Personal Preference

对用户个人审美的理解。

这些才是长期资产。

79. AI 开发 Agent 的工作方式

任何接手本项目的 AI Agent：

不要立刻修改代码。

第一步必须：

STEP 1

完整扫描 repository。

输出：

Current Architecture

Existing Modules

Current EditSpec

Renderer

Asset Pipeline

Resolve Integration

Tests

Technical Debt

Reusable Components

Deprecated Components
80. 第二步

制作：

docs/architecture/ARCHITECTURE_GAP_ANALYSIS.md

对照本文档逐项判断：

Already Exists

Partially Exists

Missing

Must Refactor

Must Remove

Can Reuse
81. 第三步

生成：

docs/planning/MIGRATION_PLAN.md

迁移必须：

Incremental。

禁止一次性推倒整个项目。

每一步必须保持系统：

可运行。

82. 第四步

优先创建新的 Core Contracts：

Asset

Shot

DirectorPlan

EditSpec v2

ResolveAdapter

Recipe

Revision

QAResult

先稳定接口。

再迁移实现。

83. 第五步

建立：

Resolve Proof of Concept。

在没有复杂 AI 的情况下：

EditSpec
→ Resolve
→ Preview

必须先跑通。

84. 第六步

将现有 FFmpeg Renderer 降级为：

Preview / Fallback。

不要删除可复用能力。

85. 第七步

逐渐迁移 Asset Pipeline。

所有素材分析结果必须进入统一 Asset / Shot 数据模型。

86. 第八步

实现 Candidate Engine。

这是 AI 能否提高用户效率的第一个关键验证。

87. 第九步

实现 Director / Sequence Planner。

不要在 Candidate Engine 尚不稳定时过早构建复杂 Agent。

88. 第十步

再逐步增加：

Fusion
Color
Sound
Critic
Preference
UI

89. 每次开发必须回答

新增任何 Feature 之前，AI 必须回答：

它属于哪一层？
输入是什么？
输出是什么？
是否进入 EditSpec？
是否需要 Resolve？
是否应该 Recipe 化？
是否能够 Cache？
是否可测试？
是否影响 Determinism？
是否真的降低用户人工操作？

如果无法回答：

不要实现。

90. Definition of Done

一个功能不应该因为：

“代码写完了”

而 Done。

必须：

Schema defined

Implementation complete

Tests complete

Logging complete

Error handling complete

Version compatibility considered

Documentation complete

End-to-end path verified
91. 第一阶段最终验收

第一阶段项目改造完成时，应能：

用户给出：

3–20 个视频素材
+
一个简单 EditSpec

系统自动：

Resolve connection

Project creation

Media import

Bin organization

Timeline creation

Clip placement

Preview rendering

Output verification

用户：

不操作 Resolve。

92. 中期验收

系统应能够：

Anime Library
↓
Automatic Shot Index
↓
Natural Language Search
↓
Candidate Ranking
↓
Candidate Review
↓
AI Sequence
↓
Resolve Preview
↓
Natural Language Revision
93. 最终验收

用户能够：

“按照 reference.mp4 的感觉，用这些素材做 25 秒视频。”

然后只经过：

少量候选选择
+
1–2 轮自然语言反馈

获得专业级可发布作品。

94. 最终原则

记住：

Anime Edit Studio 的目标不是：

替代 DaVinci Resolve。

而是：

替代用户操作 DaVinci Resolve 的过程。

DaVinci Resolve 应成为：

专业执行引擎。

Anime Edit Studio 应成为：

智能导演与自动化控制层。

EditSpec 应成为：

两者之间稳定的编辑语言。

95. 最重要的架构链

整个开发过程中永远保护这条链：

Asset Intelligence
        ↓
Creative Director
        ↓
Candidate Retrieval
        ↓
Sequence Planner
        ↓
EditSpec
        ↓
Resolve Execution
        ↓
Preview
        ↓
Critic
        ↓
Human Feedback
        ↓
Revision

任何功能如果不能明确地放入这条链中：

需要重新评估是否值得开发。

96. 给 AI 开发 Agent 的直接命令

从现在开始，你作为本项目的 Principal Engineer / AI Systems Architect。

你的任务不是在现有代码上继续堆功能。

你的任务是按照本文档将 Anime Edit Studio 从：

“自动生成 EditSpec + FFmpeg 渲染的 CLI 剪辑器”

逐步改造为：

“AI 驱动、用户轻审核、EditSpec 作为中间语言、DaVinci Resolve / Fusion 作为专业执行引擎的动漫剪辑代理系统”。

执行规则：

首先完整审计现有 repository。
不要立即大规模修改代码。
输出现状架构。
输出 Gap Analysis。
标记可复用模块。
标记需要废弃的架构。
设计目标模块边界。
给出分阶段 Migration Plan。
第一优先级验证 EditSpec → Resolve → Preview。
所有改造保持 Incremental。
每个阶段必须可运行。
所有核心数据必须 Schema 化。
所有 Resolve 操作必须经过 ResolveAdapter。
GUI Automation 只能作为 fallback。
不要过早实现复杂特效。
不要过早 Fine-tune。
不要过早开发完整 UI。
不要为了旧代码兼容而破坏新架构。
不允许用巨大 Prompt 代替系统设计。
每完成一个阶段都重新运行测试并更新迁移文档。

第一项任务：

扫描整个项目，理解目前所有目录、模块、数据模型、EditSpec、Renderer、CLI、测试、素材分析能力和已有 DaVinci 相关代码。

然后创建：

v1-final tag
docs/architecture/ARCHITECTURE_GAP_ANALYSIS.md
docs/architecture/TARGET_ARCHITECTURE.md
docs/planning/MIGRATION_PLAN.md

暂时不要进行大规模重构。

等待架构分析完成以后，再从 Phase 1 开始实施。


### 实际使用方法

我建议你把它直接放到项目根目录：

```text
anime-edit-studio/
├── AGENTS.md
├── ANIME_EDIT_STUDIO_MASTER_PLAN.md   ← 上面这份
├── README.md
├── src/
└── ...

然后第一次给 Codex / Claude Code 的命令不要写一大堆，直接：

请首先完整阅读：

1. AGENTS.md
2. ANIME_EDIT_STUDIO_MASTER_PLAN.md

把 MASTER PLAN 视为当前项目最高级别架构规范。

现在不要直接开始重构。

先完整扫描当前 repository，分析已有架构与 MASTER PLAN 的差距。

生成：

v1-final tag
docs/architecture/ARCHITECTURE_GAP_ANALYSIS.md
docs/architecture/TARGET_ARCHITECTURE.md
docs/planning/MIGRATION_PLAN.md

必须说明：

- 哪些旧代码应该保留
- 哪些需要重构
- 哪些应降级为 fallback
- 哪些需要废弃
- EditSpec 当前结构与 v2 目标的差距
- DaVinci Resolve 接入点
- Phase 1 最小可验证闭环
- 每个 Phase 的依赖关系
- 风险和技术阻塞项

完成架构分析后停止，不要擅自开始大规模修改代码。

这里最重要的一点就是最后一句：

先让 AI 审计项目，不要一拿到这份文档就让 Codex 把整个仓库推倒重写。

正确顺序应该严格保持：

现状审计 → Gap Analysis → Target Architecture → Migration Plan → Resolve PoC → 渐进式迁移。

这样你后面无论换 Codex、Claude Code，还是两者交叉开发，整个项目的“方向盘”都不会再掌握在某一个 AI 的临时判断里。