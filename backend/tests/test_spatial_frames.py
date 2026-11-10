from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import main


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_dir = tmp_path / "data"
    monkeypatch.setattr(main, "DATA_DIR", data_dir)
    monkeypatch.setattr(main, "DB_PATH", data_dir / "test.sqlite3")
    monkeypatch.setattr(main, "PROJECTS_DIR", data_dir / "projects")
    # 测试环境不加载真实深度模型；深度线程由专项用例另行覆盖。
    monkeypatch.setattr(main, "start_spatial_depth_conversion", lambda *_args, **_kwargs: None)
    main.init_db()
    with TestClient(main.app) as test_client:
        yield test_client


def make_test_video(path: Path, seconds: float = 3.0) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg not available")
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [ffmpeg, "-y", "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=320x240:rate=10", str(path)],
        check=True, capture_output=True,
    )


def make_storyboard_payload(marks: list[dict] | None) -> dict:
    shot: dict = {
        "镜头编号": "镜头1",
        "时间范围": {"开始时间": "00:00:00", "结束时间": "00:00:03"},
        "机位与构图": {"位置": "场边"},
    }
    if marks is not None:
        shot["空间参考帧"] = marks
    return {"分镜编号": "分镜_001", "镜头列表": [shot]}


def test_collect_marks_filters_and_caps():
    payload = make_storyboard_payload([
        {"时间点": "00:00:01.5", "难点": "纵深站位", "涉及资产": ["@甲"]},
        {"时间点": "00:00:09", "难点": "超出镜头范围"},          # 超出时间范围，丢弃
        {"时间点": "00:00:02", "涉及资产": []},                   # 缺难点，丢弃
        {"时间点": "", "难点": "空时间点"},                        # 空时间点，丢弃
        {"时间点": "00:00:02.5", "难点": "遮挡"},
        {"时间点": "00:00:02.8", "难点": "第四有效"},              # 受每分镜上限 3 截断
        {"时间点": "00:00:02.9", "难点": "第五有效"},
    ])
    boards = [{"id": "sb1", "title": "分镜_001", "payload": payload}]
    marks = main.collect_spatial_frame_marks(boards)
    assert [m["reason"] for m in marks] == ["纵深站位", "遮挡", "第四有效"]
    assert marks[0]["timepoint_seconds"] == pytest.approx(1.5)
    assert marks[0]["ref_assets"] == ["@甲"]
    assert marks[0]["shot_label"] == "镜头1"
    # 无字段 / 空列表 / 非列表均安全返回空
    assert main.collect_spatial_frame_marks([{"id": "x", "title": "t", "payload": {"镜头列表": []}}]) == []
    assert main.collect_spatial_frame_marks([{"id": "x", "title": "t", "payload": {}}]) == []


def test_refresh_capture_and_api(client: TestClient, tmp_path: Path):
    project = client.post("/api/projects", json={"title": "spatial", "remakeMode": "asset_replacement", "targetCountry": "中国"}).json()["project"]
    video = tmp_path / "source.mp4"
    make_test_video(video)
    with main.connect() as conn:
        conn.execute(
            "INSERT INTO episodes (id,project_id,episode_index,filename,file_path,status,created_at) VALUES (?,?,?,?,?,?,?)",
            ("ep1", project["id"], 1, "source.mp4", str(video), "已上传", "now"),
        )
        payload = make_storyboard_payload([
            {"时间点": "00:00:01", "难点": "前后纵深", "涉及资产": ["@甲-常服"]},
        ])
        conn.execute(
            "INSERT INTO storyboards (id,project_id,episode_id,title,time_range,prompt,payload_json,status,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            ("sb1", project["id"], "ep1", "分镜_001", "00:00:00-00:00:03", "prompt", json.dumps(payload, ensure_ascii=False), "可生成", "now"),
        )
        conn.commit()
        episode = conn.execute("SELECT * FROM episodes WHERE id='ep1'").fetchone()
        stats = main.refresh_episode_spatial_frames(conn, project["id"], dict(episode), rebuild=True)
    assert stats == {"标记数": 1, "截帧数": 1}

    listed = client.get(f"/api/projects/{project['id']}/episodes/ep1/spatial-frames").json()["frames"]
    assert len(listed) == 1
    frame = listed[0]
    assert frame["status"] == "已截帧"
    assert frame["timepoint"] == "00:00:01"
    assert frame["reason"] == "前后纵深"
    assert frame["ref_assets"] == ["@甲-常服"]
    assert frame["frame_url"].startswith("/api/files/")
    assert frame["depth_url"] == ""  # 深度线程已被 fixture 禁用
    assert Path(frame["frame_path"]).is_file()

    # rebuild=False 且已有记录：不重建（保留手动删除结果）
    with main.connect() as conn:
        episode = dict(conn.execute("SELECT * FROM episodes WHERE id='ep1'").fetchone())
        stats = main.refresh_episode_spatial_frames(conn, project["id"], episode, rebuild=False)
    assert stats["复用现有帧数"] == 1 and stats["截帧数"] == 0

    # 删除：行与文件同时清除
    deleted = client.delete(f"/api/spatial-frames/{frame['id']}")
    assert deleted.status_code == 200
    assert not Path(frame["frame_path"]).exists()
    assert client.get(f"/api/projects/{project['id']}/episodes/ep1/spatial-frames").json()["frames"] == []

    # 删除后 rebuild=False 视为空集，可重新补建；rebuild=True 总是重建
    with main.connect() as conn:
        episode = dict(conn.execute("SELECT * FROM episodes WHERE id='ep1'").fetchone())
        stats = main.refresh_episode_spatial_frames(conn, project["id"], episode, rebuild=False)
    assert stats["截帧数"] == 1


def test_depth_conversion_worker_updates_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # 独立环境 + 假深度生成器驱动真实 worker 线程：验证状态流转与 depth_path 落库。
    data_dir = tmp_path / "data"
    monkeypatch.setattr(main, "DATA_DIR", data_dir)
    monkeypatch.setattr(main, "DB_PATH", data_dir / "test.sqlite3")
    monkeypatch.setattr(main, "PROJECTS_DIR", data_dir / "projects")
    main.init_db()
    from backend.app import depth as depth_module

    def fake_render(frame_path: str, depth_path: str) -> None:
        Path(depth_path).write_bytes(b"fake-depth")

    monkeypatch.setattr(depth_module, "render_depth_map", fake_render)
    frame_path = tmp_path / "frame.jpg"
    frame_path.write_bytes(b"fake-frame")
    with main.connect() as conn:
        conn.execute(
            "INSERT INTO spatial_frames (id,project_id,episode_id,storyboard_id,storyboard_title,shot_label,timepoint_seconds,reason,ref_assets_json,frame_path,depth_path,status,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("sf1", "p1", "ep1", "sb1", "分镜_001", "镜头1", 1.0, "纵深", "[]", str(frame_path), "", "已截帧", "now"),
        )
        conn.commit()
    main.start_spatial_depth_conversion("p1", "ep1")
    import time as _time
    row = None
    for _ in range(50):
        with main.connect() as conn:
            row = conn.execute("SELECT status,depth_path FROM spatial_frames WHERE id='sf1'").fetchone()
        if row["status"] != "已截帧":
            break
        _time.sleep(0.1)
    assert row is not None and row["status"] == "深度图已生成"
    assert row["depth_path"].endswith(".depth.png")
