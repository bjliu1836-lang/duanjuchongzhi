// Video Remake Workshop — full mock Worker
// Serves the static frontend AND a feature-complete mock API covering
// projects / episodes / assets / storyboards / tasks / video-generations.
// Baseline data is loaded from /api-data/demo.json (real snapshot of local
// backend), user writes are layered on top via Workers KV.

const unauthorized = () => new Response("Authentication required", {
  status: 401,
  headers: { "WWW-Authenticate": 'Basic realm="Video Remake Workshop", charset="UTF-8"' },
});

function sameText(a, b) {
  if (a.length !== b.length) return false;
  let difference = 0;
  for (let i = 0; i < a.length; i += 1) difference |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return difference === 0;
}

function authorized(request, env) {
  if (!env.ACCESS_PASSWORD) return false;
  const username = env.ACCESS_USER || "owner";
  const expected = `Basic ${btoa(`${username}:${env.ACCESS_PASSWORD}`)}`;
  return sameText(request.headers.get("Authorization") || "", expected);
}

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" },
  });
}

function uid(prefix) {
  return `${prefix}_${Math.random().toString(36).slice(2, 10)}${Date.now().toString(36).slice(-4)}`;
}
function nowIso() { return new Date().toISOString(); }

// ---------- data loading & storage ----------

let _demoCache = null;
async function loadDemo(request, env) {
  if (_demoCache) return _demoCache;
  const url = new URL("/api-data/demo.json", request.url);
  // Build a fresh GET request — do NOT reuse the incoming request, since it may
  // carry a large multipart body that breaks ASSETS.fetch and burns CPU.
  const res = await env.ASSETS.fetch(new Request(url.toString(), { method: "GET" }));
  if (!res.ok) throw new Error(`demo snapshot missing: ${res.status}`);
  _demoCache = await res.json();
  return _demoCache;
}

// KV keys
const K = {
  newProjects: "u:new-projects",
  deletedPids: "u:deleted-pids",
  projectPatch: (pid) => `u:proj:${pid}`,
  projectExtras: (pid) => `u:extras:${pid}`,
  task: (tid) => `u:task:${tid}`,
};

async function kvGet(env, key, fallback) {
  if (!env.KV) return fallback;
  const v = await env.KV.get(key, { type: "json" });
  return v === null || v === undefined ? fallback : v;
}
async function kvPut(env, key, value) {
  if (!env.KV) return;
  await env.KV.put(key, JSON.stringify(value));
}

async function getState(request, env) {
  const demo = await loadDemo(request, env);
  const newProjects = await kvGet(env, K.newProjects, []);
  const deletedPids = new Set(await kvGet(env, K.deletedPids, []));

  const baseline = demo.projects.map(b => b.project).filter(p => !deletedPids.has(p.id));
  const custom = newProjects.map(b => b.project).filter(p => !deletedPids.has(p.id));
  return { demo, newProjects, deletedPids, allProjects: [...custom, ...baseline] };
}

async function getProjectBundle(request, env, pid) {
  const { demo, newProjects } = await getState(request, env);
  let bundle = newProjects.find(b => b.project.id === pid)
    || demo.projects.find(b => b.project.id === pid);
  if (!bundle) return null;

  const patch = await kvGet(env, K.projectPatch(pid), {});
  const extras = await kvGet(env, K.projectExtras(pid), {});
  return {
    project: { ...bundle.project, ...patch },
    episodes: [...(extras.episodes || []), ...(bundle.episodes || [])],
    assets: [...(extras.assets || []), ...(bundle.assets || [])],
    storyboards: [...(extras.storyboards || []), ...(bundle.storyboards || [])],
    tasks: [...(extras.tasks || []), ...(bundle.tasks || [])],
    groups: [...(extras.groups || []), ...(bundle.groups || [])],
    videoGenerations: [...(extras.videoGenerations || []), ...(bundle.videoGenerations || [])],
  };
}

async function appendExtras(env, pid, key, items) {
  const extras = await kvGet(env, K.projectExtras(pid), {});
  extras[key] = [...(extras[key] || []), ...items];
  await kvPut(env, K.projectExtras(pid), extras);
}

// ---------- file url rewriting ----------

function rewriteFileUrl(pathname) {
  let m = pathname.match(/^\/api\/files\/projects\/[^/]+\/assets\/[^/]+\/([^/]+)$/);
  if (m) return `/demo/${m[1]}`;
  m = pathname.match(/^\/api\/files\/projects\/[^/]+\/outputs\/videos\/([^/]+\.mp4)$/);
  if (m) return `/demo/videos/${m[1]}`;
  m = pathname.match(/^\/api\/files\/projects\/[^/]+\/outputs\/exports\/([^/]+\.mp4)$/);
  if (m) return `/demo/videos/${m[1]}`;
  return null;
}

// Merge KV-stored mock extras into a real-backend JSON response so that
// demo-side "writes" (mock uploads, mock task runs, mock asset generation)
// remain visible across page refreshes even though the real backend's
// SQLite doesn't know about them.
async function mergeWithKvExtras(request, env, url, resp) {
  const path = url.pathname;
  let body;
  try { body = await resp.clone().json(); } catch { return null; }

  // Per-project extras
  const projMatch = path.match(/^\/api\/projects\/([^/]+)$/);
  if (projMatch && body && typeof body === "object") {
    const pid = projMatch[1];
    const extras = await kvGet(env, K.projectExtras(pid), null);
    if (extras && Array.isArray(body.episodes) && Array.isArray(extras.episodes) && extras.episodes.length) {
      const existingIds = new Set(body.episodes.map(e => e.id));
      const newEps = extras.episodes.filter(e => !existingIds.has(e.id));
      body.episodes = [...body.episodes, ...newEps];
      return json(body, resp.status);
    }
    return null;
  }

  // Sub-resources
  const subMatch = path.match(/^\/api\/projects\/([^/]+)\/(episodes|assets|storyboards|tasks|asset-groups|video-generations)$/);
  if (subMatch && body && typeof body === "object") {
    const pid = subMatch[1];
    const sub = subMatch[2];
    const fieldName = { "episodes": "episodes", "assets": "assets", "storyboards": "storyboards", "tasks": "tasks", "asset-groups": "groups", "video-generations": "videoGenerations" }[sub];
    const extras = await kvGet(env, K.projectExtras(pid), null);
    const list = body[fieldName];
    if (extras && Array.isArray(list) && Array.isArray(extras[fieldName]) && extras[fieldName].length) {
      const existingIds = new Set(list.map(x => x.id));
      const newItems = extras[fieldName].filter(x => !existingIds.has(x.id));
      body[fieldName] = [...newItems, ...list];  // new items first
      return json(body, resp.status);
    }
    return null;
  }

  return null;
}

// ---------- api ----------

async function handleApi(request, env, url) {
  const path = url.pathname;
  const method = request.method.toUpperCase();

  if (path === "/api/health") {
    return json({ ok: true, mode: "mock", time: nowIso() });
  }
  if (path === "/api/model-status") {
    return json({
      mode: "mock", ready: true, missing: [],
      message: "演示模式：所有数据均为本地模拟，不会调用外部模型。",
      models: { textModel: "mock-text", vlmModel: "mock-vlm", imageModel: "mock-image", videoGenerationModel: "mock-video" },
    });
  }
  if (path === "/api/model-test/chat" && method === "POST") {
    return json({ mode: "mock", model: "mock-text", content: "演示模式：模型连接测试未调用外部接口。" });
  }

  // ---- projects ----
  if (path === "/api/projects" && method === "GET") {
    const { allProjects } = await getState(request, env);
    return json({ projects: allProjects });
  }
  if (path === "/api/projects" && method === "POST") {
    const body = await request.json().catch(() => ({}));
    const pid = uid("project");
    const eid = uid("episode");
    const newProject = {
      id: pid,
      title: body.title || "未命名项目",
      target_country: body.targetCountry || body.target_country || "中国",
      target_language: body.targetLanguage || body.target_language || "沿用原片语言",
      status: "已创建",
      created_at: nowIso(),
      updated_at: nowIso(),
      remake_mode: body.remakeMode || body.remake_mode || "cg_3d",
      source_language: "",
      storyboard_visual_style: body.remakeMode || body.remake_mode || "cg_3d",
      replace_characters: body.replaceCharacters ? 1 : 0,
      replace_scenes: body.replaceScenes ? 1 : 0,
      parent_project_id: body.parentProjectId || "",
    };
    const bundle = {
      project: newProject,
      episodes: [{ id: eid, project_id: pid, episode_index: 1, filename: "（待上传）", file_path: "", status: "待上传", created_at: nowIso(), source_url: "" }],
      assets: [], storyboards: [], tasks: [], groups: [], videoGenerations: [],
    };
    const newProjects = await kvGet(env, K.newProjects, []);
    newProjects.unshift(bundle);
    await kvPut(env, K.newProjects, newProjects);
    return json({ project: newProject }, 201);
  }

  // ---- single project ----
  const projectMatch = path.match(/^\/api\/projects\/([^/]+)$/);
  if (projectMatch) {
    const pid = projectMatch[1];
    const bundle = await getProjectBundle(request, env, pid);
    if (!bundle) return json({ detail: "Not Found" }, 404);
    if (method === "GET") return json({ project: bundle.project, episodes: bundle.episodes });
    if (method === "PATCH") {
      const body = await request.json().catch(() => ({}));
      const patch = await kvGet(env, K.projectPatch(pid), {});
      const fieldMap = {
        title: "title", targetCountry: "target_country", targetLanguage: "target_language",
        remakeMode: "remake_mode", storyboardVisualStyle: "storyboard_visual_style",
        status: "status",
      };
      for (const [k, v] of Object.entries(body)) {
        const field = fieldMap[k] || k;
        patch[field] = v;
      }
      patch.updated_at = nowIso();
      await kvPut(env, K.projectPatch(pid), patch);
      return json({ project: { ...bundle.project, ...patch } });
    }
    if (method === "DELETE") {
      const deleted = await kvGet(env, K.deletedPids, []);
      deleted.push(pid);
      await kvPut(env, K.deletedPids, deleted);
      return json({ ok: true });
    }
  }

  // ---- derive (copy project) ----
  const deriveMatch = path.match(/^\/api\/projects\/([^/]+)\/derive$/);
  if (deriveMatch && method === "POST") {
    const srcPid = deriveMatch[1];
    const body = await request.json().catch(() => ({}));
    const src = await getProjectBundle(request, env, srcPid);
    if (!src) return json({ detail: "Not Found" }, 404);
    const newPid = uid("project");
    const newProject = {
      ...src.project,
      id: newPid,
      title: body.title || `${src.project.title}（新制作模式）`,
      remake_mode: body.remakeMode || src.project.remake_mode,
      parent_project_id: srcPid,
      created_at: nowIso(),
      updated_at: nowIso(),
    };
    const bundle = { project: newProject, episodes: src.episodes, assets: [], storyboards: [], tasks: [], groups: [], videoGenerations: [] };
    const newProjects = await kvGet(env, K.newProjects, []);
    newProjects.unshift(bundle);
    await kvPut(env, K.newProjects, newProjects);
    return json({ project: newProject }, 201);
  }

  // ---- project subresources (GET) ----
  const subMatch = path.match(/^\/api\/projects\/([^/]+)\/(assets|asset-groups|storyboards|tasks|video-generations|episodes)$/);
  if (subMatch && method === "GET") {
    const [, pid, sub] = subMatch;
    const bundle = await getProjectBundle(request, env, pid);
    if (!bundle) return json({ detail: "Not Found" }, 404);
    if (sub === "assets") return json({ assets: bundle.assets });
    if (sub === "asset-groups") return json({ groups: bundle.groups });
    if (sub === "storyboards") return json({ storyboards: bundle.storyboards });
    if (sub === "tasks") return json({ tasks: bundle.tasks });
    if (sub === "video-generations") return json({ videoGenerations: bundle.videoGenerations });
    if (sub === "episodes") return json({ episodes: bundle.episodes });
  }

  // ---- video edits (composition drafts) ----
  const editMatch = path.match(/^\/api\/projects\/([^/]+)\/video-edits\/([^/]+)$/);
  if (editMatch) {
    if (method === "GET") {
      return json({ draft: { revision: 1, edit: { tracks: [] } } });
    }
    return json({ ok: true });
  }

  const videoAssetsMatch = path.match(/^\/api\/projects\/([^/]+)\/episodes\/([^/]+)\/video-assets$/);
  if (videoAssetsMatch) {
    if (method === "GET") return json({ assets: [] });
    return json({ ok: true });
  }

  const exportJobsMatch = path.match(/^\/api\/projects\/([^/]+)\/video-export-jobs/);
  if (exportJobsMatch) {
    return json({ jobs: [] });
  }

  // ---- asset detail / confirm / generate-image / select-image ----
  const assetMatch = path.match(/^\/api\/projects\/([^/]+)\/assets\/([^/]+)(\/.*)?$/);
  if (assetMatch) {
    const [, pid, aid, action] = assetMatch;
    const bundle = await getProjectBundle(request, env, pid);
    if (!bundle) return json({ detail: "Not Found" }, 404);
    const asset = bundle.assets.find(a => a.id === aid);
    if (!asset) return json({ detail: "Asset not found" }, 404);

    if (!action && method === "PATCH") {
      const body = await request.json().catch(() => ({}));
      Object.assign(asset, body);
      return json({ asset });
    }
    if (action === "/confirm" && method === "POST") {
      asset.status = "已确认";
      return json({ asset });
    }
    if (action === "/generate-image" && method === "POST") {
      const imageId = uid("asset_image");
      const newImage = {
        id: imageId, project_id: pid, asset_id: aid, status: "成功",
        prompt: "mock 演示生成", created_at: nowIso(),
        file_path: "", _mockSvg: true,
      };
      asset.images = asset.images || [];
      asset.images.unshift(newImage);
      asset.status = "已生成资产图";
      return json({ asset });
    }
    if (action === "/select-image" && method === "POST") return json({ asset });
    if (action === "/restore-original" && method === "POST") return json({ asset });
    if (action === "/upload-image" && method === "POST") return json({ asset });
  }

  // ---- storyboards ----
  const sbMatch = path.match(/^\/api\/projects\/([^/]+)\/storyboards(\/([^/]+)(\/move)?)?$/);
  if (sbMatch) {
    const [, pid, , sbid, isMove] = sbMatch;
    const bundle = await getProjectBundle(request, env, pid);
    if (!bundle) return json({ detail: "Not Found" }, 404);

    if (!sbid && method === "POST") {
      const body = await request.json().catch(() => ({}));
      const newSb = {
        id: uid("storyboard"), project_id: pid,
        episode_id: body.episodeId || bundle.episodes[0]?.id || "",
        title: body.title || `分镜_${String(bundle.storyboards.length + 1).padStart(3, "0")}`,
        time_range: body.timeRange || "00:00:00-00:00:05",
        prompt: body.prompt || "", status: "已生成",
        created_at: nowIso(), payload: {},
      };
      await appendExtras(env, pid, "storyboards", [newSb]);
      return json({ storyboard: newSb }, 201);
    }
    if (sbid) {
      const sb = bundle.storyboards.find(s => s.id === sbid);
      if (!sb) return json({ detail: "Not Found" }, 404);
      if (method === "PATCH") {
        const body = await request.json().catch(() => ({}));
        Object.assign(sb, body);
        return json({ storyboard: sb });
      }
      if (method === "DELETE") return json({ ok: true });
      if (isMove && method === "POST") return json({ storyboard: sb });
    }
  }

  // ---- asset groups ----
  const groupMatch = path.match(/^\/api\/projects\/([^/]+)\/asset-groups(\/([^/]+))?$/);
  if (groupMatch) {
    const [, pid, , gid] = groupMatch;
    const bundle = await getProjectBundle(request, env, pid);
    if (!bundle) return json({ detail: "Not Found" }, 404);
    if (!gid && method === "POST") {
      const body = await request.json().catch(() => ({}));
      const newGroup = {
        id: uid("asset_group"), name: body.name || "新分组",
        assetType: body.assetType || "角色",
        createdAt: nowIso(), updatedAt: nowIso(), members: [],
      };
      await appendExtras(env, pid, "groups", [newGroup]);
      return json({ group: newGroup }, 201);
    }
    if (gid) {
      const g = bundle.groups.find(x => x.id === gid);
      if (!g) return json({ detail: "Not Found" }, 404);
      if (method === "PATCH") {
        const body = await request.json().catch(() => ({}));
        Object.assign(g, body);
        return json({ group: g });
      }
      if (method === "DELETE") return json({ ok: true });
    }
  }

  // ---- tasks ----
  if (path.match(/^\/api\/projects\/([^/]+)\/tasks\/run$/) && method === "POST") {
    const pid = path.match(/^\/api\/projects\/([^/]+)\//)[1];
    const body = await request.json().catch(() => ({}));
    const taskId = uid("task");
    const task = {
      id: taskId, project_id: pid,
      episode_id: body.episodeId || "",
      task_type: body.taskType || body.task_type || "storyboard_generation",
      status: "进行中",
      message: "演示模式：任务已启动（mock）",
      created_at: nowIso(), updated_at: nowIso(),
    };
    await kvPut(env, K.task(taskId), task);
    await appendExtras(env, pid, "tasks", [task]);
    return json({ task }, 202);
  }

  const taskMatch = path.match(/^\/api\/tasks\/([^/]+)$/);
  if (taskMatch && method === "GET") {
    const tid = taskMatch[1];
    let task = await kvGet(env, K.task(tid), null);
    if (!task) {
      task = { id: tid, status: "成功", message: "演示模式：任务已完成（mock）", created_at: nowIso(), updated_at: nowIso() };
    } else {
      const age = Date.now() - new Date(task.created_at).getTime();
      if (task.status === "进行中" && age > 5000) {
        task.status = "成功";
        task.message = "演示模式：任务已完成（mock）";
        task.updated_at = nowIso();
        await kvPut(env, K.task(tid), task);
      }
    }
    return json({ task });
  }

  // ---- catch-all write endpoints ----
  if (method === "POST" || method === "PATCH" || method === "PUT" || method === "DELETE") {
    return json({ ok: true });
  }

  return json({ detail: "Not Found" }, 404);
}

async function handleRequest(request, env) {
    // Auth disabled for public demo. To re-enable, set ACCESS_PASSWORD secret
    // and restore: if (!authorized(request, env)) return unauthorized();
    const url = new URL(request.url);

    if (url.pathname.startsWith("/api/files/")) {
      // Try rewriting known demo paths to bundled assets first
      const rewritten = rewriteFileUrl(url.pathname);
      if (rewritten) {
        const r = await env.ASSETS.fetch(new Request(new URL(rewritten, request.url), request));
        if (r.status !== 404) return r;
      }
      // Fall through: forward to real backend (which serves live mock-generated files)
      if (env.BACKEND_URL) {
        try {
          const target = `${env.BACKEND_URL.replace(/\/+$/, "")}${url.pathname}${url.search}`;
          const headers = new Headers(request.headers);
          headers.delete("Authorization");
          const resp = await fetch(target, { method: "GET", headers, redirect: "manual" });
          if (resp.status !== 404) return resp;
        } catch { /* fall through */ }
      }
      return new Response("Not Found", { status: 404 });
    }

    if (url.pathname === "/api" || url.pathname.startsWith("/api/")) {
      // Uploads go straight to the real backend (bytes stream through the
      // tunnel and land on our own server); no Worker-level mock intercepts.
      // Dual mode: try real backend via BACKEND_URL (named tunnel) first,
      // fall back to embedded mock when tunnel is offline. After a successful
      // real-backend response, merge KV-stored mock extras so user-triggered
      // "writes" (mock upload, mock task runs) stay visible across refreshes.
      if (env.BACKEND_URL) {
        try {
          const target = `${env.BACKEND_URL.replace(/\/+$/, "")}${url.pathname}${url.search}`;
          const headers = new Headers(request.headers);
          headers.delete("Authorization");
          // Gateway (cloudflare/gateway.py) requires this shared secret to forward
          // the request to the local backend. Without it the gateway returns 403.
          if (env.BACKEND_SHARED_SECRET) {
            headers.set("X-Workshop-Gateway-Secret", env.BACKEND_SHARED_SECRET);
          }
          const method = request.method.toUpperCase();
          const init = { method, headers, redirect: "manual" };
          if (method !== "GET" && method !== "HEAD") init.body = request.body;
          console.log("[proxy] target=", target, "method=", method);
          const resp = await fetch(target, init);
          console.log("[proxy] status=", resp.status, "content-type=", resp.headers.get("content-type"));
          if (resp.status < 500) {
            // Merge KV extras into the real response so mock writes stay visible.
            const ct = resp.headers.get("content-type") || "";
            if (method === "GET" && ct.includes("application/json")) {
              const merged = await mergeWithKvExtras(request, env, url, resp);
              if (merged) return merged;
            }
            return resp;
          }
          console.log("[proxy] backend returned >=500, falling back to mock");
        } catch (e) {
          console.log("[proxy] fetch error:", String(e?.message || e));
        }
      } else {
        console.log("[proxy] no BACKEND_URL, using mock");
      }
      try {
        return await handleApi(request, env, url);
      } catch (err) {
        return json({ detail: String(err?.message || err) }, 500);
      }
    }

    const assetResponse = await env.ASSETS.fetch(request);
    if (assetResponse.status !== 404 || !request.headers.get("Accept")?.includes("text/html")) {
      return assetResponse;
    }
    const indexUrl = new URL("/index.html", request.url);
    return env.ASSETS.fetch(new Request(indexUrl, request));
}

export default {
  async fetch(request, env) {
    try {
      return await handleRequest(request, env);
    } catch (err) {
      // Return the real error instead of Cloudflare's opaque 1101 page so
      // production failures stay diagnosable from the response body.
      console.error("[worker] unhandled exception:", err?.stack || String(err));
      return new Response(
        JSON.stringify({ detail: "Worker 内部错误", error: String(err?.stack || err) }, null, 2),
        { status: 500, headers: { "Content-Type": "application/json; charset=utf-8" } },
      );
    }
  },
};
