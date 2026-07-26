# Documentation Index

本目录是 Anime Edit Studio v2 项目文档的唯一存放位置。根目录只保留：

- `README.md`：仓库入口与快速使用说明。
- `AGENTS.md`：对整个仓库生效的开发契约，必须位于作用域根。

## 权威文档

冲突时按以下顺序处理：

1. [产品总体规范](product/WANT.md)
2. [目标架构](architecture/TARGET_ARCHITECTURE.md)
3. [迁移计划](planning/MIGRATION_PLAN.md)
4. [架构差距分析](architecture/ARCHITECTURE_GAP_ANALYSIS.md)
5. [`config/resolve_capabilities.yaml`](../config/resolve_capabilities.yaml)

能力矩阵比叙述性文档更接近运行时事实。任何 Resolve 能力只有在真实调用、
渲染对照和测试证据都通过后，才能标记为 `verified: true`。

## 当前状态

- [2026-07-26：v2 合并前状态、改动与已知问题](status/2026-07-26-v2-status.md)
- [v2 完成度审计](V2_COMPLETION_AUDIT.md)
- [v2 需求矩阵](V2_REQUIREMENTS_MATRIX.md)

## 验收与探测证据

- `phase*.json`：各阶段结构化验收结果。
- `probes/`：Resolve 真实行为探测脚本和证据。
- `phase6_recipe_intents.md`：Recipe 的视觉与声音意图。

这些文件属于能力真实性证据，不因一次产品成片完成而删除。

## 已删除文档

`CURRENT_ARCHITECTURE.md` 已删除。它描述的是 v1 旧实现，当前需要历史代码时应直接
查看 tag `v1-final`，避免旧架构说明继续被误认为开发指导。
