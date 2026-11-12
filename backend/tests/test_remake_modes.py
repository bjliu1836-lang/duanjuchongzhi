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


def test_asset_image_model_count_and_wide_ratio_create_real_history_versions(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    project = create_project(client, title="asset-image-options", remakeMode="overseas_localization", sourceLanguage="中文")
    asset_id, _ = insert_source_records(project, selected_image=False)
    provider_calls: list[dict] = []

    class ProviderSpy:
        def generate_image(self, **kwargs):
            provider_calls.append(kwargs)
            image = io.BytesIO()
            Image.new("RGB", (16, 9), (80, 90, 100)).save(image, format="PNG")
            return {
                "image_bytes": image.getvalue(),
                "mime_type": "image/png",
                "size": kwargs["size"],
                "model": kwargs["model_name"],
                "raw": {},
            }

    monkeypatch.setattr(main, "prepare_agent", lambda _key: {"mode": "volcengine", "model_name": "doubao-seedream-5-0-260128", "prompt": "", "prompt_file": "test"})
    monkeypatch.setattr(main, "load_models_config", lambda: {"volcengine": {"imageSize": "2K"}})
    monkeypatch.setattr(main, "get_model_provider", lambda _config: ProviderSpy())
    response = client.post(
        f"/api/projects/{project['id']}/assets/{asset_id}/generate-image",
        json={
            "finalPrompt": "横向角色资产设定图",
            "model": "doubao-seedream-4-5-251128",
            "count": 3,
            "aspectRatio": "16:9",
        },
    )
    assert response.status_code == 200, response.text
    images = response.json()["asset"]["images"]
    assert len(provider_calls) == 3
    assert {call["model_name"] for call in provider_calls} == {"doubao-seedream-4-5-251128"}
    assert {call["size"] for call in provider_calls} == {"2848x1600"}
    assert len(images) == 3
    assert sum(image["is_selected"] for image in images) == 1
    assert {image["payload"]["generationIndex"] for image in images} == {1, 2, 3}
    assert {image["payload"]["aspectRatio"] for image in images} == {"16:9"}

    rejected = client.post(
        f"/api/projects/{project['id']}/assets/{asset_id}/generate-image",
        json={"finalPrompt": "横向角色资产设定图", "model": "unsupported-model", "count": 5},
    )
    assert rejected.status_code == 422
    assert len(provider_calls) == 3


def test_blank_manual_final_prompt_fails_but_nonempty_unresolved_text_is_sent(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    project = create_project(client, title="manual-final-prompt-guard", remakeMode="overseas_localization", sourceLanguage="中文")
    asset_id, _ = insert_source_records(project, selected_image=False)
    blank = client.post(f"/api/projects/{project['id']}/assets/{asset_id}/generate-image", json={"finalPrompt": "  \n"})
    assert blank.status_code == 422
    assert "不能为空" in blank.json()["detail"]
    values = [
        "根据原片补足衣摆细节",
        "目标画像待本地化；当前旧资产共享描述不能作为目标提示词。",
        "目标画像需本地化；旧共享描述不能作为目标提示词。",
        "当前资产沿用原片外观与原片资产图；不使用历史目标候选。",
    ]
    for value in values:
        accepted = client.post(f"/api/projects/{project['id']}/assets/{asset_id}/generate-image", json={"finalPrompt": value})
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["asset"]["images"][0]["prompt"] == value
    with main.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM asset_generation_jobs WHERE asset_id=?", (asset_id,)).fetchone()[0] == len(values)


def test_sending_unchanged_automatic_prompt_does_not_create_manual_override(client: TestClient, monkeypatch: pytest.MonkeyPatch):
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


def test_pending_cg_auto_status_is_not_prefilled_but_any_nonempty_user_text_can_generate(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    project = create_project(client, title="pending-cg-placeholder", remakeMode="cg_3d", sourceLanguage="中文", targetCountry="美国")
    asset_id, _ = insert_source_records(project, selected_image=False)
    asset = next(item for item in client.get(f"/api/projects/{project['id']}/assets").json()["assets"] if item["id"] == asset_id)
    pending = asset["payload"]["最终资产图生成提示词"]
    assert asset["payload"]["目标画像状态"] == "待本地化"
    assert pending == "目标画像待本地化；当前旧资产共享描述不能作为目标提示词。"
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
    status_text = client.post(f"/api/projects/{project['id']}/assets/{asset_id}/generate-image", json={"finalPrompt": pending})
    assert status_text.status_code == 200, status_text.text
    assert len(provider_calls) == 1
    assert provider_calls[0].startswith(pending)
    with main.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM asset_generation_jobs WHERE asset_id=?", (asset_id,)).fetchone()[0] == 1

    manual = "手工完整角色设定：成年墨西哥裔美国男性，深棕短发，棕色眼睛，蓝色外套，白色背景。"
    accepted = client.post(f"/api/projects/{project['id']}/assets/{asset_id}/generate-image", json={"finalPrompt": manual})
    assert accepted.status_code == 200, accepted.text
    assert len(provider_calls) == 2
    assert provider_calls[1].startswith(manual)
    assert provider_calls[1].count(main.CG_ROLE_FIXED_TEMPLATE) == 1


def test_group_portrait_generates_from_source_observations_without_single_role_prompt(client: TestClient):
    project = create_project(client, title="group-prompt", remakeMode="cg_3d", sourceLanguage="中文", targetCountry="美国", targetLanguage="English")
    with main.connect() as conn:
        episode_id = "episode_group_prompt"
        conn.execute(
            "INSERT INTO episodes (id,project_id,episode_index,filename,file_path,status,created_at) VALUES (?,?,?,?,?,?,?)",
            (episode_id, project["id"], 1, "source.mp4", "C:/test/source.mp4", "已上传", "2026-01-01T00:00:00Z"),
        )
        payload = {
            "sourceSnapshot": {
                "assetKind": "群像",
                "name": "婚礼宾客群像",
                "coreDescription": "当代中国婚礼宾客群，黄皮为主，男女混合；女性穿浅色礼服，宾客分布在新娘身后。",
                "visualDescription": "多人站立观看仪式，保留已观察到的服装颜色与前后站位。",
            },
            "当前子资产": {"原剧视觉形象描述": "多人站立观看仪式，保留已观察到的服装颜色与前后站位。"},
            "localizedProfile": {"status": "pending", "needsReview": True},
        }
        conn.execute(
            "INSERT INTO assets (id,project_id,episode_id,asset_type,name,localized_name,variable,status,payload_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("asset_group_prompt", project["id"], episode_id, "角色", "婚礼宾客群像", "婚礼宾客群像", "{{group_1}}", "已确认", json.dumps(payload, ensure_ascii=False), "2026-01-01T00:00:00Z"),
        )
    asset = next(item for item in client.get(f"/api/projects/{project['id']}/assets").json()["assets"] if item["id"] == "asset_group_prompt")
    prompt = asset["payload"]["自动编译资产图生成提示词"]
    assert "群像角色资产图" in prompt
    assert "婚礼宾客群" in prompt
    assert "浅色礼服" in prompt
    assert "宾客分布在新娘身后" in prompt
    assert prompt.count("多人站立观看仪式") == 1
    assert "多元族裔美国群像人物" in prompt
    assert "黄皮" not in prompt
    assert "当代中国" not in prompt
    assert "单人四视图" in prompt
    assert main.CG_ROLE_FIXED_TEMPLATE in prompt
    assert prompt.count(main.CG_ROLE_FIXED_TEMPLATE) == 1
    assert not prompt.startswith("目标画像待本地化；")
    generated = client.post(f"/api/projects/{project['id']}/assets/asset_group_prompt/generate-image", json={"finalPrompt": prompt})
    assert generated.status_code == 200, generated.text
    assert generated.json()["asset"]["images"][0]["prompt"].count(main.CG_ROLE_FIXED_TEMPLATE) == 1


def test_currently_generating_asset_rejects_duplicate_request(client: TestClient):
    project = create_project(client, title="same-asset-in-flight", remakeMode="overseas_localization", sourceLanguage="中文")
    asset_id, _ = insert_source_records(project, selected_image=False)
    with main.connect() as conn:
        conn.execute(
            "INSERT INTO asset_generation_jobs (id,project_id,asset_id,remake_mode,template_version,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
            ("asset_job_inflight", project["id"], asset_id, "overseas_localization", main.ASSET_PROMPT_TEMPLATE_VERSION, "running", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
    response = client.post(f"/api/projects/{project['id']}/assets/{asset_id}/generate-image", json={"finalPrompt": "非空提示词。"})
    assert response.status_code == 409
    assert "正在生成" in response.json()["detail"]
    reloaded = client.get(f"/api/projects/{project['id']}/assets").json()["assets"]
    assert next(item for item in reloaded if item["id"] == asset_id)["payload"]["generationInProgress"] is True
    with main.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM asset_generation_jobs WHERE asset_id=?", (asset_id,)).fetchone()[0] == 1
        conn.execute("UPDATE asset_generation_jobs SET status='succeeded' WHERE id='asset_job_inflight'")
    reloaded = client.get(f"/api/projects/{project['id']}/assets").json()["assets"]
    assert next(item for item in reloaded if item["id"] == asset_id)["payload"]["generationInProgress"] is False


def test_manual_prompt_restore_original_clears_override_and_edit_can_resume_generation(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    project = create_project(client, title="restore-manual-prompt", remakeMode="asset_replacement", sourceLanguage="中文", replaceCharacters=True)
    asset_id, _ = insert_source_records(project, selected_image=False)
    client.patch(f"/api/projects/{project['id']}/assets/{asset_id}", json={"payload": {"用户编辑资产图生成提示词": "完整自包含目标角色图，白底四视图。"}})
    restored = client.post(f"/api/projects/{project['id']}/assets/{asset_id}/restore-original")
    assert restored.status_code == 200, restored.text
    assert restored.json()["asset"]["payload"]["用户编辑资产图生成提示词"] == ""
    generated_from_original = client.post(f"/api/projects/{project['id']}/assets/{asset_id}/generate-image", json={"finalPrompt": "完整自包含目标角色图，白底四视图。"})
    assert generated_from_original.status_code == 200, generated_from_original.text

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


@pytest.mark.parametrize("asset_type", ["角色", "场景", "道具"])
def test_disabled_replacement_type_still_accepts_nonempty_manual_prompt(client: TestClient, asset_type: str):
    project = create_project(client, title=f"replacement-switch-off-{asset_type}", remakeMode="asset_replacement", sourceLanguage="中文", replaceCharacters=False, replaceScenes=False)
    with main.connect() as conn:
        episode_id = f"episode_switch_off_{asset_type}"
        asset_id = f"asset_switch_off_{asset_type}"
        conn.execute("INSERT INTO episodes (id,project_id,episode_index,filename,file_path,status,created_at) VALUES (?,?,?,?,?,?,?)", (episode_id, project["id"], 1, "source.mp4", "C:/test/source.mp4", "已上传", "2026-01-01T00:00:00Z"))
        conn.execute("INSERT INTO assets (id,project_id,episode_id,asset_type,name,localized_name,variable,status,payload_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)", (asset_id, project["id"], episode_id, asset_type, f"{asset_type}资产", f"{asset_type}资产", f"{{{{{asset_type}_1}}}}", "已确认", json.dumps({}, ensure_ascii=False), "2026-01-01T00:00:00Z"))
    value = f"人工指定的{asset_type}目标资产图提示词。"
    response = client.post(f"/api/projects/{project['id']}/assets/{asset_id}/generate-image", json={"finalPrompt": value})
    assert response.status_code == 200, response.text
    assert response.json()["asset"]["images"][0]["prompt"] == value


def test_legacy_missing_visual_fields_compile_nonempty_prompt_without_generation_gate(client: TestClient):
    project = create_project(client, title="legacy-incomplete-role", remakeMode="asset_replacement", sourceLanguage="中文", replaceCharacters=True)
    asset_id, _ = insert_source_records(project, selected_image=False)
    response = client.post(f"/api/projects/{project['id']}/assets/{asset_id}/generate-image", json={})
    assert response.status_code == 200, response.text
    assert response.json()["asset"]["images"][0]["prompt"].strip()
    with main.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM asset_generation_jobs WHERE asset_id=?", (asset_id,)).fetchone()[0] == 1


def test_tier_and_manual_shoe_visibility_conflicts_do_not_block_generation(client: TestClient):
    project = create_project(client, title="tier-conflict-guard", remakeMode="overseas_localization", sourceLanguage="中文", targetCountry="中国", targetLanguage="中文")
    asset_id, _ = insert_source_records(project, selected_image=False)
    high_daily = valid_role_visual_structure(服装={"服装等级": "高位礼服", "场合": "日常", "拖尾程度": "高位典礼遮鞋", "鞋面可见性": "不露鞋面", "脚踝可见性": "不露脚踝"})
    prepare_valid_role_asset(asset_id, structure=high_daily)
    tier_response = client.post(f"/api/projects/{project['id']}/assets/{asset_id}/generate-image", json={})
    assert tier_response.status_code == 200, tier_response.text
    with main.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM asset_generation_jobs WHERE asset_id=?", (asset_id,)).fetchone()[0] == 1

    high_ceremony = valid_role_visual_structure(服装={"服装等级": "帝后礼服", "场合": "宫廷登基典礼", "拖尾程度": "高位典礼遮鞋", "摆与地面关系": "裙摆压地遮鞋", "鞋面可见性": "不露鞋面", "脚踝可见性": "不露脚踝"})
    prepare_valid_role_asset(asset_id, structure=high_ceremony)
    override_response = client.post(f"/api/projects/{project['id']}/assets/{asset_id}/generate-image", json={"prompt": "靴尖微露"})
    assert override_response.status_code == 200, override_response.text
    with main.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM asset_generation_jobs WHERE asset_id=?", (asset_id,)).fetchone()[0] == 2


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
    assert captured["size"] == "2848x1600"
    assert image["payload"]["aspectRatio"] == "16:9"
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
    # 项目隔离：同视频同配置、不同项目 id 的两套缓存键必须不同（新项目不受旧项目污染）；
    # 同项目内 id 不变，键保持稳定可复用。
    assert main.asset_source_cache_key("same-source-sha", agent, {**project, "id": "project_A"}, []) != main.asset_source_cache_key("same-source-sha", agent, {**project, "id": "project_B"}, [])
    assert main.asset_source_cache_key("same-source-sha", agent, {**project, "id": "project_A"}, []) == main.asset_source_cache_key("same-source-sha", agent, {**project, "id": "project_A"}, [])
    assert main.asset_localization_cache_key([], {**project, "id": "project_A"}, agent) != main.asset_localization_cache_key([], {**project, "id": "project_B"}, agent)


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
    assert main.source_asset_appearance_ranges({"出现时间点": [0, 1.5]}) == [("0", "0"), ("1.5", "1.5")]


def test_run_task_completes_one_outer_task_and_keeps_agent_audit_linked(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    project = create_project(client, title="single-task-run")
    episode_id, _ = insert_source_records(project)
    provider_calls = {"count": 0}

    def fake_runner(conn, db_project, selected_episode_id, task_id=None):
        provider_calls["count"] += 1
        result = {"模型原始输出": "{\"ok\":true}", "解析": {"ok": True}}
        task = main.finish_or_create_task(
            conn, db_project["id"], selected_episode_id, "asset_extraction", "成功", "mock extraction complete", result, task_id
        )
        main.record_agent_run(
            conn, task["id"], db_project["id"], selected_episode_id,
            "assetSourceExtraction", {"mode": "mock", "model_name": "once"}, {"video": "fixture"}, result,
        )
        return task

    monkeypatch.setattr(main, "run_asset_extraction", fake_runner)
    response = client.post(f"/api/projects/{project['id']}/tasks/run", json={"taskType": "asset_extraction", "episodeId": episode_id})
    assert response.status_code == 200
    task_id = response.json()["task"]["id"]
    details = client.get(f"/api/tasks/{task_id}")
    assert details.status_code == 200
    assert details.json()["task"]["status"] == "成功"
    assert details.json()["task"]["result"]["模型原始输出"] == "{\"ok\":true}"
    assert len(details.json()["agentRuns"]) == 1
    assert details.json()["agentRuns"][0]["task_id"] == task_id
    assert provider_calls["count"] == 1
    with main.connect() as conn:
        rows = conn.execute("SELECT id FROM tasks WHERE project_id=? AND task_type='asset_extraction'", (project["id"],)).fetchall()
    assert [row["id"] for row in rows] == [task_id]


def test_legacy_prop_aliases_preserve_source_identity_change_cache_and_block_empty_source(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    source_prompt = (main.CONFIG_DIR / "agents" / "asset_source_extraction.md").read_text(encoding="utf-8")
    assert '"原剧道具名称"' in source_prompt and '"原剧道具描述"' in source_prompt
    assert '"道具资产变量"' in source_prompt and '"道具编号"' in source_prompt
    raw = {"资产库": {"道具资产": [
        {"道具编号": "PROP_002", "变量": "{{PROP_002}}", "原剧名称": "黑色塑料鸡笼", "原剧描述": "长方形黑色塑料周转笼，笼内有一只红棕色鸡。", "原剧截图时间点候选": ["15.0秒"]},
        {"道具编号": "PROP_003", "变量": "{{PROP_003}}", "原剧名称": "顾霜肩扛的麻袋与葱", "原剧描述": "米黄色布麻袋，袋口露出绿色长葱叶。", "原剧截图时间点候选": ["19.0秒"]},
        {"道具编号": "PROP_006", "变量": "{{PROP_006}}", "原剧名称": "新郎黑色领结", "原剧描述": "黑色蝴蝶结领结，中央有金色金属装饰。", "原剧截图时间点候选": ["2.0秒"]},
    ]}}
    assets = main.normalize_assets_from_agent(raw)
    assert [(asset["name"], asset["variable"]) for asset in assets] == [
        ("黑色塑料鸡笼", "{{PROP_002}}"),
        ("顾霜肩扛的麻袋与葱", "{{PROP_003}}"),
        ("新郎黑色领结", "{{PROP_006}}"),
    ]
    snapshots = [asset["payload"]["sourceSnapshot"] for asset in assets]
    assert [snapshot["coreDescription"] for snapshot in snapshots] == [
        "长方形黑色塑料周转笼，笼内有一只红棕色鸡。",
        "米黄色布麻袋，袋口露出绿色长葱叶。",
        "黑色蝴蝶结领结，中央有金色金属装饰。",
    ]
    assert [snapshot["sourcePropFacts"]["sourceName"] for snapshot in snapshots] == [asset["name"] for asset in assets]
    assert main.prop_source_target_category_errors(assets[0], {
        "localizedName": "Burlap Sack with Scallions",
        "coreDescription": "认亲母亲肩扛米黄色粗麻袋，袋口露出绿葱。",
        "visualDescription": "Burlap sack with scallions.",
    })
    assert main.prop_source_target_category_errors(assets[1], {
        "localizedName": "Black Plastic Chicken Crate",
        "coreDescription": "父亲抱着一只黑色塑料鸡笼，里面有红棕色鸡。",
        "visualDescription": "Black plastic chicken crate.",
    })
    assert main.prop_source_target_category_errors(assets[2], {
        "localizedName": "Crystal Bridal Necklace and Earrings",
        "coreDescription": "新娘佩戴钻饰项链和耳饰。",
        "visualDescription": "Crystal bridal necklace and earrings.",
    })
    composite = json.loads(json.dumps(assets[1], ensure_ascii=False))
    composite["payload"]["sourceSnapshot"]["sourcePropFacts"]["sourceName"] = "婚礼携带物"
    composite["payload"]["sourceSnapshot"]["sourcePropFacts"]["sourceDescription"] = "麻袋靠着黑色鸡笼放置。"
    assert main.prop_source_target_category_errors(composite, {
        "localizedName": "Scallion Satchel", "coreDescription": "A cloth satchel with scallions, placed beside a crate.",
    }) == []
    assert main.prop_source_target_category_errors(composite, {
        "localizedName": "Ceremonial Carry Bag", "coreDescription": "A locally appropriate carry bag with visible green leaves.",
    }) == []  # unknown valid synonym remains a review item, not a localization failure

    project = create_project(client, title="prop-source-bound-cache", remakeMode="cg_3d", targetCountry="美国", targetLanguage="英语")
    agent = {"mode": "volcengine", "model_name": "prop-cache", "temperature": 0, "output_format": "json"}
    key_before = main.asset_localization_cache_key(assets, project, agent)
    changed = json.loads(json.dumps(assets, ensure_ascii=False))
    changed[0]["payload"]["sourceSnapshot"]["sourcePropFacts"]["sourceDescription"] = "米黄色麻袋与绿色长葱。"
    changed[0]["payload"]["sourceSnapshot"]["coreDescription"] = "米黄色麻袋与绿色长葱。"
    key_after = main.asset_localization_cache_key(changed, project, agent)
    assert key_before != key_after

    empty = main.normalize_assets_from_agent({"资产库": {"道具资产": [
        {"道具编号": "PROP_EMPTY", "变量": "{{PROP_EMPTY}}", "原剧名称": "", "原剧描述": ""},
    ]}})
    monkeypatch.setattr(main, "call_compact_video_json", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("empty source prop must not be localized")))
    with main.connect() as conn:
        gate = main.run_target_localization(conn, empty, project, agent)
    assert gate["stage"] == "source_review"
    assert "原剧道具名称" in gate["errors"][0] and "原剧道具描述" in gate["errors"][0]
    assert gate["rawAttempts"] == []


def test_source_prop_aliases_keep_identity_keys_and_storyboard_references_real():
    assets = main.normalize_assets_from_agent({"资产库": {"道具资产": [
        {"道具编号": "PROP_003", "原剧名称": "矿泉水瓶", "原剧描述": "透明塑料矿泉水瓶，蓝色标签。"},
        {"道具编号": "PROP_007", "原剧名称": "篮球场边广告挡板", "原剧描述": "蓝色场边广告挡板。"},
    ]}})
    for asset in assets:
        main.annotate_project_asset_keys(asset, 1)
    main.ensure_unique_asset_display_variables(assets)
    assert [asset["display_variable"] for asset in assets] == ["@矿泉水瓶", "@篮球场边广告挡板"]
    assert [asset["payload"]["项目主资产Key"] for asset in assets] == ["道具:矿泉水瓶", "道具:篮球场边广告挡板"]
    assert len({asset["payload"]["项目主资产Key"] for asset in assets}) == 2

    old_to_display, _ = main.variable_maps(assets)
    boards = main.normalize_storyboards_from_agent({"分镜列表": [{
        "分镜编号": "分镜_001",
        "分镜时间范围": "00:00:00-00:00:02",
        "引用资产变量": ["@矿泉水瓶"],
        "分镜具体动作描述": "@矿泉水瓶被放在@篮球场边广告挡板前。",
    }]}, old_to_display)
    main.sanitize_storyboard_asset_references(boards, [], old_to_display, assets)
    payload = boards[0]["payload"]
    assert payload["引用资产变量"] == ["{{PROP_003}}", "{{PROP_007}}"]
    assert payload["引用资产显示变量"] == ["@矿泉水瓶", "@篮球场边广告挡板"]
    assert payload["资产引用待核对"] == []
    assert "@UnnamedProp" not in boards[0]["prompt"]


@pytest.mark.parametrize("ethnicity", ["Korean-American woman", "Japanese  American woman", "韩裔美国女性", "华裔与爱尔兰裔混血女性"])
def test_target_east_asian_ethnicity_requires_explicit_user_visual_lock(ethnicity: str):
    fixture = json.loads((Path(__file__).parent / "fixtures" / "legacy_source_alias_collisions.json").read_text(encoding="utf-8"))
    asset = next(item for item in main.normalize_assets_from_agent(fixture) if item["asset_type"] == "角色")
    structure = valid_role_visual_structure()
    structure["基础设定"]["族裔外貌"] = ethnicity
    structure["脸部特征"]["目标族裔背景"] = ethnicity
    asset["payload"]["localizedProfile"] = {
        "status": "complete", "coreDescription": ethnicity, "visualDescription": ethnicity,
        "structuredDescription": structure,
    }
    errors = main.localized_profile_validation_errors(asset, "美国")
    assert any("未提供用户族裔锁定" in error for error in errors)

    asset["payload"]["targetVisualLockProvenance"] = "user"
    asset["payload"]["目标生成描述"] = ethnicity + "，用户明确选择保留该族裔设定。"
    assert not any("未提供用户族裔锁定" in error for error in main.localized_profile_validation_errors(asset, "美国"))


def test_target_non_east_asian_ethnicity_is_not_rejected():
    fixture = json.loads((Path(__file__).parent / "fixtures" / "legacy_source_alias_collisions.json").read_text(encoding="utf-8"))
    asset = next(item for item in main.normalize_assets_from_agent(fixture) if item["asset_type"] == "角色")
    structure = valid_role_visual_structure()
    structure["基础设定"]["族裔外貌"] = "Mexican-American woman"
    structure["脸部特征"]["目标族裔背景"] = "Mexican-American woman"
    asset["payload"]["localizedProfile"] = {
        "status": "complete", "coreDescription": "Mexican-American woman with an original oval facial structure.",
        "visualDescription": "A locally designed Mexican-American woman.", "structuredDescription": structure,
    }
    assert not any("族裔锁定" in error for error in main.localized_profile_validation_errors(asset, "美国"))


def test_source_review_storyboard_uses_target_draft_without_source_frame_or_asset_sheet_prompt():
    project = {"target_country": "美国", "target_language": "英语", "remake_mode": "cg_3d"}
    payload = {
        "原剧核心形象描述": "源观察：短发女性，穿红色外套。",
        "原资产图路径": "C:/source/frame.png",
        "目标生成描述": "",
        "用户编辑资产图生成提示词": "A target woman. Four-view grid, front side back, white background. " + main.CG_ROLE_FIXED_TEMPLATE,
        "sourceSnapshot": {"coreDescription": "源观察：短发女性，穿红色外套。"},
        "localizedProfile": {
            "status": "source_review",
            "coreDescription": "A target-design woman with an original oval face and locally designed features.",
            "visualDescription": "A target-design woman with a tailored green coat.",
            "structuredDescription": {},
        },
    }
    payload["localizedProfile"]["targetContext"] = main.localized_profile_target_context(payload, project)
    asset = {"id": "asset-review", "asset_type": "角色", "name": "原片角色", "localized_name": "Lena Brooks", "variable": "{{CHAR_001}}", "payload": payload}
    implicit = main.resolve_effective_asset_reference(asset, project)
    assert implicit["effectiveAssetSource"] == "target_draft"
    assert implicit["description"] == payload["localizedProfile"]["coreDescription"]
    assert implicit["imagePath"] == ""
    assert implicit["ready"] is False
    assert "Four-view" not in implicit["description"] and "front side back" not in implicit["description"]

    uploaded = {
        "id": "image-user", "file_path": "C:/project/user-upload.png",
        "payload_json": json.dumps({"source": "user_upload"}),
    }
    selected = main.resolve_effective_asset_reference(asset, project, uploaded)
    assert selected["effectiveAssetSource"] == "target_draft"
    assert selected["imagePath"] == "C:/project/user-upload.png"
    assert selected["imageSource"] == "user_upload"
    assert selected["ready"] is False


def test_storyboard_reference_uses_static_role_appearance_not_temporary_wear_action_or_sheet_layout():
    fixture = json.loads((Path(__file__).parent / "fixtures" / "liam_dynamic_wardrobe_profile.json").read_text(encoding="utf-8"))["evidence"]
    project = {"target_country": "美国", "target_language": "英语", "remake_mode": "cg_3d"}
    source_clothing = fixture["sourceSnapshotClothing"]
    target_clothing = dict(fixture["localizedProfileClothing"])
    target_clothing["穿着状态"] = "整齐，后被掀开露出腹部"
    profile = {
        "status": "source_review",
        "coreDescription": fixture["localizedProfileCoreDescription"],
        "visualDescription": fixture["localizedProfileVisualDescription"],
        "structuredDescription": {
            "基础设定": {"性别": "男", "视觉年龄": "约25-35岁", "身份定位": "新郎", "族裔外貌": "白人（爱尔兰裔美国人）"},
            "脸部特征": {"脸型": "长方脸", "骨相": "轮廓清晰，下颌线利落", "眼形": "深眼窝长眼", "虹膜颜色": "浅蓝绿", "肤色": "浅白偏暖"},
            "发型": {"发型名称": "男士侧分短发", "发色": "深棕", "长度": "短", "刘海 / 分线": "侧分"},
            "服装": target_clothing,
        },
        "targetContext": {},
    }
    payload = {
        "sourceSnapshot": {"coreDescription": fixture["sourceSnapshotCoreDescription"], "sourceStructuredDescription": {"服装": source_clothing}},
        "原资产图路径": "C:/source/frame.jpg",
        "sourceFactReview": {"status": "reviewed", "fixedWornAccessories": [
            {"value": "左胸红色缎面胸花（礼服左胸佩戴）"},
            {"value": "黑色领结，中央金色几何金属饰件（不复制品牌标识）"},
        ]},
        "localizedProfile": profile,
    }
    profile["targetContext"] = main.localized_profile_target_context(payload, project)
    asset = {"id": fixture["assetId"], "asset_type": "角色", "name": "林浩", "localized_name": "Liam Hale", "variable": fixture["variable"], "payload": payload}
    reference = main.resolve_effective_asset_reference(asset, project)
    assert reference["effectiveAssetSource"] == "target_draft"
    assert reference["ready"] is False
    assert "脸型：长方脸" in reference["description"]
    assert "衣长：及臀" in reference["description"]
    assert "左胸红色缎面胸花" in reference["description"]
    assert "黑色领结" in reference["description"]
    assert "后被掀开露出腹部" not in reference["description"]
    assert "四视图" not in reference["description"] and "single-row grid" not in reference["description"]
    assert fixture["localizedProfileCoreDescription"] == profile["coreDescription"]
    assert profile["structuredDescription"]["服装"]["穿着状态"] == "整齐，后被掀开露出腹部"


def test_role_reference_keeps_user_appearance_edit_but_drops_asset_sheet_layout_and_style():
    profile = {
        "structuredDescription": {
            "基础设定": {"族裔外貌": "欧洲裔美国女性"},
            "脸部特征": {"脸型": "椭圆脸"},
            "发型": {"长度": "长发"},
            "服装": {"衣长": "及膝", "主色": "深蓝"},
        },
        "coreDescription": "椭圆脸的欧洲裔女性，深蓝连衣裙。",
    }
    payload = {
        "localizedProfile": profile,
        "用户编辑资产图生成提示词": (
            "【基础设定】人物外观\n【服装】主色深蓝\n"
            "[Style & Render] UE5 character asset sheet\n"
            "【用户指定目标外观覆盖】穿红色丝绒晚礼服，衣长及地。\n优先于同字段"
            "\n【共同展示构图】左侧面部特写，右侧前侧背四视图，白底。"
            "\n【目标视觉风格】三维CG\n固定约束 " + main.CG_ROLE_FIXED_TEMPLATE
        ),
    }
    description = main.localized_role_reference_appearance(payload, profile)
    assert "用户指定目标外观：穿红色丝绒晚礼服，衣长及地" in description
    assert "四视图" not in description
    assert "白底" not in description
    assert "Style & Render" not in description and "CG_ROLE_FIXED_TEMPLATE" not in description


def test_role_reference_extracts_only_changed_fields_from_user_edited_full_prompt():
    profile = {
        "structuredDescription": {
            "服装": {"主色": "白色", "外层": "白色抹胸蓬裙婚纱", "衣长": "长裙摆"},
            "发型": {"发色": "深黑棕", "长度": "长发盘起"},
        },
        "coreDescription": "白色婚纱，长发盘起。",
    }
    original = (
        "【基础设定】\n性别：女\n身份定位：新娘\n本地化视觉锚点：整齐，后被掀开露出腹部\n"
        "【发型】\n发色：深黑棕\n长度：长发盘起\n"
        "【服装】\n主色：红色\n外层：红色丝绒婚纱\n衣长：长裙摆\n"
        "【共同展示构图】左侧特写，右侧正面、侧面、背面四视图，白色干净背景。\n"
        "【目标视觉风格】高端3D角色资产图。\n固定约束 " + main.CG_ROLE_FIXED_TEMPLATE
    )
    payload = {"用户编辑资产图生成提示词": original}
    appearance = main.role_asset_prompt_user_appearance(original, profile)
    description = main.localized_role_reference_appearance(payload, profile)
    assert appearance == "服装.主色：红色；服装.外层：红色丝绒婚纱"
    assert "用户指定目标外观：服装.主色：红色；服装.外层：红色丝绒婚纱" in description
    assert "白色干净背景" not in description
    assert "四视图" not in description
    assert "高端3D角色资产图" not in description
    assert "衣长：长裙摆" in description
    assert "本地化视觉锚点" not in appearance and "后被掀开露出腹部" not in description
    unchanged = original.replace("主色：红色", "主色：白色").replace("外层：红色丝绒婚纱", "外层：白色抹胸蓬裙婚纱")
    assert main.role_asset_prompt_user_appearance(unchanged, profile) == ""


def test_real_wuyuehong_get_to_post_removes_only_resolved_shoe_lock_banner_and_archives_original(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    fixture = json.loads((Path(__file__).parent / "fixtures" / "wuyuehong_saved_manual_prompt_review_block.json").read_text(encoding="utf-8"))
    project = create_project(client, title="wuyuehong-resolved-review-banner", remakeMode="cg_3d", targetCountry="美国", targetLanguage="英语")
    profile = json.loads(json.dumps(fixture["localizedProfile"], ensure_ascii=False))
    profile["status"] = "complete"
    profile["sourceReviewPaths"] = []
    profile["nonBlockingSourceReviewPaths"] = ["服装.鞋子"]
    profile["sourceReviewNotes"] = ["鞋款仍未知；已确认婚纱长裙近地面且鞋面与脚踝不可见，生图时遮住鞋型。"]
    payload = {
        "sourceSnapshot": fixture["sourceSnapshot"],
        "localizedProfile": profile,
        "用户编辑资产图生成提示词": fixture["savedManualPrompt"],
        "当前子资产": {},
        "原剧核心形象描述": fixture["sourceDescription"],
    }
    profile["targetContext"] = main.localized_profile_target_context(payload, project)
    episode_id = "episode_wuyuehong_resolved_banner"
    with main.connect() as conn:
        conn.execute("INSERT INTO episodes (id,project_id,episode_index,filename,file_path,status,created_at) VALUES (?,?,?,?,?,?,?)", (episode_id, project["id"], 1, "source.mp4", "C:/fixture/source.mp4", "已上传", "2026-01-01T00:00:00Z"))
        main.insert_assets(conn, project["id"], episode_id, [{"asset_type": fixture["assetType"], "name": fixture["name"], "localized_name": fixture["localizedName"], "variable": fixture["variable"], "payload": payload}], status="待确认")
        asset_id = conn.execute("SELECT id FROM assets WHERE project_id=? AND episode_id=? AND variable=?", (project["id"], episode_id, fixture["variable"])).fetchone()[0]
    observed: list[str] = []

    class ProviderSpy:
        def generate_image(self, **kwargs):
            observed.append(kwargs["prompt"])
            image = io.BytesIO()
            Image.new("RGB", (4, 4), (30, 40, 50)).save(image, format="PNG")
            return {"image_bytes": image.getvalue(), "mime_type": "image/png", "size": kwargs["size"], "model": "provider-spy", "raw": {}}

    monkeypatch.setattr(main, "prepare_agent", lambda _key: {"mode": "volcengine", "model_name": "provider-spy", "prompt": "", "prompt_file": "test"})
    monkeypatch.setattr(main, "load_models_config", lambda: {"volcengine": {"imageSize": "2K"}})
    monkeypatch.setattr(main, "get_model_provider", lambda _config: ProviderSpy())
    loaded = client.get(f"/api/projects/{project['id']}/assets")
    assert loaded.status_code == 200, loaded.text
    loaded_asset = next(item for item in loaded.json()["assets"] if item["id"] == asset_id)
    final_prompt = loaded_asset["payload"]["最终资产图生成提示词"]
    original_manual = fixture["savedManualPrompt"]
    assert "【源事实待核对：自动生图已锁定】" not in final_prompt
    assert "服装.鞋子" not in final_prompt
    assert "固定约束" in final_prompt
    assert loaded_asset["payload"]["用户编辑资产图生成提示词"] == original_manual
    generated = client.post(f"/api/projects/{project['id']}/assets/{asset_id}/generate-image", json={"finalPrompt": final_prompt})
    assert generated.status_code == 200, generated.text
    assert len(observed) == 1
    assert observed[0] == generated.json()["asset"]["images"][0]["prompt"]
    assert "【源事实待核对：自动生图已锁定】" not in observed[0]
    persisted = client.get(f"/api/projects/{project['id']}/assets").json()["assets"]
    persisted_asset = next(item for item in persisted if item["id"] == asset_id)
    assert persisted_asset["payload"]["用户编辑资产图生成提示词"] == final_prompt
    archived = persisted_asset["payload"]["resolvedStatusPromptArchive"]
    assert archived and archived[-1]["prompt"] == original_manual


def test_asset_extraction_reconciles_exact_identity_without_losing_images_or_storyboard_refs(client: TestClient):
    project = create_project(client, title="asset-upsert-preserve")
    fixture = json.loads((Path(__file__).parent / "fixtures" / "legacy_source_alias_collisions.json").read_text(encoding="utf-8"))
    candidate = next(item for item in main.normalize_assets_from_agent(fixture) if item["asset_type"] == "角色")
    episode_id = "episode_upsert_1"
    with main.connect() as conn:
        conn.execute(
            "INSERT INTO episodes (id,project_id,episode_index,filename,file_path,status,created_at) VALUES (?,?,?,?,?,?,?)",
            (episode_id, project["id"], 1, "source.mp4", "C:/fixture/source.mp4", "已上传", "2026-01-01T00:00:00Z"),
        )
        candidate["payload"]["目标生成描述"] = "用户手工确认的目标外观。"
        candidate["payload"]["用户编辑资产图生成提示词"] = "用户手工生图提示词。"
        candidate["payload"]["targetVisualLockProvenance"] = "user_override"
        candidate["payload"]["localizedProfile"] = {"status": "complete", "localizedName": "Old target", "coreDescription": "Old target appearance."}
        candidate["localized_name"] = "Old target"
        candidate["payload"]["原资产图路径"] = "C:/fixture/original-frame.png"
        main.insert_assets(conn, project["id"], episode_id, [candidate], status="已确认")
        persisted = conn.execute("SELECT id,variable,payload_json FROM assets WHERE project_id=?", (project["id"],)).fetchone()
        stable_asset_id = persisted["id"]
        conn.execute(
            "INSERT INTO asset_images (id,project_id,asset_id,status,prompt,file_path,is_selected,payload_json,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            ("image_keep", project["id"], stable_asset_id, "成功", "selected", "C:/fixture/selected.png", 1, json.dumps({"source": "user_upload"}), "2026-01-01T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO storyboards (id,project_id,episode_id,title,time_range,prompt,payload_json,status,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            ("storyboard_keep", project["id"], episode_id, "shot", "00:00:01-00:00:02", candidate["variable"] + " action", json.dumps({"引用资产变量": [candidate["variable"]]}), "已生成", "2026-01-01T00:00:00Z"),
        )
        repeat = next(item for item in main.normalize_assets_from_agent(fixture) if item["asset_type"] == "角色")
        repeat["localized_name"] = "New target"
        repeat["payload"]["localizedProfile"] = {"status": "complete", "localizedName": "New target", "coreDescription": "New target appearance."}
        repeat["payload"]["原资产图路径"] = "C:/fixture/newly-captured-frame.png"
        result = main.reconcile_asset_extraction_rows(conn, project["id"], episode_id, [repeat])
        assert result["preservedExistingCount"] == 1 and result["newCandidateCount"] == 0
        assert repeat["_existingAssetId"] == stable_asset_id
        assert repeat["variable"] == persisted["variable"]
        main.insert_assets(conn, project["id"], episode_id, [repeat])
        stored = conn.execute("SELECT id,status,payload_json FROM assets WHERE id=?", (stable_asset_id,)).fetchone()
        assert stored["status"] == "已确认"
        stored_payload = json.loads(stored["payload_json"])
        assert stored_payload["目标生成描述"] == "用户手工确认的目标外观。"
        assert stored_payload["用户编辑资产图生成提示词"] == "用户手工生图提示词。"
        assert stored_payload["localizedProfile"]["coreDescription"] == "New target appearance."
        assert stored_payload["原资产图路径"] == "C:/fixture/original-frame.png"
        assert conn.execute("SELECT localized_name FROM assets WHERE id=?", (stable_asset_id,)).fetchone()[0] == "New target"
        assert conn.execute("SELECT id,is_selected FROM asset_images WHERE asset_id=?", (stable_asset_id,)).fetchone()["id"] == "image_keep"
        assert json.loads(conn.execute("SELECT payload_json FROM storyboards WHERE id='storyboard_keep'").fetchone()[0])["引用资产变量"] == [candidate["variable"]]

        changed = next(item for item in main.normalize_assets_from_agent(fixture) if item["asset_type"] == "角色")
        changed["payload"]["当前子资产"]["原剧视觉形象描述"] += "；新增可见伤疤。"
        changed["payload"]["sourceSnapshot"] = main.build_source_snapshot(changed)
        mismatch = main.reconcile_asset_extraction_rows(conn, project["id"], episode_id, [changed])
        assert mismatch["identityConflicts"]
        assert conn.execute("SELECT COUNT(*) FROM assets WHERE project_id=?", (project["id"],)).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM asset_images WHERE asset_id=?", (stable_asset_id,)).fetchone()[0] == 1

    derived = create_project(client, title="asset-upsert-derived-copy")
    with main.connect() as conn:
        conn.execute(
            "INSERT INTO episodes (id,project_id,episode_index,filename,file_path,status,created_at) VALUES (?,?,?,?,?,?,?)",
            ("episode_derived", derived["id"], 1, "source.mp4", "C:/fixture/source.mp4", "已上传", "2026-01-01T00:00:00Z"),
        )
        copied = next(item for item in main.normalize_assets_from_agent(fixture) if item["asset_type"] == "角色")
        copied["_existingAssetId"] = stable_asset_id  # foreign-project IDs must never suppress a derived row
        main.insert_assets(conn, derived["id"], "episode_derived", [copied])
        derived_row = conn.execute("SELECT id FROM assets WHERE project_id=?", (derived["id"],)).fetchone()
        assert derived_row and derived_row["id"] != stable_asset_id


def test_main_key_normalization_merges_camera_noise_and_scene_zones():
    def key(name: str, asset_type: str) -> str:
        return main.asset_project_main_key({"asset_type": asset_type, "name": name, "payload": {}})

    # 机位噪音（含纯机位括号注释）剥离后归并为同一主键
    assert key("电子记分牌（近景特写）", "道具") == key("电子记分牌", "道具")
    assert key("近景电子记分牌", "道具") == key("电子记分牌", "道具")
    # 场景空间分区后缀归并到同一物理场馆
    assert key("京北大学室内篮球馆替补席区域", "场景") == key("京北大学室内篮球馆", "场景")
    assert key("京北大学室内篮球馆场边/替补席区域", "场景") == key("京北大学室内篮球馆", "场景")
    assert key("京北大学室内篮球馆场边区域", "场景") == key("京北大学室内篮球馆", "场景")


def test_main_key_normalization_never_strips_state_variants_or_functional_words():
    def key(name: str, asset_type: str) -> str:
        return main.asset_project_main_key({"asset_type": asset_type, "name": name, "payload": {}})

    # 状态变体是子资产核心价值，永不合并
    assert key("大殿夜景", "场景") != key("大殿", "场景")
    assert key("大殿受损状态", "场景") != key("大殿", "场景")
    assert key("张三战斗服", "角色") != key("张三常服", "角色")
    assert key("张三战斗服", "角色") != key("张三", "角色")
    # 功能词保持拆分
    assert key("篮球架24秒计时牌", "道具") != key("电子记分牌", "道具")
    assert key("篮球馆场边电子广告屏/记分牌", "道具") != key("电子记分牌", "道具")
    # 括号内容含非机位词（位置/颜色）时整体保留
    assert key("电子记分牌（场边远端大屏）", "道具") != key("电子记分牌", "道具")
    assert key("红色数字电子记分牌", "道具") != key("电子记分牌", "道具")
    # 剥离为空时回退原文，防止错并
    assert key("全景", "道具") == "道具:全景"


def test_main_key_normalization_records_stripped_modifiers_for_audit():
    asset = {"asset_type": "场景", "name": "京北大学室内篮球馆替补席区域", "payload": {}}
    key = main.asset_project_main_key(asset)
    assert key == "场景:京北大学室内篮球馆"
    audit = asset["payload"]["归并规范化"]
    assert audit["规范化身份文本"] == "京北大学室内篮球馆"
    assert "替补席区域" in audit["剥离修饰"]
    clean = {"asset_type": "道具", "name": "矿泉水瓶", "payload": {}}
    main.asset_project_main_key(clean)
    assert "归并规范化" not in clean["payload"]


def test_reconcile_publishes_unambiguous_candidates_and_withholds_conflicts(client: TestClient):
    project = create_project(client, title="reconcile-per-asset-granularity")
    episode_id = "episode_partial_reconcile"
    with main.connect() as conn:
        conn.execute(
            "INSERT INTO episodes (id,project_id,episode_index,filename,file_path,status,created_at) VALUES (?,?,?,?,?,?,?)",
            (episode_id, project["id"], 1, "source.mp4", "C:/fixture/source.mp4", "已上传", "2026-01-01T00:00:00Z"),
        )
        existing = []
        for asset_id, variable, name in (
            ("asset_stable_1", "{{CHAR_001_SUB_01}}", "林浩"),
            ("asset_stable_2", "{{CHAR_002_SUB_01}}", "顾霜"),
            ("asset_stable_3", "{{PROP_001}}", "矿泉水瓶"),
            ("asset_conflict", "{{PROP_009}}", "旧记分牌"),
        ):
            payload = {
                "sourceSnapshot": {"name": name, "coreDescription": f"{name}的源事实。", "childId": variable.lstrip("{").rstrip("}")},
                "原剧核心形象描述": f"{name}的源事实。",
            }
            conn.execute(
                "INSERT INTO assets (id,project_id,episode_id,asset_type,name,localized_name,variable,status,payload_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (asset_id, project["id"], episode_id, "角色" if "CHAR" in variable else "道具", name, name, variable, "已确认", json.dumps(payload, ensure_ascii=False), "2026-01-01T00:00:00Z"),
            )
            existing.append((asset_id, variable, name))

        def candidate(variable: str, name: str, description: str) -> dict:
            asset_type = "角色" if "CHAR" in variable else "道具"
            payload = {
                "sourceSnapshot": {"name": name, "coreDescription": description, "childId": variable.lstrip("{").rstrip("}")},
                "原剧核心形象描述": description,
            }
            return {"asset_type": asset_type, "name": name, "localized_name": name, "variable": variable, "status": "待确认", "payload": payload}

        merged = [
            candidate("{{CHAR_001_SUB_01}}", "林浩", "林浩的源事实。"),
            candidate("{{CHAR_002_SUB_01}}", "顾霜", "顾霜的源事实。"),
            candidate("{{PROP_001}}", "矿泉水瓶", "矿泉水瓶的源事实。"),
        ]
        conflicted = candidate("{{PROP_009}}", "新记分牌", "全新记分牌，与旧资产身份不同。")
        fresh = candidate("{{PROP_100}}", "全新道具", "全新道具的源事实。")
        result = main.reconcile_asset_extraction_rows(conn, project["id"], episode_id, [*merged, conflicted, fresh])

        assert result["identityConflicts"] and result["identityConflicts"][0]["variable"] == "{{PROP_009}}"
        assert result["preservedExistingCount"] == 3
        assert result["withheldCount"] == 1
        assert result["newCandidateCount"] == 1
        assert conflicted.get("_withheld")
        assert [item["_existingAssetId"] for item in merged] == ["asset_stable_1", "asset_stable_2", "asset_stable_3"]
        review_payload = json.loads(conn.execute("SELECT payload_json FROM assets WHERE id='asset_conflict'").fetchone()[0])
        assert review_payload["身份核对"]["status"] == "待核对"
        stale_payload = json.loads(conn.execute("SELECT payload_json FROM assets WHERE id='asset_stable_1'").fetchone()[0])
        assert "assetSourceRefreshReview" not in stale_payload


def test_exact_source_cache_replay_keeps_reviewed_liam_accessories_from_prop006(client: TestClient):
    fixture = json.loads((Path(__file__).parent / "fixtures" / "liam_prop006_source_replay.json").read_text(encoding="utf-8"))
    replay_assets = main.normalize_reused_source_assets(fixture["assets"])
    liam = next(asset for asset in replay_assets if asset["variable"] == "{{CHAR_001_SUB_01}}")
    prop = next(asset for asset in replay_assets if asset["variable"] == "{{PROP_006}}")
    prop_evidence = prop["payload"]["原剧描述"]
    assert "佩戴在林浩白色衬衫领口" in prop_evidence

    project = create_project(client, title="liam-reviewed-wear-cache-replay", remakeMode="cg_3d", targetCountry="美国", targetLanguage="英语")
    episode_id = "episode-liam-cache-replay"
    reviewed = {
        "status": "reviewed",
        "fixedWornAccessories": [
            {"value": "左胸红色胸花（礼服左胸佩戴）", "evidenceVariable": "{{CHAR_001_SUB_01}}", "evidenceText": "新郎左胸佩戴红色胸花"},
            {"value": "黑色领结，中央为金色几何金属饰件（不复制品牌标识）", "evidenceVariable": "{{PROP_006}}", "evidenceText": prop_evidence},
        ],
    }
    old_payload = json.loads(json.dumps(liam["payload"], ensure_ascii=False))
    old_payload["sourceFactReview"] = reviewed
    old_payload.setdefault("当前子资产", {})["固定穿戴配饰"] = [item["value"] for item in reviewed["fixedWornAccessories"]]
    prior_id = "asset_liam-reviewed-cache-replay"
    with main.connect() as conn:
        conn.execute("INSERT INTO episodes (id,project_id,episode_index,filename,file_path,status,created_at) VALUES (?,?,?,?,?,?,?)", (episode_id, project["id"], 1, "source.mp4", "C:/missing/source.mp4", "已上传", "2026-10-28T00:00:00Z"))
        conn.execute("INSERT INTO assets (id,project_id,episode_id,asset_type,name,localized_name,variable,status,payload_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (prior_id, project["id"], episode_id, "角色", liam["name"], "Liam Hale", liam["variable"], "已确认", json.dumps(old_payload, ensure_ascii=False), "2026-10-28T00:00:00Z"))
        outcome = main.reconcile_asset_extraction_rows(conn, project["id"], episode_id, [liam])

    assert not outcome["identityConflicts"]
    assert liam["id"] == prior_id
    assert liam["payload"]["sourceFactReview"] == reviewed
    assert liam["payload"]["当前子资产"]["固定穿戴配饰"] == [item["value"] for item in reviewed["fixedWornAccessories"]]
    assert main.fixed_worn_accessory_observations(liam["payload"], liam["payload"]["当前子资产"], {}) == [item["value"] for item in reviewed["fixedWornAccessories"]]


def test_video_generation_nonmock_agent_fails_without_creating_placeholder_or_generation(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    project = create_project(client, title="video-not-connected")
    episode_id, _ = insert_source_records(project)
    storyboard_id = "storyboard_video_not_connected"
    with main.connect() as conn:
        conn.execute(
            "INSERT INTO storyboards (id,project_id,episode_id,title,time_range,prompt,payload_json,status,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (storyboard_id, project["id"], episode_id, "shot", "00:00:01-00:00:02", "A woman crosses the room.", json.dumps({"分镜具体动作描述": "A woman crosses the room."}), "已生成", "2026-01-01T00:00:00Z"),
        )
    monkeypatch.setattr(main, "prepare_agent", lambda key: {"mode": "volcengine", "model_name": "configured-video", "prompt": "", "prompt_file": "video"})
    response = client.post(f"/api/projects/{project['id']}/tasks/run", json={"taskType": "video_generation", "storyboardId": storyboard_id})
    assert response.status_code == 200
    task_id = response.json()["task"]["id"]
    detail = client.get(f"/api/tasks/{task_id}").json()["task"]
    assert detail["status"] == "失败"
    assert detail["result"]["error"] == "video_generation_not_implemented"
    assert "没有调用视频模型" in detail["message"]
    with main.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM video_generations WHERE project_id=?", (project["id"],)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM tasks WHERE project_id=? AND task_type='video_generation'", (project["id"],)).fetchone()[0] == 1
    output_dir = main.PROJECTS_DIR / project["id"] / "outputs" / "videos"
    assert not list(output_dir.glob("*.txt")) if output_dir.exists() else True


@pytest.mark.parametrize("remake_mode", ["cg_3d", "overseas_localization"])
def test_visual_style_does_not_disable_selected_target_localization(client: TestClient, remake_mode: str):
    project = create_project(client, title="cg-target-language", remakeMode=remake_mode, targetCountry="美国", targetLanguage="英语")
    storyboard = {"id": "shot-cg", "time_range": "00:00:01-00:00:02", "prompt": "背景中可见中文‘我们结婚’。", "payload": {"镜头列表": [{"背景": "墙面有中文‘我们结婚’标识", "人物站位": "新人站在门口"}]}}
    with main.connect() as conn:
        main.apply_storyboard_project_prompt(conn, project, storyboard)
    assert "目标国家（美国）和目标语言（英语）" in storyboard["prompt"]
    assert "画面类型只决定视觉呈现，不取消地域与语言适配" in storyboard["prompt"]
    assert "不进行地域本地化" not in storyboard["prompt"]
    assert "招牌、屏幕字和姓名条仅供语义与版式参考" in storyboard["prompt"]
    assert "我们结婚" in storyboard["prompt"]  # source observation stays intact; compiler carries target replacement rule


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
    assert "已观察衣件组合（层次未拆分，原文保留）" in profile["structuredDescription"]["服装"]["结构与廓形"]
    assert profile["sourceReviewPaths"]
    assert not main.localized_profile_validation_errors(asset, "美国")
    project = {"remake_mode": "overseas_localization", "target_country": "美国", "target_language": "英语", "target_era": "现代"}
    main.prepare_asset_prompt_fields(asset, project)
    assert asset["payload"]["目标画像状态"] == "待源事实核对"
    assert "源事实待核对" not in asset["payload"]["自动编译资产图生成提示词"]
    assert "已观察衣件组合（层次未拆分，原文保留）" in asset["payload"]["自动编译资产图生成提示词"]
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
    assert "已观察衣件组合（层次未拆分，原文保留）：内层白衬衫+中层灰马甲+外层蓝色外套" in normalized["服装"]["结构与廓形"]
    assert "已观察领袖组合（领型/袖型未拆分，原文保留）：旧合并领袖观察" in normalized["服装"]["结构与廓形"]
    assert "服装.结构与廓形" in review
    assert "服装.鞋面可见性" not in review


def test_prop_asset_image_prompt_uses_canonical_target_facts_over_stale_free_prompt():
    project = {
        "remake_mode": "overseas_localization", "target_country": "美国",
        "target_language": "英语", "source_language": "中文", "storyboard_visual_style": "cg_3d",
    }
    payload = {
        "资产类型": "道具", "原剧道具名称": "黑色塑料鸡笼",
        "原剧道具描述": "黑色长方形网格塑料周转笼，笼内有红棕色鸡。",
        "道具资产图生成提示词": "旧模型自由提示：麻布袋，袋口露出青葱。",
        "本地化道具描述": "旧缓存：Burlap sack with scallions.",
        "localizedProfile": {
            "schemaVersion": main.ASSET_LOCALIZATION_SCHEMA_VERSION,
            "status": "complete", "localizedName": "Black Plastic Chicken Crate",
            "coreDescription": "A black rectangular plastic poultry crate with grid openings and a reddish-brown chicken inside.",
            "visualDescription": "Black rectangular plastic poultry crate with grid openings.",
            "assetImagePrompt": "STALE FREE PROMPT: burlap sack with scallions, leafy greens spilling out.",
        },
    }
    payload["sourceSnapshot"] = main.build_source_snapshot({"asset_type": "道具", "name": "黑色塑料鸡笼", "variable": "{{PROP_002}}", "payload": payload})
    payload["localizedProfile"]["targetContext"] = main.localized_profile_target_context(payload, project)

    prompt = main.build_project_asset_prompt("道具", payload, project)

    assert "A black rectangular plastic poultry crate" in prompt
    assert "Burlap sack" not in prompt
    assert "scallions" not in prompt
    assert "STALE FREE PROMPT" not in prompt


def test_stored_liam_dynamic_wardrobe_is_filtered_at_automatic_get_final_prompt_boundary(client: TestClient):
    fixture = json.loads((Path(__file__).parent / "fixtures" / "liam_dynamic_wardrobe_profile.json").read_text(encoding="utf-8"))["evidence"]
    source_clothing = fixture["sourceSnapshotClothing"]
    target_clothing = fixture["localizedProfileClothing"]
    assert source_clothing["穿着状态"] == "整齐，后被掀开露出腹部"
    assert target_clothing["穿着状态"] == "整齐，后被掀开露出腹部"
    assert "后被掀开露出腹部" in fixture["localizedProfileCoreDescription"]
    assert "后被掀开露出腹部" in fixture["storedImagePrompt"]

    project = create_project(client, title="liam-real-wardrobe-evidence", remakeMode="cg_3d", targetCountry="美国", targetLanguage="英语", sourceLanguage="中文", targetEra="现代", storyboardVisualStyle="cg_3d")
    with main.connect() as conn:
        project_for_compile = main.get_project_or_404(conn, project["id"])
    structure = valid_role_visual_structure()
    structure["服装"].update(target_clothing)
    payload = {
        "资产类型": "角色", "剧情身份": "新郎", "本地化角色名称": "Liam Hale",
        "sourceSnapshot": {
            "schemaVersion": main.ASSET_SOURCE_SCHEMA_VERSION,
            "coreDescription": fixture["sourceSnapshotCoreDescription"],
            "sourceStructuredDescription": {"服装": source_clothing},
        },
        "localizedProfile": {
            "schemaVersion": main.ASSET_LOCALIZATION_SCHEMA_VERSION, "status": "complete",
            "localizedName": "Liam Hale", "coreDescription": fixture["localizedProfileCoreDescription"],
            "visualDescription": fixture["localizedProfileVisualDescription"], "structuredDescription": structure,
            "assetImagePrompt": fixture["storedImagePrompt"],
        },
        "当前子资产": {"角色资产图结构化描述": structure, "本地化视觉形象描述": fixture["localizedProfileVisualDescription"]},
    }
    payload["localizedProfile"]["targetContext"] = main.localized_profile_target_context(payload, project_for_compile)
    original_source_state = payload["sourceSnapshot"]["sourceStructuredDescription"]["服装"]["穿着状态"]
    original_target_state = payload["localizedProfile"]["structuredDescription"]["服装"]["穿着状态"]
    asset = {"id": fixture["assetId"], "asset_type": "角色", "name": "林浩", "localized_name": "Liam Hale", "variable": fixture["variable"], "payload": payload}
    with main.connect() as conn:
        conn.execute("INSERT INTO assets (id,project_id,episode_id,asset_type,name,localized_name,variable,status,payload_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (fixture["assetId"], project["id"], None, "角色", "林浩", "Liam Hale", fixture["variable"], "已确认", json.dumps(payload, ensure_ascii=False), "2026-10-28T00:00:00Z"))
    response = client.get(f"/api/projects/{project['id']}/assets")
    assert response.status_code == 200, response.text
    response_asset = next(item for item in response.json()["assets"] if item["id"] == fixture["assetId"])
    prompt = response_asset["payload"]["最终资产图生成提示词"]

    assert "衣长：及臀" in prompt
    assert "深蓝马甲" in prompt and "深蓝西装外套" in prompt and "深蓝西裤" in prompt
    assert "左胸佩戴红色胸花" in prompt
    assert "后被掀开露出腹部" not in prompt
    assert response_asset["payload"]["sourceSnapshot"]["sourceStructuredDescription"]["服装"]["穿着状态"] == original_source_state
    assert response_asset["payload"]["localizedProfile"]["structuredDescription"]["服装"]["穿着状态"] == original_target_state


def test_static_wear_state_keeps_short_top_and_distress_without_length_normalization():
    project = {"remake_mode": "overseas_localization", "target_country": "美国", "target_language": "英语", "source_language": "中文"}
    structure = valid_role_visual_structure(服装={
        "衣长": "短款上衣下摆位于腰线以上，明确露腰",
        "结构与廓形": "短款直身剪裁，原有下摆破损与泥污",
        "穿着状态": "下摆撕裂，袖口有泥污与磨损",
    })
    payload = {"资产类型": "角色", "剧情身份": "幸存者", "本地化角色名称": "Alex",
               "当前子资产": {"角色资产图结构化描述": structure, "固定穿戴配饰": []}}
    prompt = main.build_role_asset_image_prompt(payload, "美国", "英语", "live_action")
    assert "短款上衣下摆位于腰线以上，明确露腰" in prompt
    assert "短款直身剪裁，原有下摆破损与泥污" in prompt
    assert "下摆撕裂，袖口有泥污与磨损" in prompt


def test_unknown_shoes_are_nonblocking_only_when_explicit_hem_and_both_visibility_fields_cover_them():
    hidden = valid_role_visual_structure(服装={
        "衣长": "婚纱及地", "摆型": "连续长摆", "拖尾程度": "拖地",
        "摆与地面关系": "婚纱下摆及地并覆盖鞋履",
        "鞋子": "unknown", "鞋面可见性": "不可见，被婚纱遮挡", "脚踝可见性": "不可见，被婚纱遮挡",
    })
    assert main.footwear_hidden_by_garment(hidden)
    assert not any("服装.鞋子" in path for path in main.role_structured_missing_paths(hidden))
    prompt = main.build_role_asset_image_prompt({"资产类型": "角色", "当前子资产": {"角色资产图结构化描述": hidden}}, "美国", "英语", "live_action")
    assert "已确认长裙衣摆遮住鞋面与脚踝" in prompt and "不展示鞋型、鞋面或鞋尖" in prompt
    assert "鞋子：unknown" not in prompt

    source_structure = valid_role_visual_structure(服装=hidden["服装"])
    source_snapshot = {"sourceStructuredDescription": source_structure, "needsReview": ["服装.鞋子"]}
    assert "服装.鞋子" not in main.required_source_review_paths(hidden, source_snapshot)
    asset = {"asset_type": "角色", "variable": "{{BRIDE}}", "name": "新娘", "localized_name": "Bride", "payload": {"sourceSnapshot": source_snapshot, "当前子资产": {}}}
    target = {"基础设定": {"族裔外貌": "白人美国女性"}, "脸部特征": valid_role_visual_structure()["脸部特征"], "发型": valid_role_visual_structure()["发型"]}
    target["脸部特征"]["目标族裔背景"] = "白人美国女性"
    errors = main.merge_localized_profiles([asset], {"localizedAssets": [{"assetVariable": "{{BRIDE}}", "localizedProfile": {"localizedName": "Bride", "structuredDescription": target}}]}, "美国", "当代", "英语")
    profile = asset["payload"]["localizedProfile"]
    assert not errors
    assert profile["status"] == "complete"
    assert profile["sourceReviewPaths"] == []
    assert profile["nonBlockingSourceReviewPaths"] == ["服装.鞋子"]
    assert profile["sourceReviewNotes"]

    uncertain = valid_role_visual_structure(服装={
        "衣长": "unknown", "摆型": "unknown", "拖尾程度": "unknown", "摆与地面关系": "unknown",
        "鞋子": "unknown", "鞋面可见性": "清晰可见", "脚踝可见性": "可见",
    })
    assert not main.footwear_hidden_by_garment(uncertain)
    missing = main.role_structured_missing_paths(uncertain)
    assert any("服装.鞋子" in path for path in missing)
    assert any("服装.衣长" in path for path in missing)
    assert any("服装.摆型" in path for path in missing)
    assert any("服装.拖尾程度" in path for path in missing)
    assert any("服装.摆与地面关系" in path for path in missing)


def test_real_wuyuehong_long_skirt_hides_unknown_shoes_without_guessing_shoe_type():
    fixture = json.loads((Path(__file__).parent / "fixtures" / "wuyuehong_hidden_shoes_source_review.json").read_text(encoding="utf-8"))["evidence"]
    assert fixture["targetProfileStatus"] == "source_review"
    assert fixture["targetNeedsReview"] == ["服装.鞋子"]
    assert fixture["targetClothing"]["衣长"] == "长裙摆"
    assert fixture["targetClothing"]["摆与地面关系"] == "近地面"
    assert fixture["targetClothing"]["鞋面可见性"] == "不可见"
    assert fixture["targetClothing"]["脚踝可见性"] == "不可见"

    source_structure = valid_role_visual_structure(服装=fixture["sourceClothing"])
    source_snapshot = {"sourceStructuredDescription": source_structure, "needsReview": fixture["sourceNeedsReview"]}
    asset = {"asset_type": "角色", "variable": "{{BRIDE}}", "name": "吴月红", "localized_name": "Maya Carter", "payload": {"sourceSnapshot": source_snapshot, "当前子资产": {}}}
    target = {"基础设定": {"族裔外貌": "白人美国女性"}, "脸部特征": valid_role_visual_structure()["脸部特征"], "发型": valid_role_visual_structure()["发型"]}
    target["脸部特征"]["目标族裔背景"] = "白人美国女性"
    errors = main.merge_localized_profiles([asset], {"localizedAssets": [{"assetVariable": "{{BRIDE}}", "localizedProfile": {"localizedName": "Maya Carter", "structuredDescription": target}}]}, "美国", "当代", "英语")
    profile = asset["payload"]["localizedProfile"]
    assert not errors
    assert profile["status"] == "complete"
    assert profile["sourceReviewPaths"] == []
    assert profile["nonBlockingSourceReviewPaths"] == ["服装.鞋子"]
    prompt = main.build_project_asset_prompt("角色", asset["payload"], {"remake_mode": "overseas_localization", "target_country": "美国", "target_language": "英语", "storyboard_visual_style": "live_action"})
    assert "鞋面可见性：不可见" in prompt and "脚踝可见性：不可见" in prompt
    assert "鞋履被已确认长裙衣摆遮挡，源鞋款未知" in prompt
    assert "不展示鞋型、鞋面或鞋尖" in prompt
    assert "鞋子：unknown" not in prompt

    exposed = valid_role_visual_structure(服装={
        "衣长": "unknown", "摆型": "unknown", "拖尾程度": "unknown", "摆与地面关系": "unknown",
        "鞋子": "unknown", "鞋面可见性": "可见", "脚踝可见性": "可见",
    })
    assert not main.footwear_hidden_by_garment(exposed)
    assert any("服装.鞋子" in path for path in main.role_structured_missing_paths(exposed))


def test_unobservable_lower_body_unknowns_do_not_lock_automatic_asset_prompt():
    structure = valid_role_visual_structure(服装={
        "衣长": "unknown", "摆型": "unknown", "拖尾程度": "unknown", "摆与地面关系": "unknown",
        "鞋子": "unknown", "鞋面可见性": "不可见", "脚踝可见性": "不可见",
        "外层": "浅香槟色带领伴娘裙上身", "下装": "同色裙装",
    })
    review_paths = ["服装.衣长", "服装.摆型", "服装.拖尾程度", "服装.摆与地面关系", "服装.鞋子"]
    snapshot = {"needsReview": review_paths, "sourceStructuredDescription": structure}
    payload = {
        "sourceSnapshot": snapshot,
        "localizedProfile": {
            "status": "source_review", "localizedName": "Chloe Bennett",
            "localizedChildName": "Chloe Bennett Champagne Bridesmaid Look",
            "coreDescription": "A Mexican American bridesmaid with a target-localized face.",
            "visualDescription": "A champagne bridesmaid dress with a red accent.",
            "structuredDescription": structure,
            "sourceReviewPaths": review_paths, "needsReview": review_paths,
        },
        "当前子资产": {"角色资产图结构化描述": structure},
    }

    main.refresh_role_source_review_profile(payload)
    profile = payload["localizedProfile"]
    assert profile["status"] == "complete"
    assert profile["sourceReviewPaths"] == []
    assert profile["nonBlockingSourceReviewPaths"] == review_paths
    assert all(path not in main.role_structured_missing_paths(structure) for path in review_paths)

    prompt = main.build_project_asset_prompt("角色", payload, {
        "remake_mode": "cg_3d", "target_country": "美国", "target_language": "英语",
        "storyboard_visual_style": "cg_3d",
    })
    assert "源画面未观察到的下半身细节" in prompt
    assert "不得把推测写成原片事实" in prompt
    assert "unknown" not in prompt.lower()
    assert main.CG_ROLE_FIXED_TEMPLATE in prompt


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
    assert "源事实待核对" not in auto
    assert loaded_asset["payload"]["目标画像状态"] == "待源事实核对"
    generated_auto = client.post(f"/api/projects/{project['id']}/assets/{asset_id}/generate-image", json={"finalPrompt": auto})
    assert generated_auto.status_code == 200, generated_auto.text
    assert len(observed) == 1
    manual = "完整自包含角色设定：成年墨西哥裔美国男性，深棕短发，穿合身深蓝西装、白衬衫与长裤，鞋面与袖口清晰设定，白色背景资产展示。"
    accepted = client.post(f"/api/projects/{project['id']}/assets/{asset_id}/generate-image", json={"finalPrompt": manual})
    assert accepted.status_code == 200, accepted.text
    assert len(observed) == 2
    assert observed[0].count(main.CG_ROLE_FIXED_TEMPLATE) == 1
    assert observed[1].count(main.CG_ROLE_FIXED_TEMPLATE) == 1


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
    generated_after_restore = client.post(f"/api/projects/{project['id']}/assets/{asset_id}/generate-image", json={})
    assert generated_after_restore.status_code == 200, generated_after_restore.text
    switched_off_again = client.patch(f"/api/projects/{project['id']}", json={"replaceCharacters": False})
    assert switched_off_again.status_code == 200, switched_off_again.text
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
    assert "分镜时长目标为 10 秒左右" in config_prompt
    assert "不得把每个切镜单独作为一个分镜输出" in config_prompt
    assert "机位与构图" in config_prompt and "人物站位" in config_prompt and "台词与声音" in config_prompt
    assert "仅在输入明确提供白模素材时启用" in config_prompt
    assert "以下样例仅展示字段排列与格式" in config_prompt
    assert "目标国家与目标语言由项目输入独立决定" in config_prompt
    assert "`cg_3d`和`asset_replacement`不改变这一路由" in config_prompt

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
    assert all("片段说明" not in board["payload"] for board in normalized)
    assert all(board["payload"]["全局片段说明"] == root_summary for board in normalized)

    first = normalized[0]
    first_payload = first["payload"]
    first_prompt = main.format_storyboard_prompt(first["time_range"], first_payload, first["prompt"], ["@林浩-常服"], [])
    assert "夜间办公室" not in first_prompt
    assert "1.5秒" in first_prompt
    assert "[机位与构图]" in first_prompt and "[人物站位]" in first_prompt and "[台词与声音]" in first_prompt
    assert "\\n" not in first_prompt

    second = normalized[1]
    second_prompt = main.format_storyboard_prompt(second["time_range"], second["payload"], second["prompt"], ["@林浩-常服"], [])
    assert "@林浩-常服向门外走出一步。" in second_prompt
    assert "旧记录的第二条具体描述仍保留。" in second_prompt
    assert "夜间办公室" not in second_prompt


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
    assert "片段说明：" not in prompt
    assert "[机位与构图] 近景，视线高度" in prompt
    assert "[背景] 走廊墙面" in prompt
    assert "[人物站位] 角色在画面中央" in prompt
    assert "分镜01｜00:00:00.0-00:00:01.5｜1.5秒" in prompt
    assert "[动作过程] 抬手敲门" in prompt


def test_storyboard_refs_ignore_coarse_index_and_global_summary_and_store_stable_variables():
    assets = [
        {"id": "asset-bride", "asset_type": "角色", "name": "新娘", "localized_name": "Bride", "variable": "{{CHAR_BRIDE}}", "display_variable": "@Bride"},
        {"id": "asset-groom", "asset_type": "角色", "name": "新郎", "localized_name": "Groom", "variable": "{{CHAR_GROOM}}", "display_variable": "@Groom"},
        {"id": "asset-extra", "asset_type": "角色", "name": "母亲", "localized_name": "Mother", "variable": "{{CHAR_MOTHER}}", "display_variable": "@Mother"},
        {"id": "scene-one", "asset_type": "场景", "name": "主舞台", "localized_name": "Wedding Stage", "variable": "{{SCENE_STAGE}}", "display_variable": "@WeddingStage"},
        {"id": "scene-two", "asset_type": "场景", "name": "宴席区", "localized_name": "Reception", "variable": "{{SCENE_RECEPTION}}", "display_variable": "@Reception"},
    ]
    storyboard = {
        "title": "opening",
        "time_range": "00:00:01-00:00:03",
        "prompt": "新娘与新郎站在婚礼主舞台前。",
        "payload": {
            "全局片段说明": {"场景": "@WeddingStage；@Reception", "角色": "@Bride；@Groom；@Mother"},
            "引用资产变量": ["{{CHAR_BRIDE}}", "{{CHAR_GROOM}}"],
            "镜头列表": [{"人物站位": "@Bride与@Groom并肩站在@WeddingStage前", "背景": "舞台灯光"}],
        },
    }
    coarse_index = [{"开始时间": "00:00:00", "结束时间": "00:00:10", "出现资产": [
        {"资产变量": "{{CHAR_MOTHER}}", "资产类型": "角色", "展示资产变量": "@Mother"},
        {"资产变量": "{{SCENE_RECEPTION}}", "资产类型": "场景", "展示资产变量": "@Reception"},
    ]}]
    main.sanitize_storyboard_asset_references([storyboard], coarse_index, {}, assets)
    payload = storyboard["payload"]
    assert payload["引用资产变量"] == ["{{CHAR_BRIDE}}", "{{CHAR_GROOM}}", "{{SCENE_STAGE}}"]
    assert payload["引用资产显示变量"] == ["@Bride", "@Groom", "@WeddingStage"]
    assert payload["资产映射校验"]["索引可用于过滤"] is False
    assert "@Mother" not in storyboard["prompt"] and "@Reception" not in storyboard["prompt"]
    assert "@WeddingStage" in storyboard["prompt"]
    assert "出镜角色：@Bride、@Groom" in storyboard["prompt"]


def test_storyboard_reference_resolution_is_episode_scoped(client: TestClient):
    project = create_project(client, title="episode-scoped-storyboard-assets")
    with main.connect() as conn:
        for episode_id, index in (("episode_one", 1), ("episode_two", 2)):
            conn.execute(
                "INSERT INTO episodes (id,project_id,episode_index,filename,file_path,status,created_at) VALUES (?,?,?,?,?,?,?)",
                (episode_id, project["id"], index, f"{episode_id}.mp4", f"C:/test/{episode_id}.mp4", "已上传", "2026-01-01T00:00:00Z"),
            )
        for asset_id, episode_id, name, localized_name, display in (
            ("asset_ep1_prop", "episode_one", "第一集矿泉水瓶", "Bottle", "@矿泉水瓶"),
            ("asset_ep2_prop", "episode_two", "第二集篮球", "Basketball", "@篮球"),
        ):
            payload = {
                "资产类型": "道具",
                "原剧道具名称": name,
                "原剧道具描述": f"{name}的清晰外观。",
                "展示资产变量": display,
                "sourceSnapshot": {"name": name, "coreDescription": f"{name}的清晰外观。"},
                "localizedProfile": {"status": "complete", "localizedName": localized_name, "coreDescription": f"Target {name}."},
            }
            conn.execute(
                "INSERT INTO assets (id,project_id,episode_id,asset_type,name,localized_name,variable,status,payload_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (asset_id, project["id"], episode_id, "道具", name, localized_name, "{{PROP_001}}", "已确认", json.dumps(payload, ensure_ascii=False), "2026-01-01T00:00:00Z"),
            )
        storyboard = {
            "id": "storyboard_ep1",
            "episode_id": "episode_one",
            "time_range": "00:00:00-00:00:02",
            "prompt": "{{PROP_001}}被放在场边。",
            "payload": {
                "源分镜文本": "{{PROP_001}}被放在场边。",
                "分镜生成正文": "{{PROP_001}}被放在场边。",
                "引用资产变量": ["{{PROP_001}}"],
            },
        }
        main.apply_storyboard_project_prompt(conn, project, storyboard)

    payload = storyboard["payload"]
    assert payload["资产引用待核对"] == []
    assert [item["projectAssetId"] for item in payload["目标资产映射快照"]] == ["asset_ep1_prop"]
    assert "第一集矿泉水瓶" not in storyboard["prompt"] and "第二集篮球" not in storyboard["prompt"]
    assert "{{PROP_001}}=Bottle" in storyboard["prompt"]
    assert "Basketball" not in storyboard["prompt"]


def test_generated_storyboards_resolve_references_within_episode_without_ambiguity(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # 生成路径回归：两集复用同一稳定变量与展示变量时，run_storyboard_generation
    # 入库的分镜不得因缺 episode_id 退化为项目级匹配而产生假歧义（第 6 集真实缺陷）。
    project = create_project(client, title="generated-episode-scoped-refs")
    video_path = tmp_path / "generated-refs.mp4"
    video_path.write_bytes(b"isolated video fixture")
    display = "@篮球场观众群像-篮球场观众群像看台观赛造型"
    with main.connect() as conn:
        for episode_id, index, asset_id, name in (
            ("episode_refs_one", 1, "asset_refs_ep1", "第一集观众群像"),
            ("episode_refs_two", 2, "asset_refs_ep2", "第二集观众群像"),
        ):
            conn.execute(
                "INSERT INTO episodes (id,project_id,episode_index,filename,file_path,status,created_at,ark_file_id,ark_file_status) VALUES (?,?,?,?,?,?,?,?,?)",
                (episode_id, project["id"], index, f"{episode_id}.mp4", str(video_path), "已上传并同步 Ark", "2026-01-01T00:00:00Z", f"ark-{episode_id}", "active"),
            )
            payload = {
                "资产类型": "角色",
                "原剧角色名称": name,
                "原剧角色描述": f"{name}的群像外观。",
                "展示资产变量": display,
                "sourceSnapshot": {"name": name, "coreDescription": f"{name}的群像外观。"},
                "localizedProfile": {"status": "complete", "localizedName": "Crowd", "coreDescription": "Target crowd."},
            }
            conn.execute(
                "INSERT INTO assets (id,project_id,episode_id,asset_type,name,localized_name,variable,status,payload_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (asset_id, project["id"], episode_id, "角色", name, "Crowd", "{{CHAR_001_SUB_01}}", "已确认", json.dumps(payload, ensure_ascii=False), "2026-01-01T00:00:00Z"),
            )
        conn.execute(
            "INSERT INTO tasks (id,project_id,episode_id,task_type,status,message,result_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            ("task_refs_asset_index", project["id"], "episode_refs_two", "asset_extraction", "成功", "资产提取完成", json.dumps({"资产出现片段索引": [{"片段编号": "片段_001", "开始时间": "00:00:00", "结束时间": "00:00:11", "出现资产变量": [display]}]}, ensure_ascii=False), "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
    model_output = json.dumps({
        "片段说明": {"场景": display, "分镜数量": 1, "目标风格": "按项目设置", "参考素材分工": "视频提供镜头事实", "成片语言与字幕": "保留原话", "共用要求": "无水印"},
        "分镜列表": [{
            "分镜编号": "分镜_001",
            "分镜时间范围": {"开始时间": "00:00:00", "结束时间": "00:00:11"},
            "引用资产变量": ["{{CHAR_001_SUB_01}}"],
            "镜头列表": [{
                "时间范围": {"开始时间": "00:00:00", "结束时间": "00:00:11"},
                "机位与构图": {"角度": "平视", "景别": "全景", "主体构图": f"{display}位于看台"},
                "背景": {"可见环境": "篮球馆看台"},
                "人物站位": [{"资产变量": "{{CHAR_001_SUB_01}}", "画面左右": "中央"}],
                "动作过程": {"起始": "坐着观赛", "变化": "鼓掌"},
                "运镜": {"方式": "固定"},
                "台词与声音": [{"原话": "待确认", "环境音": "观众声"}],
            }],
            "最终视频生成提示词": f"{display}坐在看台观赛并鼓掌。",
        }],
    }, ensure_ascii=False)
    monkeypatch.setattr(main, "prepare_agent", lambda _key: {"mode": "volcengine", "model_name": "provider-spy", "prompt": "video storyboard", "prompt_file": "test"})
    monkeypatch.setattr(main, "call_agent_chat", lambda *_args: model_output)
    with main.connect() as conn:
        db_project = main.get_project_or_404(conn, project["id"])
        task = main.run_storyboard_generation(conn, db_project, "episode_refs_two", force=True)
        conn.commit()
        rows = conn.execute("SELECT payload_json FROM storyboards WHERE project_id=? AND episode_id=?", (project["id"], "episode_refs_two")).fetchall()
    assert task["status"] == "成功", task["message"]
    assert len(rows) == 1
    payload = json.loads(rows[0]["payload_json"])
    assert payload["资产引用待核对"] == []
    assert [item["projectAssetId"] for item in payload["目标资产映射快照"]] == ["asset_refs_ep2"]


def test_manual_reference_assets_do_not_all_become_on_screen_cast_or_scene():
    assets = [
        {"id": "bride", "asset_type": "角色", "name": "新娘", "localized_name": "Bride", "variable": "{{CHAR_BRIDE}}", "display_variable": "@Bride"},
        {"id": "groom", "asset_type": "角色", "name": "新郎", "localized_name": "Groom", "variable": "{{CHAR_GROOM}}", "display_variable": "@Groom"},
        {"id": "mother", "asset_type": "角色", "name": "母亲", "localized_name": "Mother", "variable": "{{CHAR_MOTHER}}", "display_variable": "@Mother"},
        {"id": "stage", "asset_type": "场景", "name": "主舞台", "localized_name": "Wedding Stage", "variable": "{{SCENE_STAGE}}", "display_variable": "@WeddingStage"},
        {"id": "prop", "asset_type": "道具", "name": "鸡笼", "localized_name": "Chicken Crate", "variable": "{{PROP_002}}", "display_variable": "@ChickenCrate"},
    ]
    refs = ["{{CHAR_BRIDE}}", "{{CHAR_GROOM}}", "{{CHAR_MOTHER}}", "{{SCENE_STAGE}}", "{{PROP_002}}"]
    storyboard = {
        "title": "opening", "time_range": "00:00:01-00:00:03", "prompt": "新娘与新郎站在主舞台。",
        "payload": {"引用资产变量": refs, "引用资产变量手动覆盖": True,
                    "镜头列表": [{"人物站位": "新娘与新郎并肩站在门口", "背景": "婚礼主舞台背景"}]},
    }
    main.sanitize_storyboard_asset_references([storyboard], [], {}, assets)

    assert storyboard["payload"]["引用资产变量"] == refs
    assert "出镜角色：@Bride、@Groom" in storyboard["prompt"]
    assert "@Mother" not in storyboard["prompt"].split("出镜角色：", 1)[1].splitlines()[0]
    assert "场景：@WeddingStage" in storyboard["prompt"]
    assert "参考资产（被引用不表示全部出现在画面中）：@ChickenCrate" in storyboard["prompt"]


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


def test_video_generation_blocks_ambiguous_asset_reference_before_task_creation(client: TestClient):
    project = create_project(client, title="storyboard-ambiguous-reference-guard")
    storyboard_id = "storyboard_ambiguous_prop_reference"
    with main.connect() as conn:
        conn.execute(
            "INSERT INTO storyboards (id,project_id,episode_id,title,time_range,prompt,payload_json,status,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                storyboard_id,
                project["id"],
                "episode_test",
                storyboard_id,
                "00:00:00-00:00:01",
                "胸前佩戴@UnnamedProp胸花。",
                json.dumps({"分镜生成正文": "胸前佩戴@UnnamedProp胸花。", "资产引用待核对": [{"token": "@UnnamedProp", "candidateVariables": ["{{PROP_001}}", "{{PROP_002}}"], "reason": "歧义"}]}, ensure_ascii=False),
                "待分析",
                "2026-10-27T00:00:00Z",
            ),
        )
    tasks_before = len(client.get(f"/api/projects/{project['id']}/tasks").json()["tasks"])
    rejected = client.post(f"/api/projects/{project['id']}/tasks/run", json={"taskType": "video_generation", "storyboardId": storyboard_id})
    assert rejected.status_code == 422
    assert "资产引用待核对" in rejected.json()["detail"]
    assert "@UnnamedProp" in rejected.json()["detail"]
    assert len(client.get(f"/api/projects/{project['id']}/tasks").json()["tasks"]) == tasks_before
    assert client.get(f"/api/projects/{project['id']}/video-generations").json()["videoGenerations"] == []


def _insert_reference_guard_assets(project_id: str, *, duplicate_alias: bool = False) -> None:
    with main.connect() as conn:
        for index in range(1, 3 if duplicate_alias else 2):
            token = "@UnnamedProp" if duplicate_alias else "@RoseBoutonniere"
            payload = {"源变量别名": token, "展示资产变量": token}
            conn.execute(
                "INSERT INTO assets (id,project_id,episode_id,asset_type,name,localized_name,variable,status,payload_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    f"asset_reference_guard_{index}", project_id, "episode_test", "道具",
                    f"道具{index}", f"Prop {index}", f"{{{{PROP_{index:03d}}}}}", "已确认",
                    json.dumps(payload, ensure_ascii=False), f"2026-01-01T00:00:0{index}Z",
                ),
            )


def _insert_reference_guard_storyboard(project_id: str, storyboard_id: str, body: str, refs: list[str], stale_review: list[dict]) -> None:
    with main.connect() as conn:
        conn.execute(
            "INSERT INTO storyboards (id,project_id,episode_id,title,time_range,prompt,payload_json,status,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                storyboard_id, project_id, "episode_test", storyboard_id, "00:00:00-00:00:01", body,
                json.dumps({"源分镜文本": body, "分镜生成正文": body, "引用资产变量": refs, "资产引用待核对": stale_review}, ensure_ascii=False),
                "待分析", "2026-01-01T00:00:00Z",
            ),
        )


def test_stale_asset_reference_review_does_not_block_canonical_storyboard(client: TestClient):
    project = create_project(client, title="stale-reference-review-clears")
    _insert_reference_guard_assets(project["id"])
    storyboard_id = "storyboard_stale_reference_review"
    stale = [{"token": "@OldUnnamedProp", "candidateVariables": ["{{PROP_001}}", "{{PROP_002}}"], "reason": "旧歧义记录"}]
    _insert_reference_guard_storyboard(project["id"], storyboard_id, "胸前佩戴{{PROP_001}}红花胸花。", ["{{PROP_001}}"], stale)

    response = client.get(f"/api/projects/{project['id']}/storyboards")
    assert response.status_code == 200, response.text
    storyboard = next(item for item in response.json()["storyboards"] if item["id"] == storyboard_id)
    assert storyboard["payload"]["资产引用待核对"] == []
    assert storyboard["payload"]["历史资产引用待核对"] == stale

    tasks_before = len(client.get(f"/api/projects/{project['id']}/tasks").json()["tasks"])
    with main.connect() as conn:
        preflight = main.get_video_generation_storyboard(conn, project["id"], storyboard_id)
    assert preflight["payload"]["资产引用待核对"] == []
    assert len(client.get(f"/api/projects/{project['id']}/tasks").json()["tasks"]) == tasks_before


def test_ambiguous_current_reference_blocks_until_patch_uses_canonical_asset(client: TestClient):
    project = create_project(client, title="ambiguous-reference-patch-clears")
    _insert_reference_guard_assets(project["id"], duplicate_alias=True)
    storyboard_id = "storyboard_current_ambiguous_reference"
    old_review = [{"token": "@UnnamedProp", "candidateVariables": ["{{PROP_001}}", "{{PROP_002}}"], "reason": "旧歧义记录"}]
    _insert_reference_guard_storyboard(project["id"], storyboard_id, "胸前佩戴@UnnamedProp胸花。", ["{{PROP_001}}"], old_review)

    tasks_before = len(client.get(f"/api/projects/{project['id']}/tasks").json()["tasks"])
    rejected = client.post(f"/api/projects/{project['id']}/tasks/run", json={"taskType": "video_generation", "storyboardId": storyboard_id})
    assert rejected.status_code == 422
    assert "@UnnamedProp" in rejected.json()["detail"]
    assert len(client.get(f"/api/projects/{project['id']}/tasks").json()["tasks"]) == tasks_before

    patched = client.patch(
        f"/api/projects/{project['id']}/storyboards/{storyboard_id}",
        json={"generatedBody": "胸前佩戴{{PROP_001}}红花胸花。", "payload": {"引用资产变量": ["{{PROP_001}}"]}},
    )
    assert patched.status_code == 200, patched.text
    patched_payload = patched.json()["storyboard"]["payload"]
    assert patched_payload["资产引用待核对"] == []
    assert patched_payload["历史资产引用待核对"] == old_review

    with main.connect() as conn:
        preflight = main.get_video_generation_storyboard(conn, project["id"], storyboard_id)
    assert preflight["payload"]["资产引用待核对"] == []
    assert len(client.get(f"/api/projects/{project['id']}/tasks").json()["tasks"]) == tasks_before


def test_asset_get_projects_matching_localized_profile_over_stale_legacy_targets(client: TestClient):
    project = create_project(
        client, title="asset-get-canonical-profile-projection", remakeMode="cg_3d",
        sourceLanguage="中文", targetCountry="美国", targetLanguage="英语",
    )
    role_source = {
        "schemaVersion": main.ASSET_SOURCE_SCHEMA_VERSION,
        "coreDescription": "源观察：婚礼宾客，深色长发。",
        "visualDescription": "源观察保留；不改族裔原文。",
        "childId": "CHAR_006_SUB_01",
        "sourceStructuredDescription": {},
    }
    role_structure = valid_role_visual_structure()
    role_structure["基础设定"]["族裔外貌"] = "欧洲裔美国女性"
    role_structure["脸部特征"]["目标族裔背景"] = "欧洲裔美国女性"
    role_payload = {
        "资产类型": "角色", "原剧角色名称": "夏一曼",
        "本地化核心形象描述": "旧字段：韩裔美国人，Lena Park。",
        "目标生成描述": "", "sourceSnapshot": role_source,
        "localizedProfile": {
            "schemaVersion": main.ASSET_LOCALIZATION_SCHEMA_VERSION, "status": "complete",
            "localizedName": "Lena Brooks", "localizedChildName": "Lena Brooks",
            "coreDescription": "欧洲裔美国女性，明确的椭圆脸骨相与杏仁眼。",
            "visualDescription": "欧洲裔美国女性，长发半扎，浅香槟色伴娘礼服。",
            "structuredDescription": role_structure, "childId": "CHAR_006_SUB_01",
            "assetImagePrompt": "",
        },
        "当前子资产": {
            "子资产编号": "CHAR_006_SUB_01", "本地化子资产名称": "旧名 Lena Park",
            "本地化视觉形象描述": "旧字段：韩裔美国人，旧脸部画像。",
        },
        "子资产": [{
            "子资产编号": "CHAR_006_SUB_01", "原剧子资产名称": "源服装事实保留",
            "本地化子资产名称": "旧镜像 Lena Park",
            "本地化视觉形象描述": "旧镜像：韩裔美国人。",
        }],
    }
    role_payload["localizedProfile"]["targetContext"] = main.localized_profile_target_context(role_payload, project)

    prop_source = {
        "schemaVersion": main.ASSET_SOURCE_SCHEMA_VERSION,
        "coreDescription": "长方形黑色塑料鸡笼，网格镂空，笼内有红棕色鸡。",
        "visualDescription": "源道具原始事实。", "childId": "", "sourceStructuredDescription": {},
    }
    prop_payload = {
        "资产类型": "道具", "原剧名称": "黑色塑料鸡笼", "原剧描述": prop_source["coreDescription"],
        "本地化道具描述": "旧字段：米黄色麻袋与绿葱。", "目标生成描述": "",
        "sourceSnapshot": prop_source,
        "localizedProfile": {
            "schemaVersion": main.ASSET_LOCALIZATION_SCHEMA_VERSION, "status": "complete",
            "localizedName": "Black Plastic Poultry Crate", "localizedChildName": "Black Plastic Poultry Crate",
            "coreDescription": "黑色长方形塑料周转笼，网格镂空结构，笼内有一只红棕色鸡。",
            "visualDescription": "黑色长方形塑料周转笼，网格镂空结构，笼内有一只红棕色鸡。",
            "structuredDescription": {}, "childId": "", "assetImagePrompt": "",
        },
        "当前子资产": {"本地化子资产名称": "Darlene's Burlap Sack with Green Onions", "本地化视觉形象描述": "米黄色麻袋和绿葱。"},
    }
    prop_payload["localizedProfile"]["targetContext"] = main.localized_profile_target_context(prop_payload, project)

    with main.connect() as conn:
        conn.execute(
            "INSERT INTO assets (id,project_id,episode_id,asset_type,name,localized_name,variable,status,payload_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("asset_05380f960822", project["id"], None, "角色", "夏一曼", "Lena Park", "{{CHAR_006_SUB_01}}", "待确认", json.dumps(role_payload, ensure_ascii=False), "2026-10-28T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO assets (id,project_id,episode_id,asset_type,name,localized_name,variable,status,payload_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("asset_4dfcaab24953", project["id"], None, "道具", "黑色塑料鸡笼", "Darlene's Burlap Sack with Green Onions", "{{PROP_002}}", "待确认", json.dumps(prop_payload, ensure_ascii=False), "2026-10-28T00:00:01Z"),
        )

    response = client.get(f"/api/projects/{project['id']}/assets")
    assert response.status_code == 200, response.text
    by_id = {asset["id"]: asset for asset in response.json()["assets"]}
    role = by_id["asset_05380f960822"]
    role_payload_after = role["payload"]
    assert role["localized_name"] == "Lena Brooks"
    assert role_payload_after["目标生成描述"] == ""
    assert "欧洲裔美国女性" in role_payload_after["当前目标画像描述"]
    assert "韩裔" not in role_payload_after["当前目标画像描述"]
    assert "韩裔" not in role_payload_after["当前子资产"]["本地化视觉形象描述"]
    assert "韩裔" not in role_payload_after["子资产"][0]["本地化视觉形象描述"]
    assert role_payload_after["当前子资产"]["本地化子资产名称"] == "Lena Brooks"
    assert role_payload_after["子资产"][0]["本地化子资产名称"] == "Lena Brooks"
    assert role_payload_after["sourceSnapshot"] == role_source

    prop = by_id["asset_4dfcaab24953"]
    prop_payload_after = prop["payload"]
    assert prop["localized_name"] == "Black Plastic Poultry Crate"
    assert prop_payload_after["目标生成描述"] == ""
    assert prop_payload_after["当前目标画像描述"] == prop_payload["localizedProfile"]["coreDescription"]
    assert prop_payload_after["当前子资产"]["本地化子资产名称"] == "Black Plastic Poultry Crate"
    assert "麻袋" not in prop_payload_after["当前目标画像描述"]
    assert "麻袋" not in prop_payload_after["当前子资产"]["本地化视觉形象描述"]
    assert prop_payload_after["sourceSnapshot"] == prop_source


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


def test_storyboard_retries_once_when_model_output_lacks_shot_facts(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """模型偶发输出结构塌陷时自动补一次调用，不必让用户手动重试分析。"""
    project = create_project(client, title="storyboard-quality-retry")
    episode_id, _ = _seed_storyboard_agent_input(project, tmp_path / "retry-source.mp4")
    calls: list[int] = []
    monkeypatch.setattr(main, "prepare_agent", lambda _key: {"mode": "volcengine", "model_name": "provider-spy", "prompt": "video storyboard", "prompt_file": "test"})

    def fake_storyboard_call(_key, _agent, _payload):
        calls.append(len(calls) + 1)
        if len(calls) == 1:
            # 首次输出结构塌陷：分镜没有镜头列表，逐镜事实缺失（今晚线上真实失败形态）
            return json.dumps({"分镜列表": [{"分镜时间范围": "00:00:00-00:00:11", "最终视频生成提示词": "按原片演绎"}]}, ensure_ascii=False)
        return _valid_storyboard_model_output()

    monkeypatch.setattr(main, "call_agent_chat", fake_storyboard_call)
    with main.connect() as conn:
        db_project = main.get_project_or_404(conn, project["id"])
        task = main.run_storyboard_generation(conn, db_project, episode_id)
        assert task["status"] == "成功", task
        assert task["result"]["使用回退分镜"] is False
        assert "缺少完整逐镜事实" not in task["message"]
        rows = conn.execute("SELECT payload_json FROM storyboards WHERE project_id=? AND episode_id=?", (project["id"], episode_id)).fetchall()
        assert rows
        assert all(json.loads(row["payload_json"]).get("使用回退占位") is not True for row in rows)
    assert len(calls) == 2


def test_us_cg_storyboard_provider_context_uses_target_rules_and_separate_context_cache(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    project = create_project(client, title="us-cg-routing", remakeMode="cg_3d", targetCountry="美国", targetLanguage="英语", sourceLanguage="中文")
    episode_id, _ = _seed_storyboard_agent_input(project, tmp_path / "us-cg-source.mp4")
    calls: list[dict] = []
    monkeypatch.setattr(main, "prepare_agent", lambda _key: {"mode": "volcengine", "model_name": "provider-spy", "prompt": "base prompt", "prompt_file": "test"})

    def capture_call(_key, agent, payload):
        calls.append({"agent": dict(agent), "payload": dict(payload)})
        return _valid_storyboard_model_output()

    monkeypatch.setattr(main, "call_agent_chat", capture_call)
    with main.connect() as conn:
        db_project = main.get_project_or_404(conn, project["id"])
        first_signature = main.storyboard_analysis_context_signature(db_project, [{"asset_type": "角色", "name": "林浩", "localized_name": "Lin Hao", "variable": "{{role_1}}", "payload": {}}])
        generated = main.run_storyboard_generation(conn, db_project, episode_id, force=True)
        assert generated["status"] == "成功", generated["message"]
        captured = calls[0]
        assert captured["payload"]["目标国家"] == "美国"
        assert captured["payload"]["目标语言"] == "英语"
        assert "国内模式" not in captured["payload"]["模式路由强约束"]
        assert "仅海外模式使用目标国家/翻译" not in captured["payload"]["模式路由强约束"]
        assert "当前是国内重制项目" not in captured["agent"]["prompt"]
        assert "cg_3d/真人/资产替换只决定视觉或资产策略" in captured["payload"]["模式路由强约束"]
        changed_project = {**db_project, "target_country": "加拿大"}
        second_signature = main.storyboard_analysis_context_signature(changed_project, [{"asset_type": "角色", "name": "林浩", "localized_name": "Lin Hao", "variable": "{{role_1}}", "payload": {}}])
        assert first_signature != second_signature


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
        monkeypatch.setattr(provider_module._NO_PROXY_OPENER, "open", lambda *_args, _body=body, **_kwargs: PartialResponse(_body))
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
        monkeypatch.setattr(provider_module._NO_PROXY_OPENER, "open", lambda *_args, _body=body, **_kwargs: CompleteResponse(_body))
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


def _two_board_storyboard_output_with_boundary_slip() -> str:
    shot_template = {
        "机位与构图": {"角度": "平视", "景别": "中景", "主体构图": "人物位于画面中央"},
        "背景": {"可见环境": "婚礼舞台"},
        "人物站位": [{"资产变量": "@LinHao", "画面左右": "左侧"}],
        "动作过程": {"起始": "站立", "变化": "抬手"},
        "运镜": {"方式": "固定"},
        "台词与声音": [{"原话": "待确认", "环境音": "宾客声"}],
    }
    board_one_shot = dict(shot_template)
    board_one_shot["镜头编号"] = "镜头1"
    board_one_shot["时间范围"] = {"开始时间": "00:00:00", "结束时间": "00:00:06"}
    board_two_shot = dict(shot_template)
    board_two_shot["镜头编号"] = "镜头1"
    board_two_shot["时间范围"] = {"开始时间": "00:00:06", "结束时间": "00:00:11"}
    return json.dumps({
        "片段说明": {"场景": "@婚礼舞台", "分镜数量": 2, "目标风格": "按项目设置", "参考素材分工": "视频提供镜头事实，资产变量提供外观", "成片语言与字幕": "保留可辨原话", "共用要求": "无后期字幕或水印"},
        "分镜列表": [
            {
                "分镜编号": "分镜_001",
                "分镜时间范围": {"开始时间": "00:00:00", "结束时间": "00:00:05"},
                "引用资产变量": ["@LinHao"],
                "镜头列表": [board_one_shot],
                "最终视频生成提示词": "平视中景，人物站在婚礼舞台中央。",
            },
            {
                "分镜编号": "分镜_002",
                "分镜时间范围": {"开始时间": "00:00:06", "结束时间": "00:00:11"},
                "引用资产变量": ["@LinHao"],
                "镜头列表": [board_two_shot],
                "最终视频生成提示词": "平视中景，人物抬手致意。",
            },
        ],
    }, ensure_ascii=False)


def test_storyboard_time_boundary_slip_is_repaired_and_audited(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    project = create_project(client, title="storyboard-time-boundary-repair")
    episode_id, _ = _seed_storyboard_agent_input(project, tmp_path / "boundary-repair.mp4")
    monkeypatch.setattr(main, "prepare_agent", lambda _key: {"mode": "volcengine", "model_name": "provider-spy", "prompt": "video storyboard", "prompt_file": "test"})
    monkeypatch.setattr(main, "call_agent_chat", lambda *_args: _two_board_storyboard_output_with_boundary_slip())
    response = client.post(
        f"/api/projects/{project['id']}/tasks/run",
        json={"taskType": "storyboard_generation", "episodeId": episode_id, "force": True},
    )
    assert response.status_code == 200, response.text
    task = client.get(f"/api/tasks/{response.json()['task']['id']}").json()["task"]
    assert task["status"] == "成功", task["message"]
    repairs = task["result"]["分镜时间边界自动修复"]
    assert repairs and "分镜_001" in repairs[0] and "00:00:06" in repairs[0]
    assert "已自动修复 1 处分镜时间边界" in task["message"]
    with main.connect() as conn:
        row = conn.execute(
            "SELECT time_range, payload_json FROM storyboards WHERE project_id=? AND episode_id=? ORDER BY time_range LIMIT 1",
            (project["id"], episode_id),
        ).fetchone()
        assert row["time_range"] == "00:00:00-00:00:06"
        payload = json.loads(row["payload_json"])
        assert payload["分镜时间范围"]["结束时间"] == "00:00:06"


def test_storyboard_time_boundary_collision_is_not_repaired_and_reports_offender():
    boards = [
        {
            "title": "分镜_001",
            "time_range": "00:00:00-00:00:05",
            "payload": {"镜头列表": [{"镜头编号": "镜头1", "时间范围": {"开始时间": "00:00:00", "结束时间": "00:00:08"}}]},
        },
        {
            "title": "分镜_002",
            "time_range": "00:00:06-00:00:11",
            "payload": {"镜头列表": [{"镜头编号": "镜头1", "时间范围": {"开始时间": "00:00:06", "结束时间": "00:00:11"}}]},
        },
    ]
    notes = main.repair_storyboard_time_bounds(boards, {})
    assert notes == []
    errors = main.storyboard_time_fit_errors(boards, {})
    assert errors and "分镜_001" in errors[0] and "镜头1" in errors[0] and "00:00:08" in errors[0]
    assert main.storyboard_times_fit_source(boards, {}) is False


def test_zero_length_shot_and_board_ranges_are_repaired_from_declared_duration():
    # 第 6 集真实缺陷形态：模型把分镜_002 写成 00:00:03-00:00:03（零长度），
    # 但镜头声明时长 1 秒，下一分镜从 00:00:04 开始——按声明时长补足即可消除歧义。
    boards = [
        {
            "title": "分镜_001",
            "time_range": "00:00:00-00:00:02",
            "payload": {
                "分镜时间范围": {"开始时间": "00:00:00", "结束时间": "00:00:02"},
                "镜头列表": [{"镜头编号": "镜头1", "时间范围": {"开始时间": "00:00:00", "结束时间": "00:00:02"}, "时长": "2秒"}],
            },
        },
        {
            "title": "分镜_002",
            "time_range": "00:00:03-00:00:03",
            "payload": {
                "分镜时间范围": {"开始时间": "00:00:03", "结束时间": "00:00:03"},
                "镜头列表": [{"镜头编号": "镜头1", "时间范围": {"开始时间": "00:00:03", "结束时间": "00:00:03"}, "时长": "1秒"}],
            },
        },
        {
            "title": "分镜_003",
            "time_range": "00:00:04-00:00:06",
            "payload": {
                "分镜时间范围": {"开始时间": "00:00:04", "结束时间": "00:00:06"},
                "镜头列表": [{"镜头编号": "镜头1", "时间范围": {"开始时间": "00:00:04", "结束时间": "00:00:06"}, "时长": "2秒"}],
            },
        },
    ]
    notes = main.repair_storyboard_time_bounds(boards, {"duration_seconds": 15})
    assert any("分镜_002" in note and "零长度" in note for note in notes)
    assert boards[1]["time_range"] == "00:00:03-00:00:04"
    assert boards[1]["payload"]["分镜时间范围"]["结束时间"] == "00:00:04"
    assert boards[1]["payload"]["镜头列表"][0]["时间范围"]["结束时间"] == "00:00:04"
    assert main.storyboard_time_fit_errors(boards, {"duration_seconds": 15}) == []
    assert main.storyboard_times_fit_source(boards, {"duration_seconds": 15}) is True


def test_zero_length_range_without_declared_duration_is_not_repaired():
    boards = [
        {
            "title": "分镜_002",
            "time_range": "00:00:03-00:00:03",
            "payload": {
                "分镜时间范围": {"开始时间": "00:00:03", "结束时间": "00:00:03"},
                "镜头列表": [{"镜头编号": "镜头1", "时间范围": {"开始时间": "00:00:03", "结束时间": "00:00:03"}}],
            },
        },
    ]
    notes = main.repair_storyboard_time_bounds(boards, {"duration_seconds": 15})
    assert notes == []
    errors = main.storyboard_time_fit_errors(boards, {"duration_seconds": 15})
    assert errors and "分镜_002" in errors[0] and "分镜区间非法" in errors[0]
    assert main.storyboard_times_fit_source(boards, {"duration_seconds": 15}) is False


def test_zero_length_repair_does_not_extend_into_next_storyboard():
    # 声明时长 3 秒会撞上下一分镜起点 00:00:04，属于无法无歧义修复的情况，保持不动。
    boards = [
        {
            "title": "分镜_002",
            "time_range": "00:00:03-00:00:03",
            "payload": {
                "分镜时间范围": {"开始时间": "00:00:03", "结束时间": "00:00:03"},
                "镜头列表": [{"镜头编号": "镜头1", "时间范围": {"开始时间": "00:00:03", "结束时间": "00:00:03"}, "时长": "3秒"}],
            },
        },
        {
            "title": "分镜_003",
            "time_range": "00:00:04-00:00:06",
            "payload": {
                "分镜时间范围": {"开始时间": "00:00:04", "结束时间": "00:00:06"},
                "镜头列表": [{"镜头编号": "镜头1", "时间范围": {"开始时间": "00:00:04", "结束时间": "00:00:06"}, "时长": "2秒"}],
            },
        },
    ]
    notes = main.repair_storyboard_time_bounds(boards, {"duration_seconds": 15})
    assert notes == []
    assert boards[0]["time_range"] == "00:00:03-00:00:03"


def test_series_analysis_counts_failed_or_empty_storyboard_episode_as_failure(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    project = create_project(client, title="series-failure-accounting")
    with main.connect() as conn:
        for episode_id, episode_index in (("episode_series_bad", 1), ("episode_series_empty", 2), ("episode_series_ok", 3)):
            conn.execute(
                "INSERT INTO episodes (id,project_id,episode_index,filename,file_path,status,created_at) VALUES (?,?,?,?,?,?,?)",
                (episode_id, project["id"], episode_index, f"ep{episode_index}.mp4", f"C:/test/ep{episode_index}.mp4", "已上传", "2026-01-01T00:00:00Z"),
            )

    def fake_asset_extraction(_conn, _project, episode_id):
        return {"id": f"asset_task_{episode_id}", "status": "成功", "message": "资产提取完成", "result": {"资产数量": 4}}

    def fake_storyboard_generation(_conn, _project, episode_id):
        if episode_id == "episode_series_bad":
            return {
                "id": "storyboard_task_bad",
                "status": "失败",
                "message": "分镜分析失败，现有分镜已保留：模型返回的分镜时间超出当前视频或镜头区间（分镜_002 分镜区间非法（00:00:03-00:00:03）），未替换现有分镜；请重试分析。",
                "result": {"分镜数量": 0},
            }
        if episode_id == "episode_series_empty":
            return {"id": "storyboard_task_empty", "status": "成功", "message": "完成", "result": {"分镜数量": 0, "分镜片段数量": 0}}
        return {"id": f"storyboard_task_{episode_id}", "status": "成功", "message": "分镜生成完成", "result": {"分镜数量": 5, "分镜片段数量": 2}}

    monkeypatch.setattr(main, "run_asset_extraction", fake_asset_extraction)
    monkeypatch.setattr(main, "run_storyboard_generation", fake_storyboard_generation)
    with main.connect() as conn:
        db_project = main.get_project_or_404(conn, project["id"])
        parent_task = main.create_task(conn, project["id"], None, "series_analysis", "整剧分析中", {})
        conn.commit()
        completed = main.run_series_analysis(conn, db_project, parent_task["id"])
        conn.commit()
        parent_row = conn.execute("SELECT status, result_json FROM tasks WHERE id=?", (parent_task["id"],)).fetchone()
    assert completed["result"]["成功集数"] == 1
    assert completed["result"]["失败集数"] == 2
    assert completed["message"] == "整剧分析完成：成功 1 集，失败 2 集"
    failure_ids = {item["episodeId"]: item["error"] for item in completed["result"]["失败明细"]}
    assert "episode_series_bad" in failure_ids and "分镜生成失败" in failure_ids["episode_series_bad"]
    assert "分镜区间非法" in failure_ids["episode_series_bad"]
    assert "episode_series_empty" in failure_ids and "分镜生成结果为空" in failure_ids["episode_series_empty"]
    success_ids = {item["episodeId"] for item in completed["result"]["成功明细"]}
    assert success_ids == {"episode_series_ok"}
    assert json.loads(parent_row["result_json"])["失败集数"] == 2


def upload_episode_file(client: TestClient, project_id: str, filename: str = "ep01.mp4"):
    return client.post(
        f"/api/projects/{project_id}/episodes/upload",
        files=[("files", (filename, io.BytesIO(b"fake video bytes"), "video/mp4"))],
    )


def test_episode_upload_locked_after_series_analysis(client: TestClient):
    project = create_project(client, title="series-upload-lock")

    first = upload_episode_file(client, project["id"])
    assert first.status_code == 200, first.text

    # 未提交分析前可以继续上传
    second = upload_episode_file(client, project["id"], filename="ep02.mp4")
    assert second.status_code == 200, second.text

    with main.connect() as conn:
        main.create_task_with_status(conn, project["id"], None, "series_analysis", "成功", "整剧分析完成", {})
        conn.commit()

    locked = upload_episode_file(client, project["id"], filename="ep03.mp4")
    assert locked.status_code == 409
    assert "锁定" in locked.json()["detail"]


def test_episode_upload_locked_while_series_analysis_running(client: TestClient):
    project = create_project(client, title="series-upload-lock-running")
    assert upload_episode_file(client, project["id"]).status_code == 200

    with main.connect() as conn:
        main.create_task_with_status(conn, project["id"], None, "series_analysis", "运行中", "整剧分析中", {})
        conn.commit()

    locked = upload_episode_file(client, project["id"], filename="ep02.mp4")
    assert locked.status_code == 409


def test_episode_upload_not_locked_by_failed_analysis(client: TestClient):
    project = create_project(client, title="series-upload-failed-analysis")
    assert upload_episode_file(client, project["id"]).status_code == 200

    with main.connect() as conn:
        main.create_task_with_status(conn, project["id"], None, "series_analysis", "失败", "整剧分析失败", {})
        conn.commit()

    retry = upload_episode_file(client, project["id"], filename="ep02.mp4")
    assert retry.status_code == 200, retry.text


def test_cg3d_project_with_target_language_sends_overseas_storyboard_context(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    project = create_project(client, title="cg3d-overseas-context")
    episode_id, _ = _seed_storyboard_agent_input(project, tmp_path / "cg3d-context.mp4")
    with main.connect() as conn:
        conn.execute(
            "UPDATE projects SET remake_mode='cg_3d', target_country='美国', target_language='英语', storyboard_visual_style='cg_3d' WHERE id=?",
            (project["id"],),
        )
        conn.commit()
    captured: list[dict] = []
    monkeypatch.setattr(main, "prepare_agent", lambda _key: {"mode": "volcengine", "model_name": "provider-spy", "prompt": "video storyboard", "prompt_file": "test"})
    def fake_call(_key, _agent, payload):
        captured.append(payload)
        return _valid_storyboard_model_output()
    monkeypatch.setattr(main, "call_agent_chat", fake_call)
    with main.connect() as conn:
        db_project = main.get_project_or_404(conn, project["id"])
        task = main.run_storyboard_generation(conn, db_project, episode_id, force=True)
        conn.commit()
    assert task["status"] == "成功"
    assert captured, "应捕获到分镜请求上下文"
    payload = captured[0]
    assert payload["目标国家"] == "美国"
    assert payload["目标语言"] == "英语"
    assert "按明确目标国家（美国）和目标语言（英语）适配分镜" in payload["模式路由强约束"]
    summary_names = [item["本地化资产名称"] for item in payload["资产摘要"]]
    assert "Lin Hao" in summary_names


def test_asset_groups_batch_membership_replaces_parent_style_and_preserves_assets(client: TestClient):
    project = create_project(client, title="asset-groups")
    with main.connect() as conn:
        conn.execute(
            "INSERT INTO episodes (id,project_id,episode_index,filename,file_path,status,created_at) VALUES (?,?,?,?,?,?,?)",
            ("episode_groups", project["id"], 1, "groups.mp4", "C:/test/groups.mp4", "已上传", "2026-01-01T00:00:00Z"),
        )
        assets = [
            ("asset_parent", "角色", "B主资产", "{{CHAR_001_SUB_01}}", "角色:B主资产"),
            ("asset_child", "角色", "A资产", "{{CHAR_002_SUB_01}}", "角色:A资产"),
            ("asset_grandchild", "角色", "C资产", "{{CHAR_003_SUB_01}}", "角色:C资产"),
            ("asset_prop", "道具", "水瓶", "{{PROP_001}}", "道具:水瓶"),
        ]
        for asset_id, asset_type, name, variable, main_key in assets:
            payload = {"项目主资产Key": main_key, "项目子资产Key": f"{main_key}:默认", "出现集数": [1], "源事实快照": f"{name}源事实"}
            conn.execute(
                "INSERT INTO assets (id,project_id,episode_id,asset_type,name,localized_name,variable,status,payload_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (asset_id, project["id"], "episode_groups", asset_type, name, f"{name}EN", variable, "已确认", json.dumps(payload, ensure_ascii=False), "2026-01-01T00:00:00Z"),
            )
        conn.execute(
            "INSERT INTO storyboards (id,project_id,episode_id,title,time_range,prompt,payload_json,status,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "storyboard_group",
                project["id"],
                "episode_groups",
                "分镜1",
                "00:00:00-00:00:02",
                "{{CHAR_002_SUB_01}} 出现在画面中。",
                json.dumps({"引用资产变量": ["{{CHAR_002_SUB_01}}"]}, ensure_ascii=False),
                "已生成",
                "2026-01-01T00:00:00Z",
            ),
        )
        conn.commit()

    created = client.post(
        f"/api/projects/{project['id']}/asset-groups",
        json={"name": "主角组合", "memberAssetIds": ["asset_parent", "asset_child"]},
    )
    assert created.status_code == 200, created.text
    first_group = created.json()["group"]
    assert first_group["assetType"] == "角色"
    assert [item["assetKey"] for item in first_group["members"]] == ["角色:B主资产", "角色:A资产"]

    second = client.post(
        f"/api/projects/{project['id']}/asset-groups",
        json={"name": "临时组", "memberAssetIds": ["asset_grandchild"]},
    )
    assert second.status_code == 200, second.text
    second_group = second.json()["group"]

    duplicate_name = client.post(
        f"/api/projects/{project['id']}/asset-groups",
        json={"name": "主角组合", "memberAssetIds": ["asset_prop"]},
    )
    assert duplicate_name.status_code == 400

    mixed = client.post(
        f"/api/projects/{project['id']}/asset-groups",
        json={"name": "混合组", "memberAssetIds": ["asset_child", "asset_prop"]},
    )
    assert mixed.status_code == 400
    assert "同类资产" in mixed.json()["detail"]

    moved = client.patch(
        f"/api/projects/{project['id']}/asset-groups/{first_group['id']}",
        json={"memberAssetIds": ["asset_parent", "asset_child", "asset_grandchild"]},
    )
    assert moved.status_code == 200, moved.text
    assert [item["assetKey"] for item in moved.json()["group"]["members"]] == ["角色:B主资产", "角色:A资产", "角色:C资产"]
    second_after = client.get(f"/api/projects/{project['id']}/asset-groups").json()["groups"]
    assert [item["id"] for item in second_after if item["id"] == second_group["id"]][0] == second_group["id"]
    assert second_after[next(index for index, item in enumerate(second_after) if item["id"] == second_group["id"])]["members"] == []

    with main.connect() as conn:
        child = json.loads(conn.execute("SELECT payload_json FROM assets WHERE id='asset_child'").fetchone()[0])
        storyboard = json.loads(conn.execute("SELECT payload_json FROM storyboards WHERE id='storyboard_group'").fetchone()[0])
    assert child["源事实快照"] == "A资产源事实"
    assert storyboard["引用资产变量"] == ["{{CHAR_002_SUB_01}}"]

    renamed = client.patch(
        f"/api/projects/{project['id']}/asset-groups/{first_group['id']}",
        json={"name": "篮球队员"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["group"]["name"] == "篮球队员"

    deleted = client.delete(f"/api/projects/{project['id']}/asset-groups/{first_group['id']}")
    assert deleted.status_code == 200
    listed = client.get(f"/api/projects/{project['id']}/asset-groups").json()["groups"]
    assert all(item["id"] != first_group["id"] for item in listed)
    assets_after = client.get(f"/api/projects/{project['id']}/assets").json()["assets"]
    assert {"asset_parent", "asset_child", "asset_grandchild", "asset_prop"} <= {item["id"] for item in assets_after}


def test_legacy_parent_links_migrate_into_custom_groups(client: TestClient):
    project = create_project(client, title="legacy-parent-migration")
    with main.connect() as conn:
        conn.execute(
            "INSERT INTO episodes (id,project_id,episode_index,filename,file_path,status,created_at) VALUES (?,?,?,?,?,?,?)",
            ("episode_legacy", project["id"], 1, "legacy.mp4", "C:/test/legacy.mp4", "已上传", "2026-01-01T00:00:00Z"),
        )
        for asset_id, name, variable, main_key in [
            ("asset_legacy_parent", "白教练", "{{CHAR_001_SUB_01}}", "角色:白教练"),
            ("asset_legacy_child", "1号球员", "{{CHAR_002_SUB_01}}", "角色:1号球员"),
        ]:
            payload = {"项目主资产Key": main_key, "项目子资产Key": f"{main_key}:默认", "出现集数": [1]}
            conn.execute(
                "INSERT INTO assets (id,project_id,episode_id,asset_type,name,localized_name,variable,status,payload_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (asset_id, project["id"], "episode_legacy", "角色", name, f"{name}EN", variable, "已确认", json.dumps(payload, ensure_ascii=False), "2026-01-01T00:00:00Z"),
            )
        conn.execute(
            "INSERT INTO asset_parent_links (id,project_id,parent_asset_id,child_asset_id,parent_asset_key,child_asset_key,created_at) VALUES (?,?,?,?,?,?,?)",
            ("legacy_link", project["id"], "asset_legacy_parent", "asset_legacy_child", "角色:白教练", "角色:1号球员", "2026-01-01T00:00:00Z"),
        )
        conn.commit()

    groups = client.get(f"/api/projects/{project['id']}/asset-groups").json()["groups"]
    assert len(groups) == 1
    assert groups[0]["name"] == "白教练"
    assert {item["assetKey"] for item in groups[0]["members"]} == {"角色:白教练", "角色:1号球员"}


def test_alias_occurs_standalone_skips_prefix_of_qualified_variable():
    body = "出镜角色：@23号球员-CHAR001SUB01、@京北大学替补席球员群像-CHAR003SUB01。"
    # 旧别名只是新展示变量的前缀，不应再被误判为独立引用（歧义误报根因）。
    assert main._alias_occurs_standalone(body, "@23号球员") is False
    assert main._alias_occurs_standalone(body, "@京北大学替补席球员群像") is False
    # 完整展示变量正常命中。
    assert main._alias_occurs_standalone(body, "@23号球员-CHAR001SUB01") is True
    # 独立使用的旧别名仍然命中（不误伤真实待核对项）。
    assert main._alias_occurs_standalone("他看向 @23号球员，随后传球。", "@23号球员") is True
    assert main._alias_occurs_standalone("结尾是 @23号球员", "@23号球员") is True


def test_alias_occurs_standalone_skips_prefix_of_qualified_variable():
    body = "出镜角色：@23号球员-CHAR001SUB01、@京北大学替补席球员群像-CHAR003SUB01。"
    # 旧别名只是新展示变量的前缀，不应再被误判为独立引用（歧义误报根因）。
    assert main._alias_occurs_standalone(body, "@23号球员") is False
    assert main._alias_occurs_standalone(body, "@京北大学替补席球员群像") is False
    # 完整展示变量正常命中。
    assert main._alias_occurs_standalone(body, "@23号球员-CHAR001SUB01") is True
    # 独立使用的旧别名仍然命中（不误伤真实待核对项）。
    assert main._alias_occurs_standalone("他看向 @23号球员，随后传球。", "@23号球员") is True
    assert main._alias_occurs_standalone("结尾是 @23号球员", "@23号球员") is True
