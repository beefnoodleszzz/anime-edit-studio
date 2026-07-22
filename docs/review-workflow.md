# Review Workflow

## 前置安装

Review API 依赖 FastAPI/Uvicorn(在 `review` 可选依赖组),默认安装不包含:

```bash
uv pip install -e ".[review,dev]"
cd review-web && npm install && cd ..
```

## 启动(需要两个进程)

`anime review serve` 只启动后端 API,审片界面还需单独启动前端:

```bash
# 终端 1:后端 API(默认 127.0.0.1:8765,只监听本地)
anime review serve <project_id>

# 终端 2:前端 Vite(默认 http://127.0.0.1:5173,已配 /api 代理到 8765)
npm run dev --prefix review-web
```

浏览器打开 `http://127.0.0.1:5173` 即为审片台。

## 流程

1. 先创建 Brief。
2. 为项目绑定素材池。
3. 启动后端 API + 前端 Vite(见上)。
4. 在 Review Web 中逐镜头执行 use / alternate / reject。
5. 用 `I` / `O` 设入出点。
6. 生成 Blueprint。
7. 检查 Rights Report。
8. 选择 Variant，生成 `editspec.final.variant-*.json`。
9. 正式导出:`anime render projects/<id>/editspec.final.variant-*.json`(未批准素材会被拒绝;预览加 `--preview`)。

快捷键：

- `Space` 播放/暂停
- `←` `→` 切镜头
- `1` `2` `3` 写回 use / alternate / reject
- `H B S W D F C` 追加原因
- `I O` 设置入点/出点
