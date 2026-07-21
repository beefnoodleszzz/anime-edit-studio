import { AbsoluteFill, OffthreadVideo, useVideoConfig } from "remotion";
import { RampVideo } from "./RampVideo";
import type { Shot } from "../schema";

// 在 OffthreadVideo(可靠 DOM 路径)上叠加动漫 edit 特效词汇:
//   调色(CSS filter) · 辉光(blur+screen 复制) · 色差(SVG 滤镜) · 暗角(径向叠加)
// 确定性、渲染快;是对 M1/M2 drop-shadow 近似的实质升级。
// GLSL/@remotion/three 路径见 GlslShot.tsx(shader 可跑,视频纹理桥接待解决)。

const intensityOf = (shot: Shot, type: string): number =>
  shot.effects.find((e) => e.type === type)?.intensity ?? 0;

const cover: React.CSSProperties = {
  width: "100%",
  height: "100%",
  objectFit: "cover",
};

// 横置全屏:先将 16:9 源画面旋转,再让旋转后的 9:16 正好占满画布。
// 不能复用 crop + transform.rotate: crop 会在旋转前先丢掉横版两侧。
const sidewaysStyle = (direction: "cw" | "ccw"): React.CSSProperties => ({
  position: "absolute",
  width: "177.7777778%", // portrait canvas height / width (16:9 source box)
  height: "56.25%",      // portrait canvas width / height
  left: "50%",
  top: "50%",
  objectFit: "cover",
  transform: `translate(-50%, -50%) rotate(${direction === "cw" ? 90 : -90}deg)`,
  transformOrigin: "center",
});

// 渲染阶段仅做近中性微调;签名色彩由 master 的真 3D LUT(lut3d)负责,避免双重调色。
const gradeFilter = "contrast(1.03)";

export const EffectStack: React.FC<{ shot: Shot; src: string; matte?: string }> = ({
  shot,
  src,
  matte,
}) => {
  const { fps } = useVideoConfig();
  const startFrom = Math.round(shot.source_in_sec * fps);
  const glow = intensityOf(shot, "glow");
  const vignette = intensityOf(shot, "vignette");
  const rgb = intensityOf(shot, "rgbSplit");
  const filterId = `rgb-${shot.id.replace(/[^a-zA-Z0-9]/g, "")}`;
  const shift = rgb * 8;
  const rgbFilter = rgb > 0 ? ` url(#${filterId})` : "";
  // 主体感知重构图:移动 cover 裁切位置到主体侧(不露黑边)
  const rx = Math.max(-1, Math.min(1, shot.reframe_x ?? 0));
  const objectPosition = `${Math.round(50 + rx * 50)}% 50%`;
  // 跨画幅装帧:crop=满屏 | fit_blur=完整画面 contain 居中 + 毛玻璃背景(不切坏宽景/多人)
  const fit = shot.fill_mode === "fit_blur";
  const sideways = shot.fill_mode === "sideways_cw" || shot.fill_mode === "sideways_ccw";
  const mainStyle: React.CSSProperties = fit
    ? { width: "100%", height: "100%", objectFit: "contain" }
    : sideways
      ? sidewaysStyle(shot.fill_mode === "sideways_cw" ? "cw" : "ccw")
    : { ...cover, objectPosition };

  const baseVideo = () =>
    shot.ramp && shot.ramp !== "none" ? (
      <RampVideo shot={shot} src={src} />
    ) : (
      <OffthreadVideo
        src={src}
        startFrom={startFrom}
        playbackRate={shot.speed}
        style={mainStyle}
      />
    );

  // 毛玻璃背景:同帧放大 + 强模糊 + 压暗,填满 contain 留出的上下留白
  const blurBg = fit ? (
    <AbsoluteFill style={{ filter: "blur(40px) brightness(0.5) saturate(1.1)", transform: "scale(1.15)" }}>
      <OffthreadVideo src={src} startFrom={startFrom} playbackRate={shot.speed} style={cover} />
    </AbsoluteFill>
  ) : null;

  const maskStyle: React.CSSProperties = matte
    ? {
        WebkitMaskImage: `url(${matte})`,
        maskImage: `url(${matte})`,
        WebkitMaskSize: "cover",
        maskSize: "cover",
        WebkitMaskPosition: "center",
        maskPosition: "center",
      }
    : {};

  return (
    <AbsoluteFill>
      {blurBg}
      {matte ? (
        <>
          {/* 主体高亮:背景压暗去饱和 */}
          <AbsoluteFill style={{ filter: `${gradeFilter} brightness(0.5) saturate(0.65)` }}>
            {baseVideo()}
          </AbsoluteFill>
          {/* 主体遮罩裁出,提亮 + 可选色差 */}
          <AbsoluteFill
            style={{ ...maskStyle, filter: `${gradeFilter} brightness(1.12) saturate(1.25)${rgbFilter}` }}
          >
            {baseVideo()}
          </AbsoluteFill>
        </>
      ) : (
        /* 底层:调色 + 可选色差 */
        <AbsoluteFill style={{ filter: `${gradeFilter}${rgbFilter}` }}>
          {baseVideo()}
        </AbsoluteFill>
      )}

      {/* 辉光:提亮 + 模糊的复制层,screen 混合 */}
      {glow > 0 && (
        <AbsoluteFill
          style={{
            mixBlendMode: "screen",
            opacity: Math.min(glow, 1),
            filter: `brightness(1.5) blur(${Math.round(glow * 22)}px)`,
          }}
        >
          {baseVideo()}
        </AbsoluteFill>
      )}

      {/* 暗角 */}
      {vignette > 0 && (
        <AbsoluteFill
          style={{
            background:
              "radial-gradient(ellipse at center, rgba(0,0,0,0) 45%, rgba(0,0,0,1) 130%)",
            opacity: Math.min(vignette, 1),
          }}
        />
      )}

      {/* 色差 SVG 滤镜定义 */}
      {rgb > 0 && (
        <svg width={0} height={0} style={{ position: "absolute" }}>
          <defs>
            <filter id={filterId} x="-10%" y="-10%" width="120%" height="120%">
              <feColorMatrix
                in="SourceGraphic"
                type="matrix"
                values="1 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 1 0"
                result="red"
              />
              <feOffset in="red" dx={shift} dy="0" result="redShift" />
              <feColorMatrix
                in="SourceGraphic"
                type="matrix"
                values="0 0 0 0 0  0 1 0 0 0  0 0 1 0 0  0 0 0 1 0"
                result="greenblue"
              />
              <feOffset in="greenblue" dx={-shift} dy="0" result="gbShift" />
              <feBlend in="redShift" in2="gbShift" mode="screen" />
            </filter>
          </defs>
        </svg>
      )}
    </AbsoluteFill>
  );
};
