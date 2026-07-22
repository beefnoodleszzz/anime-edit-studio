# Reference DNA

`anime reference analyze <project_id> <video>`

输出 `projects/<project_id>/reference-dna.json`，包含：

- 镜头时长分布
- 切点
- 亮度曲线
- 色彩变化曲线
- 运动曲线
- 动静交替
- hook / ending 长度

当前版本会把 DNA 用于 Blueprint 段落时长分配，不做逐帧复刻。
