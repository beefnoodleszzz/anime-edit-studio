import { Config } from "@remotion/cli/config";

Config.setVideoImageFormat("jpeg");
Config.setConcurrency(2); // M2 Pro 16GB:限制并发 Chrome 实例,防 OOM
Config.setChromiumOpenGlRenderer("angle"); // DOM/SVG/CSS 特效:GPU 加速的 ANGLE
Config.overrideWebpackConfig((c) => c);
