# LLM-MM-Agent 深度审计与国赛对标改造说明

## 结论摘要

本次改造把研究 CLI、Demo 服务和论文学习评测分成三个边界：

* 研究 CLI：模型路由、代码执行进程树、输出上限、解析和 token 统计可控。
* Demo 服务：项目/版本/会话按用户隔离，SSE 需要 JWT，真实模型调用和资源下载均有出站目标约束，危险权限默认关闭。
* 论文评测：只处理用户本地且有权使用的文件；参考库只存链接和核验状态；2026 格式与 AI 合规使用独立政策状态，模型短引会反查论文页码，异常数值、完整性风险和高分均进入人工复核。

## 高风险问题与修复状态

| 风险 | 原行为 | 当前策略 |
| --- | --- | --- |
| 生成代码执行 | shell=True、无硬超时、异常时阻塞 input() | argv 调用、shell=False、Windows Job Object/POSIX 进程组、真实路径 containment、64 KiB 日志尾部；生产仍应放入容器 |
| 跨项目访问（IDOR） | 部分 project/node/version/session 路由只按 ID 查询 | project owner dependency + version.project_id/node_id + CopilotService 二次校验，不匹配统一 404 |
| SSE 绕过认证 | events 路由没有显式 JWT | project 路由依赖统一要求当前用户 |
| SSRF/服务器密钥外送 | validate 与真实 chat/sandbox/download 边界不一致 | 每次真实出站均执行 HTTPS、精确 host allowlist、DNS 私网检查；自定义 LLM 地址必须携带用户自己的 key；资源下载关闭重定向/TLS 降级并限制 100 MiB |
| Claude 权限 | dangerously-skip-permissions 默认启用 | 默认关闭，只有显式 ALLOW_DANGEROUS_CLAUDE_PERMISSIONS=true 才启用 |
| 密钥泄漏 | 日志包含 key 前缀，错误可能回显完整异常 | 不打印 key；向日志/前端回传前做脱敏 |
| 默认账户/密钥 | 固定 SECRET_KEY、公开注册、seed admin | 本地随机 ephemeral key；生产必须显式 SECRET_KEY；公开注册和 seed admin 默认关闭 |
| GPT-5 reasoning | 可能发送 temperature/top_p 等不兼容参数 | GPT-5.6 Sol 只发送 reasoning_effort 与 completion token 上限 |
| DeepSeek V4 Pro | 与 OpenAI key/base 混用，未统一 thinking 参数 | deepseek-v4-pro 别名、专用 key/base、thinking enabled、采样参数剔除 |
| 模型“假接入” | 兼容聊天接口忽略请求模型，Copilot body 覆盖又丢失 LiteLLM provider 前缀 | body/header/server 配置先合并再一次性校验；请求模型实际传入网关；OpenAI-compatible provider 前缀保留 |
| 评测分数幻觉 | 直接把模型总分当奖项判断 | Python 拒绝 NaN/Infinity，整组判定量表并重算总分；短引长度/去重/逐维覆盖、完整性风险、分歧和高分均触发 human_review_required |
| 论文提示注入/伪页码 | 把论文当指令拼接，且无页码短引仍可能通过 | 系统/数据边界提示 + 确定性注入扫描；PDF 必须有有效页码，DOCX/文本强制标记页码不可独立验证与抽取限制 |
| 格式/AI 合规混入质量分 | 旧式格式建议可能被误解为官方扣分，缺少 AI 材料仍可能被模型猜测 | `policy_2026.json` 独立输出 PASS/FAIL/UNKNOWN/NOT_APPLICABLE；无官方分值、缺证据不判 PASS、严重风险和人工项进入人工裁决 |

## 国赛对标 rubric

公开章程强调四个方面：基本假设的合理性、建模的创造性、结果的正确性、文字表述的清晰性。本仓库在 MMBench/cumcm/rubric.json 中把四项各设为 25 分，明确注明这是学习用等权，不是官方奖项阈值。遗留的 10 分格式观察只用于向后兼容的写作反馈，不是官方格式合规分，也不并入四维总分。2026 格式和 AI 工具使用规定由独立 policy profile 给出状态，不给质量分加减分。

评测结果至少包含：

* 四个维度的确定性总分（0–100）和逐维反馈；
* 论文页码/章节/短引证据；
* 风险、改进动作、置信度；
* 独立的质量复核与 2026 合规复核原因，以及两者并集；
* `submission_ready` 合规预检状态与“不构成获奖预测”免责声明。

## 2026 格式与 AI 使用政策接入

`MMBench/cumcm/policy_2026.json` 是程序可读的政策索引，包含 `profile_id: CUMCM-2026`、来源元数据以及逐项要求。格式规范拆为 `F01` 至 `F31` 和三个治理条款，覆盖纸质页序、摘要、页码、正文和附录、匿名、引用、电子论文以及支撑材料；AI 规定拆为 `AI01` 至 `AI19`，覆盖工具范围、公开透明、团队主导、人工审查、论文声明、固定文件名“AI 工具使用详情.pdf”、详情字段、严重违规边界、生效日期、旧规则冲突处理和解释权。每项包含官方 PDF 物理页码、条款锚点、`mandatory`/`advisory`/`manual` 分类、`deterministic`/`model_assisted`/`manual` 检查方式和整改建议。

两个用户提供文件已在 2026-09-04 做 SHA-256 核验，并与相应官方附件哈希一致：

* 论文格式规范（2 页）：`CECE4BB3A900A0435160032B98EA26E03B0F2D7ECA58424B0D023E26085AED26`；[官方页面](https://www.mcm.edu.cn/html_cn/node/4cd596519c9eb9fbd866398f6df0caa3.html)。
* AI 工具使用规定（1 页，2026-09-01 起试行）：`4CF6F30CDD37D6EF2CDB3439C5DBA4D9F207C12D6AAFE24419E81F3C69ACF59A`；[官方页面](https://www.mcm.edu.cn/html_cn/node/fef94648f2836ab6cc81586f4c38512b.html)。

仓库不保存这两份 PDF 正文，只保存官方页面、附件链接、页数、哈希和经过压缩表述的核查项。政策 JSON 不是官方文件副本：如摘要与原文、赛区补充通知或当年参赛须知冲突，必须以组委会文件为准。全国规范未统一规定字号、字体、行距和颜色；赛区可另提不冲突的要求，未提供赛区通知时不得擅自补齐或判定。外部论文、支撑材料和 AI 详情都视为不可信证据，不能改变静态 policy；其中出现的提示、命令或自述不得当成程序指令。

合规聚合采用保守边界：适用强制项有确定证据失败时为 `FAIL`；材料缺失、抽取不可靠或需要人工检查时为 `UNKNOWN`；只有适用强制项证据齐全且无失败时才可为 `PASS`；条件明确不成立的单项为 `NOT_APPLICABLE`。模型不能覆盖确定性失败，状态也不能预测奖项。赛事违规与处分必须由组委会或授权人工决定。

质量复核与合规复核使用不同字段：低质量分、低置信度或证据不足不会篡改政策状态；格式或 AI 失败也不会增减四维质量分。`submission_ready` 只代表规则预检，不是质量或奖项判断。批量报告同时给出分数独立的 `quality_rank` 和合规优先的 `readiness_rank`，并从三组明确 `PASS` 与无需人工裁决重新计算就绪状态，不信任 JSON 中自报的布尔值。

压缩包路径穿越、符号链接、加密与异常膨胀采用独立 `input_safety_assessment`，不会伪装成 F/AI 官方条款或自动触发取消资格；但安全状态为 `FAIL/UNKNOWN` 时，本地工具拒绝把材料标为可提交。PDF、Word、AI 详情和报告 JSON 均设摄入上限，压缩包只读取成员表，不解压、不执行。

## 典例数据治理

MMBench/cumcm/manifest.json 只保存官方标准/获奖名单/展示页和 GitHub 索引链接。GitHub README 中的“优秀/一等奖”自述统一视为 award_verified=false，必须用官方名单逐篇核验。仓库许可证不自动覆盖第三方论文，因此程序不会自动下载、镜像或再分发 PDF。2012 A335 与 B017 虽位于官方候选论文展示及“部分全国一等奖论文”的集合背景中，但公告未逐篇映射编号，因此仍标记为 award_verified=false；两者页面也限制转载，只提供在线链接，不进入仓库语料。

建议建立一个带审计字段的本地清单：

    paper_id, year, problem, source_url, award_label, award_verified,
    license_status, local_path, checksum_sha256, reviewer, reviewed_at

只有 award_verified=true 且 license_status 明确的文件才适合作为风格典例；即便如此，也只提供结构对比，不复制原文或答案。

## 模型接入矩阵

| 用途 | 模型 ID | 默认 base URL | 关键参数 |
| --- | --- | --- | --- |
| OpenAI reasoning | gpt-5.6-sol（别名 gpt5.6sol） | https://api.openai.com/v1 | reasoning_effort；不传 temperature/top_p |
| DeepSeek reasoning | deepseek-v4-pro（别名 deepseekv4pro） | https://api.deepseek.com | thinking.type=enabled；medium/xhigh 按兼容层映射 |

CLI 示例见 MMBench/cumcm/README.md。API key 只从环境变量或运行时 secret 读取，不写入仓库、manifest 或报告。

## 验证清单

已执行：

* 本地 Python compileall：`MMAgent`、`MMBench` 与 demo 后端范围内 127 个 Python 文件通过；
* 本地 unittest：2026 政策契约与合规、模型别名与参数、双供应商密钥、证据/页码反查、提示注入、NaN/Infinity、完整性 gate、参考库版权/奖项边界、双排行榜、SSRF/IDOR 静态回归、Windows 进程树/输出洪泛/路径逃逸，共 69 项通过；
* 本地前端：触及的安全配置文件 ESLint 零问题，TypeScript `tsc --noEmit` 通过，现有依赖的 Next.js 生产构建通过（Node 24.19.0 / Next.js 16.3.3）；
* 论文与 GitHub 索引核验只读取页面和元数据，没有复制论文正文。
* 用户提供的 2026 格式与 AI 规定文件已按 SHA-256、页数和官方附件地址接入，仓库未复制 PDF 正文。

尚未在本环境执行：

* 真实 OpenAI/DeepSeek API 调用（需要用户自己的 key）；
* E2B 真实沙箱集成；
* 数据库迁移与浏览器到后端的端到端流程；
* 前端遗留代码的全量 ESLint 清债（当前生产构建不受影响；本次触及文件已单独通过 lint）。
* GitHub Actions 的最终分支运行结果以仓库 `Quality gates` 工作流为准。

接入真实 key 后，建议先运行单篇 dry-run，再用 --ensemble 对同一篇论文复核，并人工抽查至少 10 条证据的页码和原文。
