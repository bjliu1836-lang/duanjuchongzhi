import { ChangeEvent, DragEvent, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

const API_BASE = (import.meta.env.VITE_API_BASE ?? "").replace(/\/$/, "");
type Media = { id: string; episodeId: string; name: string; kind: string; source: string; fileUrl: string; thumbnailUrl?: string; durationMs: number; width: number; height: number; hasAudio: boolean; sizeBytes?: number; storyboardId?: string; storyboardTitle?: string; isSelected?: boolean; createdAt?: string };
type Clip = { id: string; sourceId: string; inMs: number; outMs: number; order: number };
type Draft = { project_id: string; episode_id: string; title: string; revision: number; updated_at: string; edit: { aspectRatio: string; resolution: string; fps: number; clips: Clip[] } };
type ExportJob = { id: string; status: string; progress: number; stage: string; error: string; revision: number; downloadUrl?: string; output_duration_ms?: number };
async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, init);
  if (!response.ok) throw new Error((await response.text()) || response.statusText);
  return response.json() as Promise<T>;
}
function fmt(ms: number) { const s = Math.max(0, ms) / 1000; return `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`; }

export type ComparisonStoryboard = {
  storyboardId: string;
  title: string;
  generationId: string | null;
  originalStartMs: number;
  originalEndMs: number;
};

type ComparisonClip = ComparisonStoryboard & {
  media?: Media;
  startMs: number;
  durationMs: number;
};

function OriginalComparison({ assets, items, episodeIndex, onExit, onMessage }: {
  assets: Media[];
  items: ComparisonStoryboard[];
  episodeIndex?: number;
  onExit: () => void;
  onMessage: (message: string) => void;
}) {
  const original = assets.find((item) => item.kind === "episode_video");
  const clips = useMemo(() => {
    let offset = 0;
    return items.map((item): ComparisonClip => {
      const media = assets.find((asset) => asset.id === item.generationId && asset.kind === "generated_video");
      const originalDuration = Math.max(0, item.originalEndMs - item.originalStartMs);
      const durationMs = Math.max(1, media?.durationMs || originalDuration || 4000);
      const clip = { ...item, media, startMs: offset, durationMs };
      offset += durationMs;
      return clip;
    });
  }, [assets, items]);
  const generatedTotalMs = clips.reduce((sum, clip) => sum + clip.durationMs, 0);
  const originalDurationMs = Math.max(original?.durationMs || 0, ...items.map((item) => item.originalEndMs), 1);
  const totalMs = Math.max(originalDurationMs, generatedTotalMs, 1);
  const [playhead, setPlayhead] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [audioSide, setAudioSide] = useState<"original" | "generated">("generated");
  const playheadRef = useRef(0);
  const playingRef = useRef(false);
  const originalRef = useRef<HTMLVideoElement>(null);
  const generatedRef = useRef<HTMLVideoElement>(null);
  const frameRef = useRef<number | null>(null);
  const lastTickRef = useRef(0);
  const lastCorrectionRef = useRef(0);
  const activeClip = clips.find((clip) => playhead >= clip.startMs && playhead < clip.startMs + clip.durationMs) ?? clips[clips.length - 1];
  const activeClipId = activeClip?.storyboardId ?? "";
  const playheadRatio = `${Math.min(100, playhead / totalMs * 100)}%`;

  function changePlaying(value: boolean) {
    playingRef.current = value;
    setPlaying(value);
  }

  function alignVideoToTime(video: HTMLVideoElement | null, targetMs: number, sourceDurationMs: number) {
    if (!video || !Number.isFinite(video.duration)) return;
    const targetSeconds = Math.max(0, Math.min(targetMs / 1000, Math.max(0, sourceDurationMs / 1000 - 0.001)));
    if (Math.abs(video.currentTime - targetSeconds) > 0.04) video.currentTime = targetSeconds;
  }

  function seekTo(value: number) {
    const target = Math.max(0, Math.min(value, totalMs));
    playheadRef.current = target;
    setPlayhead(target);
    alignVideoToTime(originalRef.current, target, originalDurationMs);
    const targetClip = clips.find((clip) => target >= clip.startMs && target < clip.startMs + clip.durationMs) ?? clips[clips.length - 1];
    if (targetClip?.media && generatedRef.current?.dataset.storyboardId === targetClip.storyboardId) {
      alignVideoToTime(generatedRef.current, target - targetClip.startMs, targetClip.durationMs);
    }
  }

  function togglePlayback() {
    if (playingRef.current) {
      changePlaying(false);
      return;
    }
    if (playheadRef.current >= totalMs - 1) seekTo(0);
    changePlaying(true);
  }

  useEffect(() => {
    playingRef.current = playing;
    const originalVideo = originalRef.current;
    if (originalVideo) {
      originalVideo.muted = audioSide !== "original";
      if (playing && playheadRef.current < originalDurationMs) void originalVideo.play().catch(() => undefined);
      else originalVideo.pause();
    }
    const generatedVideo = generatedRef.current;
    if (generatedVideo) {
      generatedVideo.muted = audioSide !== "generated";
      if (playing && activeClip?.media && playheadRef.current < generatedTotalMs) void generatedVideo.play().catch(() => undefined);
      else generatedVideo.pause();
    }
  }, [playing, audioSide, activeClipId, originalDurationMs, generatedTotalMs]);

  useEffect(() => {
    if (!playing) {
      if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
      frameRef.current = null;
      return;
    }
    lastTickRef.current = performance.now();
    const tick = (now: number) => {
      const elapsed = Math.min(250, now - lastTickRef.current);
      lastTickRef.current = now;
      const next = Math.min(totalMs, playheadRef.current + elapsed);
      playheadRef.current = next;
      setPlayhead(next);
      if (now - lastCorrectionRef.current > 450) {
        lastCorrectionRef.current = now;
        if (next < originalDurationMs && originalRef.current && Math.abs(originalRef.current.currentTime * 1000 - next) > 220) {
          alignVideoToTime(originalRef.current, next, originalDurationMs);
        }
        const currentClip = clips.find((clip) => next >= clip.startMs && next < clip.startMs + clip.durationMs) ?? clips[clips.length - 1];
        if (currentClip?.media && generatedRef.current?.dataset.storyboardId === currentClip.storyboardId) {
          const localMs = next - currentClip.startMs;
          if (Math.abs(generatedRef.current.currentTime * 1000 - localMs) > 220) alignVideoToTime(generatedRef.current, localMs, currentClip.durationMs);
        }
      }
      if (next >= totalMs) {
        changePlaying(false);
        return;
      }
      frameRef.current = requestAnimationFrame(tick);
    };
    frameRef.current = requestAnimationFrame(tick);
    return () => {
      if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
      frameRef.current = null;
    };
  }, [playing, totalMs, originalDurationMs, clips]);

  useEffect(() => () => {
    if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
    originalRef.current?.pause();
    generatedRef.current?.pause();
  }, []);

  function onOriginalLoaded(video: HTMLVideoElement) {
    alignVideoToTime(video, playheadRef.current, originalDurationMs);
    video.muted = audioSide !== "original";
    if (playingRef.current && playheadRef.current < originalDurationMs) void video.play().catch(() => undefined);
  }

  function onGeneratedLoaded(video: HTMLVideoElement) {
    const currentClip = clips.find((clip) => clip.storyboardId === video.dataset.storyboardId);
    if (!currentClip?.media) return;
    alignVideoToTime(video, playheadRef.current - currentClip.startMs, currentClip.durationMs);
    video.muted = audioSide !== "generated";
    if (playingRef.current && activeClipId === currentClip.storyboardId) void video.play().catch(() => undefined);
  }

  function timelineTrack(side: "original" | "generated") {
    return <div className="comparison-timeline-track">
      {side === "original" ? items.map((item, index) => {
        const start = Math.max(0, item.originalStartMs);
        const end = Math.max(start + 1, item.originalEndMs || start + 4000);
        return <button type="button" key={item.storyboardId} className="comparison-track-clip original" style={{ left: `${start / totalMs * 100}%`, width: `${Math.max(1.5, (end - start) / totalMs * 100)}%` }} onClick={() => seekTo(start)} title={`${item.title} · ${fmt(start)}`}>{index + 1}</button>;
      }) : clips.map((clip, index) => <button type="button" key={clip.storyboardId} className={`comparison-track-clip generated${clip.media ? "" : " missing"}`} style={{ left: `${clip.startMs / totalMs * 100}%`, width: `${Math.max(1.5, clip.durationMs / totalMs * 100)}%` }} onClick={() => seekTo(clip.startMs)} title={`${clip.title}${clip.media ? ` · ${fmt(clip.durationMs)}` : " · 待生成"}`}>{clip.media ? index + 1 : "—"}</button>)}
      <i className="comparison-shared-playhead" style={{ left: playheadRatio }} />
    </div>;
  }

  function timeline(side: "original" | "generated") {
    return <div className="comparison-timeline">
      <div className="comparison-timeline-ruler">{[0, .25, .5, .75, 1].map((point) => <span key={point}>{fmt(totalMs * point)}</span>)}</div>
      <input aria-label={side === "original" ? "原片时间轴，拖动时生成侧同步" : "生成片时间轴，拖动时原片同步"} type="range" min="0" max={totalMs} step="50" value={playhead} onChange={(event) => seekTo(Number(event.target.value))} />
      {timelineTrack(side)}
      <div className="comparison-timeline-caption"><span>{side === "original" ? "原片分镜区间 · 点击片段定位" : "当前采用版本 · 虚线为待生成缺口"}</span><b>{fmt(playhead)} / {fmt(totalMs)}</b></div>
    </div>;
  }

  return <section className="composition-workspace original-comparison">
    <header className="composition-toolbar">
      <div><b>第{episodeIndex ?? ""}集 · 原片同步对比</b><span className="save-state saved">● 从 0 秒共用绝对时间</span></div>
      <div className="composition-settings">
        <button onClick={togglePlayback}>{playing ? "同步暂停" : "同步播放"}</button>
        <button onClick={() => setAudioSide((side) => side === "generated" ? "original" : "generated")}>声音：{audioSide === "generated" ? "生成片" : "原片"}</button>
        <button onClick={() => seekTo(0)}>回到 0 秒</button>
        <button className="export-action" onClick={onExit}>退出对比</button>
      </div>
    </header>
    <div className="original-comparison-grid">
      <section className="original-comparison-panel">
        <div className="comparison-panel-head"><b>原片完整视频</b><span>{fmt(originalDurationMs)}</span></div>
        <div className="comparison-video-frame">
          {original ? <video ref={originalRef} src={`${API_BASE}${original.fileUrl}`} playsInline onLoadedMetadata={(event) => onOriginalLoaded(event.currentTarget)} onError={() => onMessage("原片无法播放，请检查视频编码")} /> : <div className="preview-empty">当前集没有可播放原片</div>}
        </div>
        {timeline("original")}
      </section>
      <section className="original-comparison-panel">
        <div className="comparison-panel-head"><b>新生成整集 · {activeClip?.title ?? "暂无分镜"}</b><span>{fmt(generatedTotalMs)}</span></div>
        <div className="comparison-video-frame">
          {activeClip?.media ? <video key={activeClip.storyboardId} data-storyboard-id={activeClip.storyboardId} ref={generatedRef} src={`${API_BASE}${activeClip.media.fileUrl}`} playsInline onLoadedMetadata={(event) => onGeneratedLoaded(event.currentTarget)} onError={() => onMessage(`${activeClip.title}生成视频无法播放`)} /> : <div className="preview-empty">{activeClip ? `${activeClip.title}尚未生成，时间轴保留对应缺口` : "暂无可播放的生成片段"}</div>}
          <span className="comparison-version-label">{activeClip?.media ? "当前采用版本" : "待生成"}</span>
        </div>
        {timeline("generated")}
      </section>
    </div>
  </section>;
}

export function VideoComposition({ projectId, episodeId, episodeIndex, comparisonItems = [], active, onMessage }: { projectId?: string; episodeId?: string; episodeIndex?: number; comparisonItems?: ComparisonStoryboard[]; active: boolean; onMessage: (message: string) => void }) {
  const [assets, setAssets] = useState<Media[]>([]);
  const [comparisonMode, setComparisonMode] = useState(false);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [jobs, setJobs] = useState<ExportJob[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [versionMenu, setVersionMenu] = useState<{ assetId: string; storyboardId: string } | null>(null);
  // 菜单用 fixed 定位，避开素材列表 overflow 裁切；记录点击按钮位置，靠近底部时向上展开。
  const [versionMenuPos, setVersionMenuPos] = useState<{ top: number; left: number }>({ top: 0, left: 0 });
  const [busy, setBusy] = useState(false);
  const [saveState, setSaveState] = useState("已保存");
  const [playhead, setPlayhead] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [activeMediaId, setActiveMediaId] = useState("");
  const [libraryCollapsed, setLibraryCollapsed] = useState(false);
  const [inspectorCollapsed, setInspectorCollapsed] = useState(false);
  const [playingClipId, setPlayingClipId] = useState("");
  const [seekOnLoad, setSeekOnLoad] = useState<number | null>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLDivElement>(null);
  const [frameSize, setFrameSize] = useState<{ width: number; height: number } | null>(null);
  const uploadRef = useRef<HTMLInputElement>(null);
  const dragIndex = useRef<number | null>(null);
  const loadedEpisodeKey = useRef("");
  const activeEpisodeKey = useRef("");
  const refreshSequence = useRef(0);
  const clips = draft?.edit.clips ?? [];
  const totalMs = clips.reduce((sum, clip) => sum + Math.max(0, clip.outMs - clip.inMs), 0);
  const clipSignature = clips.map((clip) => `${clip.id}:${clip.sourceId}:${clip.inMs}:${clip.outMs}:${clip.order}`).join("|");
  const selectedClip = clips.find((clip) => clip.id === selected) ?? null;
  const selectedMedia = assets.find((item) => item.id === selectedClip?.sourceId) ?? null;
  const filteredAssets = useMemo(() => assets.filter((asset) => `${asset.name} ${asset.source}`.toLowerCase().includes(query.toLowerCase())), [assets, query]);
  // 分镜生成视频按 storyboardId 归组，组内按创建时间升序即为 v1..vN，供「替换」下拉展示。
  const versionGroups = useMemo(() => {
    const groups = new Map<string, Media[]>();
    for (const asset of assets) {
      if (!asset.storyboardId) continue;
      const list = groups.get(asset.storyboardId) ?? [];
      list.push(asset);
      groups.set(asset.storyboardId, list);
    }
    for (const list of groups.values()) list.sort((a, b) => String(a.createdAt ?? "").localeCompare(String(b.createdAt ?? "")));
    return groups;
  }, [assets]);
  useEffect(() => {
    if (!versionMenu) return;
    const close = () => setVersionMenu(null);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [versionMenu]);

  function formatVersionDate(value?: string) {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "";
    const pad = (n: number) => String(n).padStart(2, "0");
    return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
  }

  async function refresh(): Promise<boolean> {
    if (!projectId || !episodeId) return false;
    const key = projectId + ":" + episodeId;
    const sequence = ++refreshSequence.current;
    const [mediaData, draftData, jobData] = await Promise.all([
      api<{ assets: Media[] }>(`/api/projects/${projectId}/episodes/${episodeId}/video-assets`),
      api<{ draft: Draft }>(`/api/projects/${projectId}/video-edits/${episodeId}`),
      api<{ jobs: ExportJob[] }>(`/api/projects/${projectId}/video-export-jobs?episode_id=${episodeId}`),
    ]);
    if (sequence !== refreshSequence.current || key !== activeEpisodeKey.current) return false;
    setAssets(mediaData.assets); setDraft(draftData.draft); setJobs(jobData.jobs);
    if (!selected && draftData.draft.edit.clips.length) setSelected(draftData.draft.edit.clips[0].id);
    return true;
  }
  useEffect(() => { activeEpisodeKey.current = projectId && episodeId ? projectId + ":" + episodeId : ""; refreshSequence.current += 1; loadedEpisodeKey.current = ""; setDraft(null); setAssets([]); setSelected(null); setPlayhead(0); setComparisonMode(false); }, [projectId, episodeId]);
  useEffect(() => {
    const key = projectId && episodeId ? projectId + ":" + episodeId : "";
    if (!active || !key || loadedEpisodeKey.current === key) return;
    void refresh().then((loaded) => { if (loaded && activeEpisodeKey.current === key) loadedEpisodeKey.current = key; }).catch((error) => onMessage(error.message));
  }, [projectId, episodeId, active]);
  useEffect(() => {
    const media = window.matchMedia("(max-width: 1100px)");
    const sync = () => { if (media.matches) { setLibraryCollapsed(true); setInspectorCollapsed(true); } };
    sync(); media.addEventListener("change", sync); return () => media.removeEventListener("change", sync);
  }, []);
  useEffect(() => {
    if (!active || !projectId || !episodeId || !jobs.some((job) => ["queued", "running"].includes(job.status))) return;
    const timer = window.setInterval(() => api<{ jobs: ExportJob[] }>(`/api/projects/${projectId}/video-export-jobs?episode_id=${episodeId}`).then((data) => setJobs(data.jobs)).catch(() => {}), 1200);
    return () => window.clearInterval(timer);
  }, [projectId, episodeId, jobs, active]);
  useEffect(() => {
    if (!activeMediaId || seekOnLoad === null || !videoRef.current) return;
    const video = videoRef.current;
    const seek = () => { video.currentTime = seekOnLoad / 1000; setSeekOnLoad(null); if (playing) void video.play().catch(() => setPlaying(false)); };
    if (video.readyState >= 1) seek(); else video.addEventListener("loadedmetadata", seek, { once: true });
    return () => video.removeEventListener("loadedmetadata", seek);
  }, [activeMediaId, seekOnLoad, playing]);
  useEffect(() => {
    if (!totalMs) { setPlayhead(0); setPlaying(false); return; }
    const bounded = Math.min(playhead, totalMs - 1);
    seekTo(bounded);
  }, [clipSignature, totalMs]);

  const frameRatio = draft?.edit.aspectRatio ?? "9:16";
  useLayoutEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !active) { setFrameSize(null); return; }
    const ratio = frameRatio === "16:9" ? 16 / 9 : frameRatio === "1:1" ? 1 : 9 / 16;
    const update = () => {
      const rect = canvas.getBoundingClientRect();
      const availWidth = Math.max(120, rect.width - 24);
      const availHeight = Math.max(120, rect.height - 24);
      const width = Math.round(Math.min(availWidth, availHeight * ratio));
      const height = Math.round(width / ratio);
      setFrameSize((current) => (current && Math.abs(current.width - width) <= 1 && Math.abs(current.height - height) <= 1 ? current : { width, height }));
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(canvas);
    return () => observer.disconnect();
  }, [active, frameRatio]);
  const frameStyle = frameSize ? { width: `${frameSize.width}px`, height: `${frameSize.height}px` } : undefined;

  function patchDraft(edit: Draft["edit"]) { setDraft((current) => current ? { ...current, edit } : current); setSaveState("有未保存修改"); }
  function addAsset(asset: Media) {
    if (!draft) return;
    const next = [...clips, { id: `clip_${crypto.randomUUID()}`, sourceId: asset.id, inMs: 0, outMs: asset.durationMs, order: clips.length }];
    patchDraft({ ...draft.edit, clips: next.map((clip, index) => ({ ...clip, order: index })) });
    const target = next[next.length - 1];
    setSelected(target.id); setPlayingClipId(target.id); setActiveMediaId(asset.id); setSeekOnLoad(target.inMs);
    setPlayhead(totalMs);
  }
  // 「移出」：移除轨道上所有引用该素材的片段（确认过的交互决策）。
  function removeAsset(asset: Media) {
    if (!draft) return;
    const removed = clips.filter((clip) => clip.sourceId === asset.id);
    if (!removed.length) return;
    const next = clips.filter((clip) => clip.sourceId !== asset.id).map((clip, index) => ({ ...clip, order: index }));
    patchDraft({ ...draft.edit, clips: next });
    if (selected && removed.some((clip) => clip.id === selected)) setSelected(next[0]?.id ?? null);
    onMessage(`已从时间线移除 ${removed.length} 个片段`);
  }
  // 「替换」：把轨道上引用同一分镜任意历史版本的片段换成所选版本，出入点按新旧时长比例缩放。
  function replaceWithVersion(group: Media[], version: Media) {
    if (!draft) return;
    const versionIds = new Set(group.map((item) => item.id));
    const targets = clips.filter((clip) => versionIds.has(clip.sourceId));
    const title = version.storyboardTitle || "该分镜";
    if (!targets.length) {
      setVersionMenu(null);
      onMessage(`轨道上暂无「${title}」的片段，未做改动`);
      return;
    }
    const targetIds = new Set(targets.map((clip) => clip.id));
    const next = clips.map((clip) => {
      if (!targetIds.has(clip.id)) return clip;
      const oldMedia = assets.find((item) => item.id === clip.sourceId);
      const ratio = oldMedia && oldMedia.durationMs > 0 ? version.durationMs / oldMedia.durationMs : 1;
      const inMs = Math.min(Math.max(0, Math.round(clip.inMs * ratio)), Math.max(0, version.durationMs - 1));
      const outMs = Math.max(inMs + 1, Math.min(Math.max(inMs + 1, Math.round(clip.outMs * ratio)), version.durationMs));
      return { ...clip, sourceId: version.id, inMs, outMs };
    });
    patchDraft({ ...draft.edit, clips: next });
    setSelected(targets[0].id); setPlayingClipId(targets[0].id); setActiveMediaId(version.id);
    setVersionMenu(null);
    onMessage(`已将「${title}」的轨道素材替换为所选版本`);
  }
  function reorder(from: number, to: number) {
    if (!draft || from === to || to < 0 || to >= clips.length) return;
    const next = [...clips]; const [item] = next.splice(from, 1); next.splice(to, 0, item);
    patchDraft({ ...draft.edit, clips: next.map((clip, index) => ({ ...clip, order: index })) });
  }
  async function uploadVideo(event: ChangeEvent<HTMLInputElement>) {
    const file = event.currentTarget.files?.[0]; event.currentTarget.value = "";
    if (!file || !projectId || !episodeId) return;
    setBusy(true);
    try { const body = new FormData(); body.append("file", file); await api(`/api/projects/${projectId}/episodes/${episodeId}/video-assets`, { method: "POST", body }); await refresh(); onMessage(`已导入并检测 ${file.name}`); }
    catch (error) { onMessage(error instanceof Error ? error.message : "视频导入失败"); }
    finally { setBusy(false); }
  }
  async function saveDraft(): Promise<boolean> {
    if (!draft || !projectId || !episodeId) return false;
    setBusy(true); setSaveState("保存中");
    try {
      const data = await api<{ draft: Draft }>(`/api/projects/${projectId}/video-edits/${episodeId}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ revision: draft.revision, ...draft.edit }) });
      setDraft(data.draft); setSaveState("已保存"); onMessage(`剪辑草稿已保存 · revision ${data.draft.revision}`); return true;
    } catch (error) { setSaveState("保存冲突"); onMessage(error instanceof Error ? `${error.message}；当前编辑仍保留` : "保存失败；当前编辑仍保留"); }
    finally { setBusy(false); }
    return false;
  }
  async function exportSelection() {
    if (!projectId || !episodeId || !draft) return;
    setBusy(true);
    try {
      if (saveState !== "已保存" && !(await saveDraft())) return;
      const response = await api<{ job: ExportJob }>(`/api/projects/${projectId}/video-edits/${episodeId}/exports`, { method: "POST" });
      setJobs((items) => [response.job, ...items]); onMessage("已创建所选片段导出任务");
    } catch (error) { onMessage(error instanceof Error ? error.message : "导出任务创建失败"); }
    finally { setBusy(false); }
  }
  function seekTo(ms: number) {
    if (!clips.length) return;
    const target = Math.max(0, Math.min(ms, Math.max(0, totalMs - 1)));
    let offset = 0;
    for (const clip of clips) {
      const duration = clip.outMs - clip.inMs;
      if (target < offset + duration) { const sourceMs = clip.inMs + target - offset; setPlayhead(target); setSelected(clip.id); setPlayingClipId(clip.id); setActiveMediaId(clip.sourceId); setSeekOnLoad(sourceMs); return; }
      offset += duration;
    }
  }
  function playNextClip(clipId: string) {
    const index = clips.findIndex((clip) => clip.id === clipId);
    if (index < 0 || index + 1 >= clips.length) { setPlaying(false); return; }
    const next = clips[index + 1]; const prefix = clips.slice(0, index + 1).reduce((sum, clip) => sum + clip.outMs - clip.inMs, 0);
    setSelected(next.id); setPlayhead(prefix); setPlayingClipId(next.id); setActiveMediaId(next.sourceId); setSeekOnLoad(next.inMs);
  }
  function onTimeUpdate() {
    const video = videoRef.current; const clip = clips.find((item) => item.id === playingClipId && item.sourceId === activeMediaId);
    if (!video || !clip) return;
    if (video.currentTime * 1000 >= clip.outMs - 30) { playNextClip(clip.id); return; }
    const index = clips.findIndex((item) => item.id === clip.id);
    const prefix = clips.slice(0, index).reduce((sum, item) => sum + item.outMs - item.inMs, 0);
    setPlayhead(prefix + Math.max(0, video.currentTime * 1000 - clip.inMs));
  }
  function togglePlayback() {
    if (!videoRef.current || !clips.length) return;
    if (playing) { videoRef.current.pause(); setPlaying(false); }
    else {
      if (!activeMediaId) seekTo(playhead);
      setPlaying(true); void videoRef.current.play().catch((error) => { setPlaying(false); onMessage(`无法播放当前素材：${error.message}`); });
    }
  }
  function updateTrim(field: "inMs" | "outMs", value: number) {
    if (!draft || !selectedClip || !selectedMedia) return;
    const inMs = field === "inMs" ? value : selectedClip.inMs;
    const outMs = field === "outMs" ? value : selectedClip.outMs;
    const safeIn = Math.max(0, Math.min(inMs, selectedMedia.durationMs - 1));
    const safeOut = Math.max(safeIn + 1, Math.min(outMs, selectedMedia.durationMs));
    patchDraft({ ...draft.edit, clips: clips.map((clip) => clip.id === selectedClip.id ? { ...clip, inMs: safeIn, outMs: safeOut } : clip) });
  }
  function dropClip(event: DragEvent<HTMLDivElement>, targetIndex: number) { event.preventDefault(); if (dragIndex.current !== null) reorder(dragIndex.current, targetIndex); dragIndex.current = null; }
  if (!projectId || !episodeId) return <div className="composition-no-project">先选择项目和剧集，再导入要编辑的视频。</div>;
  const activeSource = assets.find((item) => item.id === activeMediaId) ?? assets.find((item) => item.id === clips[0]?.sourceId);

  if (comparisonMode) return <OriginalComparison assets={assets} items={comparisonItems} episodeIndex={episodeIndex} onExit={() => setComparisonMode(false)} onMessage={onMessage} />;

  return <section className={`composition-workspace${libraryCollapsed ? " library-collapsed" : ""}${inspectorCollapsed ? " inspector-collapsed" : ""}`}>
    <header className="composition-toolbar"><div><b>{draft?.title ?? `第${episodeIndex ?? ""}集合成`}</b><span className={saveState === "已保存" ? "save-state saved" : "save-state"}>● {saveState}</span><button onClick={() => setComparisonMode(true)}>和原片对比</button><button onClick={() => setLibraryCollapsed((value) => !value)}>{libraryCollapsed ? "显示素材" : "收起素材"}</button><button onClick={() => setInspectorCollapsed((value) => !value)}>{inspectorCollapsed ? "显示检查器" : "收起检查器"}</button>{saveState === "保存冲突" && <button onClick={() => refresh().then(() => setSaveState("已保存")).catch((error) => onMessage(error.message))}>重新加载服务器版本</button>}</div><div className="composition-settings"><label>画幅<select value={draft?.edit.aspectRatio ?? "9:16"} onChange={(event) => draft && patchDraft({ ...draft.edit, aspectRatio: event.target.value })}><option>9:16</option><option>16:9</option><option>1:1</option></select></label><label>分辨率<select value={draft?.edit.resolution ?? "720p"} onChange={(event) => draft && patchDraft({ ...draft.edit, resolution: event.target.value })}><option>720p</option><option>1080p</option></select></label><button disabled={busy || saveState === "已保存"} onClick={saveDraft}>保存草稿</button><button className="export-action" disabled={busy || !clips.length} onClick={exportSelection}>导出所选片段</button></div></header>
    <div className="composition-body">
      <aside className={`composition-library${libraryCollapsed ? " folded" : ""}`}><div className="library-head"><b>素材库</b><button onClick={() => uploadRef.current?.click()} disabled={busy}>导入本地视频</button><input ref={uploadRef} hidden type="file" accept="video/*,.mkv,.webm" onChange={uploadVideo} /></div><input className="media-search" placeholder="搜索素材" value={query} onChange={(event) => setQuery(event.target.value)} /><div className="media-list">{filteredAssets.map((asset) => {
        const added = clips.some((clip) => clip.sourceId === asset.id);
        const group = asset.storyboardId ? versionGroups.get(asset.storyboardId) ?? [] : [];
        const menuOpen = versionMenu?.assetId === asset.id && group.length > 0;
        return (
          <article className="media-item" key={asset.id}>
            {asset.thumbnailUrl ? <img className="media-thumbnail" src={`${API_BASE}${asset.thumbnailUrl}`} alt="" /> : <div className="media-icon">▶</div>}
            <div className="media-copy"><b title={asset.name}>{asset.name}</b><span>{fmt(asset.durationMs)} · {asset.width}×{asset.height}</span><small>{asset.storyboardTitle ? `${asset.storyboardTitle} · ` : ""}{asset.source}{asset.hasAudio ? " · 含音轨" : " · 无音轨"}</small></div>
            <div className="media-actions">
              {added
                ? <button className="media-remove" title="从时间线移除该素材的全部片段" onClick={() => removeAsset(asset)}>移出</button>
                : <button onClick={() => addAsset(asset)}>加入</button>}
              {asset.kind === "generated_video" && asset.storyboardId && group.length > 0 && (
                <button className={menuOpen ? "active" : ""} title="选择该分镜的历史版本替换轨道素材" onClick={(event) => {
                  event.stopPropagation();
                  if (menuOpen) { setVersionMenu(null); return; }
                  const rect = event.currentTarget.getBoundingClientRect();
                  setVersionMenuPos({
                    top: rect.bottom + 272 > window.innerHeight ? Math.max(8, rect.top - 276) : rect.bottom + 6,
                    left: Math.max(8, Math.min(rect.left, window.innerWidth - 268)),
                  });
                  setVersionMenu({ assetId: asset.id, storyboardId: asset.storyboardId as string });
                }}>替换</button>
              )}
            </div>
            {menuOpen && (
              <div className="media-version-menu" role="menu" style={{ top: versionMenuPos.top, left: versionMenuPos.left }} onMouseDown={(event) => event.stopPropagation()}>
                <b>{asset.storyboardTitle || "当前分镜"} · {group.length} 个历史版本</b>
                {[...group].reverse().map((version) => {
                  const used = clips.some((clip) => clip.sourceId === version.id);
                  const versionNo = group.findIndex((item) => item.id === version.id) + 1;
                  return (
                    <button type="button" key={version.id} className={used ? "in-use" : ""} onClick={() => replaceWithVersion(group, version)}>
                      {version.thumbnailUrl ? <img src={`${API_BASE}${version.thumbnailUrl}`} alt="" /> : <span className="media-icon">▶</span>}
                      <span className="version-copy"><b>v{versionNo} · {fmt(version.durationMs)}</b><small>{formatVersionDate(version.createdAt)}</small></span>
                      {used && <i className="version-tag use">使用中</i>}
                      {version.isSelected && <i className="version-tag adopted">当前采用</i>}
                    </button>
                  );
                })}
              </div>
            )}
          </article>
        );
      })}{!filteredAssets.length && <p className="composition-empty">当前集还没有可用视频。原片仅作参考，点击“加入”后才会进入时间线。</p>}</div></aside>
      <div className="composition-center"><div ref={canvasRef} className={`composition-canvas ratio-${(draft?.edit.aspectRatio ?? "9:16").replace(":", "-")}`}><div className="canvas-inner" style={frameStyle}>{activeSource ? <video ref={videoRef} src={`${API_BASE}${activeSource.fileUrl}`} controls={false} onTimeUpdate={onTimeUpdate} onEnded={() => selected && playNextClip(selected)} onError={() => onMessage("当前素材无法在浏览器播放，请重新导入兼容的视频编码")} /> : <div className="preview-empty">选择时间线片段预览</div>}</div></div><div className="transport"><button onClick={togglePlayback} disabled={!clips.length}>{playing ? "暂停" : "播放"}</button><span>{fmt(playhead)} / {fmt(totalMs)}</span><input aria-label="播放位置" type="range" min="0" max={Math.max(1, totalMs)} value={Math.min(playhead, totalMs)} onChange={(event) => seekTo(Number(event.target.value))} disabled={!clips.length} /><label>音量 <input type="range" min="0" max="1" step="0.05" defaultValue="1" onChange={(event) => { if (videoRef.current) videoRef.current.volume = Number(event.target.value); }} /></label></div></div>
      <aside className={`composition-inspector${inspectorCollapsed ? " folded" : ""}`}><b>片段检查器</b>{selectedClip && selectedMedia ? <><div className="inspector-name">{selectedMedia.name}</div><label>入点（毫秒）<input type="number" min="0" max={selectedMedia.durationMs - 1} value={selectedClip.inMs} onChange={(event) => updateTrim("inMs", Number(event.target.value))} /></label><label>出点（毫秒）<input type="number" min={selectedClip.inMs + 1} max={selectedMedia.durationMs} value={selectedClip.outMs} onChange={(event) => updateTrim("outMs", Number(event.target.value))} /></label><div className="inspector-meta">有效时长 <b>{fmt(selectedClip.outMs - selectedClip.inMs)}</b></div><div className="inspector-meta">源素材 <b>{selectedMedia.source}</b></div><div className="inspector-meta">顺序 <b>{clips.findIndex((clip) => clip.id === selectedClip.id) + 1} / {clips.length}</b></div><div className="inspector-buttons"><button onClick={() => reorder(clips.findIndex((clip) => clip.id === selectedClip.id), -1)}>上移</button><button onClick={() => reorder(clips.findIndex((clip) => clip.id === selectedClip.id), clips.findIndex((clip) => clip.id === selectedClip.id) + 1)}>下移</button><button onClick={() => { if (!draft) return; const next = clips.filter((clip) => clip.id !== selectedClip.id).map((clip, index) => ({ ...clip, order: index })); patchDraft({ ...draft.edit, clips: next }); setSelected(next[0]?.id ?? null); }}>移除</button></div></> : <p className="composition-empty">选择一个时间线片段后，可调整入点、出点和顺序。</p>}<div className="export-jobs"><b>导出任务</b>{jobs.map((job) => <article key={job.id}><span>{job.stage || job.status}</span><progress max="100" value={job.progress} /><small>{job.status === "failed" ? job.error : `${job.progress}% · revision ${job.revision}`}</small>{job.status === "succeeded" && job.downloadUrl && <a href={`${API_BASE}${job.downloadUrl}`} download>下载 MP4</a>}{job.status === "failed" && <button onClick={() => api<{ job: ExportJob }>(`/api/projects/${projectId}/video-export-jobs/${job.id}/retry`, { method: "POST" }).then(({ job: retry }) => setJobs((items) => [retry, ...items])).catch((error) => onMessage(error.message))}>重试</button>}</article>)}</div></aside>
    </div>
    <section className="composition-timeline"><div className="timeline-head"><b>时间线 · 单视频轨</b><span>仅导出已加入的片段</span><div className="timeline-tools"><button type="button" disabled={!selectedClip} title="对选中片段做画质增强（规划中）" onClick={() => onMessage(selectedClip ? "「一键高清」功能规划中，后续接入视频增强管线" : "请先选中一个时间线片段")}>一键高清</button><button type="button" disabled={!selectedClip} title="去除选中片段的背景音乐（规划中）" onClick={() => onMessage(selectedClip ? "「去BGM」功能规划中，后续接入人声分离管线" : "请先选中一个时间线片段")}>去BGM</button><button type="button" disabled={!selectedClip} title="去除选中片段的硬字幕（规划中）" onClick={() => onMessage(selectedClip ? "「去字幕」功能规划中，后续接入字幕消除管线" : "请先选中一个时间线片段")}>去字幕</button></div><button onClick={() => seekTo(0)} disabled={!clips.length}>回到开头</button></div><div className="timeline-ruler">{[0, .25, .5, .75, 1].map((point) => <span key={point}>{fmt(totalMs * point)}</span>)}</div><div className="timeline-track">{clips.map((clip, index) => { const media = assets.find((item) => item.id === clip.sourceId); const duration = clip.outMs - clip.inMs; const width = totalMs ? Math.max(7, duration / totalMs * 100) : 0; return <div key={clip.id} className={clip.id === selected ? "timeline-clip active" : "timeline-clip"} style={{ width: `${width}%` }} draggable onDragStart={() => { dragIndex.current = index; }} onDragOver={(event) => event.preventDefault()} onDrop={(event) => dropClip(event, index)} onClick={() => { setSelected(clip.id); seekTo(clips.slice(0, index).reduce((sum, item) => sum + item.outMs - item.inMs, 0)); }}><img src={media?.thumbnailUrl ? `${API_BASE}${media.thumbnailUrl}` : undefined} alt="" /><b>{index + 1} · {media?.name ?? "素材不可用"}</b><small>{fmt(duration)}</small></div>; })}{!clips.length && <div className="timeline-empty">从左侧素材库显式添加视频，素材才会进入合成。</div>}</div><div className="timeline-playhead" style={{ left: `${totalMs ? playhead / totalMs * 100 : 0}%` }} /></section>
  </section>;
}
