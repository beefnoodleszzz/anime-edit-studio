# AMV Flow Edit 学习资料第一阶段研究报告

日期：2026-07-29  
输入任务书：`/Users/zhangxiaolong/Desktop/learn.md`  
状态：第一阶段完成；这不是最终教程，也不代表全部视频正文已经看完。

## 1. 研究边界与可信度

本轮先完成四件事：

1. 检查资料来源是否存在、当前环境能否访问；
2. 为可识别来源建立内容提纲；
3. 提炼不同来源反复出现的共同原则；
4. 标出冲突、证据缺口和下一阶段必须验证的问题。

可信度标签：

- **A：正文可读取**，可以引用其明确观点；
- **B：页面、标题和简介可读取，但视频正文受限**，只能用于建立待学习主题，不能据此虚构参数；
- **C：链接存在但正文当前不可读取**，不能作为已掌握知识。

## 2. 来源可访问性与内容提纲

### 2.1 AnimeMusicVideos.org

| 来源 | 状态 | 已确认内容 |
|---|---:|---|
| `viewtopic.php?t=129162` — What exactly is internal sync? | A | Internal sync 是镜头内部已经存在的动作或视觉事件与音乐对齐，例如拳头命中、灯光闪烁；同一镜头可以承载多个同步点，不必不断切镜。 |
| `viewtopic.php?t=92942` — Sync guide | B/C | 被多个 AMV 社区讨论反复推荐为同步基础资料；当前直连返回 403，尚未取得全文。 |
| `viewtopic.php?t=123259` | C | 当前直连返回 403，不能总结正文。 |
| `guides/miniguides/sq.html` | C | 当前直连返回 403，不能总结正文。 |

从可读讨论与关联反馈中还能确认：

- movement sync 不是只对单个鼓点，而是让画面运动的速度、方向和音乐的流速相合；
- beat sync、internal sync、movement sync 结合时，画面才更动态、更连贯；
- 单纯整体加速会让动作不自然，并破坏 Flow；
- 如果观众明显看出素材被加速，通常已经过量；
- Flow 与故事/主题连续性并不冲突，随机拼接不同故事会破坏观看理解。

### 2.2 YouTube

当前访客状态统一被“YouTube 需要确认不是机器人”拦截。页面标题和部分简介可读，但视频画面、讲解、时间戳和具体参数不可验证。因此全部标为 B，不把标题推断当成教程正文。

| ID | 已确认标题 | 待验证主题 |
|---|---|---|
| playlist `PL4nLvXiNDXONktVPLJ7JD0M0LQMgKYARJ` | Full AMV Guide for Beginners (IMPROVE FAST) | 完整初学者工作流与课程顺序 |
| `dLPMmVZDEz0` | Syncing Clips \|\| Full AMV Guide for Beginners | 镜头同步选择与调整 |
| `-BQRL9XEWyE` | Twixtor In Davinci Resolve - AMV Tutorial | Resolve 变速、补帧与适用边界 |
| `GHlhzoukoPg` | Ultimate GRAPH Editor Tutorial in After Effects | 速度图/值图曲线逻辑 |
| `IeySXac3vRo` | DaVinci Resolve \| AMV Beginner Walkthrough | Resolve AMV 基础路径 |
| `bzAUWuPBXbI` | How to make a flow edit on Davinci resolve | Resolve Flow Edit 整体做法 |
| `dnd5aCujFRI` | Impact Shake for FLOW EDITS in After Effects | Impact Shake 的触发与曲线 |
| `e6ANFud7xsQ` | DaVinci Resolve AMV Tutorial \| Smooth Scales + Shake | Resolve 平滑缩放与震动 |
| `rOsQgC4tTkg` | Smooth Zoom Transitions \| After Effects AMV Tutorial | Zoom 连续性与图形编辑器 |
| `vsRarfjAUpc` | How to Improve Your AMVs in DaVinci Resolve | Resolve 中 AMV 改进方法 |

说明：`learn.md` 还列出 `GHlhzoukoPg`、`IeySXac3vRo` 等来源对应的建议参数与操作重点。这些内容目前视为“任务书中的待验证假设”，不是视频正文的独立证据。

### 2.3 Bilibili

页面与播放器可访问，标题可确认；本轮未完成逐段听写，因此状态为 B。

| BV 号 | 已确认标题 | 待验证主题 |
|---|---|---|
| `BV1EZ4y1m7wr` | 最强拉镜教程！十分钟能学会的 PR 拉镜转场手法，原理级教学应用！ | 拉镜原理、方向和速度连续性 |
| `BV1Ly4y1q7ZZ` | 「提升剪辑段位」10 个卡点剪辑技巧！ | 卡点类型、层级与组合方式 |
| `BV1e7ffYkEnr` | 【全108集】2025全站最细 PR 动漫混剪教程！ | 动漫混剪完整课程；需筛选与 Flow 直接相关章节 |

### 2.4 Blackmagic Design 官方训练（Resolve 20 官方培训丛书，六本全部定位并下载）

状态 A——六本官方 PDF 的下载链接已从训练页 `https://www.blackmagicdesign.com/products/davinciresolve/training` 的页面源码中逐一提取并确认（`grep` 命中，非猜测）：

| 书目 | 页数 | 作者 | 精读状态 |
|---|---:|---|---|
| The Editor's Guide to DaVinci Resolve 20 | 659 | Chris Roberts | A，已逐页精读（剪辑/Retime/交付） |
| The Visual Effects Guide to DaVinci Resolve 20（Fusion） | 223 | — | A，已逐页精读（Fusion 基础/曲线/Shake 相关） |
| The Fairlight Audio Guide to DaVinci Resolve 20 | 773 | Mary Plummer | A，已针对声音设计相关章节精读 |
| The Beginner's Guide to DaVinci Resolve 20 | 643 | Chris Roberts, Simon Hall | B，仅目录级扫读 |
| Advanced Visual Effects in DaVinci Resolve 20 | 229 | Damian Allen, Dion Scoppettuolo | B，仅目录级扫读（纯 3D/USD，和本项目相关度低） |
| The Colorist Guide to DaVinci Resolve 20 | 496 | — | B，仅目录级扫读 |

**已确认（非推测）的关键事实：**

1. **Retime / 变速（Editor's Guide Lesson 7）**：Resolve 的变速由 Retime Controls（Cmd/Ctrl-R）驱动，支持 Add Speed Point 在同一镜头内建立多段速度；Retime Speed / Retime Frame 两条曲线可在 Keyframes 面板用贝塞尔手柄调节缓入缓出，是 AE 图表编辑器速度曲线的直接对应物。Retime Process 分三档：Nearest（重复帧）、Frame Blend（帧混合）、Optical Flow（光流插帧，遮挡时可能出现涟漪伪影）；选中 Optical Flow 后 Motion Estimation 再分 Standard/Enhanced/AI Speed Warp 各 Faster/Better 六档，AI Speed Warp 仅支持单镜头且免费版带水印。Render in Place 可以把变速烘焙成新媒体文件（对重度使用 AI Speed Warp 的镜头有用）。
2. **Fusion 曲线/变换/伪装镜头晃动（Visual Effects Guide）**：Spline Editor（区别于 Keyframes Editor）是所有可动画参数的曲线面板，支持多关键帧框选 + Smooth（Shift-S）、右键单点插值（如 Flat）、拖手柄调节加速度——是 AE Graph Editor 的直接对应物。Transform 节点（"Xf"）自带 Motion Blur 复选框，Quality（采样数）与 Shutter Angle（默认约等于 360°=整帧曝光拖影，书中案例调到 130–200 做更克制的拖影）为核心参数。**全书没有名为"Camera Shake"的专用工具**；官方给出的通用抖动/摆动机制是 Perturb 修饰器（Strength/Wobble/Speed），书中用于给 Glow 的 Focal Factor 做闪烁动画——把它接到 Transform 节点的 Center/Angle 参数上是符合其工作原理、但书中未直接演示的推论用法，须标注为"按机制推导"而非"官方步骤"。Tracker 节点区分点跟踪（Point Tracker，3 点起步的稳定/去稳定）与面跟踪（Planar Tracker，2.5D、可处理透视和缩放），Match Move 操作 + Merge "BG only" 是标准稳定/复原相机运动流程。
3. **声音分层与总线（Fairlight Audio Guide）**：Fairlight 的音轨支持"层"（Track Layers，View > Show Audio Track Layers），同一位置只播放最上层的素材，可用于堆叠多个音效候选而不必反复删除。官方推荐的标准子混合总线命名是 DX（对白，单声道）/ FX（音效）/ bgFX（背景音效）/ MX（音乐），全部汇入主 Stereo 总线——这是可以直接采纳的声音分轨规范。Foley Sampler 插件可以把音效样本映射到 MIDI/鼠标即时演奏并录制到时间线，是"卡点音效手动对轨"之外的官方录制路径。
4. **Beginner's / Advanced VFX / Colorist（目录级）**：Beginner's Guide 提到 DR20 新增 "IntelliTrack AI for panning audio to match vision" 和 "ColorSlice 六矢量分级"；Advanced VFX 全书是纯 3D/USD 合成路线（3D 摄像机跟踪、粒子、USD 渲染），与二维 AMV 剪辑相关度低，仅作为知识地图存档；Colorist Guide 的 Groups + Scene Cut Detection + 节点模板，是"给大量不同来源素材做统一观感"时唯一可能用到的官方机制，但当前项目暂无该需求。

官方资料能证明 Resolve 的工具能力边界与标准操作步骤，但不会替代 AMV 社区关于 Sync、Flow 和镜头选择的创作知识——这两类资料在本报告第 3、4 节继续分开处理，不互相替代。

## 3. 共同技术原则

### 3.1 Flow 的核心不是效果

Flow 是连续的视听运动关系：

`音乐层级 → 运动意图 → 镜头内部动作 → 剪切/变速 → 少量强调效果`

如果前四层不成立，Zoom、Shake、Flash 只会放大机械感。

### 3.2 同步至少有四种

1. **Beat sync**：切点或事件落在明确节拍上；
2. **Internal sync**：镜头内部动作、命中、眼神、光闪等与音乐对齐；
3. **Movement sync**：画面整体速度与音乐的持续流动相合；
4. **Action sync**：动作蓄力、冲击、回弹等阶段与音乐短语结构相合。

“每拍切镜”只覆盖最表面的 beat sync，而且经常损害另外三种同步。

### 3.3 音乐必须先分层

不能把所有鼓点视为等价事件。最低限度要区分：

- 小节/乐句边界；
- 主重拍；
- 次级节拍或 hi-hat；
- 冲击、drop、fill；
- 人声重音；
- 持续的旋律/能量曲线。

镜头切换优先服务乐句、主重拍和叙事变化；次级节拍更适合内部动作、微变速、轻微缩放或不处理。

### 3.4 两个镜头足以证明 Flow

两个镜头重复并不是限制，反而是有效的最小实验：

- 可以排除素材数量对结果的干扰；
- 可以测试方向、动作峰值、入出速度和能量呼吸；
- 可以明确判断 Flow 是由时序和运动建立，还是由堆效果伪装。

合格的双镜头循环不能只是 `A-B-A-B` 等长播放；每次返回应随音乐层级改变入点、速度轮廓、强调强度或动作阶段。

### 3.5 变速必须围绕动作峰值

合理的变速曲线围绕动作事件设计：

- 峰值前压缩时间或加速，制造蓄力；
- 峰值落在目标音乐事件；
- 峰值后短暂释放、回弹或减速；
- 镜头的进入速度与离开速度要服务下一个镜头。

同一套速度曲线复制到所有镜头，会抹掉每个动作自身的节奏。

### 3.6 Zoom 与 Shake 是强调，不是结构

- Zoom 的方向、中心和速度必须与镜头主体运动、下一镜头构图相连；
- 连续 Zoom 需要进出曲线相互衔接，不能只在切点突然跳比例；
- Shake 只应响应真正的冲击，幅度、频率、衰减要与音乐强度对应；
- 每个鼓点相同 Shake、相同 Flash，会把层级全部压平。

## 4. 资料间的差异与潜在冲突

1. **软件差异**：部分资料以 After Effects/PR 为例，能迁移的是曲线、时序和视听原则，不是插件名或面板步骤。
2. **Twixtor 与原生变速**：Twixtor 教程可能依赖第三方补帧；本项目只能在 Resolve 能力已验证、伪影可接受时使用对应路径。
3. **卡点教程与 Flow**：卡点技巧容易强调“事件数量”，AMV Flow 更强调层级、内部同步和连续运动；两者不能简单等同。
4. **模板参数与镜头语义**：任务书中的 Zoom、Shake、曲线参数可作为实验起点，但不能成为所有镜头的固定模板。
5. **社区经验与官方训练**：社区资料解释“为什么这样剪”；官方资料解释“Resolve 如何实现”。两者缺一不可。

## 5. 必须继续验证的问题

1. `t=92942` 对 sync 的完整分类、定义和例子是什么？
2. Vivifx/相关 Flow 教程如何定义 scene-to-scene flow，与 movement sync 的边界是什么？
3. YouTube 播放列表中每节课程的顺序、示例与时间戳是什么？
4. 【部分已确认】官方文档已确认 Retime Process 三档（Nearest/Frame Blend/Optical Flow）与 Motion Estimation 六档（含 AI Speed Warp）的选择逻辑与参数位置；但具体在 23.976 帧率的日系动画（低帧率补间、大量单帧/双帧作画）上哪一档开始出现可见涟漪/伪影，官方教材未给出针对动漫素材的边界值，仍需用项目内真实素材做人工实测才能定案。
5. Smooth Zoom 和 Impact Shake 的关键不是数值本身，而是哪种曲线形状、持续帧数和音乐触发条件？
6. 两镜头最小样片中，哪些变化来自选点，哪些来自 retime，哪些来自 Fusion/Transform？
7. 当前 `config/resolve_capabilities.yaml` 中哪些实现手段已经 `verified`，哪些只能做人工实验而不能进入自动生成？

## 6. 对此前失败结果的诊断修正

此前“机械播放、完全不在音乐 Flow 上”的根因，不是镜头不足，也不是 Resolve 不够强，而是创作模型顺序错了：

- 把节拍表当成剪切表；
- 把 Flow 当成 Zoom/Shake/转场组合；
- 没有先识别镜头内部动作峰值；
- 重复使用等长片段和相同曲线；
- 视觉事件没有按音乐主次分配；
- 在“同步骨架”未成立前就堆叠效果。

因此下一版 Gold Master 的验收顺序必须改为：

1. 关掉所有附加效果，只靠选点、切点和变速建立 Flow；
2. 双镜头循环在静音时仍有清楚的动作方向与呼吸；
3. 开音乐后，主要动作峰值准确落在主重拍/乐句事件；
4. 再增加 Zoom；
5. 最后只在少数冲击点增加 Shake/Flash；
6. 任何效果若不能提高视听连接，直接删除。

## 7. AE/PR → Resolve 官方术语映射表（附录，供后续规范文档直接引用）

标注规则：「已确认」= 来自本轮实际精读的官方 PDF 原文；「推导」= 依据已确认机制的逻辑推论，书中未见对应逐步教程，写入规范前需人工过一遍验证；不在表中的条目一律视为未验证，禁止在最终规范里编造。

| AE / PR 概念 | Resolve 对应 | 状态 | 依据 |
|---|---|---|---|
| Graph Editor（速度/值曲线） | Spline Editor（Fusion 页，非 Retime 的 Keyframes Editor） | 已确认 | Visual Effects Guide Lesson 7 |
| Time-Remapping / Speed Graph | Retime Controls：Retime Speed / Retime Frame 曲线，Add Speed Point 分段 | 已确认 | Editor's Guide Lesson 7 |
| 光流慢动作插帧 | Retime Process = Optical Flow，Motion Estimation 六档（含 AI Speed Warp） | 已确认 | Editor's Guide Lesson 7 |
| Wiggle 表达式 | Modifiers > Perturb（Strength/Wobble/Speed，通用参数随机化） | 已确认（机制），推导（接到 Transform 做镜头晃动） | Visual Effects Guide Lesson 7 |
| Camera Shake（插件/预设） | 无专用工具；只能用 Perturb 接 Transform 的 Center/Angle 手动搭 | 推导，需人工验证观感 | 同上 |
| Motion Blur 开关 | Transform 节点 Inspector > Settings > Motion Blur，含 Quality / Shutter Angle | 已确认 | Visual Effects Guide Lesson 7 |
| Transform/Scale/Position 图层属性 | Transform 节点（"Xf"） | 已确认 | Visual Effects Guide Lessons 2–3 |
| Warp Stabilizer | Tracker 节点，Operation=Match Move + Merge "BG only" | 已确认 | Visual Effects Guide Lesson 2 |
| Mocha 面跟踪 | Planar Tracker（2.5D，处理透视/缩放） | 已确认 | Visual Effects Guide Lesson 4 |
| Roto Brush / 手动遮罩 | B-Spline / Polygon / MultiPoly 节点，divide-and-conquer 关键帧法 | 已确认 | Visual Effects Guide Lesson 5 |
| Track/Layer 音轨分层（同轨多候选） | Fairlight Track Layers（View > Show Audio Track Layers） | 已确认 | Fairlight Audio Guide Lesson 2 |
| Bus/Submix 分组（DX/FX/Music） | Fairlight Bus Format：Stereo 主线 + DX/FX/bgFX/MX 子混合总线 | 已确认（官方推荐命名） | Fairlight Audio Guide Lesson 8 |
| Foley/音效手动打点 | Foley Sampler（FairlightFX，MIDI/鼠标演奏后直接录入时间线） | 已确认 | Fairlight Audio Guide Lesson 4 |
| Twixtor 插帧 | 无原生对应；项目不使用第三方插件，只能在 AI Speed Warp 伪影可接受范围内替代 | 未验证边界 | 结合第 5 节问题 4 |

## 8. 第一阶段结论

当前可以确定：项目的问题不是“设计太简单”，而是把复杂度放错了层级。真正需要加深的是音乐结构、动作语义和连续运动的建模；Resolve 应负责准确执行这些决定，而不是替代创作判断。

下一阶段不应直接写一篇泛化教程，而应先完成两个验证物：

1. 一份可人工审阅的“双镜头 Flow 节拍—动作映射表”；
2. 一条不依赖花哨效果、先证明 Sync/Flow 成立的短样片。

只有这两个验证物通过，才把结论收敛成最终《DaVinci Resolve 短视频动漫 Flow Edit 制作规范》。
