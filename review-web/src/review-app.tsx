import { useEffect, useMemo, useRef, useState } from "react";
import { Check, ChevronLeft, ChevronRight, Film, Gauge, Play, Search } from "lucide-react";

type Shot = {
  id: string;
  asset_id: string;
  asset_path: string;
  source_url?: string;
  creator?: string;
  title?: string;
  start_sec: number;
  end_sec: number;
  technical_quality: number;
  composition_quality: number;
  emotion_intensity: number;
  action_intensity: number;
  vertical_crop_score: number;
  subtitle_risk: number;
  watermark_risk: number;
  final_score: number;
  tags?: string;
  character?: string;
  action?: string;
  emotion?: string;
  prototype: string;
  explanation_json: {
    structure_role?: string;
  };
  decision?: "use" | "alternate" | "reject";
  reasons?: string[];
  trim_start_sec?: number | null;
  trim_end_sec?: number | null;
};

type Project = {
  project_id: string;
  shot_count: number;
  reviewed_count: number;
  top_score: number;
  brief?: {
    theme?: string;
    duration_sec?: number;
    aspect_ratio?: string;
    creative_contract_json?: {
      content_lane?: string;
      audience_context?: string;
      viewer_promise?: string;
      payoff?: string;
      ending_aftertaste?: string;
      edit_mode?: string;
      visual_motif?: string;
      sound_strategy?: string;
      success_criteria?: string;
    };
  };
};

type Variant = {
  id: number;
  variant_type: string;
  score: number;
  selected: number;
  explanation_json: {
    selections?: Array<{ shot_id: string; role: string; reason: string }>;
  };
};

type ExperimentReport = {
  experiment: { id: number; name: string; status: string };
  decision: "winner" | "collect_more_data";
  winner?: string | null;
  ranked_variants: Array<{
    id: number;
    label: string;
    views: number;
    score: number;
    retention_3s?: number | null;
    completion_rate?: number | null;
  }>;
};

type QualityStatus = {
  pass: boolean;
  pending: number;
  rejected: number;
};

const reasonHotkeys: Record<string, string> = {
  h: "highlight",
  b: "boring",
  s: "burned_subtitle",
  w: "watermark",
  d: "duplicate",
  f: "bad_framing",
  c: "vertical_crop_hard",
};

const roleLabel: Record<string, string> = {
  hook: "Hook",
  build: "Build",
  climax: "Climax",
  release: "Release",
  ending: "Ending",
};

function seconds(value: number) {
  return `${value.toFixed(2)}s`;
}

async function readJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}

export function ReviewApp() {
  const [projectId, setProjectId] = useState<string>("demo");
  const videoRef = useRef<HTMLVideoElement>(null);
  const [project, setProject] = useState<Project | null>(null);
  const [shots, setShots] = useState<Shot[]>([]);
  const [variants, setVariants] = useState<Variant[]>([]);
  const [experiments, setExperiments] = useState<ExperimentReport[]>([]);
  const [quality, setQuality] = useState<QualityStatus | null>(null);
  const [selected, setSelected] = useState(0);
  const [query, setQuery] = useState("");
  const [onlyPending, setOnlyPending] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const queryProject = new URLSearchParams(window.location.search).get("project");
    if (queryProject) {
      setProjectId(queryProject);
      return;
    }
    readJson<{ default_project_id?: string }>("/api/config")
      .then((payload) => {
        const nextProject = payload.default_project_id || "demo";
        setProjectId(nextProject);
        const url = new URL(window.location.href);
        url.searchParams.set("project", nextProject);
        window.history.replaceState({}, "", url);
      })
      .catch(() => undefined);
  }, []);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const [projectPayload, shotPayload, variantPayload, experimentPayload, qualityPayload] = await Promise.all([
        readJson<Project>(`/api/projects/${projectId}`),
        readJson<{ items: Shot[] }>(`/api/projects/${projectId}/shots`),
        readJson<{ items: Variant[] }>(`/api/projects/${projectId}/variants`),
        readJson<{ items: ExperimentReport[] }>(`/api/projects/${projectId}/experiments`),
        readJson<QualityStatus>(`/api/projects/${projectId}/quality`),
      ]);
      setProject(projectPayload);
      setShots(shotPayload.items);
      setVariants(variantPayload.items);
      setExperiments(experimentPayload.items);
      setQuality(qualityPayload);
      setSelected((current) => Math.min(current, Math.max(shotPayload.items.length - 1, 0)));
    } catch (err) {
      setError(err instanceof Error ? err.message : "unknown error");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!projectId) return;
    void load();
  }, [projectId]);

  const filtered = useMemo(() => {
    return shots.filter((shot) => {
      const haystack = `${shot.id} ${shot.asset_path} ${shot.tags || ""} ${shot.character || ""} ${shot.action || ""} ${shot.emotion || ""}`.toLowerCase();
      if (onlyPending && shot.decision) return false;
      return haystack.includes(query.toLowerCase());
    });
  }, [shots, query, onlyPending]);

  const current = filtered[selected] || filtered[0] || null;

  const selectShotById = (shotId: string) => {
    const index = filtered.findIndex((shot) => shot.id === shotId);
    if (index >= 0) setSelected(index);
  };

  const jump = (delta: number) => {
    if (!filtered.length) return;
    setSelected((currentIndex) => Math.min(Math.max(currentIndex + delta, 0), filtered.length - 1));
  };

  const putReview = async (decision: "use" | "alternate" | "reject", reason?: string) => {
    if (!current) return;
    const reasons = Array.from(new Set([...(current.reasons || []), ...(reason ? [reason] : [])]));
    await readJson(`/api/projects/${projectId}/shots/${current.id}/review`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        decision,
        reasons,
        trim_start_sec: current.trim_start_sec,
        trim_end_sec: current.trim_end_sec,
        preferred_role: current.explanation_json?.structure_role || null,
      }),
    });
    await load();
    if (onlyPending) {
      const nextPending = filtered.find((shot) => !shot.decision && shot.id !== current.id);
      if (nextPending) selectShotById(nextPending.id);
    }
  };

  const patchTrim = async (side: "in" | "out") => {
    if (!current || !videoRef.current) return;
    const value = videoRef.current.currentTime;
    await readJson(`/api/projects/${projectId}/shots/${current.id}/trim`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(side === "in" ? { trim_start_sec: value } : { trim_end_sec: value }),
    });
    await load();
  };

  const selectVariant = async (variantId: number) => {
    await readJson(`/api/projects/${projectId}/variants/${variantId}/select`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ selected: true }),
    });
    await load();
  };

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if ((event.target as HTMLElement | null)?.tagName === "INPUT") return;
      if (event.key === " ") {
        event.preventDefault();
        if (!videoRef.current) return;
        if (videoRef.current.paused) {
          void videoRef.current.play();
        } else {
          videoRef.current.pause();
        }
      } else if (event.key === "ArrowLeft") {
        jump(-1);
      } else if (event.key === "ArrowRight") {
        jump(1);
      } else if (event.key === "1") {
        void putReview("use");
      } else if (event.key === "2") {
        void putReview("alternate");
      } else if (event.key === "3") {
        void putReview("reject");
      } else if (event.key.toLowerCase() === "i") {
        void patchTrim("in");
      } else if (event.key.toLowerCase() === "o") {
        void patchTrim("out");
      } else if (reasonHotkeys[event.key.toLowerCase()]) {
        const reason = reasonHotkeys[event.key.toLowerCase()];
        void putReview(current?.decision || "alternate", reason);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [current, filtered]);

  const previewSrc = current ? `/api/assets/${current.asset_id}/preview` : "";

  useEffect(() => {
    const video = videoRef.current;
    if (!video || !current) return;
    const sync = () => {
      video.currentTime = current.trim_start_sec ?? current.start_sec;
    };
    if (video.readyState >= 1) {
      sync();
    } else {
      video.addEventListener("loadedmetadata", sync, { once: true });
      return () => video.removeEventListener("loadedmetadata", sync);
    }
  }, [current?.id, current?.trim_start_sec, current?.start_sec]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video || !current) return;
    const onTimeUpdate = () => {
      const outPoint = current.trim_end_sec ?? current.end_sec;
      if (video.currentTime >= outPoint) {
        video.pause();
      }
    };
    video.addEventListener("timeupdate", onTimeUpdate);
    return () => video.removeEventListener("timeupdate", onTimeUpdate);
  }, [current?.id, current?.trim_end_sec, current?.end_sec]);

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark"><Film /></div>
          <div>
            <p className="eyebrow">ANIME EDIT STUDIO / REVIEW</p>
            <h1>{projectId}</h1>
          </div>
        </div>
        <div className="top-actions">
          <button className="ghost-button" onClick={() => void load()}>刷新</button>
        </div>
      </header>

      <section className="summary-strip">
        <div>
          <p className="eyebrow">CURRENT BRIEF</p>
          <strong>
            {project?.brief?.creative_contract_json?.content_lane || project?.brief?.theme || "No brief"}
            {project?.brief?.creative_contract_json?.edit_mode ? ` · ${project.brief.creative_contract_json.edit_mode}` : ""}
            {project?.brief?.aspect_ratio ? ` · ${project.brief.aspect_ratio}` : ""}
          </strong>
          {project?.brief?.creative_contract_json?.viewer_promise && (
            <small className="brief-promise">
              {project.brief.creative_contract_json.viewer_promise}
              {project.brief.creative_contract_json.payoff ? ` → ${project.brief.creative_contract_json.payoff}` : ""}
            </small>
          )}
        </div>
        <div className="summary-item"><Film /><span><b>{project?.shot_count || 0}</b> shots</span></div>
        <div className="summary-item"><Check /><span><b>{project?.reviewed_count || 0}</b> reviewed</span></div>
        <div className="summary-item"><Gauge /><span><b>{(project?.top_score || 0).toFixed(2)}</b> top score</span></div>
        <div className={quality?.pass ? "summary-item" : "summary-item warning"}>
          <Gauge /><span><b>{quality?.pending || 0}</b> enhancement pending</span>
        </div>
      </section>

      {loading ? <section className="workspace"><div className="preview-panel">Loading…</div></section> : error ? <section className="workspace"><div className="preview-panel">API error: {error}</div></section> : (
        <div className="workspace">
          <aside className="sidebar">
            <div className="section-heading">
              <div>
                <p className="eyebrow">SHOT QUEUE</p>
                <h2>Review queue</h2>
              </div>
              <span className="count-pill">{filtered.length}</span>
            </div>
            <label className="search-box">
              <Search />
              <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索镜头、角色、标签…" />
            </label>
            <button className={onlyPending ? "clean-toggle active" : "clean-toggle"} onClick={() => setOnlyPending((currentState) => !currentState)}>
              <span className="toggle-dot" /> 下一条未审镜头模式
            </button>
            <div className="shot-list">
              {filtered.map((shot, index) => (
                <button key={shot.id} className={index === selected ? "shot-row selected" : "shot-row"} onClick={() => setSelected(index)}>
                  <span className="shot-index">{String(index + 1).padStart(2, "0")}</span>
                  <span className="shot-copy">
                    <b>{shot.prototype}</b>
                    <small>{roleLabel[shot.explanation_json?.structure_role || "build"] || "Build"} · {seconds(shot.end_sec - shot.start_sec)}</small>
                  </span>
                  <span className={shot.decision === "reject" ? "dirty-mark" : "clean-mark"}>{shot.decision || "todo"}</span>
                </button>
              ))}
            </div>
          </aside>

          <section className="preview-panel">
            {current ? (
              <>
                <div className="preview-toolbar">
                  <div>
                    <p className="eyebrow">SHOT PREVIEW</p>
                    <h2>{current.id} <span>/{current.prototype}</span></h2>
                  </div>
                  <div className="toolbar-actions">
                    <button className="icon-button" onClick={() => jump(-1)}><ChevronLeft /></button>
                    <button className="icon-button" onClick={() => jump(1)}><ChevronRight /></button>
                  </div>
                </div>
                <div className="video-frame">
                  <video ref={videoRef} controls preload="metadata" src={previewSrc} />
                  <div className="frame-label">{current.asset_id} <span>·</span> {seconds(current.start_sec)} — {seconds(current.end_sec)}</div>
                </div>
                <div className="timeline">
                  <div className="timeline-track">
                    <div className="timeline-progress" style={{ width: `${Math.min(current.final_score, 1) * 100}%` }} />
                  </div>
                  <div className="timeline-labels">
                    <span>Trim in {current.trim_start_sec != null ? seconds(current.trim_start_sec) : "—"}</span>
                    <span>Trim out {current.trim_end_sec != null ? seconds(current.trim_end_sec) : "—"}</span>
                    <span>Score {current.final_score.toFixed(2)}</span>
                  </div>
                </div>
              </>
            ) : "No shots"}
          </section>

          <aside className="inspector">
            {current && (
              <>
                <div className="section-heading">
                  <div>
                    <p className="eyebrow">SHOT INSPECTOR</p>
                    <h2>Metadata</h2>
                  </div>
                  <Gauge />
                </div>
                <div className="inspector-card hero-stat">
                  <span>Final score</span>
                  <strong>{current.final_score.toFixed(2)}<small>/ 1.00</small></strong>
                  <div className="score-bar"><span style={{ width: `${Math.min(current.final_score, 1) * 100}%` }} /></div>
                </div>
                <div className="meta-grid">
                  <div><span>Source</span><b>{current.title || current.asset_path}</b></div>
                  <div><span>Creator</span><b>{current.creator || "unknown"}</b></div>
                  <div><span>Technical</span><b>{current.technical_quality.toFixed(2)}</b></div>
                  <div><span>Composition</span><b>{current.composition_quality.toFixed(2)}</b></div>
                  <div><span>Emotion</span><b>{current.emotion_intensity.toFixed(2)}</b></div>
                  <div><span>Action</span><b>{current.action_intensity.toFixed(2)}</b></div>
                  <div><span>Crop fit</span><b>{current.vertical_crop_score.toFixed(2)}</b></div>
                  <div><span>Subtitle risk</span><b>{current.subtitle_risk.toFixed(2)}</b></div>
                  <div><span>Watermark risk</span><b>{current.watermark_risk.toFixed(2)}</b></div>
                  <div><span>Recommended role</span><b>{roleLabel[current.explanation_json?.structure_role || "build"] || "Build"}</b></div>
                </div>
                <div className="tag-group">
                  <p className="eyebrow">TAGS</p>
                  <div className="tags">
                    {(current.tags?.split(",").filter(Boolean) || []).map((tag) => <span key={tag}>{tag}</span>)}
                    {current.character && <span>{current.character}</span>}
                    {current.action && <span>{current.action}</span>}
                    {current.emotion && <span>{current.emotion}</span>}
                  </div>
                </div>
                <div className="decision-box">
                  <p className="eyebrow">DECISION</p>
                  <div className="decision-row">
                    <button className="decision reject" onClick={() => void putReview("reject")}>Reject</button>
                    <button className="decision alt" onClick={() => void putReview("alternate")}>Alt</button>
                    <button className="decision use" onClick={() => void putReview("use")}><Check /> Use</button>
                  </div>
                  <div className="decision-row" style={{ marginTop: 8 }}>
                    <button className="decision" onClick={() => void patchTrim("in")}>Set In (I)</button>
                    <button className="decision" onClick={() => void patchTrim("out")}>Set Out (O)</button>
                  </div>
                  <p>Reasons: {(current.reasons || []).join(", ") || "—"}</p>
                </div>
                <div className="tag-group">
                  <p className="eyebrow">VARIANTS</p>
                  <div className="tags">
                    {variants.map((variant) => (
                      <button key={variant.id} className={variant.selected ? "decision use" : "decision"} onClick={() => void selectVariant(variant.id)}>
                        {variant.variant_type} #{variant.id}
                      </button>
                    ))}
                  </div>
                </div>
                <div className="tag-group">
                  <p className="eyebrow">GROWTH EXPERIMENTS</p>
                  <div className="experiment-list">
                    {experiments.length ? experiments.map((item) => (
                      <div className="experiment-card" key={item.experiment.id}>
                        <b>{item.experiment.name}</b>
                        <span>{item.winner ? `Winner ${item.winner}` : "Collect more data"}</span>
                        {item.ranked_variants.slice(0, 2).map((variant) => (
                          <small key={variant.id}>
                            {variant.label} · {variant.views} views · 3s {variant.retention_3s != null ? `${(variant.retention_3s * 100).toFixed(0)}%` : "—"}
                          </small>
                        ))}
                      </div>
                    )) : <span className="empty-copy">No experiment data</span>}
                  </div>
                </div>
              </>
            )}
          </aside>
        </div>
      )}

      <footer className="footer">
        <span><Play /> Space 播放 / 暂停</span>
        <span>← → 切换镜头</span>
        <span>1 / 2 / 3 写回 use / alt / reject</span>
        <span>H B S W D F C 追加原因</span>
        <span>I / O 设置入出点</span>
      </footer>
    </main>
  );
}
