# 优化 TODO(按价值/性价比,已做的不再列)

> 已完成:M1–M7 全链、agent-reach 取材、signalstats 逐帧亮度、镜头级渲染缓存+代理、
> WD-Tagger 动漫打标(角色名+属性,替掉没用的通用 VLM)、slot z-score+WD 标签信号、
> 中文↔罗马音检索别名、能量驱动情绪弧、峰值 hero 慢镜自动 RIFE、能量对齐下拍、
> 缓存(shots/analyze/embed/slots/render)、偏好学习 pick、音频末级限幅。

## 观感深化(补最后的"AE 级"差距)

- [ ] **线性光合成(OCIO/ACES)** —— 辉光/爆发在线性空间算才物理正确(charter 要求);现在是 sRGB 空间 CSS/SVG。
- [ ] **真矢量运动模糊** —— 甩镜/快速运镜现在是 CSS 均匀模糊;换 @remotion/motion-blur 或按光流做。
- [ ] **逐帧视频遮罩** —— 特效藏人物后在运动镜头上才准(现在静态中间帧会漂)。rembg/SAM2 逐帧 matte 视频。
- [ ] **GLSL 合成器再战** —— `useOffthreadVideoTexture` 无头不出图;试离屏 canvas 手动喂帧,解锁真 bloom/能量合成。

## 声音深化

- [ ] **Demucs 分离真鼓点** 强化卡点(比合成 impact 更音乐化)。
- [x] **原声层** —— 按 EditSpec 入出点/速度提取源素材，形成可控原声床并与 BGM/SFX ducking 混合。
- [ ] **Rubber Band** 变速保调,把 BGM 对齐目标时长。

## 选片/编排质量(护城河)

- [x] **美学/质量打分** —— LAION aesthetic + 清晰度、构图、风险与 brief 匹配已进入检索/决策评分。
- [x] **成片全局视觉近重复门禁** —— `quality audit --visual` 用中点帧 dHash 检测近重复；全库离线聚类仍可继续深化。
- [x] **偏好与增长双闭环** —— variant select 回灌 picked/偏好模型，真实留存回灌 growth_score。

## 工作流/闭环

- [ ] **变体 A/B 审片** —— rhythm/情绪/flow/arc 四版并排对比,帮选版。
- [x] **多维发布实验与因果回灌** —— 文案×首镜×音乐入点矩阵、完整留存曲线、镜头跌落归因、因子洞察、授权 CSV 导入及 growth_score 回灌。
- [ ] **平台私有 API 自动同步** —— 仅在取得正式 API 授权后接入；当前不接管账号凭据，使用平台授权导出的 CSV。
- [x] **核心回归测试** —— 决策闭环、素材库、实验归因、增强门禁与 Review API 已覆盖；重型真实渲染保留人工/机器验收。
- [ ] **渲染再提速** —— 首渲仍 ~8min;可跨进程并行渲镜头(缓存已解决重渲)。
