# Anime Edit Studio

Demo 驱动的 AMV 生成引擎。

输入一段 Demo 视频、目标音乐和一批动漫素材，系统自动分析 Demo 的剪辑语言（镜头节奏、
运镜语言、转场手法），按目标音乐重新计算节奏结构，从素材库自动选镜，在 DaVinci
Resolve 中生成具有连续音乐律动和跨镜头后期运镜的 AMV。

唯一产品用例：Demo + 素材(+ 音乐) → 分析 → 规划 → AMVSpec → Resolve 渲染 → QA。

## 安装

```bash
cd ~/Desktop/anime-edit-studio
uv venv --python 3.11
source .venv/bin/activate
uv pip install -e ".[dev]"
# 语义检索 / 动漫打标需要的重模型能力（可选）：
uv pip install -e ".[ml]"
```

Resolve 必须使用 Studio 21.0.3.7，并在前台运行。脚本连接环境由
`studio/execution/resolve/connection.py` 自动配置，无需手工 `export`。

## 使用

```bash
# 环境自检
aes doctor env
aes doctor capabilities
aes doctor assets

# 增量索引一批素材（ingest → shot 检测 → 分析，幂等）
aes library index /path/to/materials

# 生成 AMV：Demo + 素材(+ 音乐) → AMVSpec → Resolve 预览 → QA
aes amv create --project my-amv --demo demo.mp4 --materials /path/to/materials --music track.wav

# 通过 QA 硬门禁后，显式发布
aes amv release --project my-amv
```

`aes amv create` 输出 `projects/<project-id>/` 下的 `amv_spec.json`、`preview.mov`
和 `qa.json`；`aes amv release` 只有在 `qa.json` 显示通过时才会把 `preview.mov`
复制为 `release.mov`。

Resolve 真实能力以 [`config/resolve_capabilities.yaml`](config/resolve_capabilities.yaml)
为唯一事实源——`verified: false` 的能力不会出现在生成链里。

## 架构与开发契约

- [`AGENTS.md`](AGENTS.md) —— 不可违反的工程规则，AI Agent 和人类开发者都先读这个
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) —— 模块边界与数据流
- [`docs/OPERATIONS.md`](docs/OPERATIONS.md) —— 运行、排障、Resolve 真机验收

## 测试

```bash
source .venv/bin/activate
pytest -q -m "not requires_resolve"          # 离线测试
pytest -q -m requires_resolve                # 需要本机运行 DaVinci Resolve
```
