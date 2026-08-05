# AGENTS.md —— Anime Edit Studio 操作与开发契约

> 适用对象：Claude Code、Codex、GPT，任何接手本项目的 AI Agent，以及人类开发者。
> 本文件是**进入本仓库后必须读的第一份文件**。

---

## 0. 这是什么系统，最终要做出什么效果

这不是一个通用剪辑工具，是一条只服务一个目标的流水线：**把一批已经选好的动漫
镜头，编排成一条节奏、运镜都对齐参考视频的"爆款"AMV**。

镜头本身**不在本项目里选**——素材的切分、打标签、质量/角色筛选发生在姊妹项目
[anime-shot-library](../anime-shot-library) 里，人工在那边挑出一批 shot_id 交
给本项目。本项目只负责"编排 + 渲染 + 渲染后 QA"这一段。

最终成片要达到的效果，按重要性排序：

1. **Demo 驱动的"精确复刻"剪辑语言**：先测量一条参考 Demo 视频的节奏语法——镜头
   时长分布、运镜方向、cut 前后的运动关系（carry 顺接 / reverse 反打 / reset 归
   零）、cut 与音乐节拍的偏移——再把这套语法套用到新音乐 + 新素材上，让成片"神
   似"那条范本，而不是自造剪法。对应 `studio.analysis.reference_analyzer`。
2. **跨镜头运动连续性**：不是每个镜头独立运镜，出片镜头减速的那一下和入片镜头
   接力的那一下是一起算的（`studio.planning.motion_planner.build_transition_pair`），
   同方向接住再稳住，不允许反向碰撞或黑边。
3. **音乐卡点**：用目标音乐的节拍/重音分配 TimelineSlot，强重音位置留给更强的
   视觉事件，稳定区允许停顿，不是无脑每拍一切（`studio.planning.rhythm_style_mapper`）。
4. **镜头编排（不是镜头发现）**：Beam Search 只在调用方给定的 shot_id 集合里选
   顺序、判连续性、判去重，不做任何"这个镜头够不够格"的判断——那件事已经在
   anime-shot-library 里做完了（`studio.planning.global_sequence_planner`）。
5. **4K + 全镜头超分 + LUT/SFX 精修的 finalize 环节 —— 计划中，尚未实现**。当前
   `aes amv release` 只是把 QA 通过的 `preview.mov` 原样拷贝成 `release.mov`；
   `kit/luts`、`kit/sfx`、`kit/bgm` 是留好的素材位，还没有任何代码在用它们。
   在这个能力建好之前，不要在文档或对话里假称成片已经过精修/超分。

唯一产品用例：`aes amv create --project --demo --shots [--music]`，再
`aes amv release --project`。没有第二条链路。旧的原始素材扫描/自动选镜 CV 栈
（`studio.asset_intelligence`、`studio.selection`）已经删除，**不要从历史 commit
里把它们的代码或概念抄回来**——历史提交里那套是被这次重构主动淘汰的架构，不是
遗漏。

---

## 1. 端到端操作流程

1. 在 anime-shot-library 里挑好这次要用的镜头，拿到一批 shot_id（那边的
   `catalog.sqlite`：assets/shots/tags）。
2. 把 shot_id 写成一个文件：一行一个，或 JSON 数组。
3. 跑：

   ```bash
   uv run aes amv create \
     --project <project-id> \
     --demo /path/to/demo.mp4 \
     --shots shot_ids.txt \
     --music /path/to/music.wav \
     --launch
   ```

   产出在 `projects/<project-id>/`：`project.json`（记录本次用的
   demo/shot_ids/music/focus）、`reference_blueprint.json`、`music_timeline.json`、
   `amv_spec.json`（最终编排结果）、`preview.mov`、`qa.json`。
4. 看 `preview.mov`，按 §0 的 5 条标准逐条核对（尤其第 1-3 条——这是本项目真正
   要对的东西）。有明确问题就改，最多两轮修正，不要无限优化。
5. `qa.json` 通过硬门禁后，显式执行 `uv run aes amv release --project <id>`，
   产出 `release.mov`。QA 不过时 `release` 会直接拒绝，不要绕过。

`ANIME_SHOT_LIBRARY_DB` 环境变量指向 anime-shot-library 的 `catalog.sqlite`；
不设置时默认按兄弟目录 `~/Desktop/anime-shot-library/data/catalog.sqlite` 查找。

---

## 2. 不可违反的规则

### R1 · Resolve 只能经 ResolveAdapter

`studio/execution/resolve/` 之外的任何文件，**禁止** `import DaVinciResolveScript`。
上层一律通过 `ResolveAdapter` 门面访问。

### R2 · 未 verified 的能力禁止生成对应指令

`config/resolve_capabilities.yaml` 中 `verified: false` 的能力，规划/编译层不得
产出对应字段。`probed: true` = 方法存在；`verified: true` = **实测调用成功且效
果达标**（渲染出帧比对支撑，不能只看返回值）。从 false 转 true 必须附带一条真
机测试，**绝不假称已执行**。

### R3 · 时间码只有一个换算实现

素材/时间线帧率非整数（如 23.976）时，帧制 IR 必然产生亚帧漂移。`AMVSpec` 用秒
+ 显式 `Timebase` 表达时间，帧数只在 `studio/execution/resolve/` 边界换算，禁止
在别处重复实现换算逻辑。

### R4 · 禁止硬编码任何具体项目

生产代码（`studio/` 下非测试代码）不得出现固定的 Demo 时间戳、固定镜头列表、固
定时长、角色名分支判断。分析结果必须来自对当前输入的实际测量，测试 fixture 允
许用虚构名字。

### R5 · LLM 不碰确定性逻辑

时间码 / 帧换算 / 时间线位置 / 媒体查找 / 缓存 / 渲染参数 / 资产哈希 / 技术 QA
必须是确定性代码。这条 pipeline 目前完全不依赖 LLM。

### R6 · 素材代理是长期资产，禁止作为项目缓存清理

`library/proxies/` 服务于全部素材和后续所有作品，不属于单个项目的临时产物。除
非用户明确点名要求删除代理库，任何清理操作都禁止删除、移动或清空该目录。

### R7 · 产物不进仓库

`projects/<project-id>/` 下的 `preview.mov`、`release.mov`、`qa.json` 是运行产
物，不提交进 git。每个项目只保留一个可覆盖的 preview 和一个可覆盖的 release，
不用 `r7`/`r8`/`v2` 之类的文件名堆积历史版本。

### R8 · 必须真实测试，禁止虚假验收

凡是声称"完成"的能力，必须有对应测试实际跑过并通过；Resolve 相关能力必须有
`@pytest.mark.requires_resolve` 标记的真机测试证据。真机环境不可用时必须如实说
明"未验证"，不得假装测过。

### R9 · 选片的边界不能悄悄挪回本项目

镜头"够不够格用"（清晰度、角色是否对、动作是否有效）是 anime-shot-library 的
职责，已经在那边做完。`studio.planning.candidates` 只做"已经确定要用的这个镜
头，怎么裁剪时长、怎么估运镜方向、跟这个 Slot 搭不搭"，**不要**在这里重新加回
任何清晰度/角色识别/学习型质量打分——那正是这次重构删掉的东西。

---

## 3. 新增任何 Feature 前必须回答

1. 它属于哪一层（analysis / planning / execution / qa）？
2. 输入是什么？输出是什么？是否已有 schema？
3. 是否需要 Resolve？对应 capability 是否 `verified`？
4. 是否可测试？能否离线测（不依赖真机 Resolve）？
5. 是否影响 Determinism？是否引入了任何项目特化的硬编码？
6. 是否越界回到了"选镜"（见 R9）？

**回答不出来，就不要实现。**

---

## 4. Resolve 开发须知（实测踩坑记录）

**完整列表见 `config/resolve_capabilities.yaml` 的 `pitfalls` 段。** 每条都对应
一次真实的静默错位，不是理论推测；删除它们等于把坑重新埋回去。高频踩坑摘要：

| # | 坑 | 应对 |
|---|---|---|
| P1 | 进程名是 `Resolve`，不是 `DaVinci Resolve` | 健康检查用 `pgrep -x Resolve` |
| P2 | Resolve 未运行时 `scriptapp()` **静默返回 None** | 必须显式判空并给出可操作报错 |
| P7 | `GetSourceStartFrame`/`GetLeftOffset` 跨帧率不可靠 | 一律用 `GetSourceStartTime()`（返回秒） |
| P8 | `AppendToTimeline` 的 `endFrame` 是**开区间** | `end = start + n`，不减 1 |
| P9 | 时间线起始帧默认 **86400**（01:00:00:00） | `recordFrame` 必须加 `GetStartFrame()` |
| P10 | `AppendToTimeline` 无法填补轨道空洞 | 时间线全量重建；增量下沉到渲染层 |
| P12 | `hasattr`/`dir()` 对远程对象是假阳性 | 可用性判定必须"调用 + 检查返回值 + 渲染出帧比对"三步 |
| P19 | `AppendToTimeline` 省略 `mediaType` 会把源音频一并放入时间线 | 视频 clip 强制 `mediaType=1` |
| P22 | Close/Delete/Create Project 后 `GetMediaPool()`/`GetClipList()`/`SetSetting()` 短暂返回 None/False，随后自愈 | 轮询 + 重试；`GetClipList()`/`GetSubFolderList()` 一律 `or []` |

环境变量（`studio/execution/resolve/connection.py` 负责注入，不要求用户手工 export）：

```bash
RESOLVE_SCRIPT_API="/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
RESOLVE_SCRIPT_LIB="/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"
PYTHONPATH="$PYTHONPATH:$RESOLVE_SCRIPT_API/Modules/"
```

---

## 5. 接手本项目的第一步

不要立刻改代码。先读本文件 + `README.md`（跑法）+ `config/resolve_capabilities.yaml`
（哪些能力真的 verified），再看 `studio/workflows/create_amv.py` 把整条链路的
调用顺序过一遍。

**没有明确要求的功能，不要"顺手"实现**——尤其是 §0 第 5 条那个 finalize 环节，
建之前先跟用户确认范围（LUT 怎么选、SFX 触发规则、超分用什么模型/走什么管线），
不要自己猜一套加上去。
