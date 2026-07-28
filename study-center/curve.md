DaVinci Resolve 动漫 Curve 推拉运镜完整操作指南
适用类型：动漫人物 Edit、Beat Sync、Flow Edit、Push-Pull、Swing Motion
推荐画幅：1080×1080，1:1
核心原则：先锁死鼓点切点，再制作镜头内部运动；运动可以提前，Cut 不得偏离鼓点。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
一、先理解 Curve 推拉的结构
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

一个镜头不能只是：

静止 → 鼓点 → 切镜

正确结构应该是：

切入后快速运动
→ 快速减速
→ 中间短暂稳定
→ 下一鼓点前重新加速
→ 鼓点处切镜
→ 新镜头继承运动

可以概括成：

FAST → EASE OUT → SETTLE → ANTICIPATION → CUT

Curve 推拉不是单纯改变 Zoom，而是同时动画：

1. Zoom / Scale：前后推拉
2. Position X/Y：左右或上下移动
3. Rotation：轻微旋转
4. Curve / Spline：控制速度变化
5. Motion Blur：解释快速运动

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
二、项目设置
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

推荐设置：

Timeline Resolution：1080×1080
Timeline Frame Rate：60 FPS
Playback Frame Rate：60 FPS

电脑性能不足时可以用 30 FPS，操作逻辑完全一样。

帧数计算公式：

每拍帧数 = 时间线帧率 × 60 ÷ BPM

例如：

112 BPM，60 FPS：
60 × 60 ÷ 112 ≈ 32 帧/拍

112 BPM，30 FPS：
30 × 60 ÷ 112 ≈ 16 帧/拍

注意：

60 FPS 时间线不代表必须把动漫素材全部补帧。
原动漫可以保持重复帧，人工制作的 Zoom、Position 和 Rotation 仍会以 60 FPS 运动。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
三、建立严格的 Beat Grid
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. 把 BGM 放到时间线。
2. 只播放音乐，不放视频。
3. 听到需要切镜的主要鼓点时按 M。
4. 连续标记至少 8 个鼓点。
5. 打开 Snapping，快捷键 N。
6. 将每个视频镜头边界吸附到 Marker。

结构必须先变成：

M1        M2        M3        M4
│ Shot A │ Shot B │ Shot C │ Shot D │

第一遍只允许 Hard Cut。

禁止添加：

- 闪白
- Glitch
- Wipe
- 转场包
- 抖动
- Motion Blur
- 复杂 Fusion 效果

先验证每个鼓点是否真的发生一次素材切换。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
四、Edit 页面入门做法
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

适合先理解原理，不需要进入 Fusion。

1. 选中第一个镜头。
2. 打开右上角 Inspector。
3. 找到 Video → Transform。
4. 为以下参数打开关键帧：

- Zoom
- Position X
- Position Y
- Rotation Angle

假设一个镜头长度为 32 帧，60 FPS。

设置五个时间位置：

第 0 帧：切入状态
第 7 帧：快速减速结束
第 17 帧：稳定状态
第 24 帧：开始向下一刀运动
第 31 帧：切点前最大速度
第 32 帧：切到下一镜头

第一个镜头可以这样设置：

Frame 0
Zoom：1.10
Position X：-45
Position Y：0
Rotation：-0.4°

Frame 7
Zoom：1.045
Position X：-5
Position Y：0
Rotation：-0.05°

Frame 17
Zoom：1.04
Position X：0
Position Y：0
Rotation：0°

Frame 24
Zoom：1.045
Position X：+5
Position Y：0
Rotation：+0.05°

Frame 31
Zoom：1.09
Position X：+45
Position Y：0
Rotation：+0.4°

形成：

左侧切入
→ 快速回到中心
→ 中间稳定
→ 向右重新加速
→ 鼓点切镜

第二个镜头反向：

Frame 0
Zoom：1.10
Position X：+45
Rotation：+0.4°

Frame 7
Zoom：1.045
Position X：+5
Rotation：+0.05°

Frame 17
Zoom：1.04
Position X：0
Rotation：0°

Frame 24
Zoom：1.045
Position X：-5
Rotation：-0.05°

Frame 31
Zoom：1.09
Position X：-45
Rotation：-0.4°

形成：

Shot A：向右
Shot B：向左
Shot C：向右
Shot D：向左

也就是：

→ ← → ← → ←

这就是基础 Swing / Sway Motion。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
五、打开 Curve Editor
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

只打关键帧还不够，默认线性运动通常会像 PPT。

1. 展开时间线中的关键帧区域。
2. 点击镜头右下方的 Curve 图标。
3. 在参数菜单中显示：

- Zoom
- Position X
- Position Y
- Rotation

4. 选中对应关键帧。
5. 改成 Smooth、Ease In 或 Ease Out。
6. 调整 Bezier Handle。

第一段曲线：

Frame 0 → Frame 7

要求：

开始速度很快
结束速度接近零

曲线逻辑：

陡峭 → 逐渐变平

第二段：

Frame 7 → Frame 17

要求：

变化非常小
接近稳定

第三段：

Frame 17 → Frame 24

要求：

慢慢启动

第四段：

Frame 24 → Frame 31

要求：

越接近切点越快

曲线逻辑：

平缓 → 逐渐陡峭

最终速度结构：

快 ─── 慢 ─── 稳 ─── 慢 ─── 快
│                                   │
镜头切入                         下一切点

注意：

不要只观察参数数值。
真正决定丝滑度的是曲线斜率，也就是速度。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
六、30 FPS 参数换算
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

假设每拍约 16 帧：

Frame 0：切入最大运动
Frame 4：快速减速
Frame 8：稳定
Frame 11：开始 anticipation
Frame 15：切点前最大速度
Frame 16：切镜

参数幅度可以保持不变，时间位置缩短一半。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
七、Fusion 推荐做法
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

熟悉基础逻辑后，建议改用 Fusion。

节点结构：

MediaIn
   ↓
Transform
   ↓
MediaOut

操作：

1. 选中镜头。
2. 打开 Fusion 页面。
3. 选中 MediaIn。
4. 按 Shift+Space。
5. 搜索 Transform。
6. 添加 Transform 节点。
7. 连接：

MediaIn → Transform → MediaOut

Transform 中主要控制：

Center：位置
Size：缩放
Angle：旋转
Pivot：旋转和缩放中心

Fusion 参数采用归一化坐标：

画面中心：
Center X = 0.5
Center Y = 0.5

向左移动：
Center X = 0.465

向右移动：
Center X = 0.535

基础参数：

Frame 0
Center：0.465, 0.5
Size：1.10
Angle：-0.4

Frame 7
Center：0.495, 0.5
Size：1.045
Angle：-0.05

Frame 17
Center：0.5, 0.5
Size：1.04
Angle：0

Frame 24
Center：0.505, 0.5
Size：1.045
Angle：0.05

Frame 31
Center：0.535, 0.5
Size：1.09
Angle：0.4

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
八、Fusion Spline 曲线
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. 点击 Fusion 页面右上角 Spline。
2. 勾选：

- Transform.Center
- Transform.Size
- Transform.Angle

3. 框选所有关键帧。
4. 点击 Smooth。
5. 调整关键帧两侧的 Bezier Handle。

目标：

第一段：
高初速度 → 慢慢停稳

中段：
只有轻微运动

最后一段：
缓慢启动 → 切点前高速

不要把整条曲线调成一个普通 S 形。

一个镜头实际上需要两个速度阶段：

第一段：Ease Out
最后一段：Ease In

也就是：

切入时快速减速
切出前快速加速

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
九、Motion Blur
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

在 Fusion Transform 节点中开启 Motion Blur。

建议起始值：

Motion Blur：开启
Shutter Angle：90°–180°
Quality：中等或较高

使用原则：

小幅慢速运动：
不需要明显 Motion Blur

高速 Whip：
可以提高到 180°附近

不要用 Motion Blur 掩盖错误的 Curve。

正确顺序：

先把 Curve 调顺
→ 再加 Motion Blur
→ 最后检查人物五官和线条是否糊掉

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
十、跨镜头运动怎样连接
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

弱拍：使用同向运动

Shot A 切点前向右加速
Shot B 切入后继续向右运动

效果：

→ CUT →

观感是运动连续，几乎感觉不到切镜。

强拍：使用反向运动

Shot A 切点前向右
Shot B 切入后向左

效果：

→ CUT ←

观感是回弹、撞击、拉扯。

推荐规则：

普通拍：同向延续
重拍：反向反弹
Drop：强 Push In 或 Whip
旋律收尾：Pull Out 或突然停止

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
十一、四种实用运动模板
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

模板 A：左右 Swing

Shot 1：左 → 中 → 右
Shot 2：右 → 中 → 左
Shot 3：左 → 中 → 右
Shot 4：右 → 中 → 左

适合：
人物展示、重复旋律、稳定鼓点。

模板 B：Push-Pull

Shot 1：
Size 1.04 → 1.12

Shot 2：
Size 1.12 → 1.04

Shot 3：
Size 1.04 → 1.10

Shot 4：
Size 1.12 → 1.03

适合：
眼睛、脸部、觉醒、角色 Aura。

模板 C：Whip Flow

切点前 5–8 帧：

Position X：
0 → +80

新镜头切入：

Position X：
-80 → 0

同时增加：

Size：1.12 → 1.04
Rotation：-0.7° → 0°
Motion Blur：开启

适合：
强鼓点、Drop、高速动作。

模板 D：Push + Rotation

Frame 0：
Size 1.10
Angle -0.6°

中间：
Size 1.04
Angle 0°

切点前：
Size 1.09
Angle +0.5°

下一镜头反向。

适合：
需要明显拉扯感，但不能频繁使用。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
十二、高级做法：双层运动
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

高端 Flow Edit 通常不是只有一层 Transform。

第一层：镜头内部构图

作用：

- 把人物放到正确位置
- 修正每个素材不同的构图
- 保证眼睛、脸和动作主体清晰
- 让不同镜头主体位置接近

第二层：整段全局 Curve

作用：

- 让运动跨越多个 Cut
- 建立连续的虚拟摄影机运动
- 防止每个镜头都独立开始和结束

操作：

1. 先完成 8 个鼓点的 Hard Cut。
2. 调整每个镜头的基础构图。
3. 选中这 8 个镜头。
4. 右键 → New Compound Clip。
5. 在 Compound Clip 上进入 Fusion。
6. 添加一个 Transform 节点。
7. 根据每个鼓点位置制作连续 Center、Size 和 Angle 曲线。

结构：

原始镜头 Transform：
负责人物构图

Compound Clip Transform：
负责整段 Flow

这种方法比给每个镜头单独套完全相同的动画更自然。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
十三、1:1 动漫素材参数建议
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

16:9 素材裁成 1:1 后，横向通常有较大移动空间。

建议范围：

普通推拉：
Zoom / Size：1.03–1.10

强拍推拉：
Zoom / Size：1.08–1.16

普通横移：
Position X：±20 至 ±50

Whip：
Position X：±60 至 ±120

普通旋转：
±0.2° 至 ±0.6°

强拍旋转：
最多约 ±1°

长期超过 1°容易产生廉价模板感。

使用 Rotation 或 Position Y 时，注意上下边缘可能露黑。
可以将基础 Zoom 保持在 1.05–1.10。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
十四、8 拍练习模板
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Beat 1：
向右 Swing + 轻微 Push In

Beat 2：
向左 Swing + 轻微 Pull Out

Beat 3：
向右 Swing + Push In

Beat 4：
向左 Whip + 强 Cut

Beat 5：
向右平滑进入

Beat 6：
向左 Pull Out

Beat 7：
正面 Push In，不横移

Beat 8：
强 Push + Impact Cut

运动方向：

→ ← → ← → ← IN IMPACT

每个鼓点必须换一个素材镜头。

练习阶段禁止使用现成转场。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
十五、常见失败原因
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

问题 1：画面像 PPT 平移

原因：

- 关键帧是 Linear
- 开头和结尾速度相同
- 没有 Ease
- 没有 Motion Blur

解决：

- 打开 Curve / Spline
- 切入后快速减速
- 切点前快速加速
- 高速段增加适量 Motion Blur

问题 2：鼓点准确，但画面仍然没音乐感

原因：

- 运动在鼓点之后才启动
- Cut 对齐了，但动作 Peak 没对齐
- 每个镜头中间完全静止

解决：

- 下一鼓点前 3–8 帧开始 anticipation
- 把拔刀、抬眼、击中等动作 Peak 对准鼓点
- 保留轻微的持续运动

问题 3：看起来晕

原因：

- Zoom 幅度过大
- 每拍都反向
- Rotation 太强
- Position、Zoom、Rotation 同时达到最大值

解决：

- 普通拍只使用一种主要运动
- Rotation 控制在 ±0.6°以内
- 每 4 拍安排一次明显强运动
- 不要每拍都做 Whip

问题 4：露黑边

原因：

- 基础 Scale 太小
- Rotation 或 Position Y 幅度过大

解决：

- Size 提高到 1.06–1.12
- 减少垂直移动
- 播放检查所有运动极值位置

问题 5：每个镜头都很丝滑，但整段很机械

原因：

- 每个镜头使用完全相同的参数
- 永远是左、右、左、右
- 没有强弱拍区别

解决：

- 普通拍小幅运动
- 重拍大幅运动
- 每四拍改变一次运动类型
- Push、Pull、Swing、Whip、Hold 交替使用

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
十六、最终检查标准
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

逐项检查：

[ ] 每个目标鼓点都有明确 Cut
[ ] Cut 与 Marker 误差不超过 1 帧
[ ] 切点前已经开始加速
[ ] 新镜头切入后仍有运动惯性
[ ] 开头不是线性运动
[ ] 中段有短暂稳定区
[ ] Position 和 Zoom 服务于同一运动方向
[ ] Rotation 没有超过合理幅度
[ ] Motion Blur 只在高速段明显
[ ] 没有黑边
[ ] 人物眼睛和脸没有被裁坏
[ ] 连续四拍的运动强度不是完全相同
[ ] 强拍和弱拍有明确区别
[ ] 关闭声音后，运动本身仍然流畅
[ ] 只听音乐时，所有 Cut 都能被预判到

核心公式：

Beat Grid
+ Hard Cut
+ Anticipation
+ Position
+ Scale
+ Rotation
+ Ease Curve
+ Motion Blur
+ Motion Matching
= Anime Curve Flow Edit