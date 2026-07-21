import { Easing, interpolate, random } from "remotion";
import type { Shot } from "../schema";

// M5 运动设计:动画运镜(Ken Burns)+ 入场转场(甩镜/缩放模糊/闪切)。
// 几何量走 transform,模糊量走 filter,闪光走叠层。

const prog = (frame: number, dur: number) =>
  Math.min(Math.max(frame / Math.max(dur, 1), 0), 1);

export const shotTransform = (shot: Shot, frame: number): string => {
  const t = shot.transform ?? { scale: 1, x: 0, y: 0, rotate: 0 };
  let scale = t.scale;
  let x = t.x;
  let y = t.y;
  // 注:主体感知重构图走 object-position(见 EffectStack/RampVideo),不用平移,避免露黑边。

  // 动画运镜
  const a = shot.camera_amount ?? 0;
  const move = shot.camera_move ?? "none";
  if (a > 0 && move !== "none") {
    const e = Easing.inOut(Easing.ease)(prog(frame, shot.duration_in_frames));
    const overscan = 1 + a; // 平移需过扫描防露边
    if (move === "pushIn") scale *= 1 + a * e;
    else if (move === "pushOut") scale *= 1 + a * (1 - e);
    else if (move === "panLeft") { scale *= overscan; x += -a * 600 * e; }
    else if (move === "panRight") { scale *= overscan; x += a * 600 * e; }
    else if (move === "panUp") { scale *= overscan; y += -a * 600 * e; }
    else if (move === "panDown") { scale *= overscan; y += a * 600 * e; }
  }

  // 入场转场的几何分量
  const ti = shot.transition_intensity ?? 0;
  if (ti > 0 && (shot.transition === "whipLeft" || shot.transition === "whipRight")) {
    const w = interpolate(frame, [0, 5], [1, 0], {
      extrapolateRight: "clamp",
      easing: Easing.out(Easing.quad),
    });
    x += (shot.transition === "whipLeft" ? -1 : 1) * 360 * ti * w;
    scale *= 1 + 0.5 * ti * w; // 过扫描外延(0.5×半宽=540px)> 位移(360px),遮住黑边
  }
  if (ti > 0 && shot.transition === "zoomBlur") {
    scale *= 1 + interpolate(frame, [0, 6], [0.18 * ti, 0], {
      extrapolateRight: "clamp",
      easing: Easing.out(Easing.quad),
    });
  }
  // zoomPunch:冲击变焦(比 zoomBlur 更猛更短,落拍那一下的径向 punch)
  if (ti > 0 && shot.transition === "zoomPunch") {
    scale *= 1 + interpolate(frame, [0, 4], [0.32 * ti, 0], {
      extrapolateRight: "clamp",
      easing: Easing.out(Easing.cubic),
    });
  }

  // 抖动
  const shake = shot.effects.find((e) => e.type === "shake");
  if (shake) {
    const amp = shake.intensity * 24;
    x += (random(`${shot.id}-${frame}-x`) - 0.5) * amp;
    y += (random(`${shot.id}-${frame}-y`) - 0.5) * amp;
  }

  return `translate(${x}px, ${y}px) scale(${scale}) rotate(${t.rotate}deg)`;
};

// 入场转场的模糊分量
export const transitionFilter = (shot: Shot, frame: number): string => {
  const ti = shot.transition_intensity ?? 0;
  if (ti <= 0) return "";
  if (shot.transition === "whipLeft" || shot.transition === "whipRight") {
    const b = interpolate(frame, [0, 5], [ti * 30, 0], { extrapolateRight: "clamp" });
    return b > 0.3 ? `blur(${b.toFixed(1)}px)` : "";
  }
  if (shot.transition === "zoomBlur") {
    const b = interpolate(frame, [0, 6], [ti * 22, 0], { extrapolateRight: "clamp" });
    return b > 0.3 ? `blur(${b.toFixed(1)}px)` : "";
  }
  if (shot.transition === "zoomPunch") {
    const b = interpolate(frame, [0, 4], [ti * 28, 0], { extrapolateRight: "clamp" });
    return b > 0.3 ? `blur(${b.toFixed(1)}px)` : "";
  }
  return "";
};

// 闪光:flash 特效 或 flash 转场
export const flashOpacity = (shot: Shot, frame: number): number => {
  const flashE = shot.effects.find((e) => e.type === "flash");
  const flashT =
    shot.transition === "flash" ? (shot.transition_intensity ?? 0) : 0;
  const intensity = Math.max(flashE ? flashE.intensity : 0, flashT);
  if (intensity <= 0) return 0;
  return interpolate(frame, [0, 4], [intensity, 0], { extrapolateRight: "clamp" });
};
