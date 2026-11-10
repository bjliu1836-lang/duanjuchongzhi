from __future__ import annotations

import json
import hashlib
import mimetypes
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
import uuid
from io import BytesIO
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from PIL import Image, UnidentifiedImageError
from json_repair import repair_json

from backend.app.providers.model_provider import ProviderError, ProviderIncompleteRead, ProviderIncompleteResponse, get_model_provider, get_model_status
from backend.app.composition import make_router


ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv(path: Path) -> None:
    """轻量加载 .env；已存在的环境变量优先，不被覆盖。"""
    if not path.exists():
        return
    try:
        content = path.read_text(encoding="utf-8-sig")
    except OSError:
        return
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data"
PROJECTS_DIR = DATA_DIR / "projects"
DB_PATH = DATA_DIR / "workshop.sqlite3"
CONFIG_DIR = ROOT / "config"
MODELS_CONFIG_PATH = CONFIG_DIR / "models.json"

REMAKE_MODES = {"overseas_localization", "cg_3d", "asset_replacement"}
ASSET_PROMPT_TEMPLATE_VERSION = "asset-prompt-v3-independent-visual"
ASSET_SOURCE_SCHEMA_VERSION = "asset-source-snapshot-v2-canonical-reviewed"
ASSET_LOCALIZATION_SCHEMA_VERSION = "asset-localized-profile-v2-source-bound"
ASSET_IMAGE_MODELS = {
    "doubao-seedream-5-0-260128",
    "doubao-seedream-4-5-251128",
    "doubao-seedream-4-0-250828",
}
ASSET_IMAGE_ASPECT_SIZES = {"16:9": "2848x1600"}
DEFAULT_ASSET_IMAGE_ASPECT_RATIO = "16:9"
STORYBOARD_STYLE_TEMPLATES = {
    "live_action": "真人实拍电影质感。角色与环境采用真实世界的比例、材质、自然光照及摄影成像，保持原分镜的动作、情绪、站位、构图和运镜；避免三维CG与卡通渲染质感。",
    "cg_3d": "高端三维游戏CG过场动画质感，Unreal Engine 5 cinematic render，Final Fantasy CG style，非摄影式三维网格与数字雕刻材质，PBR材质和电影级三维光照；保持原分镜的动作、情绪、站位、构图和运镜，角色与场景风格统一；避免真人摄影皮肤、照片颗粒与二维插画。",
}
STORYBOARD_STYLE_TEMPLATE_VERSION = "storyboard-visual-style-v1"
CG_ROLE_FIXED_TEMPLATE = """[Style & Render] High-end 3D game character asset sheet, Next-gen game character model, Unreal Engine 5 cinematic render, Octane render, Zbrush digital sculpt, Final Fantasy CG style, hyper-stylized 3D, non-photorealistic mesh rendering.
[3D Surface Shader] Stylized 3D character skin shader, heavy subsurface scattering (SSS), PBR material maps, clean 3D topology. All facial lines, age signs, and structural details are defined by 3D digital sculpting, ABSOLUTELY NO real-world photographic skin pores, photo grain, or noise.
[Layout & Light] Multi-view layout: Left panel features a front portrait bust facing camera. Right panels show three full-body standing poses (front view, 90-degree right side view, back view). Pure clean white background, no text, single-row grid. Studio 3D lighting, three-point lighting, ambient occlusion, sharp rim light, ray tracing, 8k resolution, masterpiece."""
CG_SCENE_FIXED_TEMPLATE = """风格：高端三维CG环境，Unreal Engine 5 cinematic render，Octane render，Final Fantasy CG style，风格化PBR材质、细致数字几何、电影级三维光照、环境遮蔽与光线追踪。保持来源描述中的建筑年代、布局、材质、家具、道具及空间关系。"""
CG_PROP_FIXED_TEMPLATE = """风格：高端三维游戏道具资产，Unreal Engine 5 cinematic render，Octane render，风格化PBR材质、细致数字几何与光线追踪。"""
ROLE_DISPLAY_CONSTRAINT = """展示构图：左侧一个正面面部特写（面对镜头、目光看向镜头），右侧同一角色三个全身视图，依次为正面、右侧面90°、背面；左侧特写与右侧三视图是同一角色、同一套服装、同一发型；如果特写裁入颈部或肩部，必须清晰呈现与全身视图完全相同的衣领、肩线和上身衣料连续关系，不得出现裸肩或不同的衣领/上身服装；仅这四个形象，不得增加人物或分行排版。白色干净背景，无文字、无水印、无logo。自然直立站姿，双臂自然下垂、双手空握不持物，双脚平行与肩同宽；嘴巴自然闭合，角色完整不裁切，脚下有自然投影。"""
ROLE_COVERED_FOOT_DISPLAY_CONSTRAINT = ROLE_DISPLAY_CONSTRAINT.replace(
    "双脚平行与肩同宽；嘴巴自然闭合，角色完整不裁切，脚下有自然投影。",
    "下肢保持自然站姿但完全隐藏于衣摆内，不绘制双脚；嘴巴自然闭合，角色及袍摆完整不裁切，地面投影位于衣摆下方。",
)
SCENE_DISPLAY_CONSTRAINT = """展示构图：完整场景单幅广角全景，展示室内空间布局、入口、窗户、家具/设施、墙面、地面、天花板及关键道具位置；室外则展示建筑外立面、入口、周边道路/环境、标识位置和空间尺度。无人物、无文字、无水印、无拼贴、无三视图、无局部特写。"""
PROP_DISPLAY_CONSTRAINT = """展示构图：单个完整道具居中独立展示于干净中性背景，无手持、无人物、无额外物体、无拼贴、无多视图、无文字、水印或品牌标识。"""
ROLE_LIVE_STYLE = "风格：真人写实摄影质感，模拟真实拍摄效果，皮肤自然且纹理清晰；明亮柔和影棚光，85mm人像镜头，专业布光，高分辨率与照片级细节。避免卡通、绘画和三维渲染。"
SCENE_LIVE_STYLE = "风格：真人实景摄影质感、8K高清、明亮柔和自然光，真实材质与空间尺度。"
PROP_LIVE_STYLE = "风格：真人实拍质感、8K高清、真实材质细节与自然柔和光线。"


def load_local_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip().lstrip("\ufeff")
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


load_local_env()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def data_file_url(file_path: str) -> str:
    path = Path(file_path)
    if not path.is_absolute():
        path = ROOT / path
    try:
        relative = path.resolve().relative_to(DATA_DIR.resolve())
    except ValueError:
        return ""
    return f"/api/files/{relative.as_posix()}"


def enrich_asset_image(image: dict[str, Any]) -> dict[str, Any]:
    image["payload"] = json.loads(image.pop("payload_json"))
    image["file_url"] = data_file_url(str(image.get("file_path") or ""))
    return image


def image_extension(mime_type: str) -> str:
    if mime_type == "image/jpeg":
        return ".jpg"
    if mime_type == "image/svg+xml":
        return ".svg"
    extension = mimetypes.guess_extension(mime_type or "image/png")
    return extension or ".png"


def enrich_asset_payload_urls(payload: dict[str, Any]) -> dict[str, Any]:
    for target in [payload, payload.get("当前子资产") if isinstance(payload.get("当前子资产"), dict) else None]:
        if not isinstance(target, dict):
            continue
        frame_path = target.get("原资产图路径") or target.get("原资产截图路径")
        if isinstance(frame_path, str) and frame_path:
            target["原资产图URL"] = data_file_url(frame_path)
            target["原资产截图URL"] = target["原资产图URL"]
    return payload


def parse_asset_timecode(value: Any) -> float:
    if isinstance(value, (int, float)):
        return max(0.0, float(value))
    if not isinstance(value, str):
        return 0.0
    value = value.strip()
    if not value:
        return 0.0
    parts = re.split(r"[:：]", value)
    try:
        if len(parts) == 3:
            return max(0.0, int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2]))
        if len(parts) == 2:
            return max(0.0, int(parts[0]) * 60 + float(parts[1]))
        return max(0.0, float(parts[0]))
    except ValueError:
        return 0.0


def format_timecode(seconds: float) -> str:
    seconds = max(0.0, seconds)
    whole = int(seconds)
    fraction = seconds - whole
    hours = whole // 3600
    minutes = (whole % 3600) // 60
    secs = whole % 60
    if fraction:
        return f"{hours:02d}:{minutes:02d}:{secs + fraction:05.2f}"
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def parse_time_range_seconds(start_value: Any, end_value: Any) -> tuple[float, float]:
    start = parse_asset_timecode(start_value)
    end = parse_asset_timecode(end_value)
    if end and end < start:
        end = start
    return start, end or start


def asset_capture_time_candidates(payload: dict[str, Any]) -> list[float]:
    child = payload.get("当前子资产") if isinstance(payload.get("当前子资产"), dict) else {}
    candidates: list[float] = []
    for source in [child, payload]:
        if not isinstance(source, dict):
            continue
        for key in ["原剧截图时间点候选", "截图时间点候选", "原剧截图时间点", "截图时间点", "时间码"]:
            value = source.get(key)
            values = value if isinstance(value, list) else [value]
            for item in values:
                seconds = parse_asset_timecode(item)
                if seconds > 0 or str(item).strip() in {"0", "0.0", "00:00:00"}:
                    candidates.append(seconds)
    first = first_asset_timecode(payload)
    if first > 0 or not candidates:
        candidates.append(first)
    deduped: list[float] = []
    for seconds in candidates:
        rounded = round(seconds, 2)
        if rounded not in deduped:
            deduped.append(rounded)
    return deduped[:5]


def first_asset_timecode(payload: dict[str, Any]) -> float:
    child = payload.get("当前子资产") if isinstance(payload.get("当前子资产"), dict) else {}
    for source in [child, payload]:
        first = source.get("首次出现信息") if isinstance(source.get("首次出现信息"), dict) else {}
        timecode = first.get("时间码")
        if timecode:
            return parse_asset_timecode(timecode)
        records = source.get("出现记录") if isinstance(source.get("出现记录"), list) else []
        for record in records:
            if not isinstance(record, dict):
                continue
            ranges = record.get("出现时间段") if isinstance(record.get("出现时间段"), list) else []
            for item in ranges:
                if isinstance(item, dict) and item.get("开始时间"):
                    return parse_asset_timecode(item.get("开始时间"))
    return 0.0


def capture_asset_original_frames(project_id: str, episode: dict[str, Any], assets: list[dict[str, Any]]) -> dict[str, int]:
    ffmpeg = shutil.which("ffmpeg")
    video_path = Path(str(episode.get("file_path") or ""))
    if not ffmpeg or not video_path.exists():
        return {"候选时码数": 0, "解码进程数": 0, "成功帧数": 0}
    output_dir = PROJECTS_DIR / project_id / "assets" / "original_frames"
    output_dir.mkdir(parents=True, exist_ok=True)
    asset_candidates: dict[int, list[float]] = {}
    time_to_index: dict[float, int] = {}
    for asset in assets:
        payload = asset.get("payload") if isinstance(asset.get("payload"), dict) else {}
        child = payload.get("当前子资产") if isinstance(payload.get("当前子资产"), dict) else None
        if payload.get("原资产图路径") or (isinstance(child, dict) and child.get("原资产图路径")):
            continue
        base_candidates = asset_capture_time_candidates(payload)
        candidates: list[float] = []
        for seconds in base_candidates:
            candidates.extend([max(0.0, seconds - 1.0), seconds, seconds + 1.0])
        deduped_candidates: list[float] = []
        for seconds in candidates:
            rounded = round(seconds, 2)
            if rounded not in deduped_candidates:
                deduped_candidates.append(rounded)
        asset_candidates[id(asset)] = deduped_candidates[:9]
        for seconds in deduped_candidates[:9]:
            time_to_index.setdefault(seconds, -1)
    ordered_times = list(time_to_index)
    frame_for_time: dict[float, Path] = {}
    process_count = 0
    group_size = 48
    for group_start in range(0, len(ordered_times), group_size):
        group = ordered_times[group_start : group_start + group_size]
        group_dir = output_dir / f"decode_{new_id('frames')}"
        group_dir.mkdir(parents=True, exist_ok=True)
        clauses = [f"gte(t\\,{seconds:.3f})*(isnan(prev_selected_t)+lt(prev_selected_t\\,{seconds:.3f}))" for seconds in group]
        expression = "+".join(clauses)
        command = [ffmpeg, "-y", "-i", str(video_path), "-vf", f"select={expression},showinfo", "-vsync", "0", "-q:v", "2", str(group_dir / "%03d.jpg")]
        process_count += 1
        try:
            decoded = subprocess.run(command, check=True, capture_output=True, timeout=90, text=True, encoding="utf-8", errors="replace")
        except (subprocess.SubprocessError, OSError):
            shutil.rmtree(group_dir, ignore_errors=True)
            continue
        images = sorted(group_dir.glob("*.jpg"))
        decoded_times = [float(value) for value in re.findall(r"pts_time:([0-9]+(?:\.[0-9]+)?)", decoded.stderr)]
        for frame_time, frame_path in zip(decoded_times, images):
            matches = [seconds for seconds in group if seconds <= frame_time and seconds not in frame_for_time]
            for target in matches:
                if target not in frame_for_time:
                    frame_for_time[target] = frame_path
    successful_frames = len(frame_for_time)
    for asset in assets:
        payload = asset.get("payload") if isinstance(asset.get("payload"), dict) else {}
        child = payload.get("当前子资产") if isinstance(payload.get("当前子资产"), dict) else None
        candidates = asset_candidates.get(id(asset), [])
        captured: list[dict[str, Any]] = []
        for seconds in candidates:
            frame_path = frame_for_time.get(seconds)
            if frame_path is None or not frame_path.exists():
                continue
            captured.append(
                {
                    "时间点": format_timecode(seconds),
                    "路径": str(frame_path),
                    "URL": data_file_url(str(frame_path)),
                }
            )
        if not captured:
            continue
        main_capture = captured[min(1, len(captured) - 1)]
        payload["原资产图路径"] = main_capture["路径"]
        payload["原资产图URL"] = main_capture["URL"]
        payload["原资产截图路径"] = main_capture["路径"]
        payload["原资产截图URL"] = main_capture["URL"]
        payload["原资产候选截图"] = captured
        if isinstance(child, dict):
            child["原资产图路径"] = main_capture["路径"]
            child["原资产图URL"] = main_capture["URL"]
            child["原资产截图路径"] = main_capture["路径"]
            child["原资产截图URL"] = main_capture["URL"]
            child["原资产候选截图"] = captured
    return {"候选时码数": len(ordered_times), "解码进程数": process_count, "成功帧数": successful_frames}


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects (
              id TEXT PRIMARY KEY,
              title TEXT NOT NULL,
              target_country TEXT NOT NULL,
              target_language TEXT NOT NULL,
              status TEXT NOT NULL,
              remake_mode TEXT NOT NULL DEFAULT 'overseas_localization',
              source_language TEXT NOT NULL DEFAULT '',
              storyboard_visual_style TEXT NOT NULL DEFAULT 'live_action',
              replace_characters INTEGER NOT NULL DEFAULT 0,
              replace_scenes INTEGER NOT NULL DEFAULT 0,
              parent_project_id TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS episodes (
              id TEXT PRIMARY KEY,
              project_id TEXT NOT NULL,
              episode_index INTEGER NOT NULL,
              filename TEXT NOT NULL,
              file_path TEXT NOT NULL,
              source_url TEXT NOT NULL DEFAULT '',
              ark_file_id TEXT NOT NULL DEFAULT '',
              ark_file_status TEXT NOT NULL DEFAULT '',
              ark_file_error TEXT NOT NULL DEFAULT '',
              status TEXT NOT NULL,
              created_at TEXT NOT NULL,
              UNIQUE(project_id, episode_index)
            );

            CREATE TABLE IF NOT EXISTS tasks (
              id TEXT PRIMARY KEY,
              project_id TEXT NOT NULL,
              episode_id TEXT,
              task_type TEXT NOT NULL,
              status TEXT NOT NULL,
              message TEXT NOT NULL,
              result_json TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS agent_runs (
              id TEXT PRIMARY KEY,
              task_id TEXT NOT NULL,
              project_id TEXT NOT NULL,
              episode_id TEXT,
              agent_name TEXT NOT NULL,
              model_mode TEXT NOT NULL,
              model_name TEXT NOT NULL,
              prompt_file TEXT NOT NULL,
              input_json TEXT NOT NULL,
              output_json TEXT NOT NULL,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS assets (
              id TEXT PRIMARY KEY,
              project_id TEXT NOT NULL,
              episode_id TEXT,
              asset_type TEXT NOT NULL,
              name TEXT NOT NULL,
              localized_name TEXT NOT NULL,
              variable TEXT NOT NULL,
              status TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS asset_parent_links (
              id TEXT PRIMARY KEY,
              project_id TEXT NOT NULL,
              parent_asset_id TEXT NOT NULL,
              child_asset_id TEXT NOT NULL,
              parent_asset_key TEXT NOT NULL,
              child_asset_key TEXT NOT NULL,
              created_at TEXT NOT NULL,
              UNIQUE(project_id, child_asset_key)
            );

            CREATE TABLE IF NOT EXISTS asset_groups (
              id TEXT PRIMARY KEY,
              project_id TEXT NOT NULL,
              name TEXT NOT NULL,
              asset_type TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              UNIQUE(project_id, name)
            );

            CREATE TABLE IF NOT EXISTS asset_group_members (
              id TEXT PRIMARY KEY,
              project_id TEXT NOT NULL,
              group_id TEXT NOT NULL,
              asset_id TEXT NOT NULL,
              asset_key TEXT NOT NULL,
              sort_order INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,
              UNIQUE(project_id, asset_key),
              UNIQUE(group_id, asset_key)
            );

            CREATE TABLE IF NOT EXISTS asset_images (
              id TEXT PRIMARY KEY,
              project_id TEXT NOT NULL,
              asset_id TEXT NOT NULL,
              status TEXT NOT NULL,
              prompt TEXT NOT NULL,
              file_path TEXT NOT NULL,
              is_selected INTEGER NOT NULL,
              payload_json TEXT NOT NULL,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS asset_generation_jobs (
              id TEXT PRIMARY KEY,
              project_id TEXT NOT NULL,
              asset_id TEXT NOT NULL,
              remake_mode TEXT NOT NULL,
              template_version TEXT NOT NULL,
              status TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS storyboards (
              id TEXT PRIMARY KEY,
              project_id TEXT NOT NULL,
              episode_id TEXT NOT NULL,
              title TEXT NOT NULL,
              time_range TEXT NOT NULL,
              prompt TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              status TEXT NOT NULL,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS video_generations (
              id TEXT PRIMARY KEY,
              project_id TEXT NOT NULL,
              episode_id TEXT NOT NULL,
              storyboard_id TEXT NOT NULL,
              status TEXT NOT NULL,
              prompt TEXT NOT NULL,
              output_path TEXT NOT NULL,
              is_selected INTEGER NOT NULL,
              payload_json TEXT NOT NULL,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS video_media (
              id TEXT PRIMARY KEY,
              project_id TEXT NOT NULL,
              episode_id TEXT NOT NULL,
              filename TEXT NOT NULL,
              file_path TEXT NOT NULL,
              media_json TEXT NOT NULL,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS video_edit_drafts (
              project_id TEXT NOT NULL,
              episode_id TEXT NOT NULL,
              title TEXT NOT NULL,
              revision INTEGER NOT NULL DEFAULT 0,
              edit_json TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              PRIMARY KEY(project_id, episode_id)
            );

            CREATE TABLE IF NOT EXISTS composition_exports (
              id TEXT PRIMARY KEY,
              project_id TEXT NOT NULL,
              episode_id TEXT NOT NULL,
              revision INTEGER NOT NULL,
              status TEXT NOT NULL,
              progress INTEGER NOT NULL DEFAULT 0,
              stage TEXT NOT NULL,
              snapshot_json TEXT NOT NULL,
              output_path TEXT NOT NULL DEFAULT '',
              output_url TEXT NOT NULL DEFAULT '',
              output_duration_ms INTEGER NOT NULL DEFAULT 0,
              error TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              started_at TEXT NOT NULL DEFAULT '',
              finished_at TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_video_media_episode ON video_media(project_id, episode_id);
            """
        )
        ensure_column(conn, "episodes", "source_url", "TEXT NOT NULL DEFAULT ''")
        ensure_column(conn, "episodes", "ark_file_id", "TEXT NOT NULL DEFAULT ''")
        ensure_column(conn, "episodes", "ark_file_status", "TEXT NOT NULL DEFAULT ''")
        ensure_column(conn, "episodes", "ark_file_error", "TEXT NOT NULL DEFAULT ''")
        ensure_column(conn, "projects", "remake_mode", "TEXT NOT NULL DEFAULT 'overseas_localization'")
        ensure_column(conn, "projects", "source_language", "TEXT NOT NULL DEFAULT ''")
        ensure_column(conn, "projects", "storyboard_visual_style", "TEXT NOT NULL DEFAULT 'live_action'")
        ensure_column(conn, "projects", "replace_characters", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(conn, "projects", "replace_scenes", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(conn, "projects", "parent_project_id", "TEXT NOT NULL DEFAULT ''")
        ensure_column(conn, "asset_group_members", "sort_order", "INTEGER NOT NULL DEFAULT 0")
        migrate_asset_parent_links_to_groups(conn)


def migrate_asset_parent_links_to_groups(conn: sqlite3.Connection) -> None:
    """一次性把旧的父子挂载转换为自定义资产组，不删除旧表以便审计。"""
    legacy_rows = conn.execute("SELECT * FROM asset_parent_links ORDER BY created_at").fetchall()
    if not legacy_rows:
        return
    for project_id in sorted({str(row["project_id"]) for row in legacy_rows}):
        if conn.execute("SELECT 1 FROM asset_groups WHERE project_id=? LIMIT 1", (project_id,)).fetchone():
            continue
        project_links = [row for row in legacy_rows if str(row["project_id"]) == project_id]
        parent_names: dict[str, str] = {}
        for link in project_links:
            parent_key = str(link["parent_asset_key"])
            if parent_key in parent_names:
                continue
            parent_row = conn.execute(
                "SELECT name FROM assets WHERE project_id=? AND id=?",
                (project_id, link["parent_asset_id"]),
            ).fetchone()
            parent_names[parent_key] = str(parent_row["name"] if parent_row else parent_key)
        for parent_key in sorted(parent_names):
            members = {
                parent_key,
                *{str(link["child_asset_key"]) for link in project_links if str(link["parent_asset_key"]) == parent_key},
            }
            base_name = parent_names[parent_key].strip() or parent_key
            name = base_name
            suffix = 2
            while conn.execute("SELECT 1 FROM asset_groups WHERE project_id=? AND name=?", (project_id, name)).fetchone():
                name = f"{base_name} {suffix}"
                suffix += 1
            now = now_iso()
            group_id = new_id("asset_group")
            asset_type_row = conn.execute(
                "SELECT asset_type FROM assets WHERE project_id=? AND id=?",
                (project_id, next(link["parent_asset_id"] for link in project_links if str(link["parent_asset_key"]) == parent_key)),
            ).fetchone()
            asset_type = str(asset_type_row["asset_type"] if asset_type_row else "")
            conn.execute(
                "INSERT INTO asset_groups (id,project_id,name,asset_type,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                (group_id, project_id, name, asset_type, now, now),
            )
            for member_key in sorted(members):
                member_row = conn.execute(
                    "SELECT id FROM assets WHERE project_id=? AND id=? LIMIT 1",
                    (project_id, next((link["parent_asset_id"] if str(link["parent_asset_key"]) == member_key else link["child_asset_id"]) for link in project_links if member_key in {str(link["parent_asset_key"]), str(link["child_asset_key"])})),
                ).fetchone()
                if not member_row:
                    continue
                conn.execute(
                    """INSERT OR IGNORE INTO asset_group_members
                       (id,project_id,group_id,asset_id,asset_key,created_at) VALUES (?,?,?,?,?,?)""",
                    (new_id("asset_group_member"), project_id, group_id, member_row["id"], member_key, now),
                )
    conn.commit()


def ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    columns = {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def load_models_config() -> dict[str, Any]:
    if not MODELS_CONFIG_PATH.exists():
        return {"mode": "mock", "agents": {}}
    return json.loads(MODELS_CONFIG_PATH.read_text(encoding="utf-8"))


def load_agent_config(agent_key: str) -> dict[str, Any]:
    config = load_models_config()
    agent = config.get("agents", {}).get(agent_key, {})
    prompt_file = agent.get("promptFile", "")
    prompt_path = ROOT / prompt_file if prompt_file else None
    prompt = prompt_path.read_text(encoding="utf-8") if prompt_path and prompt_path.exists() else ""
    return {
        "mode": config.get("mode", "mock"),
        "provider": config.get("provider", "volcengine"),
        "provider": agent.get("provider", ""),
        "model": agent.get("model_name") or agent.get("model", ""),
        "enabled": agent.get("enabled", True),
        "prompt_file": prompt_file,
        "prompt": prompt,
        "temperature": agent.get("temperature"),
        "output_format": agent.get("outputFormat", "json"),
    }


def prepare_agent(agent_key: str) -> dict[str, Any]:
    config = load_models_config()
    agent = load_agent_config(agent_key)
    if not agent.get("enabled", True):
        mock_config = dict(config)
        mock_config["mode"] = "mock"
        provider = get_model_provider(mock_config)
        prepared = provider.prepare_agent(agent_key, agent)
        agent.update(
            {
                "mode": prepared.mode,
                "provider": prepared.provider,
                "model_name": prepared.model_name,
                "model_key": prepared.model_key,
                "ready": prepared.ready,
            }
        )
        return agent
    provider = get_model_provider(config)
    prepared = provider.prepare_agent(agent_key, agent)
    agent.update(
        {
            "mode": prepared.mode,
            "provider": prepared.provider,
            "model_name": prepared.model_name,
            "model_key": prepared.model_key,
            "ready": prepared.ready,
        }
    )
    return agent


def call_agent_chat(agent_key: str, agent: dict[str, Any], input_payload: dict[str, Any]) -> str:
    config = load_models_config()
    provider = get_model_provider(config)
    video_url = str(input_payload.get("__video_url") or "").strip()
    video_file_id = str(input_payload.get("__video_file_id") or "").strip()
    public_payload = {key: value for key, value in input_payload.items() if not key.startswith("__")}
    user_content = json.dumps(public_payload, ensure_ascii=False, indent=2)
    return provider.chat_completion(
        model_name=agent.get("model_name", ""),
        system_prompt=agent.get("prompt", ""),
        user_content=user_content,
        temperature=agent.get("temperature"),
        response_format={"type": "json_object"} if agent.get("output_format") == "json" else None,
        video_url=video_url or None,
        video_file_id=video_file_id or None,
        video_fps=1 if video_url else None,
    )


def call_compact_video_json(agent: dict[str, Any], input_payload: dict[str, Any], compact_prompt: str) -> str:
    config = load_models_config()
    provider = get_model_provider(config)
    video_url = str(input_payload.get("__video_url") or "").strip()
    video_file_id = str(input_payload.get("__video_file_id") or "").strip()
    public_payload = {key: value for key, value in input_payload.items() if not key.startswith("__")}
    user_content = json.dumps(public_payload, ensure_ascii=False, indent=2)
    return provider.chat_completion(
        model_name=agent.get("model_name", ""),
        system_prompt=compact_prompt,
        user_content=user_content,
        temperature=agent.get("temperature"),
        response_format={"type": "json_object"},
        video_url=video_url or None,
        video_file_id=video_file_id or None,
        video_fps=1 if video_url else None,
    )


def call_compact_video_json_stream(
    agent: dict[str, Any],
    input_payload: dict[str, Any],
    compact_prompt: str,
    on_progress: Callable[[str, int], None] | None = None,
) -> tuple[str, int, list[tuple[str, int]], str]:
    config = load_models_config()
    provider = get_model_provider(config)
    if not hasattr(provider, "chat_completion_stream"):
        try:
            raw = call_compact_video_json(agent, input_payload, compact_prompt)
            return raw, len(raw), [], ""
        except ProviderError as exc:
            return "", 0, [], str(exc)
    video_url = str(input_payload.get("__video_url") or "").strip()
    video_file_id = str(input_payload.get("__video_file_id") or "").strip()
    public_payload = {key: value for key, value in input_payload.items() if not key.startswith("__")}
    user_content = json.dumps(public_payload, ensure_ascii=False, indent=2)
    accumulated = ""
    events: list[tuple[str, int]] = []
    stream_error = ""
    try:
        for event_type, chunk_data in provider.chat_completion_stream(
            model_name=agent.get("model_name", ""),
            system_prompt=compact_prompt,
            user_content=user_content,
            temperature=agent.get("temperature"),
            response_format={"type": "json_object"},
            video_url=video_url or None,
            video_file_id=video_file_id or None,
            video_fps=1 if video_url else None,
        ):
            stage = "processing"
            if event_type == "response.output_text.delta":
                delta = str(chunk_data.get("delta") or "")
                accumulated += delta
                stage = "generating"
            elif event_type in {"response.completed", "done"}:
                stage = "done"
            if not events or events[-1][0] != stage:
                events.append((stage, len(accumulated)))
            if on_progress:
                on_progress(stage, len(accumulated))
    except ProviderError as exc:
        stream_error = str(exc)
        if on_progress:
            on_progress("failed", len(accumulated))
    return accumulated, len(accumulated), events, stream_error


ASSET_SOURCE_EXTRACTION_COMPACT_PROMPT = (CONFIG_DIR / "agents" / "asset_source_extraction.md").read_text(encoding="utf-8")
ASSET_SOURCE_LEGACY_V2_PROMPT = (CONFIG_DIR / "agents" / "asset_source_extraction_v2.md").read_text(encoding="utf-8")
ASSET_SOURCE_LEGACY_V1_PROMPT = (CONFIG_DIR / "agents" / "asset_source_extraction_v1.md").read_text(encoding="utf-8")
ASSET_LOCALIZATION_COMPACT_PROMPT = (CONFIG_DIR / "agents" / "asset_target_localization.md").read_text(encoding="utf-8")
ASSET_EXTRACTION_COMPACT_PROMPT = ASSET_SOURCE_EXTRACTION_COMPACT_PROMPT
ASSET_DETAIL_COMPLETION_PROMPT = """你只补全输入资产的“缺失字段”。只输出 JSON：{"资产补全":[{"资产变量":"原变量","payload_patch":{},"current_child_patch":{},"needsReview":[]}]}
国内与海外项目使用完全相同的资产字段。JSON结构、分析内容、源事实、目标视觉描述及资产图提示词均使用简体中文；仅服装字样、店招、铭牌等资产表面可见文字遵循目标语言。只依据源事实和目标国家/语言生成目标视觉描述；源事实与目标描述分别保存。用户目标外观描述是可选视觉覆盖，不得写入本地化视觉描述或固定风格约束字段。
只返回输入批次中的资产，变量原样复制。仅写入缺失字段清单列出的键；不得改写已存在值、资产名称、变量或编号。用户目标视觉补充只可填充目标设计字段，不能当成视频/剧情来源事实。原片未明确的观察事实仍写 unknown 并在 needsReview 说明；该规则只约束原剧事实字段，不妨碍你依据明确目标国家、目标时代、角色剧情身份和输入中的目标外观要求，为“本地化视觉形象描述/角色资产图结构化描述/角色资产图生成提示词”补全具体自洽的目标设计。原视频观察到的族裔、肤色、脸部、眼睛、发型属于原剧事实，不自动等同用户指定的目标族裔；用户明确的目标族裔/外貌优先，未指定时按目标国家与时代、角色关系和场景选定具体目标外貌。不得机械执行国籍等于族裔，不得给所有欧美角色统一指定白人、蓝眼或高鼻，也不得把源场景地域元素无解释地留在冲突的目标场景中。目标角色脸部骨相、眼形/虹膜、肤色、发色、发型必须与所选目标族裔和角色连续性一致。不得补入原文不存在的情节/资产。
角色字段：本地化视觉形象描述、角色资产图结构化描述、角色资产图生成提示词。结构化描述沿用现有分区，并覆盖v3十五类：基础设定（性别及判断依据、视觉年龄、国家/时代/朝代、身份、性格、气质），身材体态（头身比、身高体感、体型、肩颈、腰腿、肌肉线条、姿态），脸部（脸型、骨相、眉眼虹膜、鼻唇、面部毛发、妆感、肤色/皮肤质感/气色/表情/眼神），发型（名称、颜色、长度、刘海/分线、质感、状态），服装（等级、场合、颜色、内中外层、领型、袖型、腰线、衣长、下装、摆型、拖尾程度、摆与地面关系、鞋、鞋面/脚踝可见性、面料、纹样及位置、结构廓形、工艺、穿着状态）。每个服装子资产只有一套完整主视觉；变化子资产分别描述，不能混穿。根据视频/剧情明确证据判别性别；姓名和外貌不作为性别证据，证据不足使用中性路径。根据时代、身份、服装等级和场合选择衣装；拖尾等级只可为有证据支持的普通垂摆、轻度拖地或高位典礼遮鞋，不按身份名一刀切，不编造长度数字。角色提示词必须写结构化字段的具体目标视觉结果，过滤手持物、武器、腰间悬挂物，仅保留已有记录中的固定穿戴配饰；不写“根据原片/保持原貌”等让图片模型另找上下文的说法。
视觉事实来源、固定穿戴配饰、手持或悬挂物档案为已有视频抽取阶段记录；详情补全只修补目标视觉结构，不得新造视频观察、剧情来源事实或配饰记录。unknown及needsReview只留在结构化记录，不作为可用生图描述。仅对已有可见/剧情证据或经确认的用户目标视觉补充生成具体目标值，不能根据名字猜性别或为高位角色自动编造服装细节。
场景字段：本地化场景描述、场景资产图生成提示词；描述完整空间结构、时代/地域语境、入口窗户/道路/建筑、家具设施、材质、光线、空间关系及当前场景子状态。将可见布局具体写明；未知内部记录unknown/needsReview，生成prompt不得保留“如果原片能判断/根据原片”条件句。
道具字段：本地化道具描述、道具资产图生成提示词；描述物件类别、形状、尺寸比例、材质、颜色、磨损/状态、文字标识、剧情用途与所属关系。将主体形状和材质写成自包含描述；未知只进入needsReview，不把unknown占位作为生图指令。不得把角色的手持/悬挂道具混入角色图。
`payload_patch`仅用于场景或道具缺失字段，`current_child_patch`用于角色当前子资产缺失字段。固定真人/CG风格约束由后端追加，不重复输出。"""


def call_agent_chat_stream(
    agent_key: str,
    agent: dict[str, Any],
    input_payload: dict[str, Any],
    on_progress: Callable[[str, int], None] | None = None,
) -> tuple[str, int, list[tuple[str, int]]]:
    """Call agent with SSE streaming. Returns (full_text, total_chars, progress_events).
    on_progress(stage, chars_so_far) is called for each meaningful event.
    """
    config = load_models_config()
    provider = get_model_provider(config)
    video_url = str(input_payload.get("__video_url") or "").strip()
    video_file_id = str(input_payload.get("__video_file_id") or "").strip()
    public_payload = {key: value for key, value in input_payload.items() if not key.startswith("__")}
    user_content = json.dumps(public_payload, ensure_ascii=False, indent=2)

    accumulated = ""
    total_chars = 0
    events: list[tuple[str, int]] = []
    stage = "connecting"

    for event_type, chunk_data in provider.chat_completion_stream(
        model_name=agent.get("model_name", ""),
        system_prompt=agent.get("prompt", ""),
        user_content=user_content,
        temperature=agent.get("temperature"),
        response_format={"type": "json_object"} if agent.get("output_format") == "json" else None,
        video_url=video_url or None,
        video_file_id=video_file_id or None,
        video_fps=1 if video_url else None,
    ):
        if event_type == "response.in_progress":
            stage = "processing"
        elif event_type == "response.output_text.delta":
            stage = "generating"
            delta = str(chunk_data.get("delta") or "")
            accumulated += delta
            total_chars += len(delta)
        elif event_type == "response.completed":
            stage = "done"

        if not events or stage != events[-1][0]:
            events.append((stage, total_chars))
            if on_progress:
                on_progress(stage, total_chars)

    if on_progress:
        on_progress("done", total_chars)
    return accumulated, total_chars, events


def parse_json_object(raw: str, fallback: dict[str, Any]) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else fallback
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(raw[start : end + 1])
                return parsed if isinstance(parsed, dict) else fallback
            except json.JSONDecodeError:
                return fallback
        return fallback


def parse_json_object_with_repair(raw: str) -> tuple[dict[str, Any], bool]:
    try:
        parsed = json.loads(raw)
        return (parsed, False) if isinstance(parsed, dict) else ({}, False)
    except json.JSONDecodeError:
        try:
            parsed = repair_json(raw, return_objects=True)
        except Exception:
            return {}, False
        return (parsed, True) if isinstance(parsed, dict) else ({}, False)


def extract_episode_index(filename: str, fallback: int) -> int:
    stem = Path(filename).stem
    matches = re.findall(r"\d+", stem)
    if not matches:
        return fallback
    return int(matches[-1])


def get_project_or_404(conn: sqlite3.Connection, project_id: str) -> dict[str, Any]:
    project = row_to_dict(conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone())
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def get_episode_or_first(conn: sqlite3.Connection, project_id: str, episode_id: str | None) -> dict[str, Any]:
    if episode_id:
        episode = row_to_dict(
            conn.execute(
                "SELECT * FROM episodes WHERE id = ? AND project_id = ?",
                (episode_id, project_id),
            ).fetchone()
        )
    else:
        episode = row_to_dict(
            conn.execute(
                "SELECT * FROM episodes WHERE project_id = ? ORDER BY created_at DESC LIMIT 1",
                (project_id,),
            ).fetchone()
        )
    if not episode:
        raise HTTPException(status_code=400, detail="Upload an episode before running this task")
    return episode


def enrich_episode(episode: dict[str, Any] | None) -> dict[str, Any] | None:
    if not episode:
        return episode
    file_path = Path(str(episode.get("file_path") or ""))
    episode["size_bytes"] = file_path.stat().st_size if file_path.exists() else 0
    episode["file_url"] = data_file_url(str(file_path)) if file_path.is_file() else ""
    return episode


def enrich_episodes(episodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [enrich_episode(episode) or episode for episode in episodes]


def upload_episode_to_ark(conn: sqlite3.Connection, episode: dict[str, Any]) -> dict[str, str]:
    try:
        config = load_models_config()
        provider = get_model_provider(config)
        expire_days = int(config.get("volcengine", {}).get("fileExpireDays") or 30)
        expire_days = max(1, min(30, expire_days))
        expire_at = int(time.time()) + expire_days * 86400
        upload_result = provider.upload_file(
            episode["file_path"], purpose="user_data", video_fps=1, expire_at=expire_at
        )
        file_id = str(upload_result.get("id") or "")
        status = str(upload_result.get("status") or "processing")
        if file_id:
            for _ in range(3):
                if status in {"processed", "completed", "success", "active"}:
                    break
                time.sleep(2)
                try:
                    file_info = provider.retrieve_file(file_id)
                    status = str(file_info.get("status") or status)
                except ProviderError:
                    break
        conn.execute(
            """
            UPDATE episodes
            SET ark_file_id = ?, ark_file_status = ?, ark_file_error = ?
            WHERE id = ?
            """,
            (file_id, status, "", episode["id"]),
        )
        return {"file_id": file_id, "status": status, "error": ""}
    except ProviderError as exc:
        error = str(exc)
        conn.execute(
            """
            UPDATE episodes
            SET ark_file_status = ?, ark_file_error = ?
            WHERE id = ?
            """,
            ("upload_failed", error, episode["id"]),
        )
        return {"file_id": "", "status": "upload_failed", "error": error}


def ensure_episode_ark_file(conn: sqlite3.Connection, episode: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Validate the stored Ark file id and re-upload the local video when the
    remote file has expired or been deleted (Ark files expire after fileExpireDays).

    Returns (episode, error). On success the episode carries a usable
    ark_file_id; on failure error describes why no usable file is available.
    """
    ark_id = str(episode.get("ark_file_id") or "").strip()
    if ark_id:
        provider = get_model_provider(load_models_config())
        if not hasattr(provider, "retrieve_file"):
            # Provider cannot validate remote files: keep trusting the stored id.
            return episode, ""
        remote_invalid = False
        try:
            status = str(provider.retrieve_file(ark_id).get("status") or "").lower()
            for _ in range(4):
                if status != "processing":
                    break
                time.sleep(2)
                status = str(provider.retrieve_file(ark_id).get("status") or status).lower()
            if status == "failed":
                remote_invalid = True
        except ProviderError as exc:
            # Only a definitive "file is gone" answer (HTTP 404) triggers a
            # re-upload; transient network errors keep the stored id so the
            # model call itself surfaces any real problem.
            if "404" in str(exc) or "NotFound" in str(exc):
                remote_invalid = True
        if not remote_invalid:
            return episode, ""
        conn.execute(
            "UPDATE episodes SET ark_file_id = '', ark_file_status = ?, ark_file_error = ? WHERE id = ?",
            ("expired", "方舟侧文件已过期或失效，触发重新上传", episode["id"]),
        )
        conn.commit()
        episode = get_episode_or_first(conn, episode["project_id"], episode["id"])
        if not Path(str(episode.get("file_path") or "")).exists():
            return episode, "方舟侧文件已过期或失效，且本地视频文件不存在，无法重新上传。"
    if not Path(str(episode.get("file_path") or "")).exists():
        return episode, ""
    ark_result = upload_episode_to_ark(conn, episode)
    conn.commit()
    episode = get_episode_or_first(conn, episode["project_id"], episode["id"])
    if not ark_result["file_id"]:
        return episode, ark_result.get("error") or "Ark 文件上传失败"
    return episode, ""


def project_valid_target_profile_for_asset_view(asset: dict[str, Any], project: dict[str, Any]) -> None:
    """Project a valid canonical target profile onto legacy UI fields in memory.

    The database payload and source evidence stay untouched. This keeps older
    UI readers from showing stale target aliases when the profile is canonical.
    """
    payload = asset.get("payload") if isinstance(asset.get("payload"), dict) else {}
    if str(project.get("target_country") or "").strip() in {"", "中国", "中国大陆"}:
        return
    profile = payload.get("localizedProfile") if isinstance(payload.get("localizedProfile"), dict) else {}
    if profile.get("status") not in {"complete", "source_review"} or not localized_profile_context_matches(payload, project):
        return

    asset["payload"] = payload
    attach_display_variable(asset)
    old_display_aliases = [
        str(asset.get("display_variable") or "").strip(),
        str(payload.get("旧展示资产变量") or "").strip(),
    ]
    payload_for_projection = dict(payload)
    payload_for_projection["资产类型"] = str(asset.get("asset_type") or payload.get("资产类型") or "")
    projected = project_asset_profile_to_legacy(payload_for_projection, project)
    for key, value in projected.items():
        if key.startswith("本地化") or key in {"角色资产图结构化描述", "场景资产图生成提示词", "道具资产图生成提示词"}:
            payload[key] = value

    if str(asset.get("asset_type") or "") == "角色":
        canonical_description = localized_role_reference_appearance(payload, profile)
    else:
        canonical_description = str(profile.get("coreDescription") or profile.get("visualDescription") or "").strip()
    if canonical_description:
        payload["当前目标画像描述"] = canonical_description

    current_child = payload.get("当前子资产") if isinstance(payload.get("当前子资产"), dict) else {}
    projected_child = projected.get("当前子资产") if isinstance(projected.get("当前子资产"), dict) else {}
    child_target_keys = {key for key in projected_child if key.startswith("本地化") or key == "角色资产图结构化描述"}
    for key in child_target_keys:
        current_child[key] = projected_child[key]
    payload["当前子资产"] = current_child

    child_id = str(profile.get("childId") or payload.get("sourceSnapshot", {}).get("childId") or current_child.get("子资产编号") or "").strip()
    children = payload.get("子资产")
    if child_id and isinstance(children, list):
        for child in children:
            if not isinstance(child, dict) or str(child.get("子资产编号") or child.get("childId") or "").strip() != child_id:
                continue
            for key in child_target_keys:
                if key in projected_child:
                    child[key] = projected_child[key]
            break

    user_name = explicit_user_localized_name(payload)
    canonical_name = user_name or str(profile.get("localizedName") or "").strip()
    if canonical_name:
        asset["localized_name"] = canonical_name
    aliases = payload.get("历史展示变量别名") if isinstance(payload.get("历史展示变量别名"), list) else []
    for alias in old_display_aliases:
        if alias and alias not in aliases:
            aliases.append(alias)
    if aliases:
        payload["历史展示变量别名"] = aliases
    asset["payload"] = payload


def get_asset_response(conn: sqlite3.Connection, project_id: str, asset_id: str) -> dict[str, Any]:
    project = get_project_or_404(conn, project_id)
    asset = row_to_dict(
        conn.execute(
            "SELECT * FROM assets WHERE id = ? AND project_id = ?",
            (asset_id, project_id),
        ).fetchone()
    )
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    asset["payload"] = enrich_asset_payload_urls(json.loads(asset.pop("payload_json")))
    project_valid_target_profile_for_asset_view(asset, project)
    source_description = asset_source_description(asset["asset_type"], asset["payload"])
    asset["payload"].setdefault("原片资产描述", source_description or "原片描述未留存")
    asset["payload"].setdefault("目标生成描述", "")
    if project.get("remake_mode") == "asset_replacement":
        asset["payload"].setdefault("目标资产来源", "沿用原貌")
    selected_image = conn.execute(
        "SELECT id,file_path,payload_json FROM asset_images WHERE project_id=? AND asset_id=? AND is_selected=1 ORDER BY created_at DESC LIMIT 1",
        (project_id, asset_id),
    ).fetchone()
    effective = resolve_effective_asset_reference(asset, project, selected_image)
    asset["payload"]["effectiveAssetSource"] = effective["effectiveAssetSource"]
    asset["payload"]["当前有效外观描述"] = effective["description"]
    asset["payload"]["当前有效资产名"] = effective["name"]
    asset["payload"]["当前有效资产就绪"] = effective["ready"]
    asset["payload"]["generationInProgress"] = conn.execute(
        "SELECT 1 FROM asset_generation_jobs WHERE project_id=? AND asset_id=? AND status='running' LIMIT 1",
        (project_id, asset_id),
    ).fetchone() is not None
    prepare_asset_prompt_fields(asset, project)
    attach_display_variable(asset)
    images = rows_to_dicts(
        conn.execute(
            "SELECT * FROM asset_images WHERE asset_id = ? ORDER BY created_at DESC",
            (asset_id,),
        ).fetchall()
    )
    for image in images:
        enrich_asset_image(image)
    asset["images"] = images
    return {"asset": asset}


def asset_source_description(asset_type: str, payload: dict[str, Any]) -> str:
    keys = {
        "角色": ["原剧视觉形象描述", "原剧角色描述", "原始角色描述", "角色原始描述", "角色描述"],
        "场景": ["原剧场景描述", "原始场景描述", "空间结构描述", "场景描述"],
        "道具": ["原剧道具描述", "原始道具描述", "道具描述", "剧情功能"],
    }.get(asset_type, ["原始描述"])
    child = payload.get("当前子资产") if isinstance(payload.get("当前子资产"), dict) else {}
    value = pick_payload_text(child, keys) or pick_payload_text(payload, keys)
    if not value and asset_type == "角色":
        value = pick_payload_text(payload, ["原剧核心形象描述", "原剧人物外观"])
    return value


def strip_previous_target_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: strip_previous_target_fields(item)
            for key, item in value.items()
            if not key.startswith("本地化") and key not in {"目标生成描述", "目标资产来源", "用户编辑资产图生成提示词", "自动编译资产图生成提示词", "展示资产变量", "最终资产图生成提示词", "最终视频生成提示词"}
        }
    if isinstance(value, list):
        return [strip_previous_target_fields(item) for item in value]
    return value


def clean_target_asset_description(text: str, style: str) -> str:
    """Clean style/layout conflicts from generation-only text; never mutate source facts."""
    cleaned = str(text or "")
    if style == "cg_3d":
        patterns = (
            r"真人(?:实拍|写实|摄影)(?:电影质感)?", r"照片级真实", r"真实摄影质感",
            r"photorealistic", r"photographic skin", r"真实世界摄影成像",
        )
        for pattern in patterns:
            cleaned = re.sub(rf"(?:避免|不要|禁止)(?:采用|使用)?{pattern}", "", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"(?:避免|不要|禁止)(?:采用|使用)?(?:三维)?CG(?:渲染)?(?:与卡通渲染质感)?", "", cleaned, flags=re.IGNORECASE)
    else:
        for pattern in (r"(?:三维)?CG渲染", r"三维游戏CG", r"数字雕刻材质", r"3D render", r"CG style"):
            cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"[，,、；;：:]\s*[，,、；;：:]", "，", cleaned)
    return cleaned.strip(" \t\n，,、；;：:")


def normalize_asset_visual_override(text: str, existing: str = "") -> tuple[str, bool]:
    """Keep editable visual text separate from a previously formatted full prompt."""
    value = str(text or "").strip()
    if not value:
        return "", True
    marker = "【用户指定目标外观覆盖】"
    if marker in value:
        value = value.split(marker, 1)[1]
        for end_marker in (
            "\n优先于同字段", "\n用户指定目标外观覆盖（优先于同字段",
            "【共同展示构图】", "【目标视觉风格】", "【固定约束】",
        ):
            value = value.split(end_marker, 1)[0]
        return value.strip(), True
    if any(marker in value for marker in ("【基础设定】", "【共同展示构图】", "【目标视觉风格】", "【固定约束】")):
        # Older callers may send a complete final prompt. Reuse the separately
        # stored user overlay rather than nesting the generated template again.
        return existing, False
    return value, True


def localized_role_reference_appearance(payload: dict[str, Any], profile: dict[str, Any]) -> str:
    """Compile a static appearance summary for storyboard references.

    Full asset-sheet prompts and temporary wear/action beats do not belong in a
    shot's target-appearance context. The source/profile records remain intact;
    this is only the read-time reference summary.
    """
    structure = profile.get("structuredDescription") if isinstance(profile.get("structuredDescription"), dict) else {}
    section_fields = (
        ("基础设定", ("性别", "视觉年龄", "身份定位", "族裔外貌")),
        ("脸部特征", ("目标族裔背景", "脸型", "骨相", "眉形", "眼形", "虹膜颜色", "鼻形", "唇形", "妆感", "皮肤肤色", "皮肤质感")),
        ("发型", ("发型名称", "发色", "长度", "刘海 / 分线", "发丝质感")),
        ("身材体态", ("体型", "肩颈", "腰腿", "整体体态")),
        ("服装", ("服装等级", "场合", "主色", "副色", "点缀色", "内衬", "中间层", "外层", "领型", "袖型", "腰线", "衣长", "下装", "摆型", "拖尾程度", "摆与地面关系", "鞋子", "面料", "纹样", "纹样位置", "结构与廓形", "工艺与重工程度", "穿着状态")),
    )
    parts: list[str] = []
    for section_name, fields in section_fields:
        section = structure.get(section_name) if isinstance(structure.get(section_name), dict) else {}
        for field in fields:
            value = str(section.get(field) or "").strip()
            if not value:
                continue
            if section_name == "服装" and field == "穿着状态":
                value = stable_wear_state_text(value)
            if value:
                parts.append(f"{field}：{value}")
    if structure:
        accessories = fixed_worn_accessory_observations(payload, {}, structure)
        if accessories:
            parts.append("固定穿戴配饰：" + "、".join(accessories))
    user_appearance = role_asset_prompt_user_appearance(
        str(payload.get("用户编辑资产图生成提示词") or ""), profile
    )
    if user_appearance:
        parts.append("用户指定目标外观：" + user_appearance)
    if parts:
        return "；".join(parts)
    return stable_wear_state_text(str(profile.get("coreDescription") or profile.get("visualDescription") or "").strip()) or user_appearance


def role_asset_prompt_user_appearance(prompt: str, profile: dict[str, Any] | None = None) -> str:
    """Extract an explicit appearance supplement without copying sheet layout/style."""
    value = str(prompt or "").strip()
    if not value:
        return ""
    marker = "【用户指定目标外观覆盖】"
    if marker in value:
        value = value.split(marker, 1)[1]
        for end_marker in ("\n优先于同字段", "\n【共同展示构图】", "\n【目标视觉风格】", "\n固定约束"):
            value = value.split(end_marker, 1)[0]
    elif "【服装】" in value or "【发型】" in value or "【脸部特征】" in value:
        # A user may edit the generated full prompt (for example, changing a
        # white dress to red). Extract only changed appearance fields so the
        # storyboard gets the edit without inheriting the asset-sheet layout.
        structure = profile.get("structuredDescription") if isinstance(profile, dict) and isinstance(profile.get("structuredDescription"), dict) else {}
        allowed_sections = {"基础设定", "脸部特征", "发型", "身材体态", "服装"}
        layout_sections = {"共同展示构图", "目标视觉风格", "服装文字约束"}
        field_values: list[str] = []
        current_section = ""
        for line in value.splitlines():
            heading = re.fullmatch(r"\s*【([^】]+)】\s*", line)
            if heading:
                section_name = heading.group(1).strip()
                current_section = section_name if section_name in allowed_sections else ""
                if section_name in layout_sections:
                    current_section = ""
                continue
            if not current_section:
                continue
            field = re.match(r"\s*([^：:]+)[：:]\s*(.*?)\s*$", line)
            if not field:
                continue
            key, candidate_value = field.group(1).strip(), field.group(2).strip()
            if not candidate_value or key in {"国家 / 时代", "角色名称", "目标语言"}:
                continue
            baseline_section = structure.get(current_section) if isinstance(structure.get(current_section), dict) else {}
            if key not in baseline_section:
                continue
            baseline = str(baseline_section.get(key) or "").strip()
            if candidate_value != baseline:
                field_values.append(f"{current_section}.{key}：{candidate_value}")
        value = "；".join(field_values)
        if not value:
            return ""
    elif re.search(r"(?i)four[- ]view|front side back|single[- ]row grid|multi[- ]view layout|三视图|四视图|三面视图", value):
        return ""
    value = value.replace(CG_ROLE_FIXED_TEMPLATE, "")
    value = re.sub(r"(?is)\[Style & Render\].*?(?=\n\[|$)", "", value)
    value = re.sub(r"(?is)\[3D Surface Shader\].*?(?=\n\[|$)", "", value)
    value = re.sub(r"(?is)\[Layout & Light\].*?(?=\n\[|$)", "", value)
    value = re.sub(r"(?is)【共同展示构图】.*", "", value)
    value = re.sub(r"(?is)【目标视觉风格】.*", "", value)
    value = re.sub(r"(?is)固定约束.*", "", value)
    return re.sub(r"\s+", " ", value).strip(" \t\r\n，,、；;：:")


def strip_resolved_source_review_status_block(prompt: str, profile: dict[str, Any]) -> str:
    """Remove only a stale system banner after every named review path resolves."""
    value = str(prompt or "")
    marker = "【源事实待核对：自动生图已锁定】"
    if marker not in value or profile.get("status") != "complete":
        return value
    before, remainder = value.split(marker, 1)
    line, separator, after = remainder.partition("\n")
    if not line.strip() and separator:
        line, separator, after = after.partition("\n")
    paths = [item.strip() for item in re.split(r"[、,，]", line.strip()) if item.strip()]
    if not paths:
        return value
    blocking = set(str(item).strip() for item in (profile.get("sourceReviewPaths") or profile.get("needsReview") or []) if str(item).strip())
    non_blocking = set(str(item).strip() for item in (profile.get("nonBlockingSourceReviewPaths") or []) if str(item).strip())
    if any(path in blocking and path not in non_blocking for path in paths):
        return value
    return (before.rstrip() + ("\n" + after if separator else "")).strip()


def resolve_effective_asset_reference(
    asset: dict[str, Any], project: dict[str, Any], selected_image: sqlite3.Row | dict[str, Any] | None = None
) -> dict[str, Any]:
    """Resolve the one asset appearance that both the UI and storyboard should use."""
    payload = asset.get("payload") if isinstance(asset.get("payload"), dict) else {}
    asset_type = str(asset.get("asset_type") or "")
    mode = str(project.get("remake_mode") or "overseas_localization")
    source_description = asset_source_description(asset_type, payload) or "原片描述未留存"
    source_image_path = str(payload.get("原资产图路径") or "")
    target_description = pick_payload_text(payload, ["目标生成描述"])
    manual_prompt = str(payload.get("用户编辑资产图生成提示词") or "").strip()
    localized_profile = payload.get("localizedProfile") if isinstance(payload.get("localizedProfile"), dict) else {}
    overseas = str(project.get("target_country") or "").strip() not in {"", "中国", "中国大陆"}
    target_profile_status = str(localized_profile.get("status") or "")
    if overseas:
        if target_profile_status in {"complete", "source_review"} and localized_profile_context_matches(payload, project):
            if asset_type == "角色":
                target_description = localized_role_reference_appearance(payload, localized_profile)
            else:
                target_description = str(localized_profile.get("coreDescription") or localized_profile.get("visualDescription") or "").strip()
        else:
            target_description = explicit_user_visual_lock(payload)
    replacement_enabled = mode == "asset_replacement" and (
        (asset_type == "角色" and bool(project.get("replace_characters")))
        or (asset_type == "场景" and bool(project.get("replace_scenes")))
    )
    effective_source = "target"
    if mode == "asset_replacement" and (
        not replacement_enabled or payload.get("目标资产来源") == "沿用原貌"
    ):
        effective_source = "source"
    if effective_source == "source":
        image_path = source_image_path
        image_id = ""
        image_source = "project_source_frame" if source_image_path else ""
        description = source_description
        name = str(asset.get("name") or asset.get("localized_name") or "")
        ready = bool(source_image_path and source_description != "原片描述未留存")
    else:
        candidate = dict(selected_image) if selected_image else None
        source_review_draft = overseas and target_profile_status == "source_review"
        # A source frame is evidence for review, never an implicit target
        # identity image in an overseas project.
        image_path = str(candidate["file_path"]) if candidate else ("" if overseas or (mode == "asset_replacement" and replacement_enabled) else source_image_path)
        image_id = str(candidate["id"]) if candidate else ""
        candidate_payload = json.loads(candidate["payload_json"]) if candidate and candidate.get("payload_json") else {}
        image_source = str(candidate_payload.get("source") or ("project_source_frame" if image_path == source_image_path and image_path else ""))
        description = target_description or (source_description if not overseas else "")
        name = str(asset.get("localized_name") or asset.get("name") or "")
        if source_review_draft:
            effective_source = "target_draft"
        if mode == "asset_replacement" and replacement_enabled:
            ready = bool(candidate) and not source_review_draft
        else:
            ready = bool(image_path and description and description != "原片描述未留存") and not source_review_draft
    return {
        "effectiveAssetSource": effective_source,
        "description": description,
        "name": name,
        "imageId": image_id,
        "imagePath": image_path,
        "imageSource": image_source,
        "ready": ready,
    }


def asset_prompt_context(project: dict[str, Any]) -> tuple[str, str, str]:
    mode = str(project.get("remake_mode") or "overseas_localization")
    source_language = str(project.get("source_language") or "").strip()
    target_country = str(project.get("target_country") or "").strip()
    target_language = str(project.get("target_language") or "").strip()
    keep_source_language = not target_language or target_language in {"沿用原片语言", "原片语言", "保留原片语言，不翻译"}
    if keep_source_language:
        target_country = target_country or "中国"
        target_language = source_language or "原片语言（保持原样，不翻译）"
    else:
        target_country = target_country or "中国"
        target_language = target_language or source_language or "原片语言（保持原样，不翻译）"
    style = "cg_3d" if mode == "cg_3d" or project.get("storyboard_visual_style") == "cg_3d" else "live_action"
    return target_country, target_language, style


def enforce_role_cg_fixed_suffix(prompt: str, asset_type: str, project: dict[str, Any]) -> str:
    if asset_type != "角色" or asset_prompt_context(project)[2] != "cg_3d":
        return prompt
    suffix = f"\n\n固定约束 \n{CG_ROLE_FIXED_TEMPLATE}"
    without_template = str(prompt or "").replace(suffix, "").replace(CG_ROLE_FIXED_TEMPLATE, "").strip()
    without_template = re.sub(r"(?:【固定约束】|固定约束)\s*$", "", without_template).rstrip()
    return f"{without_template}{suffix}"


def build_group_portrait_asset_prompt(
    asset: dict[str, Any], project: dict[str, Any], target_country: str, target_language: str
) -> str:
    """Compile observed crowd facts into a target-country group prompt."""
    payload = asset.get("payload") if isinstance(asset.get("payload"), dict) else {}
    source = payload.get("sourceSnapshot") if isinstance(payload.get("sourceSnapshot"), dict) else {}
    child = payload.get("当前子资产") if isinstance(payload.get("当前子资产"), dict) else {}
    observed_parts = (
        str(source.get("coreDescription") or "").strip(),
        str(source.get("visualDescription") or "").strip(),
        str(source.get("groupDescription") or "").strip(),
        str(child.get("原剧角色描述") or child.get("原剧视觉形象描述") or "").strip(),
        str(payload.get("原剧角色描述") or payload.get("原剧视觉形象描述") or "").strip(),
    )
    observed = "；".join(dict.fromkeys(value for value in observed_parts if value))
    if not observed:
        return ""
    # Keep source-observed crowd composition and clothing while translating
    # source-region appearance labels through the selected target country.
    observed = re.sub(r"(?:黄皮肤?|黄种人|亚洲人|东亚人|中国(?:大陆)?当代|当代中国|中国(?:大陆)?人外貌)", "", observed)
    observed = re.sub(r"[，,；;、\s]{2,}", "，", observed).strip("，,；;、 ")
    name = str(asset.get("localized_name") or asset.get("name") or source.get("name") or "群像")
    target_look = localized_ethnicity_text(target_country, observed + " 群像", "群像人物")
    _, _, style = asset_prompt_context(project)
    style_text = CG_ROLE_FIXED_TEMPLATE if style == "cg_3d" else ROLE_LIVE_STYLE
    group_layout = (
        "展示构图：左侧为整组多人正面胸像，右侧为同一组人物的正面、右侧面90°、背面全身视图；所有视图中的组内人数、人物顺序、服装与人物关系保持一致，不把每名群像人物复制成各自独立的单人四视图。白色干净背景，无文字、无水印、无logo。"
        if style == "cg_3d" else
        "展示构图：单幅完整多人群像构图，人物自然站立并保持源观察所示的相对位置；不拆成单人视图，不新增未记录的人物或道具。白色干净背景，无文字、无水印、无logo。"
    )
    prompt = (
        f"【群像角色资产图】\n资产名称：{name}\n目标国家：{target_country}\n目标语言：{target_language}\n"
        f"源视频已记录的群像事实：{observed}\n"
        f"目标群像外貌指导：{target_look}。仅将明确的外貌地域特征按目标国家转写；保留已记录的人数、性别构成、年龄范围、服装、动作、构图与人物关系，不补造未记录的个体身份或细节。群像人数遵循已记录的源观察；若源资料未注明具体人数，不强行指定确切人数。\n\n"
        f"【共同展示构图】\n{group_layout}\n\n"
        f"【目标视觉风格】\n{style_text}"
    )
    return enforce_role_cg_fixed_suffix(prompt, "角色", project)


def build_project_asset_prompt(asset_type: str, payload: dict[str, Any], project: dict[str, Any]) -> str:
    payload = project_asset_profile_to_legacy(payload, project)
    target_country, target_language, style = asset_prompt_context(project)
    asset = {"asset_type": asset_type, "payload": payload, "name": payload.get("原剧角色名称") or payload.get("原剧资产名称") or ""}
    if is_group_portrait_asset(asset):
        group_prompt = build_group_portrait_asset_prompt(asset, project, target_country, target_language)
        if group_prompt:
            return group_prompt
    prompt = infer_asset_image_prompt(
        asset_type,
        payload,
        target_country,
        target_language,
        style,
        pick_payload_text(payload, ["目标生成描述"]),
    )
    return enforce_role_cg_fixed_suffix(prompt, asset_type, project)


def prepare_asset_prompt_fields(asset: dict[str, Any], project: dict[str, Any]) -> None:
    payload = asset.get("payload") if isinstance(asset.get("payload"), dict) else {}
    target_country = str(project.get("target_country") or "").strip()
    refresh_role_source_review_profile(payload)
    profile = payload.get("localizedProfile") if isinstance(payload.get("localizedProfile"), dict) else {}
    source_review = target_country not in {"", "中国", "中国大陆"} and profile.get("status") == "source_review" and localized_profile_context_matches(payload, project)
    pending = target_country not in {"", "中国", "中国大陆"} and (
        profile.get("status") != "complete" or not localized_profile_context_matches(payload, project)
    )
    compiled_prompt = build_project_asset_prompt(asset.get("asset_type", ""), payload, project)
    group_asset = is_group_portrait_asset(asset)
    automatic = (
        compiled_prompt
        if source_review or (pending and group_asset and compiled_prompt.strip())
        else "目标画像待本地化；当前旧资产共享描述不能作为目标提示词。"
        if pending else compiled_prompt
    )
    payload["目标画像状态"] = "待源事实核对" if source_review else "待本地化" if pending else "已就绪"
    payload["自动编译资产图生成提示词"] = automatic
    final_prompt = str(payload.get("用户编辑资产图生成提示词") or automatic)
    final_prompt = strip_resolved_source_review_status_block(final_prompt, profile)
    payload["最终资产图生成提示词"] = (
        final_prompt
        if pending and not source_review and not str(payload.get("用户编辑资产图生成提示词") or "").strip()
        else enforce_role_cg_fixed_suffix(final_prompt, asset.get("asset_type", ""), project)
    )
    asset["payload"] = payload


def route_project_agent_prompt(agent: dict[str, Any], project: dict[str, Any], storyboard: bool = False) -> None:
    target_country = str(project.get("target_country") or "").strip()
    target_language = str(project.get("target_language") or "").strip()
    country_requires_localization = target_country not in {"", "中国", "中国大陆"}
    language_requires_localization = target_language not in {"", "沿用原片语言", "原片语言", "保留原片语言，不翻译", "中文"}
    if country_requires_localization or language_requires_localization:
        return
    source_language = project.get("source_language") or "原片语言（具体语言未知时也保持原片语言，不翻译）"
    route = (
        "\n\n【项目模式强约束】当前是国内重制项目。不得执行目标国家替换、跨文化本土化、英文/目标语言翻译；保留源视频人物、场景、道具和文字事实，只做用户指定的视觉风格重制。"
        f"成片语言保持：{source_language}。不得根据空字段猜测普通话、美国英语或其他语言，也不得将本提示当作语言识别结果。"
    )
    prompt = str(agent.get("prompt") or "")
    prompt = prompt.replace("短剧海外重制系统", "短剧重制系统")
    prompt = prompt.replace("目标国家为空时，默认使用美国。", "国内模式下目标国家不适用；海外模式才读取目标国家。")
    prompt = prompt.replace("目标语言为空时，默认使用英语。", "国内模式下使用源语言；海外模式才读取目标语言。")
    prompt = prompt.replace("目标国家", "项目目标国家（国内模式不适用）")
    agent["prompt"] = prompt + route


def project_asset_history_summary(
    conn: sqlite3.Connection,
    project_id: str,
    exclude_episode_id: str | None = None,
) -> list[dict[str, Any]]:
    query = """
        SELECT assets.*, episodes.episode_index
        FROM assets
        LEFT JOIN episodes ON episodes.id = assets.episode_id
        WHERE assets.project_id = ?
    """
    params: list[Any] = [project_id]
    if exclude_episode_id:
        query += " AND (assets.episode_id IS NULL OR assets.episode_id != ?)"
        params.append(exclude_episode_id)
    query += " ORDER BY assets.created_at, assets.asset_type"
    rows = rows_to_dicts(conn.execute(query, params).fetchall())
    summary_by_key: dict[str, dict[str, Any]] = {}
    for row in rows:
        payload = json.loads(row.pop("payload_json"))
        asset = {
            "asset_type": row["asset_type"],
            "name": row["name"],
            "localized_name": row["localized_name"],
            "variable": row["variable"],
            "payload": payload,
        }
        attach_display_variable(asset)
        annotate_project_asset_keys(asset, row.get("episode_index"))
        payload = asset["payload"]
        key = str(payload.get("项目子资产Key") or asset_project_variant_key(asset))
        item = summary_by_key.setdefault(
            key,
            {
                "资产类型": asset["asset_type"],
                "原剧资产名称": asset["name"],
                "本地化资产名称": asset["localized_name"],
                "展示资产变量": asset.get("display_variable", ""),
                "项目主资产Key": payload.get("项目主资产Key", ""),
                "项目子资产Key": key,
                "剧情身份或功能": pick_payload_text(payload, ["剧情身份", "剧情功能", "空间结构描述"]),
                "出现集数": [],
            },
        )
        episode_index = row.get("episode_index")
        if isinstance(episode_index, int) and episode_index not in item["出现集数"]:
            item["出现集数"].append(episode_index)
    for item in summary_by_key.values():
        item["出现集数"] = sorted(item["出现集数"])
    return list(summary_by_key.values())


def pick_payload_text(payload: dict[str, Any], keys: list[str]) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def pick_nested_text(source: dict[str, Any], section_keys: list[str], field_keys: list[str]) -> str:
    for section_key in section_keys:
        section = source.get(section_key)
        if isinstance(section, dict):
            value = pick_payload_text(section, field_keys)
            if value:
                return value
    return ""


def role_prompt_field(payload: dict[str, Any], child: dict[str, Any], section: str, field: str, fallbacks: list[str] | None = None) -> str:
    fallbacks = fallbacks or []
    value = ""
    for source in [child, payload]:
        structure = source.get("角色资产图结构化描述")
        if isinstance(structure, dict):
            section_value = structure.get(section)
            if isinstance(section_value, dict):
                value = pick_payload_text(section_value, [field, *fallbacks])
                if value:
                    break
    if not value:
        value = pick_payload_text(child, [field, *fallbacks]) or pick_payload_text(payload, [field, *fallbacks])
    if value and not is_basketball_uniform_context(role_context_text(payload, child)) and contains_basketball_clothing_terms(value):
        value = ""
    if not usable_visual_value(value):
        value = ""
    return strip_role_nonwearable_items(value or infer_role_prompt_field(payload, child, section, field))


def stable_wear_state_text(value: str) -> str:
    """Render stable wardrobe state without temporary action clauses.

    This is a display-only projection. Source snapshots and localized profiles
    remain unchanged so the original action observation stays auditable.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    dynamic_clause_markers = (
        "后被", "随后", "之后", "然后", "暂时", "短暂", "正在", "动作中",
        "掀开", "掀起", "撩起", "脱下", "脱去", "换上", "换下", "露出腹部",
    )
    clauses = re.split(r"(?<=[，,；;。])|[\r\n]+", text)
    stable = [
        clause.strip(" ，,；;。\t")
        for clause in clauses
        if clause.strip(" ，,；;。\t") and not any(marker in clause for marker in dynamic_clause_markers)
    ]
    return "，".join(stable)


def role_context_text(payload: dict[str, Any], child: dict[str, Any]) -> str:
    identity = pick_payload_text(payload, ["剧情身份", "稳定人设定位", "剧情作用"])
    items = [
        identity,
        pick_payload_text(payload, ["本地化核心形象描述"]),
        pick_payload_text(child, ["本地化视觉形象描述", "本地化子资产描述", "差异标签"]),
        pick_payload_text(child, ["本地化子资产名称"]),
        pick_payload_text(payload, ["本地化角色名称"]),
    ]
    cleaned: list[str] = []
    for item in items:
        if not item:
            continue
        if contains_basketball_clothing_terms(item) and not identity_allows_basketball_uniform(identity):
            continue
        cleaned.append(item)
    return "，".join(cleaned)


def is_female_context(text: str) -> bool:
    lower = text.lower()
    return any(token in text for token in ["女性", "女人", "女主", "母亲", "女孩", "妻子", "姐姐", "妹妹", "女儿"]) or any(token in lower for token in ["female", "woman", "girl", "mother", "wife", "sister", "daughter"])


def is_elder_context(text: str) -> bool:
    lower = text.lower()
    return any(token in text for token in ["老人", "老年", "爷爷", "奶奶", "外公", "外婆", "父亲", "母亲"]) or any(token in lower for token in ["elder", "old", "senior", "grandfather", "grandmother", "father", "mother"])


def identity_allows_basketball_uniform(text: str) -> bool:
    lower = text.lower()
    return any(token in text for token in ["篮球运动员", "篮球球员", "篮球队员", "球员", "运动员"]) or any(token in lower for token in ["basketball player", "athlete"])


def infer_role_prompt_field(payload: dict[str, Any], child: dict[str, Any], section: str, field: str) -> str:
    context = role_context_text(payload, child)
    identity = pick_payload_text(payload, ["剧情身份", "稳定人设定位", "剧情作用"]) or "核心剧情角色"
    if field == "性别":
        if any(token in context for token in ["剧本明确为女性", "明确女性", "女性角色", "女角色", "性别：女", "性别: 女", "称谓明确为女性"]):
            return "女"
        if any(token in context for token in ["剧本明确为男性", "明确男性", "男性角色", "男角色", "性别：男", "性别: 男", "称谓明确为男性"]):
            return "男"
        return "中性角色表现（性别证据不足）"
    if field in {"视觉年龄", "国家 / 时代 / 朝代", "国家 / 时代", "族裔外貌"}:
        return ""
    if field == "身份定位":
        return identity
    if field in {"主色", "副色", "点缀色", "内衬", "中间层", "外层", "领型", "袖型", "腰线", "衣长", "下装", "摆型", "拖尾程度", "摆与地面关系", "鞋子", "鞋面可见性", "脚踝可见性", "面料", "纹样", "纹样位置", "结构与廓形", "工艺与重工程度", "穿着状态"}:
        return ""
    if field in {"头身比", "身高体感", "体型", "肩颈", "腰腿", "整体体态", "脸型", "骨相", "眉形", "眼形", "虹膜颜色", "鼻形", "唇形", "面部毛发", "妆感", "皮肤肤色", "皮肤质感", "气色状态", "发型名称", "发色", "长度", "刘海 / 分线", "发丝质感", "整体状态", "性格关键词", "气质氛围"}:
        return ""
    return ""


def is_basketball_uniform_context(context: str) -> bool:
    lower = context.lower()
    has_basketball_uniform = any(token in context for token in ["篮球球衣", "篮球队服", "比赛服", "球衣号码", "无袖篮球服", "篮球鞋"]) or any(token in lower for token in ["basketball jersey", "basketball uniform", "basketball shoes"])
    has_role_guard = any(token in context for token in ["篮球运动员", "篮球球员", "篮球队员", "球员", "运动员"]) or any(token in lower for token in ["basketball player", "athlete"])
    excluded = any(token in context for token in ["观众", "路人", "家属", "老人", "母亲", "父亲", "老师", "医生", "护士", "经理", "总裁", "女性", "女主"]) or any(token in lower for token in ["audience", "passerby", "family", "elder", "mother", "father", "teacher", "doctor", "nurse", "manager", "ceo"])
    return has_basketball_uniform and has_role_guard and not excluded


def contains_basketball_clothing_terms(text: str) -> bool:
    lower = text.lower()
    return any(
        token in text
        for token in ["篮球背心", "篮球短裤", "篮球队服", "篮球比赛服", "篮球鞋", "球衣号码", "NORTH STATE", "NCAA", "蓝橙配色", "白色球裤"]
    ) or any(token in lower for token in ["basketball uniform", "basketball jersey", "basketball shorts", "basketball shoes", "north state", "ncaa"])


def localized_ethnicity_text(target_country: str, context: str, gender: str = "") -> str:
    country = f"{target_country}".lower()
    if any(token in f"{gender}{context}" for token in ["男女混合", "群像", "观众", "人群", "多名", "多人"]):
        gender_text = "群像人物"
    else:
        gender_text = "女性" if "女" in gender else "男性" if "男" in gender else "人物"
    if country in {"美国", "us", "usa", "united states", "america"} or "美国" in f"{target_country}":
        if any(token in context for token in ["多元族裔", "群像", "观众", "人群", "多人", "多名"]):
            return f"多元族裔美国{gender_text}，年龄、肤色、发色、体型和穿着具有真实人群差异，符合美国本土公共场景，不复用单一角色模板"
        if any(token in context for token in ["非裔", "African", "黑人"]):
            return f"非裔美国{gender_text}，外貌、肤色、发型和气质符合美国本土同年龄、同身份人群特征，不复用原剧地域面部特征"
        if any(token in context for token in ["拉丁", "Latino", "Hispanic"]):
            return f"拉丁裔美国{gender_text}，外貌、肤色、发型和气质符合美国本土同年龄、同身份人群特征，不复用原剧地域面部特征"
        return f"欧洲裔美国白人{gender_text}，五官骨相、肤色、发色和气质符合美国本土同年龄、同身份人群特征，不呈现东亚、东南亚或混血亚洲人的面部特征"
    if any(token in f"{target_country}" for token in ["英国", "英格兰", "UK"]):
        return f"英国本土白人{gender_text}外貌，符合英国同年龄、同身份人群气质，不呈现东亚或原剧地域面部特征"
    if "日本" in f"{target_country}":
        return "日本本土青年男性外貌，五官、发型、肤色和校园运动气质均转写为日本当代语境"
    if "韩国" in f"{target_country}":
        return "韩国本土青年男性外貌，五官、发型、肤色和校园运动气质均转写为韩国当代语境"
    if any(token in f"{target_country}" for token in ["中国", "中文"]):
        return f"中国当代{gender_text}外貌；依据原片可见身份、族裔、肤色和五官特征保持一致，不凭目标国家参数臆造外貌差异"
    return f"{target_country}本土常见族裔{gender_text}外貌，五官骨相、肤色、发色、虹膜颜色和发型均匹配目标国家、年龄和身份，不保留原剧地域面部特征"


def role_clothing_text_constraints(payload: dict[str, Any], child: dict[str, Any], target_language: str) -> str:
    context = role_context_text(payload, child)
    uses_chinese = any(token in target_language for token in ("中文", "汉语", "普通话"))
    no_chinese = "可使用目标语言文字" if uses_chinese else "不得出现中文汉字"
    if not is_basketball_uniform_context(context):
        return f"服装上如有可见文字，必须使用目标语言和目标国家常见排版；{no_chinese}，不得出现真实品牌名称、受版权保护 logo、水印、平台角标或无关标识。"
    number_match = re.search(r"(?:号码|number|#|号)\s*(\d{1,3})|(\d{1,3})\s*号", context, re.IGNORECASE)
    number = next((group for group in (number_match.groups() if number_match else []) if group), "")
    school_text = "目标语言虚构校名"
    number_text = f"可保留原片可见号码“{number}”" if number else "只有原片清楚可见号码时才写号码，不得默认添加固定数字"
    return (
        f"球衣如有可见文字，使用{school_text}并匹配目标语言排版；{number_text}。"
        f"{no_chinese}，不出现真实品牌名称或受版权保护的品牌 logo。除原片必要的球衣文字和号码外，画面中不得出现任何其他文字、水印、平台角标或标识。"
    )


def localized_role_prompt_field(
    payload: dict[str, Any],
    child: dict[str, Any],
    section: str,
    field: str,
    target_country: str,
    fallbacks: list[str] | None = None,
) -> str:
    # Target localization belongs to extraction/detail completion. The image
    # builder only compiles those explicit values and never invents country-
    # specific face or clothing facts from a missing field.
    return role_prompt_field(payload, child, section, field, fallbacks)


def localized_visual_anchor_text(payload: dict[str, Any], child: dict[str, Any], target_country: str) -> str:
    visual = pick_payload_text(child, ["本地化视觉形象描述", "本地化子资产描述"]) or pick_payload_text(payload, ["本地化核心形象描述"])
    return stable_wear_state_text(visual)


def fixed_worn_accessory_observations(payload: dict[str, Any], child: dict[str, Any], structure: dict[str, Any]) -> list[str]:
    clothing = structure.get("服装") if isinstance(structure.get("服装"), dict) else {}
    values: list[str] = []
    for candidate in (child.get("固定穿戴配饰"), clothing.get("固定穿戴配饰"), structure.get("固定穿戴配饰")):
        if isinstance(candidate, list):
            values.extend(str(item).strip() for item in candidate if str(item).strip())
        elif isinstance(candidate, str) and candidate.strip():
            values.append(candidate.strip())
    if values:
        return list(dict.fromkeys(values))
    reviewed = payload.get("sourceFactReview") if isinstance(payload.get("sourceFactReview"), dict) else {}
    if reviewed.get("status") == "reviewed":
        for item in reviewed.get("fixedWornAccessories", []) if isinstance(reviewed.get("fixedWornAccessories"), list) else []:
            if isinstance(item, dict) and str(item.get("value") or "").strip():
                values.append(str(item["value"]).strip())
        if values:
            return list(dict.fromkeys(values))
    source = payload.get("sourceSnapshot") if isinstance(payload.get("sourceSnapshot"), dict) else {}
    source_child = payload.get("当前子资产") if isinstance(payload.get("当前子资产"), dict) else {}
    observations = " ".join((
        str(source.get("coreDescription") or ""),
        str(source_child.get("原剧视觉形象描述") or ""),
        str(source_child.get("原剧核心形象描述") or ""),
    ))
    # Recover only explicit wear/attachment language from the stored source
    # observation. Carrying and handheld objects are intentionally excluded.
    for clause in re.split(r"[，,；;。\n]", observations):
        clause = clause.strip()
        if clause and any(term in clause for term in ("佩戴", "别着", "别在", "系着", "戴着")):
            if not any(term in clause for term in ("手持", "手拿", "携带", "肩扛", "背着")):
                values.append(clause)
    return list(dict.fromkeys(values))


def role_gender_prompt_text(payload: dict[str, Any], child: dict[str, Any]) -> str:
    gender = role_prompt_field(payload, child, "基础设定", "性别")
    if gender in {"男", "男性", "男生", "女", "女性", "女生"}:
        return gender
    return "性别未定，采用中性发型与衣装轮廓，不添加性别专属冠饰或服装结构"


def role_display_constraint(drape_tier: str) -> str:
    if drape_tier == "高位典礼遮鞋":
        return ROLE_COVERED_FOOT_DISPLAY_CONSTRAINT + " 典礼礼袍前、侧、背三面下摆连续触地并完整覆盖脚部；保留完整袍摆画幅，不露鞋尖、鞋面或脚踝，不得开衩或从侧视角露出内层裤装与鞋履。"
    return ROLE_DISPLAY_CONSTRAINT


def role_clothing_tier(payload: dict[str, Any], child: dict[str, Any]) -> str:
    structure = child.get("角色资产图结构化描述") or payload.get("角色资产图结构化描述") or {}
    clothing = structure.get("服装") if isinstance(structure, dict) else {}
    clothing = clothing if isinstance(clothing, dict) else {}
    explicit_tier = str(clothing.get("拖尾程度") or "").strip()
    level = str(clothing.get("服装等级") or "").strip()
    occasion = str(clothing.get("场合") or "").strip()
    normalized_level = re.sub(r"\s+", "", level)
    normalized_occasion = re.sub(r"\s+", "", occasion)
    normalized_tier = re.sub(r"\s+", "", explicit_tier)
    high_formal_level = any(token in normalized_level for token in ("高位礼服", "帝后礼服", "帝王礼服", "皇帝礼服", "皇后礼服", "太后礼服", "典礼级礼服", "宫廷大礼", "重礼服")) and not any(token in normalized_level for token in ("非高位", "非典礼", "日常"))
    formal_occasion = any(token in normalized_occasion for token in ("典礼", "大礼", "册封", "登基", "大婚", "宫廷仪式")) and not any(token in normalized_occasion for token in ("非典礼", "日常", "非仪式"))
    ordinary_occasion = any(token in normalized_occasion for token in ("日常", "非典礼", "非仪式", "便服", "普通场合"))
    asks_high = any(token in normalized_tier for token in ("高位典礼遮鞋", "压地遮鞋", "完全遮鞋"))
    asks_light = any(token in normalized_tier for token in ("轻度拖地", "轻拖地", "轻微拖地", "轻度拖尾"))
    asks_ordinary = any(token in normalized_tier for token in ("普通垂摆", "自然垂摆", "不拖地", "无拖尾", "无拖地"))
    # A high-rank title alone does not determine the hem. Daily/non-ceremonial
    # context and explicit lower tiers take precedence; only two explicit
    # positive signals (garment level + occasion) can select hidden footwear.
    if ordinary_occasion or not formal_occasion:
        if asks_high:
            return ""
        if asks_light:
            return "轻度拖地"
        if asks_ordinary or ordinary_occasion:
            return "普通垂摆"
        return ""
    if high_formal_level and formal_occasion:
        if asks_light or asks_ordinary:
            return ""
        return "高位典礼遮鞋"
    if asks_high:
        return ""
    if asks_light:
        return "轻度拖地"
    if asks_ordinary or ordinary_occasion:
        return "普通垂摆"
    return ""


def role_clothing_tier_conflict(payload: dict[str, Any], child: dict[str, Any]) -> str:
    structure = child.get("角色资产图结构化描述") or payload.get("角色资产图结构化描述") or {}
    clothing = structure.get("服装") if isinstance(structure, dict) else {}
    clothing = clothing if isinstance(clothing, dict) else {}
    level = re.sub(r"\s+", "", str(clothing.get("服装等级") or ""))
    occasion = re.sub(r"\s+", "", str(clothing.get("场合") or ""))
    tier = re.sub(r"\s+", "", str(clothing.get("拖尾程度") or ""))
    high_level = any(token in level for token in ("高位礼服", "帝后礼服", "帝王礼服", "皇帝礼服", "皇后礼服", "太后礼服", "典礼级礼服", "宫廷大礼", "重礼服")) and not any(token in level for token in ("非高位", "非典礼", "日常"))
    formal = any(token in occasion for token in ("典礼", "大礼", "册封", "登基", "大婚", "宫廷仪式")) and not any(token in occasion for token in ("非典礼", "日常", "非仪式"))
    ordinary = any(token in occasion for token in ("日常", "非典礼", "非仪式", "便服", "普通场合"))
    asks_high = any(token in tier for token in ("高位典礼遮鞋", "压地遮鞋", "完全遮鞋"))
    asks_light = any(token in tier for token in ("轻度拖地", "轻拖地", "轻微拖地", "轻度拖尾"))
    asks_ordinary = any(token in tier for token in ("普通垂摆", "自然垂摆", "不拖地", "无拖尾", "无拖地"))
    if asks_high and (ordinary or not formal or not high_level):
        return "拖尾字段要求高位典礼遮鞋，但服装等级和典礼场合未同时明确"
    if formal and high_level and (asks_light or asks_ordinary):
        return "服装等级/典礼场合要求高位典礼遮鞋，但拖尾字段明确为较轻等级"
    return ""


ROLE_NONWEARABLE_ITEM_TERMS = (
    "手持", "手拿", "拿着", "持有", "拿起", "手里", "佩剑", "佩刀", "腰间挂", "腰间悬挂", "腰牌", "玉佩", "香囊", "禁步", "宫绦", "荷包", "扇子", "团扇", "手机", "文件", "包袋", "武器", "兵器", "长剑", "短刀", "宝剑", "刀剑", "腰挂", "令牌", "药瓶", "酒壶", "卷轴", "枪械", "弓箭",
    "fan", "sword", "weapon", "phone", "handbag", "gun", "bow",
)


def strip_role_nonwearable_items(text: str) -> str:
    clauses = re.split(r"([，,、；;。\n]|\s+and\s+|以及|及)", str(text or ""), flags=re.IGNORECASE)
    kept: list[str] = []
    removed_previous_clause = False
    separators = {"，", ",", "、", "；", ";", "。", "\n"}
    for index, clause in enumerate(clauses):
        if clause in separators or re.fullmatch(r"\s+and\s+|以及|及", clause, flags=re.IGNORECASE):
            if removed_previous_clause and re.fullmatch(r"\s+and\s+|以及|及", clause, flags=re.IGNORECASE):
                continue
            kept.append(clause)
            continue
        clause_lower = clause.lower()
        has_forbidden_item = any(term in clause for term in ROLE_NONWEARABLE_ITEM_TERMS if not term.isascii()) or any(term in clause_lower for term in ROLE_NONWEARABLE_ITEM_TERMS if term.isascii())
        has_possession = any(term in clause for term in ("手持", "手拿", "拿着", "拿起", "手里", "持有", "握着", "佩剑", "佩刀", "腰间挂", "腰挂", "腰间悬挂", "悬挂", "挂着", "携带")) or any(term in clause_lower for term in ("handheld", "hand-held", "carrying"))
        is_negative = any(term in clause for term in ("不持", "不拿", "不挂", "不佩戴", "不添加", "无手持", "无腰挂", "无悬挂", "禁止", "不得", "避免", "没有", "不出现"))
        if has_forbidden_item and has_possession and not is_negative:
            removed_previous_clause = True
            continue
        kept.append(clause)
        removed_previous_clause = False
    return "".join(kept).strip(" ，,、；;。\n")


def visibility_state_is_exposed(value: Any) -> bool:
    text = re.sub(r"\s+", "", str(value or ""))
    if not text:
        return False
    negative_states = ("不可见", "不外露", "不露出", "不露鞋面", "不露脚踝", "不要露出", "不要显示", "避免露出", "完全遮挡", "完全覆盖", "被遮挡", "无外露", "未露出", "不显示", "隐藏")
    remaining = text
    for state in negative_states:
        remaining = remaining.replace(state, "")
    return any(token in remaining for token in ("清晰可见", "可见", "露出", "外露", "微露", "露鞋", "显露"))


def asset_prompt_validation_errors(asset_type: str, payload: dict[str, Any], project: dict[str, Any], prompt: str) -> list[str]:
    errors: list[str] = []
    if str(project.get("target_country") or "").strip() not in {"", "中国", "中国大陆"}:
        profile = payload.get("localizedProfile") if isinstance(payload.get("localizedProfile"), dict) else {}
        if profile.get("status") != "complete":
            errors.append("目标国家资产缺少可验证的localizedProfile；旧共享结构不会作为目标画像回退")
        elif not localized_profile_context_matches(payload, project):
            errors.append("目标本地化画像的国家、时代、语言或源快照已变化；请重新执行目标本地化")
    child = payload.get("当前子资产") if isinstance(payload.get("当前子资产"), dict) else {}
    if unresolved_asset_reference(prompt):
        errors.append("最终提示词仍含需要外部原片/参考图的未解析指代，请先补全独立视觉描述")
    target_country, target_language, style = asset_prompt_context(project)
    if style == "cg_3d" and any(token in prompt for token in ("避免卡通、绘画和三维渲染", "真人写实摄影质感", "照片级真实")):
        errors.append("目标视觉风格冲突：CG提示词含真人摄影或禁3D约束")
    if style != "cg_3d" and any(token in prompt for token in ("Unreal Engine 5", "Zbrush digital sculpt", "Octane render")):
        errors.append("目标视觉风格冲突：真人提示词含CG固定风格约束")
    if asset_type == "角色":
        structure = child.get("角色资产图结构化描述") or payload.get("角色资产图结构化描述") or {}
        missing = role_structured_missing_paths(structure)
        if missing:
            errors.append("角色结构化视觉字段待补全：" + "、".join(missing))
        gender = ""
        sections = structure if isinstance(structure, dict) else {}
        basics = sections.get("基础设定") if isinstance(sections.get("基础设定"), dict) else {}
        gender = str(basics.get("性别") or "")
        clothing = sections.get("服装") if isinstance(sections.get("服装"), dict) else {}
        clothing_text = " ".join(str(value) for value in clothing.values() if isinstance(value, str))
        if gender in {"男", "男性", "男生"} and any(token in clothing_text for token in ("凤冠", "霞帔", "襦裙", "马面裙", "女帝高髻", "女装拖尾礼裙")):
            errors.append("男性角色服装字段含女性专属服装词")
        if gender in {"女", "女性", "女生"} and any(token in clothing_text for token in ("冕冠", "翼善冠", "乌纱帽", "幞头", "男性蟒袍", "男性朝靴")):
            errors.append("女性角色服装字段含男性专属服装词")
        if role_clothing_tier_conflict(payload, child):
            errors.append("服装等级/场合/拖尾级别冲突：请修正“服装等级、场合、拖尾程度”字段")
        if role_clothing_tier(payload, child) == "高位典礼遮鞋":
            target_override = str(payload.get("目标生成描述") or "")
            if any(visibility_state_is_exposed(clothing.get(key)) for key in ("鞋面可见性", "脚踝可见性")):
                errors.append("高位典礼服装要求遮鞋，但鞋面或脚踝字段标记为可见")
            elif visibility_state_is_exposed(target_override):
                errors.append("高位典礼服装要求遮鞋，但用户目标外观补充要求露出鞋面或脚踝")
    elif asset_type == "场景":
        visual = payload.get("本地化场景描述") or child.get("本地化视觉描述") or payload.get("空间结构描述") or payload.get("目标生成描述")
        if not usable_visual_value(visual):
            errors.append("场景自包含描述缺失：需明确空间结构、入口/窗户/道路、主要设施、材质与光线")
    elif asset_type == "道具":
        visual = payload.get("本地化道具描述") or payload.get("目标生成描述")
        if not usable_visual_value(visual):
            errors.append("道具自包含描述缺失：需明确物件类别、形状比例、材质、颜色与状态")
    return errors


def build_role_asset_image_prompt(
    payload: dict[str, Any],
    target_country: str = "目标国家",
    target_language: str = "目标语言",
    visual_style: str = "live_action",
    visual_override: str = "",
) -> str:
    child = payload.get("当前子资产") if isinstance(payload.get("当前子资产"), dict) else {}
    localized_visual = localized_visual_anchor_text(payload, child, target_country)
    identity = pick_payload_text(payload, ["剧情身份", "稳定人设定位", "剧情作用"])
    localized_name = pick_payload_text(payload, ["本地化角色名称"]) or pick_payload_text(child, ["本地化子资产名称"])
    context = role_context_text(payload, child)
    structure = child.get("角色资产图结构化描述") or payload.get("角色资产图结构化描述") or {}
    structure = structure if isinstance(structure, dict) else {}
    clothing = structure.get("服装") if isinstance(structure.get("服装"), dict) else {}
    era = role_prompt_field(payload, child, "基础设定", "国家 / 时代 / 朝代")
    fixed_accessories = fixed_worn_accessory_observations(payload, child, structure)
    if isinstance(fixed_accessories, str):
        fixed_accessories = [fixed_accessories]
    fixed_accessories_text = "、".join(strip_role_nonwearable_items(str(item).strip()) for item in fixed_accessories if str(item).strip()) if isinstance(fixed_accessories, list) else ""
    if not fixed_accessories_text:
        fixed_accessories_text = "无明确固定穿戴配饰"
    profile = payload.get("localizedProfile") if isinstance(payload.get("localizedProfile"), dict) else {}
    unobservable_paths = profile.get("nonBlockingSourceReviewPaths") if isinstance(profile.get("nonBlockingSourceReviewPaths"), list) else []
    unobservable_clothing = [path for path in unobservable_paths if path in UNOBSERVABLE_LOWER_BODY_PATHS]
    unobservable_clothing_block = (
        "\n\n【源画面未观察到的下半身细节】\n"
        f"原片下半身细节未显示或被遮挡，未确认字段：{'、'.join(unobservable_clothing)}。这些内容继续保持未知，不得把推测写成原片事实；完整角色展示只做与已确认服装颜色和场合相协调的低强调、中性延展，不突出具体裙长、摆型、拖尾、裙摆落地关系或鞋型。"
        if unobservable_clothing else ""
    )
    drape_tier = role_clothing_tier(payload, child) or "按已记录的摆型、拖尾程度与地面关系呈现，不额外增加拖尾"
    covered_foot_ceremony = drape_tier == "高位典礼遮鞋"
    garment_length_text = "外袍下摆连续垂至地面并覆盖全部脚部" if covered_foot_ceremony else localized_role_prompt_field(payload, child, "服装", "衣长", target_country)
    lower_garment_text = "内层裤装与鞋履完整隐藏于袍摆内，不从开衩或侧视角露出" if covered_foot_ceremony else localized_role_prompt_field(payload, child, "服装", "下装", target_country)
    hem_relation_text = localized_role_prompt_field(payload, child, "服装", "摆与地面关系", target_country)
    hidden_by_explicit_hem = footwear_hidden_by_garment(structure)
    if covered_foot_ceremony:
        hem_relation_text = "前、侧、背外袍下摆连续触地，完整覆盖鞋履与脚踝；保留完整袍摆画幅，不露鞋尖或鞋面。" + hem_relation_text
    elif hidden_by_explicit_hem:
        hem_relation_text = "已确认长裙衣摆遮住鞋面与脚踝；资产图不展示未知鞋型。" + hem_relation_text
    shoe_text = "鞋履被已确认长裙衣摆遮挡，源鞋款未知，不展示鞋型、鞋面或鞋尖" if hidden_by_explicit_hem else "鞋履仅作衣摆内的穿着事实，不呈现鞋型、鞋面或鞋尖" if covered_foot_ceremony else localized_role_prompt_field(payload, child, "服装", "鞋子", target_country)
    shoe_visibility_text = "完全被袍摆遮挡，不露鞋面或鞋尖" if covered_foot_ceremony else localized_role_prompt_field(payload, child, "服装", "鞋面可见性", target_country)
    ankle_visibility_text = "完全被袍摆遮挡，不露脚踝" if covered_foot_ceremony else localized_role_prompt_field(payload, child, "服装", "脚踝可见性", target_country)
    display_constraint = role_display_constraint(drape_tier)
    style_constraint = CG_ROLE_FIXED_TEMPLATE if visual_style == "cg_3d" else ROLE_LIVE_STYLE
    override = strip_role_nonwearable_items(clean_target_asset_description(visual_override, visual_style))
    override_block = (
        f"\n\n【用户指定目标外观覆盖】\n{override}\n"
        "优先于同字段的自动目标视觉描述；不得改变来源事实、剧情身份、共同展示构图或目标视觉风格。"
        if override else ""
    )
    return f"""【基础设定】
性别：{role_gender_prompt_text(payload, child)}
视觉年龄：{role_prompt_field(payload, child, "基础设定", "视觉年龄")}
国家 / 时代：{target_country} / {era}
族裔外貌：{role_prompt_field(payload, child, "基础设定", "族裔外貌")}
身份定位：{role_prompt_field(payload, child, "基础设定", "身份定位", ["剧情身份", "稳定人设定位", "剧情作用"])}
角色名称：{localized_name or "未命名本地化角色"}
目标语言：{target_language}
本地化视觉锚点：{strip_role_nonwearable_items(localized_visual)}

【身材体态】
头身比：{role_prompt_field(payload, child, "身材体态", "头身比")}
身高体感：{role_prompt_field(payload, child, "身材体态", "身高体感")}
体型：{role_prompt_field(payload, child, "身材体态", "体型", ["本地化视觉形象描述", "本地化子资产描述"])}
肩颈：{role_prompt_field(payload, child, "身材体态", "肩颈")}
腰腿：{role_prompt_field(payload, child, "身材体态", "腰腿")}
整体体态：{role_prompt_field(payload, child, "身材体态", "整体体态", ["本地化视觉形象描述"])}

【脸部特征】
脸型：{localized_role_prompt_field(payload, child, "脸部特征", "脸型", target_country)}
骨相：{localized_role_prompt_field(payload, child, "脸部特征", "骨相", target_country)}
眉形：{localized_role_prompt_field(payload, child, "脸部特征", "眉形", target_country)}
眼形：{localized_role_prompt_field(payload, child, "脸部特征", "眼形", target_country)}
虹膜颜色：{localized_role_prompt_field(payload, child, "脸部特征", "虹膜颜色", target_country)}
鼻形：{localized_role_prompt_field(payload, child, "脸部特征", "鼻形", target_country)}
唇形：{localized_role_prompt_field(payload, child, "脸部特征", "唇形", target_country)}
面部毛发：{localized_role_prompt_field(payload, child, "脸部特征", "面部毛发", target_country)}
妆感：{role_prompt_field(payload, child, "脸部特征", "妆感")}
皮肤肤色：{localized_role_prompt_field(payload, child, "脸部特征", "皮肤肤色", target_country)}
皮肤质感：{role_prompt_field(payload, child, "脸部特征", "皮肤质感")}
气色状态：{role_prompt_field(payload, child, "脸部特征", "气色状态")}

【发型】
发型名称：{localized_role_prompt_field(payload, child, "发型", "发型名称", target_country)}
发色：{localized_role_prompt_field(payload, child, "发型", "发色", target_country)}
长度：{localized_role_prompt_field(payload, child, "发型", "长度", target_country)}
刘海 / 分线：{localized_role_prompt_field(payload, child, "发型", "刘海 / 分线", target_country, ["刘海 /分线", "刘海", "分线"])}
发丝质感：{localized_role_prompt_field(payload, child, "发型", "发丝质感", target_country)}
整体状态：{localized_role_prompt_field(payload, child, "发型", "整体状态", target_country)}

【服装】
服装等级：{role_prompt_field(payload, child, "服装", "服装等级")}
场合：{role_prompt_field(payload, child, "服装", "场合")}
主色：{localized_role_prompt_field(payload, child, "服装", "主色", target_country)}
副色：{localized_role_prompt_field(payload, child, "服装", "副色", target_country)}
点缀色：{localized_role_prompt_field(payload, child, "服装", "点缀色", target_country)}
内衬：{localized_role_prompt_field(payload, child, "服装", "内衬", target_country)}
中间层：{localized_role_prompt_field(payload, child, "服装", "中间层", target_country)}
外层：{localized_role_prompt_field(payload, child, "服装", "外层", target_country)}
领型：{localized_role_prompt_field(payload, child, "服装", "领型", target_country)}
袖型：{localized_role_prompt_field(payload, child, "服装", "袖型", target_country)}
腰线：{localized_role_prompt_field(payload, child, "服装", "腰线", target_country)}
衣长：{garment_length_text}
下装：{lower_garment_text}
摆型：{localized_role_prompt_field(payload, child, "服装", "摆型", target_country)}
拖尾程度：{localized_role_prompt_field(payload, child, "服装", "拖尾程度", target_country)}
摆与地面关系：{hem_relation_text}
鞋子：{shoe_text}
鞋面可见性：{shoe_visibility_text}
脚踝可见性：{ankle_visibility_text}
面料：{localized_role_prompt_field(payload, child, "服装", "面料", target_country)}
纹样：{localized_role_prompt_field(payload, child, "服装", "纹样", target_country)}
纹样位置：{localized_role_prompt_field(payload, child, "服装", "纹样位置", target_country)}
结构与廓形：{localized_role_prompt_field(payload, child, "服装", "结构与廓形", target_country)}
工艺与重工程度：{localized_role_prompt_field(payload, child, "服装", "工艺与重工程度", target_country)}
穿着状态：{stable_wear_state_text(localized_role_prompt_field(payload, child, "服装", "穿着状态", target_country))}
拖尾分级：{drape_tier}
固定穿戴配饰：{fixed_accessories_text}。不添加手持物、武器或腰间悬挂物；此人物服装与发型只采用当前子资产这一套主视觉。

【服装文字约束】
{role_clothing_text_constraints(payload, child, target_language)}
{unobservable_clothing_block}

{override_block}

【共同展示构图】
{display_constraint}

【目标视觉风格】
{style_constraint}"""


def significant_asset_text(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def compact_asset_token_part(value: str) -> str:
    parts = re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]+", value or "")
    if not parts:
        return ""
    compacted: list[str] = []
    for part in parts:
        if re.fullmatch(r"[A-Za-z0-9]+", part):
            compacted.append(part[:1].upper() + part[1:])
        else:
            compacted.append(part)
    return "".join(compacted)


def remove_duplicate_asset_prefix(base: str, variant: str) -> str:
    base_token = compact_asset_token_part(base)
    variant_token = compact_asset_token_part(variant)
    if base_token and variant_token.lower().startswith(base_token.lower()):
        return variant_token[len(base_token):]
    return variant


def make_display_variable(asset: dict[str, Any]) -> str:
    payload = asset.get("payload") if isinstance(asset.get("payload"), dict) else {}
    child = payload.get("当前子资产") if isinstance(payload.get("当前子资产"), dict) else {}
    asset_type = str(asset.get("asset_type") or "")
    localized_name = str(asset.get("localized_name") or "").strip()
    if localized_name.lower().startswith("unnamed"):
        localized_name = ""
    base_source = localized_name or str(asset.get("name") or payload.get("sourceSnapshot", {}).get("name") or "")
    base = compact_asset_token_part(base_source)
    variant_source = pick_payload_text(child, ["差异标签", "本地化子资产名称", "原剧子资产名称"])
    variant = compact_asset_token_part(remove_duplicate_asset_prefix(str(asset.get("localized_name") or asset.get("name") or ""), variant_source))
    if asset_type in {"角色", "场景"} and variant and variant != base:
        token = f"{base}-{variant}" if base else variant
    else:
        token = base
    fallback = str(asset.get("variable") or asset.get("id") or "Asset").replace("{", "").replace("}", "")
    if not token:
        token = compact_asset_token_part(fallback) or "Asset"
    return "@" + token[:64]


def attach_display_variable(asset: dict[str, Any]) -> dict[str, Any]:
    payload = asset.get("payload") if isinstance(asset.get("payload"), dict) else {}
    display_variable = payload.get("展示资产变量") if isinstance(payload.get("展示资产变量"), str) else ""
    if not display_variable or "_" in display_variable or len(display_variable) > 48 or display_variable.lower().startswith("@unnamed"):
        if display_variable and display_variable != make_display_variable(asset):
            payload.setdefault("旧展示资产变量", display_variable)
        display_variable = make_display_variable(asset)
    asset["display_variable"] = display_variable
    payload["展示资产变量"] = display_variable
    asset["payload"] = payload
    return asset


# 归并主键规范化白名单：只含机位噪音（纯拍摄方式描述，不构成本体身份）。
# 状态变体（常服/战斗服/夜景/受损/带血/带汗等）与功能词（计时牌/广告屏等）、
# 颜色材质、位置词（场边/远端/看台等）一律不在名单内——宁可错拆，不可错并。
ASSET_MAIN_KEY_CAMERA_TOKENS = (
    "近景特写",
    "大全景",
    "低角度俯拍",
    "高角度俯拍",
    "低角度",
    "高角度",
    "全景",
    "近景",
    "远景",
    "中景",
    "特写",
    "俯拍",
    "仰拍",
)
# 场景空间分区后缀：同一物理场馆的不同机位区域（替补席/场边/看台等）应归并到
# 同一主资产场馆，分区信息由子资产差异维度承载。
ASSET_MAIN_KEY_SCENE_ZONE_SUFFIXES = (
    "场边/替补席区域",
    "场边替补席区域",
    "替补席区域",
    "场边区域",
    "看台区域",
    "顶棚区域",
    "悬挂区域",
    "休息区域",
    "通道区域",
)
# 括号内纯机位噪音（如“电子记分牌（近景特写）”）可整体剥离；括号内容只要含
# 非机位词（位置/状态/功能）就整体保留。
ASSET_MAIN_KEY_SEPARATOR_CHARS = "、，,/ "


def _strip_trailing_camera_parenthetical(text: str) -> tuple[str, list[str]]:
    """剥离尾部括号内的纯机位噪音修饰；返回（剩余文本, 剥离记录）。"""
    match = re.search(r"[（(]([^（）()]*)[）)]\s*$", text)
    if not match:
        return text, []
    inner = match.group(1).strip()
    tokens = [part.strip() for part in re.split(r"[、，,/]", inner) if part.strip()]
    camera_set = set(ASSET_MAIN_KEY_CAMERA_TOKENS)
    if tokens and all(token in camera_set for token in tokens):
        return text[: match.start()].strip(), [f"（{inner}）"]
    return text, []


def normalize_asset_main_key_text(asset_type: str, text: str) -> tuple[str, list[str]]:
    """规范化归并主键的身份文本：剥离封闭白名单内的机位噪音（全类型，含尾部
    纯机位括号注释），场景资产额外剥离空间分区后缀。返回（规范化文本, 剥离修饰列表）。
    剥离结果为空时回退原文并丢弃全部剥离记录，保证不因过度剥离而错并。"""
    result = text.strip()
    if not result:
        return text, []
    stripped: list[str] = []
    result, parenthetical_stripped = _strip_trailing_camera_parenthetical(result)
    stripped.extend(parenthetical_stripped)
    changed = True
    while changed:
        changed = False
        for token in sorted(ASSET_MAIN_KEY_CAMERA_TOKENS, key=len, reverse=True):
            if result.startswith(token) and len(result) > len(token) + 1:
                result = result[len(token):].strip(ASSET_MAIN_KEY_SEPARATOR_CHARS)
                stripped.append(token)
                changed = True
            if result.endswith(token) and len(result) > len(token) + 1:
                result = result[:-len(token)].strip(ASSET_MAIN_KEY_SEPARATOR_CHARS)
                stripped.append(token)
                changed = True
    if asset_type == "场景":
        changed = True
        while changed:
            changed = False
            for zone in sorted(ASSET_MAIN_KEY_SCENE_ZONE_SUFFIXES, key=len, reverse=True):
                if result.endswith(zone) and len(result) > len(zone) + 1:
                    result = result[:-len(zone)].strip(ASSET_MAIN_KEY_SEPARATOR_CHARS)
                    stripped.append(zone)
                    changed = True
    if not result:
        return text, []
    return result, stripped


def asset_project_main_key(asset: dict[str, Any]) -> str:
    payload = asset.get("payload") if isinstance(asset.get("payload"), dict) else {}
    child = payload.get("当前子资产") if isinstance(payload.get("当前子资产"), dict) else {}
    asset_type = str(asset.get("asset_type") or "")
    source_name = str(asset.get("name") or "").strip()
    if asset_type == "角色":
        identity_fields = ["原剧角色名称", "剧情身份", "稳定人设定位"]
    elif asset_type == "场景":
        identity_fields = ["原剧场景名称", "空间结构描述"]
    else:
        identity_fields = ["原剧道具名称", "剧情功能"]
    # 逐字段剥离机位噪音/场景分区后缀；描述性字段（剧情身份/功能/空间结构）保持原样。
    normalized_name, stripped = normalize_asset_main_key_text(asset_type, source_name)
    normalized_identity, identity_stripped = normalize_asset_main_key_text(
        asset_type, pick_payload_text(payload, identity_fields[:1])
    ) if identity_fields else ("", [])
    stripped.extend(identity_stripped)
    source = " ".join(dict.fromkeys(
        value
        for value in [normalized_name, normalized_identity or pick_payload_text(payload, identity_fields)]
        if value
    ))
    if stripped:
        payload["归并规范化"] = {"原始身份文本": " ".join(dict.fromkeys(v for v in [source_name, pick_payload_text(payload, identity_fields)] if v)), "规范化身份文本": source, "剥离修饰": stripped}
        asset["payload"] = payload
    compacted = significant_asset_text(source) or significant_asset_text(str(asset.get("variable") or asset.get("id") or "asset"))
    return f"{asset_type}:{compacted[:96]}"


def asset_project_variant_key(asset: dict[str, Any]) -> str:
    payload = asset.get("payload") if isinstance(asset.get("payload"), dict) else {}
    child = payload.get("当前子资产") if isinstance(payload.get("当前子资产"), dict) else {}
    variant = significant_asset_text(
        " ".join(
            value
            for value in [
                pick_payload_text(child, ["差异类型", "差异标签", "原剧子资产名称", "状态差异"]),
                pick_payload_text(payload, ["原剧道具名称"]),
            ]
            if value
        )
    )
    return f"{asset_project_main_key(asset)}:{variant[:96]}"


def annotate_project_asset_keys(asset: dict[str, Any], episode_index: int | None = None) -> dict[str, Any]:
    payload = asset.get("payload") if isinstance(asset.get("payload"), dict) else {}
    payload["项目主资产Key"] = asset_project_main_key(asset)
    payload["项目子资产Key"] = asset_project_variant_key(asset)
    if episode_index is not None:
        appearances = payload.get("出现集数") if isinstance(payload.get("出现集数"), list) else []
        if episode_index not in appearances:
            appearances.append(episode_index)
        payload["出现集数"] = sorted(appearances)
    asset["payload"] = payload
    return asset


def ensure_unique_asset_display_variables(assets: list[dict[str, Any]]) -> None:
    for asset in assets:
        attach_display_variable(asset)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for asset in assets:
        grouped.setdefault(str(asset.get("display_variable") or ""), []).append(asset)
    for display, matches in grouped.items():
        if len(matches) < 2:
            continue
        for asset in matches:
            payload = asset.get("payload") if isinstance(asset.get("payload"), dict) else {}
            stable_suffix = compact_asset_token_part(str(asset.get("variable") or asset.get("id") or "Asset")) or "Asset"
            unique_display = f"{display[:max(1, 62 - len(stable_suffix))]}-{stable_suffix}"
            if display and payload.get("旧展示资产变量") != display:
                payload["旧展示资产变量"] = display
            payload["展示资产变量"] = unique_display
            asset["display_variable"] = unique_display
            asset["payload"] = payload


def variable_maps(assets: list[dict[str, Any]]) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    ensure_unique_asset_display_variables(assets)
    old_to_display: dict[str, str] = {}
    by_any_variable: dict[str, dict[str, Any]] = {}
    alias_candidates: dict[str, list[dict[str, Any]]] = {}
    for asset in assets:
        old_variable = str(asset.get("variable") or "")
        display_variable = str(asset.get("display_variable") or "")
        if old_variable and display_variable:
            old_to_display[old_variable] = display_variable
            by_any_variable[old_variable] = asset
        if display_variable:
            by_any_variable[display_variable] = asset
        payload = asset.get("payload") if isinstance(asset.get("payload"), dict) else {}
        for alias in (payload.get("旧展示资产变量"), asset.get("source_variable_alias"), payload.get("源变量别名")):
            alias_text = str(alias or "").strip()
            if alias_text and alias_text not in {old_variable, display_variable}:
                alias_candidates.setdefault(alias_text, []).append(asset)
    for alias, candidates in alias_candidates.items():
        unique_candidates = {str(item.get("id") or item.get("variable") or id(item)): item for item in candidates}
        if len(unique_candidates) == 1:
            asset = next(iter(unique_candidates.values()))
            old_to_display[alias] = str(asset.get("display_variable") or "")
            by_any_variable[alias] = asset
    return old_to_display, by_any_variable


def convert_asset_variables(text: str, old_to_display: dict[str, str]) -> str:
    converted = text
    for old, display in sorted(old_to_display.items(), key=lambda item: len(item[0]), reverse=True):
        converted = converted.replace(old, display)
    return converted


def convert_storyboard_asset_variables(value: Any, old_to_display: dict[str, str]) -> Any:
    if isinstance(value, str):
        return convert_asset_variables(value, old_to_display)
    if isinstance(value, list):
        return [convert_storyboard_asset_variables(item, old_to_display) for item in value]
    if isinstance(value, dict):
        return {key: convert_storyboard_asset_variables(item, old_to_display) for key, item in value.items()}
    return value


def split_variable_text(value: Any) -> list[str]:
    if not isinstance(value, str):
        return []
    text = value.strip()
    if not text:
        return []
    return [item for item in re.split(r"[\s,，、；;]+", text) if item]


def deep_merge_dict(target: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    for key, value in patch.items():
        if value in (None, ""):
            continue
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            deep_merge_dict(target[key], value)
        else:
            target[key] = value
    return target


def merge_records(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key in ["出现记录"]:
        target_records = target.get(key)
        source_records = source.get(key)
        if isinstance(target_records, list) and isinstance(source_records, list):
            target_records.extend(source_records)


def child_is_significant_duplicate(existing_child: dict[str, Any], child: dict[str, Any]) -> bool:
    existing_id = pick_payload_text(existing_child, ["子资产编号", "角色资产变量", "场景资产变量", "道具资产变量"])
    current_id = pick_payload_text(child, ["子资产编号", "角色资产变量", "场景资产变量", "道具资产变量"])
    if existing_id and current_id and existing_id == current_id:
        return True
    key_fields = ["本地化视觉形象描述", "原剧视觉形象描述", "差异标签"]
    existing_key = significant_asset_text(" ".join(pick_payload_text(existing_child, [field]) for field in key_fields))
    current_key = significant_asset_text(" ".join(pick_payload_text(child, [field]) for field in key_fields))
    return bool(existing_key and current_key and existing_key == current_key)


def compact_role_children(role: dict[str, Any]) -> list[dict[str, Any]]:
    raw_children = role.get("子资产") if isinstance(role.get("子资产"), list) else []
    children = [child for child in raw_children if isinstance(child, dict)] or [{}]
    compacted: list[dict[str, Any]] = []
    main_id = role.get("主资产编号") or "角色_001"
    for child in children:
        duplicate = next((item for item in compacted if child_is_significant_duplicate(item, child)), None)
        if duplicate:
            merge_records(duplicate, child)
            continue
        next_index = len(compacted) + 1
        child = dict(child)
        child.setdefault("子资产编号", f"{main_id}_子资产_{next_index:03d}")
        child.setdefault("角色资产变量", f"{{{{{child['子资产编号']}}}}}")
        compacted.append(child)
    return compacted


def build_non_role_asset_image_prompt(
    asset_type: str,
    payload: dict[str, Any],
    target_country: str,
    target_language: str,
    visual_style: str = "live_action",
    visual_override: str = "",
) -> str:
    if asset_type == "场景" or "本地化场景名称" in payload:
        name = pick_payload_text(payload, ["本地化场景名称", "本地化子资产名称"]) or "本地化场景"
        visual = pick_payload_text(payload, ["本地化场景描述", "本地化视觉描述", "空间结构描述"])
        child = payload.get("当前子资产") if isinstance(payload.get("当前子资产"), dict) else {}
        child_visual = pick_payload_text(child, ["本地化视觉描述", "本地化子资产名称", "状态差异"])
        panorama_hint = (
            f"{target_country}本地化{name}场景全景设定。"
            "仅按下方已经明确的目标空间描述绘制；完整展示描述中列出的空间布局、入口、窗户、建筑/道路、家具设施、墙地天花材质、光线和主要物件位置。"
            "只生成稳定可复用的场景全景资产，不生成正面/侧面/三视图，不生成局部特写，不出现人物。"
        )
        visual = "；".join(part for part in [visual, child_visual, panorama_hint] if part)
        style_constraint = CG_SCENE_FIXED_TEMPLATE if visual_style == "cg_3d" else SCENE_LIVE_STYLE
        display_constraint = SCENE_DISPLAY_CONSTRAINT
    else:
        name = pick_payload_text(payload, ["本地化道具名称", "本地化子资产名称"]) or "本地化道具"
        visual = pick_payload_text(payload, ["本地化道具描述", "本地化视觉描述", "道具资产图生成提示词"])
        style_constraint = CG_PROP_FIXED_TEMPLATE if visual_style == "cg_3d" else PROP_LIVE_STYLE
        display_constraint = PROP_DISPLAY_CONSTRAINT
    override = clean_target_asset_description(visual_override, visual_style)
    override_line = (
        f"\n用户指定目标外观覆盖（优先于同字段自动目标视觉描述；保留来源事实、共同展示构图与目标视觉风格）：{override}"
        if override else ""
    )
    return f"""【目标国家本地化资产图】
资产类型：{asset_type}
资产名称：{name}
目标国家：{target_country}
目标语言：{target_language}
本地化视觉锚点：{visual}{override_line}

【共同展示构图】
{display_constraint}

【目标视觉风格】
{style_constraint}"""


def infer_asset_image_prompt(
    asset_type: str,
    payload: dict[str, Any],
    target_country: str = "目标国家",
    target_language: str = "目标语言",
    visual_style: str = "live_action",
    visual_override: str = "",
) -> str:
    if asset_type == "角色" or "原剧角色名称" in payload or "本地化角色名称" in payload:
        return build_role_asset_image_prompt(payload, target_country, target_language, visual_style, visual_override)
    if asset_type == "场景" or "原剧场景名称" in payload or "本地化场景名称" in payload:
        return build_non_role_asset_image_prompt(asset_type, payload, target_country, target_language, visual_style, visual_override)
    if asset_type == "道具" or "原剧道具名称" in payload or "本地化道具名称" in payload:
        return build_non_role_asset_image_prompt(asset_type, payload, target_country, target_language, visual_style, visual_override)
    return ""


def render_mock_asset_svg(asset: dict[str, Any], prompt: str) -> str:
    title = f"{asset['asset_type']} - {asset['localized_name']}"
    safe_title = (
        title.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    safe_prompt = (
        prompt[:160]
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="800" viewBox="0 0 1200 800">
  <rect width="1200" height="800" fill="#f8fafc"/>
  <rect x="80" y="80" width="1040" height="640" rx="28" fill="#ffffff" stroke="#cfd7e6" stroke-width="4"/>
  <circle cx="360" cy="320" r="120" fill="#dbeafe"/>
  <rect x="260" y="450" width="200" height="180" rx="60" fill="#bfdbfe"/>
  <rect x="620" y="170" width="300" height="44" rx="8" fill="#eef2ff"/>
  <rect x="620" y="250" width="420" height="26" rx="6" fill="#e2e8f0"/>
  <rect x="620" y="298" width="360" height="26" rx="6" fill="#e2e8f0"/>
  <rect x="620" y="346" width="390" height="26" rx="6" fill="#e2e8f0"/>
  <text x="620" y="202" font-family="Arial, sans-serif" font-size="30" font-weight="700" fill="#1f2937">{safe_title}</text>
  <text x="620" y="450" font-family="Arial, sans-serif" font-size="20" fill="#475569">Mock asset image placeholder</text>
  <foreignObject x="620" y="480" width="430" height="150">
    <div xmlns="http://www.w3.org/1999/xhtml" style="font-family:Arial,sans-serif;font-size:18px;line-height:1.5;color:#64748b;">{safe_prompt}</div>
  </foreignObject>
</svg>"""


def record_agent_run(
    conn: sqlite3.Connection,
    task_id: str,
    project_id: str,
    episode_id: str | None,
    agent_name: str,
    agent: dict[str, Any],
    input_payload: dict[str, Any],
    output_payload: dict[str, Any],
) -> None:
    conn.execute(
        """
        INSERT INTO agent_runs
        (id, task_id, project_id, episode_id, agent_name, model_mode, model_name, prompt_file, input_json, output_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            new_id("agent_run"),
            task_id,
            project_id,
            episode_id,
            agent_name,
            agent.get("mode", "mock"),
            agent.get("model_name") or agent.get("model", ""),
            agent.get("prompt_file", ""),
            json.dumps(input_payload, ensure_ascii=False),
            json.dumps(output_payload, ensure_ascii=False),
            now_iso(),
        ),
    )


class ProjectCreate(BaseModel):
    title: str = "未命名短剧重制项目"
    targetCountry: str | None = None
    targetLanguage: str | None = None
    remakeMode: str = "overseas_localization"
    sourceLanguage: str = ""
    storyboardVisualStyle: str | None = None
    replaceCharacters: bool = False
    replaceScenes: bool = False


class ProjectUpdate(BaseModel):
    title: str | None = None
    remakeMode: str | None = None
    sourceLanguage: str | None = None
    storyboardVisualStyle: str | None = None
    replaceCharacters: bool | None = None
    replaceScenes: bool | None = None
    targetCountry: str | None = None
    targetLanguage: str | None = None


class ProjectDeriveRequest(ProjectCreate):
    pass


class RunTaskRequest(BaseModel):
    taskType: str
    episodeId: str | None = None
    storyboardId: str | None = None
    onlyUnprocessed: bool = False
    force: bool = False


class StoryboardUpdate(BaseModel):
    title: str | None = None
    timeRange: str | None = None
    prompt: str | None = None
    generatedBody: str | None = None
    payload: dict[str, Any] | None = None


class StoryboardCreate(BaseModel):
    episodeId: str
    title: str | None = None
    timeRange: str | None = None
    prompt: str | None = None
    duplicateFromId: str | None = None


class StoryboardMoveRequest(BaseModel):
    direction: str


class EpisodeSourceUpdate(BaseModel):
    sourceUrl: str


class AssetUpdate(BaseModel):
    name: str | None = None
    localizedName: str | None = None
    status: str | None = None
    payload: dict[str, Any] | None = None


class AssetBatchDeleteRequest(BaseModel):
    assetIds: list[str]


class AssetCreateRequest(BaseModel):
    assetType: str
    name: str | None = None
    localizedName: str | None = None


class AssetGroupCreate(BaseModel):
    name: str
    memberAssetIds: list[str]


class AssetGroupUpdate(BaseModel):
    name: str | None = None
    memberAssetIds: list[str] | None = None


class AssetUploadRequest(BaseModel):
    pass


class AssetImageRequest(BaseModel):
    prompt: str | None = None
    finalPrompt: str | None = None
    model: str | None = None
    count: int = 1
    aspectRatio: str = DEFAULT_ASSET_IMAGE_ASPECT_RATIO


class SelectAssetImageRequest(BaseModel):
    imageId: str


class ModelChatTestRequest(BaseModel):
    prompt: str = "请用一句话回复：模型连接正常。"


app = FastAPI(title="视频重制工坊 API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5174", "http://127.0.0.1:5174"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(make_router(connect, lambda: DATA_DIR, lambda: PROJECTS_DIR, get_project_or_404, get_episode_or_first, new_id, data_file_url))


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    # This process has just started, so any persisted running task belongs to
    # a terminated process and must not be presented as still making progress.
    with connect() as conn:
        running = conn.execute("SELECT id, result_json FROM tasks WHERE status = ?", ("运行中",)).fetchall()
        for row in running:
            result = json.loads(row["result_json"] or "{}")
            result["中断原因"] = "服务进程已退出或重启，本次任务未完成；请创建新任务重试。"
            update_task_status(
                conn,
                row["id"],
                "失败",
                "服务重启：上次任务已中断，请重试",
                result,
            )
        conn.commit()


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "db": str(DB_PATH), "time": now_iso()}


@app.get("/api/files/{resource_path:path}")
def read_data_file(resource_path: str, download: bool = False) -> FileResponse:
    target = (DATA_DIR / resource_path).resolve()
    try:
        target.relative_to(DATA_DIR.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="File is outside data directory") from exc
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(target, filename=target.name if download else None)


@app.get("/api/model-status")
def model_status() -> dict[str, Any]:
    try:
        return get_model_status(load_models_config())
    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/model-test/chat")
def model_chat_test(payload: ModelChatTestRequest) -> dict[str, Any]:
    config = load_models_config()
    provider = get_model_provider(config)
    text_model = config.get("volcengine", {}).get("textModel", "")
    if config.get("mode") == "mock":
        return {
            "mode": "mock",
            "model": text_model,
            "content": "mock 模式：模型连接测试未调用外部接口。"
        }
    try:
        content = provider.chat_completion(
            model_name=text_model,
            system_prompt="你是一个用于连通性测试的助手。只需简短回答。",
            user_content=payload.prompt,
            temperature=0.1,
        )
    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"mode": config.get("mode"), "model": text_model, "content": content}


@app.get("/api/projects")
def list_projects() -> dict[str, Any]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM projects ORDER BY created_at DESC").fetchall()
        return {"projects": rows_to_dicts(rows)}


@app.post("/api/projects")
def create_project(payload: ProjectCreate) -> dict[str, Any]:
    if payload.remakeMode not in REMAKE_MODES:
        raise HTTPException(status_code=400, detail="不支持的制作模式")
    style = payload.storyboardVisualStyle or ("cg_3d" if payload.remakeMode == "cg_3d" else "live_action")
    if style not in STORYBOARD_STYLE_TEMPLATES:
        raise HTTPException(status_code=400, detail="不支持的分镜画面类型")
    if payload.remakeMode == "cg_3d":
        style = "cg_3d"
    target_country = payload.targetCountry or ("中国" if payload.remakeMode == "asset_replacement" else "美国")
    target_language = payload.targetLanguage or ("沿用原片语言" if payload.remakeMode == "asset_replacement" else "英语")
    project_id = new_id("project")
    timestamp = now_iso()
    project_dir = PROJECTS_DIR / project_id
    (project_dir / "source").mkdir(parents=True, exist_ok=True)
    (project_dir / "agents").mkdir(parents=True, exist_ok=True)
    (project_dir / "assets").mkdir(parents=True, exist_ok=True)
    (project_dir / "outputs" / "videos").mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO projects (id, title, target_country, target_language, status, remake_mode, source_language, storyboard_visual_style, replace_characters, replace_scenes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                payload.title,
                target_country,
                target_language,
                "已创建",
                payload.remakeMode,
                payload.sourceLanguage,
                style,
                int(payload.replaceCharacters),
                int(payload.replaceScenes),
                timestamp,
                timestamp,
            ),
        )
        project = get_project_or_404(conn, project_id)
    return {"project": project}


@app.patch("/api/projects/{project_id}")
def update_project(project_id: str, payload: ProjectUpdate) -> dict[str, Any]:
    with connect() as conn:
        project = get_project_or_404(conn, project_id)
        mode = payload.remakeMode or project.get("remake_mode") or "overseas_localization"
        style = payload.storyboardVisualStyle or project.get("storyboard_visual_style") or "live_action"
        if mode not in REMAKE_MODES or style not in STORYBOARD_STYLE_TEMPLATES:
            raise HTTPException(status_code=400, detail="不支持的制作模式或分镜画面类型")
        target_images = conn.execute("SELECT COUNT(*) FROM asset_images WHERE project_id = ?", (project_id,)).fetchone()[0]
        if mode != project.get("remake_mode") and target_images:
            raise HTTPException(status_code=409, detail="当前项目已有目标资产图，请使用‘另存为新制作项目’切换模式。")
        if conn.execute("SELECT COUNT(*) FROM asset_generation_jobs WHERE project_id=? AND status='running'", (project_id,)).fetchone()[0]:
            raise HTTPException(status_code=409, detail="当前项目仍有资产图生成任务，请等待任务完成后切换模式或另存项目。")
        if mode == "cg_3d":
            style = "cg_3d"
        elif mode == "overseas_localization" and not payload.storyboardVisualStyle:
            style = "live_action"
        conn.execute(
            """UPDATE projects SET title=?, remake_mode=?, source_language=?, storyboard_visual_style=?, replace_characters=?, replace_scenes=?, target_country=?, target_language=?, updated_at=? WHERE id=?""",
            (payload.title.strip() if payload.title and payload.title.strip() else project["title"], mode, payload.sourceLanguage if payload.sourceLanguage is not None else project.get("source_language", ""), style,
             int(payload.replaceCharacters if payload.replaceCharacters is not None else project.get("replace_characters", 0)),
             int(payload.replaceScenes if payload.replaceScenes is not None else project.get("replace_scenes", 0)),
             payload.targetCountry or project["target_country"], payload.targetLanguage or project["target_language"], now_iso(), project_id),
        )
        if (mode != project.get("remake_mode") or style != project.get("storyboard_visual_style")
                or int(payload.replaceCharacters if payload.replaceCharacters is not None else project.get("replace_characters", 0)) != int(project.get("replace_characters", 0))
                or int(payload.replaceScenes if payload.replaceScenes is not None else project.get("replace_scenes", 0)) != int(project.get("replace_scenes", 0))):
            conn.execute("UPDATE storyboards SET status='待更新' WHERE project_id=?", (project_id,))
        updated = get_project_or_404(conn, project_id)
        return {"project": updated}


@app.post("/api/projects/{project_id}/derive")
def derive_project(project_id: str, payload: ProjectDeriveRequest) -> dict[str, Any]:
    if payload.remakeMode not in REMAKE_MODES:
        raise HTTPException(status_code=400, detail="不支持的制作模式")
    style = payload.storyboardVisualStyle or ("cg_3d" if payload.remakeMode == "cg_3d" else "live_action")
    if payload.remakeMode == "cg_3d":
        style = "cg_3d"
    if style not in STORYBOARD_STYLE_TEMPLATES:
        raise HTTPException(status_code=400, detail="不支持的分镜画面类型")
    with connect() as conn:
        source = get_project_or_404(conn, project_id)
        new_project_id = new_id("project")
        timestamp = now_iso()
        title = payload.title if payload.title != "未命名短剧重制项目" else f"{source['title']}（{payload.remakeMode}）"
        conn.execute(
            """INSERT INTO projects (id,title,target_country,target_language,status,remake_mode,source_language,storyboard_visual_style,replace_characters,replace_scenes,parent_project_id,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (new_project_id,title,payload.targetCountry or source["target_country"],payload.targetLanguage or source["target_language"],"派生项目已创建",
             payload.remakeMode,payload.sourceLanguage or source.get("source_language", ""),style,int(payload.replaceCharacters),int(payload.replaceScenes),project_id,timestamp,timestamp),
        )
        for subdir in ["source", "agents", "assets", "outputs/videos"]:
            (PROJECTS_DIR / new_project_id / subdir).mkdir(parents=True, exist_ok=True)
        episode_map: dict[str, str] = {}
        for episode in rows_to_dicts(conn.execute("SELECT * FROM episodes WHERE project_id=? ORDER BY episode_index", (project_id,)).fetchall()):
            episode_id = new_id("episode")
            episode_map[episode["id"]] = episode_id
            conn.execute("""INSERT INTO episodes (id,project_id,episode_index,filename,file_path,source_url,ark_file_id,ark_file_status,ark_file_error,status,created_at)
                          VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                         (episode_id,new_project_id,episode["episode_index"],episode["filename"],episode["file_path"],episode["source_url"],episode["ark_file_id"],episode["ark_file_status"],episode["ark_file_error"],episode["status"],episode["created_at"]))
        variable_map: dict[str, str] = {}
        old_assets = rows_to_dicts(conn.execute("SELECT * FROM assets WHERE project_id=? ORDER BY created_at", (project_id,)).fetchall())
        for asset in old_assets:
            asset_id = new_id("asset")
            original_payload = json.loads(asset["payload_json"])
            source_description = asset_source_description(asset["asset_type"], original_payload) or "原片描述未留存"
            old_for_variable = dict(asset)
            old_for_variable["payload"] = original_payload
            attach_display_variable(old_for_variable)
            asset_payload = strip_previous_target_fields(original_payload)
            source_key = str(asset_payload.get("源AssetKey") or asset_payload.get("sourceAssetKey") or asset["id"])
            asset_payload["源AssetKey"] = source_key
            asset_payload["原片资产描述"] = source_description
            asset_payload["目标生成描述"] = ""
            asset_payload["用户编辑资产图生成提示词"] = ""
            asset_payload["目标资产来源"] = "沿用原貌"
            new_for_variable = {**asset, "localized_name": asset["name"], "payload": asset_payload}
            attach_display_variable(new_for_variable)
            variable_map[str(old_for_variable.get("display_variable") or "")] = str(new_for_variable.get("display_variable") or "")
            conn.execute("INSERT INTO assets (id,project_id,episode_id,asset_type,name,localized_name,variable,status,payload_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (asset_id,new_project_id,episode_map.get(asset.get("episode_id")),asset["asset_type"],asset["name"],asset["name"],asset["variable"],asset["status"],json.dumps(asset_payload,ensure_ascii=False),asset["created_at"]))
        for storyboard in rows_to_dicts(conn.execute("SELECT * FROM storyboards WHERE project_id=? ORDER BY created_at", (project_id,)).fetchall()):
            storyboard_id = new_id("storyboard")
            sb_payload = strip_previous_target_fields(json.loads(storyboard["payload_json"]))
            source_prompt = sb_payload.get("源分镜文本") or storyboard["prompt"]
            for old_variable, new_variable in variable_map.items():
                if old_variable:
                    source_prompt = source_prompt.replace(old_variable, new_variable)
            sb_payload["源分镜文本"] = source_prompt
            refs = sb_payload.get("引用资产变量") if isinstance(sb_payload.get("引用资产变量"), list) else []
            sb_payload["引用资产变量"] = [variable_map.get(str(ref), str(ref)) for ref in refs]
            sb_payload["资产映射校验"] = {**(sb_payload.get("资产映射校验") if isinstance(sb_payload.get("资产映射校验"), dict) else {}), "派生项目目标资产重映射": True}
            sb_payload["最终提示词快照"] = ""
            sb_payload["待更新原因"] = "新制作项目尚未按当前目标资产与画面类型刷新提示词"
            conn.execute("INSERT INTO storyboards (id,project_id,episode_id,title,time_range,prompt,payload_json,status,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                         (storyboard_id,new_project_id,episode_map.get(storyboard["episode_id"], storyboard["episode_id"]),storyboard["title"],storyboard["time_range"],source_prompt,json.dumps(sb_payload,ensure_ascii=False),"待更新",storyboard["created_at"]))
        return {"project": get_project_or_404(conn, new_project_id), "parentProjectId": project_id, "assetsCopied": conn.execute("SELECT COUNT(*) FROM assets WHERE project_id=?", (new_project_id,)).fetchone()[0], "targetImagesCopied": 0}


@app.get("/api/projects/{project_id}")
def get_project(project_id: str) -> dict[str, Any]:
    with connect() as conn:
        project = get_project_or_404(conn, project_id)
        episodes = rows_to_dicts(
            conn.execute(
                "SELECT * FROM episodes WHERE project_id = ? ORDER BY episode_index",
                (project_id,),
            ).fetchall()
        )
        return {"project": project, "episodes": enrich_episodes(episodes)}


@app.post("/api/projects/{project_id}/episodes/upload")
async def upload_episodes(project_id: str, files: list[UploadFile] = File(...)) -> dict[str, Any]:
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")
    with connect() as conn:
        get_project_or_404(conn, project_id)
        locked_task = conn.execute(
            """
            SELECT id FROM tasks
            WHERE project_id = ? AND task_type IN ('series_analysis', 'asset_extraction') AND status IN ('成功', '运行中')
            LIMIT 1
            """,
            (project_id,),
        ).fetchone()
        if locked_task:
            raise HTTPException(
                status_code=409,
                detail="项目整剧分析进行中或已完成，剧集已锁定，不能再上传或调整剧集；如需更换素材请新建项目",
            )
        project_dir = PROJECTS_DIR / project_id / "source"
        project_dir.mkdir(parents=True, exist_ok=True)
        saved: list[dict[str, Any]] = []
        sorted_files = sorted(
            enumerate(files, start=1),
            key=lambda item: (extract_episode_index(item[1].filename or "", item[0]), item[1].filename or ""),
        )
        for fallback_index, upload in sorted_files:
            filename = Path(upload.filename or f"episode_{fallback_index:03d}.mp4").name
            episode_index = extract_episode_index(filename, fallback_index)
            suffix = Path(filename).suffix or ".mp4"
            stored_name = f"episode_{episode_index:03d}{suffix}"
            target_path = project_dir / stored_name
            with target_path.open("wb") as target:
                shutil.copyfileobj(upload.file, target)
            episode_id = new_id("episode")
            timestamp = now_iso()
            conn.execute(
                """
                INSERT OR REPLACE INTO episodes
                (id, project_id, episode_index, filename, file_path, source_url, ark_file_id, ark_file_status, ark_file_error, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    episode_id,
                    project_id,
                    episode_index,
                    filename,
                    str(target_path),
                    "",
                    "",
                    "",
                    "",
                    "已上传",
                    timestamp,
                ),
            )
            episode_record = {
                "id": episode_id,
                "project_id": project_id,
                "episode_index": episode_index,
                "filename": filename,
                "file_path": str(target_path),
                "source_url": "",
                "ark_file_id": "",
                "ark_file_status": "",
                "ark_file_error": "",
                "status": "已上传",
                "created_at": timestamp,
            }
            enrich_episode(episode_record)
            ark_result = upload_episode_to_ark(conn, episode_record)
            episode_record["ark_file_id"] = ark_result["file_id"]
            episode_record["ark_file_status"] = ark_result["status"]
            episode_record["ark_file_error"] = ark_result["error"]
            episode_record["status"] = "已上传并同步 Ark" if ark_result["file_id"] else "已上传，Ark 同步失败"
            conn.execute(
                "UPDATE episodes SET status = ? WHERE id = ?",
                (episode_record["status"], episode_id),
            )
            saved.append(episode_record)
        conn.execute(
            "UPDATE projects SET status = ?, updated_at = ? WHERE id = ?",
            ("已上传", now_iso(), project_id),
        )
    return {"episodes": saved}


@app.get("/api/projects/{project_id}/episodes")
def list_episodes(project_id: str) -> dict[str, Any]:
    with connect() as conn:
        get_project_or_404(conn, project_id)
        rows = conn.execute(
            "SELECT * FROM episodes WHERE project_id = ? ORDER BY episode_index",
            (project_id,),
        ).fetchall()
        return {"episodes": enrich_episodes(rows_to_dicts(rows))}


EPISODE_VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".mkv", ".webm"}


@app.post("/api/projects/{project_id}/episodes")
def create_blank_episode(project_id: str) -> dict[str, Any]:
    """新建空白分集：不携带原片，占位第 N+1 集，供分镜页"新建分集"使用。

    与 /episodes/upload 的锁定规则不同，空白分集是纯新增动作，不影响已有剧集
    的解析结果，因此即使整剧分析已完成也允许创建。
    """
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        get_project_or_404(conn, project_id)
        row = conn.execute(
            "SELECT MAX(episode_index) AS max_index FROM episodes WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        next_index = int((row["max_index"] if row else 0) or 0) + 1
        episode_id = new_id("episode")
        timestamp = now_iso()
        conn.execute(
            """
            INSERT INTO episodes
            (id, project_id, episode_index, filename, file_path, source_url, ark_file_id, ark_file_status, ark_file_error, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (episode_id, project_id, next_index, f"第 {next_index} 集（空白分集）", "", "", "", "", "", "待上传原片", timestamp),
        )
        created = row_to_dict(
            conn.execute(
                "SELECT * FROM episodes WHERE id = ? AND project_id = ?",
                (episode_id, project_id),
            ).fetchone()
        )
        return {"episode": enrich_episode(created)}


@app.post("/api/projects/{project_id}/episodes/{episode_id}/source-video")
async def attach_episode_source(project_id: str, episode_id: str, file: UploadFile = File(...)) -> dict[str, Any]:
    """给空白分集挂原片：仅允许 file_path 为空的分集，避免覆盖已有原片。"""
    suffix = Path(file.filename or "").suffix.lower()
    if suffix and suffix not in EPISODE_VIDEO_SUFFIXES:
        raise HTTPException(status_code=415, detail="仅支持 MP4、MOV、M4V、MKV、WebM 视频")
    suffix = suffix or ".mp4"
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        get_project_or_404(conn, project_id)
        episode = row_to_dict(
            conn.execute(
                "SELECT * FROM episodes WHERE id = ? AND project_id = ?",
                (episode_id, project_id),
            ).fetchone()
        )
        if not episode:
            raise HTTPException(status_code=404, detail="Episode not found")
        if str(episode.get("file_path") or "").strip():
            raise HTTPException(status_code=409, detail="该分集已有原片，不能重复挂载")
        project_dir = PROJECTS_DIR / project_id / "source"
        project_dir.mkdir(parents=True, exist_ok=True)
        target_path = project_dir / f"episode_{int(episode['episode_index']):03d}{suffix}"
        filename = Path(file.filename or target_path.name).name
        try:
            with target_path.open("wb") as target:
                while chunk := await file.read(1024 * 1024):
                    target.write(chunk)
        finally:
            await file.close()
        timestamp = now_iso()
        conn.execute(
            "UPDATE episodes SET filename = ?, file_path = ?, status = ? WHERE id = ?",
            (filename, str(target_path), "已上传", episode_id),
        )
        record = row_to_dict(
            conn.execute(
                "SELECT * FROM episodes WHERE id = ? AND project_id = ?",
                (episode_id, project_id),
            ).fetchone()
        )
        enrich_episode(record)
        ark_result = upload_episode_to_ark(conn, record)
        record["ark_file_id"] = ark_result["file_id"]
        record["ark_file_status"] = ark_result["status"]
        record["ark_file_error"] = ark_result["error"]
        record["status"] = "已上传并同步 Ark" if ark_result["file_id"] else "已上传，Ark 同步失败"
        conn.execute(
            "UPDATE episodes SET status = ? WHERE id = ?",
            (record["status"], episode_id),
        )
        conn.execute(
            "UPDATE projects SET status = ?, updated_at = ? WHERE id = ?",
            ("已上传", now_iso(), project_id),
        )
        return {"episode": record}


@app.delete("/api/projects/{project_id}/episodes/{episode_id}")
def delete_blank_episode(project_id: str, episode_id: str) -> dict[str, Any]:
    """删除空白分集：只允许没有任何产出物的分集（无原片、无分镜、无资产、无视频生成记录）。"""
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        get_project_or_404(conn, project_id)
        episode = row_to_dict(
            conn.execute(
                "SELECT * FROM episodes WHERE id = ? AND project_id = ?",
                (episode_id, project_id),
            ).fetchone()
        )
        if not episode:
            raise HTTPException(status_code=404, detail="Episode not found")
        if str(episode.get("file_path") or "").strip():
            raise HTTPException(status_code=409, detail="该分集已有原片，不能删除；如需更换素材请新建项目")
        blockers = {
            "分镜": conn.execute("SELECT COUNT(*) AS c FROM storyboards WHERE project_id=? AND episode_id=?", (project_id, episode_id)).fetchone()["c"],
            "资产": conn.execute("SELECT COUNT(*) AS c FROM assets WHERE project_id=? AND episode_id=?", (project_id, episode_id)).fetchone()["c"],
            "视频生成记录": conn.execute("SELECT COUNT(*) AS c FROM video_generations WHERE project_id=? AND episode_id=?", (project_id, episode_id)).fetchone()["c"],
            "视频素材": conn.execute("SELECT COUNT(*) AS c FROM video_media WHERE project_id=? AND episode_id=?", (project_id, episode_id)).fetchone()["c"],
            "剪辑草稿": conn.execute("SELECT COUNT(*) AS c FROM video_edit_drafts WHERE project_id=? AND episode_id=?", (project_id, episode_id)).fetchone()["c"],
        }
        if any(blockers.values()):
            detail = "、".join(f"{name} {count} 条" for name, count in blockers.items() if count)
            raise HTTPException(status_code=409, detail=f"该分集已有关联产出（{detail}），不能删除")
        conn.execute("DELETE FROM episodes WHERE id = ? AND project_id = ?", (episode_id, project_id))
        return {"deleted": True, "episodeId": episode_id}


@app.patch("/api/projects/{project_id}/episodes/{episode_id}/source-url")
def update_episode_source_url(project_id: str, episode_id: str, payload: EpisodeSourceUpdate) -> dict[str, Any]:
    with connect() as conn:
        get_project_or_404(conn, project_id)
        episode = row_to_dict(
            conn.execute(
                "SELECT * FROM episodes WHERE id = ? AND project_id = ?",
                (episode_id, project_id),
            ).fetchone()
        )
        if not episode:
            raise HTTPException(status_code=404, detail="Episode not found")
        conn.execute(
            "UPDATE episodes SET source_url = ? WHERE id = ? AND project_id = ?",
            (payload.sourceUrl.strip(), episode_id, project_id),
        )
        updated = row_to_dict(
            conn.execute(
                "SELECT * FROM episodes WHERE id = ? AND project_id = ?",
                (episode_id, project_id),
            ).fetchone()
        )
        return {"episode": enrich_episode(updated)}


@app.post("/api/projects/{project_id}/episodes/{episode_id}/sync-ark-file")
def sync_episode_ark_file(project_id: str, episode_id: str) -> dict[str, Any]:
    with connect() as conn:
        get_project_or_404(conn, project_id)
        episode = row_to_dict(
            conn.execute(
                "SELECT * FROM episodes WHERE id = ? AND project_id = ?",
                (episode_id, project_id),
            ).fetchone()
        )
        if not episode:
            raise HTTPException(status_code=404, detail="Episode not found")
        result = upload_episode_to_ark(conn, episode)
        updated = row_to_dict(
            conn.execute(
                "SELECT * FROM episodes WHERE id = ? AND project_id = ?",
                (episode_id, project_id),
            ).fetchone()
        )
        return {"episode": enrich_episode(updated), "ark": result}


def asset_group_identity_key(asset: dict[str, Any]) -> str:
    payload = asset.get("payload") if isinstance(asset.get("payload"), dict) else json.loads(asset.get("payload_json") or "{}")
    return str(payload.get("项目主资产Key") or asset_project_main_key({**asset, "payload": payload}))


def load_asset_group_candidate(conn: sqlite3.Connection, project_id: str, asset_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM assets WHERE id=? AND project_id=?", (asset_id, project_id)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Asset not found")
    asset = row_to_dict(row)
    asset["payload"] = json.loads(asset.pop("payload_json") or "{}")
    return asset


def asset_group_response(conn: sqlite3.Connection, row: dict[str, Any]) -> dict[str, Any]:
    member_rows = conn.execute(
        "SELECT asset_id, asset_key, sort_order, created_at FROM asset_group_members WHERE group_id=? ORDER BY sort_order, created_at",
        (row["id"],),
    ).fetchall()
    return {
        "id": row["id"],
        "name": row["name"],
        "assetType": row["asset_type"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "members": [
            {"assetId": member["asset_id"], "assetKey": member["asset_key"], "createdAt": member["created_at"]}
            for member in member_rows
        ],
    }


def normalize_asset_group_members(
    conn: sqlite3.Connection,
    project_id: str,
    member_asset_ids: list[str],
    *,
    group_id: str | None = None,
) -> list[tuple[str, str, str]]:
    requested_ids = [str(asset_id).strip() for asset_id in member_asset_ids if str(asset_id).strip()]
    if not requested_ids:
        raise HTTPException(status_code=400, detail="至少选择一个资产")
    candidates: list[tuple[str, str, str]] = []
    seen_keys: set[str] = set()
    expected_type = ""
    for asset_id in requested_ids:
        asset = load_asset_group_candidate(conn, project_id, asset_id)
        asset_key = asset_group_identity_key(asset)
        if asset_key in seen_keys:
            continue
        seen_keys.add(asset_key)
        asset_type = str(asset.get("asset_type") or "")
        if not expected_type:
            expected_type = asset_type
        elif asset_type != expected_type:
            raise HTTPException(status_code=400, detail="同一个资产组只能包含同类资产")
        candidates.append((asset_id, asset_key, asset_type))
    if not candidates:
        raise HTTPException(status_code=400, detail="没有可加入的资产")
    if group_id:
        current_types = conn.execute(
            "SELECT asset_type FROM asset_groups WHERE id=? AND project_id=?",
            (group_id, project_id),
        ).fetchall()
        if current_types and str(current_types[0]["asset_type"]) != expected_type:
            raise HTTPException(status_code=400, detail="组内资产类型不一致")
    return candidates


def replace_asset_group_members(
    conn: sqlite3.Connection,
    project_id: str,
    group_id: str,
    members: list[tuple[str, str, str]],
) -> None:
    conn.execute("DELETE FROM asset_group_members WHERE project_id=? AND group_id=?", (project_id, group_id))
    now = now_iso()
    for sort_order, (asset_id, asset_key, _asset_type) in enumerate(members):
        conn.execute(
            "DELETE FROM asset_group_members WHERE project_id=? AND asset_key=?",
            (project_id, asset_key),
        )
        conn.execute(
            """INSERT INTO asset_group_members
               (id,project_id,group_id,asset_id,asset_key,sort_order,created_at) VALUES (?,?,?,?,?,?,?)""",
            (new_id("asset_group_member"), project_id, group_id, asset_id, asset_key, sort_order, now),
        )
    conn.execute("UPDATE asset_groups SET updated_at=? WHERE id=? AND project_id=?", (now, group_id, project_id))


@app.get("/api/projects/{project_id}/asset-groups")
def list_asset_groups(project_id: str) -> dict[str, Any]:
    with connect() as conn:
        get_project_or_404(conn, project_id)
        migrate_asset_parent_links_to_groups(conn)
        rows = conn.execute(
            "SELECT * FROM asset_groups WHERE project_id=? ORDER BY created_at",
            (project_id,),
        ).fetchall()
        return {"groups": [asset_group_response(conn, row_to_dict(row)) for row in rows]}


@app.post("/api/projects/{project_id}/asset-groups")
def create_asset_group(project_id: str, payload: AssetGroupCreate) -> dict[str, Any]:
    name = payload.name.strip()
    if not name or len(name) > 80:
        raise HTTPException(status_code=400, detail="组名不能为空且不能超过 80 个字符")
    with connect() as conn:
        get_project_or_404(conn, project_id)
        if conn.execute("SELECT 1 FROM asset_groups WHERE project_id=? AND name=?", (project_id, name)).fetchone():
            raise HTTPException(status_code=400, detail="同名资产组已存在")
        members = normalize_asset_group_members(conn, project_id, payload.memberAssetIds)
        now = now_iso()
        group_id = new_id("asset_group")
        conn.execute(
            "INSERT INTO asset_groups (id,project_id,name,asset_type,created_at,updated_at) VALUES (?,?,?,?,?,?)",
            (group_id, project_id, name, members[0][2], now, now),
        )
        replace_asset_group_members(conn, project_id, group_id, members)
        conn.commit()
        row = row_to_dict(conn.execute("SELECT * FROM asset_groups WHERE id=?", (group_id,)).fetchone())
        return {"group": asset_group_response(conn, row)}


@app.patch("/api/projects/{project_id}/asset-groups/{group_id}")
def update_asset_group(project_id: str, group_id: str, payload: AssetGroupUpdate) -> dict[str, Any]:
    with connect() as conn:
        get_project_or_404(conn, project_id)
        row = conn.execute("SELECT * FROM asset_groups WHERE id=? AND project_id=?", (group_id, project_id)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Asset group not found")
        if payload.name is not None:
            name = payload.name.strip()
            if not name or len(name) > 80:
                raise HTTPException(status_code=400, detail="组名不能为空且不能超过 80 个字符")
            duplicate = conn.execute(
                "SELECT 1 FROM asset_groups WHERE project_id=? AND name=? AND id<>?",
                (project_id, name, group_id),
            ).fetchone()
            if duplicate:
                raise HTTPException(status_code=400, detail="同名资产组已存在")
            conn.execute(
                "UPDATE asset_groups SET name=?, updated_at=? WHERE id=? AND project_id=?",
                (name, now_iso(), group_id, project_id),
            )
        if payload.memberAssetIds is not None:
            members = normalize_asset_group_members(conn, project_id, payload.memberAssetIds, group_id=group_id)
            replace_asset_group_members(conn, project_id, group_id, members)
            conn.execute(
                "UPDATE asset_groups SET asset_type=?, updated_at=? WHERE id=? AND project_id=?",
                (members[0][2], now_iso(), group_id, project_id),
            )
        conn.commit()
        updated = row_to_dict(conn.execute("SELECT * FROM asset_groups WHERE id=? AND project_id=?", (group_id, project_id)).fetchone())
        return {"group": asset_group_response(conn, updated)}


@app.delete("/api/projects/{project_id}/asset-groups/{group_id}")
def delete_asset_group(project_id: str, group_id: str) -> dict[str, Any]:
    with connect() as conn:
        get_project_or_404(conn, project_id)
        row = conn.execute("SELECT * FROM asset_groups WHERE id=? AND project_id=?", (group_id, project_id)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Asset group not found")
        conn.execute("DELETE FROM asset_group_members WHERE project_id=? AND group_id=?", (project_id, group_id))
        conn.execute("DELETE FROM asset_groups WHERE id=? AND project_id=?", (group_id, project_id))
        conn.commit()
        return {"deleted": True, "group": asset_group_response(conn, row_to_dict(row))}


def asset_has_running_generation(conn: sqlite3.Connection, project_id: str, asset_id: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM asset_generation_jobs WHERE project_id=? AND asset_id=? AND status='running' LIMIT 1",
        (project_id, asset_id),
    ).fetchone() is not None


def remove_asset_records(conn: sqlite3.Connection, project_id: str, asset: dict[str, Any]) -> list[str]:
    """删除资产及其关联记录，返回因此变空而被清理的资产组 id。

    只清理数据库记录，磁盘上的生成图片保留，避免误删不可恢复的文件。
    """
    asset_id = str(asset["id"])
    try:
        asset_key = asset_group_identity_key(asset)
    except Exception:
        asset_key = ""
    member_rows = conn.execute(
        "SELECT DISTINCT group_id FROM asset_group_members WHERE project_id=? AND (asset_id=? OR (?<>'' AND asset_key=?))",
        (project_id, asset_id, asset_key, asset_key),
    ).fetchall()
    group_ids = [str(row["group_id"]) for row in member_rows]
    conn.execute(
        "DELETE FROM asset_group_members WHERE project_id=? AND (asset_id=? OR (?<>'' AND asset_key=?))",
        (project_id, asset_id, asset_key, asset_key),
    )
    conn.execute(
        "DELETE FROM asset_parent_links WHERE project_id=? AND (parent_asset_id=? OR child_asset_id=?)",
        (project_id, asset_id, asset_id),
    )
    conn.execute("DELETE FROM asset_images WHERE project_id=? AND asset_id=?", (project_id, asset_id))
    conn.execute("DELETE FROM asset_generation_jobs WHERE project_id=? AND asset_id=?", (project_id, asset_id))
    try:
        mark_storyboards_asset_stale(conn, project_id, asset, "引用的资产已删除")
    except Exception:
        pass
    conn.execute("DELETE FROM assets WHERE id=? AND project_id=?", (asset_id, project_id))
    emptied: list[str] = []
    for group_id in group_ids:
        remaining = conn.execute(
            "SELECT COUNT(*) AS n FROM asset_group_members WHERE project_id=? AND group_id=?",
            (project_id, group_id),
        ).fetchone()
        if not remaining or int(remaining["n"] or 0) == 0:
            conn.execute("DELETE FROM asset_groups WHERE id=? AND project_id=?", (group_id, project_id))
            emptied.append(group_id)
    return emptied


@app.delete("/api/projects/{project_id}/assets/{asset_id}")
def delete_asset(project_id: str, asset_id: str) -> dict[str, Any]:
    with connect() as conn:
        get_project_or_404(conn, project_id)
        asset = row_to_dict(
            conn.execute("SELECT * FROM assets WHERE id=? AND project_id=?", (asset_id, project_id)).fetchone()
        )
        if not asset:
            raise HTTPException(status_code=404, detail="Asset not found")
        if asset_has_running_generation(conn, project_id, asset_id):
            raise HTTPException(status_code=409, detail="该资产仍在生成中，请完成后再删除。")
        removed_groups = remove_asset_records(conn, project_id, asset)
        conn.commit()
        return {"deleted": True, "assetId": asset_id, "removedGroups": removed_groups}


@app.post("/api/projects/{project_id}/assets/batch-delete")
def batch_delete_assets(project_id: str, payload: AssetBatchDeleteRequest) -> dict[str, Any]:
    deleted: list[str] = []
    skipped: list[dict[str, str]] = []
    removed_groups: set[str] = set()
    with connect() as conn:
        get_project_or_404(conn, project_id)
        for asset_id in dict.fromkeys(str(item).strip() for item in payload.assetIds if str(item).strip()):
            asset = row_to_dict(
                conn.execute("SELECT * FROM assets WHERE id=? AND project_id=?", (asset_id, project_id)).fetchone()
            )
            if not asset:
                skipped.append({"assetId": asset_id, "reason": "资产不存在或已被删除"})
                continue
            if asset_has_running_generation(conn, project_id, asset_id):
                skipped.append({"assetId": asset_id, "reason": "仍在生成中"})
                continue
            removed_groups.update(remove_asset_records(conn, project_id, asset))
            deleted.append(asset_id)
        conn.commit()
    return {"deleted": deleted, "skipped": skipped, "removedGroups": sorted(removed_groups)}


@app.post("/api/projects/{project_id}/assets")
def create_asset(project_id: str, payload: AssetCreateRequest) -> dict[str, Any]:
    asset_type = str(payload.assetType or "").strip()
    if asset_type not in ("角色", "场景", "道具"):
        raise HTTPException(status_code=400, detail="资产类型只能是角色、场景或道具")
    with connect() as conn:
        get_project_or_404(conn, project_id)
        name_prefix = {"角色": "未命名角色", "场景": "未命名场景", "道具": "未命名道具"}[asset_type]
        existing_names = {
            str(row["name"])
            for row in conn.execute(
                "SELECT name FROM assets WHERE project_id=? AND asset_type=?",
                (project_id, asset_type),
            ).fetchall()
        }
        requested_name = (payload.name or "").strip()
        if requested_name and requested_name in existing_names:
            raise HTTPException(status_code=400, detail="同名资产已存在，请换一个名称")
        asset_name = requested_name
        if not asset_name:
            index = 1
            while f"{name_prefix}{index}" in existing_names:
                index += 1
            asset_name = f"{name_prefix}{index}"
        localized_name = (payload.localizedName or "").strip() or asset_name
        asset_id = new_id("asset")
        asset_payload: dict[str, Any] = {
            "手动新建": True,
            "原片资产描述": "手动新建的空白资产，等待填写原片与目标描述。",
            "目标生成描述": "",
            "用户编辑资产图生成提示词": "",
            "目标资产来源": "手动新建",
            "生成状态": "待补齐",
        }
        draft = {
            "id": asset_id,
            "asset_type": asset_type,
            "name": asset_name,
            "localized_name": localized_name,
            "variable": f"@{asset_name}",
            "payload": asset_payload,
        }
        annotate_project_asset_keys(draft)
        attach_display_variable(draft)
        conn.execute(
            "INSERT INTO assets (id,project_id,episode_id,asset_type,name,localized_name,variable,status,payload_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                asset_id,
                project_id,
                None,
                asset_type,
                asset_name,
                localized_name,
                f"@{asset_name}",
                "待确认",
                json.dumps(asset_payload, ensure_ascii=False),
                now_iso(),
            ),
        )
        conn.commit()
        return get_asset_response(conn, project_id, asset_id)


@app.get("/api/projects/{project_id}/assets")
def list_assets(project_id: str) -> dict[str, Any]:
    with connect() as conn:
        project = get_project_or_404(conn, project_id)
        rows = conn.execute(
            """
            SELECT assets.*, episodes.episode_index
            FROM assets
            LEFT JOIN episodes ON episodes.id = assets.episode_id
            WHERE assets.project_id = ?
            ORDER BY assets.created_at, assets.asset_type
            """,
            (project_id,),
        ).fetchall()
        raw_assets = rows_to_dicts(rows)
        merged_assets: dict[str, dict[str, Any]] = {}
        for asset in raw_assets:
            episode_index = asset.pop("episode_index", None)
            asset["payload"] = enrich_asset_payload_urls(json.loads(asset.pop("payload_json")))
            project_valid_target_profile_for_asset_view(asset, project)
            prepare_asset_prompt_fields(asset, project)
            asset["payload"].setdefault("原片资产描述", asset_source_description(asset["asset_type"], asset["payload"]) or "原片描述未留存")
            asset["payload"].setdefault("目标生成描述", "")
            selected_image = conn.execute(
                "SELECT id,file_path,payload_json FROM asset_images WHERE project_id=? AND asset_id=? AND is_selected=1 ORDER BY created_at DESC LIMIT 1",
                (project_id, asset["id"]),
            ).fetchone()
            effective = resolve_effective_asset_reference(asset, project, selected_image)
            asset["payload"]["effectiveAssetSource"] = effective["effectiveAssetSource"]
            asset["payload"]["当前有效外观描述"] = effective["description"]
            asset["payload"]["当前有效资产名"] = effective["name"]
            asset["payload"]["当前有效资产就绪"] = effective["ready"]
            asset["payload"]["generationInProgress"] = conn.execute(
                "SELECT 1 FROM asset_generation_jobs WHERE project_id=? AND asset_id=? AND status='running' LIMIT 1",
                (project_id, asset["id"]),
            ).fetchone() is not None
            attach_display_variable(asset)
            annotate_project_asset_keys(asset, episode_index if isinstance(episode_index, int) else None)
            image_rows = conn.execute(
                "SELECT * FROM asset_images WHERE asset_id = ? ORDER BY created_at DESC",
                (asset["id"],),
            ).fetchall()
            images = rows_to_dicts(image_rows)
            for image in images:
                enrich_asset_image(image)
            asset["images"] = images
            key = str(asset["payload"].get("项目子资产Key") or asset_project_variant_key(asset))
            if key not in merged_assets:
                merged_assets[key] = asset
                continue
            target = merged_assets[key]
            appearances = set(target["payload"].get("出现集数") if isinstance(target["payload"].get("出现集数"), list) else [])
            for item in asset["payload"].get("出现集数", []):
                if isinstance(item, int):
                    appearances.add(item)
            target["payload"]["出现集数"] = sorted(appearances)
            target["payload"]["去重来源资产数"] = int(target["payload"].get("去重来源资产数") or 1) + 1
            if not target.get("images") and asset.get("images"):
                target["images"] = asset["images"]
        assets = list(merged_assets.values())
        return {"assets": assets}


@app.patch("/api/projects/{project_id}/assets/{asset_id}")
def update_asset(project_id: str, asset_id: str, payload: AssetUpdate) -> dict[str, Any]:
    with connect() as conn:
        get_project_or_404(conn, project_id)
        asset = row_to_dict(
            conn.execute(
                "SELECT * FROM assets WHERE id = ? AND project_id = ?",
                (asset_id, project_id),
            ).fetchone()
        )
        if not asset:
            raise HTTPException(status_code=404, detail="Asset not found")
        if conn.execute("SELECT 1 FROM asset_generation_jobs WHERE project_id=? AND asset_id=? AND status='running' LIMIT 1", (project_id, asset_id)).fetchone():
            raise HTTPException(status_code=409, detail="该资产仍在生成中，请完成后再修改目标描述。")
        current_payload = json.loads(asset["payload_json"])
        if payload.payload is not None:
            current_payload.update(payload.payload)
        if payload.payload and "用户编辑资产图生成提示词" in payload.payload:
            manual_prompt = str(payload.payload.get("用户编辑资产图生成提示词") or "")
            if manual_prompt.strip():
                current_payload["目标资产来源"] = "手动生成"
        if payload.payload and "目标生成描述" in payload.payload:
            current_payload["目标资产来源"] = "手动生成"
            current_payload["targetVisualLockProvenance"] = "user_override"
        if payload.localizedName:
            current_payload["用户指定目标角色名称"] = payload.localizedName
            current_payload["targetNameProvenance"] = "user_override"
        # 手动新建的空白资产名称为用户随手输入的占位名，重命名时让展示变量跟随名称重算，
        # 否则卡片叫「啦啦队队长」而提示词里仍是「@未命名角色1」。真实分析出的资产保持
        # 变量稳定（分镜已引用旧变量），故仅对空白资产生效。
        if payload.name and payload.name != asset["name"]:
            if asset["asset_type"] == "角色":
                current_payload["用户指定目标角色名称"] = payload.name
            if current_payload.get("手动新建"):
                current_payload.pop("展示资产变量", None)
                current_payload.pop("旧展示资产变量", None)
            # 归并主键随名称重算，避免 payload 里留存旧名称的键值。
            renamed = dict(asset)
            renamed["name"] = payload.name
            renamed["localized_name"] = payload.localizedName or asset["localized_name"]
            renamed["payload"] = current_payload
            current_payload = annotate_project_asset_keys(renamed)["payload"]
        conn.execute(
            """
            UPDATE assets
            SET name = ?, localized_name = ?, status = ?, payload_json = ?
            WHERE id = ? AND project_id = ?
            """,
            (
                payload.name or asset["name"],
                payload.localizedName or asset["localized_name"],
                payload.status or asset["status"],
                json.dumps(current_payload, ensure_ascii=False),
                asset_id,
                project_id,
            ),
        )
        if payload.payload and ("目标生成描述" in payload.payload or "用户编辑资产图生成提示词" in payload.payload or "目标资产来源" in payload.payload) or payload.localizedName:
            mark_storyboards_asset_stale(conn, project_id, asset, "目标资产描述或名称发生变化")
        return get_asset_response(conn, project_id, asset_id)


def mark_storyboards_asset_stale(conn: sqlite3.Connection, project_id: str, asset: dict[str, Any], reason: str) -> None:
    candidate = dict(asset)
    candidate["payload"] = json.loads(asset["payload_json"]) if isinstance(asset.get("payload_json"), str) else asset.get("payload", {})
    attach_display_variable(candidate)
    keys = {str(asset.get("variable") or ""), str(candidate.get("display_variable") or "")}
    keys.discard("")
    for row in conn.execute("SELECT id,prompt,payload_json FROM storyboards WHERE project_id=?", (project_id,)).fetchall():
        payload = json.loads(row["payload_json"])
        refs = payload.get("引用资产变量") if isinstance(payload.get("引用资产变量"), list) else []
        if not (keys.intersection({str(value) for value in refs}) or any(key in str(row["prompt"]) for key in keys)):
            continue
        payload["待更新原因"] = reason
        conn.execute("UPDATE storyboards SET status='待更新',payload_json=? WHERE id=?", (json.dumps(payload, ensure_ascii=False), row["id"]))


@app.post("/api/projects/{project_id}/assets/{asset_id}/confirm")
def confirm_asset(project_id: str, asset_id: str) -> dict[str, Any]:
    with connect() as conn:
        get_project_or_404(conn, project_id)
        conn.execute(
            "UPDATE assets SET status = ? WHERE id = ? AND project_id = ?",
            ("已确认", asset_id, project_id),
        )
        return get_asset_response(conn, project_id, asset_id)


@app.post("/api/projects/{project_id}/assets/{asset_id}/restore-original")
def restore_original_asset(project_id: str, asset_id: str) -> dict[str, Any]:
    with connect() as conn:
        get_project_or_404(conn, project_id)
        asset = row_to_dict(conn.execute("SELECT * FROM assets WHERE id=? AND project_id=?", (asset_id, project_id)).fetchone())
        if not asset:
            raise HTTPException(status_code=404, detail="Asset not found")
        conn.execute("UPDATE asset_images SET is_selected=0 WHERE project_id=? AND asset_id=?", (project_id, asset_id))
        data = json.loads(asset["payload_json"])
        data["目标资产来源"] = "沿用原貌"
        data["目标生成描述"] = ""
        data["用户编辑资产图生成提示词"] = ""
        conn.execute("UPDATE assets SET status='待确认',payload_json=? WHERE id=? AND project_id=?", (json.dumps(data, ensure_ascii=False), asset_id, project_id))
        mark_storyboards_asset_stale(conn, project_id, asset, "目标资产切换为沿用原貌")
        return get_asset_response(conn, project_id, asset_id)


@app.post("/api/projects/{project_id}/assets/{asset_id}/generate-image")
def generate_asset_image(project_id: str, asset_id: str, payload: AssetImageRequest) -> dict[str, Any]:
    with connect() as conn:
        project = get_project_or_404(conn, project_id)
        asset = row_to_dict(
            conn.execute(
                "SELECT * FROM assets WHERE id = ? AND project_id = ?",
                (asset_id, project_id),
            ).fetchone()
        )
        if not asset:
            raise HTTPException(status_code=404, detail="Asset not found")
        running_job = conn.execute(
            "SELECT 1 FROM asset_generation_jobs WHERE project_id=? AND asset_id=? AND status='running' LIMIT 1",
            (project_id, asset_id),
        ).fetchone()
        if running_job:
            raise HTTPException(status_code=409, detail="当前资产正在生成，请等待本次生成完成。")
        asset_payload = json.loads(asset["payload_json"])
        refresh_role_source_review_profile(asset_payload)
        direct_final_prompt = payload.finalPrompt is not None or (payload.prompt is None and bool(str(asset_payload.get("用户编辑资产图生成提示词") or "").strip()))
        prompt = str(payload.finalPrompt if payload.finalPrompt is not None else asset_payload.get("用户编辑资产图生成提示词") or "") if direct_final_prompt else ""
        localized_profile = asset_payload.get("localizedProfile") if isinstance(asset_payload.get("localizedProfile"), dict) else {}
        stored_manual_prompt = str(asset_payload.get("用户编辑资产图生成提示词") or "")
        sanitized_stored_manual_prompt = strip_resolved_source_review_status_block(stored_manual_prompt, localized_profile)
        if sanitized_stored_manual_prompt != stored_manual_prompt:
            history = asset_payload.get("resolvedStatusPromptArchive") if isinstance(asset_payload.get("resolvedStatusPromptArchive"), list) else []
            if not any(item.get("prompt") == stored_manual_prompt for item in history if isinstance(item, dict)):
                history.append({"prompt": stored_manual_prompt, "removedSystemBlock": "【源事实待核对：自动生图已锁定】", "reason": "all named source-review paths are now resolved or explicitly non-blocking"})
            asset_payload["resolvedStatusPromptArchive"] = history[-10:]
        if direct_final_prompt:
            if not prompt.strip():
                raise HTTPException(status_code=422, detail="完整资产提示词不能为空，请填写可直接生图的完整描述。")
            asset_payload["用户编辑资产图生成提示词"] = prompt
            asset_payload["目标资产来源"] = "手动生成"
        elif payload.prompt is not None:
            # Legacy clients send a visual supplement in `prompt`; keep that contract
            # and compile it through the automatic template as before.
            asset_payload["用户编辑资产图生成提示词"] = ""
            normalized_override, should_update_override = normalize_asset_visual_override(
                payload.prompt, str(asset_payload.get("目标生成描述") or "")
            )
            if should_update_override:
                asset_payload["目标生成描述"] = normalized_override
            asset_payload["目标资产来源"] = "手动生成"
        if not direct_final_prompt:
            prompt = build_project_asset_prompt(asset["asset_type"], asset_payload, project)
        prompt = enforce_role_cg_fixed_suffix(prompt, asset["asset_type"], project)
        if direct_final_prompt:
            automatic_prompt = build_project_asset_prompt(asset["asset_type"], asset_payload, project)
            asset_payload["用户编辑资产图生成提示词"] = "" if prompt == automatic_prompt else prompt
        if not prompt.strip():
            raise HTTPException(status_code=422, detail="资产提示词不能为空，请填写或编译可发送的提示词。")
        if direct_final_prompt or payload.prompt is not None:
            conn.execute("UPDATE assets SET payload_json=? WHERE id=? AND project_id=?", (json.dumps(asset_payload, ensure_ascii=False), asset_id, project_id))
            conn.commit()
        job_id = new_id("asset_job")
        template_version = ASSET_PROMPT_TEMPLATE_VERSION
        conn.execute("INSERT INTO asset_generation_jobs (id,project_id,asset_id,remake_mode,template_version,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                     (job_id,project_id,asset_id,project.get("remake_mode", "overseas_localization"),template_version,"running",now_iso(),now_iso()))
        conn.commit()
        try:
            agent = prepare_agent("assetImageGeneration")
        except ProviderError as exc:
            conn.execute("UPDATE asset_generation_jobs SET status='failed',updated_at=? WHERE id=?", (now_iso(), job_id))
            conn.commit()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            conn.execute("UPDATE asset_generation_jobs SET status='failed',updated_at=? WHERE id=?", (now_iso(), job_id))
            conn.commit()
            raise HTTPException(status_code=500, detail=f"资产生图准备失败：{exc}") from exc
        if not 1 <= payload.count <= 4:
            conn.execute("UPDATE asset_generation_jobs SET status='failed',updated_at=? WHERE id=?", (now_iso(), job_id))
            conn.commit()
            raise HTTPException(status_code=422, detail="每次可生成 1 到 4 张资产图。")
        aspect_ratio = payload.aspectRatio.strip()
        if aspect_ratio not in ASSET_IMAGE_ASPECT_SIZES:
            conn.execute("UPDATE asset_generation_jobs SET status='failed',updated_at=? WHERE id=?", (now_iso(), job_id))
            conn.commit()
            raise HTTPException(status_code=422, detail="资产图当前仅支持 16:9 比例。")
        model_name = str(payload.model or agent.get("model_name") or "").strip()
        if not model_name or (payload.model is not None and model_name not in ASSET_IMAGE_MODELS):
            conn.execute("UPDATE asset_generation_jobs SET status='failed',updated_at=? WHERE id=?", (now_iso(), job_id))
            conn.commit()
            raise HTTPException(status_code=422, detail="请选择支持的 Seedream 图片模型。")
        image_size = ASSET_IMAGE_ASPECT_SIZES[aspect_ratio]
        image_dir = PROJECTS_DIR / project_id / "assets" / asset["asset_type"]
        image_dir.mkdir(parents=True, exist_ok=True)
        generated_images: list[tuple[Path, dict[str, Any]]] = []
        if agent.get("mode") == "mock":
            for image_index in range(payload.count):
                image_id = new_id("asset_image")
                image_path = image_dir / f"{image_id}.svg"
                image_path.write_text(render_mock_asset_svg(asset, prompt), encoding="utf-8")
                image_payload = {
                    "mock": True,
                    "agent": agent,
                    "model": model_name,
                    "aspectRatio": aspect_ratio,
                    "size": image_size,
                    "generationIndex": image_index + 1,
                    "generationCount": payload.count,
                    "note": "assetImageGeneration 未启用，使用本地 mock 占位图。",
                    "remakeMode": project.get("remake_mode", "overseas_localization"),
                    "templateVersion": ASSET_PROMPT_TEMPLATE_VERSION,
                    "targetDescription": asset_payload.get("目标生成描述", ""),
                    "promptSnapshot": prompt,
                }
                generated_images.append((image_path, image_payload))
        else:
            try:
                config = load_models_config()
                provider = get_model_provider(config)
                for image_index in range(payload.count):
                    image_id = new_id("asset_image")
                    image_result = provider.generate_image(
                        model_name=model_name,
                        prompt=enforce_role_cg_fixed_suffix(prompt, asset["asset_type"], project),
                        size=image_size,
                        response_format="url",
                        sequential_image_generation="disabled",
                        # Asset sheets are clean reference plates; keep the global
                        # watermark setting for other image-generation workflows.
                        watermark=False,
                        stream=False,
                    )
                    mime_type = str(image_result.get("mime_type") or "image/png")
                    image_path = image_dir / f"{image_id}{image_extension(mime_type)}"
                    image_path.write_bytes(image_result["image_bytes"])
                    image_payload = {
                        "mock": False,
                        "agent": agent,
                        "model": image_result.get("model") or model_name,
                        "source_url": image_result.get("image_url", ""),
                        "aspectRatio": aspect_ratio,
                        "size": image_result.get("size", image_size),
                        "generationIndex": image_index + 1,
                        "generationCount": payload.count,
                        "mime_type": mime_type,
                        "raw": image_result.get("raw", {}),
                        "remakeMode": project.get("remake_mode", "overseas_localization"),
                        "templateVersion": ASSET_PROMPT_TEMPLATE_VERSION,
                        "targetDescription": asset_payload.get("目标生成描述", ""),
                        "promptSnapshot": prompt,
                    }
                    generated_images.append((image_path, image_payload))
            except ProviderError as exc:
                for generated_path, _ in generated_images:
                    generated_path.unlink(missing_ok=True)
                conn.execute("UPDATE asset_generation_jobs SET status='failed',updated_at=? WHERE id=?", (now_iso(), job_id))
                conn.commit()
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except Exception as exc:
                for generated_path, _ in generated_images:
                    generated_path.unlink(missing_ok=True)
                conn.execute("UPDATE asset_generation_jobs SET status='failed',updated_at=? WHERE id=?", (now_iso(), job_id))
                conn.commit()
                raise HTTPException(status_code=500, detail=f"资产生图失败：{exc}") from exc
        current_project = get_project_or_404(conn, project_id)
        if current_project.get("remake_mode", "overseas_localization") != project.get("remake_mode", "overseas_localization"):
            for generated_path, _ in generated_images:
                generated_path.unlink(missing_ok=True)
            conn.execute("UPDATE asset_generation_jobs SET status='stale',updated_at=? WHERE id=?", (now_iso(), job_id))
            conn.commit()
            raise HTTPException(status_code=409, detail="项目模式已变化，旧模式生成结果已丢弃，请在当前项目重新生成。")
        conn.execute(
            "UPDATE asset_images SET is_selected = 0 WHERE asset_id = ?",
            (asset_id,),
        )
        for image_index, (image_path, image_payload) in enumerate(generated_images):
            conn.execute(
                """
                INSERT INTO asset_images
                (id, project_id, asset_id, status, prompt, file_path, is_selected, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    image_path.stem,
                    project_id,
                    asset_id,
                    "成功",
                    prompt,
                    str(image_path),
                    1 if image_index == len(generated_images) - 1 else 0,
                    json.dumps({**image_payload, "generationJobId": job_id}, ensure_ascii=False),
                    now_iso(),
                ),
            )
        conn.execute(
            "UPDATE assets SET status = ? WHERE id = ? AND project_id = ?",
            ("已生成资产图", asset_id, project_id),
        )
        conn.execute("UPDATE assets SET payload_json=? WHERE id=? AND project_id=?", (json.dumps(asset_payload, ensure_ascii=False), asset_id, project_id))
        conn.execute("UPDATE asset_generation_jobs SET status='succeeded',updated_at=? WHERE id=?", (now_iso(), job_id))
        asset["payload_json"] = json.dumps(asset_payload, ensure_ascii=False)
        mark_storyboards_asset_stale(conn, project_id, asset, "目标资产图已更新")
        return get_asset_response(conn, project_id, asset_id)


@app.post("/api/projects/{project_id}/assets/{asset_id}/upload-image")
async def upload_asset_image(project_id: str, asset_id: str, file: UploadFile = File(...)) -> dict[str, Any]:
    allowed_types = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
    mime_type = (file.content_type or "").lower()
    if mime_type not in allowed_types:
        raise HTTPException(status_code=415, detail="仅支持 PNG、JPEG、WebP 图片")
    content = await file.read(20 * 1024 * 1024 + 1)
    if not content or len(content) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="图片不能为空且不能超过 20 MB")
    signatures = {"image/png": b"\x89PNG\r\n\x1a\n", "image/jpeg": b"\xff\xd8\xff", "image/webp": b"RIFF"}
    if not content.startswith(signatures[mime_type]) or (mime_type == "image/webp" and content[8:12] != b"WEBP"):
        raise HTTPException(status_code=415, detail="图片文件内容与格式不匹配")
    try:
        with Image.open(BytesIO(content)) as decoded:
            decoded.verify()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise HTTPException(status_code=415, detail="图片无法解码，请重新选择有效的 PNG、JPEG 或 WebP 文件") from exc
    with connect() as conn:
        project = get_project_or_404(conn, project_id)
        asset = row_to_dict(conn.execute("SELECT * FROM assets WHERE id=? AND project_id=?", (asset_id, project_id)).fetchone())
        if not asset:
            raise HTTPException(status_code=404, detail="Asset not found")
        image_id = new_id("asset_image")
        image_dir = PROJECTS_DIR / project_id / "assets" / asset["asset_type"]
        image_dir.mkdir(parents=True, exist_ok=True)
        image_path = image_dir / f"{image_id}{allowed_types[mime_type]}"
        image_path.write_bytes(content)
        image_payload = {"mock": False, "source": "user_upload", "filename": Path(file.filename or "asset").name, "mime_type": mime_type, "remakeMode": project.get("remake_mode", "overseas_localization")}
        timestamp = now_iso()
        conn.execute("UPDATE asset_images SET is_selected=0 WHERE project_id=? AND asset_id=?", (project_id, asset_id))
        conn.execute("INSERT INTO asset_images (id,project_id,asset_id,status,prompt,file_path,is_selected,payload_json,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                     (image_id,project_id,asset_id,"成功","用户上传目标资产图",str(image_path),1,json.dumps(image_payload,ensure_ascii=False),timestamp))
        current = json.loads(asset["payload_json"])
        current["目标资产来源"] = "用户上传"
        conn.execute("UPDATE assets SET status='已生成资产图',payload_json=? WHERE id=? AND project_id=?", (json.dumps(current,ensure_ascii=False),asset_id,project_id))
        asset["payload_json"] = json.dumps(current, ensure_ascii=False)
        mark_storyboards_asset_stale(conn, project_id, asset, "目标资产图已上传并采用")
        return get_asset_response(conn, project_id, asset_id)


@app.post("/api/projects/{project_id}/assets/{asset_id}/upload-voice-reference")
async def upload_asset_voice_reference(project_id: str, asset_id: str, file: UploadFile = File(...)) -> dict[str, Any]:
    extension = Path(file.filename or "").suffix.lower()
    if extension not in {".wav", ".mp3", ".m4a"}:
        raise HTTPException(status_code=415, detail="仅支持 wav、mp3、m4a 格式的参考音频")
    content = await file.read(20 * 1024 * 1024 + 1)
    if not content or len(content) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="参考音频不能为空且不能超过 20 MB")
    is_wav = content.startswith(b"RIFF") and content[8:12] == b"WAVE"
    is_mp3 = content.startswith(b"ID3") or (content[0] == 0xFF and (content[1] & 0xE0) == 0xE0)
    is_m4a = len(content) > 8 and content[4:8] == b"ftyp"
    if (extension == ".wav" and not is_wav) or (extension == ".mp3" and not is_mp3) or (extension == ".m4a" and not is_m4a):
        raise HTTPException(status_code=415, detail="音频文件内容与格式不匹配，请重新选择有效的音频文件")
    with connect() as conn:
        get_project_or_404(conn, project_id)
        asset = conn.execute("SELECT id FROM assets WHERE id=? AND project_id=?", (asset_id, project_id)).fetchone()
        if not asset:
            raise HTTPException(status_code=404, detail="Asset not found")
    audio_dir = PROJECTS_DIR / project_id / "voice_references"
    audio_dir.mkdir(parents=True, exist_ok=True)
    audio_path = audio_dir / f"{new_id('voice_ref')}{extension}"
    audio_path.write_bytes(content)
    return {
        "file_url": data_file_url(str(audio_path)),
        "file_name": Path(file.filename or f"reference{extension}").name,
        "size": len(content),
        "mime_type": (file.content_type or "").lower(),
    }


@app.post("/api/projects/{project_id}/assets/{asset_id}/select-image")
def select_asset_image(project_id: str, asset_id: str, payload: SelectAssetImageRequest) -> dict[str, Any]:
    with connect() as conn:
        get_project_or_404(conn, project_id)
        image = row_to_dict(
            conn.execute(
                "SELECT * FROM asset_images WHERE id = ? AND asset_id = ? AND project_id = ?",
                (payload.imageId, asset_id, project_id),
            ).fetchone()
        )
        if not image:
            raise HTTPException(status_code=404, detail="Asset image not found")
        conn.execute("UPDATE asset_images SET is_selected = 0 WHERE project_id=? AND asset_id = ?", (project_id, asset_id))
        conn.execute("UPDATE asset_images SET is_selected = 1 WHERE id = ?", (payload.imageId,))
        asset = row_to_dict(conn.execute("SELECT * FROM assets WHERE id=? AND project_id=?", (asset_id, project_id)).fetchone())
        project = get_project_or_404(conn, project_id)
        if project.get("remake_mode") == "asset_replacement":
            image_payload = json.loads(image.get("payload_json") or "{}")
            source = "用户上传" if image_payload.get("source") == "user_upload" else "手动生成"
            asset_payload = json.loads(asset["payload_json"])
            asset_payload["目标资产来源"] = source
            conn.execute("UPDATE assets SET payload_json=? WHERE id=? AND project_id=?", (json.dumps(asset_payload, ensure_ascii=False), asset_id, project_id))
        mark_storyboards_asset_stale(conn, project_id, asset, "当前采用目标图已切换")
        return get_asset_response(conn, project_id, asset_id)


@app.get("/api/projects/{project_id}/storyboards")
def list_storyboards(project_id: str, episode_id: str | None = None) -> dict[str, Any]:
    with connect() as conn:
        project = get_project_or_404(conn, project_id)
        if episode_id:
            rows = conn.execute(
                "SELECT * FROM storyboards WHERE project_id = ? AND episode_id = ? ORDER BY created_at",
                (project_id, episode_id),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM storyboards WHERE project_id = ? ORDER BY created_at",
                (project_id,),
            ).fetchall()
        storyboards = rows_to_dicts(rows)
        latest_task_by_episode: dict[str, dict[str, Any]] = {}
        for item in storyboards:
            if item["episode_id"] not in latest_task_by_episode:
                task = conn.execute(
                    "SELECT status, message FROM tasks WHERE project_id=? AND episode_id=? AND task_type='storyboard_generation' ORDER BY created_at DESC LIMIT 1",
                    (project_id, item["episode_id"]),
                ).fetchone()
                latest_task_by_episode[item["episode_id"]] = dict(task) if task else {}
        for item in storyboards:
            item["payload"] = json.loads(item.pop("payload_json"))
            if item["payload"].get("使用回退占位") is True:
                latest_task = latest_task_by_episode.get(item["episode_id"], {})
                failed = latest_task.get("status") == "失败"
                item["analysis_state"] = "分析失败" if failed else "待分析"
                item["analysis_message"] = latest_task.get("message", "") if failed else "当前分镜为未分析占位，请重新分析视频或编辑补入真实镜头事实。"
                item["status"] = item["analysis_state"]
            apply_storyboard_project_prompt(conn, project, item)
        return {"storyboards": storyboards}


def get_storyboard_response(conn: sqlite3.Connection, project_id: str, storyboard_id: str) -> dict[str, Any]:
    item = row_to_dict(
        conn.execute(
            "SELECT * FROM storyboards WHERE id = ? AND project_id = ?",
            (storyboard_id, project_id),
        ).fetchone()
    )
    if not item:
        raise HTTPException(status_code=404, detail="Storyboard not found")
    item["payload"] = json.loads(item.pop("payload_json"))
    project = get_project_or_404(conn, project_id)
    apply_storyboard_project_prompt(conn, project, item)
    return {"storyboard": item}


def _alias_occurs_standalone(text: str, token: str) -> bool:
    """仅当别名以完整形式出现（而非更长展示别名的前缀）时才算命中。

    展示变量形如 @23号球员-CHAR001SUB01，旧别名 @23号球员 是其前缀；
    简单子串匹配会把旧别名误判为存在并造成歧义误报。这里要求别名出现后
    不是紧跟 "-字母/数字" 的续接，否则该次出现归属于更长的展示别名。
    """
    start = 0
    while True:
        index = text.find(token, start)
        if index < 0:
            return False
        following = text[index + len(token): index + len(token) + 2]
        if re.match(r"-[A-Za-z0-9]", following):
            start = index + 1
            continue
        return True


def recompute_active_storyboard_reference_review(
    conn: sqlite3.Connection,
    project_id: str,
    episode_id: str,
    payload: dict[str, Any],
    current_body: str,
) -> None:
    """Project historical reference-review records onto the current editable draft.

    Historical review entries remain available for audit, but only tokens still
    present in the current body or explicit reference list can block generation.
    Assets are scoped to the storyboard episode because stable variables such as
    {{PROP_001}} are intentionally local to each episode.
    """
    previous = payload.get("资产引用待核对")
    previous_reviews = [item for item in previous if isinstance(item, dict)] if isinstance(previous, list) else []
    explicit_refs = payload.get("引用资产变量") if isinstance(payload.get("引用资产变量"), list) else []
    active_text = current_body + "\n" + "\n".join(str(value) for value in explicit_refs)

    rows = (
        conn.execute("SELECT * FROM assets WHERE project_id=? AND episode_id=? ORDER BY created_at", (project_id, episode_id)).fetchall()
        if episode_id
        else conn.execute("SELECT * FROM assets WHERE project_id=? ORDER BY created_at", (project_id,)).fetchall()
    )
    candidates_by_token: dict[str, dict[str, dict[str, Any]]] = {}
    known_tokens: set[str] = set()
    for row in rows:
        asset = row_to_dict(row)
        raw_payload = json.loads(asset.pop("payload_json") or "{}")
        asset_payload = raw_payload if isinstance(raw_payload, dict) else {}
        asset["payload"] = asset_payload
        attach_display_variable(asset)
        aliases = [
            asset.get("variable"), asset.get("display_variable"), asset.get("source_variable_alias"),
            asset_payload.get("展示资产变量"), asset_payload.get("旧展示资产变量"),
            asset_payload.get("源变量别名"),
        ]
        historic_display_aliases = asset_payload.get("历史展示变量别名")
        if isinstance(historic_display_aliases, list):
            aliases.extend(historic_display_aliases)
        for value in aliases:
            token = str(value or "").strip()
            if not token:
                continue
            known_tokens.add(token)
            candidates_by_token.setdefault(token, {})[str(asset.get("id") or token)] = asset

    # Match project-known aliases verbatim, then recognize unresolved ASCII
    # @mentions / canonical variables without consuming adjacent Chinese prose.
    present_tokens = {token for token in known_tokens if _alias_occurs_standalone(active_text, token)}
    present_tokens.update(re.findall(r"\{\{[A-Za-z0-9_-]+\}\}|@[A-Za-z][A-Za-z0-9_-]*", active_text))
    active_reviews: list[dict[str, Any]] = []
    for token in sorted(present_tokens):
        candidates = list(candidates_by_token.get(token, {}).values())
        distinct_variables = sorted({str(item.get("variable") or "") for item in candidates})
        if len(candidates) == 1 and str(candidates[0].get("variable") or "") == token:
            continue
        if len(distinct_variables) == 1 and distinct_variables[0]:
            # 同一稳定变量在历史归并中可能留下多行副本；映射目标唯一，不算歧义。
            continue
        if len(candidates) > 1:
            active_reviews.append({
                "token": token,
                "candidateVariables": sorted(str(item.get("variable") or "") for item in candidates),
                "reason": "当前正文或显式引用中的别名对应多个稳定资产，未猜测匹配对象",
            })
        elif not candidates:
            active_reviews.append({
                "token": token,
                "candidateVariables": [],
                "reason": "当前正文或显式引用中的资产标记未能关联到当前项目资产",
            })

    historical = payload.get("历史资产引用待核对")
    historical_reviews = [item for item in historical if isinstance(item, dict)] if isinstance(historical, list) else []
    active_keys = {(item.get("token"), tuple(item.get("candidateVariables", []))) for item in active_reviews}
    for item in previous_reviews:
        key = (item.get("token"), tuple(item.get("candidateVariables", [])))
        if key not in active_keys and item not in historical_reviews:
            historical_reviews.append(item)
    if historical_reviews:
        payload["历史资产引用待核对"] = historical_reviews
    else:
        payload.pop("历史资产引用待核对", None)
    payload["资产引用待核对"] = active_reviews
    check = payload.get("资产映射校验") if isinstance(payload.get("资产映射校验"), dict) else {}
    payload["资产映射校验"] = {**check, "待核对引用": active_reviews}


def apply_storyboard_project_prompt(conn: sqlite3.Connection, project: dict[str, Any], storyboard: dict[str, Any]) -> None:
    payload = storyboard.get("payload") if isinstance(storyboard.get("payload"), dict) else {}
    if payload.get("使用回退占位") is True:
        storyboard["prompt"] = ""
        return
    style = project.get("storyboard_visual_style") or ("cg_3d" if project.get("remake_mode") == "cg_3d" else "live_action")
    if project.get("remake_mode") == "cg_3d":
        style = "cg_3d"
    constraint = STORYBOARD_STYLE_TEMPLATES.get(style, STORYBOARD_STYLE_TEMPLATES["live_action"])
    stored_source = payload.get("源分镜文本")
    source_text = str(stored_source if isinstance(stored_source, str) and stored_source else storyboard.get("prompt") or "")
    original_source = source_text
    if not (isinstance(stored_source, str) and stored_source):
        # Older records may only have the rendered prompt. Strip only known
        # composer suffixes; preserve all narrative words and layout nouns.
        markers = ("当前项目确认资产：", "保持海外本土化项目", "国内重制：", "分镜画面类型约束：")
        source_lines = original_source.splitlines()
        for index, line in enumerate(source_lines):
            if line.startswith(markers):
                original_source = "\n".join(source_lines[:index]).rstrip()
                break
        else:
            for old_template in STORYBOARD_STYLE_TEMPLATES.values():
                suffix = "\n" + old_template
                if original_source.endswith(suffix):
                    original_source = original_source[:-len(suffix)].rstrip()
                    break
    edited_body = payload.get("分镜生成正文")
    base = str(edited_body if isinstance(edited_body, str) else original_source).strip()
    episode_id = str(storyboard.get("episode_id") or "").strip()
    recompute_active_storyboard_reference_review(conn, str(project["id"]), episode_id, payload, base)
    # An exact legacy style phrase at the beginning is an explicit style field;
    # don't erase similarly named words inside actions, dialogue, or scenery.
    if style == "cg_3d":
        base = re.sub(r"^(?:真人实拍电影质感|照片级真实|真实摄影质感|photorealistic|photographic skin)[，,；;。]?\s*", "", base, flags=re.IGNORECASE)
    else:
        base = re.sub(r"^(?:三维游戏CG过场动画质感|高端三维游戏CG|三维CG渲染|3D render|CG style)[，,；;。]?\s*", "", base, flags=re.IGNORECASE)
    refs = payload.get("引用资产变量") if isinstance(payload.get("引用资产变量"), list) else []
    asset_details: list[str] = []
    target_asset_snapshot: list[dict[str, Any]] = []
    for ref in refs:
        rows = (
            conn.execute("SELECT * FROM assets WHERE project_id=? AND episode_id=? ORDER BY created_at", (project["id"], episode_id)).fetchall()
            if episode_id
            else conn.execute("SELECT * FROM assets WHERE project_id=? ORDER BY created_at", (project["id"],)).fetchall()
        )
        matched = None
        for row in rows:
            candidate = dict(row)
            candidate_payload = json.loads(candidate["payload_json"])
            candidate["payload"] = candidate_payload
            attach_display_variable(candidate)
            if str(ref) in {str(candidate.get("variable") or ""), str(candidate.get("display_variable") or ""), str(candidate.get("localized_name") or "")}:
                matched = candidate
                break
        if not matched:
            continue
        asset = matched
        ap = asset["payload"]
        selected_image = conn.execute("SELECT id,file_path,payload_json FROM asset_images WHERE project_id=? AND asset_id=? AND is_selected=1 ORDER BY created_at DESC LIMIT 1", (project["id"], asset["id"])).fetchone()
        effective = resolve_effective_asset_reference(asset, project, selected_image)
        target_description = pick_payload_text(ap, ["目标生成描述"])
        effective_source = effective["effectiveAssetSource"]
        source_image_path = str(ap.get("原资产图路径") or "")
        image_path = effective["imagePath"]
        target_name = effective["name"]
        if effective_source == "target" and project.get("remake_mode") == "asset_replacement" and selected_image and not target_description:
            description = "以当前已确认目标图为外观权威；不沿用原片外观描述"
        elif effective_source == "target" and project.get("remake_mode") == "asset_replacement" and not effective["imageId"]:
            description = target_description or "替换目标尚未设置；缺少已确认目标图，不能视为就绪"
        else:
            description = effective["description"]
        source_key = str(ap.get("源AssetKey") or asset["id"])
        if effective_source == "target_draft":
            image_status = "；目标画像草稿待审核，原片图仅作源观察，不作为目标身份参考" if not image_path else "；目标画像草稿待审核，当前选中图仍待核对"
        elif effective_source == "target" and project.get("remake_mode") == "asset_replacement" and not effective["imageId"]:
            image_status = "；缺少目标图，当前替换资产未就绪"
        elif image_path:
            image_status = "；当前确认资产图已关联" if effective_source == "target" and effective["imageId"] else "；原片资产图已关联"
        elif effective_source == "source":
            image_status = "；缺少原片资产图，当前沿用原貌未就绪"
        else:
            image_status = "；未设置目标图"
        asset_details.append(f"{ref}={target_name}；目标外观：{description}{image_status}")
        target_asset_snapshot.append({
            "reference": str(ref),
            "sourceAssetKey": source_key,
            "projectAssetId": str(asset["id"]),
            "targetName": str(target_name or ""),
            "targetDescription": str(description or ""),
            "effectiveAssetSource": effective_source,
            "imageId": effective["imageId"],
            "imagePath": image_path,
            "imageSource": effective["imageSource"],
        })
    source_language = project.get("source_language") or "原片语言"
    target_country = str(project.get("target_country") or "").strip()
    target_language = str(project.get("target_language") or "").strip()
    has_foreign_target = target_country not in {"", "中国", "中国大陆"} and target_language not in {"", "沿用原片语言", "原片语言", "保留原片语言，不翻译"}
    if has_foreign_target:
        language_rule = f"按目标国家（{target_country}）和目标语言（{target_language}）本地化剧情文本及可见场景文字；画面类型只决定视觉呈现，不取消地域与语言适配。源镜头中的招牌、屏幕字和姓名条仅供语义与版式参考，目标成片应以目标语言及当前目标角色名替换文字并保持原有位置与构图。保留源分镜原始观察作为证据，不机械删除所有汉字，不改写未要求翻译的对白含义。"
    elif project.get("remake_mode") == "overseas_localization":
        language_rule = "保持海外本土化项目已选目标国家和目标语言规则；原始画面文字保留为观察证据，目标可见文字按选定语言适配。"
    else:
        language_rule = f"国内重制：沿用原片语言（{source_language}），不进行未经选择的翻译或地域本土化。"
    addition = "\n".join(part for part in ["当前项目确认资产：" + "；".join(asset_details) if asset_details else "", language_rule, f"分镜画面类型约束：{constraint}"] if part)
    final_prompt = (base + "\n" + addition).strip()
    payload["源分镜文本"] = original_source
    payload["目标资产映射快照"] = target_asset_snapshot
    payload["最终提示词快照"] = final_prompt
    payload["分镜画面类型"] = style
    payload["分镜画面模板版本"] = STORYBOARD_STYLE_TEMPLATE_VERSION
    payload["待更新原因"] = payload.get("待更新原因", "")
    storyboard["payload"] = payload
    storyboard["prompt"] = final_prompt


@app.post("/api/projects/{project_id}/storyboards")
def create_storyboard(project_id: str, payload: StoryboardCreate) -> dict[str, Any]:
    with connect() as conn:
        get_project_or_404(conn, project_id)
        episode = row_to_dict(
            conn.execute(
                "SELECT * FROM episodes WHERE id = ? AND project_id = ?",
                (payload.episodeId, project_id),
            ).fetchone()
        )
        if not episode:
            raise HTTPException(status_code=404, detail="Episode not found")
        source: dict[str, Any] | None = None
        if payload.duplicateFromId:
            source = row_to_dict(
                conn.execute(
                    "SELECT * FROM storyboards WHERE id = ? AND project_id = ?",
                    (payload.duplicateFromId, project_id),
                ).fetchone()
            )
            if not source:
                raise HTTPException(status_code=404, detail="Source storyboard not found")
        source_payload = json.loads(source["payload_json"]) if source else {}
        title = payload.title or (f"{source['title']} 副本" if source else "新增分镜")
        time_range = payload.timeRange or (source["time_range"] if source else "00:00:00-00:00:08")
        prompt = payload.prompt or (source["prompt"] if source else "请在此输入当前分镜的视频生成提示词。")
        storyboard_payload = dict(source_payload) if isinstance(source_payload, dict) else {}
        storyboard_payload.setdefault("引用资产变量", [])
        storyboard_payload.setdefault("镜头列表", [])
        storyboard_id = new_id("storyboard")
        conn.execute(
            """
            INSERT INTO storyboards
            (id, project_id, episode_id, title, time_range, prompt, payload_json, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                storyboard_id,
                project_id,
                payload.episodeId,
                title,
                time_range,
                prompt,
                json.dumps(storyboard_payload, ensure_ascii=False),
                "可生成",
                now_iso(),
            ),
        )
        return get_storyboard_response(conn, project_id, storyboard_id)


@app.patch("/api/projects/{project_id}/storyboards/{storyboard_id}")
def update_storyboard(project_id: str, storyboard_id: str, payload: StoryboardUpdate) -> dict[str, Any]:
    with connect() as conn:
        # Serialize blur saves and asset-reference toggles so concurrent PATCH
        # requests merge into the latest payload instead of dropping a field.
        conn.execute("BEGIN IMMEDIATE")
        get_project_or_404(conn, project_id)
        current = row_to_dict(
            conn.execute(
                "SELECT * FROM storyboards WHERE id = ? AND project_id = ?",
                (storyboard_id, project_id),
            ).fetchone()
        )
        if not current:
            raise HTTPException(status_code=404, detail="Storyboard not found")
        current_payload = json.loads(current["payload_json"])
        if payload.payload:
            # None 表示显式删除该字段（deep_merge_dict 会跳过 None，需要单独处理），
            # 用于清除首帧/尾帧参考等可移除的可选配置。
            removals = [key for key, value in payload.payload.items() if value is None]
            for key in removals:
                payload.payload.pop(key)
                current_payload.pop(key, None)
            deep_merge_dict(current_payload, payload.payload)
        if payload.generatedBody is not None:
            current_payload["分镜生成正文"] = payload.generatedBody
            if current_payload.get("使用回退占位") and "回退占位" not in payload.generatedBody and "待分析（mock" not in payload.generatedBody:
                current_payload["使用回退占位"] = False
                current_payload["回退占位已由用户补全"] = True
        elif payload.prompt is not None:
            # Keep the legacy API contract for older clients; the workspace uses
            # generatedBody so editing never mutates the reverse-analysis source.
            current_payload["源分镜文本"] = payload.prompt
        conn.execute(
            """
            UPDATE storyboards
            SET title = ?, time_range = ?, prompt = ?, payload_json = ?
            WHERE id = ? AND project_id = ?
            """,
            (
                payload.title or current["title"],
                payload.timeRange or current["time_range"],
                payload.prompt if payload.prompt is not None else current["prompt"],
                json.dumps(current_payload, ensure_ascii=False),
                storyboard_id,
                project_id,
            ),
        )
        conn.execute("UPDATE storyboards SET status='待更新' WHERE id=? AND project_id=?", (storyboard_id, project_id))
        response = get_storyboard_response(conn, project_id, storyboard_id)
        conn.execute("UPDATE storyboards SET prompt=?,payload_json=? WHERE id=? AND project_id=?", (response["storyboard"]["prompt"], json.dumps(response["storyboard"]["payload"], ensure_ascii=False), storyboard_id, project_id))
        return response


@app.delete("/api/projects/{project_id}/storyboards/{storyboard_id}")
def delete_storyboard(project_id: str, storyboard_id: str) -> dict[str, Any]:
    with connect() as conn:
        get_project_or_404(conn, project_id)
        existing = row_to_dict(
            conn.execute(
                "SELECT * FROM storyboards WHERE id = ? AND project_id = ?",
                (storyboard_id, project_id),
            ).fetchone()
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Storyboard not found")
        conn.execute("DELETE FROM video_generations WHERE storyboard_id = ? AND project_id = ?", (storyboard_id, project_id))
        conn.execute("DELETE FROM storyboards WHERE id = ? AND project_id = ?", (storyboard_id, project_id))
        return {"ok": True, "deletedId": storyboard_id}


@app.post("/api/projects/{project_id}/storyboards/{storyboard_id}/move")
def move_storyboard(project_id: str, storyboard_id: str, payload: StoryboardMoveRequest) -> dict[str, Any]:
    with connect() as conn:
        get_project_or_404(conn, project_id)
        current = row_to_dict(
            conn.execute(
                "SELECT * FROM storyboards WHERE id = ? AND project_id = ?",
                (storyboard_id, project_id),
            ).fetchone()
        )
        if not current:
            raise HTTPException(status_code=404, detail="Storyboard not found")
        if payload.direction == "up":
            neighbor = row_to_dict(
                conn.execute(
                    """
                    SELECT * FROM storyboards
                    WHERE project_id = ? AND episode_id = ? AND created_at < ?
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (project_id, current["episode_id"], current["created_at"]),
                ).fetchone()
            )
        elif payload.direction == "down":
            neighbor = row_to_dict(
                conn.execute(
                    """
                    SELECT * FROM storyboards
                    WHERE project_id = ? AND episode_id = ? AND created_at > ?
                    ORDER BY created_at ASC LIMIT 1
                    """,
                    (project_id, current["episode_id"], current["created_at"]),
                ).fetchone()
            )
        else:
            raise HTTPException(status_code=400, detail="direction must be up or down")
        if neighbor:
            conn.execute("UPDATE storyboards SET created_at = ? WHERE id = ?", (neighbor["created_at"], current["id"]))
            conn.execute("UPDATE storyboards SET created_at = ? WHERE id = ?", (current["created_at"], neighbor["id"]))
        return get_storyboard_response(conn, project_id, storyboard_id)


@app.get("/api/projects/{project_id}/video-generations")
def list_video_generations(project_id: str) -> dict[str, Any]:
    with connect() as conn:
        get_project_or_404(conn, project_id)
        rows = conn.execute(
            "SELECT * FROM video_generations WHERE project_id = ? ORDER BY created_at DESC",
            (project_id,),
        ).fetchall()
        generations = rows_to_dicts(rows)
        for item in generations:
            item["payload"] = json.loads(item.pop("payload_json"))
            item["output_url"] = data_file_url(str(item.get("output_path") or ""))
        return {"videoGenerations": generations}


@app.post("/api/projects/{project_id}/video-generations/{generation_id}/select")
def select_video_generation(project_id: str, generation_id: str) -> dict[str, Any]:
    """把某个视频版本标记为当前采用（is_selected=1），同一分镜的其他版本取消采用。"""
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        get_project_or_404(conn, project_id)
        generation = row_to_dict(
            conn.execute(
                "SELECT * FROM video_generations WHERE id = ? AND project_id = ?",
                (generation_id, project_id),
            ).fetchone()
        )
        if not generation:
            raise HTTPException(status_code=404, detail="Video generation not found")
        conn.execute(
            "UPDATE video_generations SET is_selected = 0 WHERE project_id = ? AND storyboard_id = ?",
            (project_id, generation["storyboard_id"]),
        )
        conn.execute(
            "UPDATE video_generations SET is_selected = 1 WHERE id = ? AND project_id = ?",
            (generation_id, project_id),
        )
        generation["is_selected"] = 1
        generation["payload"] = json.loads(generation.pop("payload_json"))
        generation["output_url"] = data_file_url(str(generation.get("output_path") or ""))
        return {"generation": generation}


def get_video_generation_storyboard(
    conn: sqlite3.Connection,
    project_id: str,
    storyboard_id: str | None,
) -> dict[str, Any]:
    if storyboard_id:
        row = conn.execute(
            "SELECT * FROM storyboards WHERE id = ? AND project_id = ?",
            (storyboard_id, project_id),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM storyboards WHERE project_id = ? ORDER BY created_at LIMIT 1",
            (project_id,),
        ).fetchone()
    storyboard = row_to_dict(row)
    if not storyboard:
        raise HTTPException(status_code=400, detail="Generate a storyboard before video generation")
    storyboard["payload"] = json.loads(storyboard.pop("payload_json"))
    if storyboard["payload"].get("使用回退占位") is True:
        raise HTTPException(status_code=422, detail="当前分镜是未分析的回退占位；请先编辑并补入具体镜头事实，再生成。")
    project = get_project_or_404(conn, project_id)
    apply_storyboard_project_prompt(conn, project, storyboard)
    unresolved_references = storyboard["payload"].get("资产引用待核对")
    if isinstance(unresolved_references, list) and unresolved_references:
        tokens = sorted({str(item.get("token") or "") for item in unresolved_references if isinstance(item, dict) and item.get("token")})
        detail = "、".join(tokens) or "未能唯一关联的资产引用"
        raise HTTPException(status_code=422, detail=f"当前分镜仍有资产引用待核对（{detail}）；请先在分镜中完成唯一资产关联。")
    return storyboard


@app.post("/api/projects/{project_id}/tasks/run")
def run_task(project_id: str, payload: RunTaskRequest, background_tasks: BackgroundTasks) -> dict[str, Any]:
    with connect() as conn:
        project = get_project_or_404(conn, project_id)
        if payload.taskType not in {"asset_extraction", "storyboard_generation", "video_generation", "series_analysis"}:
            raise HTTPException(status_code=400, detail="Unsupported task type")
        if payload.taskType == "series_analysis":
            episode_id = None
        else:
            episode_id = payload.episodeId
        if payload.taskType == "video_generation":
            # Reject explicit unanalysed fallback placeholders before creating a running task.
            get_video_generation_storyboard(conn, project_id, payload.storyboardId)
        task = create_task_with_status(
            conn,
            project_id,
            episode_id,
            payload.taskType,
            "运行中",
            "任务已开始，后台执行中",
            {
                "taskType": payload.taskType,
                "episodeId": payload.episodeId,
                "storyboardId": payload.storyboardId,
                "onlyUnprocessed": payload.onlyUnprocessed,
                "force": payload.force,
            },
        )
        background_tasks.add_task(
            execute_task_background,
            task["id"],
            project_id,
            payload.taskType,
            episode_id,
            payload.storyboardId,
            payload.force,
        )
        return {"task": task}


@app.get("/api/projects/{project_id}/tasks")
def list_tasks(project_id: str) -> dict[str, Any]:
    with connect() as conn:
        get_project_or_404(conn, project_id)
        rows = conn.execute(
            "SELECT * FROM tasks WHERE project_id = ? ORDER BY created_at DESC",
            (project_id,),
        ).fetchall()
        tasks = rows_to_dicts(rows)
        for task in tasks:
            task["result"] = json.loads(task.pop("result_json"))
        return {"tasks": tasks}


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str) -> dict[str, Any]:
    with connect() as conn:
        task = row_to_dict(conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone())
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        task["result"] = json.loads(task.pop("result_json"))
        runs = rows_to_dicts(
            conn.execute(
                "SELECT * FROM agent_runs WHERE task_id = ? ORDER BY created_at",
                (task_id,),
            ).fetchall()
        )
        for run in runs:
            run["input"] = json.loads(run.pop("input_json"))
            run["output"] = json.loads(run.pop("output_json"))
        return {"task": task, "agentRuns": runs}


@app.post("/api/tasks/{task_id}/retry")
def retry_task(task_id: str) -> dict[str, Any]:
    with connect() as conn:
        task = row_to_dict(conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone())
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        project = get_project_or_404(conn, task["project_id"])
        if task["task_type"] == "asset_extraction":
            retried = run_asset_extraction(conn, project, task["episode_id"])
        elif task["task_type"] == "storyboard_generation":
            retried = run_storyboard_generation(conn, project, task["episode_id"], force=True)
        elif task["task_type"] == "video_generation":
            retried = run_video_generation(conn, project, None)
        else:
            raise HTTPException(status_code=400, detail="Unsupported task type")
        return {"task": retried}


def create_task(
    conn: sqlite3.Connection,
    project_id: str,
    episode_id: str | None,
    task_type: str,
    message: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    task_id = new_id("task")
    timestamp = now_iso()
    conn.execute(
        """
        INSERT INTO tasks (id, project_id, episode_id, task_type, status, message, result_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (task_id, project_id, episode_id, task_type, "成功", message, json.dumps(result, ensure_ascii=False), timestamp, timestamp),
    )
    return {
        "id": task_id,
        "project_id": project_id,
        "episode_id": episode_id,
        "task_type": task_type,
        "status": "成功",
        "message": message,
        "result": result,
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def create_task_with_status(
    conn: sqlite3.Connection,
    project_id: str,
    episode_id: str | None,
    task_type: str,
    status: str,
    message: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    task_id = new_id("task")
    timestamp = now_iso()
    conn.execute(
        """
        INSERT INTO tasks (id, project_id, episode_id, task_type, status, message, result_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (task_id, project_id, episode_id, task_type, status, message, json.dumps(result, ensure_ascii=False), timestamp, timestamp),
    )
    return {
        "id": task_id,
        "project_id": project_id,
        "episode_id": episode_id,
        "task_type": task_type,
        "status": status,
        "message": message,
        "result": result,
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def update_task_status(
    conn: sqlite3.Connection,
    task_id: str,
    status: str,
    message: str,
    result: dict[str, Any],
) -> None:
    conn.execute(
        """
        UPDATE tasks
        SET status = ?, message = ?, result_json = ?, updated_at = ?
        WHERE id = ?
        """,
        (status, message, json.dumps(result, ensure_ascii=False), now_iso(), task_id),
    )


def finish_or_create_task(
    conn: sqlite3.Connection,
    project_id: str,
    episode_id: str | None,
    task_type: str,
    status: str,
    message: str,
    result: dict[str, Any],
    task_id: str | None = None,
) -> dict[str, Any]:
    """Complete the API-created task in place; retain standalone runner compatibility."""
    if not task_id:
        if status == "成功":
            return create_task(conn, project_id, episode_id, task_type, message, result)
        return create_task_with_status(conn, project_id, episode_id, task_type, status, message, result)
    update_task_status(conn, task_id, status, message, result)
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    task = row_to_dict(row)
    if not task:
        raise RuntimeError(f"Task {task_id} disappeared while completing {task_type}")
    task["result"] = result
    return task


def update_task_stream_progress(conn: sqlite3.Connection, task_id: str, stage: str, chars: int) -> None:
    task_row = conn.execute("SELECT result_json FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not task_row:
        return
    progress_result = json.loads(task_row["result_json"] or "{}")
    phase = "模型正在返回文本" if stage == "generating" else "正在解析模型返回" if stage == "postprocessing" else "模型请求失败，正在收口" if stage == "failed" else "模型处理中，等待返回文本"
    now = now_iso()
    progress_result["实时进度"] = {"阶段": stage, "已接收字符": chars, "最后事件时间": now}
    conn.execute(
        "UPDATE tasks SET message = ?, result_json = ?, updated_at = ? WHERE id = ?",
        (f"{phase}（已收到 {chars} 字符）" if chars else phase, json.dumps(progress_result, ensure_ascii=False), now, task_id),
    )
    conn.commit()


def execute_task_background(
    task_id: str,
    project_id: str,
    task_type: str,
    episode_id: str | None,
    storyboard_id: str | None,
    force: bool = False,
) -> None:
    with connect() as conn:
        try:
            project = get_project_or_404(conn, project_id)
            if task_type == "asset_extraction":
                completed = run_asset_extraction(conn, project, episode_id, task_id)
            elif task_type == "storyboard_generation":
                completed = run_storyboard_generation(conn, project, episode_id, force=force, task_id=task_id)
            elif task_type == "video_generation":
                completed = run_video_generation(conn, project, storyboard_id, task_id=task_id)
            elif task_type == "series_analysis":
                completed = run_series_analysis(conn, project, task_id)
            else:
                raise RuntimeError(f"Unsupported task type: {task_type}")
            update_task_status(conn, task_id, completed.get("status", "成功"), completed["message"], completed["result"])
            conn.commit()
        except Exception as exc:
            if task_type == "storyboard_generation":
                conn.execute(
                    "UPDATE projects SET status = ?, updated_at = ? WHERE id = ?",
                    ("分镜分析失败", now_iso(), project_id),
                )
            update_task_status(
                conn,
                task_id,
                "失败",
                str(exc),
                {
                    "error": str(exc),
                    "taskType": task_type,
                    "episodeId": episode_id,
                    "storyboardId": storyboard_id,
                },
            )
            conn.commit()


def insert_task(
    conn: sqlite3.Connection,
    project_id: str,
    episode_id: str | None,
    task_type: str,
    message: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    return create_task(conn, project_id, episode_id, task_type, message, result)


def build_mock_assets() -> list[dict[str, Any]]:
    return [
        {
            "asset_type": "角色",
            "name": "张三",
            "localized_name": "John Carter",
            "variable": "{{角色_001_子资产_001}}",
            "payload": {
                "主资产编号": "角色_001",
                "子资产编号": "角色_001_子资产_001",
                "剧情身份": "男主，久别重逢的前任恋人",
                "本地化核心形象描述": "三十岁左右的美国男性，都市精英气质，克制但有压迫感。",
                "角色资产图生成提示词": "三十岁左右美国男性，都市精英气质，深色西装，克制冷峻。"
            },
        },
        {
            "asset_type": "场景",
            "name": "办公大楼",
            "localized_name": "Downtown Office Tower",
            "variable": "{{场景_001_子资产_001}}",
            "payload": {
                "主资产编号": "场景_001",
                "子资产编号": "场景_001_子资产_001",
                "空间结构描述": "现代办公楼入口，玻璃幕墙，雨夜街道。",
                "场景资产图生成提示词": "美国都市中心现代办公大楼入口，玻璃幕墙，雨夜，真实摄影质感。"
            },
        },
        {
            "asset_type": "道具",
            "name": "残破的笔记",
            "localized_name": "Torn Notebook",
            "variable": "{{道具_001}}",
            "payload": {
                "道具编号": "道具_001",
                "剧情功能": "承载旧日秘密的关键证物。",
                "道具资产图生成提示词": "一本边角磨损的英文笔记本，纸张泛黄，真实道具摄影。"
            },
        },
    ]


def normalize_assets_from_agent(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    asset_root = parsed.get("资产库")
    if not isinstance(asset_root, dict):
        return []
    normalized: list[dict[str, Any]] = []

    for role in asset_root.get("角色资产") or []:
        if not isinstance(role, dict):
            continue
        children = compact_role_children(role)
        main_id = role.get("主资产编号") or f"角色_{len([item for item in normalized if item.get('asset_type') == '角色']) + 1:03d}"
        for index, child in enumerate(children, start=1):
            if not isinstance(child, dict):
                child = {}
            child_id = child.get("子资产编号") or f"{main_id}_子资产_{index:03d}"
            variable = f"{{{{{child_id}}}}}"
            payload = dict(role)
            payload["子资产"] = children
            payload["当前子资产"] = child
            payload["子资产编号"] = child_id
            payload["源变量别名"] = child.get("角色资产变量", "")
            payload["角色资产图生成提示词"] = child.get("角色资产图生成提示词", "") or role.get("角色资产图生成提示词", "")
            asset = {
                "asset_type": "角色",
                "name": role.get("原剧角色名称") or child.get("原剧子资产名称") or role.get("主资产编号") or "未命名角色",
                "localized_name": role.get("本地化角色名称") or child.get("本地化子资产名称") or "Unnamed Character",
                "variable": variable,
                "source_variable_alias": child.get("角色资产变量", ""),
                "payload": payload,
            }
            normalized.append(attach_display_variable(asset))

    for scene in asset_root.get("场景资产") or []:
        if not isinstance(scene, dict):
            continue
        children = scene.get("子资产") if isinstance(scene.get("子资产"), list) else []
        if not children:
            children = [{}]
        scene_id = str(scene.get("主资产编号") or "").strip()
        scene_name = scene.get("原剧场景名称") or scene.get("原剧场景名称/描述") or scene.get("主资产编号") or "未命名场景"
        for index, child in enumerate(children, start=1):
            if not isinstance(child, dict):
                child = {}
            child_id = str(child.get("子资产编号") or (f"{scene_id}_子资产_{index:03d}" if scene_id else f"场景_{len([item for item in normalized if item.get('asset_type') == '场景']) + 1:03d}_子资产_{index:03d}"))
            variable = f"{{{{{child_id}}}}}"
            payload = dict(scene)
            payload["主资产编号"] = scene_id or re.split(r"_SUB_|_子资产_", child_id, maxsplit=1)[0]
            payload["原剧场景名称"] = scene.get("原剧场景名称") or scene.get("原剧场景名称/描述") or scene.get("原剧场景名称/描述", "")
            if child.get("原剧视觉描述") and not child.get("原剧视觉形象描述"):
                child["原剧视觉形象描述"] = child["原剧视觉描述"]
            if child.get("截图候选") and not child.get("原剧截图时间点候选"):
                child["原剧截图时间点候选"] = child["截图候选"]
            payload["当前子资产"] = child
            payload["子资产编号"] = child_id
            payload["源变量别名"] = child.get("场景资产变量", "")
            payload["场景资产图生成提示词"] = child.get("场景资产图生成提示词", "")
            asset = {
                "asset_type": "场景",
                "name": scene_name or child.get("原剧子资产名称") or "未命名场景",
                "localized_name": scene.get("本地化场景名称") or child.get("本地化子资产名称") or "Unnamed Scene",
                "variable": variable,
                "source_variable_alias": child.get("场景资产变量", ""),
                "payload": payload,
            }
            normalized.append(attach_display_variable(asset))

    for prop in asset_root.get("道具资产") or []:
        if not isinstance(prop, dict):
            continue
        prop_id = str(prop.get("道具编号") or prop.get("道具ID") or "").strip()
        variable = str(prop.get("道具资产变量") or prop.get("变量") or "").strip()
        if not variable and prop_id:
            variable = f"{{{{{prop_id}}}}}"
        if not variable:
            continue
        source_name = str(prop.get("原剧道具名称") or prop.get("原剧名称") or prop.get("道具名称") or "").strip()
        source_description = str(prop.get("原剧道具描述") or prop.get("原剧描述") or prop.get("原剧视觉描述") or "").strip()
        payload = dict(prop)
        payload.setdefault("道具编号", prop_id)
        payload.setdefault("道具资产变量", variable)
        if source_name:
            payload.setdefault("原剧道具名称", source_name)
        if source_description:
            payload.setdefault("原剧道具描述", source_description)
        if "原剧截图时间点候选" not in payload:
            payload["原剧截图时间点候选"] = prop.get("截图候选") or []
        asset = {
            "asset_type": "道具",
            "name": source_name or prop_id or "未命名道具",
            "localized_name": prop.get("本地化道具名称") or "Unnamed Prop",
            "variable": variable,
            "payload": payload,
        }
        normalized.append(attach_display_variable(asset))

    normalized = [asset for asset in normalized if asset.get("variable")]
    for asset in normalized:
        initialize_asset_profiles(asset)
    return normalized


def normalize_reused_source_assets(source_assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Upgrade an exact v1 source-cache hit in memory before target localization.

    Existing cached/published rows are not mutated; only the new extraction
    attempt receives canonical stable IDs and an explicit v2 review snapshot.
    """
    assets = json.loads(json.dumps(source_assets, ensure_ascii=False))
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        payload = asset.get("payload") if isinstance(asset.get("payload"), dict) else {}
        child = payload.get("当前子资产") if isinstance(payload.get("当前子资产"), dict) else {}
        child_id = str(child.get("子资产编号") or payload.get("子资产编号") or "").strip()
        if asset.get("asset_type") in {"角色", "场景"} and child_id:
            alias_key = "角色资产变量" if asset.get("asset_type") == "角色" else "场景资产变量"
            payload["源变量别名"] = child.get(alias_key) or payload.get("源变量别名") or ""
            asset["variable"] = f"{{{{{child_id}}}}}"
            payload["子资产编号"] = child_id
        initialize_asset_profiles(asset)
        asset["payload"] = asset.get("payload") if isinstance(asset.get("payload"), dict) else payload
        attach_display_variable(asset)
    return assets


def insert_assets(
    conn: sqlite3.Connection,
    project_id: str,
    episode_id: str,
    assets: list[dict[str, Any]],
    status: str = "待确认",
) -> None:
    timestamp = now_iso()
    for asset in assets:
        existing_id = str(asset.get("_existingAssetId") or "")
        existing = conn.execute(
            "SELECT id,payload_json,status FROM assets WHERE id=? AND project_id=? AND episode_id=?",
            (existing_id, project_id, episode_id),
        ).fetchone() if existing_id else None
        if existing:
            previous_payload = json.loads(existing["payload_json"] or "{}")
            next_payload = asset.get("payload") if isinstance(asset.get("payload"), dict) else {}
            previous_profile = previous_payload.get("localizedProfile")
            next_profile = next_payload.get("localizedProfile")
            if previous_profile != next_profile or asset.get("name") != asset.get("_previousName") or asset.get("localized_name") != asset.get("_previousLocalizedName"):
                stale_asset = {**asset, "payload": previous_payload}
                mark_storyboards_asset_stale(conn, project_id, stale_asset, "同一源资产的目标画像或名称已更新")
            conn.execute(
                "UPDATE assets SET name=?,localized_name=?,variable=?,payload_json=? WHERE id=? AND project_id=? AND episode_id=?",
                (asset["name"], asset["localized_name"], asset["variable"], json.dumps(next_payload, ensure_ascii=False), existing_id, project_id, episode_id),
            )
            continue
        conn.execute(
            """
            INSERT INTO assets
            (id, project_id, episode_id, asset_type, name, localized_name, variable, status, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("asset"),
                project_id,
                episode_id,
                asset["asset_type"],
                asset["name"],
                asset["localized_name"],
                asset["variable"],
                status,
                json.dumps(asset["payload"], ensure_ascii=False),
                timestamp,
            ),
        )


def stable_asset_source_identity(asset: dict[str, Any]) -> str:
    source = build_source_snapshot(asset)
    identity = {
        "assetType": str(asset.get("asset_type") or ""),
        "childId": str(source.get("childId") or asset.get("variable") or ""),
        "sourceName": str(source.get("name") or asset.get("name") or ""),
        "sourceFacts": {
            key: source.get(key)
            for key in (
                "coreDescription", "visualDescription", "identity", "sourceStructuredDescription",
                "sourceSceneStructure", "sourcePropFacts", "facts",
            )
            if source.get(key) not in (None, "", {}, [])
        },
    }
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def reconcile_asset_extraction_rows(
    conn: sqlite3.Connection,
    project_id: str,
    episode_id: str,
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    """Preserve exact source identities and histories; never guess fuzzy matches."""
    rows = conn.execute(
        "SELECT * FROM assets WHERE project_id=? AND episode_id=? ORDER BY created_at,id",
        (project_id, episode_id),
    ).fetchall()
    existing: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json") or "{}")
        existing.append(item)
    old_by_identity: dict[str, list[dict[str, Any]]] = {}
    old_by_variable: dict[str, list[dict[str, Any]]] = {}
    for item in existing:
        old_by_identity.setdefault(stable_asset_source_identity(item), []).append(item)
        old_by_variable.setdefault(str(item.get("variable") or ""), []).append(item)
    candidate_counts: dict[str, int] = {}
    for candidate in candidates:
        identity = stable_asset_source_identity(candidate)
        candidate_counts[identity] = candidate_counts.get(identity, 0) + 1
    matched_ids: set[str] = set()
    conflicts: list[dict[str, Any]] = []
    conflict_old_ids: set[str] = set()
    for candidate in candidates:
        identity = stable_asset_source_identity(candidate)
        matches = old_by_identity.get(identity, [])
        if len(matches) == 1 and candidate_counts[identity] == 1:
            prior = matches[0]
            previous_payload = prior["payload"]
            next_payload = candidate.get("payload") if isinstance(candidate.get("payload"), dict) else {}
            # Keep explicit user-authored generation/name choices and original
            # capture references while accepting newly normalized source and
            # target profiles for the exact same source identity.
            user_visual_lock = explicit_user_visual_lock(previous_payload)
            if previous_payload.get("用户编辑资产图生成提示词"):
                next_payload["用户编辑资产图生成提示词"] = previous_payload["用户编辑资产图生成提示词"]
            if user_visual_lock:
                for key in ("targetVisualLockProvenance", "目标生成描述", "legacyPreservedDesignConstraints"):
                    if key in previous_payload:
                        next_payload[key] = previous_payload[key]
            if previous_payload.get("目标资产来源") in {"用户上传", "手动生成", "沿用原貌"}:
                next_payload["目标资产来源"] = previous_payload["目标资产来源"]
            reviewed_source_facts = previous_payload.get("sourceFactReview")
            if isinstance(reviewed_source_facts, dict) and reviewed_source_facts.get("status") == "reviewed":
                next_payload["sourceFactReview"] = json.loads(json.dumps(reviewed_source_facts, ensure_ascii=False))
            for key in ("targetNameProvenance", "用户指定目标角色名称", "本地化角色名称", "本地化场景名称", "本地化道具名称"):
                if key in previous_payload and explicit_user_localized_name(previous_payload):
                    next_payload[key] = previous_payload[key]
            for key in ("原资产图路径", "原资产截图URL", "原资产候选截图", "原资产截图候选"):
                if key in previous_payload:
                    next_payload[key] = previous_payload[key]
            old_child = previous_payload.get("当前子资产") if isinstance(previous_payload.get("当前子资产"), dict) else {}
            new_child = next_payload.get("当前子资产") if isinstance(next_payload.get("当前子资产"), dict) else {}
            if isinstance(reviewed_source_facts, dict) and reviewed_source_facts.get("status") == "reviewed":
                accessory_values = [
                    str(item.get("value") or "").strip()
                    for item in reviewed_source_facts.get("fixedWornAccessories", [])
                    if isinstance(item, dict) and str(item.get("value") or "").strip()
                ]
                if accessory_values:
                    new_child["固定穿戴配饰"] = list(dict.fromkeys([
                        *(new_child.get("固定穿戴配饰") if isinstance(new_child.get("固定穿戴配饰"), list) else []),
                        *accessory_values,
                    ]))
            for key in ("原资产图路径", "原资产截图URL", "原资产候选截图", "原资产截图候选"):
                if key in old_child:
                    new_child[key] = old_child[key]
            if new_child:
                next_payload["当前子资产"] = new_child
            next_payload.pop("assetSourceRefreshReview", None)
            candidate["_previousName"] = prior["name"]
            candidate["_previousLocalizedName"] = prior["localized_name"]
            candidate["_existingAssetId"] = prior["id"]
            candidate["id"] = prior["id"]
            candidate["variable"] = prior["variable"]
            if explicit_user_localized_name(previous_payload):
                candidate["localized_name"] = prior["localized_name"]
            candidate["payload"] = next_payload
            candidate["status"] = prior["status"]
            matched_ids.add(str(prior["id"]))
            continue
        same_variable = old_by_variable.get(str(candidate.get("variable") or ""), [])
        if matches or same_variable:
            conflict = {
                "variable": candidate.get("variable", ""),
                "candidateName": candidate.get("name", ""),
                "candidateCount": candidate_counts[identity],
                "exactSourceIdentityMatches": len(matches),
                "sameStableVariableCount": len(same_variable),
                "reason": "源事实身份无法唯一匹配；保留现有资产待人工核对",
            }
            conflicts.append(conflict)
            # 歧义候选不发布：不合并、不新增，涉及冲突的旧资产进入身份核对队列。
            candidate["_withheld"] = conflict
            for row in [*matches, *same_variable]:
                conflict_old_ids.add(str(row["id"]))
    stale_rows = [
        item for item in existing
        if str(item["id"]) not in matched_ids and str(item["id"]) not in conflict_old_ids
    ]
    review_rows = [item for item in existing if str(item["id"]) in conflict_old_ids]
    for item in stale_rows:
        payload = item["payload"]
        payload["assetSourceRefreshReview"] = {
            "status": "stale",
            "reason": "本次提取未找到可精确关联的同源资产；保留旧资产、图片历史和分镜引用待人工核对",
        }
        conn.execute("UPDATE assets SET payload_json=? WHERE id=?", (json.dumps(payload, ensure_ascii=False), item["id"]))
        mark_storyboards_asset_stale(conn, project_id, item, "源资产提取结果无法精确关联，待人工核对")
    for item in review_rows:
        payload = item["payload"]
        related = [c for c in conflicts if c["variable"] == str(item.get("variable") or "")]
        payload["身份核对"] = {
            "status": "待核对",
            "reason": "重提取候选资产身份歧义，旧资产已保留未自动合并；请人工核对后重新提取或手动调整",
            "conflicts": related,
        }
        conn.execute("UPDATE assets SET payload_json=? WHERE id=?", (json.dumps(payload, ensure_ascii=False), item["id"]))
    return {
        "preservedExistingCount": len(matched_ids),
        "newCandidateCount": sum(1 for item in candidates if not item.get("_existingAssetId") and not item.get("_withheld")),
        "withheldCount": sum(1 for item in candidates if item.get("_withheld")),
        "staleExistingIds": [str(item["id"]) for item in stale_rows],
        "reviewExistingIds": [str(item["id"]) for item in review_rows],
        "identityConflicts": conflicts,
    }


def normalize_asset_occurrence_index(raw_index: Any, assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    old_to_display, by_variable = variable_maps(assets)
    normalized: list[dict[str, Any]] = []
    source_items = raw_index if isinstance(raw_index, list) and raw_index else []
    if not source_items:
        return []
    for index, segment in enumerate(source_items, start=1):
        if not isinstance(segment, dict):
            continue
        # Legacy source extraction emits one parent-asset ID plus a list of
        # appearance timestamps. Resolve only when that parent maps to exactly
        # one normalized asset; never fan a shared parent reference across outfit variants.
        source_id = str(segment.get("资产编号") or segment.get("assetId") or "").strip()
        direct_variable = str(segment.get("资产变量") or segment.get("变量") or "").strip()
        source_ranges = source_asset_appearance_ranges(segment)
        if source_id or direct_variable:
            matches = []
            for candidate in assets:
                payload = candidate.get("payload") if isinstance(candidate.get("payload"), dict) else {}
                parent_id = str(payload.get("主资产编号") or payload.get("道具编号") or "").strip()
                candidate_variables = {
                    str(candidate.get("variable") or ""),
                    str(candidate.get("source_variable_alias") or ""),
                    str(payload.get("源变量别名") or ""),
                }
                if (direct_variable and direct_variable in candidate_variables) or (source_id and parent_id == source_id):
                    matches.append(candidate)
            if len(matches) == 1 and source_ranges:
                candidate = matches[0]
                for time_index, (start_text, end_text) in enumerate(source_ranges, start=1):
                    normalized.append({
                        "片段编号": f"{source_id}_出现_{time_index:03d}",
                        "开始时间": start_text,
                        "结束时间": end_text,
                        "片段剧情简述": "来源主资产编号与资产出现覆盖索引；不是镜头切分",
                        "出现资产": [{
                            "资产类型": candidate.get("asset_type", ""),
                            "资产变量": candidate.get("variable", ""),
                            "展示资产变量": candidate.get("display_variable", ""),
                            "本地化资产名称": candidate.get("localized_name", ""),
                            "片段剧情作用": "",
                        }],
                        "出现资产变量": [candidate.get("display_variable", "")],
                    })
            elif source_ranges:
                start_text, end_text = source_ranges[0]
                normalized.append({
                    "片段编号": segment.get("片段编号") or f"片段_{index:03d}",
                    "开始时间": start_text, "结束时间": end_text, "片段剧情简述": "来源资产编号无法唯一关联到当前稳定资产，保留原引用待审核",
                    "出现资产": [], "出现资产变量": [], "unresolvedSourceAssetReference": {"assetId": source_id, "assetVariable": direct_variable, "candidateCount": len(matches), "ranges": source_ranges},
                })
            if source_ranges:
                continue
        raw_assets = segment.get("出现资产") if isinstance(segment.get("出现资产"), list) else split_variable_text(segment.get("出现资产"))
        raw_variables = segment.get("出现资产变量") if isinstance(segment.get("出现资产变量"), list) else split_variable_text(segment.get("出现资产变量"))
        raw_assets.extend(raw_variables)
        segment_assets: list[dict[str, Any]] = []
        for raw_asset in raw_assets:
            raw_variable = raw_asset.get("资产变量") if isinstance(raw_asset, dict) else raw_asset
            if not isinstance(raw_variable, str) or not raw_variable.strip():
                continue
            for variable in split_variable_text(raw_variable):
                matched = by_variable.get(variable) or by_variable.get(old_to_display.get(variable, ""))
                display_variable = old_to_display.get(variable, variable if variable.startswith("@") else "")
                segment_assets.append(
                    {
                        "资产类型": (raw_asset.get("资产类型", "") if isinstance(raw_asset, dict) else matched.get("asset_type", "") if matched else ""),
                        "资产变量": matched.get("variable", variable) if matched else variable,
                        "展示资产变量": matched.get("display_variable", display_variable) if matched else display_variable,
                        "本地化资产名称": (raw_asset.get("本地化资产名称", "") if isinstance(raw_asset, dict) else matched.get("localized_name", "") if matched else ""),
                        "片段剧情作用": raw_asset.get("片段剧情作用", "") if isinstance(raw_asset, dict) else "",
                    }
                )
        deduped_assets: list[dict[str, Any]] = []
        seen_variables: set[str] = set()
        for asset_item in segment_assets:
            key = asset_item.get("展示资产变量") or asset_item.get("资产变量")
            if key in seen_variables:
                continue
            seen_variables.add(key)
            deduped_assets.append(asset_item)
        normalized.append(
            {
                "片段编号": segment.get("片段编号") or f"片段_{index:03d}",
                "开始时间": segment.get("开始时间", ""),
                "结束时间": segment.get("结束时间", ""),
                "片段剧情简述": segment.get("片段剧情简述", ""),
                "出现资产": deduped_assets,
                "出现资产变量": [item["展示资产变量"] for item in deduped_assets if item.get("展示资产变量")],
            }
        )
    return normalized


def source_asset_appearance_ranges(segment: dict[str, Any]) -> list[tuple[str, str]]:
    raw_ranges = segment.get("出现时间片段")
    if isinstance(raw_ranges, str):
        raw_ranges = [raw_ranges]
    parsed: list[tuple[str, str]] = []
    for raw_range in raw_ranges if isinstance(raw_ranges, list) else []:
        text = str(raw_range if raw_range is not None else "").strip()
        match = re.match(r"^\s*(.+?)\s*(?:-|–|—|至|到)\s*(.+?)\s*(?:秒)?\s*$", text)
        if match:
            start, end = match.group(1).strip(), match.group(2).strip()
            if parse_asset_timecode(start) or start.strip() in {"0", "0.0", "00:00:00"}:
                if parse_asset_timecode(end) or end.strip() in {"0", "0.0", "00:00:00"}:
                    parsed.append((start, end))
            continue
        time_match = re.match(r"^\s*(.+?)\s*秒?\s*$", text)
        if time_match:
            time_text = time_match.group(1).strip()
            if parse_asset_timecode(time_text) or time_text in {"0", "0.0", "00:00:00"}:
                parsed.append((time_text, time_text))
    if parsed:
        return list(dict.fromkeys(parsed))
    first = segment.get("首次出现时间", segment.get("开始时间"))
    last = segment.get("最后出现时间", segment.get("结束时间"))
    if first not in (None, ""):
        start_text = str(first).strip().removesuffix("秒")
        end_text = str(last if last not in (None, "") else first).strip().removesuffix("秒")
        if parse_asset_timecode(start_text) or start_text in {"0", "0.0", "00:00:00"}:
            if parse_asset_timecode(end_text) or end_text in {"0", "0.0", "00:00:00"}:
                return [(start_text, end_text)]
    source_points = segment.get("出现时间点")
    if isinstance(source_points, list):
        return [
            (text, text) for value in source_points
            if (text := str(value if value is not None else "").strip().removesuffix("秒"))
            and (parse_asset_timecode(text) or text in {"0", "0.0", "00:00:00"})
        ]
    return []


def apply_capture_times_from_index(assets: list[dict[str, Any]], asset_occurrence_index: list[dict[str, Any]]) -> None:
    _, by_variable = variable_maps(assets)
    asset_times: dict[str, list[float]] = {}
    asset_reasons: dict[str, str] = {}
    for segment in asset_occurrence_index:
        if not isinstance(segment, dict):
            continue
        start, end = parse_time_range_seconds(segment.get("开始时间"), segment.get("结束时间"))
        midpoint = start + max(0.0, end - start) / 2
        segment_times = [midpoint, start + 0.5 if end > start else start, max(start, end - 0.5)]
        reason = str(segment.get("片段剧情简述") or "")
        for item in segment.get("出现资产") or []:
            if not isinstance(item, dict):
                continue
            for variable in [item.get("资产变量"), item.get("展示资产变量")]:
                matched = by_variable.get(str(variable or ""))
                if not matched:
                    continue
                key = str(matched.get("variable") or "")
                asset_times.setdefault(key, [])
                for seconds in segment_times:
                    if round(seconds, 2) not in [round(existing, 2) for existing in asset_times[key]]:
                        asset_times[key].append(round(seconds, 2))
                if reason and key not in asset_reasons:
                    asset_reasons[key] = reason
    for asset in assets:
        key = str(asset.get("variable") or "")
        times = asset_times.get(key, [])
        if not times:
            continue
        payload = asset.get("payload") if isinstance(asset.get("payload"), dict) else {}
        child = payload.get("当前子资产") if isinstance(payload.get("当前子资产"), dict) else {}
        target = child if isinstance(child, dict) else payload
        if not target.get("原剧截图时间点候选"):
            target["原剧截图时间点候选"] = [format_timecode(seconds) for seconds in times[:3]]
        if not target.get("原剧截图时间点"):
            target["原剧截图时间点"] = format_timecode(times[0])
        if not target.get("截图对象描述"):
            target["截图对象描述"] = " / ".join(
                item for item in [str(asset.get("name") or ""), str(asset.get("localized_name") or ""), str(asset.get("display_variable") or "")] if item
            )
        if not target.get("截图理由"):
            target["截图理由"] = asset_reasons.get(key, "资产出现片段索引中首次清晰出现的时间点")


def asset_detail_completion_input(asset: dict[str, Any]) -> dict[str, Any]:
    payload = asset.get("payload") if isinstance(asset.get("payload"), dict) else {}
    child = payload.get("当前子资产") if isinstance(payload.get("当前子资产"), dict) else {}
    return {
        "资产类型": asset.get("asset_type", ""),
        "原剧资产名称": asset.get("name", ""),
        "本地化资产名称": asset.get("localized_name", ""),
        "资产变量": asset.get("variable", ""),
        "展示资产变量": asset.get("display_variable", ""),
        "主资产编号": payload.get("主资产编号") or payload.get("道具编号", ""),
        "子资产编号": payload.get("子资产编号") or child.get("子资产编号", ""),
        "剧情身份": payload.get("剧情身份", ""),
        "剧情作用": payload.get("剧情作用") or payload.get("剧情功能", ""),
        "原剧核心形象描述": payload.get("原剧核心形象描述") or payload.get("原剧场景描述") or payload.get("原剧道具描述", ""),
        "本地化核心形象描述": payload.get("本地化核心形象描述") or payload.get("本地化场景描述") or payload.get("本地化道具描述", ""),
        "用户目标视觉补充": payload.get("目标生成描述", ""),
        "角色资产图结构化描述": child.get("角色资产图结构化描述") or payload.get("角色资产图结构化描述", {}),
        "结构化缺失路径": role_structured_missing_paths(child.get("角色资产图结构化描述") or payload.get("角色资产图结构化描述", {})) if asset.get("asset_type") == "角色" else [],
        "视觉事实来源": child.get("视觉事实来源", {}),
        "固定穿戴配饰": child.get("固定穿戴配饰", []),
        "手持或悬挂物档案": child.get("手持或悬挂物档案", []),
        "当前子资产": {
            "原剧子资产名称": child.get("原剧子资产名称", ""),
            "本地化子资产名称": child.get("本地化子资产名称", ""),
            "差异类型": child.get("差异类型") or child.get("状态差异", ""),
            "差异标签": child.get("差异标签", ""),
            "原剧视觉形象描述": child.get("原剧视觉形象描述") or child.get("原剧视觉描述", ""),
            "本地化视觉形象描述": child.get("本地化视觉形象描述") or child.get("本地化视觉描述", ""),
            "原剧截图时间点候选": child.get("原剧截图时间点候选") or payload.get("原剧截图时间点候选", []),
            "截图对象描述": child.get("截图对象描述") or payload.get("截图对象描述", ""),
            "截图理由": child.get("截图理由") or payload.get("截图理由", ""),
        },
    }


UNRESOLVED_ASSET_REFERENCES = (
    "根据原片", "参考原图", "参考原片", "保持原貌", "同上", "如果原片能判断",
    "如原片可见", "原片可见信息", "原片可见服装", "跟随原片", "依据原片", "照原片",
)
UNRESOLVED_ASSET_TASK_PLACEHOLDERS = (
    "按衣层对应", "依服装等级呈现", "按服装等级呈现", "依等级呈现", "按等级呈现",
    "按材质补充", "按场合呈现", "按需要添加", "待具体补充", "待补充具体",
)
ROLE_REQUIRED_VISUAL_PATHS = (
    ("基础设定", "性别"), ("基础设定", "性别判断依据"), ("基础设定", "视觉年龄"),
    ("基础设定", "国家 / 时代 / 朝代"), ("脸部特征", "脸型"), ("发型", "发型名称"),
    ("服装", "外层"), ("服装", "领型"), ("服装", "袖型"), ("服装", "腰线"),
    ("服装", "衣长"), ("服装", "下装"), ("服装", "摆型"), ("服装", "拖尾程度"),
    ("服装", "摆与地面关系"), ("服装", "鞋子"), ("服装", "鞋面可见性"),
    ("服装", "脚踝可见性"), ("服装", "面料"), ("服装", "纹样位置"),
)


def unresolved_asset_reference(value: Any) -> bool:
    if isinstance(value, dict):
        return any(unresolved_asset_reference(item) for item in value.values())
    if isinstance(value, list):
        return any(unresolved_asset_reference(item) for item in value)
    if not isinstance(value, str):
        return False
    return any(marker in value for marker in UNRESOLVED_ASSET_REFERENCES + UNRESOLVED_ASSET_TASK_PLACEHOLDERS)


def usable_visual_value(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    normalized = value.strip().lower()
    if any(marker in normalized for marker in ("unknown", "needsreview", "未明确", "待核实", "不确定")) or re.search(r"不明(?!显)", normalized):
        return False
    return not unresolved_asset_reference(value)


def role_structured_missing_paths(structure: Any) -> list[str]:
    if not isinstance(structure, dict):
        return ["角色资产图结构化描述"]
    missing = []
    if unresolved_asset_reference(structure):
        missing.append("角色资产图结构化描述中的源指代字段")
    footwear_hidden = footwear_hidden_by_garment(structure)
    lower_body_unobservable = lower_body_details_unobservable(structure)
    for section, field in ROLE_REQUIRED_VISUAL_PATHS:
        value = structure.get(section, {})
        field_value = value.get(field) if isinstance(value, dict) else None
        if section == "基础设定" and field == "性别判断依据":
            gender = str(value.get("性别") or "") if isinstance(value, dict) else ""
            if gender in {"中性", "未知", "未明确", "不确定", "性别未明"}:
                continue
        if footwear_hidden and section == "服装" and field == "鞋子":
            continue
        if lower_body_unobservable and f"{section}.{field}" in UNOBSERVABLE_LOWER_BODY_PATHS:
            continue
        if not usable_visual_value(field_value):
            missing.append(f"{section}.{field}")
    clothing = structure.get("服装", {})
    if isinstance(clothing, dict):
        for field in ("面料", "工艺与重工程度"):
            value = clothing.get(field)
            if isinstance(value, str) and any(marker in value for marker in UNRESOLVED_ASSET_TASK_PLACEHOLDERS):
                key = f"服装.{field}（需写明具体内容）"
                if key not in missing:
                    missing.append(key)
    if isinstance(clothing, dict) and unresolved_asset_reference(clothing):
        for field in ("衣长", "摆型", "拖尾程度", "摆与地面关系", "鞋面可见性", "脚踝可见性"):
            if not usable_visual_value(clothing.get(field)):
                key = f"服装.{field}"
                if key not in missing:
                    missing.append(key)
    return missing


UNOBSERVABLE_LOWER_BODY_PATHS = {
    "服装.衣长", "服装.摆型", "服装.拖尾程度", "服装.摆与地面关系", "服装.鞋子",
}


def lower_body_details_unobservable(structure: Any) -> bool:
    """A cropped/occluded lower body cannot supply hem or shoe source facts."""
    clothing = structure.get("服装") if isinstance(structure, dict) else None
    if not isinstance(clothing, dict):
        return False

    def explicitly_not_visible(value: Any) -> bool:
        normalized = re.sub(r"\s+", "", str(value or "")).lower()
        if not normalized:
            return False
        hidden_terms = ("不可见", "不外露", "未露出", "不显示", "被遮挡", "完全遮挡", "看不到", "notvisible", "hidden", "occluded")
        return any(term in normalized for term in hidden_terms)

    return explicitly_not_visible(clothing.get("鞋面可见性")) and explicitly_not_visible(clothing.get("脚踝可见性"))


def refresh_role_source_review_profile(payload: dict[str, Any]) -> None:
    """Reclassify source review metadata without changing any source facts."""
    profile = payload.get("localizedProfile") if isinstance(payload.get("localizedProfile"), dict) else None
    if not profile or profile.get("status") not in {"complete", "source_review"}:
        return
    structure = profile.get("structuredDescription") if isinstance(profile.get("structuredDescription"), dict) else {}
    snapshot = payload.get("sourceSnapshot") if isinstance(payload.get("sourceSnapshot"), dict) else {}
    blocking = required_source_review_paths(structure, snapshot)
    source_review = snapshot.get("needsReview") if isinstance(snapshot.get("needsReview"), list) else []
    unobservable = lower_body_details_unobservable(structure)
    non_blocking = list(dict.fromkeys(
        path for path in source_review
        if unobservable and path in UNOBSERVABLE_LOWER_BODY_PATHS and path not in blocking
    ))
    profile["sourceReviewPaths"] = blocking
    profile["nonBlockingSourceReviewPaths"] = non_blocking
    profile["needsReview"] = blocking
    if non_blocking:
        profile["sourceReviewNotes"] = [
            "原片画面未显示或遮挡下半身；衣长、摆型、拖尾、摆与地面关系及鞋款继续保留未知，提示词不补造这些源事实。"
        ]
    if blocking:
        profile["status"] = "source_review"
        profile["reviewReason"] = "目标脸部本地化已完成；仍有可观察且未确认的源视觉字段，需核对后才能生成资产图。"
    else:
        profile["status"] = "complete"
        profile.pop("reviewReason", None)


def footwear_hidden_by_garment(structure: Any) -> bool:
    """True only when explicit hem and both visibility fields prove full coverage."""
    fields = structure if isinstance(structure, dict) else {}
    clothing = fields.get("服装") if isinstance(fields.get("服装"), dict) else {}
    hem = re.sub(r"\s+", "", str(clothing.get("摆与地面关系") or ""))
    length = re.sub(r"\s+", "", str(clothing.get("衣长") or ""))
    hem_covers = any(term in hem for term in ("及地", "触地", "接地", "落地", "完全覆盖脚", "覆盖鞋履", "遮住鞋履", "覆盖鞋面"))
    if "近地面" in hem and any(term in length for term in ("长裙", "长摆", "及地", "拖地", "长袍", "礼服裙")):
        hem_covers = True
    hidden_states = ("不可见", "不外露", "不露出", "不显示", "完全遮挡", "完全覆盖", "被遮挡", "无外露", "未露出", "隐藏")
    shoe = re.sub(r"\s+", "", str(clothing.get("鞋面可见性") or ""))
    ankle = re.sub(r"\s+", "", str(clothing.get("脚踝可见性") or ""))
    return hem_covers and any(term in shoe for term in hidden_states) and any(term in ankle for term in hidden_states)


def merge_only_missing_visual(existing: Any, patch: Any) -> Any:
    """Merge detail output without replacing usable facts or user-authored values."""
    if isinstance(existing, dict) and isinstance(patch, dict):
        result = dict(existing)
        for key, value in patch.items():
            result[key] = merge_only_missing_visual(result.get(key), value) if key in result else value
        return result
    if isinstance(existing, list) and isinstance(patch, list):
        return existing if existing else patch
    if usable_visual_value(existing):
        return existing
    return patch if usable_visual_value(patch) else (existing if existing is not None else patch)


def asset_detail_missing_fields(asset: dict[str, Any]) -> list[str]:
    payload = asset.get("payload") if isinstance(asset.get("payload"), dict) else {}
    child = payload.get("当前子资产") if isinstance(payload.get("当前子资产"), dict) else {}
    source_description = str(
        payload.get("原剧核心形象描述") or payload.get("原剧场景描述") or payload.get("原剧道具描述")
        or child.get("原剧视觉形象描述") or child.get("原剧视觉描述") or ""
    ).strip()
    localized_description = str(
        payload.get("本地化核心形象描述") or payload.get("本地化场景描述") or payload.get("本地化道具描述")
        or child.get("本地化视觉形象描述") or child.get("本地化视觉描述") or ""
    ).strip()
    target_supplement = str(payload.get("目标生成描述") or "").strip()
    if not source_description and not localized_description and not target_supplement:
        return []
    if asset.get("asset_type") == "角色":
        missing = []
        if not usable_visual_value(child.get("本地化视觉形象描述")):
            missing.append("本地化视觉形象描述")
        structure = child.get("角色资产图结构化描述") or payload.get("角色资产图结构化描述")
        if role_structured_missing_paths(structure):
            missing.append("角色资产图结构化描述")
        return missing
    if asset.get("asset_type") == "场景":
        return ["本地化场景描述"] if not usable_visual_value(payload.get("本地化场景描述") or child.get("本地化视觉描述") or payload.get("空间结构描述")) else []
    if asset.get("asset_type") == "道具":
        return ["本地化道具描述"] if not usable_visual_value(payload.get("本地化道具描述")) else []
    return []


def source_file_sha256(file_path: str) -> str:
    path = Path(file_path)
    if not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def asset_source_cache_key(
    file_sha256: str, agent: dict[str, Any], project: dict[str, Any], history_assets: list[dict[str, Any]],
    *, prompt: str | None = None, pipeline_version: str | None = None,
) -> str:
    model_config = load_models_config()
    provider_config = model_config.get("volcengine", {})
    configuration = {
        "source_sha256": file_sha256,
        "model_mode": agent.get("mode", ""),
        "model": agent.get("model_name") or agent.get("model", ""),
        "temperature": agent.get("temperature"),
        "output_format": agent.get("output_format", "json"),
        "max_output_tokens": provider_config.get("maxOutputTokens"),
        "thinking": provider_config.get("thinking"),
        "source_prompt": prompt if prompt is not None else ASSET_SOURCE_EXTRACTION_COMPACT_PROMPT,
        "pipeline_version": pipeline_version or ASSET_SOURCE_SCHEMA_VERSION,
        "history_source_identity": [
            {key: item.get(key) for key in ("资产类型", "原剧资产名称", "项目主资产Key", "项目子资产Key", "剧情身份或功能") if item.get(key)}
            for item in history_assets
        ],
    }
    encoded = json.dumps(configuration, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def source_only_assets_snapshot(assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    localized_key_fragments = (
        "本地化", "目标生成", "最终资产图生成", "自动编译资产图生成",
        "用户编辑资产图生成", "资产图生成提示词", "localized", "targetprompt",
    )
    localized_keys = {
        "localizedprofile", "localizedname", "localizedchildname", "localized_name",
        "assetimageprompt", "promptsnapshot", "targetcountry", "targetera",
        "targetlanguage", "remakemode", "storyboardvisualstyle",
        "legacypreserveddesignconstraints",
    }

    def strip_target_values(value: Any) -> Any:
        if isinstance(value, list):
            return [strip_target_values(item) for item in value]
        if not isinstance(value, dict):
            return value
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            key_lower = key_text.lower()
            normalized = key_lower.replace("_", "").replace("-", "")
            if any(fragment.lower() in key_lower for fragment in localized_key_fragments) or normalized in localized_keys:
                continue
            if key_text in {"角色资产图结构化描述", "本地化角色资产图结构化描述"}:
                continue
            result[key] = strip_target_values(item)
        return result

    snapshots: list[dict[str, Any]] = []
    for asset in assets:
        copied = strip_target_values(json.loads(json.dumps(asset, ensure_ascii=False)))
        payload = copied.get("payload") if isinstance(copied.get("payload"), dict) else {}
        payload["sourceSnapshot"] = build_source_snapshot(asset)
        copied.pop("localized_name", None)
        copied["payload"] = payload
        snapshots.append(copied)
    return snapshots


def strip_asset_image_references(value: Any) -> Any:
    if isinstance(value, list):
        return [strip_asset_image_references(item) for item in value]
    if not isinstance(value, dict):
        return value
    cleaned: dict[str, Any] = {}
    for key, item in value.items():
        normalized = str(key).lower().replace("_", "").replace("-", "")
        is_image_reference = ("path" in normalized or "url" in normalized or "路径" in normalized) and any(token in normalized for token in ["image", "asset", "screenshot", "frame", "图", "截图"])
        if is_image_reference or key in {"原资产候选截图", "原资产截图候选", "目标资产图"}:
            continue
        cleaned[key] = strip_asset_image_references(item)
    return cleaned


def find_cached_asset_source(conn: sqlite3.Connection, cache_key: str) -> dict[str, Any] | None:
    rows = conn.execute("SELECT id, status, result_json FROM tasks WHERE task_type = ? AND status IN (?, ?) ORDER BY created_at DESC", ("asset_extraction", "成功", "失败")).fetchall()
    for row in rows:
        try:
            result = json.loads(row["result_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        source_stage_is_reusable = result.get("sourceStageStatus") == "complete" or row["status"] == "成功"
        if source_stage_is_reusable and result.get("资产源缓存键") == cache_key and isinstance(result.get("资产源事实快照"), list):
            result["_source_task_id"] = row["id"]
            return result
    return None


def normalize_legacy_role_source_structure(structure: Any) -> tuple[dict[str, Any], list[str]]:
    """Adapt old merged source fields into the canonical target inheritance schema.

    This is a provenance-preserving adapter: unobserved facts remain `unknown`
    and are listed by canonical path for review; it never infers visual details.
    """
    source = structure if isinstance(structure, dict) else {}
    output = json.loads(json.dumps(source, ensure_ascii=False))
    aliases = {
        "基础设定": {
            "视觉年龄": ("视觉年龄",), "国家 / 时代 / 朝代": ("国家 / 时代 / 朝代", "国家/时代", "国家/时代/朝代"),
            "身份定位": ("身份定位", "身份"), "性别": ("性别",), "性别判断依据": ("性别判断依据",),
        },
        "身材体态": {
            "头身比": ("头身比",), "身高体感": ("身高体感", "身高"), "体型": ("体型",), "肩颈": ("肩颈",),
            "腰腿": ("腰腿",), "整体体态": ("整体体态", "体态"),
        },
        "脸部特征": {
            "脸型": ("脸型",), "骨相": ("骨相",), "眉形": ("眉形",), "眼形": ("眼形",),
            "虹膜颜色": ("虹膜颜色",), "鼻形": ("鼻形",), "唇形": ("唇形",), "皮肤肤色": ("皮肤肤色",),
            "皮肤质感": ("皮肤质感",),
        },
        "发型": {
            "发型名称": ("发型名称", "名称"), "发色": ("发色", "颜色"), "长度": ("长度",),
            "刘海 / 分线": ("刘海 / 分线", "分线"), "发丝质感": ("发丝质感", "质感"), "整体状态": ("整体状态", "状态"),
        },
        "服装": {
            "服装等级": ("服装等级", "等级"), "场合": ("场合",), "主色": ("主色", "颜色"), "副色": ("副色",),
            "点缀色": ("点缀色",), "内衬": ("内衬",), "中间层": ("中间层",), "外层": ("外层",),
            "领型": ("领型",), "袖型": ("袖型",), "腰线": ("腰线",), "衣长": ("衣长",), "下装": ("下装",),
            "摆型": ("摆型",), "拖尾程度": ("拖尾程度",), "摆与地面关系": ("摆与地面关系",), "鞋子": ("鞋子",),
            "鞋面可见性": ("鞋面可见性",), "脚踝可见性": ("脚踝可见性",), "面料": ("面料",),
            "纹样位置": ("纹样位置",), "结构与廓形": ("结构与廓形",), "工艺与重工程度": ("工艺与重工程度", "工艺"),
            "穿着状态": ("穿着状态", "状态"),
        },
    }
    result: dict[str, Any] = json.loads(json.dumps(source, ensure_ascii=False))
    review: list[str] = []
    for section, fields in aliases.items():
        old_section_names = {"脸部特征": ("脸部特征", "脸部"), "基础设定": ("基础设定",), "身材体态": ("身材体态",), "发型": ("发型",), "服装": ("服装",)}[section]
        old = next((source.get(name) for name in old_section_names if isinstance(source.get(name), dict)), {})
        canonical_source = source.get(section) if isinstance(source.get(section), dict) else {}
        canonical: dict[str, Any] = json.loads(json.dumps(canonical_source, ensure_ascii=False))
        for target_key, names in fields.items():
            if target_key in canonical and canonical[target_key] not in (None, ""):
                continue
            value = next((old[name] for name in names if name in old and old[name] not in (None, "")), None)
            canonical[target_key] = value if value is not None else "unknown"
        # Old schema combined gender and its evidence in one field.
        if section == "基础设定" and canonical["性别"] == "unknown":
            combined = str(old.get("性别及判断依据") or "").strip()
            match = re.match(r"^(男|女|中性|未知|未明确|不确定)(?:[，,；;：:]\s*(.*))?$", combined)
            if match:
                canonical["性别"] = match.group(1)
                if canonical["性别判断依据"] == "unknown" and match.group(2):
                    canonical["性别判断依据"] = match.group(2)
        # Keep merged legacy observations separately; only parse explicit labels.
        if section == "服装" and "内中外层" in old:
            layers = str(old.get("内中外层") or "").strip()
            explicit_layers = {
                key: value.strip() for key, value in re.findall(r"(内衬|内层|中间层|中层|外层)\s*[:：]?\s*([^，,；;+]+)", layers)
            }
            for target_key, labels in {"内衬": ("内衬", "内层"), "中间层": ("中间层", "中层"), "外层": ("外层",)}.items():
                if canonical.get(target_key) == "unknown":
                    found = next((explicit_layers[label] for label in labels if label in explicit_layers), None)
                    if found:
                        canonical[target_key] = found
        if section == "服装" and "领袖" in old:
            collar_sleeve = str(old.get("领袖") or "").strip()
            # Only split when the legacy text explicitly labels the two parts.
            labeled_collar = re.search(r"领型\s*[:：]\s*([^，,；;]+)", collar_sleeve)
            labeled_sleeve = re.search(r"袖型\s*[:：]\s*([^，,；;]+)", collar_sleeve)
            if canonical.get("领型") == "unknown" and labeled_collar:
                canonical["领型"] = labeled_collar.group(1).strip()
            if canonical.get("袖型") == "unknown" and labeled_sleeve:
                canonical["袖型"] = labeled_sleeve.group(1).strip()
        if section == "服装" and canonical.get("纹样位置") == "unknown" and str(old.get("纹样") or "").strip() in {"无", "无明显纹样", "没有纹样"}:
            canonical["纹样位置"] = "不适用：源服装未观察到纹样"
        if section == "服装":
            merged_observations = []
            for alias, label in (("内中外层", "已观察衣件组合（层次未拆分，原文保留）"), ("领袖", "已观察领袖组合（领型/袖型未拆分，原文保留）")):
                raw_observation = str(old.get(alias) or "").strip()
                if raw_observation:
                    merged_observations.append(f"{label}：{raw_observation}")
            if merged_observations:
                existing_silhouette = str(canonical.get("结构与廓形") or "").strip()
                if existing_silhouette.lower() == "unknown":
                    canonical["结构与廓形"] = "；".join(merged_observations)
                    review.append("服装.结构与廓形")
                else:
                    canonical["结构与廓形"] = "；".join([existing_silhouette, *merged_observations])
        result[section] = canonical
        result.setdefault("sourceAliasObservations", {})
        if isinstance(result["sourceAliasObservations"], dict):
            for alias in ("性别及判断依据", "内中外层", "领袖", "鞋面脚踝可见性", "纹样", "状态", "工艺"):
                if alias in old and alias not in result["sourceAliasObservations"]:
                    result["sourceAliasObservations"][f"{section}.{alias}"] = old[alias]
        for field, value in canonical.items():
            if not usable_visual_value(value) or (isinstance(value, str) and any(marker in value for marker in ("未清晰", "未完全显示", "未显示"))):
                review.append(f"{section}.{field}")
    return result, review


def build_source_snapshot(asset: dict[str, Any]) -> dict[str, Any]:
    """Capture source observations without deriving them from target/shared legacy fields."""
    payload = asset.get("payload") if isinstance(asset.get("payload"), dict) else {}
    child = payload.get("当前子资产") if isinstance(payload.get("当前子资产"), dict) else {}
    source_structure = child.get("原剧角色资产图结构化描述") or payload.get("原剧角色资产图结构化描述") or {}
    source_review = list(child.get("needsReview") if isinstance(child.get("needsReview"), list) else [])
    if asset.get("asset_type") == "角色":
        source_structure, normalized_review = normalize_legacy_role_source_structure(source_structure)
        source_review = list(dict.fromkeys([*source_review, *normalized_review]))
    is_prop = asset.get("asset_type") == "道具"
    prop_source_name = pick_payload_text(payload, ["原剧道具名称", "原剧名称", "道具名称"])
    prop_source_description = pick_payload_text(payload, ["原剧道具描述", "原剧描述", "原剧视觉描述"])
    prop_id = pick_payload_text(payload, ["道具编号", "道具ID"])
    prop_variable = pick_payload_text(payload, ["道具资产变量", "变量"]) or str(asset.get("variable") or "")
    prop_facts = {}
    if is_prop:
        prop_facts = {
            "propId": prop_id,
            "variable": prop_variable,
            "sourceName": prop_source_name,
            "sourceDescription": prop_source_description,
            "function": pick_payload_text(payload, ["剧情功能", "剧情用途", "剧情作用"]),
            "screenshotCandidates": payload.get("原剧截图时间点候选") or payload.get("截图候选") or [],
        }
    prop_needs_review = []
    if is_prop and not prop_source_name:
        prop_needs_review.append("原剧道具名称")
    if is_prop and not prop_source_description:
        prop_needs_review.append("原剧道具描述")
    source = {
        "schemaVersion": ASSET_SOURCE_SCHEMA_VERSION,
        "coreDescription": prop_source_description if is_prop else pick_payload_text(payload, ["原剧核心形象描述", "原剧场景描述", "原剧道具描述"]),
        "visualDescription": prop_source_description if is_prop else pick_payload_text(child, ["原剧视觉形象描述", "原剧视觉描述"]) or pick_payload_text(payload, ["原剧视觉形象描述", "原剧视觉描述"]),
        "name": prop_source_name if is_prop else str(asset.get("name") or ""),
        "identity": pick_payload_text(payload, ["剧情身份", "剧情作用", "剧情功能"]),
        "sourceStructuredDescription": source_structure,
        "sourceSceneStructure": payload.get("原剧空间结构描述") or payload.get("原剧场景结构描述") or {},
        "sourcePropFacts": prop_facts or payload.get("原剧道具事实") or {},
        "childId": str(child.get("子资产编号") or payload.get("子资产编号") or prop_id),
        "childName": prop_source_name if is_prop else pick_payload_text(child, ["原剧子资产名称"]),
        "facts": child.get("视觉事实来源") if isinstance(child.get("视觉事实来源"), dict) else {},
        "needsReview": list(dict.fromkeys([*source_review, *prop_needs_review])),
    }
    return source


def initialize_asset_profiles(asset: dict[str, Any]) -> None:
    payload = asset.get("payload") if isinstance(asset.get("payload"), dict) else {}
    payload["资产类型"] = asset.get("asset_type", "")
    payload["sourceSnapshot"] = build_source_snapshot(asset)
    payload["localizedProfile"] = {
        "schemaVersion": ASSET_LOCALIZATION_SCHEMA_VERSION,
        "status": "pending",
        "coreDescription": "",
        "visualDescription": "",
        "structuredDescription": {},
        "assetImagePrompt": "",
        "childId": payload["sourceSnapshot"].get("childId", ""),
    }
    asset["payload"] = payload


def localized_profile_source_hash(payload: dict[str, Any]) -> str:
    source = payload.get("sourceSnapshot") if isinstance(payload.get("sourceSnapshot"), dict) else {}
    encoded = json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def localized_profile_target_context(payload: dict[str, Any], project: dict[str, Any]) -> dict[str, str]:
    explicit_era = str(project.get("target_era") or "").strip()
    return {
        "targetCountry": str(project.get("target_country") or "").strip(),
        "targetEra": explicit_era,
        "targetLanguage": str(project.get("target_language") or "").strip(),
        "sourceHash": localized_profile_source_hash(payload),
    }


def localized_profile_context_matches(payload: dict[str, Any], project: dict[str, Any]) -> bool:
    profile = payload.get("localizedProfile") if isinstance(payload.get("localizedProfile"), dict) else {}
    stored = profile.get("targetContext") if isinstance(profile.get("targetContext"), dict) else {}
    expected = localized_profile_target_context(payload, project)
    return (
        bool(stored)
        and str(stored.get("targetCountry") or "") == expected["targetCountry"]
        and str(stored.get("targetLanguage") or "") == expected["targetLanguage"]
        and str(stored.get("sourceHash") or "") == expected["sourceHash"]
        and (not expected["targetEra"] or str(stored.get("targetEra") or "") == expected["targetEra"])
    )


def project_asset_profile_to_legacy(payload: dict[str, Any], project: dict[str, Any]) -> dict[str, Any]:
    """Build a temporary legacy-shaped view from exactly one selected profile."""
    result = json.loads(json.dumps(payload, ensure_ascii=False))
    overseas = str(project.get("target_country") or "").strip() not in {"", "中国", "中国大陆"}
    profile = result.get("localizedProfile") if overseas else result.get("sourceSnapshot")
    if overseas and not localized_profile_context_matches(result, project):
        profile = None
    child = result.get("当前子资产") if isinstance(result.get("当前子资产"), dict) else {}
    if overseas:
        for key in list(result):
            if key.startswith("本地化") or key in {"角色资产图结构化描述", "角色资产图生成提示词", "场景资产图生成提示词", "道具资产图生成提示词"}:
                result.pop(key, None)
        for key in list(child):
            if key.startswith("本地化") or key in {"角色资产图结构化描述", "角色资产图生成提示词", "场景资产图生成提示词", "道具资产图生成提示词"}:
                child.pop(key, None)
        if not isinstance(profile, dict) or profile.get("status") not in {"complete", "source_review"}:
            result["当前子资产"] = child
            return result
        core = str(profile.get("coreDescription") or "")
        visual = str(profile.get("visualDescription") or "")
        structure = profile.get("structuredDescription") if isinstance(profile.get("structuredDescription"), dict) else {}
        image_prompt = str(profile.get("assetImagePrompt") or "")
        if core and payload.get("资产类型") == "场景":
            result["本地化场景描述"] = core
        elif core and payload.get("资产类型") == "道具":
            result["本地化道具描述"] = core
        elif core:
            result["本地化核心形象描述"] = core
        if visual:
            child["本地化视觉形象描述"] = visual
        if structure:
            child["角色资产图结构化描述"] = structure
            result["角色资产图结构化描述"] = structure
        if image_prompt:
            child["角色资产图生成提示词"] = image_prompt
            result["角色资产图生成提示词"] = image_prompt
        if profile.get("localizedName"):
            if payload.get("资产类型") == "场景":
                result["本地化场景名称"] = profile["localizedName"]
            elif payload.get("资产类型") == "道具":
                result["本地化道具名称"] = profile["localizedName"]
            else:
                result["本地化角色名称"] = profile["localizedName"]
        if profile.get("localizedChildName"):
            child["本地化子资产名称"] = profile["localizedChildName"]
        if payload.get("资产类型") == "场景":
            result["场景资产图生成提示词"] = image_prompt
        elif payload.get("资产类型") == "道具":
            result["道具资产图生成提示词"] = image_prompt
    else:
        for key in list(result):
            if key.startswith("本地化") or key in {"角色资产图结构化描述", "角色资产图生成提示词", "场景资产图生成提示词", "道具资产图生成提示词"}:
                result.pop(key, None)
        for key in list(child):
            if key.startswith("本地化") or key in {"角色资产图结构化描述", "角色资产图生成提示词", "场景资产图生成提示词", "道具资产图生成提示词"}:
                child.pop(key, None)
        if not isinstance(profile, dict):
            result["当前子资产"] = child
            return result
        core = str(profile.get("coreDescription") or "")
        visual = str(profile.get("visualDescription") or "")
        structure = profile.get("sourceStructuredDescription") if isinstance(profile.get("sourceStructuredDescription"), dict) else {}
        if core:
            if payload.get("资产类型") == "场景":
                result["原剧场景描述"] = core
                result["本地化场景描述"] = core
            elif payload.get("资产类型") == "道具":
                result["原剧道具描述"] = core
                result["本地化道具描述"] = core
            else:
                result["原剧核心形象描述"] = core
                result["本地化核心形象描述"] = core
        if visual:
            if payload.get("资产类型") == "角色":
                child["原剧视觉形象描述"] = visual
                child["本地化视觉形象描述"] = visual
            else:
                child["本地化视觉描述"] = visual
        if structure:
            child["角色资产图结构化描述"] = structure
            result["角色资产图结构化描述"] = structure
        if profile.get("name"):
            if payload.get("资产类型") == "角色":
                result["本地化角色名称"] = profile["name"]
            elif payload.get("资产类型") == "场景":
                result["本地化场景名称"] = profile["name"]
            elif payload.get("资产类型") == "道具":
                result["本地化道具名称"] = profile["name"]
        if profile.get("childName"):
            child["本地化子资产名称"] = profile["childName"]
    result["当前子资产"] = child
    return result


def asset_localization_cache_key(source_assets: list[dict[str, Any]], project: dict[str, Any], agent: dict[str, Any]) -> str:
    config = load_models_config()
    provider_config = config.get("volcengine", {})
    input_data = {
        "source_schema": ASSET_SOURCE_SCHEMA_VERSION,
        "localization_schema": ASSET_LOCALIZATION_SCHEMA_VERSION,
        "source_assets": [
            {
                "assetType": asset.get("asset_type", ""),
                "variable": asset.get("variable", ""),
                "sourceSnapshot": asset.get("payload", {}).get("sourceSnapshot", {}),
            }
            for asset in source_assets
        ],
        "legacy_preserved_design_constraints": [sanitize_legacy_preservation_constraints(asset.get("payload", {}).get("legacyPreservedDesignConstraints", {})) for asset in source_assets],
        "target_country": project.get("target_country", ""),
        "target_era": resolve_target_era(project, source_assets),
        "target_language": project.get("target_language", ""),
        "user_visual_settings": [
            {
                "description": asset.get("payload", {}).get("目标生成描述", ""),
                "provenance": asset.get("payload", {}).get("targetVisualLockProvenance") or asset.get("payload", {}).get("目标生成描述来源", ""),
            }
            for asset in source_assets
        ],
        "model": agent.get("model_name") or agent.get("model", ""),
        "temperature": agent.get("temperature"),
        "output_format": agent.get("output_format", "json"),
        "max_output_tokens": provider_config.get("maxOutputTokens"),
        "system_prompt": ASSET_LOCALIZATION_COMPACT_PROMPT,
    }
    encoded = json.dumps(input_data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def sanitize_legacy_preservation_constraints(record: Any) -> dict[str, Any]:
    if not isinstance(record, dict) or record.get("provenance") != "legacy_preserved_design_constraints":
        return {}
    structure = record.get("structuredDescription") if isinstance(record.get("structuredDescription"), dict) else {}
    allowed_fields = {
        "基础设定": {"性别", "性别判断依据", "视觉年龄", "身份定位", "性格关键词", "气质氛围"},
        "身材体态": {"头身比", "身高体感", "体型", "肩颈", "腰腿", "肌肉 / 线条", "整体体态"},
        "服装": {"服装等级", "场合", "主色", "副色", "点缀色", "内衬", "中间层", "外层", "领型", "袖型", "腰线", "衣长", "下装", "摆型", "拖尾程度", "摆与地面关系", "鞋子", "鞋面可见性", "脚踝可见性", "面料", "纹样", "纹样位置", "结构与廓形", "工艺与重工程度", "穿着状态"},
    }
    preserved = {
        section: {key: value for key, value in fields.items() if key in allowed_fields.get(section, set())}
        for section, fields in structure.items()
        if section in allowed_fields and isinstance(fields, dict)
    }
    accessories = structure.get("固定穿戴配饰")
    if isinstance(accessories, list):
        preserved["固定穿戴配饰"] = accessories
    return {"schemaVersion": record.get("schemaVersion", "legacy-preserved-design-constraints-v1"), "provenance": record["provenance"], "structuredDescription": preserved}


def resolve_target_era(project: dict[str, Any], assets: list[dict[str, Any]]) -> str:
    explicit = str(project.get("target_era") or "").strip()
    if explicit:
        return explicit
    contexts = [
        str(asset.get("payload", {}).get("目标生成描述") or "")
        + " " + str(asset.get("payload", {}).get("sourceSnapshot", {}).get("sourceStructuredDescription", {}).get("基础设定", {}).get("国家 / 时代 / 朝代") or "")
        + " " + str(asset.get("payload", {}).get("sourceSnapshot", {}).get("coreDescription") or "")
        for asset in assets if isinstance(asset.get("payload"), dict)
    ]
    combined = " ".join(contexts)
    era_tokens = ("当代", "现代", "近现代", "民国", "清代", "明代", "宋代", "唐代", "古代", "中世纪", "维多利亚时代")
    return next((token for token in era_tokens if token in combined), "时代沿用原片事实")


def find_cached_asset_localization(conn: sqlite3.Connection, cache_key: str) -> dict[str, Any] | None:
    rows = conn.execute("SELECT id, result_json FROM tasks WHERE task_type=? AND status=? ORDER BY created_at DESC", ("asset_extraction", "成功")).fetchall()
    for row in rows:
        try:
            result = json.loads(row["result_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        profiles = result.get("localizedProfileSnapshot")
        if result.get("资产本地化缓存键") == cache_key and isinstance(profiles, list):
            return {"task_id": row["id"], "profiles": profiles}
    return None


def canonical_localized_role_descriptions(profile: dict[str, Any]) -> tuple[str, str]:
    """Render all target-facing role prose from the canonical structured profile."""
    structure = profile.get("structuredDescription") if isinstance(profile.get("structuredDescription"), dict) else {}
    basics = structure.get("基础设定") if isinstance(structure.get("基础设定"), dict) else {}
    face = structure.get("脸部特征") if isinstance(structure.get("脸部特征"), dict) else {}
    hair = structure.get("发型") if isinstance(structure.get("发型"), dict) else {}
    body = structure.get("身材体态") if isinstance(structure.get("身材体态"), dict) else {}
    clothing = structure.get("服装") if isinstance(structure.get("服装"), dict) else {}

    def segment(label: str, fields: dict[str, Any], keys: tuple[str, ...]) -> str:
        values = [f"{key}{fields[key]}" for key in keys if usable_visual_value(fields.get(key))]
        return f"{label}{'、'.join(values)}" if values else ""

    identity = segment("", basics, ("性别", "视觉年龄", "族裔外貌", "身份定位"))
    face_text = segment("脸部：", face, ("目标族裔背景", "皮肤肤色", "脸型", "骨相", "眉形", "眼形", "虹膜颜色", "鼻形", "唇形", "面部毛发", "妆感", "皮肤质感"))
    hair_text = segment("发型：", hair, ("发色", "长度", "发型名称", "刘海 / 分线", "发丝质感", "整体状态"))
    body_text = segment("体态：", body, ("头身比", "身高体感", "体型", "肩颈", "腰腿", "肌肉 / 线条", "整体体态"))
    clothing_for_render = dict(clothing)
    if usable_visual_value(clothing_for_render.get("穿着状态")):
        clothing_for_render["穿着状态"] = stable_wear_state_text(str(clothing_for_render["穿着状态"]))
    clothing_text = segment("服装：", clothing_for_render, ("服装等级", "场合", "主色", "副色", "点缀色", "内衬", "中间层", "外层", "领型", "袖型", "腰线", "衣长", "下装", "摆型", "拖尾程度", "摆与地面关系", "鞋子", "鞋面可见性", "脚踝可见性", "面料", "纹样", "结构与廓形", "穿着状态"))
    localized_name = str(profile.get("localizedName") or "").strip()
    if localized_name:
        identity = f"目标角色姓名：{localized_name}；{identity}" if identity else f"目标角色姓名：{localized_name}"
    core = "；".join(part for part in (identity, face_text, hair_text, body_text, clothing_text) if part)
    visual = "；".join(part for part in (face_text, hair_text, body_text, clothing_text) if part)
    return core, visual


def build_localized_role_structure(
    source_snapshot: dict[str, Any],
    target_patch: Any,
    target_country: str,
    target_era: str,
    legacy_preserved_design_constraints: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Inherit only approved non-cultural source facts, then install target face/hair patch."""
    errors: list[str] = []
    if not isinstance(target_patch, dict):
        return {}, ["localizedProfile.structuredDescription必须是目标字段patch对象"]
    source = source_snapshot.get("sourceStructuredDescription") if isinstance(source_snapshot.get("sourceStructuredDescription"), dict) else {}
    preserved_record = legacy_preserved_design_constraints if isinstance(legacy_preserved_design_constraints, dict) else {}
    if preserved_record and preserved_record.get("provenance") != "legacy_preserved_design_constraints":
        errors.append("legacyPreservedDesignConstraints缺少明确旧设计来源标记")
        preserved_record = {}
    preserved_record = sanitize_legacy_preservation_constraints(preserved_record)
    preserved = preserved_record.get("structuredDescription") if isinstance(preserved_record.get("structuredDescription"), dict) else {}
    allowed_basics = ("性别", "性别判断依据", "视觉年龄", "身份定位", "性格关键词", "气质氛围")
    allowed_body = ("头身比", "身高体感", "体型", "肩颈", "腰腿", "肌肉 / 线条", "整体体态")
    allowed_clothing = (
        "服装等级", "场合", "主色", "副色", "点缀色", "内衬", "中间层", "外层", "领型", "袖型", "腰线", "衣长", "下装", "摆型", "拖尾程度", "摆与地面关系", "鞋子", "鞋面可见性", "脚踝可见性", "面料", "纹样", "纹样位置", "结构与廓形", "工艺与重工程度", "穿着状态",
    )
    def inherited(section: str, allowed: tuple[str, ...]) -> dict[str, Any]:
        source_fields = source.get(section) if isinstance(source.get(section), dict) else {}
        preserved_fields = preserved.get(section) if isinstance(preserved.get(section), dict) else {}
        values = {key: source_fields[key] for key in allowed if key in source_fields}
        values.update({key: preserved_fields[key] for key in allowed if key in preserved_fields and usable_visual_value(preserved_fields[key])})
        return values

    localized = {
        "基础设定": inherited("基础设定", allowed_basics),
        "身材体态": inherited("身材体态", allowed_body),
        "脸部特征": {},
        "发型": {},
        "服装": inherited("服装", allowed_clothing),
    }
    fixed_accessories = preserved.get("固定穿戴配饰") or source.get("固定穿戴配饰")
    if isinstance(fixed_accessories, list):
        localized["固定穿戴配饰"] = json.loads(json.dumps(fixed_accessories, ensure_ascii=False))

    allowed_patch_keys = {
        "基础设定": {"族裔外貌"},
        "脸部特征": {"目标族裔背景", "脸型", "骨相", "眉形", "眼形", "虹膜颜色", "鼻形", "唇形", "面部毛发", "妆感", "皮肤肤色", "皮肤质感", "气色状态", "静态微表情", "眼神气质"},
        "发型": {"发型名称", "发色", "长度", "刘海 / 分线", "发丝质感", "整体状态"},
    }
    for section, fields in target_patch.items():
        if section not in allowed_patch_keys or not isinstance(fields, dict):
            errors.append(f"目标patch包含禁止分区或错误类型：{section}")
            continue
        for key, value in fields.items():
            if key not in allowed_patch_keys[section]:
                errors.append(f"目标patch包含禁止字段：{section}.{key}")
                continue
            localized[section][key] = value
    localized["基础设定"]["国家 / 时代 / 朝代"] = target_era or "当代"

    required = {
        "基础设定": {"族裔外貌"},
        "脸部特征": allowed_patch_keys["脸部特征"],
        "发型": allowed_patch_keys["发型"],
    }
    for section, keys in required.items():
        for key in keys:
            if not usable_visual_value(localized[section].get(key)):
                errors.append(f"localizedProfile.structuredDescription.{section}.{key}缺失或不可用")
    ethnicity = str(localized["基础设定"].get("族裔外貌") or "").strip()
    face_ethnicity = str(localized["脸部特征"].get("目标族裔背景") or "").strip()
    if ethnicity and face_ethnicity and ethnicity != face_ethnicity:
        errors.append("基础设定.族裔外貌与脸部特征.目标族裔背景必须相同")
    return localized, errors


def required_source_review_paths(structure: Any, source_snapshot: dict[str, Any]) -> list[str]:
    """Return only unresolved source facts that still gate role image generation.

    Target face and hair fields are supplied by the localization patch; optional
    source fields remain reviewable but do not hold the asset image pipeline.
    """
    target_supplied = {("基础设定", "国家 / 时代 / 朝代"), ("脸部特征", "脸型"), ("发型", "发型名称")}
    lower_body_unobservable = lower_body_details_unobservable(structure)
    source_review = set(source_snapshot.get("needsReview") or []) if isinstance(source_snapshot.get("needsReview"), list) else set()
    result: list[str] = []
    fields = structure if isinstance(structure, dict) else {}
    for section, field in ROLE_REQUIRED_VISUAL_PATHS:
        if (section, field) in target_supplied:
            continue
        if lower_body_unobservable and f"{section}.{field}" in UNOBSERVABLE_LOWER_BODY_PATHS:
            continue
        if section == "服装" and field == "鞋子" and footwear_hidden_by_garment(fields):
            continue
        section_value = fields.get(section) if isinstance(fields.get(section), dict) else {}
        value = section_value.get(field)
        path = f"{section}.{field}"
        if path in source_review or not usable_visual_value(value):
            result.append(path)
    return list(dict.fromkeys(result))


def merge_localized_profiles(
    assets: list[dict[str, Any]],
    result: dict[str, Any],
    target_country: str = "",
    target_era: str = "",
    target_language: str = "",
    cached_final_profiles: bool = False,
) -> list[str]:
    # Localization responses must use the stable internal variable exactly.
    # Display names and source aliases are intentionally not accepted here.
    by_variable = {str(asset.get("variable") or ""): asset for asset in assets if asset.get("variable")}
    errors: list[str] = []
    returned_variables: set[str] = set()
    for item in result.get("localizedAssets", []) if isinstance(result.get("localizedAssets"), list) else []:
        if not isinstance(item, dict):
            continue
        variable = str(item.get("assetVariable") or item.get("资产变量") or "")
        asset = by_variable.get(variable)
        profile = item.get("localizedProfile")
        if asset and is_group_portrait_asset(asset):
            continue
        if not asset or not isinstance(profile, dict):
            errors.append(f"本地化结果无法匹配资产：{variable or '(empty)'}")
            continue
        if variable in returned_variables:
            errors.append(f"本地化结果包含重复稳定资产变量：{variable}")
            continue
        returned_variables.add(variable)
        payload = asset.get("payload") if isinstance(asset.get("payload"), dict) else {}
        child = payload.get("当前子资产") if isinstance(payload.get("当前子资产"), dict) else {}
        localized_structure = profile.get("structuredDescription") if isinstance(profile.get("structuredDescription"), dict) else {}
        profile_errors: list[str] = []
        if asset.get("asset_type") == "角色" and not cached_final_profiles:
            localized_structure, profile_errors = build_localized_role_structure(
                payload.get("sourceSnapshot", {}), localized_structure, target_country, target_era,
                payload.get("legacyPreservedDesignConstraints"),
            )
        # 缓存回放的是已完成合并的最终结构（首次发布时已通过 patch 白名单校验），
        # 不能再次按模型原始 patch 白名单校验——合并产物里的继承字段
        # （基础设定.性别/视觉年龄/身份定位、身材体态、服装）会被误判为禁止字段。
        profile = {
            "schemaVersion": ASSET_LOCALIZATION_SCHEMA_VERSION,
            "status": "invalid" if profile_errors else "complete",
            "targetContext": {
                "targetCountry": target_country,
                "targetEra": target_era or "当代",
                "targetLanguage": target_language,
                "sourceHash": localized_profile_source_hash(payload),
            },
            "localizedName": str(profile.get("localizedName") or explicit_user_localized_name(payload) or ""),
            "localizedChildName": str(profile.get("localizedChildName") or payload.get("sourceSnapshot", {}).get("childName") or ""),
            "coreDescription": str(profile.get("coreDescription") or ""),
            "visualDescription": str(profile.get("visualDescription") or ""),
            "structuredDescription": localized_structure,
            "assetImagePrompt": "" if asset.get("asset_type") == "角色" else str(profile.get("assetImagePrompt") or ""),
            "childId": str(payload.get("sourceSnapshot", {}).get("childId") or ""),
        }
        if asset.get("asset_type") == "角色":
            # The structured target profile is canonical. Never let separate
            # free-text fields reintroduce conflicting hair, skin or face traits.
            profile["coreDescription"], profile["visualDescription"] = canonical_localized_role_descriptions(profile)
            payload["localizedProfile"] = profile
            refresh_role_source_review_profile(payload)
            profile = payload["localizedProfile"]
        payload["localizedProfile"] = profile
        errors.extend(f"{variable}: {error}" for error in profile_errors)
        projected = project_asset_profile_to_legacy(payload, {"remake_mode": "overseas_localization", "target_country": target_country or "非中国目标", "target_era": target_era or "当代", "target_language": target_language})
        payload.update({key: value for key, value in projected.items() if key != "当前子资产"})
        payload["当前子资产"] = projected.get("当前子资产", child)
        asset["localized_name"] = profile["localizedName"] or asset.get("name", "")
        asset["payload"] = payload
    # Explicit group portraits do not have a single-person face contract. Keep
    # their source facts and assets intact, but never synthesize one target face.
    for asset in assets:
        if not is_group_portrait_asset(asset):
            continue
        variable = str(asset.get("variable") or "")
        if variable in returned_variables:
            continue
        payload = asset.get("payload") if isinstance(asset.get("payload"), dict) else {}
        child = payload.get("当前子资产") if isinstance(payload.get("当前子资产"), dict) else {}
        source = payload.get("sourceSnapshot") if isinstance(payload.get("sourceSnapshot"), dict) else {}
        payload["localizedProfile"] = {
            "schemaVersion": ASSET_LOCALIZATION_SCHEMA_VERSION,
            "status": "pending",
            "needsReview": True,
            "reviewReason": "群像资产不适用单人脸部画像；需按群像构图单独审核",
            "targetContext": {
                "targetCountry": target_country,
                "targetEra": target_era or "当代",
                "targetLanguage": target_language,
                "sourceHash": localized_profile_source_hash(payload),
            },
            "localizedName": "",
            "localizedChildName": str(source.get("childName") or ""),
            "coreDescription": "",
            "visualDescription": "",
            "structuredDescription": {},
            "assetImagePrompt": "",
            "childId": str(source.get("childId") or ""),
        }
        asset["localized_name"] = asset.get("name", "")
        projected = project_asset_profile_to_legacy(payload, {"remake_mode": "overseas_localization", "target_country": target_country or "非中国目标", "target_era": target_era or "当代", "target_language": target_language})
        payload.update({key: value for key, value in projected.items() if key != "当前子资产"})
        payload["当前子资产"] = projected.get("当前子资产", child)
        asset["payload"] = payload
    found = {asset.get("variable") for asset in assets if isinstance(asset.get("payload", {}).get("localizedProfile"), dict) and asset["payload"]["localizedProfile"].get("status") in {"complete", "source_review"}}
    errors.extend(f"缺少目标本地化画像：{asset.get('variable', '')}" for asset in assets if not is_group_portrait_asset(asset) and asset.get("variable") not in found)
    return errors


def is_group_portrait_asset(asset: dict[str, Any]) -> bool:
    if str(asset.get("asset_type") or "") != "角色":
        return False
    payload = asset.get("payload") if isinstance(asset.get("payload"), dict) else {}
    source = payload.get("sourceSnapshot") if isinstance(payload.get("sourceSnapshot"), dict) else {}
    explicit_kind = " ".join(str(source.get(key) or "") for key in ("assetKind", "sourceKind", "角色类型", "资产类型"))
    explicit_names = " ".join(str(value or "") for value in (asset.get("name"), source.get("name"), payload.get("原剧资产名称")))
    kind_markers = ("群像", "人群角色", "多人角色", "group portrait", "crowd character")
    name_markers = ("群像", "宾客群", "观众群", "队友群", "人群角色", "group portrait", "crowd")
    return any(marker.lower() in explicit_kind.lower() for marker in kind_markers) or any(marker.lower() in explicit_names.lower() for marker in name_markers)


def explicit_user_visual_lock(payload: dict[str, Any]) -> str:
    provenance = str(payload.get("targetVisualLockProvenance") or payload.get("目标生成描述来源") or "").strip().lower()
    if provenance not in {"user", "user_override", "explicit_user_lock", "用户明确设定", "用户手动设定"}:
        return ""
    return str(payload.get("目标生成描述") or "").strip()


def explicit_user_localized_name(payload: dict[str, Any]) -> str:
    provenance = str(payload.get("targetNameProvenance") or payload.get("本地化角色名称来源") or "").strip().lower()
    if provenance not in {"user", "user_override", "用户明确设定", "用户手动设定"}:
        return ""
    return str(payload.get("用户指定目标角色名称") or payload.get("本地化角色名称") or "").strip()


def persist_asset_localization_raw(stage: str, raw: str, input_payload: dict[str, Any], cache_key: str = "") -> str:
    """Atomically archive a model response before parsing or validating it."""
    audit_dir = DATA_DIR / "asset-localization-audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"{stage}-{cache_key[:12] or 'uncached'}-"
    temp_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=audit_dir,
            prefix=prefix, suffix=".tmp", delete=False,
        ) as handle:
            temp_name = handle.name
            json.dump({"stage": stage, "cacheKey": cache_key, "input": input_payload, "raw": raw}, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        final_path = Path(temp_name).with_suffix(".json")
        os.replace(temp_name, final_path)
        return str(final_path)
    except Exception:
        if temp_name:
            try:
                Path(temp_name).unlink(missing_ok=True)
            except OSError:
                pass
        raise


def is_western_target_country(country: str) -> bool:
    value = str(country or "").strip().lower()
    western_countries = (
        "美国", "加拿大", "英国", "爱尔兰", "法国", "德国", "西班牙", "意大利", "葡萄牙", "荷兰", "比利时", "瑞士", "奥地利",
        "瑞典", "挪威", "丹麦", "芬兰", "冰岛", "澳大利亚", "新西兰", "美国合众国", "united states", "usa", "us", "canada",
        "united kingdom", "uk", "ireland", "france", "germany", "spain", "italy", "portugal", "netherlands", "belgium", "switzerland",
        "australia", "new zealand", "sweden", "norway", "denmark", "finland", "iceland",
    )
    return any(value == country or value.startswith(country + " ") for country in western_countries)


def localized_profile_validation_errors(asset: dict[str, Any], target_country: str) -> list[str]:
    payload = asset.get("payload") if isinstance(asset.get("payload"), dict) else {}
    profile = payload.get("localizedProfile") if isinstance(payload.get("localizedProfile"), dict) else {}
    errors: list[str] = []
    if profile.get("status") not in {"complete", "source_review"}:
        return ["localizedProfile缺失"]
    if asset.get("asset_type") != "角色":
        if not usable_visual_value(profile.get("coreDescription")):
            errors.append("目标自包含视觉描述缺失")
        if asset.get("asset_type") == "道具":
            errors.extend(prop_source_target_category_errors(asset, profile))
        return errors
    structure = profile.get("structuredDescription") if isinstance(profile.get("structuredDescription"), dict) else {}
    basics = structure.get("基础设定") if isinstance(structure.get("基础设定"), dict) else {}
    face = structure.get("脸部特征") if isinstance(structure.get("脸部特征"), dict) else {}
    for label, value in [
        ("基础设定.族裔外貌", basics.get("族裔外貌")),
        ("脸部特征.目标族裔背景", face.get("目标族裔背景")),
        ("目标核心形象", profile.get("coreDescription")),
        ("目标子资产形象", profile.get("visualDescription")),
    ]:
        if not usable_visual_value(value):
            errors.append(f"{label}缺失或不可用")
    if profile.get("status") != "source_review":
        errors.extend(role_structured_missing_paths(structure))
    target_ethnicity_text = " ".join([
        str(basics.get("族裔外貌") or ""),
        str(face.get("目标族裔背景") or ""),
    ])
    lock = explicit_user_visual_lock(payload)
    normalized_ethnicity = re.sub(r"[\s_\-‐‑‒–—]+", "", target_ethnicity_text).casefold()
    east_asian_ethnicity_markers = (
        "东亚裔", "东亚人", "东亚混血", "华裔", "中国裔", "中国人", "韩裔", "韩国裔", "韩国人",
        "日裔", "日本裔", "日本人", "亚洲裔混血", "亚裔混血", "泛亚裔",
        "eastasian", "asianamerican", "chineseamerican", "chineseamerican", "koreanamerican", "japaneseamerican",
        "eastasianamerican", "chineseamericanmixed", "koreanamericanmixed", "japaneseamericanmixed",
    )
    if is_western_target_country(target_country) and any(marker.casefold() in normalized_ethnicity for marker in east_asian_ethnicity_markers) and not lock:
        errors.append("欧美目标未提供用户族裔锁定但仍输出东亚/泛亚裔身份；需按目标背景重新设计具体族裔")
    target_text = " ".join([
        str(profile.get("coreDescription") or ""), str(profile.get("visualDescription") or ""),
        json.dumps(structure, ensure_ascii=False),
    ])
    chinese_scene_markers = ("现代中国", "中国婚礼", "中国传统婚礼", "中国古代", "当代中国", "Chinese wedding", "modern China")
    if is_western_target_country(target_country) and any(marker.lower() in target_text.lower() for marker in chinese_scene_markers):
        errors.append("目标画像仍含中国源国家/婚礼场景描述")
    return list(dict.fromkeys(errors))


PROP_OBJECT_CATEGORY_TERMS: dict[str, tuple[str, ...]] = {
    "chicken_crate": ("鸡笼", "塑料周转笼", "周转笼", "鸡运输笼", "chicken crate", "chicken cage", "plastic crate"),
    "sack_or_bag": ("麻袋", "布袋", "布包", "行李包", "旅行包", "burlap sack", "duffel bag", "travel bag", "cloth bag", "sack"),
    "boutonniere": ("胸花", "新郎胸花", "玫瑰胸花", "boutonniere", "corsage", "lapel flower"),
    "bridal_necklace": ("项链", "钻饰项链", "necklace", "bridal necklace"),
    "bridal_headpiece": ("头纱", "头饰", "发饰", "花朵发饰", "veil", "bridal headpiece", "hairpiece"),
    "bow_tie": ("领结", "蝴蝶结领结", "bow tie", "bowtie", "necktie bow"),
    "aisle_floral_decor": ("路引", "花艺", "花卉装饰", "鸟笼形装饰", "aisle arrangement", "floral stand", "wedding aisle decor"),
}


def prop_source_target_category_errors(asset: dict[str, Any], profile: dict[str, Any]) -> list[str]:
    payload = asset.get("payload") if isinstance(asset.get("payload"), dict) else {}
    source = payload.get("sourceSnapshot") if isinstance(payload.get("sourceSnapshot"), dict) else {}
    facts = source.get("sourcePropFacts") if isinstance(source.get("sourcePropFacts"), dict) else {}
    source_text = str(facts.get("sourceName") or "")
    target_text = str(profile.get("localizedName") or "")
    source_categories: set[str] = set()
    target_categories: set[str] = set()
    for category, terms in PROP_OBJECT_CATEGORY_TERMS.items():
        if any(term.lower() in source_text.lower() for term in terms):
            source_categories.add(category)
        if any(term.lower() in target_text.lower() for term in terms):
            target_categories.add(category)
    # Reject only a clear one-object swap. Composite names and unfamiliar valid
    # translations remain reviewable instead of being blocked by a keyword list.
    if len(source_categories) == len(target_categories) == 1 and source_categories != target_categories:
        return [f"{asset.get('variable') or asset.get('name')}: 源道具主类别 {next(iter(source_categories))} 与目标主名称 {next(iter(target_categories))} 冲突"]
    return []


def prop_source_target_category_warnings(asset: dict[str, Any], profile: dict[str, Any]) -> list[str]:
    payload = asset.get("payload") if isinstance(asset.get("payload"), dict) else {}
    source = payload.get("sourceSnapshot") if isinstance(payload.get("sourceSnapshot"), dict) else {}
    facts = source.get("sourcePropFacts") if isinstance(source.get("sourcePropFacts"), dict) else {}
    source_text = str(facts.get("sourceName") or "")
    target_text = str(profile.get("localizedName") or "")
    source_categories = {
        category for category, terms in PROP_OBJECT_CATEGORY_TERMS.items()
        if any(term.lower() in source_text.lower() for term in terms)
    }
    target_categories = {
        category for category, terms in PROP_OBJECT_CATEGORY_TERMS.items()
        if any(term.lower() in target_text.lower() for term in terms)
    }
    missing = source_categories - target_categories
    if missing:
        return [f"源道具名称包含的类别未完整反映在目标名称中，请核对目标描述：{', '.join(sorted(missing))}"]
    if not target_categories:
        return ["目标名称未匹配已知物件类别，需人工核对源物件身份"]
    return []


def run_target_localization(
    conn: sqlite3.Connection,
    assets: list[dict[str, Any]],
    project: dict[str, Any],
    agent: dict[str, Any],
) -> dict[str, Any]:
    target_country = str(project.get("target_country") or "").strip()
    if target_country in {"", "中国", "中国大陆"}:
        return {"stage": "source_only", "cache_hit": False, "cache_key": "", "errors": [], "profiles": []}
    incomplete_source_props = []
    for asset in assets:
        if asset.get("asset_type") != "道具":
            continue
        source = asset.get("payload", {}).get("sourceSnapshot", {}) if isinstance(asset.get("payload"), dict) else {}
        facts = source.get("sourcePropFacts") if isinstance(source.get("sourcePropFacts"), dict) else {}
        missing = [
            label for label, value in (
                ("原剧道具名称", facts.get("sourceName")),
                ("原剧道具描述", facts.get("sourceDescription")),
                ("道具编号/稳定变量", facts.get("propId") or facts.get("variable")),
            )
            if not usable_visual_value(value)
        ]
        if missing:
            incomplete_source_props.append(f"{asset.get('variable') or asset.get('name')}: 源道具事实缺失 {', '.join(missing)}")
    if incomplete_source_props:
        return {
            "stage": "source_review", "cache_hit": False, "cache_key": "", "errors": incomplete_source_props,
            "profiles": [], "rawAttempts": [],
        }
    target_era = resolve_target_era(project, assets)
    cache_key = asset_localization_cache_key(assets, project, agent)
    cached = find_cached_asset_localization(conn, cache_key)
    base_assets = json.loads(json.dumps(assets, ensure_ascii=False))
    request_items = []
    for asset in assets:
        if is_group_portrait_asset(asset):
            continue
        payload = asset.get("payload") if isinstance(asset.get("payload"), dict) else {}
        request_items.append({
            "assetType": asset.get("asset_type", ""),
            "assetVariable": asset.get("variable", ""),
            "localizedNameInput": explicit_user_localized_name(payload),
            "userTargetVisualSetting": payload.get("目标生成描述", ""),
            "userTargetVisualLock": explicit_user_visual_lock(payload),
            "userTargetVisualLockProvenance": payload.get("targetVisualLockProvenance") or payload.get("目标生成描述来源") or "",
            "legacyPreservedDesignConstraints": sanitize_legacy_preservation_constraints(payload.get("legacyPreservedDesignConstraints", {})),
            "sourceSnapshot": payload.get("sourceSnapshot", {}),
        })
    local_input = {
        "targetCountry": target_country,
        "targetEra": target_era,
        "targetLanguage": project.get("target_language", ""),
        "assets": request_items,
    }
    attempts: list[dict[str, Any]] = []
    cache_repaired = False
    if cached:
        localized_assets = cached["profiles"]
        attempts.append({"source": "cache", "raw": "", "localizedAssets": localized_assets})
    elif not request_items:
        localized_assets = []
        attempts.append({"source": "group_pending", "raw": "", "localizedAssets": []})
    else:
        raw = call_compact_video_json(agent, local_input, ASSET_LOCALIZATION_COMPACT_PROMPT)
        audit_path = persist_asset_localization_raw("target-localization", raw, local_input, cache_key)
        parsed = parse_json_object(raw, {})
        localized_assets = parsed.get("localizedAssets") if isinstance(parsed.get("localizedAssets"), list) else []
        attempts.append({"source": "model", "raw": raw, "auditPath": audit_path, "localizedAssets": localized_assets})

    def validate_candidate(candidate: list[dict[str, Any]], *, from_cache: bool = False) -> list[str]:
        assets[:] = json.loads(json.dumps(base_assets, ensure_ascii=False))
        candidate_errors = merge_localized_profiles(
            assets, {"localizedAssets": candidate}, target_country, target_era,
            str(project.get("target_language") or ""), cached_final_profiles=from_cache,
        )
        candidate_errors.extend(
            f"{asset.get('variable')}: {error}"
            for asset in assets if not is_group_portrait_asset(asset)
            for error in localized_profile_validation_errors(asset, target_country)
        )
        candidate_errors = list(dict.fromkeys(candidate_errors))
        if candidate_errors:
            assets[:] = json.loads(json.dumps(base_assets, ensure_ascii=False))
            for asset in assets:
                payload = asset.get("payload") if isinstance(asset.get("payload"), dict) else {}
                source = payload.get("sourceSnapshot") if isinstance(payload.get("sourceSnapshot"), dict) else {}
                payload["localizedProfile"] = {
                    "schemaVersion": ASSET_LOCALIZATION_SCHEMA_VERSION,
                    "status": "invalid",
                    "localizedName": "",
                    "localizedChildName": "",
                    "coreDescription": "",
                    "visualDescription": "",
                    "structuredDescription": {},
                    "assetImagePrompt": "",
                    "childId": str(source.get("childId") or ""),
                }
                asset["payload"] = payload
        return candidate_errors

    errors = validate_candidate(localized_assets, from_cache=bool(cached))
    if errors:
        correction_input = {
            **local_input,
            "correction": {
                "validationErrors": errors,
                "previousLocalizedAssets": localized_assets,
                "instruction": "只根据同一sourceSnapshot修正目标本地化patch；不要重跑或补造原片事实，不得用源脸回填；只返回精确schema字段。",
            },
        }
        correction_raw = call_compact_video_json(agent, correction_input, ASSET_LOCALIZATION_COMPACT_PROMPT)
        correction_audit_path = persist_asset_localization_raw("target-localization-correction", correction_raw, correction_input, cache_key)
        correction_parsed = parse_json_object(correction_raw, {})
        corrected_assets = correction_parsed.get("localizedAssets") if isinstance(correction_parsed.get("localizedAssets"), list) else []
        attempts.append({"source": "targeted_correction", "raw": correction_raw, "auditPath": correction_audit_path, "localizedAssets": corrected_assets, "validationErrors": errors})
        localized_assets = corrected_assets
        errors = validate_candidate(localized_assets)
        cache_repaired = bool(cached) and not errors

    profiles = [
        {"assetVariable": asset.get("variable"), "localizedProfile": asset.get("payload", {}).get("localizedProfile")}
        for asset in assets
        if isinstance(asset.get("payload", {}).get("localizedProfile"), dict)
    ]
    return {
        "stage": "localized", "cache_hit": bool(cached), "cache_repaired": cache_repaired, "cache_key": cache_key,
        "source_task_id": cached["task_id"] if cached else "", "errors": errors,
        "profiles": profiles, "raw": attempts[0].get("raw", ""), "rawAttempts": attempts,
    }


def merge_asset_detail_completion(
    assets: list[dict[str, Any]],
    completion: dict[str, Any],
    target_country: str = "目标国家",
    target_language: str = "目标语言",
) -> int:
    _, by_variable = variable_maps(assets)
    items = completion.get("资产补全") if isinstance(completion.get("资产补全"), list) else []
    merged = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        variable = str(item.get("资产变量") or item.get("展示资产变量") or "")
        asset = by_variable.get(variable)
        if not asset:
            continue
        payload = asset.get("payload") if isinstance(asset.get("payload"), dict) else {}
        child = payload.get("当前子资产") if isinstance(payload.get("当前子资产"), dict) else {}
        payload_patch = item.get("payload_patch") if isinstance(item.get("payload_patch"), dict) else {}
        child_patch = item.get("current_child_patch") if isinstance(item.get("current_child_patch"), dict) else {}
        review_notes = item.get("needsReview") if isinstance(item.get("needsReview"), list) else []
        missing_fields = set(asset_detail_missing_fields(asset))
        payload_patch = {
            key: value for key, value in payload_patch.items()
            if key in missing_fields and key in {"本地化场景描述", "场景资产图生成提示词", "本地化道具描述", "道具资产图生成提示词"}
        }
        child_allowed = {
            "角色": {
                "本地化视觉形象描述": "本地化视觉形象描述",
                "角色资产图结构化描述": "角色资产图结构化描述",
                "角色资产图生成提示词": "角色资产图生成提示词",
                "视觉事实来源": "视觉事实来源",
                "固定穿戴配饰": "固定穿戴配饰",
                "手持或悬挂物档案": "手持或悬挂物档案",
            },
        }.get(asset.get("asset_type"), {})
        child_patch = {key: value for key, value in child_patch.items() if child_allowed.get(key) in missing_fields}
        for protected_key in [
            "资产变量",
            "展示资产变量",
            "角色资产变量",
            "场景资产变量",
            "道具资产变量",
            "主资产编号",
            "子资产编号",
            "道具编号",
            "子资产",
            "当前子资产",
        ]:
            payload_patch.pop(protected_key, None)
            child_patch.pop(protected_key, None)
        if not payload_patch and not child_patch and not review_notes:
            continue
        if payload_patch:
            deep_merge_dict(payload, payload_patch)
        if review_notes:
            existing_review = payload.get("needsReview") if isinstance(payload.get("needsReview"), list) else []
            payload["needsReview"] = list(dict.fromkeys([*existing_review, *[str(note) for note in review_notes if str(note).strip()]]))
        if child_patch and isinstance(child, dict):
            for key, value in child_patch.items():
                if key == "角色资产图结构化描述":
                    child[key] = merge_only_missing_visual(child.get(key), value)
                else:
                    child[key] = merge_only_missing_visual(child.get(key), value)
            for detail_key in [
                "角色资产图结构化描述",
                "角色资产图生成提示词",
                "视觉事实来源",
                "固定穿戴配饰",
                "手持或悬挂物档案",
                "场景资产图结构化描述",
                "场景资产图生成提示词",
                "道具资产图结构化描述",
                "道具资产图生成提示词",
            ]:
                if detail_key in child:
                    payload[detail_key] = child[detail_key]
            child_id = child.get("子资产编号")
            children = payload.get("子资产") if isinstance(payload.get("子资产"), list) else []
            for candidate in children:
                if isinstance(candidate, dict) and candidate.get("子资产编号") == child_id:
                    deep_merge_dict(candidate, child_patch)
        if asset.get("asset_type") == "角色":
            prompt = pick_payload_text(child, ["角色资产图生成提示词"]) or pick_payload_text(payload, ["角色资产图生成提示词"])
            if prompt:
                payload["角色资产图生成提示词"] = prompt
        elif asset.get("asset_type") == "场景":
            payload["场景资产图生成提示词"] = build_non_role_asset_image_prompt("场景", payload, target_country, target_language)
        elif asset.get("asset_type") == "道具":
            prompt = pick_payload_text(payload, ["道具资产图生成提示词"])
            if prompt:
                payload["道具资产图生成提示词"] = prompt
        asset["payload"] = payload
        attach_display_variable(asset)
        merged += 1
    return merged


def complete_asset_details(
    agent: dict[str, Any],
    base_input_payload: dict[str, Any],
    assets: list[dict[str, Any]],
    batch_size: int = 3,
) -> dict[str, Any]:
    eligible = [asset for asset in assets if asset_detail_missing_fields(asset)]
    if agent.get("mode") != "volcengine" or not eligible:
        return {"补全批次": 0, "补全资产数": 0, "补全跳过数": len(assets) - len(eligible), "补全调用数": 0, "补全错误": [], "补全原始输出": []}
    outputs: list[dict[str, Any]] = []
    errors: list[str] = []
    merged_total = 0
    for start in range(0, len(eligible), batch_size):
        batch = eligible[start : start + batch_size]
        detail_payload = {
            "目标国家": base_input_payload.get("目标国家", ""),
            "目标语言": base_input_payload.get("目标语言", ""),
            "待补全资产": [{**asset_detail_completion_input(asset), "缺失字段": asset_detail_missing_fields(asset)} for asset in batch],
            "不可修改字段": ["资产变量", "展示资产变量", "主资产编号", "子资产编号", "道具编号"],
            "约束": "只回填每项缺失字段清单中的目标字段；原剧事实未知时仅在原剧事实字段保留 unknown/needsReview；本地化目标设计字段须依据目标国家、目标时代、角色身份和用户明确目标外貌补成具体一致的设计。原片观察到的族裔是源事实而非用户目标族裔锁定；目标族裔缺省时按目标场景作合理具体选择，不机械把国籍等同族裔，不套用统一欧美脸型/眼色/鼻型。不得覆盖已有源事实或用户已确认的目标字段。",
        }
        try:
            raw = call_compact_video_json(agent, detail_payload, ASSET_DETAIL_COMPLETION_PROMPT)
            parsed = parse_json_object(raw, {})
            merged = merge_asset_detail_completion(
                batch,
                parsed,
                str(base_input_payload.get("目标国家") or "目标国家"),
                str(base_input_payload.get("目标语言") or "目标语言"),
            )
            merged_total += merged
            outputs.append({"批次": start // batch_size + 1, "字符数": len(raw), "合并资产数": merged})
        except ProviderError as exc:
            errors.append(str(exc))
    return {"补全批次": len(outputs) + len(errors), "补全资产数": merged_total, "补全跳过数": len(assets) - len(eligible), "补全调用数": len(outputs) + len(errors), "补全错误": errors, "补全原始输出": outputs}


def extract_display_variables(text: str) -> list[str]:
    if not isinstance(text, str):
        return []
    variables = re.findall(r"@[A-Za-z0-9\u4e00-\u9fff_-]+", text)
    deduped: list[str] = []
    for variable in variables:
        if variable not in deduped:
            deduped.append(variable)
    return deduped


def join_asset_refs(refs: list[str]) -> str:
    return "、".join(refs) if refs else "无"


def build_fallback_storyboard_prompt(
    time_range: str = "00:00:00-00:00:12",
    references: list[str] | None = None,
    summary: str = "",
) -> str:
    reference_text = join_asset_refs(references or [])
    return f"""分镜时间范围：{time_range}
镜头时长：{time_range}
出镜角色：无
场景：无
画面描述：
片段说明：回退占位（非视频分析结果）；当前只有资产片段区间信息，未得到逐镜头事实。剧情简述：{summary or '待确认'}；分镜数量：待确认；目标风格：待确认；参考素材分工：当前视频待分析，资产变量只标识资产；成片语言与字幕：按项目模式待确认；共用要求：禁止新增未经输入证实的镜头或内容。
分镜01｜{time_range}｜待确认
[机位与构图] 待分析（mock回退占位，非视频事实）
[背景] 待分析（mock回退占位，非视频事实）
[人物站位] 待分析（mock回退占位，非视频事实）
[动作过程] 待分析（mock回退占位，非视频事实）
[运镜] 待分析（mock回退占位，非视频事实）
[台词与声音] 待分析（mock回退占位，非视频事实）
视频生成约束：此回退占位不构成可用镜头事实；需先完成视频逐镜分析。无后期对白字幕、平台水印、上传者水印、播放器界面或平台角标；不得删除真实场景内文字。
负面提示词：把占位文字当成视频事实、调用时间范围外资产、新增未证实的动作或台词、添加对白字幕或水印
引用资产变量：{reference_text}"""


def build_fallback_storyboards_from_index(asset_occurrence_index: list[dict[str, Any]]) -> list[dict[str, Any]]:
    source_segments = asset_occurrence_index or [
        {
            "片段编号": "片段_001",
            "开始时间": "00:00:00",
            "结束时间": "00:00:12",
            "片段剧情简述": "",
            "出现资产变量": [],
        }
    ]
    storyboards: list[dict[str, Any]] = []
    for index, segment in enumerate(source_segments, start=1):
        start = segment.get("开始时间") or "00:00:00"
        end = segment.get("结束时间") or start or "00:00:12"
        time_range = f"{start}-{end}"
        references = [str(value) for value in segment.get("出现资产变量", []) if str(value).strip()]
        prompt = build_fallback_storyboard_prompt(
            time_range=time_range,
            references=references,
            summary=segment.get("片段剧情简述") or "",
        )
        storyboards.append(
            {
                "title": f"待分析片段 {index:03d}",
                "time_range": time_range,
                "prompt": prompt,
                "payload": {
                    "分镜编号": f"分镜_{index:03d}",
                    "来源片段编号": segment.get("片段编号") or f"片段_{index:03d}",
                    "分镜时间范围": {"开始时间": start, "结束时间": end},
                    "引用资产变量": references,
                    "片段说明": {
                        "场景": "待确认",
                        "分镜数量": 1,
                        "目标风格": "待确认",
                        "参考素材分工": "未执行视频分析；资产变量仅标识资产",
                        "成片语言与字幕": "按项目模式待确认",
                        "共用要求": "回退占位（非视频分析结果），不得作为实际镜头事实使用",
                    },
                    "镜头列表": [{
                        "镜头编号": "分镜01",
                        "时间范围": {"开始时间": start, "结束时间": end},
                        "机位与构图": "待分析（mock回退占位，非视频事实）",
                        "背景": "待分析（mock回退占位，非视频事实）",
                        "人物站位": "待分析（mock回退占位，非视频事实）",
                        "动作过程": "待分析（mock回退占位，非视频事实）",
                        "运镜": "待分析（mock回退占位，非视频事实）",
                        "台词与声音": "待分析（mock回退占位，非视频事实）",
                    }],
                    "视频生成约束": "该回退占位不构成可用镜头事实；需先完成视频逐镜分析。无后期对白字幕、平台水印、上传者水印、播放器界面或平台角标；不得删除真实场景内文字。",
                    "负面提示词": ["把占位文字当成视频事实", "新增未证实的动作或台词", "添加对白字幕或水印"],
                    "使用回退占位": True,
                    "最终视频生成提示词": prompt,
                },
            }
        )
    return storyboards


STORYBOARD_FACT_FIELDS = ("机位与构图", "背景", "人物站位", "动作过程", "运镜", "台词与声音")


def episode_video_signature(episode: dict[str, Any]) -> str:
    """Stable-enough cache key binding a storyboard analysis to one episode/video."""
    file_path = str(episode.get("file_path") or "")
    stat = None
    if file_path:
        try:
            stat = Path(file_path).stat()
        except OSError:
            stat = None
    ark_id = str(episode.get("ark_file_id") or "")
    if stat is None and not ark_id:
        return ""
    identity = {
        "episode_id": str(episode.get("id") or ""),
        "ark_file_id": ark_id,
        "file_size": stat.st_size if stat else int(episode.get("size_bytes") or 0),
        "file_mtime_ns": stat.st_mtime_ns if stat else None,
    }
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode("utf-8")).hexdigest()


def _storyboard_fact_value_is_real(value: Any) -> bool:
    placeholders = ("", "待分析", "待确认", "回退占位", "mock回退占位", "未执行视频分析")
    if isinstance(value, dict):
        return any(_storyboard_fact_value_is_real(item) for item in value.values())
    if isinstance(value, list):
        return any(_storyboard_fact_value_is_real(item) for item in value)
    text = str(value or "").strip()
    return text not in placeholders and not text.startswith("待分析（") and not text.startswith("待确认（")


def storyboard_has_real_shot_facts(payload: dict[str, Any]) -> bool:
    if payload.get("使用回退占位") is True:
        return False
    shots = payload.get("镜头列表")
    if not isinstance(shots, list) or not shots:
        return False
    for shot in shots:
        if not isinstance(shot, dict) or any(field not in shot or shot.get(field) in (None, "") for field in STORYBOARD_FACT_FIELDS):
            return False
        # A dimension may legitimately be uncertain (for example, inaudible
        # dialogue). The shot is real when at least one dimension contains
        # observed facts; unknown dimensions stay explicitly marked.
        if not any(_storyboard_fact_value_is_real(shot.get(field)) for field in STORYBOARD_FACT_FIELDS):
            return False
    return True


def load_reusable_storyboard_cache(
    conn: sqlite3.Connection,
    project_id: str,
    episode_id: str,
    video_signature: str,
    context_signature: str,
) -> list[dict[str, Any]]:
    if not video_signature:
        return []
    rows = conn.execute(
        "SELECT * FROM storyboards WHERE project_id=? AND episode_id=? ORDER BY created_at",
        (project_id, episode_id),
    ).fetchall()
    cached: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        payload = json.loads(item.pop("payload_json"))
        source = payload.get("分镜分析来源") if isinstance(payload.get("分镜分析来源"), dict) else {}
        if (
            source.get("来源") == "storyboardPromptGeneration"
            and source.get("episodeId") == episode_id
            and source.get("videoSignature") == video_signature
            and source.get("analysisContextSignature") == context_signature
            and storyboard_has_real_shot_facts(payload)
        ):
            item["payload"] = payload
            cached.append(item)
    return cached


def storyboard_analysis_context_signature(project: dict[str, Any], assets: list[dict[str, Any]]) -> str:
    config_prompt = str(load_agent_config("storyboardPromptGeneration").get("prompt") or "")
    routed_agent = {"prompt": config_prompt}
    route_project_agent_prompt(routed_agent, project, storyboard=True)
    identity = {
        "schemaVersion": "storyboard-analysis-context-v2",
        "mode": project.get("remake_mode", "overseas_localization"),
        "targetCountry": project.get("target_country", ""),
        "targetLanguage": project.get("target_language", ""),
        "sourceLanguage": project.get("source_language", ""),
        "visualStyle": project.get("storyboard_visual_style", "live_action"),
        "routedPrompt": routed_agent["prompt"],
        "assets": [
            {
                "type": asset.get("asset_type", ""),
                "variable": asset.get("variable", ""),
                "name": asset.get("name", ""),
                "localizedName": asset.get("localized_name", ""),
                "profileSourceHash": localized_profile_source_hash(asset.get("payload", {}) if isinstance(asset.get("payload"), dict) else {}),
            }
            for asset in assets
        ],
    }
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def storyboard_time_fit_errors(storyboards: list[dict[str, Any]], episode: dict[str, Any]) -> list[str]:
    duration = next((float(episode[key]) for key in ("duration_seconds", "durationSeconds", "video_duration") if episode.get(key) not in (None, "")), None)
    errors: list[str] = []
    for storyboard in storyboards:
        title = str(storyboard.get("title") or "未命名分镜")
        board_start, board_end = parse_storyboard_range(str(storyboard.get("time_range") or ""))
        if board_end <= board_start or board_start < 0:
            errors.append(f"{title} 分镜区间非法（{storyboard.get('time_range') or '空'}）")
            continue
        if duration is not None and board_end > duration + 0.1:
            errors.append(f"{title} 结束于 {format_timecode(board_end)}，超出视频时长 {format_timecode(duration)}")
        for shot in (storyboard.get("payload") or {}).get("镜头列表", []):
            shot_range = shot.get("时间范围") if isinstance(shot, dict) else None
            if isinstance(shot_range, dict):
                shot_text = f"{shot_range.get('开始时间', '')}-{shot_range.get('结束时间', '')}"
            else:
                shot_text = str(shot_range or "")
            shot_start, shot_end = parse_storyboard_range(shot_text)
            shot_label = str(shot.get("镜头编号") or "未编号镜头") if isinstance(shot, dict) else "未编号镜头"
            if shot_end <= shot_start or shot_start < board_start - 0.05 or shot_end > board_end + 0.05:
                errors.append(
                    f"{title} 的{shot_label}（{shot_text}）超出本分镜区间 {format_timecode(board_start)}-{format_timecode(board_end)}"
                )
    return errors


def storyboard_times_fit_source(storyboards: list[dict[str, Any]], episode: dict[str, Any]) -> bool:
    return not storyboard_time_fit_errors(storyboards, episode)


def parse_duration_seconds(value: Any) -> float | None:
    """解析镜头声明时长（如 "1秒" / "2.5s" / 3）；无有效正时长时返回 None。"""
    if isinstance(value, (int, float)):
        seconds = float(value)
        return seconds if seconds > 0 else None
    if not isinstance(value, str):
        return None
    text = value.strip().lower().replace(" ", "")
    if not text:
        return None
    match = re.match(r"^(\d+(?:\.\d+)?)(?:秒|s|sec|secs|seconds)?$", text)
    if not match:
        return None
    seconds = float(match.group(1))
    return seconds if seconds > 0 else None


def set_storyboard_shot_time_range(shot: dict[str, Any], start_text: str, end_text: str) -> None:
    value = shot.get("时间范围") if isinstance(shot.get("时间范围"), (dict, str)) else shot.get("镜头时间范围")
    if isinstance(value, dict):
        value["开始时间"] = start_text
        value["结束时间"] = end_text
        shot["时间范围"] = value
    else:
        shot["时间范围"] = f"{start_text}-{end_text}"


def storyboard_shot_range_text(shot: dict[str, Any]) -> str:
    value = shot.get("时间范围") if isinstance(shot.get("时间范围"), (dict, str)) else shot.get("镜头时间范围")
    if isinstance(value, dict):
        start = str(value.get("开始时间") or "").strip()
        end = str(value.get("结束时间") or "").strip()
        return f"{start}-{end}" if start or end else ""
    return str(value or "").strip()


def repair_storyboard_time_bounds(storyboards: list[dict[str, Any]], episode: dict[str, Any]) -> list[str]:
    """修复模型偶发的分镜边界笔误：
    1. 零长度区间（结束时间误写成与开始时间相同）——仅当镜头/分镜声明了明确时长、
       或内部镜头给出正长度区间时，才按该证据补足结束时间；证据不足或补足后与
       下一分镜/视频时长碰撞时不动，交由校验拒绝并提示重试。
    2. 分镜结束时间早于其内部镜头的结束时间（如分镜标注 24-29 秒，但其最后一个
       镜头为 29-30 秒）。
    仅在“把分镜结束延长到最后一个镜头结束、且不越过下一段分镜的开始时间与
    视频实际时长”时执行；不改动任何镜头内容与事实。返回修复说明列表。"""
    notes: list[str] = []
    duration = next((float(episode[key]) for key in ("duration_seconds", "durationSeconds", "video_duration") if episode.get(key) not in (None, "")), None)
    for index, storyboard in enumerate(storyboards):
        title = str(storyboard.get("title") or f"分镜 {index + 1}")
        board_start, board_end = parse_storyboard_range(str(storyboard.get("time_range") or ""))
        next_start = parse_storyboard_range(str(storyboards[index + 1].get("time_range") or ""))[0] if index + 1 < len(storyboards) else None
        upper_bound = next_start if next_start is not None else duration
        shots = [shot for shot in (storyboard.get("payload") or {}).get("镜头列表", []) if isinstance(shot, dict)]
        # 1a. 零长度镜头：按镜头自身声明时长补足结束时间（不越过本分镜可用上界）。
        for shot in shots:
            shot_text = storyboard_shot_range_text(shot)
            if not re.search(r"\d", shot_text):
                continue
            shot_start, shot_end = parse_storyboard_range(shot_text)
            if shot_end > shot_start:
                continue
            declared = parse_duration_seconds(shot.get("时长") or shot.get("镜头时长"))
            if declared is None:
                continue
            candidate_end = shot_start + declared
            shot_ceiling = board_end if board_end > board_start else upper_bound
            if shot_ceiling is not None and candidate_end > shot_ceiling + 0.05:
                continue
            set_storyboard_shot_time_range(shot, format_timecode(shot_start), format_timecode(candidate_end))
            shot_label = str(shot.get("镜头编号") or "未编号镜头")
            notes.append(f"{title} 的{shot_label} 零长度区间 {shot_text} → {format_timecode(shot_start)}-{format_timecode(candidate_end)}（按声明时长补足，未改镜头内容）")
        # 1b. 零长度分镜：按内部镜头的正长度区间（含 1a 修复后）补足结束时间。
        if board_end <= board_start:
            positive_ends = [
                end
                for end in (parse_storyboard_range(storyboard_shot_range_text(shot))[1] for shot in shots)
                if end > board_start
            ]
            if positive_ends:
                candidate_end = max(positive_ends)
                if upper_bound is None or candidate_end <= upper_bound + 0.05:
                    old_range_text = str(storyboard.get("time_range") or "空")
                    new_end_text = format_timecode(candidate_end)
                    storyboard["time_range"] = f"{format_timecode(board_start)}-{new_end_text}"
                    board_range = (storyboard.get("payload") or {}).get("分镜时间范围")
                    if isinstance(board_range, dict):
                        board_range["结束时间"] = new_end_text
                    notes.append(f"{title} 零长度区间 {old_range_text} → {format_timecode(board_start)}-{new_end_text}（按内部镜头结束补足，未改镜头内容）")
                    board_end = candidate_end
        if board_end <= board_start:
            continue
        shot_ends: list[float] = []
        for shot in shots:
            _, shot_end = parse_storyboard_range(storyboard_shot_range_text(shot))
            if shot_end > 0:
                shot_ends.append(shot_end)
        if not shot_ends:
            continue
        max_shot_end = max(shot_ends)
        if max_shot_end <= board_end + 0.05:
            continue
        if upper_bound is not None and max_shot_end > upper_bound + 0.05:
            continue
        old_end_text = format_timecode(board_end)
        new_end_text = format_timecode(max_shot_end)
        storyboard["time_range"] = f"{format_timecode(board_start)}-{new_end_text}"
        board_range = (storyboard.get("payload") or {}).get("分镜时间范围")
        if isinstance(board_range, dict):
            board_range["结束时间"] = new_end_text
        notes.append(f"{title} 结束时间 {old_end_text} → {new_end_text}（含内部镜头结束，未改镜头内容）")
    return notes


def parse_storyboard_range(range_text: str) -> tuple[float, float]:
    if not isinstance(range_text, str) or not range_text.strip():
        return 0.0, 0.0
    start_text, _, end_text = range_text.partition("-")
    start = parse_asset_timecode(start_text)
    end = parse_asset_timecode(end_text or start_text)
    if end < start:
        end = start
    return start, end


def ranges_overlap_seconds(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return max(a[0], b[0]) <= min(a[1] or a[0], b[1] or b[0])


def allowed_asset_variables_for_time_range(time_range: str, asset_occurrence_index: list[dict[str, Any]]) -> set[str]:
    storyboard_range = parse_storyboard_range(time_range)
    allowed: set[str] = set()
    for segment in asset_occurrence_index:
        if not isinstance(segment, dict):
            continue
        segment_range = parse_time_range_seconds(segment.get("开始时间"), segment.get("结束时间"))
        if not ranges_overlap_seconds(storyboard_range, segment_range):
            continue
        for value in segment.get("出现资产变量") or []:
            if isinstance(value, str) and value.strip():
                allowed.add(value.strip())
        for item in segment.get("出现资产") or []:
            if not isinstance(item, dict):
                continue
            for key in ["展示资产变量", "资产变量"]:
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    allowed.add(value.strip())
    return allowed


def allowed_asset_items_for_time_range(time_range: str, asset_occurrence_index: list[dict[str, Any]]) -> list[dict[str, str]]:
    storyboard_range = parse_storyboard_range(time_range)
    allowed: list[dict[str, str]] = []
    seen: set[str] = set()
    for segment in asset_occurrence_index:
        if not isinstance(segment, dict):
            continue
        segment_range = parse_time_range_seconds(segment.get("开始时间"), segment.get("结束时间"))
        if not ranges_overlap_seconds(storyboard_range, segment_range):
            continue
        for item in segment.get("出现资产") or []:
            if not isinstance(item, dict):
                continue
            display_variable = str(item.get("展示资产变量") or item.get("资产变量") or "").strip()
            if not display_variable or display_variable in seen:
                continue
            seen.add(display_variable)
            allowed.append(
                {
                    "资产变量": display_variable,
                    "资产类型": str(item.get("资产类型") or ""),
                    "本地化资产名称": str(item.get("本地化资产名称") or ""),
                }
            )
    return allowed


def storyboard_refs_by_type(refs: list[str], allowed_items: list[dict[str, str]]) -> tuple[list[str], list[str], list[str]]:
    type_by_variable = {item["资产变量"]: item.get("资产类型", "") for item in allowed_items}
    roles: list[str] = []
    scenes: list[str] = []
    props: list[str] = []
    for ref in refs:
        asset_type = type_by_variable.get(ref, "")
        if asset_type == "角色":
            roles.append(ref)
        elif asset_type == "场景":
            scenes.append(ref)
        elif asset_type == "道具":
            props.append(ref)
    return roles, scenes, props


def storyboard_text_value(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "；".join(text for item in value if (text := storyboard_text_value(item)))
    if isinstance(value, dict):
        parts = []
        for key, item in value.items():
            text = storyboard_text_value(item)
            if text:
                parts.append(f"{key}：{text}")
        return "；".join(parts)
    if value is None:
        return ""
    return str(value).strip()


def storyboard_shot_time_range(shot: dict[str, Any]) -> str:
    value = shot.get("时间范围") or shot.get("镜头时间范围")
    if isinstance(value, dict):
        start = str(value.get("开始时间") or "").strip()
        end = str(value.get("结束时间") or "").strip()
        return f"{start}-{end}" if start and end else "待确认"
    if isinstance(value, str) and value.strip():
        return value.strip()
    start = str(shot.get("开始时间") or "").strip()
    end = str(shot.get("结束时间") or "").strip()
    return f"{start}-{end}" if start and end else "待确认"


def storyboard_shot_duration(shot: dict[str, Any], time_range: str) -> str:
    explicit = shot.get("时长") or shot.get("镜头时长")
    if explicit:
        return str(explicit).strip()
    if time_range != "待确认" and "-" in time_range:
        start, end = parse_storyboard_range(time_range)
        if end > start:
            seconds = end - start
            rendered = f"{seconds:.3f}".rstrip("0").rstrip(".")
            return f"{rendered}秒"
    return "待确认"


def format_structured_storyboard_shot(shot: dict[str, Any], index: int) -> str:
    time_range = storyboard_shot_time_range(shot)
    duration = storyboard_shot_duration(shot, time_range)
    lines = [f"分镜{index:02d}｜{time_range}｜{duration}"]
    for field in ("机位与构图", "背景", "人物站位", "动作过程", "运镜", "台词与声音"):
        value = storyboard_text_value(shot.get(field)) or "待确认"
        lines.append(f"[{field}] {value}")
    transition = storyboard_text_value(shot.get("必要衔接") or shot.get("衔接"))
    if transition:
        lines.append(f"[衔接] {transition}")
    return "\n".join(lines)


def format_storyboard_segment_summary(value: Any, shot_count: int) -> str:
    if isinstance(value, str):
        summary = value.strip()
    elif isinstance(value, dict):
        labels = ("场景", "分镜数量", "目标风格", "参考素材分工", "成片语言与字幕", "共用要求")
        parts = []
        for label in labels:
            text = storyboard_text_value(value.get(label))
            if label == "分镜数量" and not text and shot_count:
                text = str(shot_count)
            if text:
                parts.append(f"{label}：{text}")
        summary = "；".join(parts)
    else:
        summary = ""
    return f"片段说明：{summary}" if summary else ""


def prompt_segments_from_payload(payload: dict[str, Any], prompt: str, refs: list[str]) -> list[str]:
    segments: list[str] = []
    shots = payload.get("镜头列表")
    shot_items = [shot for shot in shots if isinstance(shot, dict)] if isinstance(shots, list) else []
    structured_fields = ("机位与构图", "背景", "人物站位", "动作过程", "运镜", "台词与声音")
    summary = format_storyboard_segment_summary(payload.get("片段说明"), len(shot_items))
    if summary:
        segments.append(summary)
    if shot_items:
        for index, shot in enumerate(shot_items, start=1):
            has_structured_facts = any(storyboard_text_value(shot.get(field)) for field in structured_fields)
            legacy_description = str(shot.get("镜头描述") or "").strip()
            if has_structured_facts:
                compiled = format_structured_storyboard_shot(shot, index)
                if legacy_description and legacy_description not in compiled:
                    compiled = f"{compiled}\n[兼容镜头描述] {legacy_description}"
                segments.append(compiled)
            elif legacy_description:
                time_range = storyboard_shot_time_range(shot)
                duration = storyboard_shot_duration(shot, time_range)
                segments.append(f"分镜{index:02d}｜{time_range}｜{duration}\n[镜头描述] {legacy_description}")
        if any(segment.startswith("分镜") for segment in segments):
            return segments
    action = payload.get("分镜具体动作描述")
    if isinstance(action, str) and action.strip():
        segments.append(action.strip())
    for shot in shot_items:
        text = str(shot.get("镜头描述") or "").strip()
        if text:
            segments.append(text)
    if not segments:
        body = re.sub(r"^(分镜时间范围|镜头时长|出镜角色|场景|画面描述|引用资产变量|角色音色参考|视频生成约束|负面提示词)：.*$", "", prompt, flags=re.MULTILINE)
        body = body.replace("画面描述：", "").strip()
        for part in re.split(r"\n+", body):
            clean = part.strip()
            if clean:
                segments.append(clean)
    if not segments:
        segments.append("待确认：当前记录未包含可编译的具体镜头事实。")
    if refs and not any(ref in " ".join(segments) for ref in refs):
        segments[0] = f"{join_asset_refs(refs)} 参与当前片段；{segments[0]}"
    return segments


def format_storyboard_prompt(
    time_range: str,
    payload: dict[str, Any],
    prompt: str,
    refs: list[str],
    allowed_items: list[dict[str, str]],
) -> str:
    roles, scenes, other_assets = storyboard_refs_by_type(refs, allowed_items)
    observation_fields = ("人物站位", "出镜角色", "背景", "场景", "出镜场景")
    shots = payload.get("镜头列表") if isinstance(payload.get("镜头列表"), list) else []
    observation_text = " ".join(
        storyboard_text_value(shot.get(field))
        for shot in shots if isinstance(shot, dict)
        for field in observation_fields
        if storyboard_text_value(shot.get(field))
    )
    if not observation_text:
        observation_text = " ".join(
            storyboard_text_value(payload.get(field))
            for field in observation_fields
            if storyboard_text_value(payload.get(field))
        )
    if observation_text:
        observed_roles: list[str] = []
        observed_scenes: list[str] = []
        for item in allowed_items:
            asset_type = str(item.get("资产类型") or "")
            token_values = [item.get("资产变量"), item.get("本地化资产名称"), item.get("源资产名称"), item.get("剧情身份")]
            if not any(str(token or "").strip() and str(token).strip() in observation_text for token in token_values):
                continue
            if asset_type == "角色" and item.get("资产变量") not in observed_roles:
                observed_roles.append(str(item["资产变量"]))
            elif asset_type == "场景" and item.get("资产变量") not in observed_scenes:
                observed_scenes.append(str(item["资产变量"]))
        roles = observed_roles or ["待核对：人物站位未能唯一关联角色"]
        scenes = observed_scenes or ["待核对：背景未能唯一关联场景"]
    elif roles or scenes:
        roles = [f"{value}（引用待核对，不表示全部出镜）" for value in roles] or ["待核对"]
        scenes = [f"{value}（引用待核对，不表示当前背景）" for value in scenes] or ["待核对"]
    segments = prompt_segments_from_payload(payload, prompt, refs)
    lines = [
        f"镜头时长：{time_range}",
        f"出镜角色：{join_asset_refs(roles)}",
        f"场景：{join_asset_refs(scenes)}",
        "画面描述：",
    ]
    if other_assets:
        lines.append(f"参考资产（被引用不表示全部出现在画面中）：{join_asset_refs(other_assets)}")
    for index, segment in enumerate(segments, start=1):
        clean = re.sub(r"^片段\d+[：:]\s*", "", segment).strip()
        if segment.startswith("片段说明：") or segment.startswith("分镜"):
            lines.append(segment.strip())
        else:
            lines.append(f"片段{index}：{clean}")
    constraints = payload.get("视频生成约束")
    if not isinstance(constraints, str) or not constraints.strip():
        constraints = "资产引用与当前时间段的资产出现索引一致；镜头时长、机位、构图、站位、动作、运镜和声音以各镜头具体描述为准；无后期对白字幕、平台水印、上传者水印、播放器界面或平台角标；场景内可见文字属于场景内容。"
    lines.append(f"视频生成约束：{constraints.strip()}")
    negative = payload.get("负面提示词")
    if isinstance(negative, list):
        negative_text = "、".join(str(value) for value in negative if str(value).strip())
    else:
        negative_text = str(negative or "").strip()
    if not negative_text:
        negative_text = "调用时间范围外资产、新增未出现人物或道具、改变具体动作顺序、把场景内文字当作后期字幕、添加对白字幕或水印"
    lines.append(f"负面提示词：{negative_text}")
    lines.append(f"引用资产变量：{join_asset_refs(refs)}")
    return "\n".join(lines)


def enforce_no_subtitle_generation(text: str) -> str:
    if not text:
        return text
    cleaned = text
    cleaned = re.sub(r"底部[^。；;]*字幕[^。；;]*[。；;]?", "", cleaned)
    cleaned = re.sub(r"[^。；;，,]*字幕同步呈现[^。；;]*[。；;]?", "旁白以画外音呈现。", cleaned)
    cleaned = cleaned.replace("旁白字幕", "旁白画外音")
    cleaned = cleaned.replace("对白字幕同步", "对白以画外音同步")
    cleaned = cleaned.replace("底部字幕", "画外音")
    return cleaned


def sanitize_storyboard_asset_references(
    storyboards: list[dict[str, Any]],
    asset_occurrence_index: list[dict[str, Any]],
    old_to_display: dict[str, str],
    assets: list[dict[str, Any]] | None = None,
) -> None:
    current_assets = assets or []
    token_candidates: dict[str, list[dict[str, Any]]] = {}
    for asset in current_assets:
        payload = asset.get("payload") if isinstance(asset.get("payload"), dict) else {}
        for token in (
            asset.get("variable"), asset.get("display_variable"), payload.get("旧展示资产变量"),
            asset.get("source_variable_alias"), payload.get("源变量别名"),
        ):
            text = str(token or "").strip()
            if text:
                token_candidates.setdefault(text, []).append(asset)
    for storyboard in storyboards:
        payload = storyboard.get("payload") if isinstance(storyboard.get("payload"), dict) else {}
        prompt = str(storyboard.get("prompt") or "")
        refs = payload.get("引用资产变量") if isinstance(payload.get("引用资产变量"), list) else []
        manual_override = payload.get("引用资产变量手动覆盖") is True
        raw_tokens = [str(value).strip() for value in refs if str(value).strip()]
        # Legacy boards keep their observed action in free text rather than
        # structured shots. Resolve known tokens there too, but never scan the
        # global segment summary or the coarse occurrence index.
        raw_tokens.extend(token for token in token_candidates if token and token in prompt)
        shots = payload.get("镜头列表") if isinstance(payload.get("镜头列表"), list) else []
        shot_fields = ("机位与构图", "背景", "人物站位", "动作过程", "运镜", "台词与声音", "必要衔接", "衔接", "镜头描述")
        for shot in shots:
            if not isinstance(shot, dict):
                continue
            shot_text = json.dumps({field: shot.get(field) for field in shot_fields}, ensure_ascii=False)
            # Match known tokens verbatim. A generic @word regex consumes
            # adjacent Chinese prose (for example @Stage前) as part of a name.
            raw_tokens.extend(token for token in token_candidates if token and token in shot_text)

        normalized_refs: list[str] = []
        review_refs: list[dict[str, Any]] = []
        resolved_items_by_variable: dict[str, dict[str, str]] = {}
        for raw_token in dict.fromkeys(raw_tokens):
            candidates = token_candidates.get(raw_token, [])
            unique_candidates = {str(item.get("id") or item.get("variable") or id(item)): item for item in candidates}
            if len(unique_candidates) != 1:
                if len(unique_candidates) > 1:
                    review_refs.append({
                        "token": raw_token,
                        "candidateVariables": sorted(str(item.get("variable") or "") for item in unique_candidates.values()),
                        "reason": "展示变量对应多个稳定资产，未猜测匹配对象",
                    })
                continue
            asset = next(iter(unique_candidates.values()))
            stable_variable = str(asset.get("variable") or "").strip()
            display_variable = str(asset.get("display_variable") or "")
            if not stable_variable or not display_variable:
                continue
            if stable_variable not in normalized_refs:
                normalized_refs.append(stable_variable)
            resolved_items_by_variable[stable_variable] = {
                "资产变量": display_variable,
                "资产类型": str(asset.get("asset_type") or ""),
                "本地化资产名称": str(asset.get("localized_name") or asset.get("name") or ""),
                "源资产名称": str(asset.get("name") or ""),
                "剧情身份": str(payload.get("剧情身份") or payload.get("身份定位") or ""),
            }

        # An appearance index is coarse coverage, not proof that an asset was
        # absent from a particular shot. Keep it as audit context only.
        display_refs = [resolved_items_by_variable[value]["资产变量"] for value in normalized_refs if value in resolved_items_by_variable]
        allowed_items = list(resolved_items_by_variable.values())
        if display_refs and not manual_override:
            for value in display_refs:
                if value not in prompt:
                    prompt = f"{value} {prompt}"
        for review in review_refs:
            prompt = prompt.replace(review["token"], "")
        payload["引用资产变量"] = normalized_refs
        payload["引用资产显示变量"] = display_refs
        payload["资产引用待核对"] = review_refs
        payload["资产映射校验"] = {
            "允许资产变量": [],
            "采用资产变量": normalized_refs,
            "索引可用于过滤": False,
            "索引用途": "粗粒度出现覆盖，仅供核对，不据此删除逐镜明确引用",
            "待核对引用": review_refs,
        }
        storyboard["payload"] = payload
        formatted_prompt = format_storyboard_prompt(storyboard.get("time_range", ""), payload, prompt, display_refs, allowed_items)
        storyboard["prompt"] = enforce_no_subtitle_generation(formatted_prompt)
        payload["最终视频生成提示词"] = storyboard["prompt"]
        for text_key in ["分镜具体动作描述", "视频生成约束"]:
            if isinstance(payload.get(text_key), str):
                payload[text_key] = enforce_no_subtitle_generation(payload[text_key])


def normalize_storyboards_from_agent(parsed: dict[str, Any], old_to_display: dict[str, str] | None = None) -> list[dict[str, Any]]:
    old_to_display = old_to_display or {}
    items = parsed.get("分镜列表") or parsed.get("镜头列表") or parsed.get("storyboards") or parsed.get("shots")
    if not isinstance(items, list):
        return []
    root_segment_summary = parsed.get("片段说明")
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        stable_references = item.get("引用资产变量") or item.get("引用资产") or item.get("资产变量")
        item = convert_storyboard_asset_variables(item, old_to_display)
        if isinstance(root_segment_summary, (dict, str)) and not item.get("片段说明"):
            item["全局片段说明"] = convert_storyboard_asset_variables(root_segment_summary, old_to_display)
        prompt = item.get("最终视频生成提示词") or item.get("视频生成提示词") or item.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            prompt_parts = [
                item.get("角色音色参考", ""),
                item.get("分镜场景设定", ""),
                item.get("分镜具体动作描述", ""),
            ]
            shots = item.get("镜头列表")
            if isinstance(shots, list):
                for shot in shots:
                    if isinstance(shot, dict):
                        shot_label = str(shot.get("镜头编号") or "").strip()
                        shot_description = str(shot.get("镜头描述") or "").strip()
                        if shot_label or shot_description:
                            prompt_parts.append(f"{shot_label}：{shot_description}".strip("："))
            prompt_parts.append(item.get("视频生成约束", ""))
            negative = item.get("负面提示词")
            if isinstance(negative, list):
                prompt_parts.append("负面提示词：" + "、".join(str(value) for value in negative))
            prompt = "\n".join(part for part in prompt_parts if isinstance(part, str) and part.strip())
        prompt = convert_asset_variables(prompt, old_to_display)
        if not prompt.strip():
            has_shot_records = isinstance(item.get("镜头列表"), list) and any(isinstance(shot, dict) for shot in item["镜头列表"])
            has_legacy_body = bool(str(item.get("分镜具体动作描述") or item.get("分镜场景设定") or "").strip())
            if not has_shot_records and not has_legacy_body:
                continue
        references = stable_references
        if isinstance(references, list):
            item["引用资产变量"] = [str(value).strip() for value in references if str(value).strip()]
        item["最终视频生成提示词"] = prompt
        time_range = item.get("分镜时间范围") or item.get("时间范围") or item.get("time_range")
        if isinstance(time_range, dict):
            start = time_range.get("开始时间", "00:00:00")
            end = time_range.get("结束时间", "00:00:12")
            time_range_text = f"{start}-{end}"
        else:
            start = item.get("开始时间")
            end = item.get("结束时间")
            time_range_text = f"{start}-{end}" if start and end else str(time_range or "00:00:00-00:00:12")
        normalized.append(
            {
                "title": item.get("分镜编号") or item.get("镜头编号") or f"分镜 {index:03d}",
                "time_range": time_range_text,
                "prompt": prompt,
                "payload": item,
            }
        )
    return normalized


def insert_storyboards(
    conn: sqlite3.Connection,
    project_id: str,
    episode_id: str,
    storyboards: list[dict[str, Any]],
) -> None:
    timestamp = now_iso()
    for item in storyboards:
        conn.execute(
            """
            INSERT INTO storyboards
            (id, project_id, episode_id, title, time_range, prompt, payload_json, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("storyboard"),
                project_id,
                episode_id,
                item["title"],
                item["time_range"],
                item["prompt"],
                json.dumps(item["payload"], ensure_ascii=False),
                "待分析" if (item.get("payload") or {}).get("使用回退占位") else "可生成",
                timestamp,
            ),
        )


def run_series_analysis(conn: sqlite3.Connection, project: dict[str, Any], parent_task_id: str) -> dict[str, Any]:
    episodes = rows_to_dicts(
        conn.execute(
            "SELECT * FROM episodes WHERE project_id = ? ORDER BY episode_index",
            (project["id"],),
        ).fetchall()
    )
    if not episodes:
        raise HTTPException(status_code=400, detail="No episodes uploaded")
    total = len(episodes)
    successes: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for index, episode in enumerate(episodes, start=1):
        def update_series_progress(stage: str, extra: dict[str, Any] | None = None) -> None:
            progress_result = {
                "总集数": total,
                "当前序号": index,
                "当前集ID": episode["id"],
                "当前集编号": episode["episode_index"],
                "当前阶段": stage,
                "成功集数": len(successes),
                "失败集数": len(failures),
                "成功明细": successes,
                "失败明细": failures,
            }
            if extra:
                progress_result.update(extra)
            update_task_status(
                conn,
                parent_task_id,
                "运行中",
                f"{stage}第 {episode['episode_index']} 集（{index}/{total}）",
                progress_result,
            )
            conn.commit()

        update_series_progress("正在提取资产")
        try:
            asset_task = run_asset_extraction(conn, project, episode["id"])
            if asset_task.get("status") != "成功":
                raise RuntimeError(f"资产提取失败：{asset_task.get('message') or '未知错误'}")
            update_series_progress(
                "正在生成分镜",
                {"当前资产任务ID": asset_task["id"], "当前资产数量": asset_task.get("result", {}).get("资产数量", 0)},
            )
            storyboard_task = run_storyboard_generation(conn, project, episode["id"])
            storyboard_result = storyboard_task.get("result") or {}
            storyboard_count = int(storyboard_result.get("分镜数量") or 0)
            storyboard_segment_count = int(storyboard_result.get("分镜片段数量") or 0)
            if storyboard_task.get("status") != "成功":
                raise RuntimeError(f"分镜生成失败：{storyboard_task.get('message') or '未知错误'}")
            if storyboard_count == 0 or storyboard_segment_count == 0:
                raise RuntimeError(
                    f"分镜生成结果为空（分镜片段 {storyboard_segment_count}，镜头 {storyboard_count}），本集不计成功"
                )
            successes.append(
                {
                    "episodeId": episode["id"],
                    "episodeIndex": episode["episode_index"],
                    "assetTaskId": asset_task["id"],
                    "storyboardTaskId": storyboard_task["id"],
                    "assetCount": asset_task.get("result", {}).get("资产数量", 0),
                    "storyboardCount": storyboard_count,
                }
            )
            update_series_progress("已完成本集", {"当前分镜任务ID": storyboard_task["id"]})
        except Exception as exc:
            failures.append(
                {
                    "episodeId": episode["id"],
                    "episodeIndex": episode["episode_index"],
                    "error": str(exc),
                }
            )
            update_series_progress("本集分析失败", {"当前错误": str(exc)})
        conn.commit()
    result = {
        "总集数": total,
        "成功集数": len(successes),
        "失败集数": len(failures),
        "成功明细": successes,
        "失败明细": failures,
        "项目级资产数": len(project_asset_history_summary(conn, project["id"])),
    }
    conn.execute("UPDATE projects SET status = ?, updated_at = ? WHERE id = ?", ("整剧分析完成", now_iso(), project["id"]))
    message = f"整剧分析完成：成功 {len(successes)} 集，失败 {len(failures)} 集"
    return {"message": message, "result": result, "status": "成功" if not failures else "失败"}


def run_asset_extraction(
    conn: sqlite3.Connection,
    project: dict[str, Any],
    episode_id: str | None,
    task_id: str | None = None,
) -> dict[str, Any]:
    episode = get_episode_or_first(conn, project["id"], episode_id)
    episode, ark_upload_error = ensure_episode_ark_file(conn, episode)
    try:
        agent = prepare_agent("assetExtractionLocalization")
    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    history_assets = project_asset_history_summary(conn, project["id"], episode["id"])
    source_history = [
        {key: item.get(key) for key in ("资产类型", "原剧资产名称", "项目主资产Key", "项目子资产Key", "剧情身份或功能") if item.get(key)}
        for item in history_assets
    ]
    target_country, target_language, _ = asset_prompt_context(project)
    extraction_prompt = ASSET_EXTRACTION_COMPACT_PROMPT
    input_payload = {
        "当前集视频": episode["file_path"],
        "当前集视频URL": episode.get("source_url", ""),
        "当前集Ark文件ID": episode.get("ark_file_id", ""),
        "__video_url": episode.get("source_url", ""),
        "__video_file_id": episode.get("ark_file_id", ""),
        "当前集编号": f"第 {episode['episode_index']} 集",
        "历史资产库": source_history,
        "历史资产归并要求": "仅用原剧名称、剧情身份和源事实匹配历史身份；不得将历史目标族裔/目标外观当作源事实，也不得按拼音姓名锁定目标族裔。",
        "资产提取范围要求": "必须提取当前集视频中影响复刻的全部有效资产：主角、配角、群像/队友/对手/裁判/教练等可见人物，关键场景，关键道具、配件、服装号码、球鞋、篮球、哨子、记分牌等。不得用核心资产数量上限截断输出。必须返回完整合法 JSON，不能输出半截 JSON。",
        "systemPromptChars": len(ASSET_SOURCE_EXTRACTION_COMPACT_PROMPT),
    }
    source_sha256 = source_file_sha256(str(episode.get("file_path") or ""))
    source_cache_key = asset_source_cache_key(source_sha256, agent, project, history_assets) if source_sha256 else ""
    # Reuse a matching pure source stage even when prior assets exist. The hash
    # binds the source prompt/model/history; existing target assets are untouched
    # unless the full target pass succeeds and the publish transaction begins.
    cached_source = find_cached_asset_source(conn, source_cache_key) if source_cache_key else None
    legacy_source_cache_key = ""
    legacy_source_cache_hit = False
    legacy_source_cache_version = ""
    if source_sha256 and not cached_source:
        legacy_cache_candidates = (
            (ASSET_SOURCE_LEGACY_V2_PROMPT, ASSET_SOURCE_SCHEMA_VERSION, ASSET_SOURCE_SCHEMA_VERSION),
            (ASSET_SOURCE_LEGACY_V1_PROMPT, "asset-source-snapshot-v1", "asset-source-snapshot-v1"),
        )
        for legacy_prompt, legacy_pipeline_version, legacy_schema_version in legacy_cache_candidates:
            candidate_key = asset_source_cache_key(
                source_sha256, agent, project, history_assets,
                prompt=legacy_prompt, pipeline_version=legacy_pipeline_version,
            )
            candidate_source = find_cached_asset_source(conn, candidate_key)
            if candidate_source:
                cached_source = candidate_source
                legacy_source_cache_key = candidate_key
                legacy_source_cache_version = legacy_schema_version
                legacy_source_cache_hit = True
                break
        if legacy_source_cache_hit:
            # Re-normalize the exact archived model payload under the current
            # contract. The consumed cache identity remains explicit provenance.
            pass
    raw_model_output = ""
    source_raw_audit_path = ""
    parsed_model_output: dict[str, Any] = {}
    model_error = ""
    model_json_repaired = False
    stream_chars = 0
    stream_events: list[tuple[str, int]] = []
    if agent["mode"] == "volcengine" and not cached_source:
        if not str(episode.get("ark_file_id") or "").strip() and not str(episode.get("source_url") or "").strip():
            model_error = "Ark 视频文件未就绪，未调用模型；请先重试同步 Ark 后再提取资产。"
            if ark_upload_error:
                model_error = f"{model_error} 上传错误：{ark_upload_error}"
        else:
            try:
                progress_saved_at = 0.0
                progress_saved_chars = -1
                progress_saved_stage = ""

                def save_stream_progress(stage: str, chars: int) -> None:
                    nonlocal progress_saved_at, progress_saved_chars, progress_saved_stage
                    now = time.monotonic()
                    if stage == progress_saved_stage and chars - progress_saved_chars < 4096 and now - progress_saved_at < 3:
                        return
                    progress_saved_at = now
                    progress_saved_chars = chars
                    progress_saved_stage = stage
                    if task_id:
                        update_task_stream_progress(conn, task_id, stage, chars)

                if task_id:
                    save_stream_progress("processing", 0)
                raw_model_output, stream_chars, stream_events, stream_error = call_compact_video_json_stream(
                    agent,
                    input_payload,
                    extraction_prompt,
                    on_progress=save_stream_progress if task_id else None,
                )
                source_raw_audit_path = persist_asset_localization_raw(
                    "source-extraction", raw_model_output, input_payload, source_cache_key,
                )
                if stream_error:
                    model_error = stream_error
                if task_id and not stream_error:
                    save_stream_progress("postprocessing", stream_chars)
            except ProviderError as exc:
                model_error = str(exc)
                raw_model_output = f"模型调用失败：{model_error}"
                stream_chars = 0
                stream_events = []
            if not model_error:
                parsed_model_output, model_json_repaired = parse_json_object_with_repair(raw_model_output)
    if cached_source:
        parsed_model_output = cached_source.get("模型解析输出") if isinstance(cached_source.get("模型解析输出"), dict) else {}
        raw_model_output = str(cached_source.get("模型原始输出") or "")
        if legacy_source_cache_hit:
            extracted_assets = normalize_assets_from_agent(parsed_model_output)
        else:
            extracted_assets = normalize_reused_source_assets(cached_source["资产源事实快照"])
    else:
        source_analysis = json.loads(json.dumps(parsed_model_output, ensure_ascii=False))
        extracted_assets = normalize_assets_from_agent(source_analysis)
    used_fallback_assets = agent["mode"] != "volcengine" and not extracted_assets
    if agent["mode"] == "volcengine" and not cached_source and (model_error or not extracted_assets):
        if not model_error:
            model_error = "模型已返回内容，但 JSON 修复后仍没有可用资产结构。"
        result = {
            "当前集编号": f"第 {episode['episode_index']} 集",
            "模型模式": agent["mode"],
            "提示词文件": agent["prompt_file"],
            "资产数量": 0,
            "使用回退资产": False,
            "模型错误": model_error,
            "模型JSON已修复": model_json_repaired,
            "模型原始输出": raw_model_output,
            "模型原始输出审计路径": source_raw_audit_path,
            "模型解析输出": parsed_model_output,
            "流式输出字符数": stream_chars,
            "流式事件": [(stage, chars) for stage, chars in stream_events],
        }
        message = f"资产提取失败：{model_error}"
        task = finish_or_create_task(conn, project["id"], episode["id"], "asset_extraction", "失败", message, result, task_id)
        record_agent_run(
            conn,
            task["id"],
            project["id"],
            episode["id"],
            "assetExtractionLocalization",
            agent,
            input_payload,
            result,
        )
        return task
    assets_to_insert = extracted_assets or build_mock_assets()
    for asset in assets_to_insert:
        if not isinstance(asset.get("payload", {}).get("sourceSnapshot"), dict):
            initialize_asset_profiles(asset)
    assets_to_insert = [attach_display_variable(asset) for asset in assets_to_insert]
    assets_to_insert = [annotate_project_asset_keys(asset, int(episode["episode_index"])) for asset in assets_to_insert]
    asset_occurrence_index = normalize_asset_occurrence_index(parsed_model_output.get("资产出现片段索引"), assets_to_insert)
    apply_capture_times_from_index(assets_to_insert, asset_occurrence_index)
    localization_result: dict[str, Any] = {"stage": "not_run", "cache_hit": False, "cache_key": "", "errors": [], "profiles": []}
    if extracted_assets and agent["mode"] == "volcengine":
        localization_result = run_target_localization(conn, assets_to_insert, project, agent)
        if localization_result.get("errors"):
            source_fact_snapshot = source_only_assets_snapshot(assets_to_insert) if extracted_assets and not used_fallback_assets else []
            failure_result = {
                "当前集编号": f"第 {episode['episode_index']} 集",
                "模型模式": agent["mode"],
                "提示词文件": agent["prompt_file"],
                "资产数量": 0,
                "模型原始输出": raw_model_output,
                "模型原始输出审计路径": source_raw_audit_path,
                "模型解析输出": parsed_model_output,
                "sourceStageStatus": "complete",
                "资产源缓存键": source_cache_key,
                "sourceCacheProvenance": (
                    {
                        "consumedCacheKey": legacy_source_cache_key,
                        "cacheTaskId": cached_source.get("_source_task_id", ""),
                        "schemaVersion": legacy_source_cache_version,
                        "renormalizedAs": ASSET_SOURCE_SCHEMA_VERSION,
                    }
                    if cached_source and legacy_source_cache_hit else {}
                ),
                "资产源事实快照": source_fact_snapshot,
                "资产本地化缓存键": localization_result.get("cache_key", ""),
                "资产本地化原始尝试": localization_result.get("rawAttempts", []),
                "目标本地化错误": localization_result["errors"],
                "未发布资产": True,
                "cache_hit": bool(cached_source),
            }
            message = "目标本地化校验失败，未发布目标资产；已保留可复用的纯源阶段结果。"
            failed_task = finish_or_create_task(conn, project["id"], episode["id"], "asset_extraction", "失败", message, failure_result, task_id)
            record_agent_run(conn, failed_task["id"], project["id"], episode["id"], "assetSourceExtraction", agent, input_payload, {"sourceStageStatus": "complete", "资产源缓存键": source_cache_key, "资产源事实快照": source_fact_snapshot, "模型原始输出": raw_model_output})
            record_agent_run(conn, failed_task["id"], project["id"], episode["id"], "assetTargetLocalization", agent, {"targetCountry": target_country, "targetLanguage": target_language, "sourceSnapshotCount": len(source_fact_snapshot)}, {"cacheKey": localization_result.get("cache_key"), "rawAttempts": localization_result.get("rawAttempts", []), "errors": localization_result["errors"]})
            return failed_task
    ensure_unique_asset_display_variables(assets_to_insert)
    reconciliation = reconcile_asset_extraction_rows(conn, project["id"], episode["id"], assets_to_insert)
    publishable_assets = [asset for asset in assets_to_insert if not asset.get("_withheld")]
    if reconciliation["identityConflicts"] and not publishable_assets:
        result = {
            "当前集编号": f"第 {episode['episode_index']} 集",
            "模型模式": agent["mode"],
            "资产数量": 0,
            "模型原始输出": raw_model_output,
            "模型原始输出审计路径": source_raw_audit_path or str(cached_source.get("模型原始输出审计路径") or "") if cached_source else source_raw_audit_path,
            "模型解析输出": parsed_model_output,
            "sourceStageStatus": "complete",
            "资产源缓存键": source_cache_key,
            "sourceCacheProvenance": ({
                "consumedCacheKey": legacy_source_cache_key,
                "cacheTaskId": cached_source.get("_source_task_id", ""),
                "schemaVersion": legacy_source_cache_version,
                "renormalizedAs": ASSET_SOURCE_SCHEMA_VERSION,
            } if cached_source and legacy_source_cache_hit else {}),
            "资产身份匹配冲突": reconciliation["identityConflicts"],
            "资产身份匹配统计": reconciliation,
            "未发布资产": True,
        }
        message = "资产源事实与现有资产无法按稳定身份唯一匹配；旧资产、图像历史和分镜引用均已保留，未发布新结果。"
        task = finish_or_create_task(conn, project["id"], episode["id"], "asset_extraction", "失败", message, result, task_id)
        record_agent_run(conn, task["id"], project["id"], episode["id"], "assetSourceExtraction", agent, input_payload, result)
        return task
    source_cache_ready = bool(cached_source) or (bool(extracted_assets) and not used_fallback_assets)
    source_fact_snapshot = source_only_assets_snapshot(publishable_assets) if source_cache_ready else []
    new_assets = [asset for asset in publishable_assets if not asset.get("_existingAssetId")]
    frame_stats = capture_asset_original_frames(project["id"], episode, new_assets)
    insert_assets(conn, project["id"], episode["id"], publishable_assets)
    withheld_count = int(reconciliation.get("withheldCount") or 0)
    result = {
        "当前集编号": f"第 {episode['episode_index']} 集",
        "模型模式": agent["mode"],
        "提示词文件": agent["prompt_file"],
        "资产数量": len(publishable_assets),
        "身份待核对数量": withheld_count,
        "身份待核对队列": reconciliation["identityConflicts"] if withheld_count else [],
        "使用回退资产": used_fallback_assets,
        "模型错误": model_error,
        "模型JSON已修复": model_json_repaired,
        "模型原始输出": raw_model_output,
        "模型原始输出审计路径": source_raw_audit_path or str(cached_source.get("模型原始输出审计路径") or "") if cached_source else source_raw_audit_path,
        "模型解析输出": parsed_model_output,
        "资产变量映射": {asset["variable"]: asset.get("display_variable", "") for asset in publishable_assets},
        "资产出现片段索引": asset_occurrence_index,
        "资产处理阶段": {
            "sourceExtraction": {"schemaVersion": ASSET_SOURCE_SCHEMA_VERSION, "promptFile": "config/agents/asset_source_extraction.md", "cache_hit": bool(cached_source)},
            "targetLocalization": {"schemaVersion": ASSET_LOCALIZATION_SCHEMA_VERSION, **{key: localization_result.get(key) for key in ("stage", "cache_hit", "cache_key", "source_task_id", "errors")}},
        },
        "资产详情补全": {"补全批次": 0, "补全资产数": 0, "补全调用数": 0, "阶段": localization_result.get("stage", "not_run")},
        "资产身份匹配统计": reconciliation,
        "资产本地化缓存键": localization_result.get("cache_key", ""),
        "localizedProfileSnapshot": localization_result.get("profiles", []),
        "资产本地化原始尝试": localization_result.get("rawAttempts", []),
        "sourceReviewAssetVariables": [
            asset.get("variable") for asset in publishable_assets
            if isinstance(asset.get("payload", {}).get("localizedProfile"), dict)
            and asset["payload"]["localizedProfile"].get("status") == "source_review"
        ],
        "资产源缓存键": source_cache_key if source_cache_ready else "",
        "sourceStageStatus": "complete" if source_cache_ready else "unavailable",
        "sourceTaskId": cached_source.get("_source_task_id", "") if cached_source else "",
        "sourceCacheProvenance": (
            {
                "consumedCacheKey": legacy_source_cache_key,
                "cacheTaskId": cached_source.get("_source_task_id", ""),
                "schemaVersion": legacy_source_cache_version,
                "renormalizedAs": ASSET_SOURCE_SCHEMA_VERSION,
            }
            if cached_source and legacy_source_cache_hit else {}
        ),
        "cache_hit": bool(cached_source),
        "资产源事实快照": source_fact_snapshot if extracted_assets and not used_fallback_assets else [],
        "原片抽帧统计": frame_stats,
        "流式输出字符数": stream_chars,
        "流式事件": [(stage, chars) for stage, chars in stream_events],
    }
    conn.execute("UPDATE projects SET status = ?, updated_at = ? WHERE id = ?", ("资产已提取", now_iso(), project["id"]))
    message_parts: list[str] = []
    if cached_source:
        message_parts.append(f"复用同源同配置资产源结果（来源任务 {cached_source.get('_source_task_id', '')}），当前项目独立建立资产与原片帧记录")
    elif used_fallback_assets:
        message_parts.append("当前为显式 mock 模式，已生成示例资产")
    elif model_json_repaired:
        message_parts.append(f"资产提取完成（已自动修复模型 JSON，共生成 {stream_chars} 字符）")
    else:
        message_parts.append(f"资产提取完成（共生成 {stream_chars} 字符）")
    source_review_count = len(result["sourceReviewAssetVariables"])
    if source_review_count:
        message_parts.append(f"资产与目标脸部本地化已保存；{source_review_count} 个角色仍有源视觉字段待审核，审核前不能生成角色资产图（共生成 {stream_chars} 字符）")
    else:
        message_parts.append(f"资产提取完成（共生成 {stream_chars} 字符）")
    if withheld_count:
        message_parts.append(f"{withheld_count} 项资产身份歧义待人工核对，已保留旧资产未自动合并")
    task = finish_or_create_task(conn, project["id"], episode["id"], "asset_extraction", "成功", "；".join(message_parts), result, task_id)
    record_agent_run(
        conn,
        task["id"],
        project["id"],
        episode["id"],
        "assetSourceExtraction",
        agent,
        input_payload,
        result,
    )
    if localization_result.get("stage") == "localized" and (not localization_result.get("cache_hit") or localization_result.get("cache_repaired")):
        record_agent_run(
            conn,
            task["id"],
            project["id"],
            episode["id"],
            "assetTargetLocalization",
            agent,
            {"targetCountry": target_country, "targetLanguage": target_language, "sourceSnapshotCount": len(source_fact_snapshot)},
            {"schemaVersion": ASSET_LOCALIZATION_SCHEMA_VERSION, "cacheKey": localization_result.get("cache_key"), "localizedProfileSnapshot": localization_result.get("profiles"), "raw": localization_result.get("raw", ""), "rawAttempts": localization_result.get("rawAttempts", [])},
        )
    return task


def run_storyboard_generation(
    conn: sqlite3.Connection,
    project: dict[str, Any],
    episode_id: str | None,
    *,
    force: bool = False,
    task_id: str | None = None,
) -> dict[str, Any]:
    episode = get_episode_or_first(conn, project["id"], episode_id)
    episode, ark_upload_error = ensure_episode_ark_file(conn, episode)
    storyboard_target_language = str(project.get("target_language") or "").strip()
    keep_source_language = storyboard_target_language in {"", "沿用原片语言", "原片语言", "保留原片语言，不翻译"}
    target_country = str(project.get("target_country") or "").strip()
    country_requires_localization = target_country not in {"", "中国", "中国大陆"}
    language_requires_localization = not keep_source_language and storyboard_target_language != "中文"
    # Market/language selection is independent of visual remake mode.
    overseas_mode = country_requires_localization or language_requires_localization
    asset_count = conn.execute(
        "SELECT COUNT(*) FROM assets WHERE project_id = ? AND episode_id = ?",
        (project["id"], episode["id"]),
    ).fetchone()[0]
    if asset_count == 0:
        raise HTTPException(status_code=400, detail="Run asset extraction before storyboard generation")
    assets = rows_to_dicts(
        conn.execute(
            "SELECT asset_type, name, localized_name, variable, payload_json FROM assets WHERE project_id = ? AND episode_id = ? ORDER BY created_at",
            (project["id"], episode["id"]),
        ).fetchall()
    )
    for asset in assets:
        asset["payload"] = json.loads(asset.pop("payload_json"))
        attach_display_variable(asset)
    old_to_display, _ = variable_maps(assets)
    asset_occurrence_index: list[dict[str, Any]] = []
    latest_asset_task = row_to_dict(
        conn.execute(
            """
            SELECT id, result_json FROM tasks
            WHERE project_id = ? AND episode_id = ? AND task_type = ? AND status = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (project["id"], episode["id"], "asset_extraction", "成功"),
        ).fetchone()
    )
    if latest_asset_task:
        try:
            asset_task_result = json.loads(latest_asset_task["result_json"])
            raw_index = asset_task_result.get("资产出现片段索引")
            asset_occurrence_index = normalize_asset_occurrence_index(raw_index, assets)
        except (TypeError, json.JSONDecodeError):
            asset_occurrence_index = []
    if not asset_occurrence_index:
        asset_occurrence_index = normalize_asset_occurrence_index([], assets)
    video_signature = episode_video_signature(episode)
    analysis_context_signature = storyboard_analysis_context_signature(project, assets)
    asset_summary = [
        {
            "资产类型": asset["asset_type"],
            "原剧资产名称": asset["name"],
            "本地化资产名称": asset["localized_name"] if overseas_mode else asset["name"],
            "原变量": asset["variable"],
            "资产变量": asset["display_variable"],
        }
        for asset in assets
    ]
    episode_script_facts = str(
        episode.get("script")
        or episode.get("剧情事实")
        or episode.get("片段剧情简述")
        or ""
    ).strip()
    whitebox_reference = str(
        episode.get("whitebox_reference")
        or episode.get("白模参考素材")
        or ""
    ).strip()
    input_payload = {
        "当前集视频": episode["file_path"],
        "当前集视频URL": episode.get("source_url", ""),
        "当前集Ark文件ID": episode.get("ark_file_id", ""),
        "__video_url": episode.get("source_url", ""),
        "__video_file_id": episode.get("ark_file_id", ""),
        "当前集编号": f"第 {episode['episode_index']} 集",
        "当前集剧本或剧情事实": episode_script_facts or "未提供独立剧本字段；请基于当前集视频及已有分析事实，不假设存在完整剧本。",
        "白模参考素材": whitebox_reference or "未提供独立白模参考素材；当前剧情视频仅用于观察，不推定为白模。",
        "参考素材分工": "当前集视频用于观察真实切镜、画面、动作、站位和可听声音；资产摘要/资产出现片段索引用于资产外观与时间归属；独立白模素材仅在明确提供时约束机位、构图、站位、路径和动作。",
        "资产片段索引用途": "该索引是资产出现时间和剧情功能的粗粒度上下文，不代表真实切镜、镜头数量或镜头边界；必须直接观察当前集视频，按真实连续镜头切点独立确定分镜，不得按索引段数机械生成镜头。",
        "目标国家": target_country if country_requires_localization else "不适用（国内模式）",
        "目标语言": storyboard_target_language if language_requires_localization or country_requires_localization and not keep_source_language else "保留原片语言，不翻译",
        "重制模式": project.get("remake_mode", "overseas_localization"),
        "源语言": project.get("source_language", ""),
        "分镜画面类型": project.get("storyboard_visual_style", "live_action"),
        "模式路由强约束": (
            f"按明确目标国家（{target_country or '未指定'}）和目标语言（{storyboard_target_language if not keep_source_language else '沿用源语言'}）适配分镜；cg_3d/真人/资产替换只决定视觉或资产策略，不改变目标国家/语言路由。保留原始镜头观察作为证据，目标画面文字按选定语言和当前目标角色名替换；不得机械删除汉字或改写未要求翻译的对白含义。"
            if overseas_mode else
            "国内模式保留原片语言与源事实，不猜测语言或目标国家；画面类型只决定视觉呈现。"
        ),
        "资产摘要": asset_summary,
        "资产出现片段索引": asset_occurrence_index,
        "资产变量映射": old_to_display,
        "变量格式要求": "最终视频生成提示词必须使用资产摘要里的 @变量，不得使用 {{角色_001_子资产_001}} 这类旧变量。",
        "systemPromptChars": 0,
    }

    # The asset-occurrence index only supplies timing/context. It is never a
    # substitute for a shot-by-shot video analysis result.
    if not force:
        cached_storyboards = load_reusable_storyboard_cache(
            conn, project["id"], episode["id"], video_signature, analysis_context_signature
        )
        if cached_storyboards:
            result = {
                "模型模式": "cache",
                "分镜数量": sum(len((board.get("payload") or {}).get("镜头列表", [])) for board in cached_storyboards),
                "分镜片段数量": len(cached_storyboards),
                "复用分镜缓存": True,
                "资产片段作为上下文": bool(asset_occurrence_index),
                "使用回退分镜": False,
                "sourceTaskId": "",
            }
            return finish_or_create_task(conn, project["id"], episode["id"], "storyboard_generation", "成功", "复用当前视频对应的完整逐镜分析结果", result, task_id)

    try:
        agent = prepare_agent("storyboardPromptGeneration")
    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    route_project_agent_prompt(agent, project, storyboard=True)
    input_payload["systemPromptChars"] = len(agent.get("prompt", ""))
    raw_model_output = ""
    parsed_model_output: dict[str, Any] = {}
    model_error = ""
    model_json_repaired = False
    stream_chars = 0
    stream_events: list[tuple[str, int]] = []
    transport_audit: dict[str, Any] = {}
    if agent["mode"] == "volcengine":
        if not str(episode.get("ark_file_id") or "").strip() and not str(episode.get("source_url") or "").strip():
            model_error = "Ark 视频文件未就绪，未调用模型；请先重试同步 Ark 后再生成分镜。"
            if ark_upload_error:
                model_error = f"{model_error} 上传错误：{ark_upload_error}"
        else:
            try:
                raw_model_output = call_agent_chat("storyboardPromptGeneration", agent, input_payload)
                stream_chars = len(raw_model_output)
                stream_events = []
            except ProviderIncompleteResponse as exc:
                transport_audit = {
                    "模型响应未完成": True,
                    "模型响应ID": exc.response_id,
                    "模型响应错误": exc.reason,
                    "模型响应状态": exc.response_status,
                    "模型响应incomplete_details": exc.incomplete_details,
                    "模型响应finish_reason": exc.finish_reason,
                    "模型响应可提取文本": exc.extracted_text,
                    "模型响应原文": exc.raw_response,
                }
                model_error = str(exc)
                raw_model_output = "模型调用失败：" + model_error
                stream_chars = 0
                stream_events = []
            except ProviderIncompleteRead as exc:
                transport_audit = {
                    "模型传输异常": str(exc),
                    "模型传输响应ID": exc.response_id,
                    "模型传输部分响应字节数": len(exc.partial_body),
                    "模型传输部分响应原文": exc.partial_text,
                    "模型传输响应状态": exc.response_status,
                    "模型传输incomplete_details": exc.incomplete_details,
                    "模型传输finish_reason": exc.finish_reason,
                    "传输异常响应完整标记": exc.response_complete,
                }
                raw_model_output = exc.extracted_text
                stream_chars = len(raw_model_output)
                stream_events = []
                if not exc.response_complete:
                    model_error = f"{exc}；部分响应未声明完整，未采用输出。"
                else:
                    try:
                        parsed_candidate = json.loads(raw_model_output)
                    except (json.JSONDecodeError, TypeError):
                        model_error = f"{exc}；部分响应不是完整JSON，未采用输出。"
                    else:
                        candidate_storyboards = normalize_storyboards_from_agent(parsed_candidate, old_to_display)
                        if candidate_storyboards:
                            repair_storyboard_time_bounds(candidate_storyboards, episode)
                        if (
                            candidate_storyboards
                            and all(storyboard_has_real_shot_facts(item.get("payload") or {}) for item in candidate_storyboards)
                            and storyboard_times_fit_source(candidate_storyboards, episode)
                        ):
                            parsed_model_output = parsed_candidate
                            transport_audit["传输异常完整有效JSON已恢复"] = True
                        else:
                            model_error = f"{exc}；部分响应未通过完整镜头事实与时间校验，未采用输出。"
            except ProviderError as exc:
                model_error = str(exc)
                raw_model_output = f"模型调用失败：{model_error}"
                stream_chars = 0
                stream_events = []
            if not model_error:
                parsed_model_output, model_json_repaired = parse_json_object_with_repair(raw_model_output)
    generated_storyboards = normalize_storyboards_from_agent(parsed_model_output, old_to_display)
    time_boundary_repairs: list[str] = []
    if agent["mode"] == "volcengine" and not model_error and generated_storyboards:
        time_boundary_repairs = repair_storyboard_time_bounds(generated_storyboards, episode)
    if agent["mode"] == "volcengine" and not model_error and generated_storyboards and not all(
        storyboard_has_real_shot_facts(storyboard.get("payload") or {}) for storyboard in generated_storyboards
    ):
        model_error = "模型返回的分镜缺少完整逐镜事实，未替换现有分镜；请重试分析。"
        generated_storyboards = []
    if agent["mode"] == "volcengine" and not model_error and generated_storyboards and not storyboard_times_fit_source(
        generated_storyboards, episode
    ):
        fit_errors = storyboard_time_fit_errors(generated_storyboards, episode)
        detail = "；".join(fit_errors[:3])
        model_error = f"模型返回的分镜时间超出当前视频或镜头区间（{detail}），未替换现有分镜；请重试分析。"
        generated_storyboards = []
    if agent["mode"] == "volcengine" and (model_error or not generated_storyboards):
        if not model_error:
            model_error = "模型已返回内容，但 JSON 修复后仍没有可用分镜结构。"
        payload = {
            "模型模式": agent["mode"],
            "提示词文件": agent["prompt_file"],
            "模型错误": model_error,
            "模型JSON已修复": model_json_repaired,
            "模型原始输出": raw_model_output,
            "模型解析输出": parsed_model_output,
            **transport_audit,
            "分镜数量": 0,
            "使用回退分镜": False,
            "已有回退占位未替换": bool(conn.execute(
                "SELECT 1 FROM storyboards WHERE project_id=? AND episode_id=? AND payload_json LIKE '%使用回退占位%' LIMIT 1",
                (project["id"], episode["id"]),
            ).fetchone()),
            "流式输出字符数": stream_chars,
            "流式事件": [(stage, chars) for stage, chars in stream_events],
        }
        conn.execute("UPDATE projects SET status = ?, updated_at = ? WHERE id = ?", ("分镜分析失败", now_iso(), project["id"]))
        message = f"分镜分析失败，现有分镜已保留：{model_error}"
        task = finish_or_create_task(conn, project["id"], episode["id"], "storyboard_generation", "失败", message, payload, task_id)
        record_agent_run(
            conn,
            task["id"],
            project["id"],
            episode["id"],
            "storyboardPromptGeneration",
            agent,
            input_payload,
            payload,
        )
        return task
    if not generated_storyboards:
        generated_storyboards = build_fallback_storyboards_from_index(asset_occurrence_index)
    for storyboard in generated_storyboards:
        # apply_storyboard_project_prompt 按 episode_id 做集内资产解析；
        # 生成路径的字典没有该键会让解析退化为项目级全量匹配，产生跨集假歧义。
        storyboard.setdefault("episode_id", episode["id"])
    used_fallback_storyboard = any(
        (storyboard.get("payload") or {}).get("使用回退占位") is True
        for storyboard in generated_storyboards
    )
    if not used_fallback_storyboard:
        for storyboard in generated_storyboards:
            storyboard.setdefault("payload", {})["分镜分析来源"] = {
                "来源": "storyboardPromptGeneration",
                "episodeId": episode["id"],
                "videoSignature": video_signature,
                "analysisContextSignature": analysis_context_signature,
            }
    sanitize_storyboard_asset_references(generated_storyboards, asset_occurrence_index, old_to_display, assets)
    for storyboard in generated_storyboards:
        apply_storyboard_project_prompt(conn, project, storyboard)
    replaced_storyboard_backups: list[dict[str, Any]] = []
    if used_fallback_storyboard:
        existing_rows = conn.execute(
            "SELECT payload_json FROM storyboards WHERE project_id=? AND episode_id=?",
            (project["id"], episode["id"]),
        ).fetchall()
        if not any(json.loads(row["payload_json"]).get("使用回退占位") is True for row in existing_rows):
            for storyboard in generated_storyboards:
                apply_storyboard_project_prompt(conn, project, storyboard)
            insert_storyboards(conn, project["id"], episode["id"], generated_storyboards)
    else:
        existing_rows = conn.execute(
            "SELECT id, payload_json FROM storyboards WHERE project_id=? AND episode_id=?",
            (project["id"], episode["id"]),
        ).fetchall()
        for row in existing_rows:
            prior_payload = json.loads(row["payload_json"])
            generated_body = prior_payload.get("分镜生成正文")
            is_user_authored = (
                isinstance(generated_body, str) and bool(generated_body.strip())
                or prior_payload.get("回退占位已由用户补全") is True
            )
            is_managed_analysis = isinstance(prior_payload.get("分镜分析来源"), dict)
            is_unedited_placeholder = prior_payload.get("使用回退占位") is True and not is_user_authored
            if (is_managed_analysis or is_unedited_placeholder) and not is_user_authored:
                replaced_storyboard_backups.append({
                    "id": row["id"],
                    "title": conn.execute("SELECT title FROM storyboards WHERE id=?", (row["id"],)).fetchone()["title"],
                    "payload": prior_payload,
                })
                conn.execute("DELETE FROM storyboards WHERE id=?", (row["id"],))
        insert_storyboards(conn, project["id"], episode["id"], generated_storyboards)
    payload = {
        "分镜编号": "分镜_001",
        "模型模式": agent["mode"],
        "提示词文件": agent["prompt_file"],
        "模型错误": model_error,
        "模型JSON已修复": model_json_repaired,
        "模型原始输出": raw_model_output,
        "模型解析输出": parsed_model_output,
        **transport_audit,
        "分镜数量": 0 if used_fallback_storyboard else sum(len((board.get("payload") or {}).get("镜头列表", [])) for board in generated_storyboards),
        "分镜片段数量": 0 if used_fallback_storyboard else len(generated_storyboards),
        "占位片段数量": len(generated_storyboards) if used_fallback_storyboard else 0,
        "使用回退分镜": used_fallback_storyboard,
        "复用分镜缓存": False,
        "资产片段作为上下文": bool(asset_occurrence_index),
        "sourceTaskId": latest_asset_task.get("id", "") if latest_asset_task else "",
        "被替换的旧分镜备份": replaced_storyboard_backups,
        "分镜时间边界自动修复": time_boundary_repairs,
        "流式输出字符数": stream_chars,
        "流式事件": [(stage, chars) for stage, chars in stream_events],
    }
    project_status = "分镜待分析" if used_fallback_storyboard else "分镜已生成"
    conn.execute("UPDATE projects SET status = ?, updated_at = ? WHERE id = ?", (project_status, now_iso(), project["id"]))
    message_parts: list[str] = []
    if used_fallback_storyboard:
        message_parts.append("未执行真实逐镜视频分析；保留未分析占位，请切换可用视频分析Agent后重新分析")
    elif model_json_repaired:
        message_parts.append(f"分镜生成完成（已自动修复模型 JSON，共生成 {stream_chars} 字符）")
    else:
        message_parts.append(f"分镜生成完成（共生成 {stream_chars} 字符）")
    if time_boundary_repairs and not used_fallback_storyboard:
        message_parts.append(f"已自动修复 {len(time_boundary_repairs)} 处分镜时间边界")
    if used_fallback_storyboard:
        task = finish_or_create_task(conn, project["id"], episode["id"], "storyboard_generation", "失败", "；".join(message_parts), payload, task_id)
    else:
        task = finish_or_create_task(conn, project["id"], episode["id"], "storyboard_generation", "成功", "；".join(message_parts), payload, task_id)
    record_agent_run(
        conn,
        task["id"],
        project["id"],
        episode["id"],
        "storyboardPromptGeneration",
        agent,
        input_payload,
        payload,
    )
    return task


def run_video_generation(conn: sqlite3.Connection, project: dict[str, Any], storyboard_id: str | None, task_id: str | None = None) -> dict[str, Any]:
    storyboard = get_video_generation_storyboard(conn, project["id"], storyboard_id)
    try:
        agent = prepare_agent("videoGeneration")
    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    apply_storyboard_project_prompt(conn, project, storyboard)
    if agent.get("mode") != "mock":
        message = "真实视频生成尚未接入；本任务没有调用视频模型，也未创建占位视频。"
        result = {
            "available": False,
            "mock": False,
            "模型模式": agent.get("mode", ""),
            "提示词文件": agent.get("prompt_file", ""),
            "error": "video_generation_not_implemented",
            "storyboardId": storyboard["id"],
        }
        task = finish_or_create_task(conn, project["id"], storyboard["episode_id"], "video_generation", "失败", message, result, task_id)
        record_agent_run(
            conn,
            task["id"],
            project["id"],
            storyboard["episode_id"],
            "videoGeneration",
            agent,
            {"storyboardId": storyboard["id"], "prompt": storyboard["prompt"]},
            result,
        )
        return task
    generation_id = new_id("video")
    output_dir = PROJECTS_DIR / project["id"] / "outputs" / "videos"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{generation_id}.mp4"
    # 演示模式：从 data/mock 轮转复制预存模拟视频，让"生成"可重复地产出可播放的多版本。
    mock_variants = sorted((DATA_DIR / "mock").glob("demo-v*.mp4"))
    existing_count = conn.execute(
        "SELECT COUNT(*) AS c FROM video_generations WHERE project_id = ? AND storyboard_id = ?",
        (project["id"], storyboard["id"]),
    ).fetchone()["c"]
    demo_variant = 0
    if mock_variants:
        demo_variant = existing_count % len(mock_variants)
        shutil.copyfile(mock_variants[demo_variant], output_path)
    else:
        output_path.write_text(
            f"Mock video generation placeholder\n\nStoryboard: {storyboard['id']}\n\nPrompt:\n{storyboard['prompt']}\n",
            encoding="utf-8",
        )
    payload = {
        "mock": True,
        "demo": True,
        "demoVariant": demo_variant + 1,
        "note": "演示用预存模拟视频；真实视频生成接入后这里保存真实 mp4 输出路径",
    }
    conn.execute(
        "UPDATE video_generations SET is_selected = 0 WHERE project_id = ? AND storyboard_id = ?",
        (project["id"], storyboard["id"]),
    )
    conn.execute(
        """
        INSERT INTO video_generations
        (id, project_id, episode_id, storyboard_id, status, prompt, output_path, is_selected, payload_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            generation_id,
            project["id"],
            storyboard["episode_id"],
            storyboard["id"],
            "成功",
            storyboard["prompt"],
            str(output_path),
            1,
            json.dumps(payload, ensure_ascii=False),
            now_iso(),
        ),
    )
    conn.execute("UPDATE projects SET status = ?, updated_at = ? WHERE id = ?", ("视频片段已生成", now_iso(), project["id"]))
    result = {
        "generationId": generation_id,
        "outputPath": str(output_path),
        "模型模式": agent["mode"],
        "提示词文件": agent["prompt_file"],
    }
    task = finish_or_create_task(conn, project["id"], storyboard["episode_id"], "video_generation", "成功", "mock 视频生成完成", result, task_id)
    record_agent_run(
        conn,
        task["id"],
        project["id"],
        storyboard["episode_id"],
        "videoGeneration",
        agent,
        {
            "分镜编号": storyboard["id"],
            "最终视频生成提示词": storyboard["prompt"],
            "systemPromptChars": len(agent.get("prompt", "")),
        },
        result,
    )
    return task
