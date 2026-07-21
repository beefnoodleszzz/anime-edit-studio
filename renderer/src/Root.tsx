import { Composition } from "remotion";
import { Edit } from "./Edit";
import { editSpecSchema, type EditSpec } from "./schema";

const fallback: EditSpec = {
  id: "empty",
  fps: 60,
  width: 1920,
  height: 1080,
  duration_in_frames: 60,
  shots: [],
  audio: [],
};

export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="Edit"
      component={Edit}
      schema={editSpecSchema}
      defaultProps={fallback}
      // 画幅完全由 EditSpec 决定，可按素材选择横屏、竖屏或自定义尺寸。
      calculateMetadata={({ props }) => ({
        durationInFrames: props.duration_in_frames,
        fps: props.fps,
        width: props.width,
        height: props.height,
      })}
    />
  );
};
