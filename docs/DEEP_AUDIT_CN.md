# LLM-MM-Agent 深度审计与国赛对标改造说明

## 结论摘要

本次改造把研究 CLI、Demo 服务和论文学习评测分成三个边界：

* 研究 CLI：模型路由、代码执行进程树、输出上限、解析和 token 统计可控。
* Demo 服务：项目/版本/会话按用户隔离，SSE 需要 JWT，真实模型调用和资源下载均有出站目标约束，危险权限默认关闭。
* 论文评测：只处理用户本地且有权使用的文件；参考库只存链接和核验状态；模型短引会反查论文页码，异常数值、完整性风险和高分均进入人工复核。

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

## 国赛对标 rubric

公开章程强调四个方面：基本假设的合理性、建模的创造性、结果的正确性、文字表述的清晰性。本仓库在 MMBench/cumcm/rubric.json 中把四项各设为 25 分，明确注明这是学习用等权，不是官方奖项阈值。格式检查另计 10 分，用于发现符号、图表、引用和可复现性问题，不并入四维总分。

评测结果至少包含：

* 四个维度的确定性总分（0–100）和逐维反馈；
* 论文页码/章节/短引证据；
* 风险、改进动作、置信度；
* 人工复核原因与“不构成获奖预测”免责声明。

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

* 本地 Python compileall：`MMAgent`、`MMBench` 与 demo 后端范围内 123 个 Python 文件通过；
* 本地 unittest：模型别名与参数、双供应商密钥、证据/页码反查、提示注入、NaN/Infinity、完整性 gate、参考库版权/奖项边界、排行榜、SSRF/IDOR 静态回归、Windows 进程树/输出洪泛/路径逃逸，共 34 项通过；
* 本地前端：触及的安全配置文件 ESLint 零问题，TypeScript `tsc --noEmit` 通过，现有依赖的 Next.js 生产构建通过（Node 24.19.0 / Next.js 16.3.3）；
* 论文与 GitHub 索引核验只读取页面和元数据，没有复制论文正文。

尚未在本环境执行：

* 真实 OpenAI/DeepSeek API 调用（需要用户自己的 key）；
* E2B 真实沙箱集成；
* 数据库迁移与浏览器到后端的端到端流程；
* 前端遗留代码的全量 ESLint 清债（当前生产构建不受影响；本次触及文件已单独通过 lint）。
* GitHub Actions 尚未运行；CI 中 Node 22 + `npm ci --ignore-scripts` 也需在分支推送后验证。

接入真实 key 后，建议先运行单篇 dry-run，再用 --ensemble 对同一篇论文复核，并人工抽查至少 10 条证据的页码和原文。
