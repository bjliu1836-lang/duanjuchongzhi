import json
import sqlite3

from backend.app import main


def test_strip_unconfirmed_segments_keeps_confirmed_facts():
    text = "[背景] 可见环境：球场边线；装饰：待确认；重要文字：待确认。\n[运镜] 方式：固定机位；方向：无。"
    result = main.strip_unconfirmed_prompt_facts(text)
    assert result.splitlines()[0] == "[背景] 可见环境：球场边线"
    assert "待确认" not in result
    assert "[运镜] 方式：固定机位；方向：无。" in result


def test_strip_unconfirmed_drops_fully_unknown_lines():
    text = "分镜时长：00:00:00-00:00:10\n[背景] 可见环境：待确认\n[衔接] 待确认\n角色音色参考：待确认"
    result = main.strip_unconfirmed_prompt_facts(text)
    assert result == "分镜时长：00:00:00-00:00:10"


def test_strip_unconfirmed_leaves_clean_text_untouched():
    text = "[动作过程] 起始：他停下脚步；变化：看向对方；结束：两人相对。"
    assert main.strip_unconfirmed_prompt_facts(text) == text


def test_insert_storyboards_strips_unconfirmed_from_prompt_and_source_text():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE storyboards (id TEXT, project_id TEXT, episode_id TEXT, title TEXT, time_range TEXT, prompt TEXT, payload_json TEXT, status TEXT, created_at TEXT)"
    )
    body = "[背景] 可见环境：球场；装饰：待确认。"
    boards = [
        {
            "title": "分镜_001",
            "time_range": "00:00:00-00:00:10",
            "prompt": body,
            "payload": {"源分镜文本": body, "镜头列表": [{}]},
        }
    ]
    main.insert_storyboards(conn, "p1", "e1", boards)
    row = conn.execute("SELECT prompt,payload_json,status FROM storyboards").fetchone()
    assert "待确认" not in row[0]
    assert "可见环境：球场" in row[0]
    assert "待确认" not in json.loads(row[1])["源分镜文本"]
    assert row[2] == "可生成"
