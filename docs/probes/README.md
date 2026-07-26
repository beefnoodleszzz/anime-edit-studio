# docs/probes —— Resolve 能力探测的原始证据

`config/resolve_capabilities.yaml` 中每一条 `verified` 或 `available: false`
都必须能追溯到这里的一次真实运行。**结论可以被推翻，证据不能丢。**

## 探测脚本

| 脚本 | 阶段 | 回答的问题 |
|---|---|---|
| `probe_resolve_capability.py` | Phase 0 | Resolve 脚本 API 的方法表面有哪些 |
| `probe_key_capabilities.py` | Phase 1.12–1.14 | SmartReframe / MagicMask / SpeedRamp 能否调用 |
| `probe_key_capabilities2.py` | Phase 1.12–1.14 | 深挖签名与取值类型；确认 SetSpeedRamp 不存在 |
| `probe_smartreframe_visual.py` | Phase 1.12 | SmartReframe 是否**真的改变画面**（渲染出帧比对） |

跑法：

```bash
.venv/bin/python docs/probes/probe_key_capabilities2.py
```

## 视觉证据

`probe_smartreframe_visual.py` 的产物，全部为 1080×1350 竖屏时间线的渲染首帧：

| 文件 | 内容 | md5 前 12 位 | YAVG |
|---|---|---|---|
| `v2_base.png` | 基线，未做任何变换 | `d65691a396d3` | 80.62 |
| `v2_control.png` | **对照组**：手动 `Pan=400` | `98b0ad25a361` | 50.84 |
| `v2_smartreframe.png` | 调用 `SmartReframe()` 之后 | `d65691a396d3` | 80.62 |

## Phase 2.0 专业执行风险预探测

探测脚本：`probe_phase2_risks.py`
结构化证据：`phase2_risk_probe.json`

Resolve 21.0.3.7 Studio 真机结论：

| 能力 | 结论 | 输出证据 |
|---|---|---|
| Color Recipe / ColorGroup | ✅ verified | 两个 clip 入组；group post-clip LUT 生效；渲染帧哈希改变 |
| Audio Track Management | ✅ verified | stereo 轨创建成功，轨道数 1 → 2 |
| Fairlight Automation | ❌ unavailable | 无 automation/volume/gain 方法；四种属性写入全部返回 False |
| Native Transition | ❌ 不存在 | Timeline / TimelineItem 均无 transition 方法 |
| Fusion transition fallback | ⚠️ 入口存在、效果未验收 | `CreateFusionClip` 成功；正式 transition Recipe 仍保持 unverified |
| Fusion Recipe 技术闭环 | ✅ verified | AddTool、注参、连接、Export、Import 成功；渲染帧改变 |

方法仍遵守 P12–P14：方法存在与返回成功都不是视觉能力的充分证据；
Color 与 Fusion 的正结论均包含渲染输出对照。

三张图讲了一个完整的故事：

1. 对照组与基线**不同** → 检测手段有效
2. SmartReframe 与基线**完全相同**（md5 逐字节一致）→ 该 API 空转
3. 因此 `portrait_reframe` 判定为 `available: false`

若只看 `SmartReframe()` 的返回值（`True`），会得出完全相反的结论。
这就是 pitfall **P13「返回 True 但不生效」**的由来。

## 方法论要求（P12–P14）

新增任何能力判定时必须遵守：

1. **不要用 `hasattr`** —— Resolve 远程对象对未知属性返回 `None`，`hasattr` 恒为 True（P12）。
   用 `callable(getattr(obj, name, None))`。
2. **影响画面的能力必须渲染出帧比对**，不能只看返回值（P13）。
3. **比对前先确认画面有内容** —— 黑场上任何变换都不可见。
   第一版验证误取 YAVG≈30 的近黑帧，导致对照组也判为「无差异」（P14）。
4. 先跑**对照组**（已知会改变画面的操作），确认检测手段有效，再判定被测能力。

## 清理

这些脚本不属于最终架构，但**在 Phase 6 之前不要删** ——
Phase 6 需要重新验证 color / fairlight / transition 等能力时会复用同样的方法。
