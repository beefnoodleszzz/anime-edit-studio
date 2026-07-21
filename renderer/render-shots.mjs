// 镜头级渲染缓存:逐镜按内容 hash 渲染帧区间成段,命中缓存则跳过。
// 用 spec 宽高算 hash → 全命中时连 bundle/浏览器都不启动,真正秒回;
// 有 miss 才 bundle 一次 + 复用一个浏览器,只渲变了的镜头。
// 用法: node render-shots.mjs <stagedSpec.json> <segMetaDir> <cacheDir> <scale>
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

const [specPath, outDir, cacheDir, scaleStr] = process.argv.slice(2);
const scale = parseFloat(scaleStr || "1");
const crf = scale < 1 ? 18 : 12;
const spec = JSON.parse(readFileSync(specPath, "utf8"));
mkdirSync(cacheDir, { recursive: true });
mkdirSync(outDir, { recursive: true });

const dims = `${spec.width}x${spec.height}`;
const jobs = spec.shots.map((shot) => {
  const key = createHash("sha256")
    .update(`${JSON.stringify(shot)}|${scale}|${dims}`)
    .digest("hex")
    .slice(0, 16);
  const seg = path.join(cacheDir, `${key}.mp4`);
  return { shot, seg, cached: existsSync(seg) };
});

const misses = jobs.filter((j) => !j.cached);
if (misses.length > 0) {
  const { bundle } = await import("@remotion/bundler");
  const { openBrowser, renderMedia, selectComposition } = await import("@remotion/renderer");
  const serveUrl = await bundle({ entryPoint: path.resolve("src/index.ts") });
  const composition = await selectComposition({ serveUrl, id: "Edit", inputProps: spec });
  const browser = await openBrowser("chrome", { chromiumOptions: { gl: "angle" } });
  for (const j of misses) {
    const start = j.shot.start_frame;
    const end = start + j.shot.duration_in_frames - 1;
    await renderMedia({
      composition, serveUrl, codec: "h264", outputLocation: j.seg,
      inputProps: spec, frameRange: [start, end], muted: true, scale,
      crf,
      puppeteerInstance: browser, overwrite: true,
    });
  }
  try {
    await browser.close({ silent: true });
  } catch {
    /* 忽略 */
  }
}

writeFileSync(path.join(outDir, "segments.json"), JSON.stringify(jobs.map((j) => j.seg)));
console.log(`SEGMENTS ${jobs.length} rendered=${misses.length} cached=${jobs.length - misses.length}`);
process.exit(0);
