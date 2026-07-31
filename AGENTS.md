# AGENTS.md —— Anime Edit Studio 开发契约

> 适用对象：Claude Code、Codex、GPT、任何接手本项目的 AI Agent，以及人类开发者。
> 本文件是**进入本仓库后必须读的第一份文件**。

---

## 0. 这是什么系统

输入 Demo 视频、目标音乐和一批动漫素材，系统自动分析 Demo 的剪辑语言（镜头节奏、
运镜、转场），按目标音乐重新计算节奏结构，自动从素材库选镜，在 DaVinci Resolve 中
生成具有连续音乐律动和跨镜头后期运镜的 AMV。

唯一产品用例：`aes amv create --project --demo --materials [--music]`。
没有第二条链路，没有旧系统兼容层。旧的 EditSpec IR、人工 Candidate A/B/C 工作流、
Preference/Growth 学习闭环、Review UI、Recipe Zoo 已经删除，不要从历史 commit 里
把它们的代码或概念抄回来。

阅读顺序，冲突时上位者胜：

1. `docs/ARCHITECTURE.md` —— 模块边界与数据流
2. `docs/OPERATIONS.md` —— 怎么跑、怎么排障
3. `config/resolve_capabilities.yaml` —— Resolve 能力与实测踩坑的唯一真相来源

---

## 1. 不可违反的规则

### R1 · Resolve 只能经 ResolveAdapter

`studio/execution/resolve/` 之外的任何文件，**禁止** `import DaVinciResolveScript`。
上层一律通过 `ResolveAdapter` 门面访问。`tests/test_architecture_rules.py` 强制检查。

### R2 · 未 verified 的能力禁止生成对应指令

`config/resolve_capabilities.yaml` 中 `verified: false` 的能力，规划/编译层不得
产出对应字段。

- `probed: true` = 方法存在
- `verified: true` = **实测调用成功且效果达标**（渲染出帧比对支撑，不能只看返回值）

从 false 转 true 必须附带一条真机测试。**绝不假称已执行。**

### R3 · 时间码只有一个换算实现

素材/时间线帧率非整数（如 23.976）时，帧制 IR 必然产生亚帧漂移。`AMVSpec` 用秒 +
显式 `Timebase` 表达时间，帧数只在 `studio/execution/resolve/` 边界换算，禁止在
别处重复实现换算逻辑。

### R4 · 禁止硬编码任何具体项目

生产代码（`studio/` 下非测试代码）不得出现固定的 Demo 时间戳、固定镜头列表、固定
时长、角色名分支判断。分析结果必须来自对当前输入的实际测量，带置信度和证据
（`Estimate` 模式），不是抄一份写死的数字。测试 fixture 允许用虚构名字。

### R5 · LLM 不碰确定性逻辑

时间码 / 帧换算 / 时间线位置 / 媒体查找 / 缓存 / 渲染参数 / 资产哈希 / 技术 QA
必须是确定性代码。这条 pipeline 目前完全不依赖 LLM。

### R6 · 素材代理是长期资产，禁止作为项目缓存清理

`library/proxies/` 服务于全部素材和后续所有作品，不属于单个项目的临时产物。
除非用户明确点名要求删除代理库，任何清理操作都禁止删除、移动或清空该目录。
可以清理超分缓存、mezzanine、旧预览和临时渲染，但必须保留素材代理、镜头分析
数据库和关键帧。

### R7 · 产物不进仓库

`projects/<project-id>/` 下的 `preview.mov`、`release.mov`、`qa.json` 是运行产物，
不提交进 git。每个项目只保留一个可覆盖的 preview 和一个可覆盖的 release，不用
`r7`/`r8`/`v2` 之类的文件名堆积历史版本。

### R8 · 必须真实测试，禁止虚假验收

凡是声称"完成"的能力，必须有对应测试实际跑过并通过；Resolve 相关能力必须有
`@pytest.mark.requires_resolve` 标记的真机测试证据，不能仅凭代码读起来合理就
宣称完成。如果真机环境当时不可用，必须如实说明"未验证"，不得假装测过。

---

## 2. 新增任何 Feature 前必须回答

1. 它属于哪一层（analysis / planning / execution / qa）？
2. 输入是什么？输出是什么？是否已有 schema？
3. 是否需要 Resolve？对应 capability 是否 `verified`？
4. 是否可测试？能否离线测（不依赖真机 Resolve）？
5. 是否影响 Determinism？是否引入了任何项目特化的硬编码？
6. 删除旧模块后，是否还有残留 import？

**回答不出来，就不要实现。**

---

## 3. 工程底线

**必须**：typed（pydantic v2，`extra="forbid"`） / modular / testable /
可离线测试 / 有真机验收路径。

**禁止**：
- 巨型脚本
- 隐式全局状态
- 到处直接调 Resolve API（见 R1）
- JSON 字段随意变化、无 schema 数据
- 为某个具体 Demo/角色写专用分支

---

## 4. Resolve 开发须知（实测踩坑记录）

**完整列表见 `config/resolve_capabilities.yaml` 的 `pitfalls` 段（P1–P22+）。**
每条都对应一次真实的静默错位，不是理论推测；删除它们等于把坑重新埋回去。
高频踩坑摘要：

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

环境变量（`connection.py` 负责注入，不要求用户手工 export）：

```bash
RESOLVE_SCRIPT_API="/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
RESOLVE_SCRIPT_LIB="/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"
PYTHONPATH="$PYTHONPATH:$RESOLVE_SCRIPT_API/Modules/"
```

---

## 5. 接手本项目的第一步

不要立刻改代码。先读 `docs/ARCHITECTURE.md` 和 `config/resolve_capabilities.yaml`，
搞清楚哪些能力真的可用、哪些还是 `unverified`。

**没有明确要求的功能，不要"顺手"实现。**
