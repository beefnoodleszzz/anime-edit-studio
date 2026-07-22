# anime-edit-studio

本地智能选片、素材缺口分析、个人审美学习和剪辑蓝图工作站。

当前主闭环：

`Brief → Shot Scoring → Gap Analysis → Review Decisions → Preference Learning → Blueprints → Variant Select → Render`

原则：

- 本地优先，适配 Apple Silicon
- 免费和开源组件优先
- 不依赖付费云 API
- Review Web 通过本地 FastAPI 提供真实后端
- 所有 CLI 保持 `--json` 输出能力

安装：

```bash
uv venv --python 3.11 && source .venv/bin/activate
uv pip install -e ".[review,dev]"
cd review-web && npm install && cd ..
cd renderer && npm install && cd ..
```

---

## 目录组织

```text
anime-edit-studio/
├── config.toml          # 工具路径 + 交付规格(已按本机填好)
├── pyproject.toml       # 自研 CLI 包(命令: anime)
│
├── anime/               # ① AI 剪辑总监层(自研 Python,护城河)
│   ├── cli.py           #   Typer 入口: anime <命令>
│   ├── config.py        #   读 config.toml
│   ├── db.py            #   SQLite 镜头库(assets/shots + FTS5 全文检索)
│   ├── cache.py         #   内容哈希缓存
│   ├── ingest.py        #   ffprobe + sha256 + 1080p 代理(VideoToolbox)
│   ├── shots.py         #   PySceneDetect 分镜 + 关键帧 + contact sheet
│   ├── analyze.py       #   亮度/清晰度/运动方向(+可选 whisper 台词)
│   ├── search.py        #   全文/过滤/综合排序 → 带时间码候选
│   ├── editspec.py      #   EditSpec 数据契约(pydantic),Python↔Remotion
│   ├── render.py        #   调 Remotion 渲染 EditSpec
│   ├── enhance.py       #   RIFE 慢动作 / Real-ESRGAN 超分(选择性,带缓存)
│   └── qa.py            #   ffmpeg 校验(规格/黑帧/响度) → report.json
│
├── renderer/            # ② 合成器(Remotion / TS / React)
│   └── src/
│       ├── Root.tsx     #   读 EditSpec 定义 Composition
│       ├── Edit.tsx     #   主时间线: 镜头序列 + 变换 + 特效 + 音频
│       ├── effects/     #   特效层(M1: CSS/SVG;M3 升级 GLSL/@remotion/three)
│       └── schema.ts    #   EditSpec TS 类型(与 pydantic 对齐)
│
├── library/             # ③ 全局素材库(跨项目,git 忽略)
│   ├── engine.sqlite    #   镜头库数据库
│   ├── assets/  proxies/  keyframes/  cache/
│
├── kit/                 # ④ 签名创作资产(可复用)
│   ├── luts/            #   调色 .cube        overlays/  漏光粒子纹理   sfx/  音效包
│
├── projects/            # ⑤ 每条作品一个项目
│   └── <id>/  project.toml  editspec.json  outputs/
│
└── refs/                # ⑥ 竞品参考(拆解用)
```

数据流:`ingest → shots → analyze`(入库)→ `brief`/`gap`/`blueprint`/`review`(决策闭环)→ `render`(Remotion)→ `qa`。

---

## 安装

```bash
cd ~/Desktop/anime-edit-studio
uv venv --python 3.11 && source .venv/bin/activate
uv pip install -e ".[review,dev]"   # Review API(FastAPI/Uvicorn)+ 测试(pytest)
# 前端 + 合成器(需要时)
cd review-web && npm install && cd ..
cd renderer && npm install && cd ..
```

> `uv pip install -e .` 只装核心依赖,**不含** FastAPI/Uvicorn(在 `review` 组)和 pytest(在 `dev` 组)。
> 要跑 `anime review serve` 或 `uv run pytest`,必须装 `.[review,dev]`,否则会 `ModuleNotFoundError: No module named 'uvicorn'`。

## 当前最小闭环

```bash
anime ingest  <视频文件>
anime shots   <asset_id>
anime analyze <asset_id>
anime brief create <project_id> --character gojo --theme awakening --emotion intense --duration 25 --aspect 4:5 --platform douyin
anime project attach <project_id> <asset_id_1,asset_id_2>
anime gap <project_id>
```

先审片(设 use/alternate/reject 与入出点),再生成蓝图。启动 Review Web **需要两个进程**:

```bash
# 终端 1:本地 Review API(默认 127.0.0.1:8765)
anime review serve <project_id>
# 终端 2:审片前端(Vite,默认 5173,已配 /api 代理到 8765)
npm run dev --prefix review-web
```

审片完成后生成蓝图、选终版并正式导出:

```bash
anime blueprint <project_id>
anime variant select <project_id> <variant_id>   # 选终版时回灌最新入出点与决策
anime rights-report <project_id>
# 正式导出强制校验素材权利:按 shot.id 从库里重解析可信母版,未批准素材会被拒绝;调试用 --preview
anime render projects/<project_id>/editspec.final.variant-<variant_id>.json
```

> 入出点在 Review Web 里改后,`variant select` 会按最新 trim 重算 Final EditSpec 的入点与时长,无需重跑蓝图。

## 状态(M1–M4 全部完成,已在真实五条悟素材上端到端验证)

- ✅ **M1** `ingest / shots / analyze / search / assemble / render / qa` —— 端到端自适应画幅/60fps
- ✅ **M2** `beat` + `roughcut` —— 节拍驱动多版本(节奏/情绪/视觉流),切点落拍、附速度+特效
- ✅ **M3** `EffectStack` —— 调色 + 真辉光(blur+screen)+ 色差(SVG chromatic aberration)+ 暗角,确定性 DOM/SVG/CSS,4K/60fps 520 帧≈48s
- ✅ **M4a** `master` —— EBU R128 两遍 loudnorm(-14 LUFS)+ 保持原画幅的平台版导出
- ✅ **M4b** `slowmo` —— RIFE 光流插帧,慢镜顺滑(闪切镜头自动跳过)
- ✅ **M4c** `embed` + 语义检索 —— 本地 CLIP(ViT-B-32/MPS),`search "man with white hair"` → 命中五条悟
- ✅ **M4d** `matte` —— rembg(isnet-anime)主体遮罩 + EffectStack 主体高亮
- ✅ **M5 运动设计与观感(完成)**:
  - 动画运镜(Ken Burns 推/拉/摇,`useCurrentFrame` 驱动)
  - 转场(甩镜 whip+过扫描遮黑边 / 缩放模糊 / 闪切)
  - **速度曲线 ramp**(`RampVideo`:playbackRate=1+每帧驱动 startFrom 做时间重映射,decel/accel/smooth 缓动)
  - **签名 .cube LUT**(`kit/luts/signature_teal_orange.cube`,master 阶段真 3D `lut3d`)
  - **Real-ESRGAN 真超分**(`anime superres`,逐个已批准镜头 2×增强并抽检)
  - **4:5主体重构图**(`direct --canvas 4x5 --fill crop`,3072×3840；宽景例外才 `fit_blur`)
- ✅ **M6 AI 导演编排(完成)**:
  - `slots`(CLIP 把镜头分类到 opening/build/climax/ending)
  - `direct` 情绪弧装配 —— BGM 逐拍能量驱动切点疏密,**开场 hero 长镜钩子 → 铺垫 → 高潮密集切 → 长镜收束**,而非均匀每拍切
  - CLIP 近重复去重 + 全局镜头 ID 去重(素材池耗尽后才复用);各段特效/运镜/速度曲线按调性分配;长镜源起点回退防播过片尾
  - 成片:`五条悟_M6情绪弧_钩子高潮收束`(55.8s,QA 全绿)
- ✅ **M7 声音设计与打磨(完成)**:
  - `sound` 分层声音设计 —— numpy 程序化合成 SFX(impact/riser/whoosh/subdrop),按结构点混入 BGM(下拍冲击、甩镜 whoosh、高潮 riser+subdrop)+ sidechain ducking,**免重渲直接 remux 到成片**
  - 扩展 QA:冻结帧检测(告警)+ 真峰值削波检测
  - `rights` 版权来源记录(对接素材库 B站来源)
  - 缓存守卫:`shots` 已分镜则秒回(`--force` 重算)

> **M1–M7 全部完成。** 端到端:动漫原片 → 语义选片 → 导演编排(钩子/高潮/收束)→ 运镜/速度曲线/转场/主体重构图/签名 LUT → 分层声音设计 → 母带 + 平台版 + QA。全 CLI、AI 可零点击操作、一台 16GB Mac。

- ✅ **互联网取材(集成 agent-reach 后端)**:`source`(YouTube 快搜)+ `fetch`(下载→自动入库→登记版权)。**分工**:跨平台发现/登录态走 agent-reach 技能拿 URL;取材入库是项目自己的确定性流水线。
- ✅ **深度优化**:
  - slot 分类改 z-score 相对打分(四类均衡填满,climax 不再空)+ 段落贴合排序(高潮=最烈/收束=最静可见/开场=最清晰)+ 近黑填充帧过滤
  - **signalstats 逐帧亮度扫描**:一次 ffmpeg 拿整片每帧 YAVG,取镜头内最小值(`min_brightness`),抓任意位置暗瞬(比 N 点采样彻底,揪出 8 个采样会漏的暗瞬镜头)→ 根治黑场
  - **镜头级渲染缓存 + 代理迭代**:`render-shots.mjs` 逐镜按内容 hash 缓存渲染成段(bundle 一次+复用浏览器),重渲只做变了的镜头(全命中 0.15s 秒回);`render --preview` 走 0.5 缩放(1080p)快速迭代
  - `shots/analyze/embed/slots` 内容缓存(`--force` 重算);`pick` 偏好学习;QA 黑场/冻结改为告警;master 加 alimiter 末级限幅(修 SFX 削波)

- ✅ **动漫打标(`tag`,WD-Tagger)**:Danbooru v3(ONNX,~400MB,~0.35s/图)给每镜**角色名 + 属性标签**(`gojou_satoru` / `white_hair,sunglasses,adjusting_eyewear,close-up…`),写入 DB 并进 FTS;`search` 融合 CLIP 语义 + FTS 标签 → **角色名/属性可直接检索**(`search "gojou satoru"`)。`fetch` 自动链已含打标。
  - **选型依据(实测)**:通用 VLM(连 Qwen2.5-VL-7B)认不出动漫角色(把五条悟答成 "unknown"/瞎编);WD-Tagger 角色名 3/3 全对、快 15×、小 10×。7B 已删净。

**26 个 CLI 命令。**

**关于 GLSL:** `@remotion/three` 的 `useOffthreadVideoTexture` 在本机无头渲染不出视频纹理(官方最小示例亦然,环境级不兼容;自定义 shader 本身可跑)。故特效走确定性 DOM/SVG/CSS,更快更稳。

**真实素材验证:** `projects/gojo-01/` 用 `~/Desktop/动漫剪辑素材库` 咒术回战/五条悟(87 镜)跑完整链,**语义检索选片** → rhythm velocity edit → 母带,QA 全绿(分辨率/无黑帧/响度)。成片交付于素材库 `04_预览与成片/`。

## 安装

```bash
uv venv --python 3.11 && source .venv/bin/activate
uv pip install -e ".[review,dev]"   # 核心 + Review API + 测试
uv pip install -e ".[ml]"           # 可选:语义检索 + 主体遮罩(torch/rembg,较大)
cd renderer && npm install
```

## 用法

```bash
anime ingest <视频> && anime shots <id> && anime analyze <id> && anime embed <id>
anime roughcut <项目> --audio <bgm.mp3> --query "man with white hair, energy"  # 语义选片+三版粗剪
anime slowmo projects/<项目>/editspec.emotion.json     # 可选:慢镜 RIFE 顺滑
anime render projects/<项目>/editspec.rhythm.json      # 渲染
anime master projects/<项目>/outputs/editspec.rhythm.mp4   # 母带 + 平台版
anime qa     projects/<项目>/outputs/master.mp4        # 体检
anime matte  <shot_id>                                 # 可选:主体遮罩
```
