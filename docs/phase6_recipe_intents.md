# Phase 6 Recipe 视觉与声音意图清单

本文件只记录旧系统表现出的创作意图，不迁移 `renderer/` 实现。Resolve/Fusion
产物必须独立重建，并逐项满足 `recipe.yaml + artifact + preview.mp4 +
ACCEPTANCE.md` 后才允许在 `config/recipes.yaml` 标为 `verified`。

## Fusion Effect / Transition

| Recipe | 视觉意图 | 参数边界 | 样张验收重点 |
|---|---|---|---|
| `impact_shake_v1` | 命中点短促、衰减的二维震动，不形成持续晃动 | strength 0–1, duration 2–8 帧 | 首帧冲击清晰；末帧归零；边缘不露黑 |
| `anime_glow_v1` | 高亮区域柔和 bloom，保留线稿与肤色 | strength 0–1, radius 4–30 | 高光扩散但线条不糊、不过曝 |
| `white_flash_v1` | 命中点白闪后快速恢复 | strength 0–1, duration 2–6 帧 | 峰值可见；恢复无残留；不整段发白 |
| `rgb_split_impact_v1` | 命中点横向 RGB 色差后归零 | strength 0–1, pixels 0–16 | 通道分离清晰；非命中帧完全对齐 |
| `speed_flash_v1` | 速度感方向线/亮度扫光 | strength 0–1, angle | 不遮主体面部；方向与动作一致 |
| `camera_punch_v1` | 3–5 帧冲击变焦并回落 | strength 0–1 | 不露边；回到原构图；无呼吸式循环 |
| `motion_blur_transition_v1` | 同方向甩出/甩入，模糊峰值跨切点连续 | direction, strength 0–1, duration 4–12 帧 | A/B 运动方向连续；切点不出现清晰断层 |
| `eye_focus_v1` | 眼部局部放大、锐化、轻微辉光 | strength 0–1, center x/y | 眼睛保持清晰；肤色与轮廓不过度锐化 |
| `timespeed_v1` | 可重复的 TimeSpeed / TimeStretcher 时间重映射 | entry/impact/exit speed, impact time | 源时间映射正确；片长正确；命中帧不跳 |
| `subject_transform_v1` | 消费 Phase 3 主体轨迹，逐帧 Pan/Zoom 保持 4:5 构图 | track id, smoothing, max zoom | 主体不出框；无抖动；低置信轨迹安全回退 |

## Color

Color Recipe 架构已由 Phase 2.0 真机探测冻结为“一项 Recipe 对应一个
ColorGroup post-clip grade”。任何 LUT/DRX 必须在 Resolve 注册或导入后，用同一素材
输出前后对照。

| Recipe | 意图 |
|---|---|
| `anime_clean_v1` | 中性、干净、线稿清晰，作为安全基线 |
| `anime_high_contrast_v1` | 提升明暗分离但保住黑位细节和肤色 |
| `anime_cold_v1` | 冷青环境，肤色不发灰 |
| `anime_fire_v1` | 暖橙火焰与红色冲击，高光不剪切 |
| `anime_night_blue_v1` | 夜景蓝调，暗部角色仍可辨 |
| `red_impact_v1` | 极短红色命中强调，只用于 impact budget 内 |

## Sound

Fairlight 音量自动化在 Phase 2.0 已判定无可用脚本接口，因此 Sound Recipe 先以
可审计 WAV 资产和动作映射建库；在找到 verified 的时间线放置/混音路径前保持
`unverified`。

| Recipe | 声音意图 | 验收 |
|---|---|---|
| `sword_whoosh_v1` | 高频方向性挥砍 | 无削波；起音与动作前缘对齐 |
| `impact_low_v1` | 低频命中主体 | 峰值受控；手机扬声器仍可感知 |
| `sub_impact_v1` | 主 impact 下方的短 sub | 不掩盖对白与主拍 |
| `riser_v1` | build 到 drop 的张力上升 | 结尾精确落在 impact 前 |

动作映射只能输出 Recipe id 与参数：

```yaml
sword: [sword_whoosh_v1, impact_low_v1]
punch: [impact_low_v1, sub_impact_v1]
build: [riser_v1]
```
