# projects/_archive —— v1 历史产物（只读）

归档于 2026-07-25，Phase 0.5。

## 内容

57 个文件，来自 v1（Remotion）时期的 9 个项目：
`editspec*.json`、`beatmap.json`、`reference-dna.json`、`config.*.toml`、预览音床 `*.wav`。

各项目目录下保留的 `outputs/` 是 v1 成片，未移动。

## 用途 —— 只有两个

1. **回归对照数据**
   - `beatmap.json` → 验证新 MusicMap 的 BPM / beats / downbeats 是否与旧实现一致
   - `reference-dna.json` → 验证新 StyleFingerprint 的节奏语法部分是否退化
2. **创意意图参考**
   - 旧 `editspec.arc.json` 中的镜头选择与节奏，是 Sequence Planner 的人工对照样本

## 禁止事项

- ❌ 任何 v2 代码不得读取本目录（回归测试的 fixture 除外，且必须显式标注）
- ❌ 不得把旧 EditSpec 迁移进 v2 —— v1 是 Remotion 私有结构，无迁移价值
- ❌ 不得从这里复制字段设计到 EditSpec v2

## 数据现状备注（Phase 0.4 清点）

旧库 `library/engine.v1.sqlite.bak`（74MB）：

| 表 | 行数 | ETL 价值 |
|---|---|---|
| `assets` | 40 | ✅ 迁移 |
| `shots` | 15,901 | ✅ 迁移（**embedding 全部非空**，15,385 条已完成语义分析） |
| `shot_scores` | 10,099 | ❌ 不迁移，语义已变，重算 |
| `creative_briefs` | 5 | ✅ 迁移 |
| `project_assets` | 27 | ✅ 迁移 |
| `source_records` | 39 | ✅ 迁移（provenance） |
| `enhancement_reviews` | 114 | ❌ 不迁移（增强链路已废弃） |
| `review_decisions` | **0** | — 无数据 |
| `preference_models` | **0** | — 无数据 |
| `growth_experiments` / `growth_variants` / `shot_outcomes` | **0** | — 无数据 |

> **重要发现**：偏好学习与增长实验的表结构存在，但**从未有过真实数据**。
> 这意味着 Phase 8 的偏好模型没有历史数据可继承，只能从 Phase 4 开始重新采集。
> 真正有价值的存量资产是 **15,901 条带 embedding 的已分析镜头**。
