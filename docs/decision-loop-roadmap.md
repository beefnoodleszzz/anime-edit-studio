# Decision Loop Roadmap

## 当前架构

- Python CLI 负责入库、分镜、分析、检索、编排、渲染、QA
- SQLite 负责资产与镜头元数据
- Remotion renderer 负责 EditSpec 渲染
- review-web 目前是静态原型页，未接入真实后端

## 已完成能力

- 本地视频入库与代理生成
- 分镜与关键帧提取
- 基础镜头分析
- 标签/语义检索
- 节拍分析与导演编排
- Remotion 渲染、慢镜、超分、母带、QA

## 当前断点

- 烧录字幕和水印检测仍是启发式版本
- Reference DNA 只做结构约束，不做复杂节拍拟合
- 正式渲染前仍需人工确认权利状态和终版变体

## 数据流

创作 Brief
→ 项目镜头池评分
→ 素材缺口分析
→ 推荐镜头与审片决策
→ 偏好画像/模型更新
→ 生成 cut variants
→ 人工选择终版
→ 正式渲染
→ 选片结果回灌

## 本次实施范围

- 重构 SQLite 迁移层与新表
- 实现 review / brief / gap / blueprint / preference 的本地工作站逻辑
- 实现 FastAPI Review API 与 `anime review serve`
- 删除 review-web 硬编码原型并接入真实 API
- 增加最小真实回归测试
- 修正文档和构建脚本基线

## 暂不实施范围

- 新的重量级模型
- DRM、爬虫下载器、素材再分发
- 自动 4K / 超分 / RIFE 进入蓝图阶段
- 完整的烧录字幕去除承诺

## 验收命令

```bash
uv run pytest
npm run build --prefix review-web
npm run build --prefix renderer
uv run anime brief create demo --character gojo --theme awakening --emotion intense --duration 25 --aspect 4:5 --platform douyin --json
uv run anime gap demo --json
uv run anime blueprint demo --json
```
