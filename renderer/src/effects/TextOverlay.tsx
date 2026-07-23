import { AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig } from "remotion";
import type { TextOverlay as TextOverlayT } from "../schema";

// 中日文安全字体栈(本机 Chromium 渲染,macOS 自带 PingFang / Hiragino)。
const CJK =
  "'PingFang SC','Hiragino Sans GB','Hiragino Sans','Noto Sans CJK SC','Yu Gothic',sans-serif";

// 世界顶尖前3秒钩子文字:极快 scale-punch 入场(过冲回落)+ 强辉光/描边 + 收尾快出。
// 只做动力学排版,不抢画面;首屏立刻建立「谁/什么」的认知。
export const TextOverlayLayer: React.FC<{ overlay: TextOverlayT }> = ({ overlay }) => {
  const frame = useCurrentFrame();
  const { fps, height } = useVideoConfig();
  const dur = overlay.duration_in_frames;

  // 入场 ~7 帧 scale-punch(1.18→0.98→1.0 过冲),末 6 帧快出(淡出 + 轻微放大)。
  const inN = Math.min(7, Math.round(fps * 0.12));
  const outN = Math.min(6, Math.round(fps * 0.1));
  const opacity = interpolate(
    frame,
    [0, inN * 0.5, dur - outN, dur],
    [0, 1, 1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );
  const scale = interpolate(
    frame,
    [0, inN * 0.6, inN, dur - outN, dur],
    [1.18, 0.98, 1.0, 1.0, 1.06],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );
  const blur = interpolate(frame, [0, inN], [14, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  const isHook = overlay.style !== "name";
  const bigPx = Math.round(height * (isHook ? 0.072 : 0.038));
  const subPx = Math.round(height * (isHook ? 0.03 : 0.024));
  const justify =
    overlay.anchor === "top"
      ? "flex-start"
      : overlay.anchor === "bottom"
        ? "flex-end"
        : "center";
  const pad = Math.round(height * 0.12);

  // 强可读性:白字 + 多层黑色描边/投影 + 暖色辉光,任何画面上都压得住。
  const glow = isHook ? "#ff7a1a" : "#ffffff";
  const textShadow = [
    "0 0 2px rgba(0,0,0,0.9)",
    "0 3px 14px rgba(0,0,0,0.85)",
    `0 0 26px ${glow}`,
    `0 0 46px ${glow}`,
  ].join(",");

  return (
    <AbsoluteFill
      style={{
        justifyContent: justify,
        alignItems: "center",
        paddingTop: overlay.anchor === "top" ? pad : 0,
        paddingBottom: overlay.anchor === "bottom" ? pad : 0,
        opacity,
        pointerEvents: "none",
      }}
    >
      <div
        style={{
          transform: `scale(${scale})`,
          filter: `blur(${blur}px)`,
          textAlign: "center",
          fontFamily: CJK,
        }}
      >
        <div
          style={{
            fontSize: bigPx,
            fontWeight: 900,
            color: "#fff",
            letterSpacing: isHook ? "0.04em" : "0.16em",
            lineHeight: 1.05,
            textShadow,
            WebkitTextStroke: `${Math.max(1, Math.round(bigPx * 0.014))}px rgba(0,0,0,0.55)`,
          }}
        >
          {overlay.text}
        </div>
        {overlay.sub ? (
          <div
            style={{
              marginTop: Math.round(bigPx * 0.22),
              fontSize: subPx,
              fontWeight: 700,
              color: "#ffd9a8",
              letterSpacing: "0.22em",
              textShadow: "0 2px 10px rgba(0,0,0,0.85),0 0 18px rgba(255,122,26,0.6)",
            }}
          >
            {overlay.sub}
          </div>
        ) : null}
      </div>
    </AbsoluteFill>
  );
};
