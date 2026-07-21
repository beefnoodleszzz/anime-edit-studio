import { OffthreadVideo, useCurrentFrame, useVideoConfig } from "remotion";
import type { Shot } from "../schema";

// 速度曲线时间重映射:每帧算出目标源时间,用 startFrom 抵消 OffthreadVideo
// 的自然前进(playbackRate=1),从而实现加速/减速/freeze。

const cover: React.CSSProperties = {
  width: "100%",
  height: "100%",
  objectFit: "cover",
};

const sidewaysStyle = (direction: "cw" | "ccw"): React.CSSProperties => ({
  position: "absolute",
  width: "177.7777778%",
  height: "56.25%",
  left: "50%",
  top: "50%",
  objectFit: "cover",
  transform: `translate(-50%, -50%) rotate(${direction === "cw" ? 90 : -90}deg)`,
  transformOrigin: "center",
});

// local progress p(0..1) → source progress(0..1)
const curve = (ramp: string, p: number): number => {
  if (ramp === "decel") return 1 - (1 - p) * (1 - p); // 快→慢(落地慢镜)
  if (ramp === "accel") return p * p; // 慢→快
  if (ramp === "smooth")
    return p < 0.5 ? 2 * p * p : 1 - Math.pow(-2 * p + 2, 2) / 2; // easeInOut
  return p; // linear
};

export const RampVideo: React.FC<{ shot: Shot; src: string }> = ({ shot, src }) => {
  const frame = useCurrentFrame(); // 0..dur-1(镜头 Sequence 内)
  const { fps } = useVideoConfig();
  const p = Math.min(Math.max(frame / Math.max(shot.duration_in_frames - 1, 1), 0), 1);
  const span = (shot.duration_in_frames / fps) * shot.speed; // 该镜头消耗的源秒数
  const srcTime = shot.source_in_sec + span * curve(shot.ramp, p);
  // OffthreadVideo 在 local frame f 显示源帧 startFrom+f;令其恰为 round(srcTime*fps)
  const startFrom = Math.max(0, Math.round(srcTime * fps) - frame);
  const rx = Math.max(-1, Math.min(1, shot.reframe_x ?? 0));
  const style: React.CSSProperties =
    shot.fill_mode === "fit_blur"
      ? { width: "100%", height: "100%", objectFit: "contain" }
      : shot.fill_mode === "sideways_cw" || shot.fill_mode === "sideways_ccw"
        ? sidewaysStyle(shot.fill_mode === "sideways_cw" ? "cw" : "ccw")
      : { ...cover, objectPosition: `${Math.round(50 + rx * 50)}% 50%` };
  return <OffthreadVideo src={src} startFrom={startFrom} playbackRate={1} style={style} />;
};
