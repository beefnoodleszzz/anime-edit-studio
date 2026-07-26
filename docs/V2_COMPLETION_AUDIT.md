# Anime Edit Studio v2 完成审计

更新时间：2026-07-26

本文件只记录可复核证据。技术通过不代替人工创意验收。
逐任务状态见 `docs/V2_REQUIREMENTS_MATRIX.md`。

## Phase 2–5

- Phase 2：EditSpec v2、validator、diff、migration、v2 SQLite 与状态机均有自动化测试；
  ETL 证据为 `docs/phase2_etl_report.json`。
- Phase 3：素材全库增量分析证据为 `docs/phase3_full_library_acceptance.json`。
- Phase 4：召回、Contextual Ranking、A/B/C、预览和 Pairwise 选择由
  `tests/editing/test_candidate_engine.py` 与 Review API 测试覆盖。
- Phase 5：MusicMap、StyleFingerprint、DirectorPlan 和 Sequence Planner 分别有单元测试；
  真实制品证据为 `docs/phase5_*_acceptance.json`。

## Phase 6

- Resolve 25 秒 Master：H.265、1080×1350、24000/1001 fps、599 帧、
  −14 LUFS，Technical QA 13/13；证据为 `docs/phase6_master_acceptance.json`。
- Fusion/Color/Sound 共 21 个 Recipe 均具备 schema、实现产物、真实 preview
  和 ACCEPTANCE 文件；技术清单为 `docs/phase6_recipe_technical_acceptance.json`。
- 所有者 `bill` 已通过 Review UI 接受 21/21 Recipe；对应 ACCEPTANCE 与
  `config/recipes.yaml` 已原子更新，21/21 均为 `verified: true`。
- r16 Resolve Preview 实际执行 9 次 Recipe（6 次 Fusion/3 个 ColorGroup）
  并写入 6 条音频。Color 执行使用已验证的 Group PostClip LUT 路径；DRX
  继续作为 R4 归档产物，不冒充未验证的 `ApplyGradeFromDRX`。

## Phase 7

- 自然语言反馈通过结构化 schema 转为 EditSpec Diff。
- 验收句只更新 2 个 clip，38 个 clip 不变，变化区间共 1.447 秒；
  证据为 `docs/phase7_revision_acceptance.json`。
- LLM 调用、Revision 尝试、失败与版本链均持久化；Technical QA 与 Creative
  Review 使用不同数据类型和表记录。

## Phase 8

- A/B/C 选择写入 winner/loser，训练项目级与全局 Bradley–Terry 模型。
- First Cut 读取已训练模型并把偏好作为 10% contextual signal，不作为硬规则。
- Revision 记录 replacement/timing/effect/audio/reframe，锁片记录 final survival。
- Hook A/B、发布指标、逐镜留存跌幅可回流为低权重 Pairwise signal。
- 项目偏好 Profile 有只读 API：`GET /projects/{id}/preference-profile`。

## Phase 9

- 六页面：Project / Reference / Candidates / First Cut / Revision / Final。
- 上传、分析、候选选择、AI 决定、初剪、Revision、锁片、Master、QA、下载均连接真实 API。
- 页面不再使用演示候选或虚构分析数字；无真实制品时明确显示 pending。
- Workflow State Machine 已接入路由；只有 13 项 Technical QA 全绿可进入 DELIVERED。
- 前端生产构建通过，Playwright 已检查六页面与 390px 移动布局。
- 全新项目 `project-cc353e28978a` 已完全经 Review HTTP API 跑过创建、音乐上传、
  Prepare、6 组 AI 委托选择、First Cut、Resolve Preview 和状态机，不要求用户 CLI；
  首帧预览总耗时 79.951 秒，证据为 `docs/phase9_review_ui_e2e.json`。
- AI 委托选择与人工接受已分开统计，不能再用 AI 自动选择虚增 Candidate Precision。
- Candidate Group 已有 active generation 与 plan revision；重复规划不再把历史组混入 UI/KPI。
- 候选入口先校验可用时长并禁止跨 role 重复选择，避免选择后 Sequence Planner 才失败。

## 单主题产品 E2E（技术路径）

- 项目：`projects/product-e2e-tanjiro`，主题角色 `kamado_tanjirou`，25 秒。
- 当音乐前 25 秒只有一个超长 intro 时，DirectorPlan 会确定性降级为完整六段叙事弧：
  opening / buildup / pre_drop / impact / release / ending；回归测试覆盖该场景。
- 生成 6 个 Candidate Group、31 个 clip；实际入选的 12 个 asset 均来自
  `anime-material-library/sources/kimetsu/`，不存在跨作品素材污染。
- Resolve 21.0.3.7 实机重建成功：31/31 clip、31 marker、1 条音乐轨，
  changed range 为 0–25 秒，用户 Resolve 手工操作为 0。
- 实机预览：
  `projects/product-e2e-tanjiro/renders/product-e2e-tanjiro-r2-resolve-execution-2.1.0-preview.mov`；
  H.264、1080×1350、24000/1001 fps、AAC、25.109 秒，Resolve 渲染状态 100%。
- 抽帧人工初检确认均为《鬼灭之刃》炭治郎相关画面；该检查不替代最终所有者创意验收。
- 对 31 个实际入选镜头运行了自研主体跟踪：31/31 落库，平均置信度 0.835，
  冷缓存耗时 106.63 秒；动态/静态偏移在视觉确认前没有擅自写入 EditSpec。
- 本次预览 `recipes_applied=0`：这是 capability gate 的正确行为，不能在 Human
  status 仍为 `PENDING` 时绕过门禁。

## Recipe-enabled 产品 E2E（当前待锁片版本）

- 项目：`projects/project-cc353e28978a`，单主角炭治郎，25 秒。
- Recipe 验收后首次真实执行暴露并修复两项确定性缺陷：增量 fingerprint 原先
  未覆盖 effects/color/audio；Color DRX 调用超出已验证能力。两者均有回归测试。
- 候选正式门新增字幕标签、伪截图、parody/chibi、多人污染、人物可见度与姿态门禁；
  `review_decisions=reject` 会在项目级重排时确定性排除。
- Sequence Planner 以实际获评分的五帧代表图为中心取源区间，不再默认从场景起点
  截取；并保留角色锚点、惩罚跨片视觉重复。该修复消除了标题卡、黑帧和场景边界误取。
- 当前 r16 已由所有者锁片：31 clips、31 markers、9 Recipe、6 audio；Resolve
  Master 为 HEVC 1080×1350、24000/1001 fps、AAC、600 帧、25.025 秒。
- Master：
  `projects/project-cc353e28978a/renders/project-cc353e28978a-r16-resolve-execution-2.1.0-master.mov`。
- Technical QA 13/13：无黑帧、丢帧、损坏或异常静音，−14.6 LUFS；工作流状态
  已进入 `DELIVERED`。
- QA 修复以视频流时长为画面权威，避免 AAC packet 尾部误报；冻结帧只豁免
  EditSpec 中有低运动分析证据的镜头范围，范围外最长冻结 0.501 秒。
- 锁片 KPI：First Cut Survival 32.26%、Sequence Preservation 22.58%（均未达
  目标，反映初始候选质量确有不足）；Timing Delta 0%、Resolve 手工操作 0。

## 自动化证据

- `pytest -q`：168 passed（2026-07-26）。
- `npm --prefix review-web run build`：TypeScript 与 Vite production build 通过。
- Recipe 人工验收台：`aes review` 后访问 `/recipe-review.html`；集中展示 21 个
  preview，只有明确填写验收人并点击接受/拒绝才会原子更新 ACCEPTANCE 与 registry。

## 删除与架构守卫

- v1 `anime/`、Remotion `renderer/`、旧 scripts/config 和七篇旧闭环文档已删除。
- v1 仍可从 tag `v1-final` 恢复；素材库、v2 DB、Recipe 与验收证据未删除。
- CI 守卫禁止 `studio/` 引用 v1，禁止 Resolve API 越过 Adapter，禁止未验证能力进入执行。

## 尚未满足

1. Candidate Precision 仅允许人工 A/B/C 接受计数，本项目为 AI 委托，故保持
   `insufficient_data`。
2. Human Effort 尚未录入。
3. 真实发布 retention / Hook turnaround 尚无外部数据。

完成这三项之前，不得宣称 Phase 2–9 全部最终验收完成。
