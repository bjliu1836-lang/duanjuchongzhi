from __future__ import annotations

import io
import hashlib
import http.client
import json
import shutil
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.app import main
from backend.app.providers.model_provider import ProviderIncompleteRead, ProviderIncompleteResponse, VolcengineModelProvider


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_dir = tmp_path / "data"
    monkeypatch.setattr(main, "DATA_DIR", data_dir)
    monkeypatch.setattr(main, "DB_PATH", data_dir / "test.sqlite3")
    monkeypatch.setattr(main, "PROJECTS_DIR", data_dir / "projects")
    monkeypatch.setattr(main, "prepare_agent", lambda _key: {"mode": "mock", "model_name": "test-mock", "prompt": "", "prompt_file": "test"})
    main.init_db()
    with TestClient(main.app) as test_client:
        yield test_client


def create_project(client: TestClient, **payload):
    response = client.post("/api/projects", json=payload)
    assert response.status_code == 200, response.text
    return response.json()["project"]


def insert_source_records(project: dict, *, selected_image: bool = False) -> tuple[str, str]:
    with main.connect() as conn:
        episode_id = "episode_source_1"
        conn.execute(
            "INSERT INTO episodes (id,project_id,episode_index,filename,file_path,status,created_at) VALUES (?,?,?,?,?,?,?)",
            (episode_id, project["id"], 1, "source.mp4", "C:/test/source.mp4", "已上传", "2026-01-01T00:00:00Z"),
        )
        asset_id = "asset_source_1"
        payload = {
            "原剧核心形象描述": "源片里是一位30岁男性，穿蓝色外套。",
            "本地化核心形象描述": "旧项目海外化女性角色描述，不可继承。",
            "本地化角色名称": "Overseas Alice",
            "剧情身份": "主角",
        }
        conn.execute(
            "INSERT INTO assets (id,project_id,episode_id,asset_type,name,localized_name,variable,status,payload_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (asset_id, project["id"], episode_id, "角色", "原片角色甲", "Overseas Alice", "{{role_1}}", "已确认", json.dumps(payload, ensure_ascii=False), "2026-01-01T00:00:00Z"),
        )
        image_id = "image_source_1"
        if selected_image:
            image_path = main.PROJECTS_DIR / project["id"] / "assets" / "roles" / "old.png"
            image_path.parent.mkdir(parents=True, exist_ok=True)
            image_path.write_bytes(b"parent target image")
            conn.execute(
                "INSERT INTO asset_images (id,project_id,asset_id,status,prompt,file_path,is_selected,payload_json,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (image_id, project["id"], asset_id, "成功", "old prompt", str(image_path), 1, json.dumps({"source": "old"}), "2026-01-01T00:00:00Z"),
            )
        storyboard_payload = {"引用资产变量": ["@OverseasAlice"], "分镜具体动作描述": "角色向前走三步，然后回头。"}
        conn.execute(
            "INSERT INTO storyboards (id,project_id,episode_id,title,time_range,prompt,payload_json,status,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            ("storyboard_source_1", project["id"], episode_id, "分镜1", "00:00:01-00:00:03", "@OverseasAlice 向前走三步，然后回头。", json.dumps(storyboard_payload, ensure_ascii=False), "已生成", "2026-01-01T00:00:00Z"),
        )
    return asset_id, image_id


def valid_role_visual_structure(**overrides) -> dict:
    structure = {
        "基础设定": {"性别": "男", "性别判断依据": "剧情称谓明确为男性", "视觉年龄": "30岁", "国家 / 时代 / 朝代": "美国现代", "族裔外貌": "墨西哥裔美国男性", "身份定位": "主角", "性格关键词": "克制", "气质氛围": "沉稳"},
        "身材体态": {"头身比": "约7.5头身", "身高体感": "中等偏高", "体型": "匀称", "肩颈": "自然", "腰腿": "比例匀称", "肌肉 / 线条": "自然", "整体体态": "站姿平稳"},
        "脸部特征": {"目标族裔背景": "墨西哥裔美国男性", "脸型": "椭圆脸", "骨相": "轮廓自然", "眉形": "自然眉", "眼形": "杏眼", "虹膜颜色": "深棕色", "鼻形": "直鼻", "唇形": "自然唇形", "面部毛发": "无明显胡须", "妆感": "自然", "皮肤肤色": "自然肤色", "皮肤质感": "真实肤理", "气色状态": "健康", "静态微表情": "平静", "眼神气质": "沉着"},
        "发型": {"发型名称": "短发", "发色": "黑色", "长度": "短发", "刘海 / 分线": "自然分线", "发丝质感": "自然", "整体状态": "整洁"},
        "服装": {"服装等级": "日常服", "场合": "日常", "主色": "蓝色", "副色": "深蓝", "点缀色": "无明显点缀", "内衬": "无明显内衬", "中间层": "无中间层", "外层": "蓝色棉质外套", "领型": "翻领", "袖型": "长袖", "腰线": "自然腰线", "衣长": "至胯部", "下装": "深色长裤", "摆型": "自然垂摆", "拖尾程度": "普通垂摆", "摆与地面关系": "下摆离地，不拖地", "鞋子": "深色平底鞋", "鞋面可见性": "可见", "脚踝可见性": "不外露", "面料": "棉布", "纹样": "素色", "纹样位置": "无明显纹样位置", "结构与廓形": "合身直身廓形", "工艺与重工程度": "普通缝线", "穿着状态": "整洁"},
    }
    for section, fields in overrides.items():
        structure.setdefault(section, {}).update(fields)
    return structure


def prepare_valid_role_asset(asset_id: str, *, structure: dict | None = None, fixed_accessories: list[str] | None = None) -> None:
    with main.connect() as conn:
        row = conn.execute("SELECT payload_json FROM assets WHERE id=?", (asset_id,)).fetchone()
        payload = json.loads(row[0])
        child = payload.setdefault("当前子资产", {"子资产编号": "角色_001_子资产_001", "差异类型": "服装"})
        structure = structure or valid_role_visual_structure()
        child["角色资产图结构化描述"] = structure
        child["本地化视觉形象描述"] = "30岁男性，蓝色棉质翻领长袖外套，深色长裤，整洁短发。"
        child["视觉事实来源"] = {"视频可见": ["蓝色外套", "短发"], "剧情已知": ["30岁男性"], "推断补全": [], "needsReview": []}
        child["固定穿戴配饰"] = fixed_accessories or []
        child["手持或悬挂物档案"] = ["宝剑"]
        payload["当前子资产"] = child
        payload["角色资产图结构化描述"] = structure
        payload["sourceSnapshot"] = {"schemaVersion": main.ASSET_SOURCE_SCHEMA_VERSION, "coreDescription": payload.get("原剧核心形象描述", ""), "visualDescription": child.get("原剧视觉形象描述", ""), "sourceStructuredDescription": {}, "childId": child.get("子资产编号", "")}
        profile = {"schemaVersion": main.ASSET_LOCALIZATION_SCHEMA_VERSION, "status": "complete", "localizedName": "Alex", "localizedChildName": "Daily outfit", "coreDescription": "30岁墨西哥裔美国男性，蓝色棉质外套和深色长裤。", "visualDescription": child["本地化视觉形象描述"], "structuredDescription": structure, "assetImagePrompt": ""}
        project = main.get_project_or_404(conn, conn.execute("SELECT project_id FROM assets WHERE id=?", (asset_id,)).fetchone()[0])
        profile["targetContext"] = main.localized_profile_target_context(payload, project)
        payload["localizedProfile"] = profile
        conn.execute("UPDATE assets SET payload_json=? WHERE id=?", (json.dumps(payload, ensure_ascii=False), asset_id))


def test_legacy_default_and_derived_project_separates_target_outputs(client: TestClient):
    legacy = create_project(client, title="legacy")
    assert legacy["remake_mode"] == "overseas_localization"
    assert legacy["storyboard_visual_style"] == "live_action"
    domestic_default = create_project(client, title="domestic-default", remakeMode="asset_replacement", sourceLanguage="中文")
    assert domestic_default["target_country"] == "中国"
    assert domestic_default["target_language"] == "沿用原片语言"
    parent = create_project(client, title="parent", remakeMode="overseas_localization")
    insert_source_records(parent, selected_image=True)

    derived_response = client.post(
        f"/api/projects/{parent['id']}/derive",
        json={"title": "cg child", "remakeMode": "cg_3d", "sourceLanguage": "中文"},
    )
    assert derived_response.status_code == 200, derived_response.text
    derived = derived_response.json()["project"]
    assert derived["parent_project_id"] == parent["id"]
    assert derived["storyboard_visual_style"] == "cg_3d"
    with main.connect() as conn:
        old_asset = conn.execute("SELECT * FROM assets WHERE project_id=?", (parent["id"],)).fetchone()
        new_asset = conn.execute("SELECT * FROM assets WHERE project_id=?", (derived["id"],)).fetchone()
        assert conn.execute("SELECT COUNT(*) FROM asset_images WHERE project_id=?", (derived["id"],)).fetchone()[0] == 0
        assert conn.execute("SELECT is_selected FROM asset_images WHERE project_id=?", (parent["id"],)).fetchone()[0] == 1
        old_payload = json.loads(old_asset["payload_json"])
        new_payload = json.loads(new_asset["payload_json"])
        assert old_payload["本地化核心形象描述"] == "旧项目海外化女性角色描述，不可继承。"
        assert new_payload["原片资产描述"] == "源片里是一位30岁男性，穿蓝色外套。"
        assert "本地化核心形象描述" not in new_payload
        assert new_payload["源AssetKey"] == "asset_source_1"
        assert new_payload["目标生成描述"] == ""
        derived_asset_id = new_asset["id"]
        derived_variable = new_payload["展示资产变量"]
        # A historical overseas style phrase may remain in the source prompt;
        # changing project mode must clean that phrase without losing actions.
        conn.execute("UPDATE projects SET storyboard_visual_style='cg_3d' WHERE id=?", (derived["id"],))
        board_row = conn.execute("SELECT * FROM storyboards WHERE project_id=?", (derived["id"],)).fetchone()
        board_payload = json.loads(board_row["payload_json"])
        board_payload["源分镜文本"] = "真人实拍电影质感，角色向前走三步，然后回头。"
        conn.execute("UPDATE storyboards SET prompt=?, payload_json=? WHERE id=?", (board_payload["源分镜文本"], json.dumps(board_payload, ensure_ascii=False), board_row["id"]))
    storyboard_response = client.get(f"/api/projects/{derived['id']}/storyboards")
    assert storyboard_response.status_code == 200
    storyboard = storyboard_response.json()["storyboards"][0]
    assert storyboard["payload"]["引用资产变量"] == [derived_variable]
    assert storyboard["payload"]["分镜具体动作描述"] == "角色向前走三步，然后回头。"
    assert storyboard["prompt"].count(main.STORYBOARD_STYLE_TEMPLATES["cg_3d"]) == 1
    assert "角色向前走三步，然后回头" in storyboard["prompt"]
    assert "真人实拍电影质感" not in storyboard["prompt"]
    assert "白色背景" not in storyboard["prompt"]
    assert "源AssetKey asset_source_1" not in storyboard["prompt"]
    mapping = storyboard["payload"]["目标资产映射快照"][0]
    assert mapping["sourceAssetKey"] == "asset_source_1"
    assert mapping["projectAssetId"] == derived_asset_id
    assert mapping["imagePath"] == ""

    # Save -> GET preview -> refresh/list -> retry composer stays idempotent.
    saved = client.patch(
        f"/api/projects/{derived['id']}/storyboards/{storyboard['id']}",
        json={"prompt": "角色向前走三步，然后回头。"},
    )
    assert saved.status_code == 200, saved.text
    preview1 = saved.json()["storyboard"]
    preview2 = client.get(f"/api/projects/{derived['id']}/storyboards").json()["storyboards"][0]
    assert preview1["prompt"] == preview2["prompt"]
    assert preview2["payload"]["源分镜文本"] == "角色向前走三步，然后回头。"
    assert preview2["prompt"].count(main.STORYBOARD_STYLE_TEMPLATES["cg_3d"]) == 1
    run = client.post(
        f"/api/projects/{derived['id']}/tasks/run",
        json={"taskType": "video_generation", "storyboardId": storyboard["id"]},
    )
    assert run.status_code == 200, run.text
    task = run.json()["task"]
    task_state = client.get(f"/api/tasks/{task['id']}").json()["task"]
    assert task_state["status"] == "成功"
    generation_path = Path(task_state["result"]["outputPath"])
    final_text = generation_path.read_text(encoding="utf-8")
    assert final_text.count(main.STORYBOARD_STYLE_TEMPLATES["cg_3d"]) == 1
    assert "真人实拍电影质感" not in final_text

    prepare_valid_role_asset(derived_asset_id)
    generated = client.post(
        f"/api/projects/{derived['id']}/assets/{derived_asset_id}/generate-image",
        json={"prompt": "真人实拍电影质感，避免三维CG渲染，一只蓝色卡通猫，圆耳朵。"},
    )
    assert generated.status_code == 200, generated.text
    image = generated.json()["asset"]["images"][0]
    assert image["prompt"].count(main.CG_ROLE_FIXED_TEMPLATE) == 1
    assert "蓝色卡通猫" in image["prompt"]
    assert "真人实拍电影质感" not in image["prompt"]
    assert "避免三维CG渲染" not in image["prompt"]
    assert "性别：男" in image["prompt"]
    assert image["payload"]["promptSnapshot"] == image["prompt"]
    assert image["payload"]["remakeMode"] == "cg_3d"
    assert image["payload"]["templateVersion"] == main.ASSET_PROMPT_TEMPLATE_VERSION
    with main.connect() as conn:
        persisted = json.loads(conn.execute("SELECT payload_json FROM assets WHERE id=?", (derived_asset_id,)).fetchone()[0])
        assert persisted["原片资产描述"] == "源片里是一位30岁男性，穿蓝色外套。"
    assert persisted["目标生成描述"] == "真人实拍电影质感，避免三维CG渲染，一只蓝色卡通猫，圆耳朵。"


def test_replacement_generates_full_prompt_without_manual_target_description(client: TestClient):
    project = create_project(client, title="replacement-no-target", remakeMode="asset_replacement", sourceLanguage="中文", replaceCharacters=True)
    asset_id, _ = insert_source_records(project, selected_image=False)
    prepare_valid_role_asset(asset_id)
    response = client.post(f"/api/projects/{project['id']}/assets/{asset_id}/generate-image", json={})
    assert response.status_code == 200, response.text
    image = response.json()["asset"]["images"][0]
    assert "【共同展示构图】" in image["prompt"]
    assert "左侧一个正面面部特写" in image["prompt"]
    assert "【目标视觉风格】" in image["prompt"]
    assert image["payload"]["targetDescription"] == ""
    assert "蓝色外套" in image["prompt"] or "蓝色" in image["prompt"]


def test_editable_final_prompt_persists_and_is_sent_exactly_without_retemplating(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    project = create_project(client, title="editable-final-prompt", remakeMode="overseas_localization", sourceLanguage="中文")
    asset_id, _ = insert_source_records(project, selected_image=False)
    provider_prompts: list[str] = []
    prepared_agents: list[str] = []

    class ProviderSpy:
        def generate_image(self, **kwargs):
            provider_prompts.append(kwargs["prompt"])
            image = io.BytesIO()
            Image.new("RGB", (4, 4), (80, 90, 100)).save(image, format="PNG")
            return {"image_bytes": image.getvalue(), "mime_type": "image/png", "size": kwargs["size"], "model": "provider-spy", "raw": {}}

    monkeypatch.setattr(main, "prepare_agent", lambda key: (prepared_agents.append(key) or {"mode": "volcengine", "model_name": "provider-spy", "prompt": "", "prompt_file": "test"}))
    monkeypatch.setattr(main, "load_models_config", lambda: {"volcengine": {"imageSize": "2K", "imageWatermark": True}})
    monkeypatch.setattr(main, "get_model_provider", lambda _config: ProviderSpy())

    edited = "角色单人设定图。左侧正面面部特写；右侧正面、90度侧面、背面三张全身图。同一套青色棉布长袍与黑色布鞋，纯白背景。"
    saved = client.patch(f"/api/projects/{project['id']}/assets/{asset_id}", json={"payload": {"用户编辑资产图生成提示词": edited}})
    assert saved.status_code == 200, saved.text
    saved_asset = saved.json()["asset"]
    assert saved_asset["payload"]["用户编辑资产图生成提示词"] == edited
    assert saved_asset["payload"]["最终资产图生成提示词"] == edited
    assert saved_asset["payload"]["自动编译资产图生成提示词"] != edited

    for _ in range(2):
        response = client.post(f"/api/projects/{project['id']}/assets/{asset_id}/generate-image", json={"finalPrompt": edited})
        assert response.status_code == 200, response.text
        assert response.json()["asset"]["images"][0]["prompt"] == edited

    reloaded = client.get(f"/api/projects/{project['id']}/assets").json()["assets"]
    loaded_asset = next(item for item in reloaded if item["id"] == asset_id)
    assert loaded_asset["payload"]["用户编辑资产图生成提示词"] == edited
    assert loaded_asset["payload"]["最终资产图生成提示词"] == edited
    assert provider_prompts == [edited, edited]
    assert prepared_agents == ["assetImageGeneration", "assetImageGeneration"]
    assert "【基础设定】" not in provider_prompts[0]


def test_blank_or_unresolved_manual_final_prompt_fails_before_job_or_provider(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    project = create_project(client, title="manual-final-prompt-guard", remakeMode="overseas_localization", sourceLanguage="中文")
    asset_id, _ = insert_source_records(project, selected_image=False)
    called = False

    class ProviderMustNotRun:
        def generate_image(self, **_kwargs):
            nonlocal called
            called = True
            raise AssertionError("invalid manual prompt must be blocked before provider")

    monkeypatch.setattr(main, "prepare_agent", lambda _key: {"mode": "volcengine", "model_name": "must-not-run", "prompt": "", "prompt_file": "test"})
    monkeypatch.setattr(main, "get_model_provider", lambda _config: ProviderMustNotRun())
    for value, expected in (("  \n", "不能为空"), ("根据原片补足衣摆细节", "外部指代")):
        response = client.post(f"/api/projects/{project['id']}/assets/{asset_id}/generate-image", json={"finalPrompt": value})
        assert response.status_code == 422
        assert expected in response.json()["detail"]
    with main.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM asset_generation_jobs WHERE asset_id=?", (asset_id,)).fetchone()[0] == 0
    assert called is False


def test_sending_unchanged_automatic_prompt_does_not_persist_manual_override(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    project = create_project(client, title="unchanged-auto-prompt", remakeMode="overseas_localization", sourceLanguage="中文")
    asset_id, _ = insert_source_records(project, selected_image=False)
    prepare_valid_role_asset(asset_id)
    asset = client.get(f"/api/projects/{project['id']}/assets").json()["assets"][0]
    automatic = asset["payload"]["自动编译资产图生成提示词"]
    captured: list[str] = []

    class ProviderSpy:
        def generate_image(self, **kwargs):
            captured.append(kwargs["prompt"])
            image = io.BytesIO()
            Image.new("RGB", (4, 4), (20, 30, 40)).save(image, format="PNG")
            return {"image_bytes": image.getvalue(), "mime_type": "image/png", "size": kwargs["size"], "model": "provider-spy", "raw": {}}

    monkeypatch.setattr(main, "prepare_agent", lambda _key: {"mode": "volcengine", "model_name": "provider-spy", "prompt": "", "prompt_file": "test"})
    monkeypatch.setattr(main, "load_models_config", lambda: {"volcengine": {"imageSize": "2K"}})
    monkeypatch.setattr(main, "get_model_provider", lambda _config: ProviderSpy())
    response = client.post(f"/api/projects/{project['id']}/assets/{asset_id}/generate-image", json={"finalPrompt": automatic})
    assert response.status_code == 200, response.text
    assert captured == [automatic]
    saved = client.get(f"/api/projects/{project['id']}/assets").json()["assets"]
    loaded = next(item for item in saved if item["id"] == asset_id)
    assert loaded["payload"]["用户编辑资产图生成提示词"] == ""
    assert loaded["payload"]["最终资产图生成提示词"] == automatic


def test_pending_cg_placeholder_cannot_be_sent_but_manual_self_contained_prompt_can(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    project = create_project(client, title="pending-cg-placeholder", remakeMode="cg_3d", sourceLanguage="中文", targetCountry="美国")
    asset_id, _ = insert_source_records(project, selected_image=False)
    asset = next(item for item in client.get(f"/api/projects/{project['id']}/assets").json()["assets"] if item["id"] == asset_id)
    pending = asset["payload"]["最终资产图生成提示词"]
    assert asset["payload"]["目标画像状态"] == "待本地化"
    assert pending.startswith("目标画像待本地化；")
    assert main.CG_ROLE_FIXED_TEMPLATE not in pending
    provider_calls: list[str] = []

    class ProviderSpy:
        def generate_image(self, **kwargs):
            provider_calls.append(kwargs["prompt"])
            image = io.BytesIO()
            Image.new("RGB", (4, 4), (20, 30, 40)).save(image, format="PNG")
            return {"image_bytes": image.getvalue(), "mime_type": "image/png", "size": kwargs["size"], "model": "provider-spy", "raw": {}}

    monkeypatch.setattr(main, "prepare_agent", lambda _key: {"mode": "volcengine", "model_name": "provider-spy", "prompt": "", "prompt_file": "test"})
    monkeypatch.setattr(main, "load_models_config", lambda: {"volcengine": {"imageSize": "2K"}})
    monkeypatch.setattr(main, "get_model_provider", lambda _config: ProviderSpy())
    rejected = client.post(f"/api/projects/{project['id']}/assets/{asset_id}/generate-image", json={"finalPrompt": pending})
    assert rejected.status_code == 422
    assert "待本地化" in rejected.json()["detail"]
    assert provider_calls == []
    with main.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM asset_generation_jobs WHERE asset_id=?", (asset_id,)).fetchone()[0] == 0

    manual = "手工完整角色设定：成年墨西哥裔美国男性，深棕短发，棕色眼睛，蓝色外套，白色背景。"
    accepted = client.post(f"/api/projects/{project['id']}/assets/{asset_id}/generate-image", json={"finalPrompt": manual})
    assert accepted.status_code == 200, accepted.text
    assert len(provider_calls) == 1
    assert provider_calls[0].startswith(manual)
    assert provider_calls[0].count(main.CG_ROLE_FIXED_TEMPLATE) == 1


def test_manual_prompt_restore_original_clears_override_and_edit_can_resume_generation(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    project = create_project(client, title="restore-manual-prompt", remakeMode="asset_replacement", sourceLanguage="中文", replaceCharacters=True)
    asset_id, _ = insert_source_records(project, selected_image=False)
    client.patch(f"/api/projects/{project['id']}/assets/{asset_id}", json={"payload": {"用户编辑资产图生成提示词": "完整自包含目标角色图，白底四视图。"}})
    restored = client.post(f"/api/projects/{project['id']}/assets/{asset_id}/restore-original")
    assert restored.status_code == 200, restored.text
    assert restored.json()["asset"]["payload"]["用户编辑资产图生成提示词"] == ""
    blocked = client.post(f"/api/projects/{project['id']}/assets/{asset_id}/generate-image", json={"finalPrompt": "完整自包含目标角色图，白底四视图。"})
    assert blocked.status_code == 409

    captured: list[str] = []
    class ProviderSpy:
        def generate_image(self, **kwargs):
            captured.append(kwargs["prompt"])
            image = io.BytesIO()
            Image.new("RGB", (4, 4), (40, 50, 60)).save(image, format="PNG")
            return {"image_bytes": image.getvalue(), "mime_type": "image/png", "size": kwargs["size"], "model": "provider-spy", "raw": {}}

    monkeypatch.setattr(main, "prepare_agent", lambda _key: {"mode": "volcengine", "model_name": "provider-spy", "prompt": "", "prompt_file": "test"})
    monkeypatch.setattr(main, "load_models_config", lambda: {"volcengine": {"imageSize": "2K"}})
    monkeypatch.setattr(main, "get_model_provider", lambda _config: ProviderSpy())
    resumed = client.patch(f"/api/projects/{project['id']}/assets/{asset_id}", json={"payload": {"用户编辑资产图生成提示词": "恢复后编辑的自包含角色图提示词。"}})
    assert resumed.status_code == 200, resumed.text
    generated = client.post(f"/api/projects/{project['id']}/assets/{asset_id}/generate-image", json={"finalPrompt": "恢复后编辑的自包含角色图提示词。"})
    assert generated.status_code == 200, generated.text
    assert captured == ["恢复后编辑的自包含角色图提示词。"]


def test_disabled_replacement_type_cannot_send_reuse_prompt_to_image_provider(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    project = create_project(client, title="replacement-switch-off", remakeMode="asset_replacement", sourceLanguage="中文", replaceCharacters=False)
    asset_id, _ = insert_source_records(project, selected_image=False)
    called = False

    class ProviderMustNotRun:
        def generate_image(self, **_kwargs):
            nonlocal called
            called = True
            raise AssertionError("provider must not run for source-reuse assets")

    monkeypatch.setattr(main, "prepare_agent", lambda _key: {"mode": "volcengine", "model_name": "must-not-run", "prompt": "", "prompt_file": "test"})
    monkeypatch.setattr(main, "get_model_provider", lambda _config: ProviderMustNotRun())
    response = client.post(f"/api/projects/{project['id']}/assets/{asset_id}/generate-image", json={})
    assert response.status_code == 409
    assert "替换开关未启用" in response.json()["detail"]
    assert called is False


def test_legacy_missing_visual_fields_return_actionable_422_before_job_or_image_provider(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    project = create_project(client, title="legacy-incomplete-role", remakeMode="asset_replacement", sourceLanguage="中文", replaceCharacters=True)
    asset_id, _ = insert_source_records(project, selected_image=False)
    image_provider_called = False

    class ProviderMustNotRun:
        def generate_image(self, **_kwargs):
            nonlocal image_provider_called
            image_provider_called = True
            raise AssertionError("image provider must not run when required visual fields are missing")

    monkeypatch.setattr(main, "get_model_provider", lambda _config: ProviderMustNotRun())
    response = client.post(f"/api/projects/{project['id']}/assets/{asset_id}/generate-image", json={})
    assert response.status_code == 422
    assert "角色结构化视觉字段待补全" in response.json()["detail"]
    assert image_provider_called is False
    with main.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM asset_generation_jobs WHERE asset_id=?", (asset_id,)).fetchone()[0] == 0


def test_tier_and_manual_shoe_visibility_conflicts_block_provider_before_job(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    project = create_project(client, title="tier-conflict-guard", remakeMode="overseas_localization", sourceLanguage="中文", targetCountry="中国", targetLanguage="中文")
    asset_id, _ = insert_source_records(project, selected_image=False)
    called = False

    class ProviderMustNotRun:
        def generate_image(self, **_kwargs):
            nonlocal called
            called = True
            raise AssertionError("conflicting visual prompt must be blocked before provider")

    monkeypatch.setattr(main, "prepare_agent", lambda _key: {"mode": "volcengine", "model_name": "must-not-run", "prompt": "", "prompt_file": "test"})
    monkeypatch.setattr(main, "get_model_provider", lambda _config: ProviderMustNotRun())
    high_daily = valid_role_visual_structure(服装={"服装等级": "高位礼服", "场合": "日常", "拖尾程度": "高位典礼遮鞋", "鞋面可见性": "不露鞋面", "脚踝可见性": "不露脚踝"})
    prepare_valid_role_asset(asset_id, structure=high_daily)
    tier_response = client.post(f"/api/projects/{project['id']}/assets/{asset_id}/generate-image", json={})
    assert tier_response.status_code == 422
    assert "服装等级/场合/拖尾级别冲突" in tier_response.json()["detail"]
    with main.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM asset_generation_jobs WHERE asset_id=?", (asset_id,)).fetchone()[0] == 0

    high_ceremony = valid_role_visual_structure(服装={"服装等级": "帝后礼服", "场合": "宫廷登基典礼", "拖尾程度": "高位典礼遮鞋", "摆与地面关系": "裙摆压地遮鞋", "鞋面可见性": "不露鞋面", "脚踝可见性": "不露脚踝"})
    prepare_valid_role_asset(asset_id, structure=high_ceremony)
    override_response = client.post(f"/api/projects/{project['id']}/assets/{asset_id}/generate-image", json={"prompt": "靴尖微露"})
    assert override_response.status_code == 422
    assert "用户目标外观补充要求露出鞋面或脚踝" in override_response.json()["detail"]
    with main.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM asset_generation_jobs WHERE asset_id=?", (asset_id,)).fetchone()[0] == 0
    assert called is False


def test_provider_receives_exact_final_role_prompt_snapshot_and_size(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    project = create_project(client, title="provider-prompt-spy", remakeMode="cg_3d", sourceLanguage="中文")
    asset_id, _ = insert_source_records(project, selected_image=False)
    prepare_valid_role_asset(asset_id)
    captured: dict = {}

    class ProviderSpy:
        def generate_image(self, **kwargs):
            captured.update(kwargs)
            image = io.BytesIO()
            Image.new("RGB", (8, 8), (20, 50, 80)).save(image, format="PNG")
            return {"image_bytes": image.getvalue(), "mime_type": "image/png", "size": kwargs["size"], "model": "provider-spy", "raw": {}}

    monkeypatch.setattr(main, "prepare_agent", lambda _key: {"mode": "volcengine", "model_name": "provider-spy", "prompt": "", "prompt_file": "test"})
    monkeypatch.setattr(main, "load_models_config", lambda: {"volcengine": {"imageSize": "2K", "imageWatermark": True}})
    monkeypatch.setattr(main, "get_model_provider", lambda _config: ProviderSpy())
    response = client.post(
        f"/api/projects/{project['id']}/assets/{asset_id}/generate-image",
        json={"prompt": "蓝色卡通猫，黄色雨衣"},
    )
    assert response.status_code == 200, response.text
    image = response.json()["asset"]["images"][0]
    assert captured["prompt"] == image["prompt"] == image["payload"]["promptSnapshot"]
    assert captured["prompt"].count(main.CG_ROLE_FIXED_TEMPLATE) == 1
    assert captured["size"] == "2K"
    assert image["payload"]["mock"] is False
    repeated = client.post(
        f"/api/projects/{project['id']}/assets/{asset_id}/generate-image",
        json={"prompt": image["prompt"]},
    )
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["asset"]["images"][0]["prompt"] == image["prompt"]
    with main.connect() as conn:
        saved = json.loads(conn.execute("SELECT payload_json FROM assets WHERE id=?", (asset_id,)).fetchone()[0])
    assert saved["目标生成描述"] == "蓝色卡通猫，黄色雨衣"
    direct = client.post(
        f"/api/projects/{project['id']}/assets/{asset_id}/generate-image",
        json={"finalPrompt": "用户手填的完整角色资产描述，黑发棕眼，白底。"},
    )
    assert direct.status_code == 200, direct.text
    direct_image = direct.json()["asset"]["images"][0]
    assert captured["prompt"] == direct_image["prompt"] == direct_image["payload"]["promptSnapshot"]
    assert captured["prompt"].count(main.CG_ROLE_FIXED_TEMPLATE) == 1
    assert captured["prompt"].startswith("用户手填的完整角色资产描述，黑发棕眼，白底。")
    loaded = next(item for item in client.get(f"/api/projects/{project['id']}/assets").json()["assets"] if item["id"] == asset_id)
    assert loaded["payload"]["最终资产图生成提示词"] == captured["prompt"]
    assert loaded["payload"]["最终资产图生成提示词"].count(main.CG_ROLE_FIXED_TEMPLATE) == 1


def test_common_asset_prompt_format_across_modes_and_asset_types():
    asset_payloads = {
        "角色": {
            "原剧角色名称": "源角色甲", "本地化角色名称": "目标角色甲",
            "原剧核心形象描述": "源片角色穿蓝色外套", "本地化核心形象描述": "自动提取目标角色穿深蓝外套",
            "剧情身份": "主角", "基础设定": {"性别": "男性", "视觉年龄": "30岁"},
        },
        "场景": {
            "原剧场景名称": "源场景甲", "本地化场景名称": "目标场景甲",
            "原剧场景描述": "源片旧木屋室内", "本地化场景描述": "自动提取的中国乡村木屋室内空间",
        },
        "道具": {
            "原剧道具名称": "源道具甲", "本地化道具名称": "目标道具甲",
            "原剧道具描述": "源片蓝色搪瓷杯", "本地化道具描述": "自动提取的蓝色搪瓷杯，杯沿有缺口",
        },
    }
    layouts = {
        "角色": main.ROLE_DISPLAY_CONSTRAINT,
        "场景": main.SCENE_DISPLAY_CONSTRAINT,
        "道具": main.PROP_DISPLAY_CONSTRAINT,
    }
    for mode, style, country, language in (
        ("asset_replacement", "live_action", "中国", "沿用原片语言"),
        ("overseas_localization", "live_action", "美国", "英文"),
        ("cg_3d", "cg_3d", "中国", "中文"),
    ):
        project = {
            "remake_mode": mode, "source_language": "中文",
            "target_country": country, "target_language": language,
            "storyboard_visual_style": style,
            "replace_characters": True, "replace_scenes": True,
        }
        resolved_country, resolved_language, resolved_style = main.asset_prompt_context(project)
        if mode == "asset_replacement":
            assert (resolved_country, resolved_language) == ("中国", "中文")
        else:
            assert (resolved_country, resolved_language) == (country, language)
        for asset_type, fixture in asset_payloads.items():
            payload = {**fixture, "目标生成描述": "用户覆盖测试细节"}
            payload["资产类型"] = asset_type
            payload["sourceSnapshot"] = {
                "schemaVersion": main.ASSET_SOURCE_SCHEMA_VERSION,
                "coreDescription": fixture.get("原剧核心形象描述", fixture.get("原剧场景描述", fixture.get("原剧道具描述", ""))),
                "visualDescription": "源视觉事实",
                "sourceStructuredDescription": valid_role_visual_structure() if asset_type == "角色" else {},
                "childId": "child-1",
            }
            if mode != "asset_replacement":
                profile = {
                    "schemaVersion": main.ASSET_LOCALIZATION_SCHEMA_VERSION, "status": "complete",
                    "localizedName": fixture.get("本地化角色名称", fixture.get("本地化场景名称", fixture.get("本地化道具名称", "目标资产"))),
                    "localizedChildName": "目标子资产",
                    "coreDescription": fixture.get("本地化核心形象描述", fixture.get("本地化场景描述", fixture.get("本地化道具描述", ""))),
                    "visualDescription": fixture.get("本地化核心形象描述", ""),
                    "structuredDescription": valid_role_visual_structure() if asset_type == "角色" else {},
                    "assetImagePrompt": "",
                }
                profile["targetContext"] = main.localized_profile_target_context(payload, project)
                payload["localizedProfile"] = profile
            prompt = main.build_project_asset_prompt(asset_type, payload, project)
            assert layouts[asset_type] in prompt, f"{mode}/{asset_type}"
            assert "用户覆盖测试细节" in prompt
            if country == "中国":
                source_text = "源视觉事实" if asset_type == "角色" else fixture.get("原剧场景描述", fixture.get("原剧道具描述", ""))
                assert source_text in prompt
            else:
                assert "自动提取" in prompt
            assert resolved_country in prompt and resolved_language in prompt
            assert "优先于同字段" in prompt
            assert main.build_project_asset_prompt(asset_type, payload, project) == prompt
            if resolved_style == "cg_3d":
                assert "真人实拍" not in prompt
                assert "避免卡通、绘画和三维渲染" not in prompt
                assert "Unreal Engine 5 cinematic render" in prompt
            else:
                assert "风格：真人" in prompt
                if asset_type == "角色":
                    assert "避免卡通、绘画和三维渲染" in prompt

    explicit_country = main.asset_prompt_context({
        "remake_mode": "asset_replacement", "source_language": "中文",
        "target_country": "美国", "target_language": "英语",
    })
    assert explicit_country == ("美国", "英语", "live_action")


def test_english_target_only_controls_visible_asset_text_and_preserves_english_override():
    assert "不要执行目标国家本地化" in main.ASSET_SOURCE_EXTRACTION_COMPACT_PROMPT
    assert "不得输出本地化字段" in main.ASSET_SOURCE_EXTRACTION_COMPACT_PROMPT
    assert "原片观察到的族裔" in main.ASSET_LOCALIZATION_COMPACT_PROMPT
    assert "不得默认美国等于白人" in main.ASSET_LOCALIZATION_COMPACT_PROMPT
    assert "内部核心描述、视觉描述和目标图像描述统一使用简体中文" in main.ASSET_LOCALIZATION_COMPACT_PROMPT
    assert "目标视觉描述及资产图提示词均使用简体中文" in main.ASSET_DETAIL_COMPLETION_PROMPT
    assert "仅服装字样、店招、铭牌等资产表面可见文字遵循目标语言" in main.ASSET_DETAIL_COMPLETION_PROMPT

    project = {
        "remake_mode": "overseas_localization", "source_language": "中文",
        "target_country": "美国", "target_language": "英语", "storyboard_visual_style": "live_action",
    }
    payload = {
        "原剧角色名称": "源角色甲", "本地化角色名称": "目标角色甲",
        "本地化核心形象描述": "自动提取的角色穿深蓝棉质外套",
        "目标生成描述": "A cotton coat with a small blue patch.\nKeep the original seam pattern.",
        "基础设定": {"性别": "男性", "视觉年龄": "30岁"},
        "资产类型": "角色",
        "sourceSnapshot": {"schemaVersion": main.ASSET_SOURCE_SCHEMA_VERSION, "coreDescription": "源角色", "visualDescription": "源视觉", "sourceStructuredDescription": valid_role_visual_structure(), "childId": "child-1"},
    }
    payload["localizedProfile"] = {
        "schemaVersion": main.ASSET_LOCALIZATION_SCHEMA_VERSION, "status": "complete",
        "localizedName": "目标角色甲", "localizedChildName": "目标服装",
        "coreDescription": "自动提取的角色穿深蓝棉质外套", "visualDescription": "自动提取的角色穿深蓝棉质外套",
        "structuredDescription": valid_role_visual_structure(), "assetImagePrompt": "",
    }
    payload["localizedProfile"]["targetContext"] = main.localized_profile_target_context(payload, project)
    prompt = main.build_project_asset_prompt("角色", payload, project)
    assert "性别：" in prompt
    assert "国家 / 时代：美国 / " in prompt
    assert " / 现代" not in prompt
    assert "本地化视觉锚点：自动提取的角色穿深蓝棉质外套" in prompt
    assert "目标语言：英语" in prompt
    assert "服装上如有可见文字，必须使用目标语言和目标国家常见排版；不得出现中文汉字" in prompt
    assert "资产提示词使用与目标语言相符的文字" not in main.ASSET_EXTRACTION_COMPACT_PROMPT

    raw_override = "A cotton coat with a small blue patch.\nKeep the original seam pattern."
    cleaned_override = main.clean_target_asset_description(raw_override, "live_action")
    assert cleaned_override == raw_override
    generated_prompt = main.build_project_asset_prompt("角色", {**payload, "目标生成描述": raw_override}, project)
    recovered_override, should_update = main.normalize_asset_visual_override(generated_prompt, "old value")
    assert should_update is True
    assert recovered_override == raw_override
    rebuilt_prompt = main.build_project_asset_prompt("角色", {**payload, "目标生成描述": recovered_override}, project)
    assert rebuilt_prompt == generated_prompt


def test_hairline_value_describing_no_bangs_is_not_misread_as_unknown():
    hairline = "无刘海，后梳中分线不明显"
    assert main.usable_visual_value(hairline) is True
    structure = valid_role_visual_structure(发型={"刘海 / 分线": hairline})
    assert "发型.刘海 / 分线" not in main.role_structured_missing_paths(structure)


def test_v3_role_drape_tiers_are_evidence_based_and_visibility_is_negation_aware():
    child = {"角色资产图结构化描述": valid_role_visual_structure()}
    payload = {"资产类型": "角色", "当前子资产": child, "sourceSnapshot": {"schemaVersion": main.ASSET_SOURCE_SCHEMA_VERSION, "coreDescription": "源角色", "visualDescription": "源服装", "sourceStructuredDescription": child["角色资产图结构化描述"], "childId": "child-1"}}
    clothing = child["角色资产图结构化描述"]["服装"]

    # "无" is a meaningful observed value, not a missing/unknown placeholder.
    assert main.role_structured_missing_paths(valid_role_visual_structure(
        服装={"中间层": "无", "拖尾程度": "无/不拖地", "摆与地面关系": "下摆离地，不拖地"}
    )) == []
    assert main.role_clothing_tier(payload, child) == "普通垂摆"

    clothing.update({"服装等级": "日常服", "场合": "日常", "拖尾程度": "普通垂摆"})
    assert main.role_clothing_tier(payload, child) == "普通垂摆"
    clothing.update({"服装等级": "高位礼服", "场合": "日常", "拖尾程度": "无/不拖地"})
    assert main.role_clothing_tier(payload, child) == "普通垂摆"
    clothing.update({"服装等级": "高位礼服", "场合": "宫廷登基典礼", "拖尾程度": "高位典礼遮鞋"})
    assert main.role_clothing_tier(payload, child) == "高位典礼遮鞋"
    clothing.update({"鞋子": "金色朝靴", "鞋面可见性": "不露鞋面", "脚踝可见性": "不露脚踝", "摆与地面关系": "厚重袍摆压地并遮住鞋面"})
    ceremony_prompt = main.build_project_asset_prompt("角色", {**payload, "当前子资产": child}, {
        "remake_mode": "overseas_localization", "target_country": "中国", "target_language": "中文",
        "storyboard_visual_style": "live_action",
    })
    assert "外袍下摆连续垂至地面并覆盖全部脚部" in ceremony_prompt
    assert "前、侧、背外袍下摆连续触地，完整覆盖鞋履与脚踝" in ceremony_prompt
    assert "鞋履仅作衣摆内的穿着事实" in ceremony_prompt
    assert "下肢保持自然站姿但完全隐藏于衣摆内，不绘制双脚" in ceremony_prompt
    assert "双脚平行与肩同宽" not in ceremony_prompt
    assert "衣长：长袍及踝" not in ceremony_prompt
    assert "金色朝靴" not in ceremony_prompt
    clothing.update({"服装等级": "帝王礼服", "场合": "宫廷登基典礼", "拖尾程度": "高位典礼遮鞋"})
    assert main.role_clothing_tier(payload, child) == "高位典礼遮鞋"
    clothing["场合"] = "日常"
    assert main.role_clothing_tier(payload, child) == ""
    assert main.role_clothing_tier_conflict(payload, child)
    clothing["场合"] = "宫廷登基典礼"
    assert main.visibility_state_is_exposed("靴尖微露，其余被遮挡") is True
    for covered in ("不可见", "不外露", "完全遮挡", "不露鞋面、不露脚踝"):
        assert main.visibility_state_is_exposed(covered) is False
    clothing["鞋面可见性"] = "露出靴尖"
    prompt = main.build_project_asset_prompt("角色", {**payload, "当前子资产": child}, {
        "remake_mode": "overseas_localization", "target_country": "中国", "target_language": "中文",
        "storyboard_visual_style": "live_action",
    })
    assert any("高位典礼服装要求遮鞋" in item for item in main.asset_prompt_validation_errors(
        "角色", {**payload, "当前子资产": child}, {
            "remake_mode": "overseas_localization", "target_country": "中国", "target_language": "中文",
            "storyboard_visual_style": "live_action",
        }, prompt
    ))
    clothing["鞋面可见性"] = "靴尖微露，其余被遮挡"
    mixed_prompt = main.build_project_asset_prompt("角色", {**payload, "当前子资产": child}, {
        "remake_mode": "overseas_localization", "target_country": "中国", "target_language": "中文",
        "storyboard_visual_style": "live_action",
    })
    assert any("高位典礼服装要求遮鞋" in item for item in main.asset_prompt_validation_errors(
        "角色", {**payload, "当前子资产": child}, {
            "remake_mode": "overseas_localization", "target_country": "中国", "target_language": "中文",
            "storyboard_visual_style": "live_action",
        }, mixed_prompt
    ))

    clothing.update({"拖尾程度": "高位典礼遮鞋", "场合": "日常"})
    assert main.role_clothing_tier(payload, child) == ""
    assert main.role_clothing_tier_conflict(payload, child)
    conflict_prompt = main.build_project_asset_prompt("角色", {**payload, "当前子资产": child}, {
        "remake_mode": "overseas_localization", "target_country": "中国", "target_language": "中文",
        "storyboard_visual_style": "live_action",
    })
    assert "拖尾字段要求高位典礼遮鞋" not in conflict_prompt


def test_v3_role_material_and_craft_task_placeholders_are_rejected_until_expanded():
    structure = valid_role_visual_structure(服装={
        "服装等级": "帝王礼服", "场合": "宫廷登基典礼", "拖尾程度": "高位典礼遮鞋",
        "面料": "丝绸与棉麻按衣层对应", "工艺与重工程度": "依服装等级呈现，不额外添加工艺",
    })
    child = {"角色资产图结构化描述": structure}
    payload = {"当前子资产": child, "目标生成描述": "玄色礼袍，前侧后下摆连续触地并完全遮住脚部。"}
    missing = main.role_structured_missing_paths(structure)
    assert any("服装.面料（需写明具体内容）" in item for item in missing)
    assert any("服装.工艺与重工程度（需写明具体内容）" in item for item in missing)
    project = {"remake_mode": "overseas_localization", "target_country": "中国", "target_language": "中文", "storyboard_visual_style": "live_action"}
    prompt = main.build_project_asset_prompt("角色", payload, project)
    errors = main.asset_prompt_validation_errors("角色", payload, project, prompt)
    assert any("角色结构化视觉字段待补全" in item for item in errors)
    assert any("服装.面料（需写明具体内容）" in item for item in errors)
    assert any("服装.工艺与重工程度（需写明具体内容）" in item for item in errors)

    structure["服装"].update({
        "面料": "内衬为黑色细密绸缎，中间层为深玄色织锦，外层为玄色重磅丝绸",
        "工艺与重工程度": "金线盘绣集中在前襟和宽阔袍摆边缘，采用礼服级重工包边",
    })
    assert not main.role_structured_missing_paths(structure)


def test_unknown_gender_and_modern_identity_do_not_invent_gendered_or_historical_clothing():
    structure = valid_role_visual_structure(
        基础设定={"性别": "未知", "性别判断依据": ""},
        服装={"服装等级": "日常服", "场合": "现代日常", "外层": "", "领型": "", "袖型": "", "腰线": "", "衣长": "", "下装": "", "摆型": "", "拖尾程度": "无/不拖地", "摆与地面关系": "下摆离地，不拖地", "鞋子": "", "鞋面可见性": "无", "脚踝可见性": "无", "面料": "", "纹样位置": "无"},
    )
    child = {"角色资产图结构化描述": structure}
    payload = {"当前子资产": child, "剧情身份": "现代剧亲王身份角色"}
    assert main.role_gender_prompt_text(payload, child).startswith("性别未定，采用中性")
    assert main.infer_role_prompt_field(payload, child, "服装", "外层") == ""
    prompt = main.build_project_asset_prompt("角色", payload, {
        "remake_mode": "overseas_localization", "target_country": "中国", "target_language": "中文",
        "storyboard_visual_style": "live_action",
    })
    assert "性别：性别未定，采用中性" in prompt
    assert "凤冠" not in prompt and "冕冠" not in prompt and "古装" not in prompt


def test_role_nonwearable_filter_removes_only_positive_handheld_or_hanging_items():
    source = "头戴金冠，身穿黑色外袍，腰挂宝剑。固定穿戴金属腰封，无腰挂物。Handheld fan and cotton coat.\nNo props; same person, same outfit across views."
    cleaned = main.strip_role_nonwearable_items(source)
    assert "头戴金冠" in cleaned
    assert "黑色外袍" in cleaned
    assert "固定穿戴金属腰封" in cleaned
    assert "无腰挂物" in cleaned
    assert "cotton coat" in cleaned
    assert "No props" in cleaned
    assert "same person, same outfit across views" in cleaned
    assert "腰挂宝剑" not in cleaned
    assert "Handheld fan" not in cleaned


def test_overseas_localization_prioritizes_user_ethnicity_and_compiles_target_visual_fields(monkeypatch: pytest.MonkeyPatch):
    prompt_file = Path(__file__).resolve().parents[2] / "config" / "agents" / "asset_extraction_localization.md"
    system_prompt = prompt_file.read_text(encoding="utf-8")
    assert "用户明确提供的目标角色外貌/族裔、场景设定、时代或文化要求优先" in system_prompt
    assert "原视频观察到的年龄、性别表现" in system_prompt
    assert "原片族裔、肤色、脸部和发型属于“原剧视觉事实”" in system_prompt
    assert "不得机械执行“美国=白人”" in system_prompt
    assert "欧洲裔美国白人" not in system_prompt
    assert "不得给所有欧美角色统一指定白人、蓝眼或高鼻" in main.ASSET_DETAIL_COMPLETION_PROMPT

    project = {
        "remake_mode": "overseas_localization", "source_language": "中文",
        "target_country": "美国", "target_language": "英语", "storyboard_visual_style": "live_action",
    }
    target_values = valid_role_visual_structure(
        基础设定={"视觉年龄": "二十多岁", "国家 / 时代 / 朝代": "美国 / 当代婚礼", "身份定位": "新郎", "族裔外貌": "墨西哥裔美国男性"},
        脸部特征={"皮肤肤色": "暖橄榄色", "骨相": "中等颧骨、柔和下颌线", "虹膜颜色": "深棕色"},
        发型={"发型名称": "整洁短款侧分", "发色": "深棕色"},
        服装={"服装等级": "正式礼服", "场合": "美国当代婚礼", "外层": "深蓝色现代西装"},
    )
    target_payload = {
        "剧情身份": "新郎",
        "原剧核心形象描述": "源事实：二十多岁东亚裔男性，黑发，参加中国传统婚礼。",
        "sourceSnapshot": {"schemaVersion": main.ASSET_SOURCE_SCHEMA_VERSION, "coreDescription": "源事实：二十多岁东亚裔男性，黑发，参加中国传统婚礼。", "visualDescription": "源事实：东亚裔男性、黑色短发。", "sourceStructuredDescription": {}, "childId": "child-1"},
        "当前子资产": {
            "原剧视觉形象描述": "源事实：东亚裔男性、黑色短发。",
            "本地化视觉形象描述": "二十多岁墨西哥裔美国新郎，暖橄榄色皮肤、深棕眼睛、整洁深棕短发侧分，身穿深蓝现代西装，处于美国当代婚礼场景。",
            "角色资产图结构化描述": target_values,
        },
    }
    target_payload["localizedProfile"] = {
        "schemaVersion": main.ASSET_LOCALIZATION_SCHEMA_VERSION, "status": "complete", "localizedName": "Alex", "localizedChildName": "Wedding attire",
        "coreDescription": "", "visualDescription": "", "structuredDescription": target_values,
    }
    target_payload["localizedProfile"]["targetContext"] = main.localized_profile_target_context(target_payload, project)
    localized_prompt = main.build_project_asset_prompt("角色", target_payload, project)
    assert "国家 / 时代：美国 / 美国 / 当代婚礼" in localized_prompt
    assert "族裔外貌：墨西哥裔美国男性" in localized_prompt
    assert "暖橄榄色" in localized_prompt and "深棕色" in localized_prompt
    assert "美国当代婚礼" in localized_prompt
    assert "中国传统婚礼" not in localized_prompt
    assert "东亚裔男性" not in localized_prompt

    explicit_target = json.loads(json.dumps(target_payload, ensure_ascii=False))
    explicit_target["当前子资产"]["本地化视觉形象描述"] = "用户指定东亚裔美国新郎；暖米色皮肤、深棕眼睛、黑色短发。"
    explicit_target["当前子资产"]["角色资产图结构化描述"]["基础设定"]["族裔外貌"] = "东亚裔美国男性（用户明确设定）"
    explicit_target["目标生成描述"] = "用户明确指定东亚裔美国新郎；暖米色皮肤、深棕眼睛、黑色短发。"
    explicit_target["targetVisualLockProvenance"] = "user_override"
    explicit_target["localizedProfile"]["structuredDescription"]["基础设定"]["族裔外貌"] = "东亚裔美国男性（用户明确设定）"
    explicit_target["localizedProfile"]["structuredDescription"]["脸部特征"]["目标族裔背景"] = "东亚裔美国男性（用户明确设定）"
    explicit_target["localizedProfile"]["coreDescription"] = "用户指定东亚裔美国新郎；暖米色皮肤、深棕眼睛、黑色短发。"
    explicit_target["localizedProfile"]["targetContext"] = main.localized_profile_target_context(explicit_target, project)
    explicit_prompt = main.build_project_asset_prompt("角色", explicit_target, project)
    assert "族裔外貌：东亚裔美国男性（用户明确设定）" in explicit_prompt
    assert "用户指定东亚裔美国新郎" in explicit_prompt
    assert "欧洲裔美国白人" not in explicit_prompt


def test_source_and_localization_caches_have_independent_inputs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(main, "load_models_config", lambda: {"volcengine": {"maxOutputTokens": 4096, "thinking": False}})
    agent = {"mode": "volcengine", "model_name": "cache-test", "temperature": 0, "output_format": "json", "prompt_file": "asset_extraction_localization.md", "prompt": "stable agent prompt"}
    project = {"remake_mode": "overseas_localization", "target_country": "美国", "target_language": "英语"}
    before = main.asset_source_cache_key("same-source-sha", agent, project, [])
    original = main.ASSET_LOCALIZATION_COMPACT_PROMPT
    localization_before = main.asset_localization_cache_key([], project, agent)
    monkeypatch.setattr(main, "ASSET_LOCALIZATION_COMPACT_PROMPT", original + "\n新增目标族裔一致性校验")
    after = main.asset_source_cache_key("same-source-sha", agent, project, [])
    localization_after = main.asset_localization_cache_key([], project, agent)
    assert before == after
    assert localization_before != localization_after
    assert main.asset_source_cache_key("same-source-sha", agent, {**project, "target_country": "加拿大", "target_language": "法语", "remake_mode": "cg_3d"}, []) == before
    source_history = [{"资产类型": "角色", "原剧资产名称": "源角色", "本地化资产名称": "US target", "localizedProfile": {"face": "target-only"}}]
    changed_target_history = [{**source_history[0], "本地化资产名称": "Canadian target", "localizedProfile": {"face": "different target"}}]
    assert main.asset_source_cache_key("same-source-sha", agent, project, source_history) == main.asset_source_cache_key("same-source-sha", agent, project, changed_target_history)


def test_role_cg_suffix_preserves_exact_template_and_normalizes_legacy_wrappers():
    project = {"remake_mode": "cg_3d", "target_country": "美国", "target_language": "英语"}
    specification = (main.ROOT / "docs" / "小云雀重绘多模式功能方案-草案-2026-10-23.md").read_text(encoding="utf-8")
    template_start = specification.index("[Style & Render]")
    template_end = specification.index("\n\n### 11.2", template_start)
    assert main.CG_ROLE_FIXED_TEMPLATE == specification[template_start:template_end]
    body = "角色年龄与外观正文。"
    legacy_inputs = (
        f"{body}\n\n【固定约束】\n{main.CG_ROLE_FIXED_TEMPLATE}",
        f"{body}\n\n固定约束\n{main.CG_ROLE_FIXED_TEMPLATE}",
        f"{body}\n\n固定约束 \n{main.CG_ROLE_FIXED_TEMPLATE}",
    )
    for source_prompt in legacy_inputs:
        final = main.enforce_role_cg_fixed_suffix(source_prompt, "角色", project)
        assert final.startswith(body)
        assert final.count(main.CG_ROLE_FIXED_TEMPLATE) == 1
        assert final.count("固定约束") == 1


@pytest.mark.parametrize(("target_country", "expect_localizer"), [("美国", True), ("中国", False)])
def test_extraction_uses_separate_source_and_target_model_messages(client: TestClient, monkeypatch: pytest.MonkeyPatch, target_country: str, expect_localizer: bool):
    project = create_project(client, title=f"two-stage-{target_country}", remakeMode="cg_3d", sourceLanguage="中文", targetCountry=target_country, targetLanguage="英语" if target_country == "美国" else "中文")
    episode_id = "episode-two-stage"
    with main.connect() as conn:
        conn.execute("INSERT INTO episodes (id,project_id,episode_index,filename,file_path,source_url,ark_file_id,status,created_at) VALUES (?,?,?,?,?,?,?,?,?)", (episode_id, project["id"], 1, "sample.mp4", "missing-local.mp4", "", "ark-source-id", "ready", "2026-01-01T00:00:00Z"))

    source_structure = valid_role_visual_structure()
    source_structure["基础设定"]["国家 / 时代 / 朝代"] = "现代中国"
    source_structure["基础设定"]["族裔外貌"] = "原片观察：东亚裔中国男性"
    source_structure["脸部特征"].pop("目标族裔背景", None)
    source_child = {
        "子资产编号": "role-source-1", "角色资产变量": "{{role-source-1}}", "原剧子资产名称": "原片日常装",
        "原剧视觉形象描述": "原片观察：东亚裔中国男性，黑色短发。",
        "原剧角色资产图结构化描述": source_structure,
    }
    source_result = {"资产库": {"角色资产": [{
        "主资产编号": "role-source", "原剧角色名称": "林浩", "剧情身份": "新郎",
        "原剧核心形象描述": "原片观察：二十多岁东亚裔男性，现代中国婚礼。", "原剧角色资产图结构化描述": source_structure,
        "子资产": [source_child],
    }, {
        "主资产编号": "crowd-source", "原剧角色名称": "婚礼宾客群像", "剧情身份": "婚礼背景宾客群像",
        "原剧核心形象描述": "婚礼宾客群像，数名宾客站在礼堂后排。",
        "子资产": [{"子资产编号": "crowd-child", "角色资产变量": "{{crowd-child}}", "原剧子资产名称": "礼堂后排群像", "原剧视觉形象描述": "数名宾客站在礼堂后排。"}],
    }]}, "资产出现片段索引": []}
    target_structure = valid_role_visual_structure()
    target_structure["基础设定"]["族裔外貌"] = "墨西哥裔美国男性"
    target_structure["基础设定"]["国家 / 时代 / 朝代"] = "美国 / 当代婚礼"
    target_structure["脸部特征"]["目标族裔背景"] = "墨西哥裔美国男性"
    target_structure["脸部特征"]["皮肤肤色"] = "暖橄榄色"
    target_structure["发型"]["发色"] = "深棕色"
    target_structure = {
        "基础设定": {"族裔外貌": "墨西哥裔美国男性"},
        "脸部特征": target_structure["脸部特征"],
        "发型": target_structure["发型"],
    }
    localized_result = {"localizedAssets": [{
        "assetVariable": "{{role-source-1}}",
        "localizedProfile": {
            "localizedName": "Alex", "localizedChildName": "Wedding attire",
            "coreDescription": "二十多岁墨西哥裔美国新郎，黑色短发，参加美国当代婚礼。",
            "visualDescription": "墨西哥裔美国男性，暖橄榄肤色、深棕眼、整洁黑色短发，穿现代西装。",
            "structuredDescription": target_structure, "assetImagePrompt": "来源提示：现代中国婚礼、东亚裔黑发。",
        },
    }]}
    captured: list[dict] = []

    target_call_count = 0

    class StageProvider:

        def chat_completion_stream(self, **kwargs):
            captured.append({"stage": "source", **kwargs})
            yield "response.output_text.delta", {"delta": json.dumps(source_result, ensure_ascii=False)}
            yield "response.completed", {}

        def chat_completion(self, **kwargs):
            nonlocal target_call_count
            captured.append({"stage": "target", **kwargs})
            target_call_count += 1
            target_result = json.loads(json.dumps(localized_result, ensure_ascii=False))
            target_input = json.loads(kwargs["user_content"])
            if target_input["targetCountry"] == "美国" and target_call_count == 1:
                for item in target_result["localizedAssets"]:
                    item["localizedProfile"]["structuredDescription"]["脸部特征"].pop("眼神气质", None)
            if target_input["targetCountry"] == "加拿大":
                for item in target_result["localizedAssets"]:
                    profile = item["localizedProfile"]
                    structure = profile["structuredDescription"]
                    structure["基础设定"]["族裔外貌"] = "加拿大拉丁裔男性"
                    structure["脸部特征"]["目标族裔背景"] = "加拿大拉丁裔男性"
            return json.dumps(target_result, ensure_ascii=False)

    monkeypatch.setattr(main, "prepare_agent", lambda _key: {"mode": "volcengine", "model_name": "stage-spy", "prompt": "legacy combined system must not be sent", "prompt_file": "legacy-combined.md", "temperature": 0, "output_format": "json"})
    monkeypatch.setattr(main, "get_model_provider", lambda _config: StageProvider())
    monkeypatch.setattr(main, "load_models_config", lambda: {"volcengine": {"maxOutputTokens": 4096}})
    monkeypatch.setattr(main, "source_file_sha256", lambda _path: "same-source-sha")
    monkeypatch.setattr(main, "capture_asset_original_frames", lambda *_args: {})

    with main.connect() as conn:
        task = main.run_asset_extraction(conn, main.get_project_or_404(conn, project["id"]), episode_id)
    assert task["status"] == "成功"
    assert [item["stage"] for item in captured] == (["source", "target", "target"] if expect_localizer else ["source"])
    source_call = captured[0]
    assert source_call["system_prompt"] == main.ASSET_SOURCE_EXTRACTION_COMPACT_PROMPT
    assert source_call["system_prompt"] == (main.CONFIG_DIR / "agents" / "asset_source_extraction.md").read_text(encoding="utf-8")
    assert "legacy combined system must not be sent" not in source_call["system_prompt"]
    source_input = json.loads(source_call["user_content"])
    assert not any(key in source_input for key in ("目标国家", "目标语言", "重制模式", "storyboard_visual_style"))
    with main.connect() as conn:
        asset_row = conn.execute("SELECT * FROM assets WHERE project_id=? AND variable='{{role-source-1}}'", (project["id"],)).fetchone()
        before_get = asset_row["payload_json"]
    stored = json.loads(before_get)
    assert stored["sourceSnapshot"]["sourceStructuredDescription"]["脸部特征"]["脸型"] == "椭圆脸"
    assert "身材体态" in stored["sourceSnapshot"]["sourceStructuredDescription"]
    assert "服装" in stored["sourceSnapshot"]["sourceStructuredDescription"]
    if expect_localizer:
        target_call = captured[1]
        assert target_call["system_prompt"] == main.ASSET_LOCALIZATION_COMPACT_PROMPT
        assert target_call["system_prompt"] == (main.CONFIG_DIR / "agents" / "asset_target_localization.md").read_text(encoding="utf-8")
        assert "legacy combined system must not be sent" not in target_call["system_prompt"]
        target_input = json.loads(target_call["user_content"])
        assert target_input["targetCountry"] == target_country
        assert [item["assetVariable"] for item in target_input["assets"]] == ["{{role-source-1}}"]
        assert "sourceSnapshot" in target_call["user_content"]
        correction_input = json.loads(captured[2]["user_content"])
        assert correction_input["correction"]["validationErrors"]
        assert "眼神气质缺失" in " ".join(correction_input["correction"]["validationErrors"])
        assert "sourceSnapshot" in correction_input["assets"][0]
        assert stored["localizedProfile"]["structuredDescription"]["脸部特征"]["目标族裔背景"] == "墨西哥裔美国男性"
        assert stored["localizedProfile"]["status"] == "complete"
        assert stored["localizedProfile"]["structuredDescription"]["基础设定"]["国家 / 时代 / 朝代"] == "现代"
        assert stored["localizedProfile"]["structuredDescription"]["身材体态"] == source_structure["身材体态"]
        assert stored["localizedProfile"]["structuredDescription"]["服装"] == source_structure["服装"]
        assert "东亚裔" not in stored["localizedProfile"]["coreDescription"]
        assert "深棕色" in stored["localizedProfile"]["coreDescription"]
        assert "黑色" not in stored["localizedProfile"]["coreDescription"]
        assert "深棕色" in stored["localizedProfile"]["visualDescription"]
        assert "黑色" not in stored["localizedProfile"]["visualDescription"]
        automatic_prompt = client.get(f"/api/projects/{project['id']}/assets").json()["assets"][0]["payload"]["自动编译资产图生成提示词"]
        assert "墨西哥裔美国男性" in automatic_prompt
        assert "深棕色" in automatic_prompt
        assert "来源提示：现代中国婚礼" not in automatic_prompt
        with main.connect() as conn:
            crowd_row = conn.execute("SELECT payload_json FROM assets WHERE project_id=? AND variable='{{crowd-child}}'", (project["id"],)).fetchone()
        crowd_payload = json.loads(crowd_row["payload_json"])
        assert crowd_payload["localizedProfile"]["status"] == "pending"
        assert crowd_payload["localizedProfile"]["needsReview"] is True
        assert "群像" in crowd_payload["localizedProfile"]["reviewReason"]
        listed_assets = client.get(f"/api/projects/{project['id']}/assets").json()["assets"]
        visible_group = next(item for item in listed_assets if item["variable"] == "{{crowd-child}}")
        assert visible_group["payload"]["目标画像状态"] == "待本地化"
    else:
        assert stored["localizedProfile"]["status"] == "pending"
    response = client.get(f"/api/projects/{project['id']}/assets")
    assert response.status_code == 200
    with main.connect() as conn:
        after_get = conn.execute("SELECT payload_json FROM assets WHERE id=?", (asset_row["id"],)).fetchone()[0]
    assert after_get == before_get
    if expect_localizer:
        changed = client.patch(f"/api/projects/{project['id']}", json={"targetCountry": "加拿大", "targetLanguage": "英语"})
        assert changed.status_code == 200, changed.text
        stale_assets = client.get(f"/api/projects/{project['id']}/assets").json()["assets"]
        assert stale_assets[0]["payload"]["目标画像状态"] == "待本地化"
        assert stale_assets[0]["payload"]["自动编译资产图生成提示词"] == "目标画像待本地化；当前旧资产共享描述不能作为目标提示词。"
        assert "墨西哥裔美国男性" not in stale_assets[0]["payload"]["当前有效外观描述"]
        with main.connect() as conn:
            conn.execute("DELETE FROM assets WHERE project_id=? AND episode_id=?", (project["id"], episode_id))
            cached_task = main.run_asset_extraction(conn, main.get_project_or_404(conn, project["id"]), episode_id)
        assert cached_task["status"] == "成功"
        assert [item["stage"] for item in captured] == ["source", "target", "target", "target"]
        cached_target_input = json.loads(captured[-1]["user_content"])
        assert cached_target_input["targetCountry"] == "加拿大"
        assert cached_task["result"]["cache_hit"] is True


def test_source_only_snapshot_recursively_strips_target_and_legacy_profile_values():
    source_structure = valid_role_visual_structure()
    source_structure["基础设定"]["国家 / 时代 / 朝代"] = "现代中国"
    source_structure["基础设定"]["族裔外貌"] = "原片观察：东亚裔中国男性"
    source_structure["脸部特征"].pop("目标族裔背景", None)
    asset = {
        "id": "asset-1", "name": "原剧林浩", "asset_type": "角色", "variable": "{{角色1}}",
        "localized_name": "target name",
        "payload": {
            "原剧核心形象描述": "source core",
            "角色资产图结构化描述": {"脸部特征": {"脸型": "legacy target face"}},
            "原剧角色资产图结构化描述": source_structure,
            "localizedProfile": {"structuredDescription": {"基础设定": {"族裔外貌": "target ethnicity"}}},
            "当前子资产": {
                "原剧视觉形象描述": "source visual",
                "角色资产图结构化描述": {"发型": {"发色": "legacy target hair"}},
                "原剧角色资产图结构化描述": source_structure,
                "镜像": [{"targetCountry": "美国", "localizedProfile": {"name": "target"}, "sourceFact": "keep"}],
            },
            "nested": {"children": [{"localized_name": "nested target", "原剧证据": "keep"}]},
        },
    }
    snapshot = main.source_only_assets_snapshot([asset])[0]
    serialized = json.dumps(snapshot, ensure_ascii=False)
    assert "legacy target face" not in serialized
    assert "legacy target hair" not in serialized
    assert "target ethnicity" not in serialized
    assert "target name" not in serialized
    assert "targetCountry" not in serialized and "美国" not in serialized
    assert "keep" in serialized and "source core" in serialized
    assert snapshot["payload"]["sourceSnapshot"]["sourceStructuredDescription"]["脸部特征"]["脸型"] == "椭圆脸"
    assert asset["payload"]["localizedProfile"]["structuredDescription"]["基础设定"]["族裔外貌"] == "target ethnicity"


def test_real_legacy_source_fixture_normalizes_ids_scenes_and_review_without_fabricating_fields():
    fixture = json.loads((Path(__file__).parent / "fixtures" / "legacy_source_alias_collisions.json").read_text(encoding="utf-8"))
    assets = main.normalize_assets_from_agent(fixture)
    assert len(assets) == 4
    assert len({asset["variable"] for asset in assets}) == 4
    roles = [asset for asset in assets if asset["asset_type"] == "角色"]
    scenes = [asset for asset in assets if asset["asset_type"] == "场景"]
    assert {asset["variable"] for asset in roles} == {"{{CHAR_003_SUB_01}}", "{{CHAR_004_SUB_01}}"}
    assert roles[0]["payload"]["源变量别名"] == roles[1]["payload"]["源变量别名"]
    assert {asset["variable"] for asset in scenes} == {"{{SCENE_001_SUB_01}}", "{{SCENE_002_SUB_01}}"}
    assert {asset["name"] for asset in scenes} == {"婚礼主舞台及红色LED背景屏", "婚礼红毯通道及宴席区"}
    assert all(asset["payload"]["当前子资产"]["原剧视觉形象描述"] for asset in scenes)
    assert all(asset["payload"]["当前子资产"]["原剧截图时间点候选"] for asset in scenes)
    for asset in roles:
        source = asset["payload"]["sourceSnapshot"]
        structure = source["sourceStructuredDescription"]
        assert structure["基础设定"]["性别"] in {"男", "女"}
        assert structure["服装"]["领型"] == "unknown"  # combined legacy alias is preserved, never misfiled
        assert structure["服装"]["袖型"] == "unknown"
        assert "服装.袖型" in source["needsReview"]
        assert structure["服装"]["纹样"]  # original pattern observation remains intact
    index = main.normalize_asset_occurrence_index(fixture["资产出现片段索引"], assets)
    assert len({entry["出现资产"][0]["资产变量"] for entry in index if entry["出现资产"]}) == 4


def test_source_review_profile_keeps_target_face_but_blocks_automatic_generation_and_duplicate_keys():
    fixture = json.loads((Path(__file__).parent / "fixtures" / "legacy_source_alias_collisions.json").read_text(encoding="utf-8"))
    asset = next(item for item in main.normalize_assets_from_agent(fixture) if item["asset_type"] == "角色")
    complete = valid_role_visual_structure()
    target_patch = {
        "基础设定": {"族裔外貌": "Mexican American"},
        "脸部特征": {**complete["脸部特征"], "目标族裔背景": "Mexican American"},
        "发型": complete["发型"],
    }
    response = {"localizedAssets": [{
        "assetVariable": asset["variable"],
        "localizedProfile": {"localizedName": "Morgan Reed", "structuredDescription": target_patch},
    }]}
    errors = main.merge_localized_profiles([asset], response, "美国", "现代", "英语")
    assert not errors
    profile = asset["payload"]["localizedProfile"]
    assert profile["status"] == "source_review"
    assert profile["structuredDescription"]["基础设定"]["族裔外貌"] == "Mexican American"
    assert profile["structuredDescription"]["服装"]["袖型"] == "unknown"
    assert profile["sourceReviewPaths"]
    assert not main.localized_profile_validation_errors(asset, "美国")
    project = {"remake_mode": "overseas_localization", "target_country": "美国", "target_language": "英语", "target_era": "现代"}
    main.prepare_asset_prompt_fields(asset, project)
    assert asset["payload"]["目标画像状态"] == "待源事实核对"
    assert "源事实待核对" in asset["payload"]["自动编译资产图生成提示词"]
    assert "Mexican American" in asset["payload"]["自动编译资产图生成提示词"]
    duplicated = {"localizedAssets": [response["localizedAssets"][0], response["localizedAssets"][0]]}
    duplicate_errors = main.merge_localized_profiles([asset], duplicated, "美国", "现代", "英语")
    assert any("重复稳定资产变量" in item for item in duplicate_errors)


def test_exact_v1_source_cache_retries_target_stage_and_publishes_source_review(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    project = create_project(
        client, title="legacy-v1-cache-source-review", remakeMode="cg_3d",
        sourceLanguage="中文", targetCountry="美国", targetLanguage="英语",
    )
    episode_id = "episode-legacy-v1-cache"
    with main.connect() as conn:
        conn.execute(
            "INSERT INTO episodes (id,project_id,episode_index,filename,file_path,source_url,ark_file_id,status,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (episode_id, project["id"], 1, "source.mp4", "missing-source.mp4", "", "ark-v1-source", "ready", "2026-01-01T00:00:00Z"),
        )

    fixture = json.loads((Path(__file__).parent / "fixtures" / "legacy_source_alias_collisions.json").read_text(encoding="utf-8"))
    cached_assets = main.normalize_assets_from_agent(fixture)
    cached_asset = next(item for item in cached_assets if item["asset_type"] == "角色")
    legacy_agent = {"mode": "volcengine", "model_name": "legacy-cache-test", "temperature": 0, "output_format": "json", "prompt_file": "asset_source_extraction_v1.md", "prompt": "legacy v1 agent prompt"}
    localized_agent = {**legacy_agent, "prompt_file": "asset_source_extraction.md", "prompt": "current target agent prompt"}
    monkeypatch.setattr(main, "prepare_agent", lambda _key: localized_agent)
    monkeypatch.setattr(main, "load_models_config", lambda: {"volcengine": {"maxOutputTokens": 4096, "thinking": False}})
    monkeypatch.setattr(main, "source_file_sha256", lambda _path: "legacy-v1-video-sha")
    monkeypatch.setattr(main, "capture_asset_original_frames", lambda *_args: {})

    legacy_cache_key = main.asset_source_cache_key(
        "legacy-v1-video-sha", localized_agent, project, [],
        prompt=main.ASSET_SOURCE_LEGACY_V1_PROMPT, pipeline_version="asset-source-snapshot-v1",
    )
    source_snapshots = main.source_only_assets_snapshot(cached_assets)
    with main.connect() as conn:
        source_task = main.create_task_with_status(
            conn, project["id"], episode_id, "asset_extraction", "失败", "旧版目标本地化失败，源阶段完整",
            {
                "sourceStageStatus": "complete", "资产源缓存键": legacy_cache_key,
                "资产源事实快照": source_snapshots, "模型解析输出": fixture,
                "模型原始输出": json.dumps(fixture, ensure_ascii=False),
            },
        )

    localized_assets = []
    for index, asset in enumerate(cached_assets, start=1):
        if asset["asset_type"] == "角色":
            ethnicity = "墨西哥裔美国女性" if index == 1 else "墨西哥裔美国男性"
            target_structure = valid_role_visual_structure()
            target_structure["基础设定"]["族裔外貌"] = ethnicity
            target_structure["脸部特征"]["目标族裔背景"] = ethnicity
            profile = {
                "localizedName": f"Morgan Reed {index}", "localizedChildName": "Arrival outfit",
                "coreDescription": f"一位{ethnicity}，神情坚毅。",
                "visualDescription": f"{ethnicity}，深棕眼睛，整洁短发，穿当代日常服装。",
                "structuredDescription": {
                    "基础设定": {"族裔外貌": ethnicity},
                    "脸部特征": target_structure["脸部特征"],
                    "发型": target_structure["发型"],
                },
            }
        else:
            profile = {
                "localizedName": f"American reception hall {index}",
                "coreDescription": "美国当代宴会厅，红毯通道、舞台与宴席桌形成清晰空间布局。",
                "visualDescription": "明亮的美国当代宴会厅，入口、红毯通道、舞台和宴席桌位置清晰。",
            }
        localized_assets.append({"assetVariable": asset["variable"], "localizedProfile": profile})
    localized_response = {"localizedAssets": localized_assets}
    target_calls: list[dict] = []

    class TargetOnlyProvider:
        def chat_completion(self, **kwargs):
            target_calls.append(kwargs)
            return json.dumps(localized_response, ensure_ascii=False)

        def chat_completion_stream(self, **_kwargs):
            raise AssertionError("exact v1 cache hit must skip source video analysis")

    monkeypatch.setattr(main, "get_model_provider", lambda _config: TargetOnlyProvider())
    with main.connect() as conn:
        task = main.run_asset_extraction(conn, main.get_project_or_404(conn, project["id"]), episode_id)

    assert task["status"] == "成功"
    assert task["result"]["cache_hit"] is True
    assert task["result"]["sourceTaskId"] == source_task["id"]
    assert task["result"]["资产源缓存键"] == main.asset_source_cache_key("legacy-v1-video-sha", localized_agent, project, [])
    assert task["result"]["sourceCacheProvenance"] == {
        "consumedCacheKey": legacy_cache_key, "cacheTaskId": source_task["id"],
        "schemaVersion": "asset-source-snapshot-v1", "renormalizedAs": main.ASSET_SOURCE_SCHEMA_VERSION,
    }
    assert task["result"]["资产处理阶段"]["sourceExtraction"]["cache_hit"] is True
    assert len(task["result"]["sourceReviewAssetVariables"]) == 2
    assert len(target_calls) == 1
    target_input_assets = json.loads(target_calls[0]["user_content"])["assets"]
    assert {asset["sourceSnapshot"]["childId"] for asset in target_input_assets if asset["assetType"] == "角色"} == {"CHAR_003_SUB_01", "CHAR_004_SUB_01"}
    with main.connect() as conn:
        published_assets = conn.execute(
            "SELECT variable,name,asset_type,payload_json FROM assets WHERE project_id=? AND episode_id=? ORDER BY asset_type,name",
            (project["id"], episode_id),
        ).fetchall()
    assert len(published_assets) == 4
    assert len({row["variable"] for row in published_assets}) == 4
    assert {row["name"] for row in published_assets if row["asset_type"] == "场景"} == {"婚礼主舞台及红色LED背景屏", "婚礼红毯通道及宴席区"}
    published_roles = [json.loads(row["payload_json"]) for row in published_assets if row["asset_type"] == "角色"]
    assert all(payload["localizedProfile"]["status"] == "source_review" for payload in published_roles)
    assert all(payload["localizedProfile"]["structuredDescription"]["脸部特征"]["目标族裔背景"] for payload in published_roles)
    assert all(payload["localizedProfile"]["structuredDescription"]["服装"]["袖型"] == "unknown" for payload in published_roles)
    visible_assets = client.get(f"/api/projects/{project['id']}/assets").json()["assets"]
    visible_asset = next(item for item in visible_assets if item["variable"] == cached_asset["variable"])
    assert visible_asset["payload"]["目标画像状态"] == "待源事实核对"


def test_legacy_alias_adapter_preserves_canonical_values_and_unresolved_observations():
    structure = {
        "基础设定": {"性别": "女", "性别及判断依据": "男，字幕称谓", "视觉年龄": "30岁"},
        "服装": {
            "外层": "已确认的蓝色外套", "内中外层": "内层白衬衫+中层灰马甲+外层蓝色外套",
            "领型": "圆领", "领袖": "旧合并领袖观察", "袖型": "无袖", "纹样": "卷草纹",
            "鞋面可见性": "被裙摆遮挡不可见", "摆型": "needsReview",
        },
        "固定穿戴配饰": ["红色胸花"],
    }
    normalized, review = main.normalize_legacy_role_source_structure(structure)
    assert normalized["基础设定"]["性别"] == "女"
    assert normalized["服装"]["外层"] == "已确认的蓝色外套"
    assert normalized["服装"]["领型"] == "圆领"
    assert normalized["服装"]["袖型"] == "无袖"
    assert normalized["服装"]["纹样"] == "卷草纹"
    assert normalized["服装"]["纹样位置"] == "unknown"
    assert normalized["服装"]["鞋面可见性"] == "被裙摆遮挡不可见"
    assert normalized["固定穿戴配饰"] == ["红色胸花"]
    assert normalized["sourceAliasObservations"]["服装.内中外层"] == structure["服装"]["内中外层"]
    assert "服装.鞋面可见性" not in review


def test_source_review_get_post_auto_draft_blocks_provider_but_manual_complete_prompt_keeps_cg_suffix(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    data_dir = Path(__file__).parent / f".source-review-test-{uuid.uuid4().hex}"
    data_dir.mkdir(parents=True)
    request.addfinalizer(lambda: shutil.rmtree(data_dir, ignore_errors=True))
    monkeypatch.setattr(main, "DATA_DIR", data_dir)
    monkeypatch.setattr(main, "DB_PATH", data_dir / "test.sqlite3")
    monkeypatch.setattr(main, "PROJECTS_DIR", data_dir / "projects")
    main.init_db()
    client = TestClient(main.app)
    project = create_project(client, title="source-review-manual-override", remakeMode="cg_3d", sourceLanguage="中文", targetCountry="美国", targetLanguage="英语", storyboardVisualStyle="cg_3d")
    fixture = json.loads((Path(__file__).parent / "fixtures" / "legacy_source_alias_collisions.json").read_text(encoding="utf-8"))
    asset = next(item for item in main.normalize_assets_from_agent(fixture) if item["asset_type"] == "角色")
    complete = valid_role_visual_structure()
    target_patch = {
        "基础设定": {"族裔外貌": "Mexican American"},
        "脸部特征": {**complete["脸部特征"], "目标族裔背景": "Mexican American"},
        "发型": complete["发型"],
    }
    main.merge_localized_profiles([asset], {"localizedAssets": [{
        "assetVariable": asset["variable"], "localizedProfile": {"localizedName": "Morgan Reed", "structuredDescription": target_patch},
    }]}, "美国", "现代", "英语")
    with main.connect() as conn:
        episode_id = "episode-source-review"
        conn.execute("INSERT INTO episodes (id,project_id,episode_index,filename,file_path,status,created_at) VALUES (?,?,?,?,?,?,?)", (episode_id, project["id"], 1, "source.mp4", "C:/test/source.mp4", "已上传", "2026-01-01T00:00:00Z"))
        main.insert_assets(conn, project["id"], episode_id, [asset])
        asset_id = conn.execute("SELECT id FROM assets WHERE project_id=?", (project["id"],)).fetchone()[0]
    observed: list[str] = []

    class ProviderSpy:
        def generate_image(self, **kwargs):
            observed.append(kwargs["prompt"])
            image = io.BytesIO()
            Image.new("RGB", (4, 4), (20, 30, 40)).save(image, format="PNG")
            return {"image_bytes": image.getvalue(), "mime_type": "image/png", "size": kwargs["size"], "model": "provider-spy", "raw": {}}

    monkeypatch.setattr(main, "prepare_agent", lambda _key: {"mode": "volcengine", "model_name": "provider-spy", "prompt": "", "prompt_file": "test"})
    monkeypatch.setattr(main, "load_models_config", lambda: {"volcengine": {"imageSize": "2K"}})
    monkeypatch.setattr(main, "get_model_provider", lambda _config: ProviderSpy())
    loaded = client.get(f"/api/projects/{project['id']}/assets")
    assert loaded.status_code == 200, loaded.text
    loaded_asset = next(item for item in loaded.json()["assets"] if item["id"] == asset_id)
    auto = loaded_asset["payload"]["最终资产图生成提示词"]
    assert "源事实待核对" in auto
    blocked = client.post(f"/api/projects/{project['id']}/assets/{asset_id}/generate-image", json={"finalPrompt": auto})
    assert blocked.status_code == 422
    assert "未解决占位" in blocked.json()["detail"]
    assert observed == []
    manual = "完整自包含角色设定：成年墨西哥裔美国男性，深棕短发，穿合身深蓝西装、白衬衫与长裤，鞋面与袖口清晰设定，白色背景资产展示。"
    accepted = client.post(f"/api/projects/{project['id']}/assets/{asset_id}/generate-image", json={"finalPrompt": manual})
    assert accepted.status_code == 200, accepted.text
    assert len(observed) == 1
    assert observed[0].count(main.CG_ROLE_FIXED_TEMPLATE) == 1


def test_invalid_localized_profile_is_not_marked_complete_or_projected():
    asset = {
        "asset_type": "角色", "variable": "{{role-1}}", "name": "原剧角色", "localized_name": "原名",
        "payload": {"sourceSnapshot": {"sourceStructuredDescription": valid_role_visual_structure()}, "当前子资产": {}},
    }
    broken = valid_role_visual_structure()
    broken["脸部特征"].pop("鼻形")
    errors = main.merge_localized_profiles([asset], {"localizedAssets": [{
        "assetVariable": "{{role-1}}", "localizedProfile": {"structuredDescription": broken},
    }]}, "美国", "当代")
    assert errors
    profile = asset["payload"]["localizedProfile"]
    assert profile["status"] == "invalid"
    assert not profile["structuredDescription"]["脸部特征"].get("鼻形")
    assert main.localized_profile_validation_errors(asset, "美国") == ["localizedProfile缺失"]


def test_western_localizer_rejects_asian_ethnicity_without_source_ethnicity_or_explicit_lock():
    structure = valid_role_visual_structure()
    structure["基础设定"]["族裔外貌"] = "亚裔混血美国女性"
    structure["脸部特征"]["目标族裔背景"] = "亚裔混血美国女性"
    asset = {
        "asset_type": "角色", "variable": "{{bride}}", "payload": {
            "sourceSnapshot": {"coreDescription": "二十多岁女性，黑色盘发，穿婚纱。", "visualDescription": "黑色盘发。", "sourceStructuredDescription": {}},
            "localizedProfile": {"status": "complete", "coreDescription": "亚裔混血美国女性", "visualDescription": "亚裔混血美国女性", "structuredDescription": structure},
        },
    }
    errors = main.localized_profile_validation_errors(asset, "美国")
    assert any("泛亚裔身份" in error for error in errors)
    asset["payload"]["目标生成描述"] = "用户明确指定亚裔混血美国女性"
    asset["payload"]["targetVisualLockProvenance"] = "user_override"
    assert not main.localized_profile_validation_errors(asset, "美国")


def test_profile_context_change_blocks_old_target_and_domestic_projection_clears_legacy_face():
    payload = {
        "sourceSnapshot": {"coreDescription": "原剧人物", "sourceStructuredDescription": {}},
        "localizedProfile": {"status": "complete", "coreDescription": "美国目标脸", "visualDescription": "美国目标脸", "structuredDescription": valid_role_visual_structure(),
                             "targetContext": {"targetCountry": "美国", "targetEra": "现代", "targetLanguage": "英语", "sourceHash": "old-hash"}},
        "角色资产图结构化描述": valid_role_visual_structure(),
        "本地化核心形象描述": "旧目标脸",
        "当前子资产": {"角色资产图结构化描述": valid_role_visual_structure(), "本地化视觉形象描述": "旧目标脸"},
    }
    project = {"remake_mode": "overseas_localization", "target_country": "加拿大", "target_language": "英语", "target_era": "现代"}
    projected = main.project_asset_profile_to_legacy(payload, project)
    assert "角色资产图结构化描述" not in projected
    assert "本地化核心形象描述" not in projected
    assert "角色资产图结构化描述" not in projected["当前子资产"]
    domestic = main.project_asset_profile_to_legacy(payload, {"remake_mode": "overseas_localization", "target_country": "中国"})
    assert "角色资产图结构化描述" not in domestic
    assert "角色资产图结构化描述" not in domestic["当前子资产"]


def test_failed_localization_persists_source_stage_and_retry_reuses_it(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    project = create_project(client, title="localization-retry-source-cache", remakeMode="cg_3d", sourceLanguage="中文", targetCountry="美国", targetLanguage="英语")
    episode_id = "episode-localization-retry"
    with main.connect() as conn:
        conn.execute("INSERT INTO episodes (id,project_id,episode_index,filename,file_path,source_url,ark_file_id,status,created_at) VALUES (?,?,?,?,?,?,?,?,?)", (episode_id, project["id"], 1, "sample.mp4", "missing-local.mp4", "", "ark-source-id", "ready", "2026-01-01T00:00:00Z"))
        conn.execute("INSERT INTO assets (id,project_id,episode_id,asset_type,name,localized_name,variable,status,payload_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)", ("existing-asset", project["id"], episode_id, "角色", "既有角色", "", "{{existing}}", "已确认", json.dumps({"原剧核心形象描述": "既有源事实"}, ensure_ascii=False), "2026-01-01T00:00:00Z"))
    source_structure = valid_role_visual_structure()
    source_structure["基础设定"]["族裔外貌"] = "原片观察：东亚裔中国男性"
    source_structure["基础设定"]["国家 / 时代 / 朝代"] = "现代中国"
    source_child = {
        "子资产编号": "retry-role-child", "角色资产变量": "{{retry-role-child}}", "原剧子资产名称": "原片常服",
        "原剧视觉形象描述": "原片观察：东亚裔中国男性，黑色短发。", "原剧角色资产图结构化描述": source_structure,
    }
    source_response = {"资产库": {"角色资产": [{
        "主资产编号": "retry-role", "原剧角色名称": "林浩", "剧情身份": "新郎",
        "原剧核心形象描述": "原片观察：现代中国婚礼中的东亚裔男性。", "原剧角色资产图结构化描述": source_structure,
        "子资产": [source_child],
    }]}, "资产出现片段索引": []}
    target_structure = valid_role_visual_structure()
    target_structure["基础设定"]["族裔外貌"] = "墨西哥裔美国男性"
    target_structure["脸部特征"]["目标族裔背景"] = "墨西哥裔美国男性"
    target_patch = {
        "基础设定": {"族裔外貌": "墨西哥裔美国男性"},
        "脸部特征": target_structure["脸部特征"],
        "发型": target_structure["发型"],
    }
    localized_response = {"localizedAssets": [{"assetVariable": "{{retry-role-child}}", "localizedProfile": {
        "localizedName": "Alex", "localizedChildName": "Wedding attire", "structuredDescription": target_patch,
    }}]}
    invalid_response = json.loads(json.dumps(localized_response, ensure_ascii=False))
    invalid_response["localizedAssets"][0]["localizedProfile"]["structuredDescription"]["脸部特征"].pop("鼻形")
    raw_target_results = [json.dumps(invalid_response, ensure_ascii=False), json.dumps(invalid_response, ensure_ascii=False), json.dumps(localized_response, ensure_ascii=False)]
    target_calls: list[dict] = []
    source_calls = 0

    class SourceProvider:
        def chat_completion_stream(self, **kwargs):
            nonlocal source_calls
            source_calls += 1
            yield "response.output_text.delta", {"delta": json.dumps(source_response, ensure_ascii=False)}
            yield "response.completed", {}

    def localized_call(agent, input_payload, compact_prompt):
        target_calls.append(input_payload)
        return raw_target_results[len(target_calls) - 1]

    monkeypatch.setattr(main, "prepare_agent", lambda _key: {"mode": "volcengine", "model_name": "stage-spy", "prompt": "legacy", "prompt_file": "legacy", "temperature": 0, "output_format": "json"})
    monkeypatch.setattr(main, "get_model_provider", lambda _config: SourceProvider())
    monkeypatch.setattr(main, "load_models_config", lambda: {"volcengine": {"maxOutputTokens": 4096}})
    monkeypatch.setattr(main, "source_file_sha256", lambda _path: "same-retry-source-sha")
    monkeypatch.setattr(main, "capture_asset_original_frames", lambda *_args: {})
    monkeypatch.setattr(main, "call_compact_video_json", localized_call)
    original_parse_json = main.parse_json_object
    original_source_parse = main.parse_json_object_with_repair

    def assert_target_raw_archived(raw, default):
        audit_files = list((main.DATA_DIR / "asset-localization-audit").glob("target-localization*.json"))
        assert any(json.loads(path.read_text(encoding="utf-8"))["raw"] == raw for path in audit_files)
        return original_parse_json(raw, default)

    def assert_source_raw_archived(raw):
        audit_files = list((main.DATA_DIR / "asset-localization-audit").glob("source-extraction*.json"))
        assert any(json.loads(path.read_text(encoding="utf-8"))["raw"] == raw for path in audit_files)
        return original_source_parse(raw)

    monkeypatch.setattr(main, "parse_json_object", assert_target_raw_archived)
    monkeypatch.setattr(main, "parse_json_object_with_repair", assert_source_raw_archived)

    with main.connect() as conn:
        failed = main.run_asset_extraction(conn, main.get_project_or_404(conn, project["id"]), episode_id)
    assert failed["status"] == "失败"
    assert source_calls == 1 and len(target_calls) == 2
    assert failed["result"]["sourceStageStatus"] == "complete"
    assert len(failed["result"]["资产源事实快照"]) == 1
    assert len(failed["result"]["资产本地化原始尝试"]) == 2
    assert Path(failed["result"]["模型原始输出审计路径"]).is_file()
    assert all(Path(attempt["auditPath"]).is_file() for attempt in failed["result"]["资产本地化原始尝试"])
    with main.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM assets WHERE project_id=? AND episode_id=?", (project["id"], episode_id)).fetchone()[0] == 1
        retried = main.run_asset_extraction(conn, main.get_project_or_404(conn, project["id"]), episode_id)
    assert retried["status"] == "成功"
    assert source_calls == 1 and len(target_calls) == 3
    assert retried["result"]["cache_hit"] is True
    assert retried["result"]["sourceTaskId"] == failed["id"]
    assert target_calls[-1]["assets"][0]["sourceSnapshot"]["coreDescription"]


def test_extraction_normalize_detail_merge_to_image_provider_keeps_source_and_fills_target_only(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    project = create_project(client, title="v3-extraction-detail-provider", remakeMode="overseas_localization", sourceLanguage="中文", targetCountry="美国", targetLanguage="英语", storyboardVisualStyle="cg_3d")
    observed_structure = valid_role_visual_structure(服装={"摆与地面关系": "根据原片确认下摆是否接地"})
    extracted_child = {
        "子资产编号": "角色_007_子资产_001", "角色资产变量": "{{角色_007_子资产_001}}",
        "原剧子资产名称": "原角色常服", "本地化子资产名称": "Jane daily outfit", "差异类型": "日常服装",
        "原剧视觉形象描述": "视频观察：黑色外袍、短发。", "本地化视觉形象描述": "根据原片补全服装下摆与脚部关系。",
        "角色资产图结构化描述": observed_structure,
        "视觉事实来源": {"视频可见": ["黑色外袍", "短发"], "剧情已知": ["男性称谓"], "推断补全": [], "needsReview": []},
        "固定穿戴配饰": ["金属腰封"], "手持或悬挂物档案": ["团扇"],
    }
    extracted = {"资产库": {"角色资产": [{
        "主资产编号": "角色_007", "原剧角色名称": "源角色", "本地化角色名称": "Jane",
        "剧情身份": "主角", "原剧核心形象描述": "源事实：黑色外袍、短发。", "子资产": [extracted_child],
    }]}}
    assets = main.normalize_assets_from_agent(extracted)
    assert len(assets) == 1
    asset = assets[0]
    completed_structure = valid_role_visual_structure(服装={"摆与地面关系": "长裙前摆接近地面但不遮鞋"})
    merged = main.merge_asset_detail_completion([asset], {"资产补全": [{
        "资产变量": asset["variable"],
        "current_child_patch": {
            "本地化视觉形象描述": "30岁男性，黑色外袍、短发，前摆接近地面且露出鞋面。",
            "角色资产图结构化描述": completed_structure,
        },
    }]}, "美国", "英语")
    assert merged == 1
    payload = asset["payload"]
    child = payload["当前子资产"]
    assert payload["原剧核心形象描述"] == "源事实：黑色外袍、短发。"
    assert child["视觉事实来源"]["视频可见"] == ["黑色外袍", "短发"]
    assert child["角色资产图结构化描述"]["服装"]["摆与地面关系"] == "长裙前摆接近地面但不遮鞋"
    assert child["固定穿戴配饰"] == ["金属腰封"]
    payload["localizedProfile"] = {
        "schemaVersion": main.ASSET_LOCALIZATION_SCHEMA_VERSION, "status": "complete",
        "localizedName": "Jane", "localizedChildName": "Jane daily outfit",
        "coreDescription": "30岁墨西哥裔美国男性，黑色短发，穿蓝色棉质外套。",
        "visualDescription": child["本地化视觉形象描述"],
        "structuredDescription": completed_structure, "assetImagePrompt": "",
    }
    payload["localizedProfile"]["targetContext"] = main.localized_profile_target_context(payload, project)

    asset_id, _ = insert_source_records(project, selected_image=False)
    with main.connect() as conn:
        conn.execute("UPDATE assets SET payload_json=? WHERE id=?", (json.dumps(payload, ensure_ascii=False), asset_id))
    captured: dict = {}

    class ProviderSpy:
        def generate_image(self, **kwargs):
            captured.update(kwargs)
            image = io.BytesIO()
            Image.new("RGB", (8, 8), (20, 50, 80)).save(image, format="PNG")
            return {"image_bytes": image.getvalue(), "mime_type": "image/png", "size": kwargs["size"], "model": "provider-spy", "raw": {}}

    monkeypatch.setattr(main, "prepare_agent", lambda _key: {"mode": "volcengine", "model_name": "provider-spy", "prompt": "", "prompt_file": "test"})
    monkeypatch.setattr(main, "load_models_config", lambda: {"volcengine": {"imageSize": "2K", "imageWatermark": False}})
    monkeypatch.setattr(main, "get_model_provider", lambda _config: ProviderSpy())
    response = client.post(f"/api/projects/{project['id']}/assets/{asset_id}/generate-image", json={})
    assert response.status_code == 200, response.text
    final_prompt = response.json()["asset"]["images"][0]["prompt"]
    assert captured["prompt"] == final_prompt
    assert captured["watermark"] is False
    assert "根据原片" not in final_prompt
    assert "手持团扇" not in final_prompt
    assert "金属腰封" in final_prompt
    assert "长裙前摆接近地面但不遮鞋" in final_prompt


def test_storyboard_preserves_raw_source_hash_and_narrative_layout_words(client: TestClient):
    project = create_project(client, title="storyboard-source-preservation", remakeMode="cg_3d", sourceLanguage="中文")
    insert_source_records(project, selected_image=False)
    source = "角色看向墙上的胸像，镜头拉远到白色背景墙；旁边挂着四视图照片和全身站姿海报。"
    with main.connect() as conn:
        conn.execute("UPDATE storyboards SET prompt=? WHERE project_id=?", (source, project["id"]))

    first = client.get(f"/api/projects/{project['id']}/storyboards").json()["storyboards"][0]
    stored_source = first["payload"]["源分镜文本"]
    assert hashlib.sha256(stored_source.encode("utf-8")).hexdigest() == hashlib.sha256(source.encode("utf-8")).hexdigest()
    for phrase in ("墙上的胸像", "白色背景墙", "四视图照片", "全身站姿海报"):
        assert phrase in first["prompt"]
    second = client.get(f"/api/projects/{project['id']}/storyboards").json()["storyboards"][0]
    assert second["payload"]["源分镜文本"] == source
    assert second["prompt"] == first["prompt"]


def test_effective_asset_source_ignores_historical_candidates_when_reusing_original(client: TestClient, tmp_path: Path):
    project = create_project(client, title="effective-asset-source", remakeMode="asset_replacement", sourceLanguage="中文", replaceCharacters=True)
    asset_id, _ = insert_source_records(project, selected_image=False)
    source_path = tmp_path / "source-character.png"
    Image.new("RGB", (3, 3), (60, 70, 80)).save(source_path, format="PNG")
    with main.connect() as conn:
        row = conn.execute("SELECT payload_json FROM assets WHERE id=?", (asset_id,)).fetchone()
        asset_payload = json.loads(row[0])
        asset_payload["原资产图路径"] = str(source_path)
        conn.execute("UPDATE assets SET payload_json=? WHERE id=?", (json.dumps(asset_payload, ensure_ascii=False), asset_id))

    target_desc = "蓝色卡通猫，穿黄色雨衣。"
    updated = client.patch(
        f"/api/projects/{project['id']}/assets/{asset_id}",
        json={"payload": {"目标生成描述": target_desc}},
    )
    assert updated.status_code == 200, updated.text
    image_bytes = io.BytesIO()
    Image.new("RGB", (4, 4), (10, 20, 30)).save(image_bytes, format="PNG")
    uploaded = client.post(
        f"/api/projects/{project['id']}/assets/{asset_id}/upload-image",
        files={"file": ("target.png", image_bytes.getvalue(), "image/png")},
    )
    assert uploaded.status_code == 200, uploaded.text
    target_image = uploaded.json()["asset"]["images"][0]
    board_url = f"/api/projects/{project['id']}/storyboards"
    target_board = client.get(board_url).json()["storyboards"][0]
    target_map = target_board["payload"]["目标资产映射快照"][0]
    assert target_map["effectiveAssetSource"] == "target"
    assert target_map["imageId"] == target_image["id"]
    assert target_map["targetDescription"] == target_desc

    # Turning the per-type switch off forces the source image and source facts,
    # even while a historical target candidate remains selected in storage.
    switched_off = client.patch(f"/api/projects/{project['id']}", json={"replaceCharacters": False})
    assert switched_off.status_code == 200, switched_off.text
    source_board = client.get(board_url).json()["storyboards"][0]
    source_map = source_board["payload"]["目标资产映射快照"][0]
    assert source_map["effectiveAssetSource"] == "source"
    assert source_map["imagePath"] == str(source_path)
    assert "30岁男性" in source_map["targetDescription"]
    asset_response = client.get(f"/api/projects/{project['id']}/assets").json()["assets"][0]
    assert asset_response["payload"]["effectiveAssetSource"] == "source"
    assert asset_response["payload"]["当前有效外观描述"] == source_map["targetDescription"]

    switched_on = client.patch(f"/api/projects/{project['id']}", json={"replaceCharacters": True})
    assert switched_on.status_code == 200, switched_on.text
    target_again = client.get(board_url).json()["storyboards"][0]["payload"]["目标资产映射快照"][0]
    assert target_again["effectiveAssetSource"] == "target"
    assert target_again["imageId"] == target_image["id"]

    restored = client.post(f"/api/projects/{project['id']}/assets/{asset_id}/restore-original")
    assert restored.status_code == 200, restored.text
    assert restored.json()["asset"]["payload"]["effectiveAssetSource"] == "source"
    assert len(restored.json()["asset"]["images"]) == 1
    assert restored.json()["asset"]["images"][0]["is_selected"] == 0
    blocked_restore_generation = client.post(f"/api/projects/{project['id']}/assets/{asset_id}/generate-image", json={})
    assert blocked_restore_generation.status_code == 409
    assert "沿用原貌" in blocked_restore_generation.json()["detail"]
    restored_board = client.get(board_url).json()["storyboards"][0]["payload"]["目标资产映射快照"][0]
    assert restored_board["effectiveAssetSource"] == "source"
    assert restored_board["imagePath"] == str(source_path)
    assert "30岁男性" in restored_board["targetDescription"]

    with main.connect() as conn:
        row = conn.execute("SELECT payload_json FROM assets WHERE id=?", (asset_id,)).fetchone()
        missing_source_payload = json.loads(row[0])
        missing_source_payload["原资产图路径"] = ""
        conn.execute("UPDATE assets SET payload_json=? WHERE id=?", (json.dumps(missing_source_payload, ensure_ascii=False), asset_id))
    missing_source_asset = client.get(f"/api/projects/{project['id']}/assets").json()["assets"][0]
    assert missing_source_asset["payload"]["effectiveAssetSource"] == "source"
    assert missing_source_asset["payload"]["当前有效资产就绪"] is False
    missing_source_board = client.get(board_url).json()["storyboards"][0]
    missing_source_map = missing_source_board["payload"]["目标资产映射快照"][0]
    assert missing_source_map["imagePath"] == ""
    assert "缺少原片资产图" in missing_source_board["prompt"]


def test_upload_decode_ownership_and_existing_selection_protection(client: TestClient):
    project = create_project(client, title="upload-test", remakeMode="asset_replacement", sourceLanguage="中文", replaceCharacters=True)
    asset_id, _ = insert_source_records(project, selected_image=False)
    valid_image = io.BytesIO()
    Image.new("RGB", (2, 2), (12, 34, 56)).save(valid_image, format="PNG")
    upload = client.post(
        f"/api/projects/{project['id']}/assets/{asset_id}/upload-image",
        files={"file": ("tiny.png", valid_image.getvalue(), "image/png")},
    )
    assert upload.status_code == 200, upload.text
    chosen = upload.json()["asset"]["images"][0]
    assert chosen["is_selected"] == 1
    assert chosen["payload"]["source"] == "user_upload"
    assert Path(chosen["file_path"]).is_file()
    board = client.get(f"/api/projects/{project['id']}/storyboards").json()["storyboards"][0]
    assert "以当前已确认目标图为外观权威" in board["prompt"]
    assert "30岁男性" not in board["prompt"]

    invalid = client.post(
        f"/api/projects/{project['id']}/assets/{asset_id}/upload-image",
        files={"file": ("fake.png", b"\x89PNG\r\n\x1a\nnot-an-image", "image/png")},
    )
    assert invalid.status_code == 415
    with main.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM asset_images WHERE project_id=? AND is_selected=1", (project["id"],)).fetchone()[0] == 1
        selected_id = conn.execute("SELECT id FROM asset_images WHERE project_id=? AND is_selected=1", (project["id"],)).fetchone()[0]
    foreign = create_project(client, title="foreign")
    rejected = client.post(f"/api/projects/{foreign['id']}/assets/{asset_id}/select-image", json={"imageId": selected_id})
    assert rejected.status_code == 404


def test_mode_update_rejects_existing_outputs_and_running_jobs(client: TestClient):
    project = create_project(client, title="mode-lock")
    asset_id, _ = insert_source_records(project, selected_image=True)
    rejected = client.patch(f"/api/projects/{project['id']}", json={"remakeMode": "cg_3d"})
    assert rejected.status_code == 409
    with main.connect() as conn:
        conn.execute("DELETE FROM asset_images WHERE project_id=?", (project["id"],))
        conn.execute(
            "INSERT INTO asset_generation_jobs (id,project_id,asset_id,remake_mode,template_version,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
            ("job_running", project["id"], asset_id, "overseas_localization", "legacy-v1", "running", "now", "now"),
        )
    rejected_inflight = client.patch(f"/api/projects/{project['id']}", json={"remakeMode": "cg_3d"})
    assert rejected_inflight.status_code == 409


def test_generation_preparation_failure_preserves_adopted_image_and_unlocks_mode(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    project = create_project(client, title="provider-failure", remakeMode="cg_3d", sourceLanguage="中文")
    asset_id, selected_id = insert_source_records(project, selected_image=True)
    prepare_valid_role_asset(asset_id)

    def fail_prepare(_key: str):
        raise main.ProviderError("test provider unavailable")

    monkeypatch.setattr(main, "prepare_agent", fail_prepare)
    failed = client.post(f"/api/projects/{project['id']}/assets/{asset_id}/generate-image", json={})
    assert failed.status_code == 400
    with main.connect() as conn:
        job = conn.execute("SELECT status FROM asset_generation_jobs WHERE project_id=? ORDER BY created_at DESC LIMIT 1", (project["id"],)).fetchone()
        selected = conn.execute("SELECT id FROM asset_images WHERE project_id=? AND asset_id=? AND is_selected=1", (project["id"], asset_id)).fetchone()
        assert job["status"] == "failed"
        assert selected["id"] == selected_id
    unlocked = client.patch(f"/api/projects/{project['id']}", json={"sourceLanguage": "中文（确认）"})
    assert unlocked.status_code == 200


def test_legacy_database_migrates_projects_to_overseas_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_dir = tmp_path / "legacy-data"
    data_dir.mkdir()
    db_path = data_dir / "legacy.sqlite3"
    import sqlite3

    with sqlite3.connect(db_path) as legacy:
        legacy.execute("CREATE TABLE projects (id TEXT PRIMARY KEY,title TEXT NOT NULL,target_country TEXT NOT NULL,target_language TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)")
        legacy.execute("INSERT INTO projects VALUES ('old-1','旧项目','美国','英语','旧项目状态','now','now')")
    monkeypatch.setattr(main, "DATA_DIR", data_dir)
    monkeypatch.setattr(main, "DB_PATH", db_path)
    monkeypatch.setattr(main, "PROJECTS_DIR", data_dir / "projects")
    main.init_db()
    with main.connect() as conn:
        migrated = dict(conn.execute("SELECT * FROM projects WHERE id='old-1'").fetchone())
    assert migrated["remake_mode"] == "overseas_localization"
    assert migrated["storyboard_visual_style"] == "live_action"
    assert migrated["source_language"] == ""


def test_domestic_analysis_allows_unknown_source_language(client: TestClient):
    project = create_project(client, title="source-language-required", remakeMode="cg_3d", sourceLanguage="")
    insert_source_records(project, selected_image=False)
    episode_id = "episode_source_1"
    started = client.post(
        f"/api/projects/{project['id']}/tasks/run",
        json={"taskType": "asset_extraction", "episodeId": episode_id},
    )
    assert started.status_code == 200
    tasks = client.get(f"/api/projects/{project['id']}/tasks").json()["tasks"]
    task = next(item for item in tasks if item["id"] == started.json()["task"]["id"])
    assert task["status"] == "成功", task["message"]
    agent = {"prompt": ""}
    main.route_project_agent_prompt(agent, {"remake_mode": "cg_3d", "source_language": ""})
    assert "具体语言未知时也保持原片语言，不翻译" in agent["prompt"]
    assert "不得将本提示当作语言识别结果" in agent["prompt"]


def test_domestic_storyboard_keeps_english_source_language_without_forbidding_english(client: TestClient):
    project = create_project(client, title="english-source-language", remakeMode="asset_replacement", sourceLanguage="English")
    insert_source_records(project, selected_image=False)
    storyboard = client.get(f"/api/projects/{project['id']}/storyboards").json()["storyboards"][0]
    assert "沿用原片语言（English）" in storyboard["prompt"]
    assert "不得注入目标国家、英语" not in storyboard["prompt"]
    assert "不进行未经选择的翻译或地域本土化" in storyboard["prompt"]


def test_derive_shares_source_file_paths_without_project_delete_endpoint(client: TestClient):
    parent = create_project(client, title="shared-source-parent")
    parent_dir = main.PROJECTS_DIR / parent["id"]
    source_video = parent_dir / "source" / "source.mp4"
    source_frame = parent_dir / "assets" / "original_frames" / "frame.png"
    source_video.parent.mkdir(parents=True, exist_ok=True)
    source_frame.parent.mkdir(parents=True, exist_ok=True)
    source_video.write_bytes(b"test-source-video")
    Image.new("RGB", (2, 2), (1, 2, 3)).save(source_frame)
    with main.connect() as conn:
        conn.execute("INSERT INTO episodes (id,project_id,episode_index,filename,file_path,status,created_at) VALUES (?,?,?,?,?,?,?)",
                     ("episode_shared", parent["id"], 1, "source.mp4", str(source_video), "已上传", "now"))
        payload = {"原剧视觉形象描述": "原片角色", "原资产图路径": str(source_frame), "源AssetKey": "shared-key"}
        conn.execute("INSERT INTO assets (id,project_id,episode_id,asset_type,name,localized_name,variable,status,payload_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     ("asset_shared", parent["id"], "episode_shared", "角色", "角色", "角色", "{{role}}", "已确认", json.dumps(payload, ensure_ascii=False), "now"))
    derived = client.post(f"/api/projects/{parent['id']}/derive", json={"title": "shared-source-child", "remakeMode": "cg_3d", "sourceLanguage": "中文"}).json()["project"]
    with main.connect() as conn:
        episode = conn.execute("SELECT file_path FROM episodes WHERE project_id=?", (derived["id"],)).fetchone()
        asset = conn.execute("SELECT payload_json FROM assets WHERE project_id=?", (derived["id"],)).fetchone()
        assert episode["file_path"] == str(source_video)
        assert json.loads(asset["payload_json"])["原资产图路径"] == str(source_frame)
        assert conn.execute("SELECT COUNT(*) FROM asset_images WHERE project_id=?", (derived["id"],)).fetchone()[0] == 0
    assert source_video.is_file() and source_frame.is_file()
    assert client.delete(f"/api/projects/{parent['id']}").status_code == 405
    assert source_video.is_file() and source_frame.is_file()


def test_storyboard_structured_contract_preserves_root_summary_and_mixed_legacy_shots():
    config_prompt = main.load_agent_config("storyboardPromptGeneration")["prompt"]
    assert "一个分镜”以视频里实际连续镜头为单位" in config_prompt
    assert "机位与构图" in config_prompt and "人物站位" in config_prompt and "台词与声音" in config_prompt
    assert "仅在输入明确提供白模素材时启用" in config_prompt
    assert "以下样例仅展示字段排列与格式" in config_prompt
    assert "海外本土化项目才按目标国家/语言适配；国内重制、3DCG和资产替换按输入保留源剧情/源语言" in config_prompt
    assert "仅当项目模式是`overseas_localization`且目标语言明确不是中文" in config_prompt

    root_summary = {
        "场景": "@夜间办公室",
        "分镜数量": 2,
        "目标风格": "沿用项目已确认风格",
        "参考素材分工": "视频提供动作事实，资产变量提供外观",
        "成片语言与字幕": "保留可辨原话；不加后期字幕",
        "共用要求": "不添加画面中没有的物件",
    }
    parsed = {
        "片段说明": root_summary,
        "分镜列表": [
            {
                "分镜编号": "分镜_001",
                "分镜时间范围": {"开始时间": "00:00:01.0", "结束时间": "00:00:02.5"},
                "引用资产变量": ["@林浩-常服"],
                "镜头列表": [{
                    "时间范围": {"开始时间": "00:00:01.0", "结束时间": "00:00:02.5"},
                    "时长": "1.5秒",
                    "机位与构图": {"景别": "中近景", "主体构图": "@林浩-常服位于画面左侧"},
                    "背景": {"可见环境": "办公室门口"},
                    "人物站位": [{"资产变量": "@林浩-常服", "画面左右": "左侧"}],
                    "动作过程": {"起始": "站立", "变化": "转头", "结束": "看向门外"},
                    "运镜": {"方式": "固定", "方向": "无"},
                    "台词与声音": [{"说话者": "林浩", "原话": "等等", "画内外": "画内"}],
                }],
            },
            {
                "分镜编号": "分镜_002",
                "分镜时间范围": {"开始时间": "00:00:02.5", "结束时间": "00:00:04.0"},
                "引用资产变量": ["@林浩-常服"],
                "镜头列表": [
                    {"时间范围": "00:00:02.5-00:00:04.0", "镜头描述": "@林浩-常服向门外走出一步。"},
                    {"机位与构图": "", "镜头描述": "旧记录的第二条具体描述仍保留。"},
                ],
            },
        ],
    }
    normalized = main.normalize_storyboards_from_agent(parsed)
    assert len(normalized) == 2
    assert all(board["payload"]["片段说明"] == root_summary for board in normalized)

    first = normalized[0]
    first_payload = first["payload"]
    first_prompt = main.format_storyboard_prompt(first["time_range"], first_payload, first["prompt"], ["@林浩-常服"], [])
    assert first_prompt.count("片段说明：") == 1
    assert "1.5秒" in first_prompt
    assert "[机位与构图]" in first_prompt and "[人物站位]" in first_prompt and "[台词与声音]" in first_prompt
    assert "\\n" not in first_prompt

    second = normalized[1]
    second_prompt = main.format_storyboard_prompt(second["time_range"], second["payload"], second["prompt"], ["@林浩-常服"], [])
    assert "@林浩-常服向门外走出一步。" in second_prompt
    assert "旧记录的第二条具体描述仍保留。" in second_prompt
    assert second_prompt.count("片段说明：") == 1


def test_storyboard_normalize_keeps_structured_only_shots_for_compilation():
    parsed = {
        "片段说明": {"场景": "@走廊", "共用要求": "无后期字幕"},
        "分镜列表": [{
            "分镜编号": "分镜_001",
            "引用资产变量": ["@角色-常服"],
            "镜头列表": [{
                "时间范围": {"开始时间": "00:00:00.0", "结束时间": "00:00:01.5"},
                "机位与构图": "近景，视线高度",
                "背景": "走廊墙面",
                "人物站位": "角色在画面中央",
                "动作过程": "抬手敲门",
                "运镜": "缓慢推近",
                "台词与声音": "敲门声",
            }],
        }],
    }
    normalized = main.normalize_storyboards_from_agent(parsed)
    assert len(normalized) == 1
    assert normalized[0]["prompt"] == ""
    main.sanitize_storyboard_asset_references(normalized, [], {})
    prompt = normalized[0]["prompt"]
    assert prompt.count("片段说明：") == 1
    assert "[机位与构图] 近景，视线高度" in prompt
    assert "[背景] 走廊墙面" in prompt
    assert "[人物站位] 角色在画面中央" in prompt
    assert "分镜01｜00:00:00.0-00:00:01.5｜1.5秒" in prompt
    assert "[动作过程] 抬手敲门" in prompt


def test_video_generation_rejects_only_explicit_unanalysed_fallback_before_task_creation(client: TestClient):
    project = create_project(client, title="storyboard-fallback-guard")
    fallback_id = "storyboard_explicit_fallback"
    old_id = "storyboard_legacy_without_marker"
    with main.connect() as conn:
        for storyboard_id, payload, prompt in (
            (
                fallback_id,
                {"使用回退占位": True, "片段说明": {"共用要求": "回退占位"}},
                "回退占位（非视频分析结果）",
            ),
            (
                old_id,
                {"源分镜文本": "旧版手工分镜，无七维结构字段。"},
                "旧版手工分镜，无七维结构字段。",
            ),
        ):
            conn.execute(
                "INSERT INTO storyboards (id,project_id,episode_id,title,time_range,prompt,payload_json,status,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (storyboard_id, project["id"], "episode_test", storyboard_id, "00:00:00-00:00:01", prompt, json.dumps(payload, ensure_ascii=False), "待分析", "2026-10-27T00:00:00Z"),
            )

    task_url = f"/api/projects/{project['id']}/tasks/run"
    tasks_before = len(client.get(f"/api/projects/{project['id']}/tasks").json()["tasks"])
    rejected = client.post(task_url, json={"taskType": "video_generation", "storyboardId": fallback_id})
    assert rejected.status_code == 422
    assert "未分析的回退占位" in rejected.json()["detail"]
    assert len(client.get(f"/api/projects/{project['id']}/tasks").json()["tasks"]) == tasks_before
    assert client.get(f"/api/projects/{project['id']}/video-generations").json()["videoGenerations"] == []

    patched = client.patch(
        f"/api/projects/{project['id']}/storyboards/{fallback_id}",
        json={"generatedBody": "固定中景，角色位于画面左侧；抬手敲门，门内传来脚步声。"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["storyboard"]["payload"]["使用回退占位"] is False
    generated = client.post(task_url, json={"taskType": "video_generation", "storyboardId": fallback_id})
    assert generated.status_code == 200, generated.text
    assert client.get(f"/api/projects/{project['id']}/video-generations").json()["videoGenerations"]

    legacy = client.post(task_url, json={"taskType": "video_generation", "storyboardId": old_id})
    assert legacy.status_code == 200, legacy.text
    assert len(client.get(f"/api/projects/{project['id']}/video-generations").json()["videoGenerations"]) == 2


def _seed_storyboard_agent_input(project: dict, video_path: Path) -> tuple[str, list[dict]]:
    episode_id = "episode_storyboard_agent"
    video_path.write_bytes(b"isolated video fixture")
    asset_index = [{
        "片段编号": "片段_001",
        "开始时间": "00:00:00",
        "结束时间": "00:00:11",
        "片段剧情简述": "两位角色在舞台相遇。",
        "出现资产变量": ["@LinHao"],
    }]
    with main.connect() as conn:
        conn.execute(
            "INSERT INTO episodes (id,project_id,episode_index,filename,file_path,status,created_at,ark_file_id,ark_file_status) VALUES (?,?,?,?,?,?,?,?,?)",
            (episode_id, project["id"], 1, "source.mp4", str(video_path), "已上传并同步 Ark", "2026-01-01T00:00:00Z", "ark-storyboard-fixture", "active"),
        )
        conn.execute(
            "INSERT INTO assets (id,project_id,episode_id,asset_type,name,localized_name,variable,status,payload_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("asset_storyboard_agent", project["id"], episode_id, "角色", "林浩", "Lin Hao", "{{role_1}}", "已确认", "{}", "2026-01-01T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO tasks (id,project_id,episode_id,task_type,status,message,result_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            ("task_asset_index_fixture", project["id"], episode_id, "asset_extraction", "成功", "资产提取完成", json.dumps({"资产出现片段索引": asset_index}, ensure_ascii=False), "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
    return episode_id, asset_index


def _valid_storyboard_model_output() -> str:
    return json.dumps({
        "片段说明": {"场景": "@婚礼舞台", "分镜数量": 1, "目标风格": "按项目设置", "参考素材分工": "视频提供镜头事实，资产变量提供外观", "成片语言与字幕": "保留可辨原话", "共用要求": "无后期字幕或水印"},
        "分镜列表": [{
            "分镜编号": "分镜_001",
            "分镜时间范围": {"开始时间": "00:00:00", "结束时间": "00:00:11"},
            "引用资产变量": ["@LinHao"],
            "镜头列表": [{
                "时间范围": {"开始时间": "00:00:00", "结束时间": "00:00:11"},
                "机位与构图": {"角度": "平视", "景别": "中景", "主体构图": "两人位于画面中央"},
                "背景": {"可见环境": "婚礼舞台"},
                "人物站位": [{"资产变量": "@LinHao", "画面左右": "左侧"}],
                "动作过程": {"起始": "面对面站立", "变化": "握手"},
                "运镜": {"方式": "固定"},
                "台词与声音": [{"原话": "待确认", "环境音": "宾客声"}],
            }],
            "最终视频生成提示词": "平视中景，两人站在婚礼舞台中央并握手。",
        }],
    }, ensure_ascii=False)


def test_storyboard_uses_video_agent_not_asset_index_as_shot_cache_and_protects_manual_text(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    project = create_project(client, title="storyboard-cache-regression")
    video_path = tmp_path / "source.mp4"
    episode_id, asset_index = _seed_storyboard_agent_input(project, video_path)
    with main.connect() as conn:
        conn.execute(
            "INSERT INTO storyboards (id,project_id,episode_id,title,time_range,prompt,payload_json,status,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            ("storyboard_manual_fixture", project["id"], episode_id, "手工分镜", "00:00:00-00:00:02", "手工真实镜头描述", json.dumps({"分镜生成正文": "用户手工编辑的正文"}, ensure_ascii=False), "已生成", "2026-01-01T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO storyboards (id,project_id,episode_id,title,time_range,prompt,payload_json,status,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            ("storyboard_fallback_fixture", project["id"], episode_id, "待分析片段 001", "00:00:00-00:00:11", "mock占位提示词", json.dumps({"使用回退占位": True}, ensure_ascii=False), "待分析", "2026-01-01T00:00:01Z"),
        )
        conn.execute(
            "INSERT INTO storyboards (id,project_id,episode_id,title,time_range,prompt,payload_json,status,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            ("storyboard_empty_draft_fixture", project["id"], episode_id, "待分析片段 空草稿", "00:00:00-00:00:11", "占位提示词", json.dumps({"使用回退占位": True, "分镜生成正文": "  "}, ensure_ascii=False), "待分析", "2026-01-01T00:00:02Z"),
        )
        conn.execute(
            "INSERT INTO storyboards (id,project_id,episode_id,title,time_range,prompt,payload_json,status,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            ("storyboard_race_fixture", project["id"], episode_id, "待分析片段 并发编辑", "00:00:00-00:00:11", "占位提示词", json.dumps({"使用回退占位": True}, ensure_ascii=False), "待分析", "2026-01-01T00:00:03Z"),
        )
    calls: list[dict] = []
    monkeypatch.setattr(main, "prepare_agent", lambda _key: {"mode": "volcengine", "model_name": "provider-spy", "prompt": "video storyboard", "prompt_file": "test"})
    def fake_storyboard_call(_key, _agent, payload):
        calls.append(payload.copy())
        if len(calls) == 1:
            # Simulate the user saving a manual body while the provider call is in flight.
            with main.connect() as concurrent_conn:
                row = concurrent_conn.execute("SELECT payload_json FROM storyboards WHERE id=?", ("storyboard_race_fixture",)).fetchone()
                concurrent_payload = json.loads(row["payload_json"])
                concurrent_payload.update({"使用回退占位": False, "回退占位已由用户补全": True, "分镜生成正文": "分析期间保存的人工正文"})
                concurrent_conn.execute("UPDATE storyboards SET payload_json=?,prompt=? WHERE id=?", (json.dumps(concurrent_payload, ensure_ascii=False), "分析期间保存的人工正文", "storyboard_race_fixture"))
        return _valid_storyboard_model_output()
    monkeypatch.setattr(main, "call_agent_chat", fake_storyboard_call)

    with main.connect() as conn:
        db_project = main.get_project_or_404(conn, project["id"])
        generated = main.run_storyboard_generation(conn, db_project, episode_id)
        assert generated["status"] == "成功", generated
        assert generated["result"]["使用回退分镜"] is False
        assert generated["result"]["复用分镜缓存"] is False
        assert generated["result"]["资产片段作为上下文"] is True
        assert len(calls) == 1
        assert calls[0]["资产出现片段索引"][0]["开始时间"] == asset_index[0]["开始时间"]
        assert calls[0]["资产出现片段索引"][0]["结束时间"] == asset_index[0]["结束时间"]
        assert calls[0]["资产出现片段索引"][0]["出现资产变量"] == ["@LinHao"]
        assert "不代表真实切镜" in calls[0]["资产片段索引用途"]
        assert {item["id"] for item in generated["result"]["被替换的旧分镜备份"]} == {"storyboard_fallback_fixture", "storyboard_empty_draft_fixture"}
        rows = conn.execute("SELECT id,payload_json FROM storyboards WHERE project_id=? AND episode_id=? ORDER BY created_at", (project["id"], episode_id)).fetchall()
        assert any(row["id"] == "storyboard_manual_fixture" and json.loads(row["payload_json"])["分镜生成正文"] == "用户手工编辑的正文" for row in rows)
        assert all(row["id"] != "storyboard_fallback_fixture" for row in rows)
        assert all(row["id"] != "storyboard_empty_draft_fixture" for row in rows)
        assert any(row["id"] == "storyboard_race_fixture" and json.loads(row["payload_json"])["分镜生成正文"] == "分析期间保存的人工正文" for row in rows)
        assert any(json.loads(row["payload_json"]).get("分镜分析来源", {}).get("videoSignature") for row in rows)

        cached = main.run_storyboard_generation(conn, db_project, episode_id)
        assert cached["result"]["复用分镜缓存"] is True
        assert len(calls) == 1
        forced = main.run_storyboard_generation(conn, db_project, episode_id, force=True)
        assert forced["result"]["复用分镜缓存"] is False
        assert len(calls) == 2
        rows = conn.execute("SELECT id,payload_json FROM storyboards WHERE project_id=? AND episode_id=?", (project["id"], episode_id)).fetchall()
        assert any(row["id"] == "storyboard_manual_fixture" and json.loads(row["payload_json"])["分镜生成正文"] == "用户手工编辑的正文" for row in rows)
        assert any(row["id"] == "storyboard_race_fixture" and json.loads(row["payload_json"])["分镜生成正文"] == "分析期间保存的人工正文" for row in rows)
        assert sum(bool(json.loads(row["payload_json"]).get("分镜分析来源")) for row in rows) == 1

        before_ids = {row["id"] for row in rows}
        monkeypatch.setattr(main, "call_agent_chat", lambda *_args: json.dumps({"分镜列表": [{"分镜时间范围": "00:00:00-00:00:11", "最终视频生成提示词": "按原片演绎"}]}, ensure_ascii=False))
        failed = main.run_storyboard_generation(conn, db_project, episode_id, force=True)
        assert failed["status"] == "失败"
        assert "缺少完整逐镜事实" in failed["message"]
        after_rows = conn.execute("SELECT id,payload_json FROM storyboards WHERE project_id=? AND episode_id=?", (project["id"], episode_id)).fetchall()
        assert {row["id"] for row in after_rows} == before_ids
        assert any(row["id"] == "storyboard_manual_fixture" and json.loads(row["payload_json"])["分镜生成正文"] == "用户手工编辑的正文" for row in after_rows)


def test_mock_storyboard_fallback_is_reported_as_pending_and_get_hides_placeholder_prompt(client: TestClient, tmp_path: Path):
    project = create_project(client, title="storyboard-placeholder-display")
    episode_id, _ = _seed_storyboard_agent_input(project, tmp_path / "mock-source.mp4")
    with main.connect() as conn:
        db_project = main.get_project_or_404(conn, project["id"])
        task = main.run_storyboard_generation(conn, db_project, episode_id)
        assert task["status"] == "失败"
        assert task["result"]["使用回退分镜"] is True
        assert task["result"]["分镜数量"] == 0
        assert task["result"]["占位片段数量"] == 1
        assert conn.execute("SELECT status FROM projects WHERE id=?", (project["id"],)).fetchone()[0] == "分镜待分析"
    boards = client.get(f"/api/projects/{project['id']}/storyboards?episode_id={episode_id}").json()["storyboards"]
    assert len(boards) == 1
    assert boards[0]["payload"]["使用回退占位"] is True
    assert boards[0]["analysis_state"] == "分析失败"
    assert boards[0]["prompt"] == ""
    assert boards[0]["title"].startswith("待分析片段")


def test_unexpected_storyboard_transport_error_marks_project_failed_and_preserves_placeholders(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    project = create_project(client, title="storyboard-incomplete-read")
    episode_id, _ = _seed_storyboard_agent_input(project, tmp_path / "incomplete-read-source.mp4")
    with main.connect() as conn:
        db_project = main.get_project_or_404(conn, project["id"])
        fallback = main.run_storyboard_generation(conn, db_project, episode_id)
        assert fallback["status"] == "失败"
        old_rows = conn.execute(
            "SELECT id, payload_json FROM storyboards WHERE project_id=? AND episode_id=? ORDER BY id",
            (project["id"], episode_id),
        ).fetchall()
        old_ids = [row["id"] for row in old_rows]
        assert old_ids
        assert all(json.loads(row["payload_json"]).get("使用回退占位") is True for row in old_rows)

    monkeypatch.setattr(main, "prepare_agent", lambda _key: {"mode": "volcengine", "model_name": "provider-spy", "prompt": "video storyboard", "prompt_file": "test"})
    monkeypatch.setattr(main, "call_agent_chat", lambda *_args: (_ for _ in ()).throw(http.client.IncompleteRead(b"partial", 10)))
    response = client.post(
        f"/api/projects/{project['id']}/tasks/run",
        json={"taskType": "storyboard_generation", "episodeId": episode_id, "force": True},
    )
    assert response.status_code == 200, response.text
    task_id = response.json()["task"]["id"]
    task = client.get(f"/api/tasks/{task_id}").json()["task"]
    assert task["status"] == "失败"
    assert "IncompleteRead" in task["message"]
    with main.connect() as conn:
        assert conn.execute("SELECT status FROM projects WHERE id=?", (project["id"],)).fetchone()[0] == "分镜分析失败"
        after_rows = conn.execute(
            "SELECT id, payload_json FROM storyboards WHERE project_id=? AND episode_id=? ORDER BY id",
            (project["id"], episode_id),
        ).fetchall()
        assert [row["id"] for row in after_rows] == old_ids
        assert all(json.loads(row["payload_json"]).get("使用回退占位") is True for row in after_rows)


def test_storyboard_recovers_only_complete_fact_checked_partial_response_and_audits_transport(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    project = create_project(client, title="storyboard-complete-partial-response")
    episode_id, _ = _seed_storyboard_agent_input(project, tmp_path / "partial-response-source.mp4")
    with main.connect() as conn:
        db_project = main.get_project_or_404(conn, project["id"])
        fallback = main.run_storyboard_generation(conn, db_project, episode_id)
        assert fallback["status"] == "失败"
        old_rows = conn.execute(
            "SELECT id FROM storyboards WHERE project_id=? AND episode_id=? ORDER BY id",
            (project["id"], episode_id),
        ).fetchall()
        old_ids = [row["id"] for row in old_rows]

    model_output = _valid_storyboard_model_output()
    envelope = json.dumps({"id": "resp_test_complete_partial", "status": "completed", "output_text": model_output}, ensure_ascii=False)
    partial_error = ProviderIncompleteRead(
        envelope.encode("utf-8"),
        envelope,
        model_output,
        "resp_test_complete_partial",
        True,
    )
    monkeypatch.setattr(main, "prepare_agent", lambda _key: {"mode": "volcengine", "model_name": "provider-spy", "prompt": "video storyboard", "prompt_file": "test"})
    monkeypatch.setattr(main, "call_agent_chat", lambda *_args: (_ for _ in ()).throw(partial_error))
    response = client.post(
        f"/api/projects/{project['id']}/tasks/run",
        json={"taskType": "storyboard_generation", "episodeId": episode_id, "force": True},
    )
    assert response.status_code == 200, response.text
    task_id = response.json()["task"]["id"]
    task = client.get(f"/api/tasks/{task_id}").json()["task"]
    assert task["status"] == "成功"
    assert task["result"]["传输异常完整有效JSON已恢复"] is True
    assert task["result"]["模型传输响应ID"] == "resp_test_complete_partial"
    assert task["result"]["模型传输部分响应原文"] == envelope
    assert task["result"]["被替换的旧分镜备份"]
    with main.connect() as conn:
        rows = conn.execute("SELECT id, payload_json FROM storyboards WHERE project_id=? AND episode_id=?", (project["id"], episode_id)).fetchall()
        assert not ({row["id"] for row in rows} & set(old_ids))
        assert all(main.storyboard_has_real_shot_facts(json.loads(row["payload_json"])) for row in rows)


def test_storyboard_audits_truncated_partial_but_keeps_placeholder(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    project = create_project(client, title="storyboard-truncated-partial-response")
    episode_id, _ = _seed_storyboard_agent_input(project, tmp_path / "truncated-response-source.mp4")
    with main.connect() as conn:
        db_project = main.get_project_or_404(conn, project["id"])
        main.run_storyboard_generation(conn, db_project, episode_id)
        before_ids = [row[0] for row in conn.execute(
            "SELECT id FROM storyboards WHERE project_id=? AND episode_id=? ORDER BY id",
            (project["id"], episode_id),
        ).fetchall()]

    partial_text = json.dumps(
        {"id": "resp_truncated", "status": "incomplete", "output_text": '{"分镜列表":['},
        ensure_ascii=False,
    )
    partial_error = ProviderIncompleteRead(partial_text.encode("utf-8"), partial_text, "{}", "resp_truncated", False)
    monkeypatch.setattr(main, "prepare_agent", lambda _key: {"mode": "volcengine", "model_name": "provider-spy", "prompt": "video storyboard", "prompt_file": "test"})
    monkeypatch.setattr(main, "call_agent_chat", lambda *_args: (_ for _ in ()).throw(partial_error))
    response = client.post(
        f"/api/projects/{project['id']}/tasks/run",
        json={"taskType": "storyboard_generation", "episodeId": episode_id, "force": True},
    )
    assert response.status_code == 200, response.text
    task = client.get(f"/api/tasks/{response.json()['task']['id']}").json()["task"]
    assert task["status"] == "失败"
    assert task["result"]["模型传输部分响应原文"] == partial_text
    assert task["result"]["传输异常响应完整标记"] is False
    assert "未采用输出" in task["result"]["模型错误"]
    with main.connect() as conn:
        rows = conn.execute(
            "SELECT id, payload_json FROM storyboards WHERE project_id=? AND episode_id=? ORDER BY id",
            (project["id"], episode_id),
        ).fetchall()
        assert [row["id"] for row in rows] == before_ids
        assert all(json.loads(row["payload_json"]).get("使用回退占位") is True for row in rows)


def test_provider_incomplete_read_keeps_partial_response_and_rejects_length_finish(monkeypatch: pytest.MonkeyPatch):
    import backend.app.providers.model_provider as provider_module

    monkeypatch.setenv("ARK_API_KEY", "test-key")
    provider = VolcengineModelProvider({"volcengine": {"baseUrl": "https://example.invalid", "responseTimeoutSeconds": 1}})
    bodies = [
        json.dumps({"id": "resp_complete", "status": "completed", "output_text": "{}"}).encode("utf-8"),
        json.dumps({"id": "resp_length", "status": "completed", "output_text": "{}", "choices": [{"finish_reason": "length"}]}).encode("utf-8"),
        json.dumps({"id": "resp_incomplete", "status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}, "output_text": '{"分镜列表":[]}'}, ensure_ascii=False).encode("utf-8"),
    ]

    class PartialResponse:
        def __init__(self, body: bytes):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            raise http.client.IncompleteRead(self.body, len(self.body) + 12)

    for body, expected_id, expected_complete in (
        (bodies[0], "resp_complete", True),
        (bodies[1], "resp_length", False),
        (bodies[2], "resp_incomplete", False),
    ):
        monkeypatch.setattr(provider_module.urllib.request, "urlopen", lambda *_args, _body=body, **_kwargs: PartialResponse(_body))
        with pytest.raises(ProviderIncompleteRead) as caught:
            provider.chat_completion(model_name="test-model", system_prompt="", user_content="{}")
        assert caught.value.partial_body == body
        assert caught.value.response_id == expected_id
        assert caught.value.response_complete is expected_complete
        if expected_id == "resp_length":
            assert caught.value.finish_reason == "length"
        if expected_id == "resp_incomplete":
            assert caught.value.extracted_text == '{"分镜列表":[]}'
            assert caught.value.response_complete is False

    class CompleteResponse:
        def __init__(self, body: bytes):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return self.body

    incomplete_envelopes = (
        {"id": "resp_incomplete", "status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}, "output_text": '{"shots":[]}'},
        {"id": "resp_failed", "status": "failed", "error": {"message": "generation failed"}, "output_text": "{}"},
        {"id": "resp_finish_length", "status": "completed", "choices": [{"finish_reason": "length"}], "output_text": "{}"},
    )
    for envelope in incomplete_envelopes:
        body = json.dumps(envelope).encode("utf-8")
        monkeypatch.setattr(provider_module.urllib.request, "urlopen", lambda *_args, _body=body, **_kwargs: CompleteResponse(_body))
        with pytest.raises(ProviderIncompleteResponse) as caught:
            provider.chat_completion(model_name="test-model", system_prompt="", user_content="{}")
        assert caught.value.raw_response == body.decode("utf-8")
        assert caught.value.response_id == envelope["id"]
        assert caught.value.response_status == envelope["status"]
        assert caught.value.extracted_text == envelope["output_text"]
    assert caught.value.finish_reason == "length"


def test_storyboard_audits_complete_http_incomplete_envelope_and_keeps_placeholder(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    project = create_project(client, title="storyboard-incomplete-envelope")
    episode_id, _ = _seed_storyboard_agent_input(project, tmp_path / "incomplete-envelope-source.mp4")
    with main.connect() as conn:
        db_project = main.get_project_or_404(conn, project["id"])
        main.run_storyboard_generation(conn, db_project, episode_id)
        before_ids = [row[0] for row in conn.execute(
            "SELECT id FROM storyboards WHERE project_id=? AND episode_id=? ORDER BY id",
            (project["id"], episode_id),
        ).fetchall()]

    raw_response = json.dumps(
        {"id": "resp_incomplete_audit", "status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}, "output_text": '{"分镜列表":[]}'},
        ensure_ascii=False,
    )
    incomplete = ProviderIncompleteResponse(
        raw_response,
        "resp_incomplete_audit",
        "方舟接口返回了未完成响应，不能作为有效模型结果使用。",
        "incomplete",
        {"reason": "max_output_tokens"},
        "",
        '{"分镜列表":[]}',
    )
    monkeypatch.setattr(main, "prepare_agent", lambda _key: {"mode": "volcengine", "model_name": "provider-spy", "prompt": "video storyboard", "prompt_file": "test"})
    monkeypatch.setattr(main, "call_agent_chat", lambda *_args: (_ for _ in ()).throw(incomplete))
    response = client.post(
        f"/api/projects/{project['id']}/tasks/run",
        json={"taskType": "storyboard_generation", "episodeId": episode_id, "force": True},
    )
    assert response.status_code == 200, response.text
    task = client.get(f"/api/tasks/{response.json()['task']['id']}").json()["task"]
    assert task["status"] == "失败"
    assert task["result"]["模型响应未完成"] is True
    assert task["result"]["模型响应ID"] == "resp_incomplete_audit"
    assert task["result"]["模型响应incomplete_details"] == {"reason": "max_output_tokens"}
    assert task["result"]["模型响应可提取文本"] == '{"分镜列表":[]}'
    assert task["result"]["模型响应原文"] == raw_response
    assert "未完成响应" in task["result"]["模型错误"]
    with main.connect() as conn:
        rows = conn.execute(
            "SELECT id, payload_json FROM storyboards WHERE project_id=? AND episode_id=? ORDER BY id",
            (project["id"], episode_id),
        ).fetchall()
        assert [row["id"] for row in rows] == before_ids
        assert all(json.loads(row["payload_json"]).get("使用回退占位") is True for row in rows)


def test_storyboard_mode_prompt_routes_keep_shared_shot_rules_without_defaults_or_duplicate_style():
    base_prompt = main.load_agent_config("storyboardPromptGeneration")["prompt"]
    assert "新输出将它们留空字符串，不得复述七维内容" in base_prompt
    for mode in ("overseas_localization", "cg_3d", "asset_replacement"):
        agent = {"prompt": base_prompt}
        project = {
            "remake_mode": mode,
            "source_language": "中文",
            "target_country": "日本",
            "target_language": "日语",
            "storyboard_visual_style": "cg_3d" if mode == "cg_3d" else "live_action",
        }
        main.route_project_agent_prompt(agent, project, storyboard=True)
        routed = agent["prompt"]
        assert "8至15秒" not in routed and "8-15秒" not in routed and "8–15秒" not in routed
        assert "目标国家为空时，默认使用美国。" not in routed
        assert "目标语言为空时，默认使用英语。" not in routed
        assert main.STORYBOARD_STYLE_TEMPLATES["cg_3d"] not in routed
