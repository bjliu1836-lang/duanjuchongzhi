# 后端上云手册（中国香港节点 · 免备案）

目标：后端迁到腾讯云轻量中国香港节点，摆脱对本机开机状态的依赖；浏览器 API 直连香港服务器，上传不再绕境外代理。域名无 ICP 备案是选香港节点的直接原因——香港免备案，且国内访问延迟低（30~60ms）、到火山方舟北京区走跨境专线速度快。

## 1. 购机规格

- 产品：腾讯云轻量应用服务器（Lighthouse），地域**中国香港**
- 配置：**2 核 4G / 60GB SSD**（ffmpeg 转码有 CPU 突发，4G 内存留余量；现有数据 2.4GB）
- 镜像：选 **Docker CE** 应用镜像（省去装 Docker）
- 带宽：默认 30Mbps 峰值即可
- 价格参考：约 ¥60~100/月

> 防火墙：在轻量控制台放行 22（SSH）、80、443 端口。

## 2. DNS 切换（Cloudflare 控制台）

`api.0po3eu1.com` 当前是隧道的 CNAME 记录。上云后改为：

- 类型 `A`，名称 `api`，内容 `<服务器IP>`，**代理状态：仅 DNS（灰云）**
  - Caddy 需要用 80/443 直接签发证书，不能隔 Cloudflare 代理

隧道（cloudflared）和本地桥接任务在验收通过前**不要停**，作为回退路径。

## 3. 服务器初始化

```bash
ssh root@<服务器IP>
mkdir -p /root/workshop && cd /root/workshop
# 拉代码（仓库含 Dockerfile / deploy/）
git clone https://github.com/bjliu1836-lang/duanjuchongzhi.git .
cp deploy/.env.example deploy/.env
# 编辑 deploy/.env：填 ARK_API_KEY，确认 API_DOMAIN=api.0po3eu1.com
```

## 4. 数据迁移（本机执行）

先停本地桥接（保证 SQLite 一致快照）：

```powershell
Stop-ScheduledTask -TaskName "VideoRemakeWorkshop-Bridge"
powershell -File deploy\migrate-data.ps1 -ServerIp <服务器IP>
# 完成后本地桥接保持停止，避免新旧两端数据分叉
```

服务器上把数据装入 docker 卷：

```bash
cd /root/workshop
docker compose -f deploy/compose.server.yaml --env-file deploy/.env up -d --build
docker compose -f deploy/compose.server.yaml --env-file deploy/.env down
docker run --rm -v deploy_workshop-data:/to -v /root/workshop/data:/from alpine sh -c "cp -a /from/. /to/"
docker compose -f deploy/compose.server.yaml --env-file deploy/.env up -d
```

> 卷名前缀以实际为准（`docker volume ls` 查看，形如 `deploy_workshop-data` 或 `workshop_workshop-data`）。

## 5. 前端改为 API 直连并重新部署

```powershell
$env:VITE_API_BASE = "https://api.0po3eu1.com"
npm --prefix frontend run build
Remove-Item Env:VITE_API_BASE
npx wrangler@latest deploy --config cloudflare/wrangler.jsonc
```

页面仍由 Worker 托管（workers.dev，走代理加载页面）；所有 `/api/*` 与 `/api/files/*` 由浏览器直连香港服务器，绕开代理限速。

## 6. 验收标准

| 检查 | 通过标准 |
|---|---|
| `https://api.0po3eu1.com/api/health` | 返回 ok:true（浏览器直接打开） |
| `model-status` | ready:true |
| 上传 15MB 视频 | 前端可见进度、总耗时 ≤ 30s（对比旧链路 77s） |
| 整剧分析跑通 1 集 | series_analysis + asset_extraction 成功 |
| 原片/生成视频播放 | 视频 Range 加载正常 |

## 7. 回退方案

1. Cloudflare DNS：`api` 记录改回隧道 CNAME（或删除 A 记录恢复隧道）
2. 前端重新用 `VITE_API_BASE = "/"` 构建部署（走 Worker 代理回本机隧道）
3. 本机 `Start-ScheduledTask -TaskName "VideoRemakeWorkshop-Bridge"` 恢复本地链路

## 8. 善后（验收通过后）

- 停用本地桥接任务、cloudflared（可保留配置备查）
- Worker 的 `BACKEND_URL` / `BACKEND_SHARED_SECRET` 可保留作灾备，也可删除
- 服务器侧：配置快照/自动备份（轻量控制台支持定时快照）
