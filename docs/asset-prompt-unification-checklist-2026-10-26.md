# 资产提取与生图提示词统一执行清单

- [x] 统一实际资产提取系统提示词和详情补全字段；国内按中国/源语言提供目标参数，并保留源事实。
- [x] 国内、海外、3DCG共用角色/场景/道具提示词骨架；可选人工描述仅作独立视觉覆盖，不替代模板或累积固定约束。
- [x] 按画面风格选真人或CG固定约束，三类资产均有对应约束；保留沿用原貌、候选图采用和历史资产映射。
- [x] 增加国内/海外/CG × 三类资产、编辑幂等与沿用原貌回归断言；运行必要后端测试和前端构建。
- [x] 本地工作台切换至真实同库新后端 `127.0.0.1:8006`：仅更新被忽略的 `frontend/.env.local`，由 Vite `.env.local` watcher 原生重启；8003 保留运行，配置原值已备份至用户 Temp。
- [x] 新后端健康、34 项目、两条国内资产完整提示词 marker、CORS、5174 转译后的 App API_BASE=8006 均已核验；隔离 HTTP 角色生成/编辑/重复生成幂等通过，无外部模型调用或项目数据变更。
- [ ] 浏览器DOM交互 smoke 未运行：Playwright模块导入失败且Chrome启动被工具策略拒绝；不尝试绕过，已用实际HTTP链路验证生成/保存数据流。

## v3 独立生图描述完善（2026-10-26）

- [x] 活跃 compact 抽取 schema 覆盖15类角色事实及服装衣长/领袖/腰线/摆型/拖尾与遮鞋证据；观察、推断、目标设计补全区分，服装变体各自保持一套主视觉。
- [x] detail completion 将上述字段写入当前子资产；未知/needsReview不作为可用生图描述，兼容旧资产缺字段路径并在provider之前报告具体缺项。
- [x] 角色 prompt 分级编译普通垂摆、轻度拖地、高位典礼遮鞋；按证据选性别/时代/身份/场合规则，过滤手持与腰挂物，保留固定穿戴配饰。
- [x] 角色构图左正面面部特写 + 右正/90度侧/背三全身；真人/CG共享构图，风格约束互斥。
- [x] 场景/道具 prompt 编译成自包含的空间或物件描述；增加provider提交前的未解析指代、关键字段及风格/服装冲突检查。
- [x] 增加跨抽取->normalize->detail merge->最终prompt->provider捕获的数据流回归；含海外英语/中文内部描述、CG、源事实保留、用户编辑覆盖、子资产道具过滤、缺项422和provider前无任务记录等边界。`python -m pytest -q backend/tests`：25 passed，3个FastAPI `on_event`弃用警告；前端build已在此前前端改动后通过。
- [x] 独立临时库HTTP smoke已完成，无用户项目/资产写入、无调用模型；已用真实provider完成A、B、C、A2、C2五次上限内样本，参数2K、无自动重试，产物/脱敏prompt/记录保存于 `C:\Users\liubijie\.codex\visualizations\2026\10\26\01a17670-f474-7312-9125-4488ae1acdc1\asset-prompt-v3`。视觉结果：A1裸肩不通过、A2同款衣领通过；B日常真人通过；C1露鞋未通过、C2遮鞋通过。
- [x] 最新源码已加载至现有5174工作台：普通受管uvicorn会话 `session_id=32362` / PID 39996 在8007使用仓库工作目录和真实 `data/workshop.sqlite3`；health确认真实库、35个项目，启动前tasks/image jobs/composition exports均无活动项且所需列齐全。8003 PID 67104、8006 PID 32644、5174 PID 47832原进程保留。备份原env至 `C:\Users\liubijie\AppData\Local\Temp\frontend.env.local.asset-prompt-v3-f3ed7ada58d74083a82eaab6b32c93d0.bak` 后仅将ignored `frontend/.env.local` 唯一VITE_API_BASE由8006切至8007；Vite watcher原生重载，`/src/App.tsx`转换模块只含8007。5174→8007 CORS OPTIONS为200。真实国内项目 `project_24bc00bdd50a` GET 22资产无整页错误；8006与8007的22个asset ID完全一致（角色12/场景7/道具3），12个角色prompt均含左正面面部特写，22条均有最终prompt且无未解析指代/任务占位。
- [x] 资产图片请求单独关闭API水印，不修改全局 `config/models.json` 的 `imageWatermark: true`；provider捕获测试以全局配置true验证资产调用仍为false。C2无水印。

## 用户编辑完整资产提示词（2026-10-26）

- [x] 角色/场景/道具共用面板移除“目标外观补充”，改为直接编辑完整最终提示词；上传图/沿用原貌保留。
- [x] 手工完整提示词单独持久化，GET重载和切换资产可恢复；生成使用精确编辑文本，不再套模板；旧 `prompt` 视觉补充接口保持兼容。
- [x] 手稿只校验非空和未解析外部指代；沿用原貌清空手稿并仍阻止生图，编辑后可切回手动生成；相等自动稿不落手稿字段。
- [x] 针对手工稿 provider capture、保存重载、重复幂等、空白/指代拒绝、沿用原貌恢复增加回归；`python -m pytest -q backend/tests`：29 passed；`npm run build`通过。
- [x] 复核0活动任务/图片job/合成任务后，通过受管会话正常Ctrl+C停止本任务的旧8007并同端口托管重启新版；新session `11233` / PID `49184`。健康接口指向真实 `data/workshop.sqlite3`（36项目）；5174转译模块确认无旧补充输入、textarea可编辑且发送 `finalPrompt`。
- [ ] 未做真实DOM手势smoke：当前无Browser Plugin控制工具；未启动额外浏览器。API字段、生成/保存数据流由隔离TestClient验证。

## 分镜工作台布局与资产分类（2026-10-26）

- [x] 左侧资产栏新增角色/场景/道具tabs，保留搜索、数量、列表/网格；类别工具区固定，仅资产项滚动。
- [x] 工作台active/selection色统一紫色主题，保留语义成功/错误状态色。
- [x] 工作台占用header后的视口剩余高度；header、各面板工具区和底部分镜条保持可达，正文/资产列表有限滚动，底条可横向滚动。
- [x] 窄屏使用main作为唯一横向/纵向滚动viewport，工作台基础宽940px且资产/预览折叠时分别缩至700/640px、双折叠390px；内部保留响应式边距。短高底条136px并将卡片限制到48–64px，正文/预览内容自身受控滚动。`cd frontend && npm run build`（含`tsc -b`）通过，Vite 5174返回编译后的新tab/preview markup。未找到可用的既有浏览器控制运行时，未做真实DOM/滚轮手势验证；未启动新浏览器。

## 设定固定区与后续阶段浅色主题修正（2026-10-26）

- [x] 根因确认：实际frontend入口引入的 `frontend/src/styles.css` 在共享CSS之后覆盖 segments/compare 为深色；删除深色主题覆盖，保留工作区布局并切换到共享浅色变量，视频画面自身底色保留。
- [x] 设定页固定项目导航、标题统计、分类tab、资产panel标题/重提操作及底部返回/进入分镜；资产列表在panel内部滚动，detail内容在可达的内部区域滚动。
- [x] `frontend` 下 `npm run build`（含 `tsc -b`）通过。5174真实入口链已核对：根HTML加载 `/src/main.tsx`，转换模块导入 `/src/styles.css`，Vite返回CSS注入内容的文件来源为 `frontend/src/styles.css`，包含本轮设定固定布局及 compare 浅色规则。未找到可连接浏览器；没有做真实DOM视觉/滚动手势验证。

## 分镜可拖动分栏与引用缩略图（2026-10-27）

- [x] 左/右拖动分隔线、键盘调宽、折叠恢复与四种工作区列位/底条跨列；pointerup/cancel/lost capture/window blur与卸载均清理拖动状态。pane max按当前可见面板/分隔线数计算；940px下限默认宽度经同公式clamp。
- [x] 引用条按实际引用逐项显示目标图/原图/空占位缩略图；固定单行横向滚动（84px上限），`object-fit: contain`，保留hover名称和移除入口，图片加载失败回空位。
- [x] 自动推导引用首次编辑时物化为精确payload覆盖；移除后不由正文/时间索引复加；@选择器加入也更新覆盖。同步draft ref、PATCH队列串行和revision保护避免快速操作旧refs；生成handler等待当前PATCH队列；生成端只读取显式payload引用，未改后端。
- [x] `frontend` 下 `npm run build`（含`tsc -b`）通过。临时Node/TypeScript提取源码检查500/940/1100/1600/2560宽度初始值≤最大值、另一栏折叠不再扣隐藏面板、手动空override不派生引用、未知token可呈空图项及移除、快速A/B移除/添加PATCH持久化顺序。5174真实入口已跟随root HTML→`/src/main.tsx`→`/src/App.tsx`及`/src/styles.css`确认，转换App含最新队列等待与图片key；实际CSS来源`frontend/src/styles.css`并含共享CSS的分隔器/contain/nowrap样式。
- [ ] 未做真实DOM拖动/键盘/滚动手势smoke：无可用的既有浏览器控制连接；未启动浏览器。引用状态以代码路径复核，未新增专用自动化用例。

## 分镜正文变量缩略图（2026-10-27）

- [x] 阅读态正文保留完整原始`@变量`名称，并在名称后紧邻显示约1em图片；已知展示变量和原始变量按最长别名映射同一资产。仍使用目标图→原图优先级，无图/加载失败显示同尺寸占位且名称不丢失。
- [x] 有图缩略图可双击或键盘Enter/Space打开既有图片预览弹窗，显示资产名及完整contain图片；遮罩、关闭按钮和Escape可关闭。空占位不打开预览。
- [x] 原生编辑textarea保持原始文本、保存、复制/生成契约；只有阅读态进行HTML渲染，不将`img`标签写入prompt字段。边界稳定的未知中文/ASCII `@token`以空位显示、tooltip保留原token；普通正文、邮箱、标点和空白不吞。
- [x] 临时Node/TypeScript提取实际分词函数验证重叠变量最长匹配、同资产原始@变量与展示变量分别命中、已知变量紧跟中文正文、中文未知变量、邮箱及中文标点；parts按原token拼回后与输入逐字相同。`npm run build`（含`tsc -b`）及5174入口资源核验通过。无可用既有浏览器连接，本轮双击/键盘视觉交互未做真实DOM验证。

## 分镜反推 V1 七维契约（2026-10-27）

- [x] 活跃分镜Agent系统提示词按实际切镜建立片段说明+逐镜七维规则；明确观察视频与最终自包含描述边界、白模仅显式输入启用、成片内文字与后期字幕/水印区分、各模式保留源事实/仅海外按目标语言适配。
- [x] 服务端请求上下文标记实际素材分工；仅在episode实际带独立剧本/白模字段时传入，否则明确标注缺失，不覆盖视频输入或猜测存在剧本/白模。
- [x] normalize将输出根级片段说明复制进各分镜payload；结构化编译每个镜头各自处理、混合新旧/部分空字段时保留旧镜头描述，时间/小数时长和资产变量由原结构继续传递。
- [x] 有针对性的提示词契约、normalize→实际sanitize编译、混合旧字段、根级摘要只输出一次及1.5秒时间精度回归通过：2 passed。
- [x] `python -m pytest -q backend/tests`：33 passed，3个既有FastAPI `on_event`弃用警告；含显式回退marker的422无task/job、手工补全后mock生成及无marker旧记录兼容。本轮无前端改动，不需重复前端build。
- [x] 未调用付费视频分析、不改写现有项目分镜；本轮仅做代码/隔离测试验证。
- [x] 新版源码安全加载到8007：通过受管session 11233 Ctrl+C正常停止本任务旧PID49184，再用同Python/cwd/端口启动session28528 / PID70340；8003/8006/5174保持原样。health=200并指向真实 `data/workshop.sqlite3`（37项目）；现有storyboard GET=200；8006与8007项目/episode/storyboard共173个ID一致（0缺失、0新增）；tasks/image generations/composition exports活动数均0。
- [ ] 未调用完整真实视频分析模型；未验证模型实际生成质量。

## 目标外貌本地化与分镜正文 @资产编辑（2026-10-27）

- [x] 写清用户明确目标族裔/外貌/时代/场景优先级；源视频族裔事实不等于目标族裔锁定；国家不等于单一族裔；删除“美国=白人/排斥东亚面貌”等冲突范例。
- [x] 对齐资产详情补全系统提示词：原剧未知事实仍可标 unknown/needsReview；目标本地化字段按目标国家、时代、角色身份、用户目标设定补齐具体一致外貌；不机械设定欧美眼色/鼻型。
- [x] 用资产详情 merge→最终角色生图提示词的具体混合场景/族裔字段验证无源目标冲突，另验证用户显式族裔设定优先；不覆盖已存在资产字段，记录真实生效范围。
- [x] 分镜正文改为常驻可编辑控件，明确仅编辑正文、后端系统包装继续自动编译；@候选展示项目资产名称/变量/缩略图，允许空 @、过滤、键盘上下选择/Enter、Esc及无结果状态。
- [x] 在光标处写入展示资产变量，序列化保存正文和资产引用，刷新后恢复；视频生成前保存并确认队列成功，失败保留草稿且可提示重试。
- [x] 运行必要后端用例和前端构建；查现存浏览器/CDP能力并尝试真实交互 smoke；如无可用运行时记录证据边界。

## project_9718763481a1 当前角色资产目标外貌修复（2026-10-27）

- [x] 按原用户目标提示词精确定位 project_9718763481a1；读取七角色原目标字段、源字段、任务及图片/分镜引用；原项目共有7个角色资产、0条asset_images、0条storyboards，无进行中任务。
- [x] 使用已配置文本模型做一次六角色候选补全，并复用已验证的新郎脸部锚点；根据审核进一步将夏一曼明确设计为美国欧裔女性、修正赵琳目标核心发色与结构化字段不一致；未调用生图/视频/重新提取。
- [x] 七角色真实角色prompt编译及不可变字段校验通过：目标族裔canonical非空；编译中无东亚/泛亚裔混血/中国婚礼源回流；身材、服装、配饰、源事实、身份/变量关系保持原值。
- [x] 逐一CAS匹配七个原payload后在单一SQLite事务内应用目标字段和当前生图prompt；同步已存在的结构化字段镜像；不改asset状态、图片/历史prompt或分镜引用。
- [x] 8007真实health与资产GET=200；七个资产均返回新目标族裔及新自动编译prompt，7/7无源冲突词，ID/变量/待确认状态一致，0图片；原payload完整前快照保存在`artifacts/localization-review/project_9718763481a1-before-apply-full-payloads.json`，候选diff与编译证据在`artifacts/localization-review/project_9718763481a1-seven-role-localization-patch-review.json`。

- 作用范围：config extraction prompt 在新的资产提取请求读取；详情补全提示词是 backend module constant，须重启后端加载。2026-10-27 用户在本机运行 `scripts/Restart-AssetDetailBackend.ps1` 后，新服务 PID 71276 已启动，health与项目/资产只读接口验证通过。资产源缓存键按 prompt 内容 hash，完整缓存会被新内容失效；已存在的已填字段不会自动重写，需通过“分析当前集”重新提取；普通重绘不触发本地化 LLM。未调用 LLM，未验证模型真实遵循。
- 验证：backend/tests/test_remake_modes.py 39 passed；npm --prefix frontend run build 通过。CDP 隔离 fixture 确认空 @、缩略图 img.complete/naturalWidth、键盘 Enter、句中插入、成功后刷新恢复、正文/引用 PATCH 早于模拟 tasks/run；注入 PATCH 500 后 tasks/run 计数未增加且显示“保存失败，草稿已保留”。截图：C:\Users\liubijie\.codex\visualizations\2026\10\27\01a17cd3-4d6e-79b2-8ac1-19b2736627f7\storyboard-mentions-menu.png。
## 修复资产片段索引误当分镜缓存（2026-10-27）

- [x] 资产出现索引只作为上下文，不再满足分镜缓存命中条件；缓存要求本系统逐镜Agent来源、当前episode与视频指纹一致、非占位且具备镜头维度事实。
- [x] 模型结果需含真实逐镜事实且时间范围自洽；局部“待确认”允许保留。失败时不替换原分镜/人工正文；成功仅替换本系统自动结果和未编辑占位。
- [x] mock回退明示为“待分析片段”，GET不把mock正文作为prompt返回；状态明确待分析/分析失败，人工填写入口保留，回退占位不能启动视频生成。
- [x] 增加仅对分镜任务生效的force参数；有效视频分析缓存可复用，手动重分析绕过缓存。
- [x] `python -m pytest -q backend/tests`：35 passed，3个既有FastAPI弃用警告；`frontend` 下 `npm run build`（含`tsc -b`）通过。5174真实入口尚待本轮源码加载后核验。
- [x] 8007安全重载到最新版并确认真实数据库、项目/分镜GET；5174根HTML→`/src/main.tsx`→`/src/App.tsx`与`/src/styles.css`响应包含待分析状态、重试入口及对应样式。
- [x] 对 `project_595e01c4b5d6` / `episode_b36bbc18ad24` 仅发起一次真实分镜分析；方舟请求失败为`IncompleteRead(141616 bytes read, 9458 more expected)`，未重试。失败任务已记录；原5条fallback及资产保持不变，当前项目状态为“分镜分析失败”。
- [ ] 修复传输中断审计/完整响应判定并安全加载；经主代理批准后，最多再做一次同项目同视频验证请求，不重提资产/生成视频。若再次外部传输失败即停止并保留审计。

## 分镜分析传输截断审计补强（2026-10-27）

- [x] 只有Provider envelope明确`status=completed`、无`incomplete_details`且无`finish_reason=length/max_tokens`时，才允许验证截断响应的提取文本；原JSON须完整可解析，并继续通过逐镜事实和视频时长校验，禁止修括号/补猜事实。
- [x] Provider将IncompleteRead部分响应文本、字节数及已有response id带入任务结果供审计；截断/未完成响应作为失败保留原分镜。通用后台异常也将分镜项目状态置为“分镜分析失败”。
- [x] 完整HTTP但响应`status=failed/incomplete/in_progress`、含`incomplete_details`或`finish_reason=length/max_tokens`时拒绝使用；任务结果保留响应ID、原始envelope、可提取文本及完成原因，失败不进入镜头缓存。
- [x] 分镜提示词明确七维事实只写一次；`分镜具体动作描述`与`镜头描述`为旧数据兼容字段，新结果留空，不重复复述结构字段事实。
- [x] 回归覆盖截断部分响应保持占位、完整响应envelope incomplete/failed/length留原文拒绝、完整API envelope+完整事实JSON的受限恢复与事实/时间校验；`python -m pytest -q backend/tests`：40 passed，3条既有弃用警告。
- [x] Provider对完整HTTP语义失败响应的审计补强已加载至8007；正常Ctrl+C原受管session5377/PID65596后，以相同Python/cwd/端口在session6151/PID67392启动；health与project/asset/storyboard/task GET均200，仍指向真实workshop.sqlite3，不再触发模型调用。
- [x] 两次已批准的真实调用均失败且未得到可验证分镜：首次是HTTP `IncompleteRead(141616 bytes read, 9458 more expected)`，响应partial、ID未保存；修复后验证请求收到完整HTTP但模型响应标记未完成。首次partial/响应ID及第二次raw envelope/ID均未保存，不能从现有任务记录恢复；不猜补、不再请求。项目状态已纠正为“分镜分析失败”，原5条占位、资产与人工内容保持不变。
- [x] 最新真实库只读复核：目标episode仍14条资产、5条分镜（5条均为待分析占位且prompt为空），asset_extraction task仍2条，storyboard_generation task共5条；当前全局活动task=0。两次失败结果保留，后端API不显示占位为可用prompt；本轮无真实分镜prompt可摘录。

## sourceSnapshot / localizedProfile 分阶段资产链路（2026-10-28）

- [x] 版本化sourceSnapshot与localizedProfile契约；legacy字段仅做显示投影，不作为目标缺项回退源字段；保持ID/变量/来源/子资产关系。
- [x] 拆分纯源识别与国家/时代本地化调用；国内链路不调用localizer，目标国家链路对源快照做独立pass，render/remake mode独立选择。
- [x] 拆source cache与localization cache；旧组合缓存不得跳过新target pass；缓存键只含所属阶段的输入/版本/model/prompt/config。
- [x] 最终角色提示词从canonical目标结构组装core/visual anchor/face/hair；族裔必填，目标缺项报错，不回退源脸；保留用户明确锁定与资产替换已选图。两段自由文本与结构字段不一致时，由结构字段重建一致文本。
- [x] 3DCG角色固定后缀在最终provider请求边界逐字追加且exactly-once；直接完整prompt/手填/自动/缓存一致，保存promptSnapshot与请求文本一致；场景/道具仍用独立模板。
- [x] 隔离回归：国内真人/CG、美国真人/CG、用户锁定族裔、缺字段拒绝、source/localization cache切换、legacy完整缓存、直接prompt、精确固定后缀；provider消息捕获验证source阶段不含目标参数、localization阶段使用独立系统prompt。
- [x] 对project_c2463ccc0cea只跑target localization+compiler，不重新识别视频source、不生图；真实模型target pass经候选审核后窄patch应用，保留source事实、ID/变量、服装身材、历史图片与分镜引用。
- [x] 已使用项目配置文本模型对真实项目source facts/legacy design constraints运行target pass及一次定向修正；这验证目标本地化文本阶段，不代表重新跑真实视频source识别或模型会在所有资产上自动无误。

## project_c2463ccc0cea 目标画像候选与窄CAS应用准备（2026-10-28）

- [x] 精确定位用户当前案例：美国 / 英语 / `cg_3d`，13项资产（6名单人角色、1群像、2场景、4道具）；复核当前项目配置未变化，任务表2项均成功，asset generation jobs为failed/succeeded，video generation与composition export均0；当前无非终态任务。
- [x] 复用已归档吴月红新娘原始模型响应，并对其余资产执行一次localization文本批次及一次批准的定向纠错；raw与纠错响应先保存。对模型返回英文的6项场景/道具描述按既有中文内部结构契约规范化，保留Welcome/We're Married/Just Married/Celebrating Our Family原文。
- [x] 最新候选以真实`cg_3d`模式编译；12项角色/场景/道具完整画像，群像保留source并待人工审核，不套单人脸部字段。编译/约束校验`errors=[]`，13/13身份、source及历史图片引用不变量通过；非空服装/配饰原值逐字一致，unknown衬层及空配饰按无有效约束处理。数据库未写入。
- [x] 经主代理审核批准后，执行一次SQLite单事务窄patch/CAS，13/13资产行通过；完整原库备份为`project_c2463-preapply-runtime-20261028-115636.sqlite`，CAS guard核对项目配置与终态任务/生成job，并同步匹配childId的`子资产`数组目标字段。
- [x] 后端全量回归：`python -m pytest backend/tests -q`，52 passed、3条既有FastAPI弃用警告；隔离Uvicorn真实启动读取最新target prompt（SHA256 `27dda4d58c693732341d4d386f4e903d5b7918272be48439656e561fdaedd06f`），source/target配置均已加载，`GET /api/projects`=200空隔离库，之后以Ctrl+C结束测试进程。
- [x] 提交后隔离SQLite副本+当前源码TestClient验证：13项，12 complete + 1群像pending；六名单人角色族裔canonical非空且结构完整，生图最终prompt与候选逐字一致、3DCG固定后缀恰一次、无中国/东亚源回流；原始source snapshot、非空服装/配饰、IDs/变量/status、图片(1条及选用/历史prompt)、storyboards、tasks/jobs均保持不变，errors=[]。报告：`artifacts/localization-review/project_c2463-post-apply-verification.json`。
- [x] 候选与审计：`artifacts/localization-review/project_c2463-current-cast-migration-candidate.json`；raw/纠错/源快照/完整SQLite备份/行级guard及应用/验证脚本在忽略目录。CDP本轮WebSocket连接超时，未操作用户页面，故本轮migration DOM未验证。
- [x] 通过既有 `scripts/Restart-AssetDetailBackend.ps1` 的身份、同库、无活动数据库任务守卫，经Windows UAC管理员授权重启真实8007；health为200且指向项目 `data/workshop.sqlite3`。监听PID 35596于2026-10-28 12:17:29启动，晚于 `main.py`（11:46:10）、`asset_source_extraction.md`（10:51:03）和 `asset_target_localization.md`（11:45:11）的最后修改；启动日志 `backend-20261028-121726.stdout.log` / `.stderr.log` 已生成，stderr为空。
- [x] 实际8007 API复核13项（12个完整画像、1个群像待审核）；六名单人角色的最终提示词均以用户11.1三段固定英文约束逐字结尾且只出现一次，目标画像不含东亚/中国族裔回流，发型分线字段全部非空；吴月红保留 `asset_image_184e287fa708`。复核报告：`artifacts/localization-review/project_c2463-live-8007-verification.json`。未调用生图或视频模型。
