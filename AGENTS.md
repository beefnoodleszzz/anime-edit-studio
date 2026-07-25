# AGENTS.md —— Anime Edit Studio 开发契约

> 适用对象：Claude Code、Codex、GPT、任何接手本项目的 AI Agent，以及人类开发者。
> 本文件是**进入本仓库后必须读的第一份文件**。

---

## 0. 文档权威级别

阅读顺序，冲突时上位者胜：

1. `WANT.md` —— 产品级最高规范（MASTER PLAN）
2. `TARGET_ARCHITECTURE.md` —— 目标架构与设计决策记录
3. `MIGRATION_PLAN.md` —— 分阶段计划、KPI、风险登记册
4. `ARCHITECTURE_GAP_ANALYSIS.md` —— 处置决定（哪些重写、哪些删除）
5. `CURRENT_ARCHITECTURE.md` —— v1 现状存档，**仅供理解历史，不指导新开发**
6. `config/resolve_capabilities.yaml` —— Resolve 能力的唯一真相来源

---

## 1. 项目当前状态

| 项 | 值 |
|---|---|
| 分支 | `v2`（重建中） |
| v1 快照 | tag `v1-final` —— 仅供应急出片，**不得从中复制代码到 v2** |
| 阶段 | Phase 0 完成 → Phase 1（Resolve PoC） |
| 执行后端 | **DaVinci Resolve 21.0.3.7 Studio，唯一成片后端** |
| 口径 | **完全改造**。不保留兼容层，不需要的直接删除 |

---

## 2. 不可违反的规则

### R1 · Resolve 只能经 ResolveAdapter

`studio/execution/resolve/` 之外的任何文件，**禁止** `import DaVinciResolveScript`。
上层一律通过 `ResolveAdapter` 门面访问。CI 强制检查。

### R2 · EditSpec 必须过 validator 才能进入执行层

任何 EditSpec 在被 compiler 消费前必须 `validate()` 通过。
禁止"先执行，出错再说"。

### R3 · 未 verified 的能力禁止生成对应指令

`config/resolve_capabilities.yaml` 中 `verified: false` 的能力，
EditSpec 生成器不得产出对应字段。

- `probed: true` = 方法存在
- `verified: true` = **实测调用成功且效果达标**

两者不可混淆。从 false 转 true 必须附带一条测试。
**绝不假称已执行**（继承 v1 `extreme.capabilities()` 的唯一优良传统）。

### R4 · Recipe 必须有验收物

每个 Effect / Color / Sound Recipe 目录必须包含：

```
recipe.yaml        参数 schema（type/min/max/default）+ version + 依赖能力
<实现产物>          .comp / .drx / .wav
preview.mp4        视觉样张
ACCEPTANCE.md      人工验收记录（谁、何时、判定）
```

**缺任何一项 → capability 保持 unverified → AI 不得使用该 recipe。**

### R5 · v2 禁止引用 v1

`studio/` 中禁止 import `anime/`、禁止读取 `renderer/`、
禁止读取 `projects/_archive/`（回归测试 fixture 除外，且须显式标注）。

### R6 · LLM 不碰确定性逻辑

以下必须是确定性代码，禁止交给 LLM：
时间码 / 帧换算 / 时间线位置 / 媒体查找 / 缓存 / 渲染参数 / 资产哈希 / 版本 / 校验 / 技术 QA。

LLM 只负责：语义推理、创意决策、排序、风格理解、修订意图解析。
且必须输出结构化 JSON（schema 强约束），禁止"自然语言 → 正则解析 → 执行"。

### R7 · Prompt 不是业务逻辑

禁止把规则写进巨型 prompt 然后指望模型自觉遵守。
约束 / 校验 / 时序 / schema / 数据 / 能力 / 执行 **全部由代码负责**。

### R8 · EditSpec 以秒为权威

实测素材帧率为 23.976，交付为 4:5。非整数帧率下帧制 IR 必然累积亚帧漂移。
EditSpec 用秒 + 显式 `timebase`，帧数由 compiler 换算。
`studio/core/timecode.py` 是**唯一**的换算实现，禁止在别处重复实现。

### R9 · GUI Automation 默认禁用

鼠标点击 Resolve 是最后手段，默认关闭，需显式开关且必须记录调用。
禁止把它设计成核心能力。

---

## 3. 新增任何 Feature 前必须回答

1. 它属于哪一层？
2. 输入是什么？输出是什么？
3. 是否进入 EditSpec？
4. 是否需要 Resolve？对应 capability 是否 `verified`？
5. 是否应该 Recipe 化？
6. 是否能 Cache？Cache key 含哪些版本（asset hash / model / model version / pipeline version / 参数）？
7. 是否可测试？
8. 是否影响 Determinism？涉及 LLM 是否已隔离在 `agents/`？
9. **是否真的降低用户人工操作？**
10. 对出片效率 / 变现目标的贡献是什么？

**回答不出来，就不要实现。**

---

## 4. Definition of Done

代码写完 ≠ 完成。必须全部满足：

- [ ] Schema 已定义并纳入版本
- [ ] 实现完成
- [ ] 测试完成
- [ ] 结构化日志完成
- [ ] 错误处理与重试完成
- [ ] 版本兼容性已考虑
- [ ] 文档更新（受影响的架构文档）
- [ ] 端到端路径已验证：`aes` 一条命令跑通主用例
- [ ] 本阶段应删除的旧模块已删除，CI 无残留 import
- [ ] 新验证的 capability 已转 `verified` 并配测试

---

## 5. 工程底线

**必须**：typed / modular / testable / observable / versioned / recoverable

**禁止**：
- 巨型脚本、巨型 prompt
- 隐式全局状态
- 到处 subprocess
- 到处直接调 Resolve API
- JSON 字段随意变化、无 schema 数据
- 无版本缓存、无日志执行

---

## 6. Resolve 开发须知（实测踩坑记录）

| # | 坑 | 应对 |
|---|---|---|
| P1 | 进程名是 `Resolve`，不是 `DaVinci Resolve` | 健康检查用 `pgrep -x Resolve` |
| P2 | Resolve 未运行时 `scriptapp()` **静默返回 None**，不抛异常 | 必须显式判空并给出可操作的报错 |
| P3 | 系统 Python 3.14 与 fusionscript 不兼容 | 强制 venv 3.11；启动校验 `sys.version_info` |
| P4 | 素材真实帧率 23.976 | 见 R8 |
| P5 | Resolve 必须前台运行，无真 headless | 启动等待 + 健康检查；渲染串行排队 |

环境变量（`connection.py` 负责注入，不要求用户手工 export）：

```bash
RESOLVE_SCRIPT_API="/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
RESOLVE_SCRIPT_LIB="/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"
PYTHONPATH="$PYTHONPATH:$RESOLVE_SCRIPT_API/Modules/"
```

---

## 7. 接手本项目的第一步

不要立刻改代码。先：

1. 读 §0 的 1–4 号文档
2. 读 `config/resolve_capabilities.yaml`，搞清楚哪些能力真的可用
3. 确认当前处于 `MIGRATION_PLAN.md` 的哪个 Phase
4. 确认要做的事在该 Phase 的任务表里；不在，就先问

**不在计划内的功能，不要"顺手"实现。**
