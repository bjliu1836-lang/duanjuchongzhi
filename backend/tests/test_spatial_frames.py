from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import main
from backend.app import depth as depth_module


def fake_render(frame_path: str, depth_path: str) -> None:
    from PIL import Image

    Image.new("L", (160, 120), 128).save(depth_path)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_dir = tmp_path / "data"
    monkeypatch.setattr(main, "DATA_DIR", data_dir)
    monkeypatch.setattr(main, "DB_PATH", data_dir / "test.sqlite3")
    monkeypatch.setattr(main, "PROJECTS_DIR", data_dir / "projects")
    # 测试环境不加载真实深度模型；所有深度生成走假实现。
    monkeypatch.setattr(depth_module, "render_depth_map", fake_render)
    main.init_db()
    with TestClient(main.app) as test_client:
        yield test_client


def make_test_video(path: Path, seconds: float = 2.0) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg not available")
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [ffmpeg, "-y", "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=320x240:rate=10", str(path)],
        check=True, capture_output=True,
    )


def make_project_with_episode(client: TestClient, tmp_path: Path) -> tuple[dict, str]:
    project = client.post("/api/projects", json={"title": "spatial", "remakeMode": "asset_replacement", "targetCountry": "中国"}).json()["project"]
    video = tmp_path / "source.mp4"
    make_test_video(video)
    with main.connect() as conn:
        conn.execute(
            "INSERT INTO episodes (id,project_id,episode_index,filename,file_path,status,created_at) VALUES (?,?,?,?,?,?,?)",
            ("ep1", project["id"], 1, "source.mp4", str(video), "已上传", "now"),
        )
        conn.commit()
    return project, "ep1"


def test_manual_capture_list_and_delete(client: TestClient, tmp_path: Path):
    project, ep_id = make_project_with_episode(client, tmp_path)
    created = client.post(
        f"/api/projects/{project['id']}/episodes/{ep_id}/spatial-frames",
        json={"timepointSeconds": 1.0, "note": "三人纵深站位"},
    )
    assert created.status_code == 200, created.text
    frame = created.json()["frame"]
    assert frame["timepoint"] == "00:00:01"
    assert frame["reason"] == "三人纵深站位"
    assert frame["frame_url"].startswith("/api/files/")
    # 假深度生成器：同步转深度成功
    assert frame["status"] == "深度图已生成"
    assert frame["depth_url"].startswith("/api/files/")

    listed = client.get(f"/api/projects/{project['id']}/episodes/{ep_id}/spatial-frames").json()["frames"]
    assert len(listed) == 1 and listed[0]["id"] == frame["id"]

    frame_file = main.PROJECTS_DIR / project["id"] / "spatial_frames" / f"{frame['id']}.jpg"
    depth_file = main.PROJECTS_DIR / project["id"] / "spatial_frames" / f"{frame['id']}.depth.png"
    assert frame_file.is_file() and depth_file.is_file()

    deleted = client.delete(f"/api/spatial-frames/{frame['id']}")
    assert deleted.status_code == 200
    assert not frame_file.exists() and not depth_file.exists()
    assert client.get(f"/api/projects/{project['id']}/episodes/{ep_id}/spatial-frames").json()["frames"] == []


def test_manual_capture_invalid_episode(client: TestClient):
    project = client.post("/api/projects", json={"title": "x", "remakeMode": "asset_replacement", "targetCountry": "中国"}).json()["project"]
    response = client.post(f"/api/projects/{project['id']}/episodes/nope/spatial-frames", json={"timepointSeconds": 1.0})
    assert response.status_code == 404


def test_depth_video_job_lifecycle(client: TestClient, tmp_path: Path):
    project, ep_id = make_project_with_episode(client, tmp_path)
    before = client.get(f"/api/projects/{project['id']}/episodes/{ep_id}/depth-video").json()["job"]
    assert before["status"] == "none"

    started = client.post(f"/api/projects/{project['id']}/episodes/{ep_id}/depth-video")
    assert started.status_code == 200, started.text
    assert started.json()["job"]["status"] == "running"

    deadline = time.time() + 60
    job: dict = {}
    while time.time() < deadline:
        job = client.get(f"/api/projects/{project['id']}/episodes/{ep_id}/depth-video").json()["job"]
        if job["status"] != "running":
            break
        time.sleep(0.3)
    assert job["status"] == "done", job
    assert job["video_url"].startswith("/api/files/")
    out_file = main.PROJECTS_DIR / project["id"] / "depth_video" / f"{ep_id}.mp4"
    assert out_file.is_file() and out_file.stat().st_size > 0

    # 完成后 GET 按文件恢复 done 状态（模拟进程重启后内存丢失）
    main._depth_video_jobs.clear()
    restored = client.get(f"/api/projects/{project['id']}/episodes/{ep_id}/depth-video").json()["job"]
    assert restored["status"] == "done" and restored["video_url"]
