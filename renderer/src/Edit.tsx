import {
  AbsoluteFill,
  Audio,
  Sequence,
  staticFile,
  useCurrentFrame,
} from "remotion";
import { flashOpacity, shotTransform, transitionFilter } from "./effects/apply";
import { EffectStack } from "./effects/EffectStack";
import type { EditSpec, Shot } from "./schema";

// src 由 render 桥接暂存到 renderer/public/sources/ 下,故用 staticFile。
const resolve = (src: string): string =>
  src.startsWith("http") ? src : staticFile(src);

const ShotLayer: React.FC<{ shot: Shot }> = ({ shot }) => {
  const frame = useCurrentFrame();
  const transform = shotTransform(shot, frame);
  const flash = flashOpacity(shot, frame);
  const tFilter = transitionFilter(shot, frame);
  return (
    <AbsoluteFill style={{ overflow: "hidden" }}>
      {/* 运镜/抖动/转场位移走 transform;转场模糊走 filter;调色特效在 EffectStack */}
      <AbsoluteFill style={{ transform, filter: tFilter || undefined }}>
        <EffectStack
          shot={shot}
          src={resolve(shot.src)}
          matte={shot.matte ? resolve(shot.matte) : undefined}
        />
      </AbsoluteFill>
      {flash > 0 && (
        <AbsoluteFill style={{ backgroundColor: "white", opacity: flash }} />
      )}
    </AbsoluteFill>
  );
};

export const Edit: React.FC<EditSpec> = ({ shots, audio }) => {
  return (
    <AbsoluteFill style={{ backgroundColor: "black" }}>
      {shots.map((shot) => (
        <Sequence
          key={shot.id}
          from={shot.start_frame}
          durationInFrames={shot.duration_in_frames}
        >
          <ShotLayer shot={shot} />
        </Sequence>
      ))}
      {audio.map((a) => (
        <Sequence key={a.id} from={a.start_frame}>
          <Audio
            src={resolve(a.src)}
            trimBefore={a.trim_start_frames}
            volume={Math.pow(10, a.gain_db / 20)}
          />
        </Sequence>
      ))}
    </AbsoluteFill>
  );
};
