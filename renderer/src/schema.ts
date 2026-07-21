import { z } from "zod";

// 与 anime/editspec.py 的 model_dump() 输出(snake_case)对齐。
export const transformSchema = z.object({
  scale: z.number().default(1),
  x: z.number().default(0),
  y: z.number().default(0),
  rotate: z.number().default(0),
});

export const effectSchema = z.object({
  type: z.enum(["glow", "rgbSplit", "shake", "vignette", "flash"]),
  intensity: z.number().default(0.5),
  params: z.record(z.string(), z.any()).default({}),
});

export const shotSchema = z.object({
  id: z.string(),
  src: z.string(),
  source_in_sec: z.number().default(0),
  start_frame: z.number(),
  duration_in_frames: z.number(),
  speed: z.number().default(1),
  transform: transformSchema.default({ scale: 1, x: 0, y: 0, rotate: 0 }),
  effects: z.array(effectSchema).default([]),
  lut: z.string().nullable().default(null),
  matte: z.string().nullable().default(null),
  camera_move: z.string().default("none"),
  camera_amount: z.number().default(0),
  transition: z.string().default("none"),
  transition_intensity: z.number().default(0),
  ramp: z.string().default("none"),
  reframe_x: z.number().default(0),
  fill_mode: z.string().default("crop"),
});

export const audioSchema = z.object({
  id: z.string(),
  src: z.string(),
  start_frame: z.number().default(0),
  trim_start_frames: z.number().default(0),
  gain_db: z.number().default(0),
});

export const editSpecSchema = z.object({
  id: z.string(),
  fps: z.number().default(60),
  width: z.number().positive(),
  height: z.number().positive(),
  duration_in_frames: z.number(),
  shots: z.array(shotSchema).default([]),
  audio: z.array(audioSchema).default([]),
});

export type EditSpec = z.infer<typeof editSpecSchema>;
export type Shot = z.infer<typeof shotSchema>;
export type Effect = z.infer<typeof effectSchema>;
