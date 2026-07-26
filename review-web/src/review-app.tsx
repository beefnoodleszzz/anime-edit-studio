import {
  ArrowLeft, ArrowRight, Check, CirclePlay, Download,
  Film, ImagePlus, MessageSquareText, Play, RefreshCw,
  Send, Sparkles, Upload, WandSparkles,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

type Page = "project" | "reference" | "candidates" | "cut" | "revision" | "final";
type Candidate = { id: string; label: "A" | "B" | "C"; preview?: string; image?: string; note: string; tags: string[] };
type CandidateGroup = { id: string; role: string; selected_shot_id?: string | null; candidates: Candidate[] };
type Delivery = {
  status: string;
  passed: boolean;
  output_path?: string | null;
  checks: Array<{ name: string; passed: boolean; measured?: unknown }>;
};
type RevisionResponse = {
  to_version: number;
  operations: number;
  changed_ranges: number[][];
  changed_duration_sec: number;
  preview_url: string;
};
type ProjectSummary = {
  ready: boolean;
  duration_sec?: number | null;
  clip_count: number;
  roles: string[];
};
type KpiMetric = {
  value: number | null;
  target: string;
  status: "pass" | "fail" | "insufficient_data";
};
type ProjectKpis = {
  candidate_selection_count: KpiMetric;
  candidate_precision: KpiMetric;
  time_to_first_preview_sec: KpiMetric;
  revision_count_to_lock: KpiMetric;
  first_cut_survival_rate: KpiMetric;
  technical_qa_pass_rate: KpiMetric;
};

const pages: Array<{ id: Page; label: string; caption: string }> = [
  { id: "project", label: "项目", caption: "告诉我们要做什么" },
  { id: "reference", label: "参考", caption: "读取你喜欢的节奏" },
  { id: "candidates", label: "选镜", caption: "只做有价值的选择" },
  { id: "cut", label: "初剪", caption: "看完整第一版" },
  { id: "revision", label: "修改", caption: "直接说哪里不对" },
  { id: "final", label: "成片", caption: "确认、下载、发布" },
];

async function json<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json() as Promise<T>;
}

function StepRail({ page, onChange }: { page: Page; onChange: (page: Page) => void }) {
  const activeIndex = pages.findIndex((item) => item.id === page);
  return (
    <aside className="step-rail" aria-label="制作步骤">
      <div className="wordmark">
        <span className="wordmark-glyph">AES</span>
        <span>Anime Edit<br />Studio</span>
      </div>
      <div className="film-spine" aria-hidden="true">
        <span style={{ height: `${(activeIndex / (pages.length - 1)) * 100}%` }} />
      </div>
      <nav>
        {pages.map((item, index) => {
          const state = index < activeIndex ? "done" : index === activeIndex ? "active" : "pending";
          return (
            <button key={item.id} className={`step ${state}`} onClick={() => onChange(item.id)}>
              <span className="step-index">{index < activeIndex ? <Check /> : index + 1}</span>
              <span><b>{item.label}</b><small>{item.caption}</small></span>
            </button>
          );
        })}
      </nav>
      <div className="rail-status"><i /> 自动保存已开启</div>
    </aside>
  );
}

function PageHeader({ eyebrow, title, note }: { eyebrow: string; title: string; note: string }) {
  return (
    <header className="page-header">
      <p>{eyebrow}</p>
      <h1>{title}</h1>
      <span>{note}</span>
    </header>
  );
}

function ProjectPage({ next, onCreated }: { next: () => void; onCreated: (id: string) => void }) {
  const [title, setTitle] = useState("炭治郎 · 觉醒");
  const [duration, setDuration] = useState("25 秒");
  const [intent, setIntent] = useState("做一条炭治郎从压抑到爆发的燃向短片。开头要立刻认出角色，Drop 必须有动作反转，结尾留一点余韵。");
  const [character, setCharacter] = useState("炭治郎");
  const [music, setMusic] = useState<File | null>(null);
  const [sources, setSources] = useState<File[]>([]);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");
  const upload = async (projectId: string, kind: string, file: File) => {
    const body = new FormData();
    body.append("file", file);
    return json(`/projects/${projectId}/uploads/${kind}`, { method: "POST", body });
  };
  const create = async () => {
    if (!music) {
      setError("请先选择音乐文件。");
      return;
    }
    setWorking(true);
    setError("");
    try {
      const project = await json<{ project_id: string }>("/projects", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title, intent, duration_sec: Number.parseFloat(duration),
          platform: "douyin", primary_characters: character.trim() ? [character.trim()] : [],
          tone: ["燃向"],
        }),
      });
      await upload(project.project_id, "music", music);
      for (const source of sources) await upload(project.project_id, "source", source);
      onCreated(project.project_id);
      next();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "项目创建失败");
    } finally {
      setWorking(false);
    }
  };
  return (
    <section className="page">
      <PageHeader eyebrow="01 / Project" title="先说想让观众感受到什么。" note="素材分析、找镜头和技术设置会自动完成。" />
      <div className="project-grid">
        <div className="hero-input">
          <label htmlFor="intent">这一条视频的核心</label>
          <textarea id="intent" value={intent} onChange={(event) => setIntent(event.target.value)} />
          <div className="prompt-chips">
            {["燃向角色展示", "情绪递进", "Drop 爆发", "干净无字幕"].map((item) => <button key={item}>{item}</button>)}
          </div>
        </div>
        <div className="project-settings">
          <div className="field"><label>项目名</label><input value={title} onChange={(event) => setTitle(event.target.value)} /></div>
          <div className="field"><label>主角</label><input value={character} onChange={(event) => setCharacter(event.target.value)} /></div>
          <div className="field-row">
            <div className="field"><label>时长</label><select value={duration} onChange={(event) => setDuration(event.target.value)}><option>15 秒</option><option>25 秒</option><option>30 秒</option></select></div>
            <div className="field"><label>发布到</label><select><option>抖音 / TikTok</option><option>小红书</option><option>Reels</option></select></div>
          </div>
          <div className="upload-zone" tabIndex={0}><Upload /><b>选择音乐与可选素材</b><span>{music ? `音乐：${music.name} · 素材 ${sources.length} 个` : "音乐是必需的；素材库已有内容时可不上传整集"}</span><label className="file-button">选择音乐<input type="file" accept="audio/*,video/*" onChange={(event) => setMusic(event.target.files?.[0] || null)} /></label><label className="file-button">添加整集素材<input type="file" multiple accept="video/*,.mkv" onChange={(event) => setSources(Array.from(event.target.files || []))} /></label></div>
          {error && <p className="form-error">{error}</p>}
        </div>
      </div>
      <footer className="page-actions"><span>预计 18 分钟生成第一版</span><button className="primary" disabled={working} onClick={() => void create()}>{working ? "正在建立项目…" : "开始创作"} <ArrowRight /></button></footer>
    </section>
  );
}

function ReferencePage({ projectId, next, back }: { projectId: string | null; next: () => void; back: () => void }) {
  const [hasReference, setHasReference] = useState(true);
  const [reference, setReference] = useState<File | null>(null);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");
  const prepare = async () => {
    if (!projectId) return;
    setWorking(true);
    setError("");
    try {
      if (reference) {
        const body = new FormData();
        body.append("file", reference);
        await json(`/projects/${projectId}/uploads/reference`, { method: "POST", body });
      }
      await json(`/projects/${projectId}/prepare`, { method: "POST" });
      next();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "候选生成失败");
    } finally {
      setWorking(false);
    }
  };
  return (
    <section className="page">
      <PageHeader eyebrow="02 / Reference" title="给一个你喜欢的节奏，或让导演自己判断。" note="系统学习剪辑语法，不复制画面与特效。" />
      <div className="reference-stage">
        <div className={`reference-drop ${hasReference ? "filled" : ""}`} onClick={() => setHasReference(true)}>
          {hasReference ? <><div className="reference-poster"><CirclePlay /></div><div><b>{reference?.name || "不使用参考片"}</b><span>{reference ? "将在生成候选前读取" : "导演将根据音乐自行判断"}</span></div><label className="file-button">选择参考<input type="file" accept="video/*" onChange={(event) => setReference(event.target.files?.[0] || null)} /></label></> : <><ImagePlus /><b>加入参考视频</b></>}
        </div>
        <div className="grammar-card">
          <div className="grammar-title"><WandSparkles /><span><b>读到的节奏语言</b><small>不是滤镜参数，是镜头如何组织</small></span></div>
          <div className="energy-curve" aria-label="能量走势"><i /><i /><i /><i /><i /><i /><i /><i /></div>
          <div className="grammar-grid">
            <div><span>镜头节奏</span><b>上传后分析</b></div>
            <div><span>平均镜长</span><b>等待参考片</b></div>
            <div><span>Drop</span><b>由音乐实测</b></div>
            <div><span>运动语法</span><b>由镜头序列提取</b></div>
          </div>
          <p>分析完成前不预设结论；系统只迁移可验证的节奏与镜头组织方式。</p>
        </div>
      </div>
      {error && <p className="form-error">{error}</p>}
      <footer className="page-actions"><button className="quiet" onClick={back}><ArrowLeft /> 返回</button><button className="primary" disabled={working || !projectId} onClick={() => void prepare()}>{working ? "正在分析…" : "生成候选"} <ArrowRight /></button></footer>
    </section>
  );
}

function CandidateCard({ candidate, selected, onSelect }: { candidate: Candidate; selected: boolean; onSelect: () => void }) {
  return (
    <article className={`candidate-card ${selected ? "selected" : ""}`}>
      <div className="candidate-media">
        {candidate.preview ? <video src={candidate.preview} controls preload="metadata" /> : <div className={`demo-frame frame-${candidate.label.toLowerCase()}`}><Play /></div>}
        <span className="candidate-letter">{candidate.label}</span>
        {selected && <span className="selected-mark"><Check /> 已选</span>}
      </div>
      <div className="candidate-copy"><p>{candidate.note}</p><div>{candidate.tags.map((tag) => <span key={tag}>{tag}</span>)}</div></div>
      <button onClick={onSelect}>{selected ? "保持这个镜头" : `选择 ${candidate.label}`}</button>
    </article>
  );
}

function CandidatesPage({ projectId, next, back }: { projectId: string | null; next: () => void; back: () => void }) {
  const [groups, setGroups] = useState<CandidateGroup[]>([]);
  const [groupIndex, setGroupIndex] = useState(0);
  const group = groups[groupIndex];
  const [selected, setSelected] = useState<string | null>(group?.selected_shot_id || null);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!projectId) return;
    json<{ groups: Array<{ id: string; role: string; selected_shot_id?: string; candidates: Array<{ id: string; label: "A" | "B" | "C"; preview?: string }> }> }>(`/projects/${projectId}/candidate-groups`)
      .then((payload) => {
        if (!payload.groups.length) throw new Error("没有生成可审核候选。");
        setGroups(payload.groups.map((item) => ({
          id: item.id, role: item.role, selected_shot_id: item.selected_shot_id,
          candidates: item.candidates.map((candidate, index) => ({
            ...candidate,
            note: ["角色辨识最强", "动作与落点最贴合", "构图反差最大"][index],
            tags: [["角色", "清晰"], ["动作", "高能"], ["构图", "反差"]][index],
          })),
        })));
      }).catch((caught) => setError(caught instanceof Error ? caught.message : "候选读取失败"));
  }, [projectId]);
  useEffect(() => {
    setSelected(groups[groupIndex]?.selected_shot_id || null);
  }, [groups, groupIndex]);

  const choose = async (candidate: Candidate) => {
    try {
      await json(`/candidate-groups/${group.id}/selection`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ shot_id: candidate.id, context: { role: group.role }, project_style: "current" }),
      });
      setSelected(candidate.id);
      setGroups((items) => items.map((item) => item.id === group.id ? { ...item, selected_shot_id: candidate.id } : item));
      setError("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "选择保存失败");
    }
  };
  const aiChoose = async () => {
    try {
      const result = await json<{ selected_shot_id: string }>(`/candidate-groups/${group.id}/ai-selection`, { method: "POST" });
      const candidate = group.candidates.find((item) => item.id === result.selected_shot_id);
      if (!candidate) throw new Error("AI 返回了组外镜头");
      setSelected(candidate.id);
      setGroups((items) => items.map((item) => item.id === group.id ? { ...item, selected_shot_id: candidate.id } : item));
      setError("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "AI 决策失败");
    }
  };
  const finish = async () => {
    if (!projectId) return;
    if (groups.some((item) => !item.selected_shot_id)) {
      setError("请完成所有候选组，或逐组选择“让 AI 决定”。");
      return;
    }
    setWorking(true);
    try {
      await json(`/projects/${projectId}/first-cut`, { method: "POST" });
      next();
    } finally {
      setWorking(false);
    }
  };
  if (!group) return <section className="page candidate-page"><PageHeader eyebrow="03 / Candidates" title="正在读取候选…" note="这里只展示真实生成的候选，不使用演示数据。" />{error && <p className="form-error">{error}</p>}</section>;
  return (
    <section className="page candidate-page">
      <PageHeader eyebrow={`03 / Candidates · ${groupIndex + 1} of ${groups.length}`} title={group.role} note="每一组只留三个真正有差异的选择。你也可以交给 AI。" />
      <div className="candidate-grid">{group.candidates.map((candidate) => <CandidateCard key={candidate.id} candidate={candidate} selected={candidate.id === selected} onSelect={() => void choose(candidate)} />)}</div>
      <div className="ai-choice"><Sparkles /><span><b>不想选？</b><small>AI 会使用已保存的上下文评分，不固定选择某个位置。</small></span><button onClick={() => void aiChoose()}>让 AI 决定</button></div>
      {error && <p className="form-error">{error}</p>}
      <footer className="page-actions"><button className="quiet" onClick={back}><ArrowLeft /> 返回</button><div className="group-dots">{groups.map((item, index) => <button key={item.id} className={index === groupIndex ? "active" : ""} onClick={() => { setGroupIndex(index); setSelected(groups[index].selected_shot_id || null); }} aria-label={`候选组 ${index + 1}`} />)}</div><button className="primary" disabled={groups.some((item) => !item.selected_shot_id) || working} onClick={() => void finish()}>{working ? "正在生成第一版…" : "看第一版"} <ArrowRight /></button></footer>
    </section>
  );
}

function CutPage({ projectId, next, back }: { projectId: string | null; next: () => void; back: () => void }) {
  const [summary, setSummary] = useState<ProjectSummary | null>(null);
  useEffect(() => {
    if (!projectId) return;
    json<ProjectSummary>(`/projects/${projectId}/summary`).then(setSummary).catch(() => setSummary(null));
  }, [projectId]);
  const ready = Boolean(projectId && summary?.ready);
  const roles = Array.from(new Set((summary?.roles || []).filter(Boolean))).slice(0, 7);
  return (
    <section className="page cut-page">
      <PageHeader eyebrow="04 / First Cut" title={ready ? "第一版已经剪好。" : "第一版尚未生成。"} note={ready ? "先完整看一遍。细节问题可以直接用一句话修改。" : "完成候选选择后，这里才会展示真实 Resolve 预览。"} />
      <div className="screening-room">
        <div className="player">
          <div className="player-art">
            {ready ? <video src={`/projects/${projectId}/preview`} controls preload="metadata" /> : <button disabled aria-label="预览尚未生成"><Play /></button>}
            <span>{ready ? `${summary?.duration_sec?.toFixed(2)}s` : "--:--"}</span>
          </div>
          <div className="cut-strip">{roles.map((role, index) => <i key={`${role}-${index}`} className={role} title={role} />)}<b /></div>
        </div>
        <aside className="cut-notes">
          <span>导演自检</span>
          <h2>{ready ? "结构已生成，可以开始审美修改。" : "等待真实初剪与检查结果。"}</h2>
          <ul><li>{ready ? <Check /> : "○"} EditSpec 已验证</li><li>{ready ? <Check /> : "○"} Resolve 预览已生成</li><li>{ready ? <Check /> : "○"} 技术与创意检查分离</li></ul>
          <div className="watch-card"><b>{ready ? `${summary?.duration_sec?.toFixed(1)} 秒` : "等待生成"}</b><span>{ready ? `${summary?.clip_count} 个镜头 · 4:5 · Resolve 预览` : "无占位结果"}</span></div>
        </aside>
      </div>
      <footer className="page-actions"><button className="quiet" onClick={back}><ArrowLeft /> 返回选镜</button><button className="secondary" disabled={!ready} onClick={next}>我想改一点</button><button className="primary" disabled={!ready} onClick={next}>继续确认 <ArrowRight /></button></footer>
    </section>
  );
}

function RevisionPage({ projectId, next, back }: { projectId: string | null; next: () => void; back: () => void }) {
  const examples = ["第 8 秒不够炸", "第二段太快", "结尾镜头换掉", "人物再靠中一点"];
  const [message, setMessage] = useState("");
  const [messages, setMessages] = useState<Array<{ text: string; state: string }>>([
    { text: projectId ? "第一版已准备好。直接告诉我哪里不对。" : "请先完成真实初剪。", state: "assistant" },
  ]);
  const [revision, setRevision] = useState<RevisionResponse | null>(null);
  const [working, setWorking] = useState(false);
  const [locking, setLocking] = useState(false);
  useEffect(() => {
    if (!projectId) return;
    json<RevisionResponse & { status: string }>(`/projects/${projectId}/revision-status`)
      .then((result) => {
        if (result.status === "complete") setRevision(result);
      })
      .catch(() => undefined);
  }, [projectId]);
  const send = async () => {
    if (!message.trim()) return;
    const feedback = message;
    setMessages((items) => [...items, { text: feedback, state: "user" }]);
    setMessage("");
    if (!projectId) {
      setMessages((items) => [...items, { text: "请先打开一个真实项目。", state: "assistant" }]);
      return;
    }
    setWorking(true);
    try {
      const result = await json<RevisionResponse>(`/projects/${projectId}/revision`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ feedback }),
      });
      setRevision(result);
      setMessages((items) => [...items, {
        text: `V${result.to_version} 已生成：只修改 ${result.operations} 处，共 ${result.changed_duration_sec.toFixed(3)} 秒。`,
        state: "assistant",
      }]);
    } catch (error) {
      setMessages((items) => [...items, {
        text: `修改未执行：${error instanceof Error ? error.message : "未知错误"}`,
        state: "assistant",
      }]);
    } finally {
      setWorking(false);
    }
  };
  const lock = async () => {
    if (!projectId) return;
    setLocking(true);
    try {
      await json(`/projects/${projectId}/lock`, { method: "POST" });
      next();
    } catch (error) {
      setMessages((items) => [...items, {
        text: `母版未生成：${error instanceof Error ? error.message : "未知错误"}`,
        state: "assistant",
      }]);
    } finally {
      setLocking(false);
    }
  };
  return (
    <section className="page revision-page">
      <PageHeader eyebrow="05 / Revision" title="像和剪辑师说话一样修改。" note="锁定的镜头不会被改；每次修改都能撤回。" />
      <div className="revision-layout">
        <div className="revision-preview">
          <div className="mini-player">{projectId ? <video key={revision?.to_version} src={`/projects/${projectId}/preview`} controls preload="metadata" /> : <Play />}<span>{revision ? `V${revision.to_version} 预览` : "等待修改"}</span></div>
          <div className="change-map"><p><b>{revision ? `本轮只改 ${revision.operations} 处` : "尚未执行修改"}</b><span>{revision ? `待渲 ${revision.changed_duration_sec.toFixed(3)} 秒` : "输入反馈后显示真实变化"}</span></p><div>{revision?.changed_ranges.map(([start, end]) => <i key={start} style={{ left: `${start / 25 * 100}%`, width: `${(end - start) / 25 * 100}%` }} />)}</div></div>
        </div>
        <div className="conversation">
          <div className="messages">{messages.map((item, index) => <div key={index} className={`message ${item.state}`}>{item.text}</div>)}</div>
          <div className="suggestions">{examples.map((item) => <button key={item} onClick={() => setMessage(item)}>{item}</button>)}</div>
          <div className="composer"><textarea disabled={working} value={message} onChange={(event) => setMessage(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void send(); } }} placeholder="例如：Drop 再狠一点，结尾换成安静的近景" /><button disabled={working} onClick={() => void send()} aria-label="发送修改">{working ? <RefreshCw /> : <Send />}</button></div>
        </div>
      </div>
      <footer className="page-actions"><button className="quiet" onClick={back}><ArrowLeft /> 返回预览</button><button className="primary" disabled={locking || !projectId} onClick={() => void lock()}>{locking ? "正在生成母版…" : "锁定画面并出片"} <ArrowRight /></button></footer>
    </section>
  );
}

function FinalPage({ projectId, back }: { projectId: string | null; back: () => void }) {
  const checks = ["文件完整", "时长正确", "画面尺寸", "帧率", "编码", "音轨", "响度", "无异常黑帧", "无冻结帧", "无缺帧", "无损坏", "无意外静音", "画幅正确"];
  const [delivery, setDelivery] = useState<Delivery>({
    status: "pending", passed: false, checks: [],
  });
  const [published, setPublished] = useState(false);
  const [kpis, setKpis] = useState<ProjectKpis | null>(null);
  useEffect(() => {
    if (!projectId) return;
    json<Delivery>(`/projects/${projectId}/delivery`)
      .then(setDelivery)
      .catch(() => setDelivery({ status: "unavailable", passed: false, checks: [] }));
    json<ProjectKpis>(`/projects/${projectId}/kpis`)
      .then(setKpis)
      .catch(() => setKpis(null));
  }, [projectId]);
  const complete = delivery.passed && delivery.checks.length === 13;
  const markPublished = async () => {
    if (!projectId || !complete) return;
    await json(`/projects/${projectId}/publish`, { method: "POST" });
    setPublished(true);
  };
  const technical = Object.fromEntries(
    delivery.checks.map((item) => [item.name, item.measured]),
  );
  const kpiRows: Array<[string, KpiMetric | undefined]> = [
    ["首版预览", kpis?.time_to_first_preview_sec],
    ["候选选择", kpis?.candidate_selection_count],
    ["人工候选准确率", kpis?.candidate_precision],
    ["锁片修改轮数", kpis?.revision_count_to_lock],
    ["初剪保留率", kpis?.first_cut_survival_rate],
    ["技术首过率", kpis?.technical_qa_pass_rate],
  ];
  const formatKpi = (metric?: KpiMetric) => {
    if (!metric || metric.value === null) return "待采集";
    return metric.target.includes("0.") && metric.value <= 1
      ? `${Math.round(metric.value * 100)}%`
      : Number.isInteger(metric.value) ? String(metric.value) : metric.value.toFixed(1);
  };
  return (
    <section className="page final-page">
      <PageHeader
        eyebrow="06 / Final"
        title={complete ? "成片已通过交付检查。" : "正在等待真实母版检查。"}
        note="创意判断与你的确认分开；技术问题不会被“感觉不错”掩盖。"
      />
      <div className="final-layout">
        <div className="master-card"><div className="master-preview">{complete ? <video src={`/projects/${projectId}/download`} controls preload="metadata" /> : <Play />}<span>{complete ? "MASTER · READY" : "MASTER · PENDING"}</span></div><div className="master-meta"><div><Film /><span><b>{projectId || "未选择项目"}</b><small>{complete ? `${Array.isArray(technical.resolution) ? technical.resolution.join(" × ") : technical.resolution} · ${technical.fps} fps · ${String(technical.codec).toUpperCase()}` : "真实母版生成后显示技术参数"}</small></span></div><a className={complete ? "download-button" : "download-button disabled"} href={complete ? `/projects/${projectId}/download` : undefined}><Download /> 下载成片</a></div></div>
        <div className={`delivery-check ${complete ? "" : "pending"}`}>
          <div className="pass-seal">{complete ? <Check /> : <RefreshCw />}<span><b>{complete ? "13 / 13" : "0 / 13"}</b><small>{complete ? "技术检查通过" : "尚无通过记录"}</small></span></div>
          <div className="check-grid">{checks.map((item, index) => <span key={item}>{complete && delivery.checks[index]?.passed ? <Check /> : "○"} {item}</span>)}</div>
          <button className="publish" disabled={!complete || published} onClick={() => void markPublished()}><Sparkles /> {published ? "已记录发布" : "确认已发布"}</button>
        </div>
      </div>
      <section className="kpi-panel" aria-label="项目 KPI">
        <div><b>完成证据</b><span>缺少数据会明确显示“待采集”，不会自动判定通过。</span></div>
        <div className="kpi-grid">
          {kpiRows.map(([label, metric]) => (
            <article key={label} className={metric?.status || "insufficient_data"}>
              <span>{label}</span><b>{formatKpi(metric)}</b>
              <small>{metric?.target || "等待项目数据"}</small>
            </article>
          ))}
        </div>
      </section>
      <footer className="page-actions"><button className="quiet" onClick={back}><ArrowLeft /> 返回修改</button><span>所有版本和选择都已保存</span><a className={`primary ${complete ? "" : "disabled"}`} href={complete ? `/projects/${projectId}/download` : undefined}><Download /> 下载交付母版</a></footer>
    </section>
  );
}

export function ReviewApp() {
  const [projectId, setProjectId] = useState<string | null>(new URLSearchParams(window.location.search).get("project"));
  const initial = (new URLSearchParams(window.location.search).get("page") as Page) || "project";
  const [page, setPage] = useState<Page>(pages.some((item) => item.id === initial) ? initial : "project");
  const index = pages.findIndex((item) => item.id === page);
  const go = (next: Page) => {
    setPage(next);
    const url = new URL(window.location.href);
    url.searchParams.set("page", next);
    window.history.replaceState({}, "", url);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };
  const setProject = (id: string) => {
    setProjectId(id);
    const url = new URL(window.location.href);
    url.searchParams.set("project", id);
    window.history.replaceState({}, "", url);
  };
  const next = () => go(pages[Math.min(index + 1, pages.length - 1)].id);
  const back = () => go(pages[Math.max(index - 1, 0)].id);
  const content = useMemo(() => {
    if (page === "project") return <ProjectPage next={next} onCreated={setProject} />;
    if (page === "reference") return <ReferencePage projectId={projectId} next={next} back={back} />;
    if (page === "candidates") return <CandidatesPage projectId={projectId} next={next} back={back} />;
    if (page === "cut") return <CutPage projectId={projectId} next={next} back={back} />;
    if (page === "revision") return <RevisionPage projectId={projectId} next={next} back={back} />;
    return <FinalPage projectId={projectId} back={back} />;
  }, [page, projectId]);
  return <main className="app-shell"><StepRail page={page} onChange={go} /><div className="main-stage"><div className="top-utility"><span><i /> 项目进行中</span><button><MessageSquareText /> 帮助</button></div>{content}</div></main>;
}
