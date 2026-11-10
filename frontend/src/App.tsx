import { ChangeEvent, ClipboardEvent as ReactClipboardEvent, CSSProperties, FormEvent, Fragment, KeyboardEvent as ReactKeyboardEvent, PointerEvent as ReactPointerEvent, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { VideoComposition } from "./VideoComposition";

const API_BASE = (import.meta.env.VITE_API_BASE ?? "").replace(/\/$/, "");
const ASSET_IMAGE_MODELS = [
  { id: "doubao-seedream-5-0-260128", label: "Seedream 5.0" },
  { id: "doubao-seedream-4-5-251128", label: "Seedream 4.5" },
  { id: "doubao-seedream-4-0-250828", label: "Seedream 4.0" },
];
const DEFAULT_ASSET_IMAGE_MODEL = ASSET_IMAGE_MODELS[0].id;
const VIDEO_MODELS = [
  { id: "doubao-seedance-1-5-pro-251215", label: "Seedance 1.5 Pro" },
  { id: "doubao-seedance-1-0-pro-250528", label: "Seedance 1.0 Pro" },
  { id: "doubao-seedance-1-0-lite-t2v-250428", label: "Seedance 1.0 Lite" },
];
const DEFAULT_VIDEO_MODEL = VIDEO_MODELS[0].id;
type VoiceReferenceAudio = { fileUrl: string; fileName: string; size?: number; mimeType?: string; status?: string };
const DEFAULT_VOICE_CONFIG = { model: "clone", referenceAudio: undefined as VoiceReferenceAudio | undefined };
const ROLE_FIXED_CONSTRAINT = `【固定约束】
风格：真人写实风格 画质：模拟真实拍摄效果，8K超高清
光线：明亮柔和
构图：左侧正面胸像（正面面对镜头，目光看向镜头），右侧三视图（依次是正面、右侧面90°、背面），不允许出现第4个全身人物，不允许上下分两行或多行网格。
背景：干净的白色背景，无文字 人物肤感：皮肤质感真实自然，纹理清晰
姿态：自然直立站姿，双臂自然下垂贴于身体两侧，手指自然微曲，双脚平行与肩同宽
表情：角色默认微表情神态，嘴巴自然闭合，禁止夸张表情 道具：无。双手空握不持任何物品，腰间不挂载任何悬挂物，画面中无任何道具武器
约束：无水印，无文字，无logo，无畸形肢体，角色完整不裁切，脚下有自然投影
摄影强化：专业人像摄影、85mm镜头、浅景深、影棚布光、高分辨率、对焦清晰
光效建议：柔光、伦勃朗光、蝴蝶光、侧逆光
画质与质感要求：8K超清、极致细节、照片级真实
负面限制：避免卡通、绘画、3D渲染、模糊、畸形、低质量`;

type Screen = "home" | "source" | "analysis" | "assets" | "segments" | "compare";
type StepScreen = "home" | "assets" | "segments" | "compare";
type AssetTab = "角色" | "场景" | "道具" | "资产进度";
type UploadMode = "folder" | "single";
type ModalName = "upload-confirm-modal" | "image-view-modal" | null;
type AssetView = "overview" | "detail" | "group-detail";
type SegmentPanel = "shot-prompt" | "shot-assets";
type WorkbenchPane = "assets" | "preview";

type Project = {
  id: string;
  title: string;
  target_country: string;
  target_language: string;
  status: string;
  remake_mode?: "overseas_localization" | "cg_3d" | "asset_replacement";
  source_language?: string;
  storyboard_visual_style?: "live_action" | "cg_3d";
  replace_characters?: number;
  replace_scenes?: number;
  parent_project_id?: string;
};

type Episode = {
  id: string;
  episode_index: number;
  filename: string;
  size_bytes: number;
  status: string;
  source_url: string;
  file_url?: string;
  ark_file_id: string;
  ark_file_status: string;
  ark_file_error: string;
  created_at: string;
};

type Asset = {
  id: string;
  episode_id?: string;
  asset_type: string;
  name: string;
  localized_name: string;
  variable: string;
  display_variable?: string;
  status: string;
  payload?: Record<string, any>;
  images?: Array<{ id: string; file_path: string; file_url?: string; is_selected: number; prompt?: string; created_at?: string; payload?: Record<string, any> }>;
};

type VoiceConfiguration = typeof DEFAULT_VOICE_CONFIG & { updatedAt?: string };
type VoiceEditorState = {
  assetId: string;
  roleName: string;
  config: VoiceConfiguration;
};

type CustomAssetGroupMember = {
  assetId: string;
  assetKey: string;
  createdAt: string;
};

type CustomAssetGroup = {
  id: string;
  name: string;
  assetType: string;
  members: CustomAssetGroupMember[];
  createdAt: string;
  updatedAt: string;
};

type Storyboard = {
  id: string;
  episode_id: string;
  title: string;
  time_range: string;
  prompt: string;
  status: string;
  analysis_state?: string;
  analysis_message?: string;
  payload?: Record<string, any>;
};

type PromptVariablePart =
  | { kind: "text"; value: string }
  | { kind: "variable"; token: string; asset?: Asset };

type VideoGeneration = {
  id: string;
  storyboard_id: string;
  status: string;
  output_path: string;
  output_url?: string;
  is_selected?: number;
  created_at: string;
  payload?: Record<string, any>;
};

type Task = {
  id: string;
  project_id?: string;
  episode_id: string | null;
  task_type: string;
  status: string;
  message: string;
  result?: Record<string, any>;
  created_at: string;
  updated_at: string;
};

type AssetGroup = {
  key: string;
  type: string;
  name: string;
  localizedName: string;
  items: Asset[];
};

type ImageView = {
  url: string;
  title: string;
} | null;

let storyboardFrameQueue: Promise<void> = Promise.resolve();

function enqueueStoryboardFrame(task: () => Promise<void>) {
  const queued = storyboardFrameQueue.catch(() => undefined).then(task);
  storyboardFrameQueue = queued.catch(() => undefined);
  return queued;
}

type SelectedSource = {
  kind: "local";
  uploadMode?: UploadMode;
  title: string;
  meta: string;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, init);
  } catch (error) {
    if (error instanceof TypeError) {
      throw new Error("无法连接分析服务，请检查后端服务或网络连接后重试");
    }
    throw error;
  }
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || response.statusText);
  }
  return response.json() as Promise<T>;
}

function currentChild(asset: Asset) {
  return (asset.payload?.当前子资产 as Record<string, any> | undefined) ?? asset.payload ?? {};
}

function pickText(source: Record<string, any> | undefined, keys: string[]) {
  if (!source) return "";
  for (const key of keys) {
    const value = source[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  return "";
}

function mainAssetKey(asset: Asset) {
  return pickText(asset.payload, ["项目主资产Key", "主资产编号", "道具编号"]) || `${asset.asset_type}:${asset.name}:${asset.localized_name}`;
}

function variantName(asset: Asset) {
  const child = currentChild(asset);
  return (
    pickText(child, ["本地化子资产名称", "原剧子资产名称"]) ||
    pickText(asset.payload, ["本地化道具名称", "原剧道具名称"]) ||
    asset.localized_name
  );
}

function assetPrompt(asset: Asset) {
  const child = currentChild(asset);
  const isPlaceholder = (value: string) =>
    [
      "目标画像待本地化；当前旧资产共享描述不能作为目标提示词。",
      "目标画像需本地化；旧共享描述不能作为目标提示词。",
      "当前资产沿用原片外观与原片资产图；不使用历史目标候选。",
    ].includes(value.trim());
  const finalPrompt = pickText(asset.payload, ["最终资产图生成提示词"]);
  const manualPrompt = pickText(asset.payload, ["用户编辑资产图生成提示词"]);
  const prompt =
    (finalPrompt && !isPlaceholder(finalPrompt) ? finalPrompt : "") || manualPrompt ||
    pickText(child, ["角色资产图生成提示词", "场景资产图生成提示词", "道具资产图生成提示词"]) ||
    pickText(asset.payload, ["角色资产图生成提示词", "场景资产图生成提示词", "道具资产图生成提示词"]) ||
    (() => {
      const automatic = pickText(asset.payload, ["自动编译资产图生成提示词"]);
      return isPlaceholder(automatic) ? "" : automatic;
    })();
  if (prompt) return prompt;
  if (asset.asset_type !== "角色") return prompt;
  if (prompt.includes("【基础设定】") && prompt.includes("【固定约束】")) return prompt;
  const field = (section: string, key: string, fallbackKeys: string[] = []) => {
    const structure = child["角色资产图结构化描述"] ?? asset.payload?.["角色资产图结构化描述"];
    if (structure && typeof structure === "object") {
      const sectionValue = structure[section];
      if (sectionValue && typeof sectionValue === "object" && typeof sectionValue[key] === "string" && sectionValue[key].trim()) {
        return sectionValue[key].trim();
      }
    }
    return pickText(child, [key, ...fallbackKeys]) || pickText(asset.payload, [key, ...fallbackKeys]) || inferRolePromptField(asset, section, key);
  };
  return `【基础设定】
性别：${field("基础设定", "性别")}
视觉年龄：${field("基础设定", "视觉年龄")}
国家 / 时代 / 朝代：${field("基础设定", "国家 / 时代 / 朝代", ["国家时代朝代"])}
身份定位：${field("基础设定", "身份定位", ["剧情身份", "稳定人设定位", "剧情作用"])}
角色名称：${asset.localized_name || asset.name}
本地化视觉锚点：${pickText(child, ["本地化视觉形象描述", "本地化子资产描述"]) || pickText(asset.payload, ["本地化核心形象描述"]) || "根据目标国家审美生成稳定真人写实角色资产，不复用原剧演员脸、原剧地域特征或原剧服装细节。"}

【身材体态】
头身比：${field("身材体态", "头身比")}
身高体感：${field("身材体态", "身高体感")}
体型：${field("身材体态", "体型", ["本地化视觉形象描述", "本地化子资产描述"])}
肩颈：${field("身材体态", "肩颈")}
腰腿：${field("身材体态", "腰腿")}
整体体态：${field("身材体态", "整体体态")}

【脸部特征】
脸型：${field("脸部特征", "脸型")}
骨相：${field("脸部特征", "骨相")}
眉形：${field("脸部特征", "眉形")}
眼形：${field("脸部特征", "眼形")}
鼻形：${field("脸部特征", "鼻形")}
唇形：${field("脸部特征", "唇形")}
妆感：${field("脸部特征", "妆感")}
皮肤肤色：${field("脸部特征", "皮肤肤色")}
皮肤质感：${field("脸部特征", "皮肤质感")}
气色状态：${field("脸部特征", "气色状态")}

【发型】
发型名称：${field("发型", "发型名称")}
发色：${field("发型", "发色")}
长度：${field("发型", "长度")}
刘海 /分线：${field("发型", "刘海 /分线", ["刘海", "分线"])}
发丝质感：${field("发型", "发丝质感")}
整体状态：${field("发型", "整体状态")}

【服装】
主色：${field("服装", "主色")}
副色：${field("服装", "副色")}
点缀色：${field("服装", "点缀色")}
内衬：${field("服装", "内衬")}
中间层：${field("服装", "中间层")}
外层：${field("服装", "外层")}
下装：${field("服装", "下装")}
鞋子：${field("服装", "鞋子")}
面料：${field("服装", "面料")}
纹样：${field("服装", "纹样")}
纹样位置：${field("服装", "纹样位置")}
结构与廓形：${field("服装", "结构与廓形")}
工艺与重工程度：${field("服装", "工艺与重工程度")}
穿着状态：${field("服装", "穿着状态")}

${ROLE_FIXED_CONSTRAINT}`;
}

function assetContextText(asset: Asset) {
  const child = currentChild(asset);
  return [
    pickText(asset.payload, ["剧情身份", "稳定人设定位", "剧情作用"]),
    pickText(asset.payload, ["本地化核心形象描述"]),
    pickText(child, ["本地化视觉形象描述", "本地化子资产描述", "差异标签"]),
    pickText(child, ["本地化子资产名称"]),
    pickText(asset.payload, ["本地化角色名称"]),
  ].filter(Boolean).join("，");
}

function inferRolePromptField(asset: Asset, section: string, key: string) {
  const context = assetContextText(asset);
  const identity = pickText(asset.payload, ["剧情身份", "稳定人设定位", "剧情作用"]) || "核心剧情角色";
  const visual = context || identity;
  const lower = visual.toLowerCase();
  if (key === "性别") {
    if (/[女母]/.test(visual) || ["female", "woman", "girl", "mother"].some((token) => lower.includes(token))) return "女";
    if (/[男父]/.test(visual) || ["male", "man", "boy", "father"].some((token) => lower.includes(token))) return "男";
    return "符合目标国家本地化角色设定的性别表现";
  }
  if (key === "视觉年龄") {
    const age = visual.match(/(\d{2})\s*(?:岁|year|years)/i)?.[1];
    if (age) return `${age}岁`;
    if (/[大学学生球员校园]/.test(visual)) return "20-24岁";
    if (/[总裁父亲母亲教练经理]/.test(visual)) return "30-45岁";
    return "符合目标国家本地化角色设定的真人视觉年龄";
  }
  if (key === "国家 / 时代 / 朝代") return "目标国家本地化 / 现代";
  if (key === "身份定位") return identity;
  if (key === "头身比") return "真实真人比例，约7-8头身";
  if (key === "身高体感") return /篮球|球员|运动员/.test(visual) ? "高挑运动员体感，约185-195cm" : "符合目标国家本地化角色设定和同框比例的真实身高体感";
  if (key === "体型") return visual;
  if (key === "肩颈") return "肩颈自然舒展，符合角色身份和体态气质";
  if (key === "腰腿") return "腰腿比例真实自然，符合目标国家本地化角色体态";
  if (key === "整体体态") return `体现目标国家本地化后${identity}的气质、姿态和当前子资产状态`;
  if (key === "脸型") return "符合目标国家本地化真人审美的写实脸型";
  if (key === "骨相") return "骨相真实自然，符合角色年龄感和剧情气质";
  if (key === "眉形") return "自然眉形，符合目标国家本地化五官气质";
  if (key === "眼形") return "真实自然眼形，符合目标国家本地化人物气质";
  if (key === "鼻形") return "真实自然鼻形，符合本地化真人审美";
  if (key === "唇形") return "自然唇形，嘴巴自然闭合";
  if (key === "妆感") return "自然真实妆感";
  if (key === "皮肤肤色") return "符合目标国家本地化真人写实肤色";
  if (key === "皮肤质感") return "皮肤质感真实自然，纹理清晰";
  if (key === "气色状态") return "符合当前剧情状态的自然气色";
  if (key === "发型名称") return "符合目标国家本地化设定的自然发型";
  if (key === "发色") return "符合本地化角色设定的自然发色";
  if (key === "长度") return "符合目标国家本地化设定的自然长度";
  if (key === "刘海 /分线") return "符合目标国家本地化设定的自然刘海或分线";
  if (key === "发丝质感") return "真实自然发丝质感，清晰有层次";
  if (key === "整体状态") return "干净自然，符合角色身份与当前子资产状态";
  const clothing: Record<string, string> = {
    主色: "从当前子资产服装中提取的主视觉颜色",
    副色: "从当前子资产服装中提取的辅助颜色",
    点缀色: "从当前子资产服装细节中提取的点缀颜色",
    内衬: "符合当前子资产服装层次的内衬",
    中间层: "符合当前子资产服装层次的中间层",
    外层: "符合当前子资产身份和场景的外层服装",
    下装: "符合当前子资产服装搭配的下装",
    鞋子: "符合当前子资产身份、场景和服装搭配的鞋子",
    面料: "真实可见材质，符合角色身份",
    纹样: "符合当前子资产服装的自然纹样",
    纹样位置: "位于服装主体或细节区域的自然纹样位置",
    结构与廓形: "符合角色身份、剧情状态和目标国家审美的服装廓形",
    工艺与重工程度: "真实自然，细节清晰",
    穿着状态: "符合当前剧情状态的自然穿着状态",
  };
  if (clothing[key]) return `${clothing[key]}；参考：${visual}`;
  return visual;
}

function assetDisplayName(asset: Asset) {
  return variantName(asset) || asset.localized_name || asset.name || asset.variable;
}

function slugAssetToken(value: string, fallback: string) {
  const tokens = value.match(/[A-Za-z0-9]+/g);
  if (tokens?.length) return tokens.join("_").slice(0, 64);
  return fallback.replace(/[{}]/g, "").replace(/[^A-Za-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 64) || "Asset";
}

function assetDisplayVariable(asset: Asset) {
  const explicit = asset.display_variable || pickText(asset.payload, ["展示资产变量"]);
  if (explicit) return explicit;
  const child = currentChild(asset);
  const source = [
    asset.localized_name || asset.name,
    pickText(child, ["本地化子资产名称", "原剧子资产名称", "差异标签"]),
  ].filter(Boolean).join(" ");
  return `@${slugAssetToken(source, asset.variable || asset.id)}`;
}

function assetReferenceAliases(asset: Asset) {
  const historic = Array.isArray(asset.payload?.["历史展示变量别名"])
    ? asset.payload["历史展示变量别名"].filter((value: unknown): value is string => typeof value === "string")
    : [];
  return Array.from(new Set([
    assetDisplayVariable(asset),
    pickText(asset.payload, ["旧展示资产变量", "源变量别名"]),
    ...historic,
  ].filter(Boolean)));
}

function assetDisplayMeta(asset: Asset) {
  const episodeIndexes = asset.payload?.出现集数;
  const episodeMeta = Array.isArray(episodeIndexes) && episodeIndexes.length
    ? `出现：第 ${episodeIndexes.join("、")} 集`
    : "";
  return [assetDisplayVariable(asset), asset.localized_name && asset.localized_name !== assetDisplayName(asset) ? asset.localized_name : "", episodeMeta]
    .filter(Boolean)
    .join(" · ");
}

function resolveUniqueAssetReference(token: string, assets: Asset[]): Asset | undefined {
  const stableMatches = assets.filter((asset) => asset.variable === token);
  if (stableMatches.length === 1) return stableMatches[0];
  if (stableMatches.length > 1) return undefined;
  const aliasMatches = assets.filter((asset) => [...assetReferenceAliases(asset), assetDisplayName(asset)].includes(token));
  return aliasMatches.length === 1 ? aliasMatches[0] : undefined;
}

function canonicalizeAssetReferences(tokens: string[], assets: Asset[]): string[] {
  const result: string[] = [];
  const seenAssetIds = new Set<string>();
  for (const token of tokens) {
    const asset = resolveUniqueAssetReference(token, assets);
    if (!asset) {
      if (!result.includes(token)) result.push(token);
      continue;
    }
    if (seenAssetIds.has(asset.id)) continue;
    seenAssetIds.add(asset.id);
    result.push(asset.variable);
  }
  return result;
}

function parseTimecode(value: string) {
  const parts = value.split(":").map((part) => Number(part));
  if (parts.some((part) => Number.isNaN(part))) return 0;
  if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
  if (parts.length === 2) return parts[0] * 60 + parts[1];
  return parts[0] || 0;
}

function parseRange(range: string) {
  const [start = "00:00:00", end = start] = range.split("-");
  return { start: parseTimecode(start.trim()), end: parseTimecode(end.trim()) };
}

function rangesOverlap(a: { start: number; end: number }, b: { start: number; end: number }) {
  return Math.max(a.start, b.start) <= Math.min(a.end || a.start, b.end || b.start);
}

function splitAssetVariableValues(value: any): string[] {
  if (Array.isArray(value)) return value.flatMap((item) => splitAssetVariableValues(item));
  if (typeof value !== "string") return [];
  return value.split(/[\s,，、；;]+/).map((item) => item.trim()).filter(Boolean);
}

function detailRows(asset: Asset) {
  const child = currentChild(asset);
  return [
    ["剧情身份/功能", pickText(asset.payload, ["剧情身份", "剧情功能"])],
    ["剧情作用", pickText(asset.payload, ["剧情作用", "社会阶层与情绪功能"])],
    ["稳定定位", pickText(asset.payload, ["稳定人设定位", "空间结构描述"])],
    ["原剧核心形象", pickText(asset.payload, ["原剧核心形象描述", "原剧场景描述", "原剧道具描述"])],
    ["本地化核心形象", pickText(asset.payload, ["本地化核心形象描述", "本地化场景描述", "本地化道具描述"])],
    ["原剧变体视觉", pickText(child, ["原剧视觉形象描述", "原剧视觉描述", "原剧子资产描述"])],
    ["本地化变体视觉", pickText(child, ["本地化视觉形象描述", "本地化视觉描述", "本地化子资产描述"])],
    ["音色/状态", pickText(child, ["状态音色调整", "场景状态差异"]) || pickText(asset.payload, ["基础音色描述", "目标语言声音要求"])],
  ].filter(([, value]) => value);
}

function originalAssetDescription(asset: Asset) {
  if (asset.payload?.["原片资产描述"] === "原片描述未留存") return "原片描述未留存。此项目兼容沿用，不把海外化描述误标为原片事实。";
  if (typeof asset.payload?.["原片资产描述"] === "string" && asset.payload["原片资产描述"]) return asset.payload["原片资产描述"];
  const child = currentChild(asset);
  return [
    pickText(asset.payload, ["原剧核心形象描述", "原剧场景描述", "原剧道具描述"]),
    pickText(child, ["原剧视觉形象描述", "原剧视觉描述", "原剧子资产描述"]),
    pickText(asset.payload, ["剧情身份", "剧情功能"]),
  ].filter(Boolean).join("\n") || "原片描述未留存";
}

function localizedAssetDescription(asset: Asset) {
  const targetDescription = typeof asset.payload?.["目标生成描述"] === "string" ? asset.payload["目标生成描述"] : "";
  const targetLock = String(asset.payload?.targetVisualLockProvenance ?? asset.payload?.["目标生成描述来源"] ?? "").toLowerCase();
  if (targetDescription && ["user", "user_override", "explicit_user_lock", "用户明确设定", "用户手动设定"].includes(targetLock)) return targetDescription;
  if (typeof asset.payload?.["当前目标画像描述"] === "string" && asset.payload["当前目标画像描述"]) return asset.payload["当前目标画像描述"];
  if (targetDescription) return targetDescription;
  const child = currentChild(asset);
  return [
    pickText(asset.payload, ["本地化核心形象描述", "本地化场景描述", "本地化道具描述"]),
    pickText(child, ["本地化视觉形象描述", "本地化视觉描述", "本地化子资产描述"]),
    pickText(child, ["差异标签", "差异类型"]),
  ].filter(Boolean).join("\n") || "目标国家资产描述：保留原资产剧情功能，并完成目标国家本地化转化。";
}

function StoryboardFrameThumb({ storyboard, sourceUrl, selected, onClick }: {
  storyboard: Storyboard;
  sourceUrl?: string;
  selected: boolean;
  onClick: () => void;
}) {
  const rootRef = useRef<HTMLButtonElement | null>(null);
  const [frameUrl, setFrameUrl] = useState("");
  const [frameState, setFrameState] = useState<"waiting" | "loading" | "ready" | "failed">("waiting");
  const [frameFailure, setFrameFailure] = useState("");
  const range = parseRange(storyboard.time_range || "");
  const frameTime = range.end > range.start ? (range.start + range.end) / 2 : range.start;
  useEffect(() => {
    if (!sourceUrl || !Number.isFinite(frameTime)) {
      setFrameUrl("");
      setFrameFailure("");
      setFrameState("failed");
      return;
    }
    setFrameUrl("");
    setFrameFailure("");
    setFrameState("waiting");
    const root = rootRef.current;
    if (!root) return;
    let cancelled = false;
    let video: HTMLVideoElement | null = null;
    let finishFrameTask: (() => void) | null = null;
    let frameTimeoutId: number | null = null;
    const cleanupVideo = () => {
      if (frameTimeoutId !== null) {
        window.clearTimeout(frameTimeoutId);
        frameTimeoutId = null;
      }
      if (video) {
        video.onloadedmetadata = null;
        video.onloadeddata = null;
        video.onseeked = null;
        video.onerror = null;
        video.removeAttribute("src");
        video.load();
        video = null;
      }
      finishFrameTask?.();
      finishFrameTask = null;
    };
    const observer = new IntersectionObserver((entries) => {
      if (cancelled) return;
      if (!entries.some((entry) => entry.isIntersecting)) return;
      observer.disconnect();
      setFrameState("loading");
      void enqueueStoryboardFrame(() => new Promise<void>((resolve) => {
        if (cancelled) { resolve(); return; }
        const currentVideo = document.createElement("video");
        video = currentVideo;
        finishFrameTask = resolve;
        currentVideo.muted = true;
        currentVideo.preload = "metadata";
        currentVideo.crossOrigin = "anonymous";
        const fail = () => {
          if (!cancelled) {
            setFrameFailure(currentVideo.error?.message || "原片无法解码或读取");
            setFrameState("failed");
          }
          cleanupVideo();
        };
        const captureFrame = () => {
          if (cancelled || video !== currentVideo) return;
          try {
            const canvas = document.createElement("canvas");
            canvas.width = currentVideo.videoWidth;
            canvas.height = currentVideo.videoHeight;
            if (!canvas.width || !canvas.height) throw new Error("视频没有可用画面");
            const context = canvas.getContext("2d");
            if (!context) throw new Error("浏览器无法读取视频帧");
            context.drawImage(currentVideo, 0, 0, canvas.width, canvas.height);
            setFrameUrl(canvas.toDataURL("image/jpeg", 0.76));
            setFrameState("ready");
          } catch (error) {
            setFrameFailure(error instanceof Error ? error.message : "浏览器无法读取视频帧");
            setFrameState("failed");
          }
          cleanupVideo();
        };
        currentVideo.onerror = fail;
        currentVideo.onloadedmetadata = () => {
          const target = Math.min(Math.max(0, frameTime), Math.max(0, currentVideo.duration - 0.08));
          if (!Number.isFinite(currentVideo.duration) || currentVideo.duration <= 0) { fail(); return; }
          if (target <= 0.01) currentVideo.onloadeddata = captureFrame;
          else currentVideo.currentTime = target;
        };
        currentVideo.onseeked = captureFrame;
        currentVideo.src = sourceUrl;
        frameTimeoutId = window.setTimeout(() => {
          if (!cancelled) {
            setFrameFailure("取帧超时，请检查原片连接");
            setFrameState("failed");
          }
          cleanupVideo();
        }, 12000);
      }));
    }, { root: root.closest(".thumb-row"), rootMargin: "120px" });
    observer.observe(root);
    return () => { cancelled = true; observer.disconnect(); cleanupVideo(); };
  }, [sourceUrl, storyboard.id, frameTime]);
  return <button ref={rootRef} type="button" className={`shot-thumb${selected ? " active" : ""}`} aria-pressed={selected} onClick={onClick}>
    <span className="shot-frame-visual" data-frame-state={frameState} title={frameFailure}>{frameUrl ? <img src={frameUrl} alt="" /> : <small>{frameState === "loading" ? "正在取帧…" : frameState === "failed" ? "原片帧不可用" : "等待取帧"}</small>}</span>
    <span className="shot-frame-copy"><b>{storyboard.title}</b><small>{storyboard.time_range}</small><small>{storyboard.status}</small></span>
  </button>;
}

function formatBytes(bytes = 0) {
  if (!bytes) return "0 B";
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function assetThumbClass(asset?: Asset) {
  if (asset?.asset_type === "角色") return "face-a";
  if (asset?.asset_type === "场景") return "scene";
  if (asset?.asset_type === "道具") return "prop";
  return "text";
}

function apiAssetUrl(path?: string) {
  if (!path) return "";
  if (/^https?:\/\//i.test(path)) return path;
  return `${API_BASE}${path.startsWith("/") ? path : `/${path}`}`;
}

function selectedAssetImage(asset?: Asset) {
  if (asset?.payload?.effectiveAssetSource === "source") return undefined;
  return asset?.images?.find((item) => item.is_selected);
}

function selectedAssetImageUrl(asset?: Asset) {
  const image = selectedAssetImage(asset);
  return apiAssetUrl(image?.file_url || image?.payload?.source_url);
}

function originalAssetImageUrl(asset?: Asset) {
  if (!asset) return "";
  return apiAssetUrl(
    pickText(asset?.payload, ["原资产图URL", "原资产截图URL"]) ||
    pickText(currentChild(asset), ["原资产图URL", "原资产截图URL"])
  );
}

function splitPromptAssetVariables(prompt: string, assets: Asset[]): PromptVariablePart[] {
  const aliasMap = new Map<string, Asset | null>();
  assets.forEach((asset) => {
    [...assetReferenceAliases(asset), asset.variable].filter((token) => token.startsWith("@") || token.startsWith("{{")).forEach((token) => {
      if (!aliasMap.has(token)) aliasMap.set(token, asset);
      else if (aliasMap.get(token)?.id !== asset.id) aliasMap.set(token, null);
    });
  });
  const aliases = Array.from(aliasMap.entries()).sort((left, right) => right[0].length - left[0].length);
  const isAsciiVariableChar = (character: string) => Boolean(character && /[A-Za-z0-9_-]/.test(character));
  const parts: PromptVariablePart[] = [];
  let textStart = 0;
  let index = 0;
  while (index < prompt.length) {
    const previous = prompt[index - 1] ?? "";
    const atBoundary = index === 0 || !/[A-Za-z0-9_@]/.test(previous);
    const known = aliases.find(([token]) => {
      if (!prompt.startsWith(token, index)) return false;
      if (token.startsWith("{{")) return true;
      return atBoundary && !isAsciiVariableChar(prompt[index + token.length] ?? "");
    });
    const unknownMatch = !known && atBoundary && prompt[index] === "@"
      ? prompt.slice(index).match(/^@[\p{L}\p{N}]+(?:[_-][\p{L}\p{N}]+)*/u)?.[0]
      : undefined;
    const token = known?.[0] ?? unknownMatch;
    if (!token) {
      index += 1;
      continue;
    }
    if (index > textStart) parts.push({ kind: "text", value: prompt.slice(textStart, index) });
    parts.push({ kind: "variable", token, asset: known?.[1] ?? undefined });
    index += token.length;
    textStart = index;
  }
  if (textStart < prompt.length) parts.push({ kind: "text", value: prompt.slice(textStart) });
  return parts;
}

function serializePromptEditorNode(node: Node): string {
  if (node.nodeType === Node.TEXT_NODE) return node.textContent ?? "";
  if (node.nodeType !== Node.ELEMENT_NODE && node.nodeType !== Node.DOCUMENT_FRAGMENT_NODE) return "";
  const element = node as HTMLElement;
  const token = node.nodeType === Node.ELEMENT_NODE ? element.dataset.promptToken : undefined;
  if (token !== undefined) return token;
  if (element.tagName === "BR") return "\n";
  let output = "";
  Array.from(element.childNodes).forEach((child, index) => {
    const isBlock = child.nodeType === Node.ELEMENT_NODE && ["DIV", "P"].includes((child as HTMLElement).tagName);
    if (index > 0 && isBlock && output && !output.endsWith("\n")) output += "\n";
    output += serializePromptEditorNode(child);
  });
  return output;
}

function readPromptEditor(editor: HTMLElement | null): string {
  if (!editor) return "";
  return serializePromptEditorNode(editor);
}

function renderPromptEditor(editor: HTMLElement, prompt: string, assets: Asset[], openImage: (url: string, title: string) => void) {
  editor.replaceChildren();
  const parts = splitPromptAssetVariables(prompt, assets);
  for (const part of parts) {
    if (part.kind === "text") {
      editor.append(document.createTextNode(part.value));
      continue;
    }
    if (!part.asset) {
      editor.append(document.createTextNode(part.token));
      continue;
    }
    const asset = part.asset;
    const token = document.createElement("span");
    token.className = "prompt-variable-chip";
    token.contentEditable = "false";
    token.dataset.promptToken = part.token;
    token.setAttribute("role", "button");
    token.setAttribute("tabindex", "0");
    token.setAttribute("aria-label", `${part.token}，点击查看资产图片`);
    const text = document.createElement("span");
    text.className = "prompt-variable-chip-token";
    text.textContent = part.token;
    token.append(text);
    const thumbUrl = selectedAssetImageUrl(asset) || originalAssetImageUrl(asset);
    if (thumbUrl) {
      const image = document.createElement("img");
      image.src = thumbUrl;
      image.alt = "";
      image.onerror = () => {
        image.hidden = true;
        const missing = document.createElement("small");
        missing.className = "prompt-variable-chip-missing";
        missing.textContent = "图片不可用";
        token.append(missing);
      };
      token.append(image);
    } else {
      const missing = document.createElement("small");
      missing.className = "prompt-variable-chip-missing";
      missing.textContent = "无图";
      token.append(missing);
    }
    const showImage = () => { if (thumbUrl) openImage(thumbUrl, `${assetDisplayName(asset)} · ${part.token}`); };
    token.addEventListener("click", showImage);
    token.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); showImage(); } });
    editor.append(token);
  }
}

function promptSelectionOffset(editor: HTMLElement): number | null {
  const selection = window.getSelection();
  if (!selection?.rangeCount || !editor.contains(selection.anchorNode)) return null;
  const range = document.createRange();
  range.selectNodeContents(editor);
  range.setEnd(selection.anchorNode!, selection.anchorOffset);
  return serializePromptEditorNode(range.cloneContents()).length;
}

function promptSelectedText(editor: HTMLElement): string {
  const selection = window.getSelection();
  if (!selection?.rangeCount || selection.isCollapsed || !editor.contains(selection.anchorNode) || !editor.contains(selection.focusNode)) return "";
  return serializePromptEditorNode(selection.getRangeAt(0).cloneContents());
}

function promptDomPoint(editor: HTMLElement, target: number): { node: Node; offset: number } {
  let position = 0;
  let result: { node: Node; offset: number } | null = null;
  const visit = (parent: Node) => {
    const children = Array.from(parent.childNodes);
    for (let index = 0; index < children.length && !result; index += 1) {
      const child = children[index];
      const isBlock = child.nodeType === Node.ELEMENT_NODE && ["DIV", "P"].includes((child as HTMLElement).tagName);
      if (index > 0 && isBlock && position > 0) {
        if (target <= position) { result = { node: parent, offset: index }; return; }
        position += 1;
      }
      if (child.nodeType === Node.TEXT_NODE) {
        const length = child.textContent?.length ?? 0;
        if (target <= position + length) { result = { node: child, offset: Math.max(0, target - position) }; return; }
        position += length;
      } else if (child.nodeType === Node.ELEMENT_NODE && (child as HTMLElement).dataset.promptToken !== undefined) {
        const length = ((child as HTMLElement).dataset.promptToken ?? "").length;
        const indexInParent = Array.prototype.indexOf.call(parent.childNodes, child);
        if (target <= position) { result = { node: parent, offset: indexInParent }; return; }
        if (target <= position + length) { result = { node: parent, offset: indexInParent + (target > position ? 1 : 0) }; return; }
        position += length;
      } else if (child.nodeType === Node.ELEMENT_NODE && (child as HTMLElement).tagName === "BR") {
        const indexInParent = Array.prototype.indexOf.call(parent.childNodes, child);
        if (target <= position) { result = { node: parent, offset: indexInParent }; return; }
        position += 1;
      } else visit(child);
    }
  };
  visit(editor);
  return result ?? { node: editor, offset: editor.childNodes.length };
}

function insertPromptToken(editor: HTMLElement, start: number, end: number, value: string, assets: Asset[], openImage: (url: string, title: string) => void) {
  const from = promptDomPoint(editor, start);
  const to = promptDomPoint(editor, end);
  const range = document.createRange();
  range.setStart(from.node, from.offset);
  range.setEnd(to.node, to.offset);
  range.deleteContents();
  const holder = document.createElement("div");
  renderPromptEditor(holder, value, assets, openImage);
  const inserted = holder.firstChild;
  if (!inserted) return;
  range.insertNode(inserted);
  const selection = window.getSelection();
  const caret = document.createRange();
  caret.setStartAfter(inserted);
  caret.collapse(true);
  selection?.removeAllRanges();
  selection?.addRange(caret);
}

function groupOriginalImageUrl(group: AssetGroup) {
  for (const item of group.items) {
    const url = originalAssetImageUrl(item);
    if (url) return url;
  }
  return "";
}

function groupConfirmedImageUrl(group: AssetGroup) {
  for (const item of group.items) {
    if (item.status !== "已确认") continue;
    const url = selectedAssetImageUrl(item);
    if (url) return url;
  }
  return "";
}

function uploadProjectTitle(files: ArrayLike<File> | null, fallback: string) {
  const first = files?.[0] as (File & { webkitRelativePath?: string }) | undefined;
  if (!first) return fallback;
  const relative = first.webkitRelativePath || "";
  const folder = relative.split("/").filter(Boolean)[0];
  if (folder) return folder;
  return first.name.replace(/\.[^.]+$/, "") || fallback;
}

function isTaskRunning(task?: Task | null) {
  return Boolean(task && ["运行中", "pending", "processing"].includes(task.status));
}

function getInitialWorkbenchPaneWidths() {
  const workbenchWidth = Math.max(window.innerWidth, 940);
  const gap = Math.min(14, Math.max(8, workbenchWidth * 0.01));
  const availableTracks = workbenchWidth - gap * 2 - gap * 4 - 16 - 360;
  const minimumTotal = 230 + 260;
  const preferredAssets = Math.min(300, Math.max(260, Math.round(workbenchWidth * 0.21)));
  const preferredPreview = Math.min(380, Math.max(320, Math.round(workbenchWidth * 0.26)));
  const preferredExtra = Math.max(0, preferredAssets - 230) + Math.max(0, preferredPreview - 260);
  const extraBudget = Math.max(0, Math.min(preferredExtra, availableTracks - minimumTotal));
  const scale = preferredExtra > 0 ? extraBudget / preferredExtra : 1;
  return {
    assets: Math.floor(230 + Math.max(0, preferredAssets - 230) * scale),
    preview: Math.floor(260 + Math.max(0, preferredPreview - 260) * scale),
  };
}

// 原片对比：只循环播放当前选中分镜对应的原片片段，分镜切换统一由底部卡片承担。
function OriginalClipPreview({ sourceUrl, storyboard }: {
  sourceUrl: string;
  storyboard: Storyboard;
}) {
  const range = parseRange(storyboard.time_range || "");
  return (
    <div className="original-clip-preview">
      <div className="source-preview-label">原片片段 · {storyboard.title} · {storyboard.time_range}（循环）</div>
      <video
        key={storyboard.id}
        src={sourceUrl}
        controls
        playsInline
        autoPlay
        muted
        onLoadedMetadata={(event) => {
          const video = event.currentTarget;
          if (Number.isFinite(range.start) && range.start < video.duration) video.currentTime = range.start;
        }}
        onTimeUpdate={(event) => {
          const video = event.currentTarget;
          if (range.end > range.start && video.currentTime >= range.end) {
            video.currentTime = range.start;
            void video.play().catch(() => undefined);
          }
        }}
      />
    </div>
  );
}

export function App() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectsLoading, setProjectsLoading] = useState(true);
  const [projectsError, setProjectsError] = useState("");
  const [projectSearch, setProjectSearch] = useState("");
  const [projectPage, setProjectPage] = useState(0);
  const [sourcePage, setSourcePage] = useState(0);
  const [assetStatusFilter, setAssetStatusFilter] = useState<"all" | "pending" | "confirmed">("all");
  const [assetSearchQuery, setAssetSearchQuery] = useState("");
  const [assetSearchOpen, setAssetSearchOpen] = useState(false);
  const [assetBatchAction, setAssetBatchAction] = useState<"review" | "generate" | "delete" | "group" | null>(null);
  const [assetDeleteTarget, setAssetDeleteTarget] = useState<{ ids: string[]; labels: string[] } | null>(null);
  const [assetRenameTarget, setAssetRenameTarget] = useState<{ id: string; name: string } | null>(null);
  const [assetRenameDraft, setAssetRenameDraft] = useState("");
  const [currentProject, setCurrentProject] = useState<Project | null>(null);
  const [episodes, setEpisodes] = useState<Episode[]>([]);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [customAssetGroups, setCustomAssetGroups] = useState<CustomAssetGroup[]>([]);
  const [assetSelectionMode, setAssetSelectionMode] = useState(false);
  const [selectedAssetGroupKeys, setSelectedAssetGroupKeys] = useState<Set<string>>(() => new Set());
  const [assetGroupModal, setAssetGroupModal] = useState<"create" | "move" | null>(null);
  const [assetGroupNameDraft, setAssetGroupNameDraft] = useState("");
  const [assetGroupTargetId, setAssetGroupTargetId] = useState("");
  const [selectedCustomGroupId, setSelectedCustomGroupId] = useState<string | null>(null);
  const [storyboardAssetGroupId, setStoryboardAssetGroupId] = useState("all");
  const [storyboards, setStoryboards] = useState<Storyboard[]>([]);
  const [generations, setGenerations] = useState<VideoGeneration[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [screen, setScreen] = useState<Screen>("home");
  const [assetSidebarCollapsed, setAssetSidebarCollapsed] = useState(false);
  const [previewPanelCollapsed, setPreviewPanelCollapsed] = useState(false);
  const [initialWorkbenchPaneWidths] = useState(getInitialWorkbenchPaneWidths);
  const [assetPaneWidth, setAssetPaneWidth] = useState(initialWorkbenchPaneWidths.assets);
  const [previewPaneWidth, setPreviewPaneWidth] = useState(initialWorkbenchPaneWidths.preview);
  const [selectedSource, setSelectedSource] = useState<SelectedSource | null>(null);
  const [selectedFiles, setSelectedFiles] = useState<File[] | null>(null);
  const [uploadState, setUploadState] = useState<"idle" | "selected" | "uploading" | "uploaded" | "failed">("idle");
  const [modal, setModal] = useState<ModalName>(null);
  const [imageView, setImageView] = useState<ImageView>(null);
  const [targetCountry, setTargetCountry] = useState("美国");
  const [targetLanguage, setTargetLanguage] = useState("英语");
  const [remakeMode, setRemakeMode] = useState<Project["remake_mode"]>("overseas_localization");
  const [sourceLanguage, setSourceLanguage] = useState("");
  const [storyboardVisualStyle, setStoryboardVisualStyle] = useState<Project["storyboard_visual_style"]>("live_action");
  const [replaceCharacters, setReplaceCharacters] = useState(false);
  const [replaceScenes, setReplaceScenes] = useState(false);
  const [assetPromptDraft, setAssetPromptDraft] = useState("");
  const [assetImageModel, setAssetImageModel] = useState(DEFAULT_ASSET_IMAGE_MODEL);
  const [assetImageCount, setAssetImageCount] = useState(1);
  const assetImageAspectRatio = "16:9";
  const assetPromptDraftRevision = useRef(new Map<string, number>());
  const selectedAssetIdForPrompt = useRef<string | null>(null);
  const [frameRatio, setFrameRatio] = useState("9:16");
  const [resolution, setResolution] = useState("720p");
  const [videoModel, setVideoModel] = useState(DEFAULT_VIDEO_MODEL);
  const [videoDuration, setVideoDuration] = useState(4);
  const [selectedGenerationId, setSelectedGenerationId] = useState<string | null>(null);
  const [shotMenu, setShotMenu] = useState<{ id: string; x: number; y: number } | null>(null);
  const [deletingStoryboard, setDeletingStoryboard] = useState<Storyboard | null>(null);
  const [voiceEditor, setVoiceEditor] = useState<VoiceEditorState | null>(null);
  const [voiceUploading, setVoiceUploading] = useState(false);
  const [voiceRefPlaying, setVoiceRefPlaying] = useState(false);
  const voiceRefAudioRef = useRef<HTMLAudioElement | null>(null);
  const [styleType, setStyleType] = useState("写实通用");
  const [assetTab, setAssetTab] = useState<AssetTab>("角色");
  const [assetView, setAssetView] = useState<AssetView>("overview");
  const [selectedEpisodeId, setSelectedEpisodeId] = useState<string | null>(null);
  const [episodeMenuOpen, setEpisodeMenuOpen] = useState(false);
  const blankEpisodeUploadRef = useRef<HTMLInputElement>(null);
  const [selectedAssetId, setSelectedAssetId] = useState<string | null>(null);
  const [selectedStoryboardId, setSelectedStoryboardId] = useState<string | null>(null);
  const [segmentPanel, setSegmentPanel] = useState<SegmentPanel>("shot-prompt");
  // 右侧预览区分两种：整集原片参考（episode）与切分原片片段对比（segments）。
  const [previewMode, setPreviewMode] = useState<"episode" | "segments">("episode");
  const [storyboardSaveStates, setStoryboardSaveStates] = useState<Record<string, "saved" | "saving" | "failed">>({});
  const [activeAnalysisTask, setActiveAnalysisTask] = useState<Task | null>(null);
  const [analysisProgress, setAnalysisProgress] = useState({ story: 0, asset: 0 });
  const [analysisNow, setAnalysisNow] = useState(Date.now());
  const [mentionQuery, setMentionQuery] = useState<string | null>(null);
  const [mentionRange, setMentionRange] = useState<{ start: number; end: number } | null>(null);
  const [mentionMenuPosition, setMentionMenuPosition] = useState({ left: 12, top: 12 });
  const [activeMentionIndex, setActiveMentionIndex] = useState(0);
  const [storyboardAssetSearch, setStoryboardAssetSearch] = useState("");
  const [storyboardAssetLayout, setStoryboardAssetLayout] = useState<"list" | "grid">("grid");
  const [storyboardAssetTab, setStoryboardAssetTab] = useState<"角色" | "场景" | "道具" | "视频帧" | "本地素材">("角色");
  // 首尾帧参考图选择器：点击提示词区的首帧/尾帧槽位时弹出缩小版资产列表
  const [framePicker, setFramePicker] = useState<{ kind: "首帧" | "尾帧" } | null>(null);
  const [framePickerTab, setFramePickerTab] = useState<"角色" | "场景" | "道具">("角色");
  // 生成模式（全能参考/仅首帧/首尾帧）——前端状态，控制生成前的首尾帧校验提示
  const [videoGenMode, setVideoGenMode] = useState("全能参考");
  const [busy, setBusy] = useState(false);
  const [storyboardLoading, setStoryboardLoading] = useState(false);
  const [generatingAssetIds, setGeneratingAssetIds] = useState<Set<string>>(() => new Set());
  const generatingAssetIdsRef = useRef<Set<string>>(new Set());
  const [message, setMessage] = useState("准备就绪");
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const folderInputRef = useRef<HTMLInputElement | null>(null);
  const retryUploadInputRef = useRef<HTMLInputElement | null>(null);
  const promptInputRef = useRef<HTMLDivElement | null>(null);
  const promptEditorSync = useRef<{ storyboardId: string; value: string; assets: Asset[] } | null>(null);
  const promptCompositionActive = useRef(false);
  const storyboardPatchQueue = useRef<Promise<void>>(Promise.resolve());
  const storyboardSaveRevisions = useRef(new Map<string, number>());
  const storyboardSavedBodies = useRef(new Map<string, string>());
  const storyboardPromptSaveRequests = useRef(new Map<string, { prompt: string; promise: Promise<boolean> }>());
  const assetImageInputRef = useRef<HTMLInputElement | null>(null);
  const pollingTaskIds = useRef(new Set<string>());
  const workbenchRef = useRef<HTMLElement | null>(null);
  const splitterDragRef = useRef<{ pane: WorkbenchPane; pointerId: number; startX: number; startWidth: number; target: HTMLButtonElement } | null>(null);
  const storyboardReferenceRevision = useRef(new Map<string, number>());
  const storyboardReferenceDrafts = useRef(new Map<string, { projectId: string; references: string[] }>());

  const selectedEpisode = useMemo(
    () => episodes.find((episode) => episode.id === selectedEpisodeId) ?? null,
    [episodes, selectedEpisodeId]
  );
  const selectedAsset = useMemo(
    () => assets.find((asset) => asset.id === selectedAssetId) ?? assets[0] ?? null,
    [assets, selectedAssetId]
  );
  const selectedStoryboard = useMemo(
    () => storyboards.find((storyboard) => storyboard.id === selectedStoryboardId) ?? storyboards[0] ?? null,
    [storyboards, selectedStoryboardId]
  );
  const storyboardReferenceReview = Array.isArray(selectedStoryboard?.payload?.["资产引用待核对"])
    ? selectedStoryboard.payload["资产引用待核对"] as Array<Record<string, unknown>>
    : [];
  const storyboardHasUnresolvedReferences = storyboardReferenceReview.length > 0;
  const storyboardSaveState = selectedStoryboard ? storyboardSaveStates[selectedStoryboard.id] ?? "saved" : "saved";
  const assetGroups = useMemo(() => {
    const map = new Map<string, AssetGroup>();
    assets.forEach((asset) => {
      const key = mainAssetKey(asset);
      const existing = map.get(key);
      if (existing) {
        existing.items.push(asset);
      } else {
        map.set(key, {
          key,
          type: asset.asset_type,
          name: asset.name,
          localizedName: asset.localized_name,
          items: [asset],
        });
      }
    });
    return Array.from(map.values());
  }, [assets]);
  const normalizedAssetQuery = assetSearchQuery.trim().toLowerCase();
  const filteredGroups = useMemo(
    () => assetGroups.filter((group) => (assetTab === "资产进度" || group.type === assetTab)
      && !customAssetGroups.some((customGroup) => customGroup.members.some((member) => member.assetKey === group.key))
      && (assetStatusFilter === "all" || group.items.some((item) => assetStatusFilter === "confirmed" ? item.status === "已确认" : item.status !== "已确认"))
      && (!normalizedAssetQuery || `${group.name} ${group.localizedName} ${group.items.map((item) => `${item.name} ${item.localized_name} ${item.variable ?? ""} ${item.display_variable ?? ""}`).join(" ")}`.toLowerCase().includes(normalizedAssetQuery))),
    [assetGroups, assetTab, assetStatusFilter, customAssetGroups, normalizedAssetQuery]
  );
  const allVisibleAssetsSelected = filteredGroups.length > 0 && filteredGroups.every((group) => selectedAssetGroupKeys.has(group.key));
  const searchedProjects = useMemo(() => projects.filter((project) => (project.title + " " + project.target_country + " " + project.target_language + " " + project.source_language).toLowerCase().includes(projectSearch.trim().toLowerCase())), [projects, projectSearch]);
  const projectPageSize = 8;
  const pagedProjects = searchedProjects.slice(projectPage * projectPageSize, (projectPage + 1) * projectPageSize);
  const roleCount = assets.filter((asset) => asset.asset_type === "角色").length;
  const sceneCount = assets.filter((asset) => asset.asset_type === "场景").length;
  const propCount = assets.filter((asset) => asset.asset_type === "道具").length;
  const customGroupByAssetKey = useMemo(() => {
    const map = new Map<string, CustomAssetGroup>();
    customAssetGroups.forEach((group) => group.members.forEach((member) => map.set(member.assetKey, group)));
    return map;
  }, [customAssetGroups]);
  const customGroupById = useMemo(() => new Map(customAssetGroups.map((group) => [group.id, group])), [customAssetGroups]);
  const storyboardFilteredAssets = useMemo(
    () => assets.filter((asset) => {
      if (asset.asset_type !== storyboardAssetTab) return false;
      if (!`${assetDisplayName(asset)} ${assetDisplayVariable(asset)}`.toLowerCase().includes(storyboardAssetSearch.toLowerCase())) return false;
      const group = customGroupByAssetKey.get(mainAssetKey(asset));
      if (storyboardAssetGroupId === "ungrouped") return !group;
      if (storyboardAssetGroupId !== "all") return group?.id === storyboardAssetGroupId;
      return true;
    }),
    [assets, customGroupByAssetKey, storyboardAssetSearch, storyboardAssetTab, storyboardAssetGroupId]
  );
  const storyboardCustomGroups = useMemo(
    () => customAssetGroups.filter((group) => group.assetType === storyboardAssetTab),
    [customAssetGroups, storyboardAssetTab]
  );
  const selectedAssetCustomGroup = selectedAsset ? customGroupByAssetKey.get(mainAssetKey(selectedAsset)) ?? null : null;
  const selectedCustomGroup = selectedCustomGroupId ? customGroupById.get(selectedCustomGroupId) ?? null : null;
  const selectedCustomGroupGroups = useMemo(() => {
    if (!selectedCustomGroup) return [] as AssetGroup[];
    const memberKeys = new Set(selectedCustomGroup.members.map((member) => member.assetKey));
    return assetGroups.filter((group) => memberKeys.has(group.key));
  }, [assetGroups, selectedCustomGroup]);
  // 稳定变量（如 {{CHAR_001_SUB_01}}）按集隔离，同一变量在不同集会重复出现。
  // 后端按"项目子资产Key"合并资产，episode_id 只保留首条而"出现集数"跨多集，
  // 因此必须按"出现集数"（episode_index）过滤，否则跨集多命中会被误判为歧义、缩略图无法解析。
  const storyboardScopeAssets = useMemo(() => {
    const episodeIndex = selectedEpisode?.episode_index;
    return assets.filter((asset) => {
      const appearances = asset.payload?.["出现集数"];
      if (!episodeIndex || !Array.isArray(appearances) || !appearances.length) return true;
      return appearances.includes(episodeIndex);
    });
  }, [assets, selectedEpisode]);
  const selectedStoryboardReferenceTokens = useMemo(() => {
    if (!selectedStoryboard) return [];
    const payloadRefs = splitAssetVariableValues(selectedStoryboard.payload?.["引用资产变量"]);
    return canonicalizeAssetReferences(payloadRefs, storyboardScopeAssets);
  }, [storyboardScopeAssets, selectedStoryboard]);
  const selectedStoryboardAssets = useMemo(() => {
    const matches = selectedStoryboardReferenceTokens
      .map((token) => resolveUniqueAssetReference(token, storyboardScopeAssets))
      .filter((asset): asset is Asset => Boolean(asset));
    return Array.from(new Map(matches.map((asset) => [asset.id, asset])).values());
  }, [storyboardScopeAssets, selectedStoryboardReferenceTokens]);
  function getLatestStoryboardReferenceTokens(storyboard: Storyboard) {
    const draft = storyboardReferenceDrafts.current.get(storyboard.id);
    if (draft && draft.projectId === currentProject?.id) return canonicalizeAssetReferences(draft.references, storyboardScopeAssets);
    if (selectedStoryboard?.id === storyboard.id) return [...selectedStoryboardReferenceTokens];
    return canonicalizeAssetReferences(splitAssetVariableValues(storyboard.payload?.["引用资产变量"]), storyboardScopeAssets);
  }
  const selectedStoryboardReferenceItems = useMemo(() => {
    const seen = new Set<string>();
    return selectedStoryboardReferenceTokens.flatMap((token) => {
      const asset = resolveUniqueAssetReference(token, storyboardScopeAssets);
      const key = asset ? `asset:${asset.id}` : `reference:${token}`;
      if (seen.has(key)) return [];
      seen.add(key);
      return [{ key, token, asset, label: asset ? assetDisplayName(asset) : token }];
    });
  }, [assets, selectedStoryboardReferenceTokens]);
  const workbenchStyle = {
    "--asset-pane-width": `${assetPaneWidth}px`,
    "--preview-pane-width": `${previewPaneWidth}px`,
  } as CSSProperties;
  const selectedStoryboardPrompt = useMemo(
    () => selectedStoryboard?.payload?.["使用回退占位"] === true ? "" : String(
      selectedStoryboard?.payload?.["分镜生成正文"] ?? selectedStoryboard?.payload?.["源分镜文本"] ?? selectedStoryboard?.prompt ?? ""
    ),
    [selectedStoryboard]
  );
  useLayoutEffect(() => {
    if (!selectedStoryboard || !promptInputRef.current) return;
    const previous = promptEditorSync.current;
    if (previous?.storyboardId === selectedStoryboard.id && promptCompositionActive.current) return;
    if (previous?.storyboardId === selectedStoryboard.id && previous.value === selectedStoryboardPrompt && previous.assets === storyboardScopeAssets) return;
    renderPromptEditor(promptInputRef.current, selectedStoryboardPrompt, storyboardScopeAssets, openImage);
    promptEditorSync.current = { storyboardId: selectedStoryboard.id, value: selectedStoryboardPrompt, assets: storyboardScopeAssets };
  }, [selectedStoryboard?.id, selectedStoryboardPrompt, storyboardScopeAssets]);
  const mentionOptions = useMemo(() => {
    const query = (mentionQuery ?? "").toLowerCase();
    return storyboardScopeAssets.filter((asset) => {
      if (!query) return true;
      return `${asset.asset_type} ${assetDisplayName(asset)} ${assetDisplayVariable(asset)}`.toLowerCase().includes(query);
    }).slice(0, 40);
  }, [storyboardScopeAssets, mentionQuery]);
  useEffect(() => setActiveMentionIndex(0), [mentionQuery]);
  useEffect(() => {
    document.querySelector(`[data-mention-index="${activeMentionIndex}"]`)?.scrollIntoView({ block: "nearest" });
  }, [activeMentionIndex]);
  useEffect(() => {
    if (mentionQuery === null) return;
    const update = () => updateMentionMenuPosition();
    window.addEventListener("resize", update);
    document.addEventListener("scroll", update, true);
    return () => {
      window.removeEventListener("resize", update);
      document.removeEventListener("scroll", update, true);
    };
  }, [mentionQuery]);
  useEffect(() => {
    if (!selectedStoryboard) return;
    const savedBody = selectedStoryboard.payload?.["使用回退占位"] === true
      ? ""
      : String(selectedStoryboard.payload?.["分镜生成正文"] ?? selectedStoryboard.payload?.["源分镜文本"] ?? selectedStoryboard.prompt ?? "");
    if (selectedStoryboardPrompt === savedBody) return;
    const storyboard = selectedStoryboard;
    const timer = window.setTimeout(() => { void savePrompt(storyboard, selectedStoryboardPrompt); }, 700);
    return () => window.clearTimeout(timer);
  }, [selectedStoryboard?.id, selectedStoryboardPrompt]);
  const isSingleUpload = selectedSource?.kind === "local" && selectedSource.uploadMode === "single";
  const sourceEpisodeCount = isSingleUpload
    ? 1
    : selectedSource?.kind === "local"
      ? Math.max(episodes.length, selectedFiles?.length ?? 0)
      : Math.max(episodes.length, 40);
  const uploadedEpisodeCount = episodes.filter((episode) => episode.ark_file_id || episode.status.includes("已上传")).length;
  const hasUploadedEpisodes = uploadedEpisodeCount > 0;
  const effectiveUploadState = hasUploadedEpisodes ? "uploaded" : uploadState;
  const failedEpisodeCount = effectiveUploadState === "failed" ? Math.max(1, (selectedFiles?.length ?? 0) - uploadedEpisodeCount) : 0;
  const uploadingEpisodeCount = effectiveUploadState === "uploading" ? Math.max(sourceEpisodeCount - uploadedEpisodeCount, 0) : 0;
  const uploadInProgress = uploadState === "uploading";
  const allEpisodesUploaded = episodes.length > 0 && episodes.every((episode) => Boolean(episode.ark_file_id || episode.status.includes("已上传")));
  const analysisLocked = tasks.some((task) =>
    (task.task_type === "series_analysis" || task.task_type === "asset_extraction")
    && (task.status === "成功" || task.status === "运行中"));
  const canStartSeriesAnalysis = !analysisLocked && !uploadInProgress && allEpisodesUploaded;
  const sourceEpisodeRows = useMemo(() => episodes.length ? episodes : Array.from({ length: Math.min(sourceEpisodeCount, 7) }).map((_, index) => ({
    id: "pending-" + index, episode_index: index + 1, filename: selectedFiles?.[index]?.name ?? String(index + 1).padStart(3, "0") + ".mp4",
    size_bytes: selectedFiles?.[index]?.size ?? 0, status: effectiveUploadState, source_url: "", ark_file_id: "", ark_file_status: "", ark_file_error: "", created_at: "",
  })), [episodes, sourceEpisodeCount, selectedFiles, effectiveUploadState]);
  const sourcePageSize = 10;
  const pagedEpisodes = sourceEpisodeRows.slice(sourcePage * sourcePageSize, (sourcePage + 1) * sourcePageSize);

  function pickDefaultEpisodeId(items: Episode[], currentId: string | null) {
    if (currentId && items.some((episode) => episode.id === currentId)) return currentId;
    const candidates = [...items].sort((a, b) => {
      const sizeDelta = (b.size_bytes ?? 0) - (a.size_bytes ?? 0);
      if (sizeDelta !== 0) return sizeDelta;
      return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
    });
    return candidates[0]?.id ?? null;
  }

  async function loadProjects() {
    setProjectsLoading(true);
    setProjectsError("");
    try {
      const data = await request<{ projects: Project[] }>("/api/projects");
      setProjects(data.projects);
      setProjectPage(0);
    } catch (error) {
      const detail = error instanceof Error ? error.message : "项目列表加载失败";
      setProjectsError(detail);
      throw error;
    } finally {
      setProjectsLoading(false);
    }
  }

  async function selectProject(projectId: string) {
    const data = await request<{ project: Project; episodes: Episode[] }>(`/api/projects/${projectId}`);
    const defaultEpisodeId = pickDefaultEpisodeId(data.episodes, selectedEpisodeId);
    setSourcePage(0);
    setCurrentProject(data.project);
    setTargetCountry(data.project.target_country);
    const savedLanguage = data.project.target_language || "英语";
    setTargetLanguage(({ English: "英语", Japanese: "日语", Korean: "韩语", Spanish: "西班牙语", French: "法语" } as Record<string, string>)[savedLanguage] || savedLanguage);
    setRemakeMode(data.project.remake_mode ?? "overseas_localization");
    setSourceLanguage(data.project.source_language ?? "");
    setStoryboardVisualStyle(data.project.storyboard_visual_style ?? "live_action");
    setReplaceCharacters(Boolean(data.project.replace_characters));
    setReplaceScenes(Boolean(data.project.replace_scenes));
    setEpisodes(data.episodes);
    if (data.episodes.some((episode) => episode.ark_file_id || episode.status.includes("已上传"))) {
      setUploadState("uploaded");
    }
    setSelectedEpisodeId(defaultEpisodeId);
    const [, , , , loadedTasks] = await Promise.all([
      loadAssets(projectId),
      loadStoryboards(projectId, defaultEpisodeId),
      loadGenerations(projectId),
      loadCustomAssetGroups(projectId),
      loadTasks(projectId),
    ]);
    const recoveryTaskId = sessionStorage.getItem("activeAnalysisTaskId");
    const storedRecoveryTask = loadedTasks.find((task) => task.id === recoveryTaskId);
    const recentRunningTask = loadedTasks.find((task) =>
      task.episode_id === defaultEpisodeId
      && task.task_type === "asset_extraction"
      && isTaskRunning(task)
      && Date.now() - new Date(task.created_at).getTime() < 30 * 60 * 1000
    );
    const recoveryTask = storedRecoveryTask || recentRunningTask;
    if (recoveryTask) {
      if (isTaskRunning(recoveryTask)) sessionStorage.setItem("activeAnalysisTaskId", recoveryTask.id);
      setActiveAnalysisTask(recoveryTask);
      setScreen("analysis");
      setAnalysisProgress(isTaskRunning(recoveryTask) ? { story: 8, asset: 8 } : { story: 100, asset: 100 });
      if (isTaskRunning(recoveryTask)) {
        setMessage(recoveryTask.message || "后台分析任务仍在运行");
        if (!pollingTaskIds.current.has(recoveryTask.id)) {
          pollingTaskIds.current.add(recoveryTask.id);
          void pollTask(recoveryTask.id, async (doneTask) => {
            setActiveAnalysisTask(doneTask);
            await Promise.all([loadAssets(projectId), loadTasks(projectId)]);
            setAnalysisProgress({ story: 100, asset: 100 });
            setMessage(doneTask.status === "失败" ? doneTask.message : "分析完成，可以进入设定确认资产");
            sessionStorage.removeItem("activeAnalysisTaskId");
            pollingTaskIds.current.delete(doneTask.id);
          });
        }
      } else {
        setMessage(recoveryTask.status === "失败" ? recoveryTask.message : "分析完成，可以进入设定确认资产");
        sessionStorage.removeItem("activeAnalysisTaskId");
      }
    }
  }

  function clearCurrentWorkspace() {
    setEpisodes([]);
    setAssets([]);
    setCustomAssetGroups([]);
    setAssetSelectionMode(false);
    setSelectedAssetGroupKeys(new Set());
    setAssetGroupModal(null);
    setSelectedCustomGroupId(null);
    setStoryboardAssetGroupId("all");
    setStoryboards([]);
    setGenerations([]);
    setTasks([]);
    setSelectedEpisodeId(null);
    setSelectedAssetId(null);
    setSelectedStoryboardId(null);
    setActiveAnalysisTask(null);
    setStoryboardLoading(false);
    setAnalysisProgress({ story: 0, asset: 0 });
  }

  async function createProjectForUpload(title: string) {
    const data = await request<{ project: Project }>("/api/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title, targetCountry, targetLanguage, remakeMode, sourceLanguage, storyboardVisualStyle: remakeMode === "cg_3d" ? "cg_3d" : storyboardVisualStyle, replaceCharacters, replaceScenes }),
    });
    setStoryboardVisualStyle(data.project.storyboard_visual_style ?? "live_action");
    setCurrentProject(data.project);
    clearCurrentWorkspace();
    setProjects((items) => [data.project, ...items.filter((item) => item.id !== data.project.id)]);
    return data.project;
  }

  async function loadAssets(projectId = currentProject?.id) {
    if (!projectId) return;
    const data = await request<{ assets: Asset[] }>(`/api/projects/${projectId}/assets`);
    setAssets(data.assets);
  }

  async function loadCustomAssetGroups(projectId = currentProject?.id) {
    if (!projectId) return [] as CustomAssetGroup[];
    const data = await request<{ groups: CustomAssetGroup[] }>(`/api/projects/${projectId}/asset-groups`);
    setCustomAssetGroups(data.groups);
    return data.groups;
  }

  function assetAppearsInSelectedEpisode(asset: Asset) {
    const appearances = asset.payload?.["出现集数"];
    if (!selectedEpisode || !Array.isArray(appearances) || !appearances.length) return true;
    return appearances.includes(selectedEpisode.episode_index);
  }

  function toggleAssetGroupSelection(group: AssetGroup) {
    if (!assetSelectionMode) return;
    setSelectedAssetGroupKeys((current) => {
      const next = new Set(current);
      const firstKey = [...next][0];
      const firstGroup = assetGroups.find((item) => item.key === firstKey);
      if (!next.has(group.key) && firstGroup && firstGroup.type !== group.type) {
        setMessage("批量操作只能选择同类资产");
        return current;
      }
      if (next.has(group.key)) next.delete(group.key);
      else next.add(group.key);
      return next;
    });
  }

  function selectedAssetIdsForGrouping() {
    return assetGroups
      .filter((group) => selectedAssetGroupKeys.has(group.key))
      .map((group) => group.items[0]?.id)
      .filter((id): id is string => Boolean(id));
  }

  function openAssetRename(asset: Asset) {
    setAssetRenameTarget({ id: asset.id, name: asset.name });
    setAssetRenameDraft(asset.name);
  }

  async function submitAssetRename() {
    if (!currentProject || !assetRenameTarget) return;
    const name = assetRenameDraft.trim();
    if (!name) {
      setMessage("资产名称不能为空");
      return;
    }
    setBusy(true);
    try {
      const data = await request<{ asset: Asset }>(`/api/projects/${currentProject.id}/assets/${assetRenameTarget.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, localizedName: name }),
      });
      setAssets((items) => items.map((item) => (item.id === data.asset.id ? data.asset : item)));
      setAssetRenameTarget(null);
      setMessage("资产名称已更新");
      await loadAssets(currentProject.id);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "重命名失败");
    } finally {
      setBusy(false);
    }
  }

  async function createBlankAsset(overrideType?: "角色" | "场景" | "道具") {
    if (!currentProject) return;
    const assetType = overrideType ?? (assetTab === "资产进度" ? "角色" : assetTab) as "角色" | "场景" | "道具";
    setBusy(true);
    try {
      const data = await request<{ asset: Asset }>(`/api/projects/${currentProject.id}/assets`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ assetType }),
      });
      await loadAssets(currentProject.id);
      setAssetStatusFilter("all");
      setAssetSearchQuery("");
      setAssetSearchOpen(false);
      setMessage(`已新建空白${assetType}「${data.asset.name}」，点击卡片填写描述后可生成`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "新建资产失败");
    } finally {
      setBusy(false);
    }
  }

  function startAssetSelectionMode(action: "review" | "generate" | "delete" | "group" = "group") {
    setAssetSelectionMode(true);
    setAssetBatchAction(action);
    setSelectedAssetGroupKeys(new Set());
  }

  function cancelAssetSelectionMode() {
    setAssetSelectionMode(false);
    setAssetBatchAction(null);
    setSelectedAssetGroupKeys(new Set());
    setAssetGroupModal(null);
  }

  function selectedGroupsForBatch() {
    return assetGroups.filter((group) => selectedAssetGroupKeys.has(group.key));
  }

  function toggleSelectAllAssetGroups() {
    const visibleKeys = filteredGroups.map((group) => group.key);
    const allSelected = visibleKeys.length > 0 && visibleKeys.every((key) => selectedAssetGroupKeys.has(key));
    setSelectedAssetGroupKeys((current) => {
      const next = new Set(current);
      if (allSelected) {
        visibleKeys.forEach((key) => next.delete(key));
      } else {
        const firstKey = [...current][0];
        const firstGroup = assetGroups.find((item) => item.key === firstKey);
        visibleKeys.forEach((key) => {
          const group = assetGroups.find((item) => item.key === key);
          if (!firstGroup || !group || group.type === firstGroup.type) next.add(key);
        });
      }
      return next;
    });
  }

  function batchSubmitAssetReview() {
    const count = selectedAssetGroupKeys.size;
    if (!count) {
      setMessage("请先选择要送审的资产");
      return;
    }
    setMessage(`已提交 ${count} 个资产送审（模拟操作，未同步审核系统）`);
    cancelAssetSelectionMode();
  }

  async function batchGenerateAssetImages() {
    if (!currentProject) return;
    const groups = selectedGroupsForBatch();
    if (!groups.length) {
      setMessage("请先选择要生成图片的资产");
      return;
    }
    const targets: Array<{ asset: Asset; prompt: string }> = [];
    const skipped: string[] = [];
    groups.forEach((group) => {
      const asset = group.items[0];
      if (!asset) return;
      if (generatingAssetIdsRef.current.has(asset.id) || asset.payload?.generationInProgress === true) {
        skipped.push(`${group.name}（生成中）`);
        return;
      }
      const prompt = assetPrompt(asset);
      if (!prompt.trim()) {
        skipped.push(`${group.name}（缺少提示词）`);
        return;
      }
      targets.push({ asset, prompt });
    });
    if (!targets.length) {
      setMessage(skipped.length ? `没有可生成的资产：${skipped.join("、")}` : "没有可生成的资产");
      return;
    }
    setBusy(true);
    const failed: string[] = [];
    let done = 0;
    try {
      for (const target of targets) {
        done += 1;
        setMessage(`正在批量生图 ${done}/${targets.length}：${target.asset.name}`);
        try {
          const canonicalPrompt = await saveAssetPrompt(target.asset, target.prompt);
          await request(`/api/projects/${currentProject.id}/assets/${target.asset.id}/generate-image`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              finalPrompt: canonicalPrompt || target.prompt,
              model: assetImageModel,
              count: assetImageCount,
              aspectRatio: assetImageAspectRatio,
            }),
          });
        } catch (error) {
          failed.push(`${target.asset.name}（${error instanceof Error ? error.message : "生成失败"}）`);
        }
      }
      await loadAssets(currentProject.id);
      const parts = [`批量生图完成：成功 ${targets.length - failed.length} 个`];
      if (failed.length) parts.push(`失败 ${failed.length} 个：${failed.join("、")}`);
      if (skipped.length) parts.push(`跳过 ${skipped.length} 个：${skipped.join("、")}`);
      setMessage(parts.join("；"));
      cancelAssetSelectionMode();
    } finally {
      setBusy(false);
    }
  }

  function requestDeleteAssetGroups(groups: AssetGroup[]) {
    const targets = groups
      .map((group) => ({ id: group.items[0]?.id, label: `${group.type} · ${group.name} → ${group.localizedName}` }))
      .filter((item): item is { id: string; label: string } => Boolean(item.id));
    if (!targets.length) return;
    setAssetDeleteTarget({ ids: targets.map((item) => item.id), labels: targets.map((item) => item.label) });
  }

  function openBatchDeleteConfirm() {
    const groups = selectedGroupsForBatch();
    if (!groups.length) {
      setMessage("请先选择要删除的资产");
      return;
    }
    requestDeleteAssetGroups(groups);
  }

  async function confirmBatchDelete() {
    if (!currentProject || !assetDeleteTarget) return;
    const assetIds = assetDeleteTarget.ids;
    setBusy(true);
    try {
      const data = await request<{ deleted: string[]; skipped: Array<{ assetId: string; reason: string }> }>(
        `/api/projects/${currentProject.id}/assets/batch-delete`,
        { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ assetIds }) },
      );
      await loadAssets(currentProject.id);
      await loadCustomAssetGroups(currentProject.id);
      setAssetDeleteTarget(null);
      cancelAssetSelectionMode();
      const parts = [`已删除 ${data.deleted.length} 个资产`];
      if (data.skipped.length) parts.push(`${data.skipped.length} 个未删除：${data.skipped.map((item) => item.reason).join("、")}`);
      setMessage(parts.join("；"));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "删除资产失败");
    } finally {
      setBusy(false);
    }
  }

  function openCreateAssetGroupModal() {
    if (!selectedAssetGroupKeys.size) {
      setMessage("请先选择要组成资产组的同类资产");
      return;
    }
    setAssetGroupNameDraft("");
    setAssetGroupModal("create");
  }

  function openMoveAssetGroupModal() {
    if (!selectedAssetGroupKeys.size) {
      setMessage("请先选择要移动的同类资产");
      return;
    }
    const selectedGroups = assetGroups.filter((group) => selectedAssetGroupKeys.has(group.key));
    const candidates = customAssetGroups.filter((group) => group.assetType === selectedGroups[0]?.type);
    if (!candidates.length) {
      setMessage("当前类型还没有可加入的资产组");
      return;
    }
    setAssetGroupTargetId(candidates[0].id);
    setAssetGroupModal("move");
  }

  async function createAssetGroupFromSelection() {
    if (!currentProject) return;
    const memberAssetIds = selectedAssetIdsForGrouping();
    if (!memberAssetIds.length) return;
    setBusy(true);
    try {
      await request(`/api/projects/${currentProject.id}/asset-groups`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: assetGroupNameDraft, memberAssetIds }),
      });
      await loadCustomAssetGroups(currentProject.id);
      setAssetGroupModal(null);
      setAssetGroupNameDraft("");
      setSelectedAssetGroupKeys(new Set());
      setAssetSelectionMode(false);
      setMessage(`已创建资产组，包含 ${memberAssetIds.length} 个资产`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "创建资产组失败");
    } finally {
      setBusy(false);
    }
  }

  async function moveSelectionToCustomGroup() {
    if (!currentProject || !assetGroupTargetId) return;
    const target = customAssetGroups.find((group) => group.id === assetGroupTargetId);
    if (!target) return;
    const selectedIds = selectedAssetIdsForGrouping();
    const memberAssetIds = [...target.members.map((member) => member.assetId), ...selectedIds];
    setBusy(true);
    try {
      await request(`/api/projects/${currentProject.id}/asset-groups/${target.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ memberAssetIds }),
      });
      await loadCustomAssetGroups(currentProject.id);
      setAssetGroupModal(null);
      setSelectedAssetGroupKeys(new Set());
      setAssetSelectionMode(false);
      setMessage(`已将 ${selectedIds.length} 个资产移动到「${target.name}」`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "移动到资产组失败");
    } finally {
      setBusy(false);
    }
  }

  async function saveCustomGroupMembers(group: CustomAssetGroup, memberAssetIds: string[]) {
    if (!currentProject) return;
    setBusy(true);
    try {
      await request(`/api/projects/${currentProject.id}/asset-groups/${group.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ memberAssetIds }),
      });
      await loadCustomAssetGroups(currentProject.id);
      setMessage("资产组成员已更新");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "更新资产组失败");
    } finally {
      setBusy(false);
    }
  }

  async function renameCustomGroup(group: CustomAssetGroup) {
    if (!currentProject || !assetGroupNameDraft.trim()) return;
    setBusy(true);
    try {
      await request(`/api/projects/${currentProject.id}/asset-groups/${group.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: assetGroupNameDraft.trim() }),
      });
      await loadCustomAssetGroups(currentProject.id);
      setMessage("资产组名称已更新");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "重命名资产组失败");
    } finally {
      setBusy(false);
    }
  }

  async function removeAssetFromCustomGroup(group: CustomAssetGroup, asset: Asset) {
    const memberAssetIds = group.members
      .filter((member) => member.assetKey !== mainAssetKey(asset))
      .map((member) => member.assetId);
    if (!memberAssetIds.length) {
      setMessage("组内至少保留一个资产；如需清空请删除资产组");
      return;
    }
    await saveCustomGroupMembers(group, memberAssetIds);
  }

  async function deleteCustomGroup(group: CustomAssetGroup) {
    if (!currentProject) return;
    setBusy(true);
    try {
      await request(`/api/projects/${currentProject.id}/asset-groups/${group.id}`, { method: "DELETE" });
      await loadCustomAssetGroups(currentProject.id);
      if (selectedCustomGroupId === group.id) {
        setSelectedCustomGroupId(null);
        setAssetView("overview");
      }
      setMessage("资产组已删除，组内资产保持不变");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "删除资产组失败");
    } finally {
      setBusy(false);
    }
  }

  async function loadStoryboards(projectId = currentProject?.id, episodeId?: string | null) {
    if (!projectId) return;
    const epId = episodeId ?? selectedEpisodeId;
    const params = epId ? `?episode_id=${epId}` : "";
    const data = await request<{ storyboards: Storyboard[] }>(`/api/projects/${projectId}/storyboards${params}`);
    setStoryboards(data.storyboards);
    data.storyboards.forEach((storyboard) => {
      const body = storyboard.payload?.["使用回退占位"] === true ? "" : String(
        storyboard.payload?.["分镜生成正文"] ?? storyboard.payload?.["源分镜文本"] ?? storyboard.prompt ?? ""
      );
      storyboardSavedBodies.current.set(storyboard.id, body);
    });
  }

  async function loadGenerations(projectId = currentProject?.id): Promise<VideoGeneration[]> {
    if (!projectId) return [];
    const data = await request<{ videoGenerations: VideoGeneration[] }>(`/api/projects/${projectId}/video-generations`);
    setGenerations(data.videoGenerations);
    return data.videoGenerations;
  }

  async function loadTasks(projectId = currentProject?.id) {
    if (!projectId) return [];
    const data = await request<{ tasks: Task[] }>(`/api/projects/${projectId}/tasks`);
    setTasks(data.tasks);
    return data.tasks;
  }

  function scheduleRefresh(projectId: string) {
    [2500, 8000, 20000, 45000, 90000, 180000, 300000].forEach((delay) => {
      window.setTimeout(() => selectProject(projectId).catch((error) => setMessage(error.message)), delay);
    });
  }

  function activeStep(): StepScreen {
    if (screen === "source" || screen === "analysis") return "home";
    return screen;
  }

  function showAssetOverview(tab: AssetTab) {
    setAssetTab(tab);
    setAssetView("overview");
  }

  function openVoiceConfiguration(group: AssetGroup) {
    const asset = group.items[0];
    if (!asset) return;
    const saved = asset.payload?.["音色配置"];
    const config: VoiceConfiguration = {
      ...DEFAULT_VOICE_CONFIG,
      referenceAudio: saved?.referenceAudio && typeof saved.referenceAudio === "object" && typeof saved.referenceAudio.fileUrl === "string" ? saved.referenceAudio as VoiceReferenceAudio : undefined,
    };
    setVoiceEditor({ assetId: asset.id, roleName: group.localizedName || group.name, config });
  }

  function closeVoiceConfiguration() {
    if (voiceRefAudioRef.current) { voiceRefAudioRef.current.pause(); voiceRefAudioRef.current = null; }
    setVoiceRefPlaying(false);
    setVoiceEditor(null);
  }

  async function uploadVoiceReference(file: File) {
    if (!currentProject || !voiceEditor) return;
    if (!/\.(wav|mp3|m4a)$/i.test(file.name)) {
      setMessage("仅支持 wav / mp3 / m4a 格式的参考音频");
      return;
    }
    if (file.size <= 0 || file.size > 20 * 1024 * 1024) {
      setMessage("参考音频不能为空且不能超过 20 MB");
      return;
    }
    setVoiceUploading(true);
    try {
      const form = new FormData();
      form.append("file", file);
      const data = await request<{ file_url: string; file_name: string; size: number; mime_type: string }>(`/api/projects/${currentProject.id}/assets/${voiceEditor.assetId}/upload-voice-reference`, { method: "POST", body: form });
      setVoiceEditor((current) => current ? { ...current, config: { ...current.config, referenceAudio: { fileUrl: data.file_url, fileName: data.file_name, size: data.size, mimeType: data.mime_type, status: "uploaded" } } } : current);
      setMessage("参考音频已上传，保存音色配置后生效");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "参考音频上传失败，请重试");
    } finally {
      setVoiceUploading(false);
    }
  }

  function toggleVoicePreview() {
    const reference = voiceEditor?.config.referenceAudio;
    const url = apiAssetUrl(reference?.fileUrl);
    if (!url) return;
    if (voiceRefPlaying && voiceRefAudioRef.current) {
      voiceRefAudioRef.current.pause();
      voiceRefAudioRef.current = null;
      setVoiceRefPlaying(false);
      return;
    }
    const audio = new Audio(url);
    voiceRefAudioRef.current = audio;
    audio.onended = () => { setVoiceRefPlaying(false); voiceRefAudioRef.current = null; };
    audio.onerror = () => { setVoiceRefPlaying(false); voiceRefAudioRef.current = null; setMessage("参考音频播放失败，请检查文件是否可访问"); };
    setVoiceRefPlaying(true);
    void audio.play().catch(() => { setVoiceRefPlaying(false); voiceRefAudioRef.current = null; setMessage("参考音频播放失败，请检查文件是否可访问"); });
  }

  async function saveVoiceConfiguration() {
    if (!currentProject || !voiceEditor) return;
    setBusy(true);
    try {
      const config = { ...voiceEditor.config, updatedAt: new Date().toISOString() };
      const data = await request<{ asset: Asset }>(`/api/projects/${currentProject.id}/assets/${voiceEditor.assetId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ payload: { "音色配置": config } }),
      });
      setAssets((items) => items.map((item) => item.id === data.asset.id ? data.asset : item));
      closeVoiceConfiguration();
      setMessage(`已保存 ${data.asset.localized_name || data.asset.name} 的音色配置`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "音色配置保存失败，请重试");
    } finally {
      setBusy(false);
    }
  }

  function renderAssetCard(group: AssetGroup, onClick?: () => void, onRemove?: () => void) {
    const originalUrl = groupOriginalImageUrl(group);
    const confirmedUrl = groupConfirmedImageUrl(group);
    const generatedUrl = group.items.map((item) => selectedAssetImageUrl(item)).find(Boolean) || "";
    const generationStatus = group.items.some((item) => generatingAssetIds.has(item.id) || item.payload?.generationInProgress === true)
      ? "生成中"
      : group.items.some((item) => (item.images?.length ?? 0) > 0) ? "已生成" : "待生成";
    // 左上徽标 = 送审状态（图2 语义）：全部子资产已确认 → 已过审；有失败标记 → 审核失败；否则待送审。
    // 生成状态不再占用卡角，由「新」图占位的「待生成」文案承担。
    const reviewStatus = group.items.some((item) => item.payload?.["审核状态"] === "失败")
      ? { label: "审核失败", cls: "rejected" }
      : group.items.every((item) => item.status === "已确认")
        ? { label: "已过审", cls: "approved" }
        : { label: "待送审", cls: "pending" };
    const voiceConfig = group.type === "角色" ? group.items[0]?.payload?.["音色配置"] as VoiceConfiguration | undefined : undefined;
    const isBlankAsset = !originalUrl && !generatedUrl && group.items.every((item) => item.payload?.["手动新建"] === true);
    return (
      <article className={selectedAssetGroupKeys.has(group.key) ? "asset-card selected" : "asset-card"} key={group.key} onClick={onClick ?? (() => assetSelectionMode ? toggleAssetGroupSelection(group) : selectAssetCard(group.items[0]))}>
        <div className="asset-card-statuses">
          <i className={`asset-review-status ${reviewStatus.cls}`}>{generationStatus === "生成中" ? "生成中" : reviewStatus.label}</i>
          <span className="asset-card-status-end">
            <i>{selectedAssetGroupKeys.has(group.key) ? "已选择" : group.items.every((item) => item.status === "已确认") ? "已确认" : "待确认"}</i>
            <button type="button" className="asset-card-delete" disabled={busy} onClick={(event) => { event.stopPropagation(); requestDeleteAssetGroups([group]); }}>删除</button>
          </span>
        </div>
        <div className="asset-card-visuals">
          <figure>
            {originalUrl ? (
              <img className="asset-card-image" src={originalUrl} alt="原资产图" onClick={(event) => {
                event.stopPropagation();
                openImage(originalUrl, `${group.name} 原资产图`);
              }} />
            ) : isBlankAsset ? (
              <div className="asset-placeholder compact">待填写</div>
            ) : (
              <div className={`thumb ${assetThumbClass(group.items[0])}`}></div>
            )}
            <em>原</em>
            <figcaption>{group.name}</figcaption>
          </figure>
          <figure>
            {generatedUrl ? (
              <img className="asset-card-image" src={generatedUrl} alt={confirmedUrl ? "已确认目标资产图" : "目标资产图"} onClick={(event) => {
                event.stopPropagation();
                openImage(generatedUrl, `${group.localizedName} 目标资产图`);
              }} />
            ) : (
              <div className="asset-placeholder compact">{isBlankAsset ? "待填写" : "待生成"}</div>
            )}
            <em>新</em>
            <figcaption>{group.localizedName}</figcaption>
          </figure>
        </div>
        <b>{isBlankAsset ? group.name : `${group.name} → ${group.localizedName}`}</b>
        <span>{isBlankAsset ? "空白资产 · 待填写描述与提示词" : `${group.items.length} 个子资产 · ${assetDisplayMeta(group.items[0])}`}</span>
        {group.type === "角色" && <div className="asset-voice-action">
          <button type="button" className="asset-voice-button" onClick={(event) => { event.stopPropagation(); openVoiceConfiguration(group); }}><i className="voice-dot" aria-hidden="true" />配置音色</button>
          <small>{voiceConfig?.referenceAudio ? "音色克隆 · 参考音频已上传" : "未配置 · 点击上传参考音频"}</small>
        </div>}
        {reviewStatus.cls === "rejected" && <div className="asset-review-banner"><i>审核失败</i><span>{group.items.every((item) => item.status === "已确认") ? "已确认" : "待确认"}</span></div>}
        {onRemove && <button className="ghost small asset-card-remove" disabled={busy} onClick={(event) => { event.stopPropagation(); onRemove(); }}>移出组</button>}
      </article>
    );
  }

  function selectAssetCard(asset: Asset) {
    setSelectedAssetId(asset.id);
    setAssetTab(asset.asset_type as AssetTab);
    setAssetView("detail");
  }

  function openImage(url: string, title: string) {
    if (!url) return;
    setImageView({ url, title });
    setModal("image-view-modal");
  }

  function handleLocalFiles(files: FileList | null, uploadMode: UploadMode) {
    if (!files?.length) return;
    const fileItems = Array.from(files);
    const firstFile = fileItems[0];
    const title = uploadProjectTitle(fileItems, "未命名本地视频项目");
    setSelectedFiles(fileItems);
    setSelectedSource({
      kind: "local",
      uploadMode,
      title,
      meta: uploadMode === "single"
        ? `本地单个视频 · 1 集 · ${firstFile.name}`
        : `本地文件夹 · ${files.length} 个视频文件`,
    });
    setUploadState("selected");
    setMessage("素材已选择，请确认参数后开始上传");
  }

  async function createProject() {
    setBusy(true);
    try {
      const data = await request<{ project: Project }>("/api/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: "六年后再遇首富前男友", targetCountry, targetLanguage, remakeMode, sourceLanguage, storyboardVisualStyle: remakeMode === "cg_3d" ? "cg_3d" : storyboardVisualStyle, replaceCharacters, replaceScenes }),
      });
      setMessage("项目已创建");
      await loadProjects();
      await selectProject(data.project.id);
    } finally {
      setBusy(false);
    }
  }

  async function uploadEpisodes(files = selectedFiles, options: { reuseCurrentProject?: boolean } = {}) {
    if (selectedSource?.kind === "local" && !files?.length) {
      setMessage("未读取到本地视频文件，请重新选择文件夹后再上传");
      setModal(null);
      return;
    }
    setBusy(true);
    setUploadState("uploading");
    setScreen("source");
    setModal(null);
    try {
      const project = currentProject && (options.reuseCurrentProject || !episodes.length)
        ? currentProject
        : await createProjectForUpload(selectedSource?.title ?? "未命名本地视频项目");
      // 始终真实上传视频字节：文件落在我们自己的服务器（后端 data/projects），
      // 方舟仅在分析环节作为模型解析的临时句柄，不是上传目的地。
      const body = new FormData();
      Array.from(files ?? []).forEach((file) => body.append("files", file));
      await request(`/api/projects/${project.id}/episodes/upload`, { method: "POST", body });
      setMessage("视频已保存并上传完成");
      await selectProject(project.id);
      setUploadState("uploaded");
    } catch (error) {
      setUploadState("failed");
      setMessage(error instanceof Error ? error.message : "上传失败");
    } finally {
      setBusy(false);
    }
  }

  async function handleReuploadSelection(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.currentTarget.files ?? []);
    event.currentTarget.value = "";
    if (!files.length) return;
    setSelectedFiles(files);
    setSelectedSource((current) => current ?? {
      kind: "local",
      uploadMode: files.length === 1 ? "single" : "folder",
      title: currentProject?.title ?? "未命名本地视频项目",
      meta: files.length === 1 ? `本地单个视频 · 1 集 · ${files[0].name}` : `本地文件夹 · ${files.length} 个视频文件`,
    });
    setUploadState("selected");
    await uploadEpisodes(files, { reuseCurrentProject: true });
  }

  async function runBackendTask(taskType: "asset_extraction" | "storyboard_generation" | "video_generation" | "series_analysis", options: { force?: boolean } = {}) {
    if (!currentProject) return null;
    if (taskType !== "video_generation" && taskType !== "series_analysis" && !selectedEpisodeId) {
      setMessage("请先选择要处理的视频");
      return null;
    }
    const payload = taskType === "video_generation"
      ? { taskType, storyboardId: selectedStoryboard?.id ?? storyboards[0]?.id }
      : taskType === "series_analysis"
        ? { taskType }
        : { taskType, episodeId: selectedEpisodeId, ...(taskType === "storyboard_generation" && options.force ? { force: true } : {}) };
    const data = await request<{ task: Task }>(`/api/projects/${currentProject.id}/tasks/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    setMessage(data.task.message);
    scheduleRefresh(currentProject.id);
    await loadTasks(currentProject.id);
    return data.task;
  }

  async function pollTask(
    taskId: string,
    onDone: (task: Task) => Promise<void> | void,
    onProgress?: (task: Task) => Promise<void> | void
  ) {
    let consecutiveErrors = 0;
    for (let index = 0; index < 2400; index += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 3000));
      try {
        const data = await request<{ task: Task }>(`/api/tasks/${taskId}`);
        consecutiveErrors = 0;
        setActiveAnalysisTask(data.task);
        setMessage(data.task.message);
        await onProgress?.(data.task);
        if (!isTaskRunning(data.task)) {
          await onDone(data.task);
          sessionStorage.removeItem("activeAnalysisTaskId");
          pollingTaskIds.current.delete(taskId);
          return;
        }
      } catch {
        consecutiveErrors += 1;
        if (consecutiveErrors >= 5) {
          setMessage(`轮询暂时中断：连续 ${consecutiveErrors} 次请求失败，后台任务状态未知，正在继续重试。`);
          consecutiveErrors = 0;
        }
      }
    }
  }

  function failAnalysisStart(error: unknown, fallback: string) {
    const detail = error instanceof Error && error.message ? error.message : fallback;
    sessionStorage.removeItem("activeAnalysisTaskId");
    setActiveAnalysisTask((current) => current ? {
      ...current,
      status: "失败",
      message: detail,
      updated_at: new Date().toISOString(),
    } : current);
    setMessage(detail);
  }

  async function startAnalysis(mode: "normal" | "aigc") {
    if (!currentProject || !selectedEpisodeId) {
      setMessage("请先选择要分析的剧集");
      return;
    }
    setBusy(true);
    setScreen("analysis");
    setAnalysisProgress({ story: 4, asset: mode === "aigc" ? 8 : 4 });
    setMessage(mode === "aigc" ? "正在创建 AIGC 关联分析任务" : "正在创建当前集分析任务");
    setActiveAnalysisTask({
      id: "pending-analysis",
      project_id: currentProject.id,
      episode_id: selectedEpisodeId,
      task_type: "asset_extraction",
      status: "运行中",
      message: "正在创建当前集分析任务",
      created_at: "",
      updated_at: "",
    });
    try {
      const task = await runBackendTask("asset_extraction");
      if (!task) {
        failAnalysisStart(new Error("任务创建失败，请重试"), "任务创建失败，请重试");
        setBusy(false);
        return;
      }
      setAnalysisProgress({ story: 8, asset: mode === "aigc" ? 12 : 8 });
      setMessage(mode === "aigc" ? "正在关联 AIGC 资产并分析" : "正在执行普通视频分析");
      setActiveAnalysisTask(task);
      sessionStorage.setItem("activeAnalysisTaskId", task.id);
      setBusy(false);
      pollingTaskIds.current.add(task.id);
      void pollTask(task.id, async (doneTask) => {
        setActiveAnalysisTask(doneTask);
        if (currentProject) await Promise.all([loadAssets(currentProject.id), loadTasks(currentProject.id)]);
        if (doneTask.status === "失败") {
          setMessage(doneTask.message || "分析失败，请重试");
          setAnalysisProgress({ story: 100, asset: 100 });
          return;
        }
        setAnalysisProgress({ story: 100, asset: 100 });
        const degraded = doneTask.result?.自动降级;
        const modelError = doneTask.result?.模型错误;
        if (degraded) {
          setMessage("已自动切换 mock 模式：" + (doneTask.result?.降级原因 || "外部服务不可用"));
        } else if (modelError) {
          setMessage("方舟分析超时，已先生成基础可编辑资产；可继续进入设定，或稍后重试真实分析：" + String(modelError).slice(0, 80));
        } else {
          setMessage("分析完成，可以进入设定确认资产");
        }
      });
    } catch (error) {
      setBusy(false);
      failAnalysisStart(error, "任务启动失败");
    }
  }

  async function startSeriesAnalysis(mode: "normal" | "aigc" = "normal") {
    if (!currentProject || !episodes.length) {
      setMessage("请先上传剧集后再分析整剧");
      return;
    }
    setBusy(true);
    setScreen("analysis");
    setAnalysisProgress({ story: 4, asset: mode === "aigc" ? 8 : 4 });
    setMessage(mode === "aigc" ? "正在创建整剧 AIGC 关联分析任务" : "正在创建整剧分析任务");
    setActiveAnalysisTask({
      id: "pending-series-analysis",
      project_id: currentProject.id,
      episode_id: null,
      task_type: "series_analysis",
      status: "运行中",
      message: "正在创建整剧分析任务",
      created_at: "",
      updated_at: "",
    });
    try {
      const task = await runBackendTask("series_analysis");
      if (!task) {
        failAnalysisStart(new Error("整剧分析任务创建失败，请重试"), "整剧分析任务创建失败，请重试");
        setBusy(false);
        return;
      }
      setScreen("analysis");
      setAnalysisProgress({ story: 6, asset: mode === "aigc" ? 10 : 6 });
      setMessage(mode === "aigc" ? "正在逐集关联 AIGC 资产并分析整剧" : "正在逐集分析整剧，资产将按项目去重");
      setActiveAnalysisTask(task);
      setBusy(false);
      void pollTask(
        task.id,
        async (doneTask) => {
          setActiveAnalysisTask(doneTask);
          if (currentProject) {
            await Promise.all([
              loadAssets(currentProject.id),
              loadStoryboards(currentProject.id),
              loadTasks(currentProject.id),
              selectProject(currentProject.id),
            ]);
          }
          setAnalysisProgress({ story: 100, asset: 100 });
          if (doneTask.status === "失败") {
            setMessage(doneTask.message || "整剧分析失败，请重试");
            return;
          }
          const total = doneTask.result?.总集数 ?? episodes.length;
          const success = doneTask.result?.成功集数 ?? 0;
          const failed = doneTask.result?.失败集数 ?? 0;
          setMessage(`整剧分析完成：共 ${total} 集，成功 ${success} 集，失败 ${failed} 集`);
        },
        (runningTask) => {
          const result = runningTask.result ?? {};
          const total = Number(result.总集数 || episodes.length || 1);
          const current = Number(result.当前序号 || result.成功集数 || 1);
          const stage = String(result.当前阶段 || "");
          const stageOffset = stage.includes("分镜") ? 0.7 : stage.includes("完成") ? 0.95 : stage.includes("资产") ? 0.35 : 0.15;
          const percent = Math.min(96, Math.max(8, Math.round((((current - 1) + stageOffset) / total) * 92 + 4)));
          setAnalysisProgress({ story: percent, asset: percent });
          const episodeLabel = result.当前集 ?? result.当前集编号;
          if (episodeLabel) {
            const episodeText = String(episodeLabel).includes("集") ? String(episodeLabel) : `第 ${episodeLabel} 集`;
            setMessage(`${stage || "正在分析"}${episodeText}（${current}/${total}）`);
          }
        }
      );
    } catch (error) {
      setBusy(false);
      failAnalysisStart(error, "整剧分析任务启动失败");
    }
  }

  async function enterStoryboards(force = false) {
    if (!currentProject || busy) return;
    setBusy(true);
    setStoryboardLoading(true);
    try {
      const task = await runBackendTask("storyboard_generation", { force });
      if (!task) {
        setStoryboardLoading(false);
        return;
      }
      setScreen("segments");
      setSegmentPanel("shot-prompt");
      setActiveAnalysisTask(task);
      setMessage("分镜正在生成，请稍候...");
      setBusy(false);
      void pollTask(task.id, async (doneTask) => {
        if (currentProject) await loadStoryboards(currentProject.id);
        setStoryboardLoading(false);
        if (doneTask.status === "失败") {
          setMessage(doneTask.message || "分镜生成失败，请重试");
        } else {
          setMessage("分镜生成完成");
        }
      });
    } catch (error) {
      setStoryboardLoading(false);
      setMessage(error instanceof Error ? error.message : "分镜生成失败");
    } finally {
      setBusy(false);
    }
  }

  async function runVideoGeneration() {
    if (!currentProject || busy) return;
    setBusy(true);
    try {
      if (selectedStoryboard && promptInputRef.current) {
        const saved = await savePrompt(selectedStoryboard, readPromptEditor(promptInputRef.current));
        if (!saved) {
          setMessage("分镜正文尚未保存；草稿仍保留，可重试保存后再生成");
          return;
        }
        const refsSaved = await saveStoryboardReferenceOverrides(selectedStoryboard, getLatestStoryboardReferenceTokens(selectedStoryboard));
        if (!refsSaved) {
          setMessage("资产引用尚未保存；分镜草稿仍保留，可重试后再生成");
          return;
        }
      }
      await storyboardPatchQueue.current;
      const task = await runBackendTask("video_generation");
      if (task) {
        setMessage("正在生成新版本…");
        void waitVideoTaskAndRefresh(task.id, selectedStoryboard?.id ?? null);
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "视频生成失败");
    } finally {
      setBusy(false);
    }
  }

  async function waitVideoTaskAndRefresh(taskId: string, storyboardId: string | null) {
    if (!currentProject) return;
    const projectId = currentProject.id;
    for (let attempt = 0; attempt < 30; attempt += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 700));
      try {
        const data = await request<{ task: Task }>(`/api/tasks/${taskId}`);
        if (isTaskRunning(data.task)) continue;
        const latest = await loadGenerations(projectId);
        if (data.task.status === "成功" || data.task.status === "completed") {
          const newest = latest.find((item) => item.storyboard_id === storyboardId) ?? latest[0];
          if (newest) setSelectedGenerationId(newest.id);
          setMessage("已生成新版本，可在历史版本中切换");
        } else {
          setMessage(data.task.message || "视频生成失败");
        }
        return;
      } catch {
        // 单次轮询失败可忽略，继续等待
      }
    }
    const latest = await loadGenerations(projectId).catch(() => [] as VideoGeneration[]);
    const newest = latest.find((item) => item.storyboard_id === storyboardId) ?? latest[0];
    if (newest) setSelectedGenerationId(newest.id);
  }

  async function confirmAsset(assetId: string) {
    if (!currentProject) return;
    setBusy(true);
    try {
      await request(`/api/projects/${currentProject.id}/assets/${assetId}/confirm`, { method: "POST" });
      setMessage("资产已确认");
      await loadAssets(currentProject.id);
    } finally {
      setBusy(false);
    }
  }

  async function toggleAssetFavoriteVersion() {
    if (!currentProject || !selectedAsset) return;
    const currentFavorite = String(selectedAsset.payload?.["收藏版本图片ID"] ?? "");
    const currentImageId = selectedImageVersion?.id ?? "";
    if (!currentImageId) {
      setMessage("暂无可收藏的版本，请先生成或上传资产图");
      return;
    }
    const nextFavorite = currentFavorite === currentImageId ? "" : currentImageId;
    setBusy(true);
    try {
      const data = await request<{ asset: Asset }>(`/api/projects/${currentProject.id}/assets/${selectedAsset.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ payload: { "收藏版本图片ID": nextFavorite } }),
      });
      setAssets((items) => items.map((item) => item.id === selectedAsset.id ? data.asset : item));
      setMessage(nextFavorite ? "已收藏当前预览版本" : "已取消收藏");
    } finally {
      setBusy(false);
    }
  }

  async function selectAssetImageVersion(assetId: string, imageId: string) {
    if (!currentProject) return;
    setBusy(true);
    try {
      const data = await request<{ asset: Asset }>(`/api/projects/${currentProject.id}/assets/${assetId}/select-image`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ imageId }),
      });
      setAssets((items) => items.map((item) => (item.id === assetId ? data.asset : item)));
      setSelectedAssetId(assetId);
      setMessage("已切换当前采用版本");
    } finally {
      setBusy(false);
    }
  }

  async function generateAssetImage(asset: Asset, finalPrompt: string) {
    if (!currentProject) return;
    if (generatingAssetIdsRef.current.has(asset.id) || !finalPrompt.trim()) return;
    const started = new Set(generatingAssetIdsRef.current);
    started.add(asset.id);
    generatingAssetIdsRef.current = started;
    setGeneratingAssetIds(started);
    try {
      const canonicalPrompt = await saveAssetPrompt(asset, finalPrompt);
      if (!canonicalPrompt.trim()) {
        throw new Error("资产提示词不能为空。");
      }
      await request(`/api/projects/${currentProject.id}/assets/${asset.id}/generate-image`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          finalPrompt: canonicalPrompt,
          model: assetImageModel,
          count: assetImageCount,
          aspectRatio: assetImageAspectRatio,
        }),
      });
      setMessage(`已生成 ${assetImageCount} 张资产图，可在历史版本中切换`);
      await loadAssets(currentProject.id);
    } finally {
      const finished = new Set(generatingAssetIdsRef.current);
      finished.delete(asset.id);
      generatingAssetIdsRef.current = finished;
      setGeneratingAssetIds(finished);
    }
  }

  async function saveAssetPrompt(asset: Asset, finalPrompt: string): Promise<string> {
    if (!currentProject) return "";
    const requestRevision = assetPromptDraftRevision.current.get(asset.id) ?? 0;
    const existingManual = String(asset.payload?.["用户编辑资产图生成提示词"] ?? "");
    const automatic = String(asset.payload?.["自动编译资产图生成提示词"] ?? "");
    const manualPrompt = finalPrompt === automatic ? "" : finalPrompt;
    if (manualPrompt === existingManual) return String(asset.payload?.["最终资产图生成提示词"] ?? finalPrompt);
    const data = await request<{ asset: Asset }>(`/api/projects/${currentProject.id}/assets/${asset.id}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ payload: { "用户编辑资产图生成提示词": manualPrompt } }),
    });
    setAssets((items) => items.map((item) => item.id === asset.id ? data.asset : item));
    setMessage("完整资产提示词已保存");
    const canonicalPrompt = String(data.asset.payload?.["最终资产图生成提示词"] ?? finalPrompt);
    if (selectedAssetIdForPrompt.current === asset.id && (assetPromptDraftRevision.current.get(asset.id) ?? 0) === requestRevision) {
      setAssetPromptDraft(canonicalPrompt);
    }
    return canonicalPrompt;
  }

  async function uploadTargetAssetImage(file?: File) {
    if (!currentProject || !selectedAsset || !file) return;
    const body = new FormData();
    body.append("file", file);
    setBusy(true);
    try {
      const data = await request<{ asset: Asset }>(`/api/projects/${currentProject.id}/assets/${selectedAsset.id}/upload-image`, { method: "POST", body });
      setAssets((items) => items.map((item) => item.id === selectedAsset.id ? data.asset : item));
      setMessage("目标图已上传、保存并设为当前采用图");
    } finally { setBusy(false); }
  }

  async function saveStoryboardVisualStyle(style: "live_action" | "cg_3d") {
    if (!currentProject) return;
    const data = await request<{ project: Project }>(`/api/projects/${currentProject.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ storyboardVisualStyle: style }) });
    setStoryboardVisualStyle(data.project.storyboard_visual_style ?? "live_action");
    setCurrentProject(data.project);
    await loadStoryboards(currentProject.id);
    setMessage("分镜画面类型已更新，既有源拆解保留；提示词已标记待更新");
  }

  async function savePrompt(storyboard: Storyboard, prompt: string): Promise<boolean> {
    if (!currentProject) return false;
    const inFlight = storyboardPromptSaveRequests.current.get(storyboard.id);
    if (inFlight?.prompt === prompt) return inFlight.promise;
    const projectId = currentProject.id;
    const revision = storyboardSaveRevisions.current.get(storyboard.id) ?? 0;
    setStoryboardSaveStates((states) => ({ ...states, [storyboard.id]: "saving" }));
    const queueTask = storyboardPatchQueue.current.catch(() => undefined).then(async () => {
      const data = await request<{ storyboard: Storyboard }>(
        `/api/projects/${projectId}/storyboards/${storyboard.id}`,
        { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ generatedBody: prompt }) }
      );
      if (storyboardSaveRevisions.current.get(storyboard.id) === revision) {
        storyboardSavedBodies.current.set(storyboard.id, prompt);
        setStoryboards((items) => items.map((item) => {
          if (item.id !== storyboard.id) return item;
          const localPayload = item.payload ?? {};
          const serverPayload = data.storyboard.payload ?? {};
          return {
            ...data.storyboard,
            payload: {
              ...serverPayload,
              ...(localPayload["引用资产变量"] !== undefined ? { "引用资产变量": localPayload["引用资产变量"] } : {}),
              ...(localPayload["引用资产变量手动覆盖"] !== undefined ? { "引用资产变量手动覆盖": localPayload["引用资产变量手动覆盖"] } : {}),
            },
          };
        }));
        setStoryboardSaveStates((states) => ({ ...states, [storyboard.id]: "saved" }));
      }
    });
    storyboardPatchQueue.current = queueTask;
    const saveRequest = (async () => {
      try {
      await queueTask;
      return true;
      } catch (error) {
      setMessage(`提示词保存失败，当前编辑保留：${error instanceof Error ? error.message : "请重试"}`);
      if (storyboardSaveRevisions.current.get(storyboard.id) === revision) {
        setStoryboardSaveStates((states) => ({ ...states, [storyboard.id]: "failed" }));
      }
      return false;
      }
    })();
    storyboardPromptSaveRequests.current.set(storyboard.id, { prompt, promise: saveRequest });
    const saved = await saveRequest;
    if (storyboardPromptSaveRequests.current.get(storyboard.id)?.promise === saveRequest) {
      storyboardPromptSaveRequests.current.delete(storyboard.id);
    }
    return saved;
  }

  async function selectStoryboardWithSave(nextStoryboard: Storyboard) {
    if (nextStoryboard.id === selectedStoryboard?.id) return;
    if (selectedStoryboard) {
      const draft = promptInputRef.current ? readPromptEditor(promptInputRef.current) : selectedStoryboardPrompt;
      const savedBody = storyboardSavedBodies.current.get(selectedStoryboard.id);
      const saveState = storyboardSaveStates[selectedStoryboard.id] ?? "saved";
      if (draft !== savedBody || saveState === "failed" || storyboardPromptSaveRequests.current.has(selectedStoryboard.id)) {
        const saved = await savePrompt(selectedStoryboard, draft);
        if (!saved) {
          setMessage("当前分镜正文保存失败；草稿仍保留，已取消切换。请重试保存后再切换。");
          return;
        }
      }
      if (storyboardReferenceDrafts.current.has(selectedStoryboard.id)) {
        const referencesSaved = await saveStoryboardReferenceOverrides(selectedStoryboard, getLatestStoryboardReferenceTokens(selectedStoryboard));
        if (!referencesSaved) {
          setMessage("当前分镜资产引用保存失败；草稿仍保留，已取消切换。请重试后再切换。");
          return;
        }
      }
    }
    setSelectedStoryboardId(nextStoryboard.id);
    setShotMenu(null);
    setSegmentPanel("shot-prompt");
  }

  function handlePromptCopy(event: ReactClipboardEvent<HTMLDivElement>) {
    const text = promptSelectedText(event.currentTarget);
    if (!text) return;
    event.preventDefault();
    event.clipboardData.setData("text/plain", text);
    event.clipboardData.setData("text/html", text.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll("\n", "<br>"));
  }

  function handlePromptPaste(event: ReactClipboardEvent<HTMLDivElement>) {
    event.preventDefault();
    const text = event.clipboardData.getData("text/plain");
    if (!text) return;
    const editor = event.currentTarget;
    editor.focus();
    if (!document.execCommand("insertText", false, text)) {
      const selection = window.getSelection();
      if (selection?.rangeCount && editor.contains(selection.anchorNode)) {
        const range = selection.getRangeAt(0);
        range.deleteContents();
        range.insertNode(document.createTextNode(text));
        range.collapse(false);
        selection.removeAllRanges();
        selection.addRange(range);
      }
    }
    const value = readPromptEditor(editor);
    if (selectedStoryboard) promptEditorSync.current = { storyboardId: selectedStoryboard.id, value, assets: storyboardScopeAssets };
    updateStoryboardPromptText(value);
    if (!promptCompositionActive.current) updatePromptMention(value, promptSelectionOffset(editor) ?? value.length);
  }

  async function saveStoryboardReferenceOverrides(storyboard: Storyboard, references: string[]) {
    if (!currentProject) return false;
    const projectId = currentProject.id;
    const normalizedReferences = canonicalizeAssetReferences(references.filter(Boolean), storyboardScopeAssets);
    storyboardReferenceDrafts.current.set(storyboard.id, { projectId, references: normalizedReferences });
    const revision = (storyboardReferenceRevision.current.get(storyboard.id) ?? 0) + 1;
    storyboardReferenceRevision.current.set(storyboard.id, revision);
    setStoryboards((items) => items.map((item) => item.id === storyboard.id
      ? { ...item, payload: { ...(item.payload ?? {}), "引用资产变量": normalizedReferences, "引用资产变量手动覆盖": true } }
      : item));
    const queueTask = storyboardPatchQueue.current.catch(() => undefined).then(async () => {
      const data = await request<{ storyboard: Storyboard }>(`/api/projects/${projectId}/storyboards/${storyboard.id}`, {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ payload: { "引用资产变量": normalizedReferences, "引用资产变量手动覆盖": true } }),
      });
      if (storyboardReferenceRevision.current.get(storyboard.id) === revision) {
        setStoryboards((items) => items.map((item) => {
          if (item.id !== data.storyboard.id) return item;
          const localBody = item.payload?.["分镜生成正文"];
          const serverBody = data.storyboard.payload?.["分镜生成正文"];
          const preserveDraft = typeof localBody === "string" && localBody !== serverBody;
          return preserveDraft
            ? { ...data.storyboard, payload: { ...data.storyboard.payload, "分镜生成正文": localBody } }
            : data.storyboard;
        }));
        storyboardReferenceDrafts.current.delete(storyboard.id);
      }
    });
    storyboardPatchQueue.current = queueTask;
    try {
      await queueTask;
      return true;
    } catch (error) {
      setMessage(`资产引用保存失败：${error instanceof Error ? error.message : "请重试"}`);
      return false;
    }
  }

  function getWorkbenchPaneMaxWidth(pane: WorkbenchPane) {
    const workbench = workbenchRef.current;
    if (!workbench) return pane === "assets" ? 230 : 260;
    const style = window.getComputedStyle(workbench);
    const horizontalPadding = (Number.parseFloat(style.paddingLeft) || 0) + (Number.parseFloat(style.paddingRight) || 0);
    const gap = Number.parseFloat(style.columnGap) || 0;
    const otherPanel = workbench.querySelector<HTMLElement>(pane === "assets" ? ".video-preview" : ".asset-sidebar");
    const otherVisible = Boolean(otherPanel && otherPanel.getClientRects().length > 0);
    const gapCount = otherVisible ? 4 : 2;
    const splitterWidth = otherVisible ? 16 : 8;
    const availableTracks = workbench.clientWidth - horizontalPadding - gap * gapCount - splitterWidth;
    const otherWidth = otherVisible ? otherPanel!.getBoundingClientRect().width : 0;
    const minimum = pane === "assets" ? 230 : 260;
    const preferredMaximum = pane === "assets" ? 680 : 720;
    return Math.max(minimum, Math.min(preferredMaximum, Math.floor(availableTracks - 360 - otherWidth)));
  }

  function updateWorkbenchPaneWidth(pane: WorkbenchPane, width: number) {
    const minimum = pane === "assets" ? 230 : 260;
    const bounded = Math.max(minimum, Math.min(getWorkbenchPaneMaxWidth(pane), Math.round(width)));
    if (pane === "assets") setAssetPaneWidth(bounded);
    else setPreviewPaneWidth(bounded);
  }

  function startWorkbenchResize(pane: WorkbenchPane, event: ReactPointerEvent<HTMLButtonElement>) {
    if (event.button !== 0) return;
    event.preventDefault();
    const workbench = workbenchRef.current;
    const panel = workbench?.querySelector<HTMLElement>(pane === "assets" ? ".asset-sidebar" : ".video-preview");
    if (!panel) return;
    const startWidth = panel.getBoundingClientRect().width;
    if (pane === "assets") setAssetPaneWidth(startWidth);
    else setPreviewPaneWidth(startWidth);
    splitterDragRef.current = { pane, pointerId: event.pointerId, startX: event.clientX, startWidth, target: event.currentTarget };
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function moveWorkbenchResize(event: ReactPointerEvent<HTMLButtonElement>) {
    const drag = splitterDragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    const delta = event.clientX - drag.startX;
    updateWorkbenchPaneWidth(drag.pane, drag.startWidth + (drag.pane === "assets" ? delta : -delta));
  }

  function endWorkbenchResize(event?: ReactPointerEvent<HTMLButtonElement>) {
    const drag = splitterDragRef.current;
    if (!drag || (event && drag.pointerId !== event.pointerId)) return;
    splitterDragRef.current = null;
    try {
      if (drag.target.hasPointerCapture(drag.pointerId)) drag.target.releasePointerCapture(drag.pointerId);
    } catch {
      // Pointer capture may already be released by the browser on cancellation/blur.
    }
  }

  function handleWorkbenchResizeKey(pane: WorkbenchPane, event: ReactKeyboardEvent<HTMLButtonElement>) {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const step = event.shiftKey ? 48 : 16;
    const sign = event.key === "ArrowRight" ? 1 : -1;
    const current = pane === "assets" ? assetPaneWidth : previewPaneWidth;
    updateWorkbenchPaneWidth(pane, current + sign * step * (pane === "assets" ? 1 : -1));
  }

  async function bindStoryboardCustomGroup(group: CustomAssetGroup) {
    if (!selectedStoryboard) return;
    const memberKeys = new Set(group.members.map((member) => member.assetKey));
    const groupAssets = assets.filter((asset) => memberKeys.has(mainAssetKey(asset)) && assetAppearsInSelectedEpisode(asset));
    if (!groupAssets.length) {
      setMessage("当前剧集没有该资产组成员");
      return;
    }
    const currentReferences = getLatestStoryboardReferenceTokens(selectedStoryboard);
    const referencedIds = new Set(currentReferences.map((item) => resolveUniqueAssetReference(item, storyboardScopeAssets)?.id).filter(Boolean));
    const additions = groupAssets.filter((asset) => !referencedIds.has(asset.id)).map((asset) => asset.variable || assetDisplayVariable(asset));
    if (!additions.length) {
      setMessage("该资产组已在当前分镜中调用");
      return;
    }
    if (!await saveStoryboardReferenceOverrides(selectedStoryboard, [...currentReferences, ...additions])) return;
    setMessage(`已整组调用「${group.name}」中的 ${groupAssets.length} 个资产`);
  }

  async function toggleStoryboardAsset(asset: Asset) {
    if (!selectedStoryboard) return;
    const currentReferences = getLatestStoryboardReferenceTokens(selectedStoryboard);
    const wasReferenced = currentReferences.some((item) => resolveUniqueAssetReference(item, storyboardScopeAssets)?.id === asset.id);
    const nextRefs = currentReferences.filter((item) => resolveUniqueAssetReference(item, storyboardScopeAssets)?.id !== asset.id);
    if (!wasReferenced) nextRefs.push(asset.variable || assetDisplayVariable(asset));
    if (!await saveStoryboardReferenceOverrides(selectedStoryboard, nextRefs)) return;
    if (wasReferenced) setMessage(`已移除引用 ${assetDisplayName(asset)}`);
    else setMessage(`已引用资产 ${assetDisplayName(asset)}`);
  }

  async function removeStoryboardReference(token: string, asset?: Asset) {
    if (!selectedStoryboard) return;
    const currentReferences = getLatestStoryboardReferenceTokens(selectedStoryboard);
    const nextReferences = asset
      ? currentReferences.filter((item) => resolveUniqueAssetReference(item, storyboardScopeAssets)?.id !== asset.id)
      : currentReferences.filter((item) => item !== token);
    if (!await saveStoryboardReferenceOverrides(selectedStoryboard, nextReferences)) return;
    setMessage(`已移除引用 ${asset ? assetDisplayName(asset) : token}`);
  }

  // 首帧/尾帧参考图：存入分镜 payload，删除时传 null（后端会把 None 解释为删除字段）。
  async function saveFrameReference(kind: "首帧" | "尾帧", asset: Asset | null) {
    if (!currentProject || !selectedStoryboard) return;
    const key = kind === "首帧" ? "首帧参考" : "尾帧参考";
    const value = asset
      ? { assetId: asset.id, name: assetDisplayName(asset), variable: assetDisplayVariable(asset), image: selectedAssetImageUrl(asset) || originalAssetImageUrl(asset) }
      : null;
    setBusy(true);
    try {
      const data = await request<{ storyboard: Storyboard }>(`/api/projects/${currentProject.id}/storyboards/${selectedStoryboard.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ payload: { [key]: value } }),
      });
      setStoryboards((items) => items.map((item) => (item.id === data.storyboard.id ? data.storyboard : item)));
      setFramePicker(null);
      setMessage(asset ? `已将「${assetDisplayName(asset)}」设为${kind}参考` : `已移除${kind}参考`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : `${kind}参考保存失败`);
    } finally {
      setBusy(false);
    }
  }

  // 历史版本点击：本地切换预览 + 持久化"当前采用"
  async function selectGenerationVersion(gen: VideoGeneration) {
    if (!currentProject) return;
    setSelectedGenerationId(gen.id);
    try {
      await request(`/api/projects/${currentProject.id}/video-generations/${gen.id}/select`, { method: "POST" });
      setGenerations((items) => items.map((item) => (item.storyboard_id === gen.storyboard_id ? { ...item, is_selected: item.id === gen.id ? 1 : 0 } : item)));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "版本采用状态保存失败");
    }
  }

  function frameReferenceOf(storyboard: Storyboard | null, kind: "首帧" | "尾帧") {
    const value = storyboard?.payload?.[kind === "首帧" ? "首帧参考" : "尾帧参考"];
    return value && typeof value === "object" ? value as { assetId?: string; name?: string; image?: string } : null;
  }

  function storyboardDurationSeconds(storyboard: Storyboard) {
    const match = /(\d+):(\d+):(\d+)\s*-\s*(\d+):(\d+):(\d+)/.exec(storyboard.time_range ?? "");
    if (!match) return null;
    const start = Number(match[1]) * 3600 + Number(match[2]) * 60 + Number(match[3]);
    const end = Number(match[4]) * 3600 + Number(match[5]) * 60 + Number(match[6]);
    return Math.max(0, end - start);
  }

  async function createBlankEpisode() {
    if (!currentProject) return;
    setBusy(true);
    try {
      const data = await request<{ episode: Episode }>(`/api/projects/${currentProject.id}/episodes`, { method: "POST" });
      setEpisodes((items) => [...items, data.episode].sort((a, b) => a.episode_index - b.episode_index));
      setSelectedEpisodeId(data.episode.id);
      setEpisodeMenuOpen(false);
      setMessage(`已创建空白分集「第 ${data.episode.episode_index} 集」，上传原片后即可解析，也可先手动搭建分镜`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "新建分集失败");
    } finally {
      setBusy(false);
    }
  }

  async function uploadEpisodeSource(file: File) {
    if (!currentProject || !selectedEpisode) return;
    setBusy(true);
    try {
      const body = new FormData();
      body.append("file", file);
      const data = await request<{ episode: Episode }>(`/api/projects/${currentProject.id}/episodes/${selectedEpisode.id}/source-video`, { method: "POST", body });
      setEpisodes((items) => items.map((item) => (item.id === data.episode.id ? data.episode : item)));
      setMessage(`已为「第 ${data.episode.episode_index} 集」挂载原片，可进入分镜解析`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "原片挂载失败");
    } finally {
      setBusy(false);
    }
  }

  async function createStoryboard(duplicateFrom?: Storyboard) {
    if (!currentProject || !selectedEpisodeId) return;
    const body = duplicateFrom
      ? { episodeId: selectedEpisodeId, duplicateFromId: duplicateFrom.id }
      : {
          episodeId: selectedEpisodeId,
          title: `分镜_${String(storyboards.length + 1).padStart(3, "0")}`,
          timeRange: selectedStoryboard?.time_range ?? "00:00:00-00:00:08",
        };
    const data = await request<{ storyboard: Storyboard }>(`/api/projects/${currentProject.id}/storyboards`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    await loadStoryboards(currentProject.id, selectedEpisodeId);
    setSelectedStoryboardId(data.storyboard.id);
    setSegmentPanel("shot-prompt");
    setMessage(duplicateFrom ? "分镜已复制" : "分镜已新增");
  }

  async function deleteStoryboard(storyboard: Storyboard) {
    if (!currentProject) return;
    setBusy(true);
    try {
      await request(`/api/projects/${currentProject.id}/storyboards/${storyboard.id}`, { method: "DELETE" });
      const deletedIndex = storyboards.findIndex((item) => item.id === storyboard.id);
      const remaining = storyboards.filter((item) => item.id !== storyboard.id);
      const next = remaining[Math.min(Math.max(deletedIndex, 0), Math.max(remaining.length - 1, 0))];
      setSelectedStoryboardId(next?.id ?? null);
      await loadStoryboards(currentProject.id, selectedEpisodeId);
      setDeletingStoryboard(null);
      setMessage("分镜已删除");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "分镜删除失败，请重试");
    } finally {
      setBusy(false);
    }
  }

  async function moveStoryboard(storyboard: Storyboard, direction: "up" | "down") {
    if (!currentProject) return;
    await request<{ storyboard: Storyboard }>(`/api/projects/${currentProject.id}/storyboards/${storyboard.id}/move`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ direction }),
    });
    await loadStoryboards(currentProject.id, selectedEpisodeId);
    setSelectedStoryboardId(storyboard.id);
  }

  function updateStoryboardPromptText(value: string) {
    if (!selectedStoryboard) return;
    storyboardSaveRevisions.current.set(selectedStoryboard.id, (storyboardSaveRevisions.current.get(selectedStoryboard.id) ?? 0) + 1);
    setStoryboardSaveStates((states) => ({ ...states, [selectedStoryboard.id]: "saving" }));
    setStoryboards((items) => items.map((item) => item.id === selectedStoryboard.id
      ? { ...item, payload: { ...(item.payload ?? {}), "分镜生成正文": value } }
      : item));
  }

  function updateMentionMenuPosition() {
    const editor = promptInputRef.current;
    if (!editor) return;
    const selection = window.getSelection();
    const caretRect = selection?.rangeCount ? selection.getRangeAt(0).getBoundingClientRect() : null;
    const editorRect = editor.getBoundingClientRect();
    const anchor = caretRect && (caretRect.width || caretRect.height || caretRect.top) ? caretRect : editorRect;
    const width = Math.min(360, window.innerWidth - 24);
    const left = Math.max(12, Math.min(anchor.left, window.innerWidth - width - 12));
    const top = Math.max(12, Math.min(anchor.bottom + 7, window.innerHeight - 292));
    setMentionMenuPosition({ left: Math.round(left), top: Math.round(top) });
  }

  function updatePromptMention(value: string, caret: number) {
    const at = value.lastIndexOf("@", Math.max(0, caret - 1));
    const query = at >= 0 ? value.slice(at + 1, caret) : "";
    const prefix = at > 0 ? value[at - 1] : "";
    // 只排除邮箱式 ASCII 前缀；中文提示词末尾直接输入 @ 也必须拉起资产列表。
    if (at >= 0 && !/[A-Za-z0-9_@]/.test(prefix) && /^[\p{L}\p{N}_-]*$/u.test(query)) {
      updateMentionMenuPosition();
      setMentionQuery(query);
      setMentionRange({ start: at, end: caret });
    } else {
      setMentionQuery(null);
      setMentionRange(null);
    }
  }

  function handlePromptInput(event: FormEvent<HTMLDivElement>) {
    const editor = event.currentTarget;
    const value = readPromptEditor(editor);
    if (selectedStoryboard) promptEditorSync.current = { storyboardId: selectedStoryboard.id, value, assets: storyboardScopeAssets };
    updateStoryboardPromptText(value);
    if (promptCompositionActive.current || (event.nativeEvent as InputEvent).isComposing) return;
    const caret = promptSelectionOffset(editor) ?? value.length;
    updatePromptMention(value, caret);
  }

  function handlePromptCompositionEnd() {
    promptCompositionActive.current = false;
    const editor = promptInputRef.current;
    if (!editor) return;
    const value = readPromptEditor(editor);
    const caret = promptSelectionOffset(editor) ?? value.length;
    renderPromptEditor(editor, value, storyboardScopeAssets, openImage);
    const point = promptDomPoint(editor, caret);
    const range = document.createRange();
    range.setStart(point.node, point.offset);
    range.collapse(true);
    const selection = window.getSelection();
    selection?.removeAllRanges();
    selection?.addRange(range);
    if (selectedStoryboard) promptEditorSync.current = { storyboardId: selectedStoryboard.id, value, assets: storyboardScopeAssets };
    updateStoryboardPromptText(value);
    updatePromptMention(value, caret);
  }

  function insertMention(asset: Asset) {
    if (!selectedStoryboard || mentionQuery === null || !mentionRange) return;
    const value = promptInputRef.current ? readPromptEditor(promptInputRef.current) : selectedStoryboardPrompt;
    const stableToken = asset.variable || asset.id;
    if (promptInputRef.current) insertPromptToken(promptInputRef.current, mentionRange.start, mentionRange.end, stableToken, storyboardScopeAssets, openImage);
    if (promptInputRef.current) {
      const selection = window.getSelection();
      const range = selection?.rangeCount ? selection.getRangeAt(0).cloneRange() : null;
      if (range) {
        const space = document.createTextNode(" ");
        range.insertNode(space);
        range.setStartAfter(space);
        range.collapse(true);
        selection?.removeAllRanges();
        selection?.addRange(range);
      }
    }
    const nextValue = promptInputRef.current ? readPromptEditor(promptInputRef.current) : `${value.slice(0, mentionRange.start)}${stableToken} ${value.slice(mentionRange.end)}`;
    if (selectedStoryboard) promptEditorSync.current = { storyboardId: selectedStoryboard.id, value: nextValue, assets: storyboardScopeAssets };
    updateStoryboardPromptText(nextValue);
    const currentReferences = getLatestStoryboardReferenceTokens(selectedStoryboard);
    if (!currentReferences.some((item) => resolveUniqueAssetReference(item, storyboardScopeAssets)?.id === asset.id)) {
      void saveStoryboardReferenceOverrides(selectedStoryboard, [...currentReferences, stableToken]);
    }
    setMentionQuery(null);
    setMentionRange(null);
    promptInputRef.current?.focus();
  }

  function handlePromptMentionKeys(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (promptCompositionActive.current || event.nativeEvent.isComposing) return;
    if (mentionQuery !== null && event.key === "Escape") {
      event.preventDefault();
      setMentionQuery(null);
      setMentionRange(null);
      return;
    } else if (mentionQuery !== null && event.key === "ArrowDown" && mentionOptions.length) {
      event.preventDefault();
      setActiveMentionIndex((index) => (index + 1) % mentionOptions.length);
      return;
    } else if (mentionQuery !== null && event.key === "ArrowUp" && mentionOptions.length) {
      event.preventDefault();
      setActiveMentionIndex((index) => (index - 1 + mentionOptions.length) % mentionOptions.length);
      return;
    } else if (mentionQuery !== null && event.key === "Enter" && mentionOptions.length) {
      event.preventDefault();
      insertMention(mentionOptions[activeMentionIndex] ?? mentionOptions[0]);
      return;
    }
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      const editor = promptInputRef.current;
      const selection = window.getSelection();
      if (!editor || !selection?.rangeCount || !editor.contains(selection.anchorNode)) return;
      document.execCommand("insertText", false, "\n");
      const value = readPromptEditor(editor);
      if (selectedStoryboard) promptEditorSync.current = { storyboardId: selectedStoryboard.id, value, assets: storyboardScopeAssets };
      updateStoryboardPromptText(value);
      setMentionQuery(null);
      setMentionRange(null);
    }
  }

  useEffect(() => {
    void loadProjects().catch(() => undefined);
  }, []);
  useEffect(() => {
    if (modal !== "image-view-modal") return;
    const closeOnEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") setModal(null);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [modal]);
  useEffect(() => {
    if (!voiceEditor) return;
    const closeOnEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key !== "Escape" || busy) return;
      if (voiceRefAudioRef.current) { voiceRefAudioRef.current.pause(); voiceRefAudioRef.current = null; }
      setVoiceRefPlaying(false);
      setVoiceEditor(null);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [voiceEditor?.assetId, busy]);
  useEffect(() => {
    const releaseDragOnBlur = () => endWorkbenchResize();
    window.addEventListener("blur", releaseDragOnBlur);
    return () => {
      window.removeEventListener("blur", releaseDragOnBlur);
      endWorkbenchResize();
    };
  }, []);
  useEffect(() => {
    const media = window.matchMedia("(max-width: 1100px)");
    const sync = () => {
      if (media.matches) { setAssetSidebarCollapsed(true); setPreviewPanelCollapsed(true); }
    };
    sync();
    media.addEventListener("change", sync);
    return () => media.removeEventListener("change", sync);
  }, []);

  useEffect(() => {
    if (assets.length && (!selectedAssetId || !assets.some((asset) => asset.id === selectedAssetId))) {
      setSelectedAssetId(assets[0].id);
    }
  }, [assets, selectedAssetId]);

  const hasAssetGenerationInProgress = assets.some((asset) => asset.payload?.generationInProgress === true);
  useEffect(() => {
    if (!currentProject || !hasAssetGenerationInProgress) return;
    let active = true;
    const refresh = () => {
      void request<{ assets: Asset[] }>(`/api/projects/${currentProject.id}/assets`)
        .then((data) => { if (active) setAssets(data.assets); })
        .catch((error) => { if (active) setMessage(error.message); });
    };
    const timer = window.setInterval(refresh, 1500);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [currentProject?.id, hasAssetGenerationInProgress]);

  useEffect(() => {
    setAssetPromptDraft(selectedAsset ? assetPrompt(selectedAsset) : "");
    selectedAssetIdForPrompt.current = selectedAsset?.id ?? null;
  }, [selectedAsset?.id, selectedAsset?.payload?.["最终资产图生成提示词"], selectedAsset?.payload?.["用户编辑资产图生成提示词"]]);

  useEffect(() => {
    if (storyboards.length && (!selectedStoryboardId || !storyboards.some((item) => item.id === selectedStoryboardId))) {
      setSelectedStoryboardId(storyboards[0].id);
    }
  }, [storyboards, selectedStoryboardId]);

  useEffect(() => {
    if (currentProject && screen === "segments") {
      setStoryboardLoading(false);
      loadStoryboards(currentProject.id);
    }
  }, [selectedEpisodeId]);

  useEffect(() => {
    document.body.dataset.screen = screen;
  }, [screen]);

  useEffect(() => {
    if (screen !== "analysis" || !isTaskRunning(activeAnalysisTask)) return;
    const timer = window.setInterval(() => setAnalysisNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [screen, activeAnalysisTask?.id, activeAnalysisTask?.status]);

  const displayStyleType = remakeMode === "cg_3d" || storyboardVisualStyle === "cg_3d" ? "3DCG 画面" : "真人画面";
  const paramSummary = `${frameRatio} · ${resolution}`;
  const analysisFailed = activeAnalysisTask && ["失败", "failed"].includes(activeAnalysisTask.status);
  const analysisDone = activeAnalysisTask && (analysisFailed || ["成功", "完成", "completed"].includes(activeAnalysisTask.status));
  const analysisProgressIsIndeterminate = Boolean(isTaskRunning(activeAnalysisTask) && activeAnalysisTask?.task_type !== "series_analysis");
  const analysisElapsedSeconds = activeAnalysisTask?.created_at
    ? Math.max(0, Math.floor((analysisNow - new Date(activeAnalysisTask.created_at).getTime()) / 1000))
    : 0;
  const analysisIsDegraded =
    analysisDone && !analysisFailed &&
    (Boolean(activeAnalysisTask?.result?.自动降级) || Boolean(activeAnalysisTask?.result?.模型错误));
  const analysisWarning =
    activeAnalysisTask?.result?.降级原因
    || (activeAnalysisTask?.result?.模型错误 ? "方舟分析超时，当前为基础可编辑资产：" + String(activeAnalysisTask.result.模型错误).slice(0, 120) : null);
  const selectedAssetGeneratedImage = selectedAssetImageUrl(selectedAsset);
  const selectedAssetOriginalImage = originalAssetImageUrl(selectedAsset);
  const selectedAssetFavoriteImageId = String(selectedAsset?.payload?.["收藏版本图片ID"] ?? "");
  const selectedImageVersion = selectedAssetImage(selectedAsset);
  const isGeneratingSelectedAsset = Boolean(selectedAsset && (generatingAssetIds.has(selectedAsset.id) || selectedAsset.payload?.generationInProgress === true));
  const storyboardGenerations = useMemo(
    () => generations.filter((item) => item.storyboard_id === selectedStoryboard?.id),
    [generations, selectedStoryboard?.id]
  );
  const activeGeneration =
    storyboardGenerations.find((item) => item.id === selectedGenerationId)
    ?? storyboardGenerations.find((item) => item.is_selected === 1)
    ?? storyboardGenerations[0]
    ?? null;
  const videoComparisonItems = useMemo(() => storyboards.map((storyboard) => {
    const range = parseRange(storyboard.time_range || "");
    const versions = generations.filter((item) => item.storyboard_id === storyboard.id && Boolean(item.output_url));
    const selectedVersion = versions.find((item) => item.is_selected === 1) ?? versions[0] ?? null;
    return {
      storyboardId: storyboard.id,
      title: storyboard.title,
      generationId: selectedVersion?.id ?? null,
      originalStartMs: Math.round(Math.max(0, range.start) * 1000),
      originalEndMs: Math.round(Math.max(range.start, range.end) * 1000),
    };
  }), [storyboards, generations]);
  const messageIsError = /(失败|错误|冲突|无法|不可|请重试|未保存)/.test(message);
  useEffect(() => {
    setSelectedGenerationId(null);
    setShotMenu(null);
  }, [selectedStoryboard?.id]);
  useEffect(() => {
    if (!shotMenu) return;
    const closeOnOutsideClick = (event: PointerEvent) => {
      const target = event.target as HTMLElement | null;
      if (!target?.closest(".shot-card-menu") && !target?.closest(".shot-card-menu-trigger")) setShotMenu(null);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setShotMenu(null);
    };
    document.addEventListener("pointerdown", closeOnOutsideClick);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOnOutsideClick);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [shotMenu]);
  useEffect(() => {
    if (!message || messageIsError) return;
    const timer = window.setTimeout(() => setMessage(""), 4200);
    return () => window.clearTimeout(timer);
  }, [message, messageIsError]);

  return (
    <>
      <header className="app-top">
        <div className="project">
          <button className="back-btn" onClick={() => {
            // 资产生成（详情/分组）页点返回 → 先回资产列表，而不是直接退出项目
            if (screen === "assets" && assetView !== "overview") { setAssetView("overview"); return; }
            setScreen("home");
          }}>‹</button>
          <div className="home-brand">视频重制工坊</div>
          <div className="project-copy">
          <div className="project-title" onClick={async () => { if (!currentProject) return; const title = window.prompt("项目名称", currentProject.title); if (title === null || !title.trim()) return; const data = await request<{ project: Project }>(`/api/projects/${currentProject.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: title.trim() }) }); setCurrentProject(data.project); await loadProjects(); }}>{currentProject?.title ?? "未命名项目"} <span>✎</span></div>
          <div className="project-meta">{frameRatio}　｜　{resolution}</div>
          </div>
        </div>
        {(screen === "assets" || screen === "segments" || screen === "compare") && (
          <div className="episode-switch topbar-episode-switch">
            <button type="button" className="episode-switch-btn" aria-expanded={episodeMenuOpen} onClick={() => setEpisodeMenuOpen((value) => !value)}>
              <b>{selectedEpisode ? `第 ${selectedEpisode.episode_index} 集` : "选择分集"}</b><i aria-hidden="true" />
            </button>
            {episodeMenuOpen && (
              <div className="episode-switch-menu" role="menu" aria-label="切换分集">
                {episodes.map((episode) => (
                  <button type="button" role="menuitem" className={episode.id === selectedEpisodeId ? "active" : ""} key={episode.id} onClick={() => { setSelectedEpisodeId(episode.id); setEpisodeMenuOpen(false); }}>
                    <b>第 {episode.episode_index} 集</b><span title={episode.filename}>{episode.filename}</span>{!episode.file_url && <i className="episode-tag pending">待上传原片</i>}
                  </button>
                ))}
                <button type="button" className="episode-switch-new" disabled={busy} onClick={() => void createBlankEpisode()}>＋ 新建分集</button>
              </div>
            )}
          </div>
        )}
        <nav className="steps">
          {[
            ["home", "▻", "原片"],
            ["assets", "▣", "设定"],
            ["segments", "▦", "分镜"],
            ["compare", "⌁", "视频"],
          ].map(([id, icon, label]) => (
            <button className={activeStep() === id ? "step active" : "step"} key={id} onClick={() => setScreen(id as StepScreen)}>
              <span>{icon}</span>{label}
            </button>
          ))}
        </nav>
        <div className="top-actions">
          {screen === "segments" && (
            <button type="button" className="ghost small script-record-btn" title="查看原剧本与原片对照（规划中）" onClick={() => setMessage("「原剧本视频记录」功能规划中，将展示原剧本与原片片段的对照关系")}>原剧本视频记录</button>
          )}
          {(screen === "assets" || screen === "segments" || screen === "compare") && <span className="credit-pill" title="积分余额（示意）">⚡ 100.0</span>}
          {screen === "assets" && (
            <button className="primary enter-storyboards" disabled={busy || !assets.length} onClick={() => void enterStoryboards()}>进入分镜&nbsp; →</button>
          )}
        </div>
      </header>

      {message && message !== "准备就绪" && !modal && <div className={messageIsError ? "global-message error" : "global-message"} role={messageIsError ? "alert" : "status"} aria-live="polite"><span>{message}</span><button type="button" aria-label="关闭提示" onClick={() => setMessage("")}>×</button></div>}
      <main>
        <section className={screen === "home" ? "screen active home-screen" : "screen home-screen"} id="home">
          <div className="upload-card">
            <div className="upload-heading">上传本地视频</div>

            {!selectedSource && (
              <section className="source-empty">
                <div className="drop-icon">▣</div>
                <b>上传本地视频</b>
                <span>可上传整部短剧文件夹，也可先上传单个视频。确认素材后再开始上传。</span>
                <div className="empty-actions">
                  <button className="primary" disabled={busy} onClick={() => folderInputRef.current?.click()}>上传文件夹</button>
                  <button className="ghost" disabled={busy} onClick={() => fileInputRef.current?.click()}>上传单个视频</button>
                </div>
              </section>
            )}

            {selectedSource && (
              <section className="selected-source">
                <div>
                  <span>本地视频</span>
                  <b>{selectedSource.title}</b>
                  <small>{selectedSource.meta}</small>
                </div>
                <button className="ghost" onClick={() => {
                  setSelectedSource(null);
                  setSelectedFiles(null);
                  setUploadState("idle");
                }}>重新选择</button>
              </section>
            )}

            <div className="config-row">
              <label className="select-field">画面类型<select value={storyboardVisualStyle ?? "live_action"} onChange={(event) => { const style = event.target.value as "live_action" | "cg_3d"; setStoryboardVisualStyle(style); setRemakeMode(style === "cg_3d" ? "cg_3d" : targetLanguage === "沿用原片语言" ? "asset_replacement" : "overseas_localization"); }}><option value="live_action">真人</option><option value="cg_3d">3D</option></select></label>
              <label className="select-field">地区语言<select value={targetLanguage} onChange={(event) => { const language = event.target.value; setTargetLanguage(language); setTargetCountry(language === "沿用原片语言" ? "中国" : language === "日语" ? "日本" : language === "韩语" ? "韩国" : language === "英语" ? "美国" : "海外"); setRemakeMode(storyboardVisualStyle === "cg_3d" ? "cg_3d" : language === "沿用原片语言" ? "asset_replacement" : "overseas_localization"); setReplaceCharacters(language === "沿用原片语言"); setReplaceScenes(language === "沿用原片语言"); }}><option value="沿用原片语言">国内（原片语言）</option><option value="英语">海外·英语</option><option value="日语">海外·日语</option><option value="韩语">海外·韩语</option><option value="西班牙语">海外·西班牙语</option><option value="法语">海外·法语</option></select></label>
              <div className="spacer"></div>
              <details className="param-sheet">
                <summary>生成参数 <b>{paramSummary}</b></summary>
                <div className="param-popover">
                  <div className="param-row"><span>画幅</span><div className="param-options">{["16:9", "9:16", "1:1"].map((item) => <button className={frameRatio === item ? "active" : ""} key={item} onClick={() => setFrameRatio(item)}>{item}</button>)}</div></div>
                  <div className="param-row"><span>清晰度</span><div className="param-options">{["480p", "720p", "1080p"].map((item) => <button className={resolution === item ? "active" : ""} key={item} onClick={() => setResolution(item)}>{item}</button>)}</div></div>
                  {remakeMode === "overseas_localization" && <div className="param-row"><span>风格</span><div className="param-options">{["写实通用", "欧美短剧", "广告质感"].map((item) => <button className={styleType === item ? "active" : ""} key={item} onClick={() => setStyleType(item)}>{item}</button>)}</div></div>}
                </div>
              </details>
              <button className="create-btn in-card" disabled={busy || !selectedSource} onClick={() => setModal("upload-confirm-modal")}>开始上传</button>
            </div>

            <input hidden multiple type="file" accept="video/*" ref={fileInputRef} onChange={(event: ChangeEvent<HTMLInputElement>) => {
              handleLocalFiles(event.target.files, "single");
              event.currentTarget.value = "";
            }} />
            <input hidden multiple type="file" accept="video/*" ref={folderInputRef} {...{ webkitdirectory: "" }} onChange={(event: ChangeEvent<HTMLInputElement>) => {
              handleLocalFiles(event.target.files, "folder");
              event.currentTarget.value = "";
            }} />
          </div>

          <section className="project-library">
            <div className="library-head">
              <div><h2>项目管理</h2><span>过往重绘项目</span></div>
              <div className="library-actions"><button className="ghost" onClick={createProject}>新建项目</button><button className="ghost" onClick={() => currentProject && selectProject(currentProject.id)}>刷新当前项目</button></div>
            </div>
            <div className="project-library-tools"><input type="search" value={projectSearch} placeholder="搜索项目名称或地区" onChange={(event) => { setProjectSearch(event.target.value); setProjectPage(0); }} aria-label="搜索项目" /><span>{projectsLoading ? "正在加载项目…" : projectsError ? "项目列表加载失败" : "共 " + searchedProjects.length + " 个项目"}</span></div>
            {projectsError && <div className="project-load-error" role="alert"><span>项目列表暂时不可用：{projectsError}</span><button className="ghost" onClick={() => void loadProjects().catch(() => undefined)}>重试</button></div>}
            <div className="project-grid">
              {pagedProjects.map((project, index) => (
                <article className="project-card" key={project.id} onClick={() => {
                  void selectProject(project.id);
                  setScreen("source");
                }}>
                  <div className={`project-cover cover-${["a", "b", "c", "d"][index % 4]}`}></div>
                  <div className="project-info">
                  <span>{project.status}</span>
                  <span className="project-mode-tag">{project.remake_mode === "cg_3d" ? "3DCG 风格" : project.remake_mode === "asset_replacement" ? "替换角色/场景" : "海外本土化"}</span>
                    <b>{project.title}</b>
                  <small>{project.remake_mode === "overseas_localization" ? `${project.target_country} · ${project.target_language}` : `源语言：${project.source_language || "待确认"}`}</small>
                  </div>
                </article>
              ))}
            </div>
            {!projectsLoading && !projectsError && !pagedProjects.length && <p className="project-empty">{projects.length ? "没有匹配的项目，请调整搜索词。" : "还没有项目，创建项目或上传视频开始制作。"}</p>}
            {!projectsError && searchedProjects.length > projectPageSize && <div className="list-pagination"><button className="ghost" disabled={projectPage === 0} onClick={() => setProjectPage((page) => Math.max(0, page - 1))}>上一页</button><span>{projectPage + 1} / {Math.ceil(searchedProjects.length / projectPageSize)}</span><button className="ghost" disabled={(projectPage + 1) * projectPageSize >= searchedProjects.length} onClick={() => setProjectPage((page) => page + 1)}>下一页</button></div>}
          </section>
        </section>

        <section className={screen === "source" ? "screen active page-screen" : "screen page-screen"} id="source">
          <div className="info-bar">
            {analysisLocked
              ? "本项目整剧分析进行中或已完成，剧集已锁定，不能再上传或调整。"
              : uploadInProgress
                ? `正在上传《${selectedSource?.title ?? currentProject?.title ?? "当前项目"}》原片视频。上传完成后，可继续上传或开始整剧分析。`
                : effectiveUploadState === "failed"
                  ? "上传失败，请在下方失败剧集的操作列点击「重新上传」，或点击「继续上传」重新选择文件。"
                  : allEpisodesUploaded
                    ? "全部剧集已上传成功，可以开始整剧分析；也可以在分析前继续上传更多剧集。"
                    : "尚未开始上传，点击「继续上传」选择本地视频文件。"}
          </div>
          <div className="page-head">
            <h1>原片剧集</h1>
            <div>全部 {episodes.length || sourceEpisodeCount}　已上传 {uploadedEpisodeCount || (effectiveUploadState === "uploaded" ? episodes.length || sourceEpisodeCount : 0)}　上传中 {uploadingEpisodeCount}　失败 {failedEpisodeCount}</div>
          </div>
          <div className="table-card">
            <table>
              <thead><tr><th>剧集信息</th><th>时长</th><th>大小</th><th>状态</th><th>操作</th></tr></thead>
              <tbody>
                {pagedEpisodes.map((episode) => {
                  const uploaded = Boolean(episode.ark_file_id || episode.status.includes("已上传"));
                  const failed = !uploaded && (effectiveUploadState === "failed" || /失败|failed/i.test(episode.status));
                  return <tr key={episode.id} className={episode.id === selectedEpisodeId ? "selected" : ""} onClick={() => { if (!episode.id.startsWith("pending-")) setSelectedEpisodeId(episode.id); }}>
                    <td><button type="button" className="episode-select" aria-pressed={episode.id === selectedEpisodeId} disabled={episode.id.startsWith("pending-")}><b>第 {episode.episode_index} 集</b><span>{episode.filename}</span></button></td>
                    <td>--:--</td>
                    <td>{formatBytes(episode.size_bytes)}</td>
                    <td><span className="episode-status"><i className={`dot ${uploaded ? "green" : failed ? "red" : "blue"}`}></i>{uploaded ? "上传成功" : effectiveUploadState === "uploading" ? "上传中" : failed ? "上传失败" : "等待上传"}</span></td>
                    <td><button type="button" className="episode-reupload" disabled={busy || !failed || analysisLocked || uploadInProgress} onClick={(event) => { event.stopPropagation(); retryUploadInputRef.current?.click(); }}>重新上传</button></td>
                  </tr>;
                })}
              </tbody>
            </table>
          </div>
          <input hidden multiple type="file" accept="video/*" ref={retryUploadInputRef} onChange={(event: ChangeEvent<HTMLInputElement>) => { void handleReuploadSelection(event); }} />
          {sourceEpisodeRows.length > sourcePageSize && <div className="list-pagination"><button className="ghost" disabled={sourcePage === 0} onClick={() => setSourcePage((page) => Math.max(0, page - 1))}>上一页</button><span>第 {sourcePage * sourcePageSize + 1}–{Math.min(sourceEpisodeRows.length, (sourcePage + 1) * sourcePageSize)} 集 / 共 {sourceEpisodeRows.length} 集</span><button className="ghost" disabled={(sourcePage + 1) * sourcePageSize >= sourceEpisodeRows.length} onClick={() => setSourcePage((page) => page + 1)}>下一页</button></div>}
          {analysisLocked ? (
            <section className="analysis-choice source-analysis-choice">
              <div>
                <b>剧集已锁定</b>
                <span>本项目整剧分析进行中或已完成，不能再上传或调整剧集；如需更换素材，请新建项目。</span>
              </div>
              <div className="analysis-actions">
                <button className="primary" onClick={() => setScreen("assets")}>查看资产</button>
              </div>
            </section>
          ) : (
            <section className="analysis-choice source-analysis-choice">
              <div>
                <b>整剧分析</b>
                <span>{allEpisodesUploaded
                  ? "全部剧集已上传成功，可以开始整剧分析；分析完成后剧集将锁定，不能再上传调整。"
                  : `已上传成功 ${uploadedEpisodeCount}/${episodes.length || sourceEpisodeCount} 集，全部剧集上传成功后才能开始分析。`}</span>
              </div>
              <div className="analysis-actions">
                <button className="ghost" disabled={busy || uploadInProgress} onClick={() => retryUploadInputRef.current?.click()}>继续上传</button>
                <button className="primary" disabled={!canStartSeriesAnalysis} title={canStartSeriesAnalysis ? undefined : "全部剧集上传成功后才能开始分析"} onClick={() => startSeriesAnalysis("normal")}>开始分析全部剧集</button>
              </div>
            </section>
          )}
          <div className="footer-actions">
            <button className="ghost" onClick={() => setScreen("home")}>返回</button>
          </div>
        </section>

        <section className={screen === "analysis" ? "screen active page-screen analysis-screen" : "screen page-screen analysis-screen"} id="analysis">
          <div className="analysis-loading-card">
            {analysisFailed ? (
              <>
                <div className="analysis-failed-icon">✕</div>
                <h1>分析失败</h1>
                <p className="analysis-error-message">{activeAnalysisTask?.message || "分析过程中发生错误"}</p>
                <div className="analysis-failed-actions">
                  <button className="ghost" onClick={() => setScreen("source")}>返回上传页</button>
                  <button className="primary" onClick={() => activeAnalysisTask?.task_type === "series_analysis" ? startSeriesAnalysis("normal") : startAnalysis("normal")}>重试分析</button>
                </div>
              </>
            ) : analysisDone ? (
              <>
                <div className="analysis-complete-icon">✓</div>
                <h1>分析完成</h1>
                <p>{activeAnalysisTask?.message || "分析结果已保存，可以继续审核资产并进入分镜。"}{analysisIsDegraded && <small className="analysis-elapsed">部分内容使用回退结果</small>}</p>
                <div className="analysis-failed-actions"><button className="ghost" onClick={() => setScreen("source")}>返回原片</button><button className="primary" onClick={() => setScreen("assets")}>查看资产</button></div>
              </>
            ) : (
              <>
                <div className="loader-ring"></div>
                <h1>正在分析视频资源</h1>
                <p>{message}{isTaskRunning(activeAnalysisTask) && activeAnalysisTask?.task_type !== "series_analysis" && (
                  <small className="analysis-elapsed">已用时 {Math.floor(analysisElapsedSeconds / 60)} 分 {analysisElapsedSeconds % 60} 秒</small>
                )}</p>
                <div className="analysis-progress-list">
                  <article>
                    <div className="analysis-head"><b>剧情解析转化进度</b><span>{analysisProgressIsIndeterminate ? "处理中" : `${analysisProgress.story}%`}</span></div>
                    <div className={analysisProgressIsIndeterminate ? "progress indeterminate" : "progress"}><i style={analysisProgressIsIndeterminate ? undefined : { width: `${analysisProgress.story}%` }}></i></div>
                    <small>识别剧情段落、台词、镜头节奏，并转化为目标国家分镜基础描述。</small>
                  </article>
                  <article>
                    <div className="analysis-head"><b>资产解析转化进度</b><span>{analysisProgressIsIndeterminate ? "处理中" : `${analysisProgress.asset}%`}</span></div>
                    <div className={analysisProgressIsIndeterminate ? "progress indeterminate" : "progress"}><i style={analysisProgressIsIndeterminate ? undefined : { width: `${analysisProgress.asset}%` }}></i></div>
                    <small>通过视频抽帧和视觉理解分析角色、场景、道具与画面文字。</small>
                  </article>
                </div>
                <button className={analysisDone ? "primary" : "primary disabled"} disabled={!analysisDone} onClick={() => {
                  setScreen("assets");
                  showAssetOverview("角色");
                }}>进入设定</button>
                {!analysisDone && (
                  <button className="ghost" style={{ marginTop: 12 }} onClick={() => { sessionStorage.removeItem("activeAnalysisTaskId"); setScreen("source"); }}>取消并返回</button>
                )}
              </>
            )}
          </div>
        </section>

        <section className={screen === "assets" ? "screen active page-screen" : "screen page-screen"} id="assets">
          <div className="asset-workspace-nav">
            <div className="module-tabs" role="tablist" aria-label="资产类别">
              {(["角色", "场景", "道具", "资产进度"] as AssetTab[]).map((tab) => (
                <button type="button" role="tab" aria-selected={assetTab === tab} className={assetTab === tab ? "active" : ""} key={tab} onClick={() => showAssetOverview(tab)}>{tab}</button>
              ))}
            </div>
            <div className="asset-count-summary" aria-label="资产统计">
              <span>角色 <b>{roleCount}</b></span>
              <span>场景 <b>{sceneCount}</b></span>
              <span>道具 <b>{propCount}</b></span>
            </div>
          </div>

          {analysisIsDegraded && assetView !== "detail" && (
            <div className="degradation-banner">
              <span>⚠ {analysisWarning || "当前资产和分镜使用回退测试数据，不具备真实模型分析结果。"}</span>
            </div>
          )}
          {assetView === "overview" && (
            <div className="asset-overview sub-panel active">
              <div className="overview-head">
                <h2 className="sr-only">{assetTab === "资产进度" ? "资产进度" : `${assetTab}资产`}</h2>
                <div className="asset-status-tabs" role="group" aria-label="资产状态筛选">
                  {([["all", "全部"], ["pending", "待确认"], ["confirmed", "已确认"]] as const).map(([value, label]) => (
                    <button type="button" key={value} className={assetStatusFilter === value ? "active" : ""} aria-pressed={assetStatusFilter === value} onClick={() => setAssetStatusFilter(value)}>{label}</button>
                  ))}
                </div>
                <div className="overview-actions">
                  {assetTab !== "资产进度" && (assetSelectionMode ? (
                    <button className="ghost" onClick={cancelAssetSelectionMode} disabled={busy}>退出批量</button>
                  ) : (
                    <>
                      <div className="asset-toolbar">
                        <button type="button" className={assetSearchOpen ? "asset-toolbar-icon active" : "asset-toolbar-icon"} aria-label="搜索资产" aria-expanded={assetSearchOpen} onClick={() => setAssetSearchOpen((open) => !open)}>
                          <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true"><circle cx="6" cy="6" r="4.2" stroke="currentColor" strokeWidth="1.3" /><path d="M9.2 9.2L12.2 12.2" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" /></svg>
                        </button>
                        {assetSearchOpen && (
                          <span className="asset-toolbar-search">
                            <input autoFocus value={assetSearchQuery} onChange={(event) => setAssetSearchQuery(event.target.value)} placeholder={`搜索${assetTab}名称 / 变量`} aria-label="搜索资产" />
                            {assetSearchQuery && <button type="button" aria-label="清空搜索" onClick={() => setAssetSearchQuery("")}>×</button>}
                          </span>
                        )}
                        <span className="asset-toolbar-divider" aria-hidden="true" />
                        <button type="button" className="asset-toolbar-icon" aria-label="批量删除资产" onClick={() => startAssetSelectionMode("delete")} disabled={busy}>
                          <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true"><path d="M2.5 3.8h9M5.4 3.8V2.6h3.2v1.2M3.6 3.8l.6 7.3h5.6l.6-7.3" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" /></svg>
                        </button>
                        <span className="asset-toolbar-divider" aria-hidden="true" />
                        <button type="button" className="asset-toolbar-action" onClick={() => void createBlankAsset()} disabled={busy}>
                          <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true"><path d="M6 1.5v9M1.5 6h9" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" /></svg>
                          新建资产
                        </button>
                        <span className="asset-toolbar-divider" aria-hidden="true" />
                        <button type="button" className="asset-toolbar-action" onClick={() => startAssetSelectionMode("generate")} disabled={busy}>
                          <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true"><rect x="1.8" y="2.8" width="10.4" height="8.4" rx="1.6" stroke="currentColor" strokeWidth="1.2" /><circle cx="4.9" cy="5.9" r="1" fill="currentColor" /><path d="M2.2 10.4l3.1-2.7 2.2 1.8 1.9-1.5 2.4 2.4" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round" /></svg>
                          一键生图
                        </button>
                        <span className="asset-toolbar-divider" aria-hidden="true" />
                        <button type="button" className="asset-toolbar-action" onClick={() => startAssetSelectionMode("review")} disabled={busy}>
                          <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true"><circle cx="7" cy="4.6" r="2.3" stroke="currentColor" strokeWidth="1.2" /><path d="M2.8 12c.4-2.3 2.1-3.5 4.2-3.5s3.8 1.2 4.2 3.5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" /></svg>
                          一键送审
                        </button>
                      </div>
                      <button type="button" className="asset-group-btn" onClick={() => startAssetSelectionMode("group")} disabled={busy}>批量分组</button>
                    </>
                  ))}
                </div>
              </div>
              {assetSelectionMode && (
                <div className="asset-batch-bar">
                  <label className="asset-batch-select-all">
                    <input type="checkbox" checked={allVisibleAssetsSelected} onChange={toggleSelectAllAssetGroups} disabled={!filteredGroups.length} />
                    全选（{filteredGroups.length}）
                  </label>
                  <span className="asset-batch-count">已选 {selectedAssetGroupKeys.size} 项</span>
                  <div className="asset-batch-actions">
                    <button className={assetBatchAction === "review" ? "primary small" : "ghost small"} disabled={!selectedAssetGroupKeys.size || busy} onClick={batchSubmitAssetReview}>批量送审</button>
                    <button className={assetBatchAction === "generate" ? "primary small" : "ghost small"} disabled={!selectedAssetGroupKeys.size || busy} onClick={() => void batchGenerateAssetImages()}>批量生图</button>
                    <details className="asset-batch-group-menu">
                      <summary className={assetBatchAction === "group" ? "batch-action-active" : ""}>分组</summary>
                      <div>
                        <button className="ghost small" disabled={!selectedAssetGroupKeys.size || busy} onClick={openCreateAssetGroupModal}>新建分组</button>
                        <button className="ghost small" disabled={!selectedAssetGroupKeys.size || busy} onClick={openMoveAssetGroupModal}>加入已有分组</button>
                      </div>
                    </details>
                    <button className={assetBatchAction === "delete" ? "asset-danger solid small" : "ghost small asset-danger"} disabled={!selectedAssetGroupKeys.size || busy} onClick={openBatchDeleteConfirm}>删除</button>
                    <button className="ghost small" onClick={cancelAssetSelectionMode}>取消</button>
                  </div>
                </div>
              )}
              {assetTab === "资产进度" ? (
                <div className="sub-panel simple-panel active">
                  <div className="metric"><b>待确认</b><span>{assets.filter((asset) => asset.status !== "已确认").length} 个资产</span></div>
                  <div className="metric"><b>已确认</b><span>{assets.filter((asset) => asset.status === "已确认").length} 个子资产</span></div>
                  <div className="review-rule">选择一项待确认资产可直接进入审核；确认资产后，可在分镜中检查并引用。</div>
                  <div className="progress-action-list">{assetGroups.filter((group) => group.items.some((item) => item.status !== "已确认")).slice(0, 16).map((group) => { const pending = group.items.find((item) => item.status !== "已确认") ?? group.items[0]; return <button type="button" key={group.key} onClick={() => { setSelectedAssetId(pending.id); setAssetView("detail"); }}><span>{group.type} · {group.localizedName || group.name}</span><b>{group.items.filter((item) => item.status !== "已确认").length} 项待确认 →</b></button>; })}{!assetGroups.some((group) => group.items.some((item) => item.status !== "已确认")) && <p>当前资产已全部确认。</p>}</div>
                </div>
              ) : (
                <>
                  {customAssetGroups.filter((customGroup) => customGroup.assetType === assetTab).length > 0 && (
                    <section className="asset-group-overview">
                      <div className="panel-title">资产组</div>
                      <div className="asset-grid overview-grid">
                        {customAssetGroups.filter((customGroup) => customGroup.assetType === assetTab).map((customGroup) => {
                          const memberGroups = assetGroups.filter((group) => customGroup.members.some((member) => member.assetKey === group.key));
                          const firstMember = memberGroups[0];
                          const secondMember = memberGroups[1] ?? memberGroups[0];
                          const firstUrl = firstMember ? groupOriginalImageUrl(firstMember) : "";
                          const secondUrl = secondMember ? groupConfirmedImageUrl(secondMember) : "";
                          return (
                            <article className="asset-card asset-group-overview-card" key={customGroup.id} onClick={() => {
                              setSelectedCustomGroupId(customGroup.id);
                              setAssetGroupNameDraft(customGroup.name);
                              setAssetView("group-detail");
                            }}>
                              <i>资产组</i>
                              <div className="asset-card-visuals">
                                <figure>
                                  {firstUrl ? <img className="asset-card-image" src={firstUrl} alt="组内资产图" onClick={(event) => event.stopPropagation()} /> : <div className="thumb"></div>}
                                  <em>组</em>
                                  <figcaption>{firstMember?.name ?? "组内成员"}</figcaption>
                                </figure>
                                <figure>
                                  {secondUrl ? <img className="asset-card-image" src={secondUrl} alt="组内目标资产图" onClick={(event) => event.stopPropagation()} /> : <div className="asset-placeholder compact">成员</div>}
                                  <em>组</em>
                                  <figcaption>{secondMember?.localizedName ?? "组内成员"}</figcaption>
                                </figure>
                              </div>
                              <b>{customGroup.name}</b>
                              <span>{customGroup.members.length} 个资产 · {customGroup.assetType}</span>
                            </article>
                          );
                        })}
                      </div>
                    </section>
                  )}
                  <div className="asset-groups-divider"><span>未分组资产</span></div>
                  <div className="asset-grid overview-grid">
                    {filteredGroups.map((group) => renderAssetCard(group))}
                    {!filteredGroups.length && <p className="empty-shot-assets">当前类型没有未分组资产。</p>}
                  </div>
                </>
              )}
            </div>
          )}

          {assetView === "group-detail" && selectedCustomGroup && (
            <div className="asset-detail sub-panel active">
              <div className="detail-topbar">
                <button className="ghost" onClick={() => setAssetView("overview")}>返回资产概览</button>
                <h2>{selectedCustomGroup.name}</h2>
                <button className="ghost" disabled={busy} onClick={() => deleteCustomGroup(selectedCustomGroup)}>删除资产组</button>
              </div>
              <div className="asset-group-detail-head">
                <div className="asset-parent-picker">
                  <input aria-label="资产组名称" value={assetGroupNameDraft} onChange={(event) => setAssetGroupNameDraft(event.target.value)} />
                  <button className="primary small" disabled={busy || !assetGroupNameDraft.trim()} onClick={() => renameCustomGroup(selectedCustomGroup)}>保存组名</button>
                </div>
                <p>组名只用于管理与整组调用，不会修改资产名称、变量或提示词。</p>
              </div>
              <div className="panel-title">组内资产（{selectedCustomGroupGroups.length}）</div>
              <div className="asset-grid overview-grid">
                {selectedCustomGroupGroups.map((group) => renderAssetCard(
                  group,
                  () => selectAssetCard(group.items[0]),
                  () => removeAssetFromCustomGroup(selectedCustomGroup, group.items[0])
                ))}
              </div>
            </div>
          )}

          {assetView === "detail" && selectedAsset && (
            <div className="asset-detail sub-panel active">
              <div className="generation-layout">
                <section className="asset-source-panel">
                  <div className="asset-source-head">
                    <h2 className="asset-source-name">{variantName(selectedAsset)}</h2>
                    <span className="asset-source-meta">{assetDisplayVariable(selectedAsset)} · {Array.isArray(selectedAsset.payload?.出现集数) && selectedAsset.payload.出现集数.length ? `第 ${selectedAsset.payload.出现集数.join("、")} 集` : "当前集"}{selectedAssetCustomGroup ? ` · ${selectedAssetCustomGroup.name}` : ""}</span>
                    <button type="button" className="ghost small asset-rename-btn" disabled={busy} onClick={() => openAssetRename(selectedAsset)}>重命名</button>
                  </div>
                  <figure className="asset-source-figure">
                    <div className="asset-figure-frame">
                      {selectedAssetOriginalImage ? (
                        <img className="clickable-image" src={selectedAssetOriginalImage} alt="原片截图" onClick={() => openImage(selectedAssetOriginalImage, `${selectedAsset.name} 原片截图`)} />
                      ) : (
                        <div className="asset-empty-thumb asset-empty-large">暂无原片截图</div>
                      )}
                    </div>
                    <figcaption>原片截图</figcaption>
                  </figure>
                  <figure className="asset-source-figure">
                    <div className="asset-figure-frame">
                      {selectedAssetGeneratedImage ? (
                        <img className="clickable-image" src={selectedAssetGeneratedImage} alt="确认版本" onClick={() => openImage(selectedAssetGeneratedImage, `${variantName(selectedAsset)} 确认版本`)} />
                      ) : (
                        <div className="asset-empty-thumb asset-empty-large">确认版本待生成</div>
                      )}
                    </div>
                    <figcaption>确认版本</figcaption>
                  </figure>
                  <section className="asset-source-facts">
                    <div className="panel-title">源资产事实 <span>只读</span></div>
                    <p>{originalAssetDescription(selectedAsset)}</p>
                  </section>
                </section>
                <aside className="generation-panel asset-generation-studio">
                  <div className="studio-toolbar">
                    <div className="studio-title"><span>当前形象</span><b>{variantName(selectedAsset)}</b></div>
                    <div className="studio-actions">
                      <button
                        type="button"
                        className={selectedAssetFavoriteImageId && selectedAssetFavoriteImageId === selectedImageVersion?.id ? "ghost studio-favorite active" : "ghost studio-favorite"}
                        onClick={() => void toggleAssetFavoriteVersion().catch((error) => setMessage(error.message))}
                        disabled={busy || !selectedImageVersion}
                        title={selectedImageVersion ? (selectedAssetFavoriteImageId === selectedImageVersion.id ? "取消收藏当前版本" : "收藏当前预览版本") : "暂无可收藏的版本"}
                      >{selectedAssetFavoriteImageId && selectedAssetFavoriteImageId === selectedImageVersion?.id ? "★ 已收藏" : "收藏版本"}</button>
                      <button className="primary studio-confirm" onClick={() => confirmAsset(selectedAsset.id)} disabled={busy}>确认{assetTab}</button>
                    </div>
                  </div>
                  <div className="studio-preview-main">
                    {isGeneratingSelectedAsset ? (
                      <div className="studio-image-loading"><i></i><span>生成中</span></div>
                    ) : selectedAssetGeneratedImage ? (
                      <img className="studio-generated-image clickable-image" src={selectedAssetGeneratedImage} alt="当前生效资产图" onClick={() => openImage(selectedAssetGeneratedImage, `${variantName(selectedAsset)} 当前生效资产图`)} />
                    ) : (
                        <div className="asset-empty-thumb asset-empty-large">暂无生成图，生成或上传后在此预览</div>
                    )}
                    <button
                      type="button"
                      className="studio-upload-fab"
                      aria-label="上传本地图片作为一个历史版本"
                      title="上传本地图片作为一个历史版本"
                      onClick={() => assetImageInputRef.current?.click()}
                      disabled={busy || isGeneratingSelectedAsset}
                    >上传</button>
                  </div>
                  <div className="studio-current">
                    <span>历史版本</span>
                    <div className="asset-version-list">
                      {(selectedAsset.images ?? []).map((image, index) => {
                        const imageUrl = apiAssetUrl(image.file_url || image.payload?.source_url);
                        const isFavorite = selectedAssetFavoriteImageId === image.id;
                        return (
                          <button className={image.id === selectedImageVersion?.id ? "asset-version active" : "asset-version"} key={image.id} onClick={() => selectAssetImageVersion(selectedAsset.id, image.id)} disabled={busy}>
                            {imageUrl ? <img src={imageUrl} alt={`历史版本 ${index + 1}`} /> : <i></i>}
                            {isFavorite && <em className="asset-version-fav" aria-label="已收藏">★</em>}
                            <span>v{(selectedAsset.images?.length ?? 0) - index}</span>
                          </button>
                        );
                      })}
                      {!selectedAsset.images?.length && <em>暂无历史版本</em>}
                    </div>
                  </div>
                  <div className="agent-generate-card">
                    <div className="agent-card-head"><h3>Agent 生成{assetTab === "角色" ? "形象" : assetTab}</h3></div>
                    <div className="agent-prompt-box">
                      {selectedAsset.payload?.["目标画像状态"] === "待源事实核对" && (
                        <div className="asset-profile-pending" role="status">
                          源视觉字段仍有待核对：{(() => {
                            const profile = selectedAsset.payload?.localizedProfile as Record<string, unknown> | undefined;
                            const paths = (profile?.sourceReviewPaths as string[] | undefined) || (profile?.needsReview as string[] | undefined) || [];
                            return paths.join("、") || "源视觉字段";
                          })()}。这不会阻止生成，可直接按当前提示词生成或先编辑提示词。
                        </div>
                      )}
                      {selectedAsset.payload?.["目标画像状态"] === "待源事实核对" && assetPromptDraft.includes("源事实待核对") && (
                        <div className="asset-profile-pending" role="alert">
                          提示词保留了源事实状态说明；这不会阻止生成，可直接按当前提示词生成。
                        </div>
                      )}
                      {selectedAsset.payload?.["目标画像状态"] === "待本地化" && (
                        <div className="asset-profile-pending" role="status">
                          {((selectedAsset.payload?.localizedProfile as Record<string, unknown> | undefined)?.needsReview)
                            ? "群像不套用单人脸部画像；当前提示词已按源观察编译，可直接生成。"
                            : "目标画像仍待本地化；可编辑当前资产提示词，输入内容后即可生成。"}
                        </div>
                      )}
                      <textarea value={assetPromptDraft} onChange={(event) => {
                        assetPromptDraftRevision.current.set(selectedAsset.id, (assetPromptDraftRevision.current.get(selectedAsset.id) ?? 0) + 1);
                        setAssetPromptDraft(event.target.value);
                      }} onBlur={() => saveAssetPrompt(selectedAsset, assetPromptDraft).catch((error) => setMessage(error.message))} placeholder="当前资产未返回提示词" disabled={busy} />
                      <div className="asset-generation-options">
                        <label>
                          <span>模型</span>
                          <select value={assetImageModel} onChange={(event) => setAssetImageModel(event.target.value)} disabled={isGeneratingSelectedAsset}>
                            {ASSET_IMAGE_MODELS.map((model) => <option key={model.id} value={model.id}>{model.label}</option>)}
                          </select>
                        </label>
                        <label>
                          <span>张数</span>
                          <select value={assetImageCount} onChange={(event) => setAssetImageCount(Number(event.target.value))} disabled={isGeneratingSelectedAsset}>
                            {[1, 2, 3, 4].map((count) => <option key={count} value={count}>{count} 张</option>)}
                          </select>
                        </label>
                        <span className="asset-ratio-setting">比例 <b>16:9</b></span>
                        <div className="agent-prompt-actions">
                          {currentProject?.remake_mode === "asset_replacement" && <button className="ghost" onClick={async () => { const data = await request<{ asset: Asset }>(`/api/projects/${currentProject.id}/assets/${selectedAsset.id}/restore-original`, { method: "POST" }); setAssets((items) => items.map((item) => item.id === selectedAsset.id ? data.asset : item)); setAssetPromptDraft(assetPrompt(data.asset)); setMessage("已设为沿用原貌；受影响分镜待更新"); }}>沿用原貌</button>}
                          <input ref={assetImageInputRef} hidden type="file" accept="image/png,image/jpeg,image/webp" onChange={(event) => { const file = event.currentTarget.files?.[0]; void uploadTargetAssetImage(file).catch((error) => setMessage(error.message)); event.currentTarget.value = ""; }} />
                          <button className="primary agent-cost" onClick={() => generateAssetImage(selectedAsset, assetPromptDraft).catch((error) => setMessage(error.message))} disabled={generatingAssetIds.has(selectedAsset.id) || selectedAsset.payload?.generationInProgress === true || !assetPromptDraft.trim()}>生成资产图</button>
                        </div>
                      </div>
                    </div>
                  </div>
                </aside>
              </div>
            </div>
          )}

          {assetGroupModal && (
            <div className="asset-group-modal-backdrop" role="presentation">
              <div className="asset-group-modal" role="dialog" aria-modal="true" aria-label={assetGroupModal === "create" ? "新建资产组" : "加入已有分组"}>
                <h3>{assetGroupModal === "create" ? "新建资产组" : "加入已有分组"}</h3>
                {assetGroupModal === "create" ? (
                  <>
                    <label>组名<input value={assetGroupNameDraft} onChange={(event) => setAssetGroupNameDraft(event.target.value)} placeholder="例如：篮球队员、主角团" /></label>
                    <p>将创建一个自定义组，包含当前选择的 {selectedAssetGroupKeys.size} 个同类资产。</p>
                    <div className="asset-group-modal-actions">
                      <button className="ghost" onClick={() => setAssetGroupModal(null)}>取消</button>
                      <button className="primary" disabled={busy || !assetGroupNameDraft.trim()} onClick={() => void createAssetGroupFromSelection()}>创建分组</button>
                    </div>
                  </>
                ) : (
                  <>
                    <label>目标分组<select value={assetGroupTargetId} onChange={(event) => setAssetGroupTargetId(event.target.value)}>{customAssetGroups.filter((group) => group.assetType === assetGroups.find((item) => selectedAssetGroupKeys.has(item.key))?.type).map((group) => <option value={group.id} key={group.id}>{group.name}（{group.members.length}）</option>)}</select></label>
                    <p>选中的资产会移动到目标组；如果之前属于其他组，会自动移出旧组。</p>
                    <div className="asset-group-modal-actions">
                      <button className="ghost" onClick={() => setAssetGroupModal(null)}>取消</button>
                      <button className="primary" disabled={busy || !assetGroupTargetId} onClick={() => void moveSelectionToCustomGroup()}>移动到分组</button>
                    </div>
                  </>
                )}
              </div>
            </div>
          )}

          {assetRenameTarget && (
            <div className="asset-group-modal-backdrop" role="presentation">
              <div className="asset-group-modal" role="dialog" aria-modal="true" aria-label="重命名资产">
                <h3>重命名资产</h3>
                <label>资产名称
                  <input
                    autoFocus
                    value={assetRenameDraft}
                    onChange={(event) => setAssetRenameDraft(event.target.value)}
                    onKeyDown={(event) => { if (event.key === "Enter" && assetRenameDraft.trim() && !busy) void submitAssetRename(); }}
                    placeholder="例如：啦啦队前排队员"
                  />
                </label>
                <p>名称会同步作为资产变量与卡片标题；原片描述和提示词不受影响。</p>
                <div className="asset-group-modal-actions">
                  <button className="ghost" onClick={() => setAssetRenameTarget(null)} disabled={busy}>取消</button>
                  <button className="primary" onClick={() => void submitAssetRename()} disabled={busy || !assetRenameDraft.trim()}>保存</button>
                </div>
              </div>
            </div>
          )}

          {assetDeleteTarget && (
            <div className="asset-group-modal-backdrop" role="presentation">
              <div className="asset-group-modal asset-delete-modal" role="dialog" aria-modal="true" aria-label="确认删除资产">
                <h3>确认删除 {assetDeleteTarget.labels.length} 个资产？</h3>
                <p>删除后资产记录与生成图版本会从项目中移除，引用这些资产的分镜将被标记为待更新。此操作不可撤销。</p>
                <ul className="asset-delete-list">
                  {assetDeleteTarget.labels.slice(0, 8).map((label) => <li key={label}>{label}</li>)}
                  {assetDeleteTarget.labels.length > 8 && <li>…等共 {assetDeleteTarget.labels.length} 项</li>}
                </ul>
                <div className="asset-group-modal-actions">
                  <button className="ghost" onClick={() => setAssetDeleteTarget(null)} disabled={busy}>取消</button>
                  <button className="asset-danger solid" onClick={() => void confirmBatchDelete()} disabled={busy}>确认删除</button>
                </div>
              </div>
            </div>
          )}

        </section>

        <section ref={workbenchRef} style={workbenchStyle} className={`screen workbench${screen === "segments" ? " active" : ""}${assetSidebarCollapsed ? " assets-collapsed" : ""}${previewPanelCollapsed ? " preview-collapsed" : ""}`} id="segments">
          {assetSidebarCollapsed && <button type="button" className="pane-edge-toggle asset-edge-toggle" onClick={() => setAssetSidebarCollapsed(false)}>展开资产库</button>}
          {previewPanelCollapsed && <button type="button" className="pane-edge-toggle preview-edge-toggle" onClick={() => setPreviewPanelCollapsed(false)}>展开原片</button>}
          <section className={`asset-sidebar${assetSidebarCollapsed ? " folded" : ""}`}>
            <div className="storyboard-asset-controls">
              <div className="storyboard-library-head">
                <b>项目资产库</b>
                <div className="storyboard-library-actions">
                  <button type="button" aria-label="刷新资产库" title="刷新资产库" disabled={busy} onClick={() => { if (currentProject) { void loadAssets(currentProject.id); void loadCustomAssetGroups(currentProject.id); } }}>↻</button>
                  <button type="button" aria-label="搜索资产" title="搜索资产" onClick={() => document.querySelector<HTMLInputElement>(".storyboard-asset-search")?.focus()}>⌕</button>
                  <button type="button" aria-label="新建资产" title="新建空白资产" disabled={busy} onClick={() => {
                    if (storyboardAssetTab === "视频帧" || storyboardAssetTab === "本地素材") { setMessage("当前标签页暂不支持新建资产"); return; }
                    void createBlankAsset(storyboardAssetTab);
                  }}>＋</button>
                </div>
              </div>
              <div className="storyboard-asset-tabs" role="tablist" aria-label="分镜资产类别">
                {(["角色", "场景", "道具", "视频帧", "本地素材"] as const).map((type) => (
                  <button type="button" role="tab" aria-selected={storyboardAssetTab === type} className={storyboardAssetTab === type ? "active" : ""} key={type} onClick={() => setStoryboardAssetTab(type)}>
                    {type}
                  </button>
                ))}
              </div>
              <input className="storyboard-asset-search" placeholder={`搜索${storyboardAssetTab}`} value={storyboardAssetSearch} onChange={(event) => setStoryboardAssetSearch(event.target.value)} />
              {(storyboardAssetTab === "角色" || storyboardAssetTab === "场景" || storyboardAssetTab === "道具") && (
                <>
                  <select className="storyboard-group-filter" aria-label="按资产组筛选" value={storyboardAssetGroupId} onChange={(event) => setStoryboardAssetGroupId(event.target.value)}>
                    <option value="all">全部资产</option>
                    <option value="ungrouped">未分组</option>
                    {storyboardCustomGroups.map((group) => <option value={group.id} key={group.id}>{group.name}</option>)}
                  </select>
                  {storyboardAssetGroupId !== "all" && storyboardAssetGroupId !== "ungrouped" && customGroupById.get(storyboardAssetGroupId) && (
                    <button className="primary small storyboard-family-bind" disabled={!selectedStoryboard || busy} onClick={() => {
                      const group = customGroupById.get(storyboardAssetGroupId);
                      if (group) void bindStoryboardCustomGroup(group);
                    }}>整组调用（{customGroupById.get(storyboardAssetGroupId)?.members.length ?? 0}）</button>
                  )}
                </>
              )}
              <div className="storyboard-asset-view-controls"><span>资产库</span><div><button className={storyboardAssetLayout === "list" ? "active" : ""} aria-pressed={storyboardAssetLayout === "list"} onClick={() => setStoryboardAssetLayout("list")}>列表</button><button className={storyboardAssetLayout === "grid" ? "active" : ""} aria-pressed={storyboardAssetLayout === "grid"} onClick={() => setStoryboardAssetLayout("grid")}>网格</button></div></div>
            </div>
            <div className={`storyboard-asset-list ${storyboardAssetLayout}`} role="tabpanel" aria-label={`${storyboardAssetTab}资产`}>
              {(storyboardAssetTab === "视频帧" || storyboardAssetTab === "本地素材") ? (
                <p className="empty-shot-assets">{storyboardAssetTab === "视频帧" ? "暂无视频帧；原片解析后这里会展示可引用的关键帧。" : "暂无本地素材；可在素材上传区导入图片素材。"}</p>
              ) : (
                <>
                  {storyboardFilteredAssets.map((asset) => {
                    const thumbUrl = selectedAssetImageUrl(asset) || originalAssetImageUrl(asset);
                    const isReferenced = selectedStoryboardAssets.some((item) => item.id === asset.id);
                    return <button className={isReferenced ? "storyboard-asset-row referenced" : "storyboard-asset-row"} key={asset.id} disabled={!selectedStoryboard} onClick={() => toggleStoryboardAsset(asset)}>
                      {thumbUrl ? <img src={thumbUrl} alt="" /> : <span className="asset-empty-thumb">—</span>}<span><b>{assetDisplayName(asset)}</b><small>{assetDisplayVariable(asset)}</small></span><i>{isReferenced ? "已引用" : "引用"}</i>
                    </button>;
                  })}
                  {!storyboardFilteredAssets.length && <p className="empty-shot-assets">{assets.some((asset) => asset.asset_type === storyboardAssetTab) ? "没有匹配的资产。" : `当前项目还没有${storyboardAssetTab}资产。`}</p>}
                </>
              )}
            </div>
          </section>
          <button type="button" className="workbench-splitter asset-pane-splitter" role="separator" aria-label="调整资产栏宽度" aria-orientation="vertical" aria-valuemin={230} aria-valuemax={getWorkbenchPaneMaxWidth("assets")} aria-valuenow={assetPaneWidth} tabIndex={0} onPointerDown={(event) => startWorkbenchResize("assets", event)} onPointerMove={moveWorkbenchResize} onPointerUp={endWorkbenchResize} onPointerCancel={endWorkbenchResize} onLostPointerCapture={() => endWorkbenchResize()} onKeyDown={(event) => handleWorkbenchResizeKey("assets", event)} />
          <section className="shot-main">
            {!assetSidebarCollapsed && <button type="button" className="pane-edge-toggle asset-edge-toggle" onClick={() => setAssetSidebarCollapsed(true)}>‹ 收起资产库</button>}
            {!previewPanelCollapsed && <button type="button" className="pane-edge-toggle preview-edge-toggle" onClick={() => setPreviewPanelCollapsed(true)}>收起原片 ›</button>}
            <div className="shot-head">
              <div className="storyboard-heading">
                <h2>{selectedStoryboard ? `片段 ${storyboards.findIndex((item) => item.id === selectedStoryboard.id) + 1} · ${selectedStoryboard.title}` : "暂无分镜"} <span>{selectedStoryboard?.time_range ?? "等待自动切分"}</span></h2>
                <p className="shot-hint">输入"@"可快速调整相关内容、引用角色、场景、素材</p>
              </div>
            </div>
            {segmentPanel === "shot-prompt" && !selectedStoryboard && (
              selectedEpisode && !selectedEpisode.file_url ? (
                <div className="shot-empty sub-panel active episode-blank-guide">
                  <b>第 {selectedEpisode.episode_index} 集 · 空白分集</b>
                  <span>还没有本集原片。上传后系统将自动解析并生成分镜；也可以先在空白集里手动搭建分镜结构。</span>
                  <div className="episode-blank-actions">
                    <button className="primary" disabled={busy} onClick={() => blankEpisodeUploadRef.current?.click()}>上传本集原片</button>
                    <button className="ghost" disabled={busy} onClick={() => void createStoryboard()}>从空白分镜开始</button>
                  </div>
                  <input ref={blankEpisodeUploadRef} hidden type="file" accept="video/*,.mkv,.webm" onChange={(event) => { const file = event.currentTarget.files?.[0]; event.currentTarget.value = ""; if (file) void uploadEpisodeSource(file); }} />
                </div>
              ) : storyboardLoading ? (
                <div className="shot-empty sub-panel active">
                  <div className="loader-ring"></div>
                  <b>正在生成分镜</b>
                  <span>系统正在调用 Agent 解析视频并生成分镜提示词，请稍候...</span>
                </div>
              ) : (
                <div className="shot-empty sub-panel active">
                  <b>当前项目还没有分镜</b>
                  <span>点击"进入分镜"后系统会根据资产出现片段自动生成；生成完成后这里会显示可编辑提示词。</span>
                </div>
              )
            )}
            {segmentPanel === "shot-prompt" && selectedStoryboard && (
              <div className="shot-text sub-panel active">
                <div className="storyboard-frame-slots" aria-label="首尾帧参考图">
                  {(["首帧", "尾帧"] as const).map((kind, index) => {
                    const ref = frameReferenceOf(selectedStoryboard, kind);
                    return (
                      <Fragment key={kind}>
                        {index === 1 && <span className="frame-slot-arrow" aria-hidden="true">⇄</span>}
                        <div className="frame-slot-wrap">
                          <button type="button" className={ref?.image ? "frame-slot filled" : "frame-slot"} disabled={busy} title={`${kind}参考图 · 点击选择资产`} onClick={() => setFramePicker({ kind })}>
                            {ref?.image ? <img src={ref.image} alt={`${kind}参考图`} /> : <span className="frame-slot-empty">＋ {kind}</span>}
                          </button>
                          {ref && (
                            <button type="button" className="frame-slot-clear" aria-label={`移除${kind}参考`} disabled={busy} onClick={(event) => { event.stopPropagation(); void saveFrameReference(kind, null); }}>×</button>
                          )}
                        </div>
                      </Fragment>
                    );
                  })}
                </div>
                <div className="prompt-body">
                  {selectedStoryboard.payload?.["使用回退占位"] === true && <div className={`storyboard-analysis-state ${selectedStoryboard.analysis_state === "分析失败" ? "failed" : "pending"}`} role="status">
                    <div><strong>{selectedStoryboard.analysis_state ?? "待分析"}</strong><span>{selectedStoryboard.analysis_message ?? "当前分镜没有真实逐镜分析结果；占位内容不会作为提示词或视频生成输入。"}</span></div>
                    <button className="ghost small" disabled={busy} onClick={() => void enterStoryboards(true)}>重新分析当前视频</button>
                  </div>}
                  {storyboardHasUnresolvedReferences && <div className="asset-profile-pending" role="alert">
                    <strong>资产引用待核对，已阻止生成。</strong>
                    <span>请在分镜正文中把歧义占位改成唯一资产变量后保存。</span>
                    <ul>{storyboardReferenceReview.map((item, index) => <li key={`${String(item.token || "reference")}-${index}`}>
                      <code>{String(item.token || "未知引用")}</code>：{String(item.reason || "无法唯一关联资产")}
                      {Array.isArray(item.candidateVariables) && item.candidateVariables.length > 0 ? `（候选：${item.candidateVariables.map(String).join("、")}）` : ""}
                    </li>)}</ul>
                  </div>}
                  <div className="tool-row"><button onClick={() => setSegmentPanel("shot-assets")}>引用</button><span className={`storyboard-save-state ${storyboardSaveState}`}>{storyboardSaveState === "saved" ? "分镜正文已保存" : storyboardSaveState === "saving" ? "正在保存分镜正文…" : "保存失败，草稿已保留"}</span>{storyboardSaveState === "failed" && <button className="ghost small" onClick={() => selectedStoryboard && promptInputRef.current && void savePrompt(selectedStoryboard, readPromptEditor(promptInputRef.current))}>重试保存</button>}<span>仅编辑正文；项目语言与画面约束仍由系统编译。</span></div>
                  <div className={selectedStoryboardReferenceItems.length ? "storyboard-ref-chips" : "storyboard-ref-chips is-empty"}>{selectedStoryboardReferenceItems.map((reference) => {
                    const thumbUrl = reference.asset ? selectedAssetImageUrl(reference.asset) || originalAssetImageUrl(reference.asset) : "";
                    return <button className="storyboard-ref-thumb" key={reference.key} type="button" title={`${reference.label} · 点击移除引用`} aria-label={`移除资产引用：${reference.label}`} onClick={() => void removeStoryboardReference(reference.token, reference.asset)}>
                      <span className="storyboard-ref-thumb-visual">{thumbUrl ? <img key={thumbUrl} src={thumbUrl} alt="" onError={(event) => { event.currentTarget.hidden = true; }} /> : <span aria-hidden="true">　</span>}</span>
                      <span className="storyboard-ref-thumb-remove" aria-hidden="true">×</span>
                    </button>;
                  })}{!selectedStoryboardReferenceItems.length && <span>当前分镜尚未关联资产；可从左侧资产库添加。</span>}</div>
                  <div className="prompt-editor-wrap">
                    <div ref={promptInputRef} className="prompt-rich-editor" role="textbox" aria-label="编辑当前分镜正文" aria-multiline="true" contentEditable={!busy} suppressContentEditableWarning data-placeholder={selectedStoryboard.payload?.["使用回退占位"] === true ? "请填写可核实的具体镜头事实；保存后即可作为手工分镜使用。" : "输入分镜正文；输入 @ 可插入资产变量"} onInput={handlePromptInput} onPaste={handlePromptPaste} onCopy={handlePromptCopy} onCompositionStart={() => { promptCompositionActive.current = true; }} onCompositionEnd={handlePromptCompositionEnd} onKeyDown={handlePromptMentionKeys} onBlur={(event) => {
                      const value = readPromptEditor(event.currentTarget);
                      if (selectedStoryboard) {
                        renderPromptEditor(event.currentTarget, value, storyboardScopeAssets, openImage);
                        promptEditorSync.current = { storyboardId: selectedStoryboard.id, value, assets: storyboardScopeAssets };
                      }
                      if (selectedStoryboard && (
                        value !== storyboardSavedBodies.current.get(selectedStoryboard.id)
                        || storyboardSaveStates[selectedStoryboard.id] === "failed"
                      )) void savePrompt(selectedStoryboard, value);
                      setMentionQuery(null);
                      setMentionRange(null);
                    }} />
                    {mentionQuery !== null && mentionRange !== null && (
                      <div className="mention-menu" role="listbox" aria-label="选择要插入的资产" style={mentionMenuPosition}>
                        <b>选择资产 · ↑↓ 导航 · Enter 插入 · Esc 关闭</b>
                        {mentionOptions.map((asset, index) => {
                          const thumbUrl = selectedAssetImageUrl(asset) || originalAssetImageUrl(asset);
                          return <button key={asset.id} data-mention-index={index} role="option" className={index === activeMentionIndex ? "active" : ""} aria-selected={index === activeMentionIndex} onMouseDown={(event) => {
                            event.preventDefault();
                            insertMention(asset);
                          }}>
                            <span className={`mention-thumb ${assetThumbClass(asset)}`}>{thumbUrl ? <img src={thumbUrl} alt="" onError={(event) => { event.currentTarget.hidden = true; }} /> : <span aria-hidden="true">　</span>}</span>
                            <span><strong>{assetDisplayName(asset)}</strong><small>{asset.asset_type} · {assetDisplayVariable(asset)}</small></span>
                          </button>;
                        })}
                        {!mentionOptions.length && <em>当前集无匹配资产</em>}
                      </div>
                    )}
                  </div>
                </div>
                <div className="video-generate-bar">
                  <div className="video-generate-options">
                    <label>
                      <span className="sr-only">视频模型</span>
                      <select value={videoModel} onChange={(event) => setVideoModel(event.target.value)} disabled={busy} aria-label="视频模型">
                        {VIDEO_MODELS.map((model) => <option key={model.id} value={model.id}>{model.label}</option>)}
                      </select>
                    </label>
                    <label>
                      <span className="sr-only">参考模式</span>
                      <select value={videoGenMode} onChange={(event) => setVideoGenMode(event.target.value)} disabled={busy} aria-label="参考模式">
                        {["全能参考", "仅首帧参考", "首尾帧参考"].map((mode) => <option key={mode} value={mode}>{mode}</option>)}
                      </select>
                    </label>
                    <label>
                      <span className="sr-only">画幅</span>
                      <select value={frameRatio} onChange={(event) => setFrameRatio(event.target.value)} disabled={busy} aria-label="画幅">
                        {["9:16", "16:9"].map((ratio) => <option key={ratio} value={ratio}>{ratio}</option>)}
                      </select>
                    </label>
                    <label>
                      <span className="sr-only">清晰度</span>
                      <select value={resolution} onChange={(event) => setResolution(event.target.value)} disabled={busy} aria-label="清晰度">
                        {["720p", "1080p"].map((item) => <option key={item} value={item}>{item}</option>)}
                      </select>
                    </label>
                    <label>
                      <span className="sr-only">时长</span>
                      <select value={videoDuration} onChange={(event) => setVideoDuration(Number(event.target.value))} disabled={busy} aria-label="时长">
                        {[4, 6, 8].map((seconds) => <option key={seconds} value={seconds}>{seconds} 秒</option>)}
                      </select>
                    </label>
                  </div>
                  <button className="primary video-generate-submit" disabled={busy || !selectedStoryboard || selectedStoryboard.payload?.["使用回退占位"] === true || storyboardHasUnresolvedReferences} title={storyboardHasUnresolvedReferences ? "请先完成资产引用核对" : undefined} onClick={() => {
                    // 首尾帧模式的前置校验：缺帧图时提示但仍允许按全能参考生成
                    if (videoGenMode !== "全能参考" && selectedStoryboard) {
                      const missing: string[] = [];
                      if ((videoGenMode === "仅首帧参考" || videoGenMode === "首尾帧参考") && !frameReferenceOf(selectedStoryboard, "首帧")?.image) missing.push("首帧");
                      if (videoGenMode === "首尾帧参考" && !frameReferenceOf(selectedStoryboard, "尾帧")?.image) missing.push("尾帧");
                      if (missing.length) setMessage(`「${videoGenMode}」未配置${missing.join("、")}参考图，本次仍按全能参考生成`);
                    }
                    runVideoGeneration();
                  }}><i className="credit-mark" aria-hidden="true">✦ 2.0</i> 生成视频</button>
                </div>
              </div>
            )}
            {segmentPanel === "shot-assets" && <div className="sub-panel simple-panel active">{selectedStoryboardAssets.map((asset) => <span className="chip" key={asset.id}>{assetDisplayVariable(asset)}</span>)}<button className="ghost full" onClick={() => setSegmentPanel("shot-prompt")}>返回提示词</button></div>}
          </section>
          <aside className={`video-preview${previewPanelCollapsed ? " folded" : ""}`}>
            <div className="preview-tabs"><button className={previewMode === "episode" ? "active" : ""} onClick={() => { setSegmentPanel("shot-prompt"); setPreviewMode("episode"); }}>分镜生成</button><button className={previewMode === "segments" ? "active" : ""} onClick={() => { setSegmentPanel("shot-prompt"); setPreviewMode("segments"); }}>原片对比</button></div>
            <div className="preview-content">
              {previewMode === "segments" ? (
                selectedEpisode?.file_url && selectedStoryboard ? (
                  <OriginalClipPreview
                    sourceUrl={apiAssetUrl(selectedEpisode.file_url)}
                    storyboard={selectedStoryboard}
                  />
                ) : (
                  <div className="video-not-ready">选择分镜后可查看对应原片片段。</div>
                )
              ) : (
                <>
                  <div className="storyboard-gen-preview">
                    {activeGeneration?.output_url ? (
                      <video key={activeGeneration.id} src={apiAssetUrl(activeGeneration.output_url)} controls playsInline loop muted autoPlay />
                    ) : (
                      <div className="asset-empty-thumb asset-empty-large">尚未生成视频；在分镜正文下方点击「生成」创建第一个版本；原片可切到「原片对比」查看</div>
                    )}
                    <span className="preview-res-badge">{resolution}</span>
                    <div className="preview-corner-actions">
                      <button type="button" aria-label="画面裁切（规划中）" title="画面裁切（规划中）" onClick={() => setMessage("画面裁切功能规划中")}>◱</button>
                      <button type="button" aria-label="生成参数信息" title="生成参数信息" onClick={() => setMessage(`当前参数：${videoModel} · ${frameRatio} · ${resolution} · ${videoDuration} 秒 · ${videoGenMode}`)}>ⓘ</button>
                    </div>
                  </div>
                  <div className="storyboard-gen-versions">
                    <span>历史版本</span>
                    <div className="storyboard-gen-version-list">
                      {storyboardGenerations.map((gen, index) => (
                        <button type="button" className={gen.id === activeGeneration?.id ? "gen-version active" : "gen-version"} key={gen.id} onClick={() => void selectGenerationVersion(gen)} title={`采用并预览版本 v${storyboardGenerations.length - index} · ${gen.created_at}`}>
                          {gen.output_url
                            ? <video src={apiAssetUrl(gen.output_url)} preload="metadata" muted playsInline aria-hidden="true" />
                            : <i className="gen-version-thumb-empty"></i>}
                          <b>v{storyboardGenerations.length - index}</b>
                        </button>
                      ))}
                      {!storyboardGenerations.length && <em className="gen-version-empty">暂无版本，生成后在此管理</em>}
                    </div>
                  </div>
                </>
              )}
            </div>
          </aside>
          <button type="button" className="workbench-splitter preview-pane-splitter" role="separator" aria-label="调整预览栏宽度" aria-orientation="vertical" aria-valuemin={260} aria-valuemax={getWorkbenchPaneMaxWidth("preview")} aria-valuenow={previewPaneWidth} tabIndex={0} onPointerDown={(event) => startWorkbenchResize("preview", event)} onPointerMove={moveWorkbenchResize} onPointerUp={endWorkbenchResize} onPointerCancel={endWorkbenchResize} onLostPointerCapture={() => endWorkbenchResize()} onKeyDown={(event) => handleWorkbenchResizeKey("preview", event)} />
          <section className="shot-strip">
            <button type="button" className="shot-strip-nav prev" aria-label="向左翻页" onClick={(event) => { const row = event.currentTarget.parentElement?.querySelector(".thumb-row"); row?.scrollBy({ left: -320, behavior: "smooth" }); }}>‹</button>
            <div className="thumb-row">
              {storyboards.map((storyboard, index) => {
                const versionCount = generations.filter((gen) => gen.storyboard_id === storyboard.id).length;
                const durationSeconds = storyboardDurationSeconds(storyboard);
                return (
                  <div className="shot-thumb-wrap" key={storyboard.id}>
                    <StoryboardFrameThumb storyboard={storyboard} sourceUrl={selectedEpisode?.file_url ? apiAssetUrl(selectedEpisode.file_url) : undefined} selected={storyboard.id === selectedStoryboard?.id} onClick={() => void selectStoryboardWithSave(storyboard)} />
                    <span className="shot-card-index">{index + 1}</span>
                    <span className={versionCount ? "shot-card-state done" : "shot-card-state pending"}>{versionCount ? "已生成" : "未完成"}</span>
                    <div className="shot-card-meta">
                      {durationSeconds !== null && <span>{durationSeconds}s</span>}
                      <span>{versionCount} 个版本</span>
                    </div>
                    <button
                      type="button"
                      className="shot-card-menu-trigger"
                      aria-label={`${storyboard.title}操作`}
                      aria-haspopup="menu"
                      aria-expanded={shotMenu?.id === storyboard.id}
                      onClick={(event) => {
                        event.stopPropagation();
                        if (shotMenu?.id === storyboard.id) {
                          setShotMenu(null);
                          return;
                        }
                        const rect = event.currentTarget.getBoundingClientRect();
                        setShotMenu({ id: storyboard.id, x: rect.right, y: rect.bottom + 6 });
                      }}
                    >⋯</button>
                    {shotMenu?.id === storyboard.id && (
                      <div className="shot-card-menu" role="menu" style={{ left: shotMenu.x, top: shotMenu.y }} onClick={(event) => event.stopPropagation()}>
                        <button type="button" role="menuitem" disabled={index === 0} onClick={() => { setShotMenu(null); void moveStoryboard(storyboard, "up"); }}>上移</button>
                        <button type="button" role="menuitem" disabled={index === storyboards.length - 1} onClick={() => { setShotMenu(null); void moveStoryboard(storyboard, "down"); }}>下移</button>
                        <button type="button" role="menuitem" className="danger" onClick={() => { setShotMenu(null); setDeletingStoryboard(storyboard); }}>删除</button>
                      </div>
                    )}
                    <button className="insert-shot" aria-label={`在${storyboard.title}后新增`} onClick={() => createStoryboard(storyboard)}>+</button>
                  </div>
                );
              })}
              <button className="add-shot-card" onClick={() => createStoryboard()}>+ 新增分镜</button>
            </div>
            <button type="button" className="shot-strip-nav next" aria-label="向右翻页" onClick={(event) => { const row = event.currentTarget.parentElement?.querySelector(".thumb-row"); row?.scrollBy({ left: 320, behavior: "smooth" }); }}>›</button>
          </section>
        </section>

        <section className={screen === "compare" ? "screen active video-page" : "screen video-page"} id="compare">
          <VideoComposition projectId={currentProject?.id} episodeId={selectedEpisodeId ?? undefined} episodeIndex={selectedEpisode?.episode_index} comparisonItems={videoComparisonItems} active={screen === "compare"} onMessage={setMessage} />
        </section>
      </main>

      {framePicker && (
        <div className="asset-group-modal-backdrop" role="presentation" onClick={() => setFramePicker(null)}>
          <div className="asset-group-modal frame-picker" role="dialog" aria-modal="true" aria-label={`选择${framePicker.kind}参考图`} onClick={(event) => event.stopPropagation()}>
            <h3>选择{framePicker.kind}参考图</h3>
            <div className="storyboard-asset-tabs frame-picker-tabs" role="tablist">
              {(["角色", "场景", "道具"] as const).map((type) => (
                <button type="button" role="tab" aria-selected={framePickerTab === type} className={framePickerTab === type ? "active" : ""} key={type} onClick={() => setFramePickerTab(type)}>
                  {type}<span>{assets.filter((asset) => asset.asset_type === type).length}</span>
                </button>
              ))}
            </div>
            <div className="frame-picker-grid">
              {assets.filter((asset) => asset.asset_type === framePickerTab).map((asset) => {
                const thumbUrl = selectedAssetImageUrl(asset) || originalAssetImageUrl(asset);
                return (
                  <button type="button" className="frame-picker-item" key={asset.id} disabled={busy} onClick={() => void saveFrameReference(framePicker.kind, asset)}>
                    {thumbUrl ? <img src={thumbUrl} alt="" /> : <span className="asset-empty-thumb">—</span>}
                    <small>{assetDisplayName(asset)}</small>
                  </button>
                );
              })}
              {!assets.some((asset) => asset.asset_type === framePickerTab) && <p className="empty-shot-assets">当前项目还没有{framePickerTab}资产。</p>}
            </div>
            <div className="asset-group-modal-actions">
              <button className="ghost" onClick={() => setFramePicker(null)} disabled={busy}>取消</button>
              <button className="ghost asset-danger" onClick={() => void saveFrameReference(framePicker.kind, null)} disabled={busy || !frameReferenceOf(selectedStoryboard, framePicker.kind)}>清除{framePicker.kind}参考</button>
            </div>
          </div>
        </div>
      )}

      {voiceEditor && (
        <div className="modal-overlay open" aria-hidden="false" onClick={(event) => {
          if (event.currentTarget === event.target && !busy) closeVoiceConfiguration();
        }}>
          <section className="upload-confirm-dialog voice-config-dialog" role="dialog" aria-modal="true" aria-labelledby="voice-config-title">
            <div className="modal-titlebar light"><h3 id="voice-config-title">音色配置 · {voiceEditor.roleName}</h3><button type="button" aria-label="关闭音色配置" disabled={busy} onClick={closeVoiceConfiguration}>×</button></div>
            <div className="confirm-body voice-config-body">
              <div className="voice-config-section">
                <b>参考音频</b>
                {voiceEditor.config.referenceAudio && <i className="voice-ref-status">参考音频已就绪</i>}
              </div>
              {voiceEditor.config.referenceAudio && (
                <div className="voice-ref-card">
                  <div className="voice-ref-main">
                    <div className="voice-ref-wave" aria-hidden="true">{[8, 16, 10, 20, 12, 6, 18, 10, 14, 6, 16, 8, 20, 10, 14, 6, 12, 18, 8, 4].map((height, index) => <i key={index} style={{ height: `${height}px` }} />)}</div>
                    <div className="voice-ref-meta">
                      <b>{voiceEditor.config.referenceAudio.fileName}</b>
                      <span>{voiceEditor.config.referenceAudio.size ? `${(voiceEditor.config.referenceAudio.size / 1024 / 1024).toFixed(1)} MB` : "已上传"}</span>
                    </div>
                  </div>
                </div>
              )}
              <label className={voiceUploading ? "voice-ref-upload uploading" : "voice-ref-upload"}>
                <input type="file" accept=".wav,.mp3,.m4a,audio/wav,audio/mpeg,audio/mp4" hidden disabled={voiceUploading} onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ""; if (file) void uploadVoiceReference(file); }} />
                <b>{voiceUploading ? "上传中…" : voiceEditor.config.referenceAudio ? "重新上传参考音频" : "点击选择参考音频"}</b>
                <span>wav / mp3 / m4a · 建议 3–30 秒 · 单人清晰人声，无背景音乐</span>
                <em>建议直接截取原剧角色台词片段，克隆效果最稳定。</em>
              </label>
              <div className="voice-preview-row">
                <button type="button" className="ghost" disabled={!voiceEditor.config.referenceAudio} onClick={toggleVoicePreview}>{voiceRefPlaying ? "停止试听" : "试听"}</button>
                <span>克隆试听将在配音服务接入后开放；当前试听播放参考音频，用于检查素材质量。</span>
              </div>
            </div>
            <div className="confirm-actions"><button type="button" className="ghost" disabled={busy} onClick={closeVoiceConfiguration}>取消</button><button type="button" className="primary" disabled={busy} onClick={() => void saveVoiceConfiguration()}>{busy ? "保存中…" : "保存音色配置"}</button></div>
          </section>
        </div>
      )}

      {deletingStoryboard && (
        <div className="modal-overlay open" aria-hidden="false" onClick={(event) => {
          if (event.currentTarget === event.target && !busy) setDeletingStoryboard(null);
        }}>
          <section className="upload-confirm-dialog storyboard-delete-dialog" role="dialog" aria-modal="true" aria-labelledby="delete-storyboard-title">
            <div className="modal-titlebar light"><h3 id="delete-storyboard-title">确认删除分镜？</h3><button type="button" aria-label="关闭删除确认" disabled={busy} onClick={() => setDeletingStoryboard(null)}>×</button></div>
            <div className="confirm-body">
              <div className="confirm-copy">
                <b>{deletingStoryboard.title}</b>
                <span>时间范围：{deletingStoryboard.time_range || "未设置"}。删除后，关联的生成记录也会一并移除，且无法恢复。</span>
              </div>
            </div>
            <div className="confirm-actions"><button type="button" className="ghost" disabled={busy} onClick={() => setDeletingStoryboard(null)}>取消</button><button type="button" className="primary shot-delete-confirm" disabled={busy} onClick={() => void deleteStoryboard(deletingStoryboard)}>{busy ? "删除中…" : "确认删除"}</button></div>
          </section>
        </div>
      )}

      {modal === "upload-confirm-modal" && (
        <div className="modal-overlay open" aria-hidden="false" onClick={(event) => event.currentTarget === event.target && setModal(null)}>
          <section className="upload-confirm-dialog" role="dialog" aria-modal="true">
            <div className="modal-titlebar light"><h3>确认上传参数</h3><button onClick={() => setModal(null)}>×</button></div>
            <div className="confirm-body">
              <div className="confirm-copy"><b>请确认本项目的重制目标和生成参数</b><span>确认后将进入原片上传进度页，上传完成后再选择分析方式。</span></div>
              <div className="confirm-grid">
                <article><span>视频来源</span><b>本地视频</b></article>
                <article><span>项目名称</span><b>{selectedSource?.title ?? currentProject?.title ?? "未选择项目"}</b></article>
                <article><span>剧集数量</span><b>{selectedSource?.uploadMode === "single" ? "1 集" : `${selectedFiles?.length || sourceEpisodeCount} 集`}</b></article>
                <article><span>画面类型</span><b>{displayStyleType}</b></article>
                <article><span>地区语言</span><b>{targetLanguage === "沿用原片语言" ? "国内（原片语言）" : `海外·${targetLanguage}`}</b></article>
                <article><span>画幅</span><b>{frameRatio}</b></article>
                <article><span>清晰度</span><b>{resolution}</b></article>
              </div>
            </div>
            <div className="confirm-actions"><button className="ghost" onClick={() => setModal(null)}>返回修改</button><button className="primary" onClick={() => uploadEpisodes()}>确认并开始上传</button></div>
          </section>
        </div>
      )}

      {modal === "image-view-modal" && imageView && (
        <div className="modal-overlay open" aria-hidden="false" onClick={(event) => event.currentTarget === event.target && setModal(null)}>
          <section className="image-view-dialog" role="dialog" aria-modal="true">
            <div className="modal-titlebar"><h3>{imageView.title}</h3><button onClick={() => setModal(null)}>×</button></div>
            <div className="image-view-body"><img src={imageView.url} alt={imageView.title} /></div>
          </section>
        </div>
      )}
    </>
  );
}
