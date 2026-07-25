import { Easing, interpolate, random } from "remotion";
import type { Shot } from "../schema";

// M5 运动设计:动画运镜(Ken Burns)+ 入场转场(甩镜/缩放模糊/闪切)。
// 几何量走 transform,模糊量走 filter,闪光走叠层。

const prog = (frame: number, dur: number) =>
  Math.min(Math.max(frame / Math.max(dur, 1), 0), 1);

// 甩镜平移与过扫描均按画宽比例(而非绝对像素),保证任何画幅(竖屏/4:5/横屏)下
// 过扫描恒能盖住位移、不露黑边。SHIFT=12% 画宽≈4K 下原 360px 观感;OVERSCAN 系数
// 满足 (scale-1)/2 ≥ SHIFT 即全盖(0.30/2=0.15 > 0.12,留 25% 余量)。
const WHIP_SHIFT = 0.12;
const WHIP_OVERSCAN = 0.3;

export const shotTransform = (shot: Shot, frame: number, width = 1080): string => {
  const t = shot.transform ?? { scale: 1, x: 0, y: 0, rotate: 0 };
  let scale = t.scale;
  let x = t.x;
  let y = t.y;
  // 注:主体感知重构图走 object-position(见 EffectStack/RampVideo),不用平移,避免露黑边。

  // 连续推镜(micro-pushpull):camera_from→camera_to 线性插值 scale。
  // 匀速、无 ease-in-out 归零,跨剪辑点由导演层令相邻镜首尾 scale 接力,读作一镜连续推。
  const cf = shot.camera_from ?? 0;
  const ct = shot.camera_to ?? 0;
  if (cf > 0 && ct > 0) {
    scale *= interpolate(prog(frame, shot.duration_in_frames), [0, 1], [cf, ct]);
  } else {
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
  }

  // 入场转场的几何分量
  const ti = shot.transition_intensity ?? 0;
  if (ti > 0 && (shot.transition === "whipLeft" || shot.transition === "whipRight")) {
    const w = interpolate(frame, [0, 5], [1, 0], {
      extrapolateRight: "clamp",
      easing: Easing.out(Easing.quad),
    });
    x += (shot.transition === "whipLeft" ? -1 : 1) * WHIP_SHIFT * width * ti * w;
    scale *= 1 + WHIP_OVERSCAN * ti * w; // 过扫描按比例恒盖位移,任何画幅不露黑边
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

  // 出镜拖尾(whip-drag):镜尾最后 5 帧朝下一镜甩镜的同一屏幕方向加速滑移+过扫描。
  // 与下一镜的入场 whip 合成一次完整甩镜(出镜糊出去→切→入镜糊进来,同向连续)。
  // 位移符号取入场 whip 的反号:入场 whipLeft(x:-360→0,画面右移),对应出镜续右(x:0→+360)。
  const eo = shot.exit_intensity ?? 0;
  if (eo > 0 && (shot.exit_transition === "whipLeft" || shot.exit_transition === "whipRight")) {
    const framesFromEnd = shot.duration_in_frames - frame;
    const w = interpolate(framesFromEnd, [0, 5], [1, 0], {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
      easing: Easing.in(Easing.quad),
    });
    x += (shot.exit_transition === "whipLeft" ? 1 : -1) * WHIP_SHIFT * width * eo * w;
    scale *= 1 + WHIP_OVERSCAN * eo * w;
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

// 入场转场 + 出镜拖尾的模糊分量(取两者较大者,允许同一镜既入场糊进又镜尾糊出)
export const transitionFilter = (shot: Shot, frame: number): string => {
  let b = 0;
  const ti = shot.transition_intensity ?? 0;
  if (ti > 0) {
    if (shot.transition === "whipLeft" || shot.transition === "whipRight") {
      b = Math.max(b, interpolate(frame, [0, 5], [ti * 30, 0], { extrapolateRight: "clamp" }));
    } else if (shot.transition === "zoomBlur") {
      b = Math.max(b, interpolate(frame, [0, 6], [ti * 22, 0], { extrapolateRight: "clamp" }));
    } else if (shot.transition === "zoomPunch") {
      b = Math.max(b, interpolate(frame, [0, 4], [ti * 28, 0], { extrapolateRight: "clamp" }));
    }
  }
  // 出镜拖尾模糊:镜尾最后 5 帧渐强
  const eo = shot.exit_intensity ?? 0;
  if (eo > 0 && (shot.exit_transition === "whipLeft" || shot.exit_transition === "whipRight")) {
    const framesFromEnd = shot.duration_in_frames - frame;
    b = Math.max(b, interpolate(framesFromEnd, [0, 5], [eo * 30, 0], {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
    }));
  }
  return b > 0.3 ? `blur(${b.toFixed(1)}px)` : "";
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
