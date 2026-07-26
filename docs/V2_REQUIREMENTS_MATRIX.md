# v2 Phase 2–9 要求—证据矩阵

更新时间：2026-07-26。状态只使用 `PASS`、`FAIL`、`INSUFFICIENT_DATA`、
`PENDING_HUMAN`、`PENDING_EXTERNAL_DATA`；没有证据不算通过。

| Phase | 要求 | 状态 | 权威证据 |
|---|---|---|---|
| 2 | 完整 EditSpec、validator、diff、migration、v2 DB、状态机 | PASS | `tests/editspec/`、`tests/core/`、`docs/phase2_etl_report.json` |
| 2 | v1 ETL 数量与 embedding 一致 | PASS | 40 assets、15,901 shots/embeddings；`phase2_etl_report.json` |
| 3 | Ingest、Shot、Keyframe、Proxy、Embedding、视觉/运动/音频维度、Search | PASS | 40/40 assets、20,785 shots；`phase3_full_library_acceptance.json` |
| 3 | 自研主体跟踪 fallback | PASS | 单元测试；产品 E2E 31/31 shot 落库，平均置信度 0.835 |
| 4 | Retrieval → Ranking → A/B/C → Preview → Pairwise | PASS | `tests/editing/`、真实 Review E2E 6 groups / 18 previews |
| 4 | Candidate generation 幂等、无过期组、选择可执行 | PASS | active generation、plan revision、时长门禁、跨 role 唯一性测试 |
| 4 | Candidate Precision ≥ 50% | PENDING_HUMAN | AI 委托不计人工接受；`GET /projects/{id}/kpis` 返回 insufficient_data |
| 5 | MusicMap / StyleFingerprint / DirectorPlan / Sequence Planner | PASS | `docs/phase5_*`、对应自动化测试 |
| 5 | 单段音乐形成完整短片叙事弧 | PASS | 六段 fallback arc 回归测试、25 秒产品 E2E |
| 5 | First Cut Survival / Sequence Preservation / Timing Delta | FAIL | 32.26% / 22.58% / 0%；前两项未达目标，记录为首剪质量基线 |
| 6A | 21 个 Recipe schema、实现物、真实 preview、ACCEPTANCE 文件 | PASS | `docs/phase6_recipe_technical_acceptance.json` |
| 6A | 21 个 Recipe 人工视听通过 | PASS | 所有者 `bill` 已接受 21/21；registry 21/21 `verified: true` |
| 6B | ResolveAdapter、Fusion/Color/Sound、Preview/Master、13 项 QA | PASS | Resolve 21.0.3.7；`docs/phase6_master_acceptance.json` |
| 6B | Recipe-enabled 单主题 Preview 的执行与创意初检 | PASS | `project-cc353e28978a` r16；31 clips、9 Recipe、6 audio，Resolve 100% |
| 6B | Recipe-enabled 单主题 Master 的所有者创意确认 | PASS | 所有者已锁片；r16 HEVC Master，Technical QA 13/13 |
| 7 | Structured LLM、Creative Critic、Diff Revision、选择性更新、恢复 | PASS | `docs/phase7_*`；2 clips changed、38 unchanged、1.447s |
| 7 | Revision Count / Human Effort | INSUFFICIENT_DATA | Revision Count 0；Human Effort 未采集 |
| 8 | Pairwise Ranker、修改/存活、Growth、留存回流、Profile | PASS | `tests/creative/`、`tests/growth/`、Preference Profile API |
| 8 | 真实发布 retention / Hook turnaround | PENDING_EXTERNAL_DATA | 发布指标采集路径已实现，尚无真实平台数据 |
| 9 | 六页面、真实 API、状态机、交付门禁、无需 CLI | PASS | `docs/phase9_review_ui_e2e.json`，79.951s 到真实 Resolve Preview |
| 9 | Final UI 展示技术 QA 与 KPI 证据 | PASS | `/delivery`、`/kpis`；缺数据明确显示“待采集” |
| 清理 | 删除 v1 `anime/`、`renderer/`、旧 scripts/docs；CI 禁止残留引用 | PASS | `tests/test_architecture_rules.py`；v1 可从 `v1-final` 恢复 |

## 当前唯一可执行顺序

1. 如需衡量 Human Effort，补录本次人工操作用时。
2. 发布后录入真实平台指标，形成 retention 偏好信号与 Hook 周转证据。

没有真实发布数据时，不得宣称增长 KPI 已兑现。First Cut Survival 与 Sequence
Preservation 未达目标是已记录的产品质量债务，不得改写为通过。
