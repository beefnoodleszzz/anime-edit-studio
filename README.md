# Anime Edit Studio v2

AI 原生动漫短视频生产系统：创意决策写入可验证、可 Diff 的 EditSpec，
确定性编译器通过 DaVinci Resolve 21 Studio 完成时间线与最终渲染。

当前分支为完全重建的 `v2`。旧 Remotion 系统只保留在 `v1-final` tag，
不得把旧代码复制回 `studio/`。

## 当前进度

- Phase 0：清场与地基，完成
- Phase 1：EditSpec → validator → ResolveAdapter → 时间线 → 渲染，完成
- Phase 2：完整 EditSpec、Recipe 门禁、diff、v2 数据库、ETL、状态机，完成验收
- Phase 3：Asset Intelligence 全库增量分析，完成技术验收
- Phase 4：两阶段 Candidate Engine、A/B/C 与 Pairwise，完成技术验收
- Phase 5：MusicMap、StyleFingerprint、DirectorPlan、Sequence Planner，完成技术验收
- Phase 6：Resolve/Fusion/Color/Sound 执行与 13 项 QA 已真机跑通；21 个 Recipe 等待所有者人工视听签字
- Phase 7：结构化 Critic/Revision、两镜选择性更新与失败恢复，完成技术验收
- Phase 8：Pairwise 偏好、修订/存活信号、增长留存回流，已接入 Ranking 主链
- Phase 9：六页面 Review UI 已接真实 API、状态机和交付门禁；等待 Recipe 签字后的单主题产品级 E2E

Resolve 真实能力以
[`config/resolve_capabilities.yaml`](config/resolve_capabilities.yaml) 为唯一事实源。
SmartReframe、CreateMagicMask、SetSpeedRamp 已经真机证伪，不能进入生成链。

## 安装

```bash
cd ~/Desktop/anime-edit-studio
uv venv --python 3.11
source .venv/bin/activate
uv pip install -e ".[dev]"
```

Resolve 必须使用 Studio 21.0.3.7，并在前台运行。脚本连接环境由
`studio/execution/resolve/connection.py` 自动配置。

## 当前可运行闭环

```bash
# 环境、能力与 v2 数据库
aes doctor env
aes doctor capabilities
aes data status --json

# EditSpec
aes spec validate path/to/editspec.json --json
aes spec show path/to/editspec.json

# Resolve 时间线与预览
aes resolve build path/to/editspec.json --reset --json
aes resolve build path/to/editspec.json --incremental --json
aes resolve preview path/to/editspec.json --json

# 无需 CLI 的六页面工作台
aes review
# 浏览器打开 http://127.0.0.1:8765
# Recipe 所有者集中验收：http://127.0.0.1:8765/recipe-review.html
```

Phase 1 真机验收证明：

- 23.976 / 29.97 混合素材通过有理数时码换算，无累计漂移
- 一条命令创建工程、Bin、时间线、放置片段并渲染
- 用户手动 Resolve 操作次数为 0
- Resolve 时间线按 P10 全量重建，但 `changed_ranges` 只要求渲染变化区间

## v2 数据库

`library/engine.sqlite` 是不可变的 v1 数据源；v2 使用
`library/engine.v2.sqlite`。

真实 ETL 已核验：

- assets：40 → 40
- shots：15,901 → 15,901
- embeddings：15,901 → 15,901
- creative briefs：5 → 5
- project assets：27 → 27
- source records：39 → 39

证据见 [`docs/phase2_etl_report.json`](docs/phase2_etl_report.json)。
重新迁移必须指定一个不存在的目标文件，工具拒绝覆盖：

```bash
aes data migrate-v1 --target /tmp/engine.v2-check.sqlite --json
```

## 开发契约

进入仓库先读：

1. `WANT.md`
2. `TARGET_ARCHITECTURE.md`
3. `MIGRATION_PLAN.md`
4. `ARCHITECTURE_GAP_ANALYSIS.md`
5. `config/resolve_capabilities.yaml`

核心硬规则：

- Resolve 只能经 `ResolveAdapter`
- EditSpec 必须先通过 validator
- 未 `verified` 的能力不得生成指令
- Recipe 必须有参数 schema、实现产物、`preview.mp4` 与 `ACCEPTANCE.md`
- `studio/` 不得引用旧 `anime/`
- 时间码、缓存、校验、媒体定位等确定性逻辑不得交给 LLM

运行验收：

```bash
source .venv/bin/activate
pytest -q
npm run build --prefix review-web
```

当前逐项完成证据与剩余人工门槛见
[`docs/V2_COMPLETION_AUDIT.md`](docs/V2_COMPLETION_AUDIT.md)。
