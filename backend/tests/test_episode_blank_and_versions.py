from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import main


@pytest.fixture
def composition_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data = tmp_path / "data"
    monkeypatch.setattr(main, "DATA_DIR", data)
    monkeypatch.setattr(main, "DB_PATH", data / "test.sqlite3")
    monkeypatch.setattr(main, "PROJECTS_DIR", data / "projects")
    monkeypatch.setattr(main, "prepare_agent", lambda _key: {"mode": "mock", "model_name": "test", "prompt": "", "prompt_file": ""})
    main.init_db()
    with TestClient(main.app) as client:
        yield client, tmp_path


def _make_video(path: Path, *, size: str, color: str, audio: bool) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg is not installed")
    args = [ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c={color}:s={size}:r=25:d=2"]
    if audio:
        args += ["-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000:duration=2", "-shortest"]
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if audio:
        args += ["-c:a", "aac"]
    args.append(str(path))
    subprocess.run(args, check=True, capture_output=True)


def test_blank_episode_create_attach_and_delete_guards(composition_client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    client, root = composition_client
    monkeypatch.setattr(main, "upload_episode_to_ark", lambda conn, episode: {"file_id": "", "status": "", "error": ""})
    project = client.post("/api/projects", json={"title": "blank-episodes"}).json()["project"]

    first = client.post(f"/api/projects/{project['id']}/episodes")
    assert first.status_code == 200, first.text
    first_ep = first.json()["episode"]
    assert first_ep["episode_index"] == 1
    assert first_ep["status"] == "待上传原片"
    assert first_ep.get("file_url", "") == ""

    second = client.post(f"/api/projects/{project['id']}/episodes")
    assert second.status_code == 200, second.text
    second_ep = second.json()["episode"]
    assert second_ep["episode_index"] == 2 and second_ep["id"] != first_ep["id"]

    # 空白分集可以直接删除
    deleted = client.delete(f"/api/projects/{project['id']}/episodes/{first_ep['id']}")
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted"] is True

    # 有分镜的空白分集禁止删除
    with main.connect() as conn:
        conn.execute(
            "INSERT INTO storyboards (id,project_id,episode_id,title,time_range,prompt,payload_json,status,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            ("sb_blocker", project["id"], second_ep["id"], "手动分镜", "00:00:00-00:00:08", "p", json.dumps({}), "待更新", "2026-01-01T00:00:00Z"),
        )
    blocked = client.delete(f"/api/projects/{project['id']}/episodes/{second_ep['id']}")
    assert blocked.status_code == 409 and "分镜" in blocked.json()["detail"]
    with main.connect() as conn:
        conn.execute("DELETE FROM storyboards WHERE id = ?", ("sb_blocker",))

    # 挂载原片：仅空白分集可挂，重复挂载 409，非视频后缀 415
    video_path = tmp_path / "episode2.mp4"
    _make_video(video_path, size="160x90", color="green", audio=True)
    bad_type = client.post(
        f"/api/projects/{project['id']}/episodes/{second_ep['id']}/source-video",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert bad_type.status_code == 415
    with video_path.open("rb") as handle:
        attached = client.post(
            f"/api/projects/{project['id']}/episodes/{second_ep['id']}/source-video",
            files={"file": ("episode2.mp4", handle, "video/mp4")},
        )
    assert attached.status_code == 200, attached.text
    attached_ep = attached.json()["episode"]
    assert attached_ep["file_url"].startswith("/api/files/")
    assert "已上传" in attached_ep["status"]
    with video_path.open("rb") as handle:
        duplicated = client.post(
            f"/api/projects/{project['id']}/episodes/{second_ep['id']}/source-video",
            files={"file": ("episode2b.mp4", handle, "video/mp4")},
        )
    assert duplicated.status_code == 409

    # 已有原片的分集不可删除
    not_empty = client.delete(f"/api/projects/{project['id']}/episodes/{second_ep['id']}")
    assert not_empty.status_code == 409


def test_generated_video_assets_expose_storyboard_version_fields(composition_client):
    client, root = composition_client
    project = client.post("/api/projects", json={"title": "versions"}).json()["project"]
    episode_id = "episode_versions"
    source = root / "data" / "projects" / project["id"] / "source" / "reference.mp4"
    source.parent.mkdir(parents=True, exist_ok=True)
    _make_video(source, size="160x90", color="green", audio=True)
    gen_v1 = root / "data" / "projects" / project["id"] / "outputs" / "videos" / "gen_v1.mp4"
    gen_v2 = root / "data" / "projects" / project["id"] / "outputs" / "videos" / "gen_v2.mp4"
    gen_v1.parent.mkdir(parents=True, exist_ok=True)
    _make_video(gen_v1, size="160x90", color="red", audio=False)
    _make_video(gen_v2, size="160x90", color="blue", audio=False)
    with main.connect() as conn:
        conn.execute(
            "INSERT INTO episodes (id,project_id,episode_index,filename,file_path,status,created_at) VALUES (?,?,?,?,?,?,?)",
            (episode_id, project["id"], 1, source.name, str(source), "已上传", "2026-01-01T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO storyboards (id,project_id,episode_id,title,time_range,prompt,payload_json,status,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            ("sb1", project["id"], episode_id, "两人对峙", "00:00:00-00:00:04", "p", json.dumps({}), "待更新", "2026-01-01T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO video_generations VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("gen1", project["id"], episode_id, "sb1", "成功", "p", str(gen_v1), 0, json.dumps({}), "2026-01-01T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO video_generations VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("gen2", project["id"], episode_id, "sb1", "成功", "p", str(gen_v2), 1, json.dumps({}), "2026-01-02T00:00:00Z"),
        )
    assets = client.get(f"/api/projects/{project['id']}/episodes/{episode_id}/video-assets").json()["assets"]
    generated = {item["id"]: item for item in assets if item["kind"] == "generated_video"}
    assert set(generated) == {"gen1", "gen2"}
    for item in generated.values():
        assert item["storyboardId"] == "sb1"
        assert item["storyboardTitle"] == "两人对峙"
        assert item["createdAt"]
    assert generated["gen1"]["isSelected"] is False
    assert generated["gen2"]["isSelected"] is True
