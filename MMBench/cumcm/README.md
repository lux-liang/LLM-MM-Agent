# 国赛数模论文学习评测模块

这个模块把“找典例”与“打分”拆成可审计的两步：

1. manifest.json 只保存官方页面和 GitHub 索引链接，不自动下载或再分发论文。
2. 用户把自己有权处理的 PDF、DOCX 或 Markdown 放到本地目录，由模型按 rubric.json 逐维评分。
3. 每条高分意见都要求论文页码、章节和短引；程序会把短引反查到待评论文，证据未命中、页码无效、模型分歧较大或置信度低时自动标记人工复核。
4. `policy_2026.json` 与确定性检查器单独输出格式、AI 使用和人工裁决状态；模型评分不能改写这些结果。

四个核心维度对应竞赛公开章程：基本假设的合理性、建模的创造性、结果的正确性、文字表述的清晰性。四项各 25 分是本项目用于训练的等权操作定义，不是官方奖项分数线，也不能推出“一等奖”。

## 快速使用

    python -m MMBench.cumcm.evaluate_paper --paper samples/my_paper.pdf --model gpt5.6sol --output reports/my_paper.json
    python -m MMBench.cumcm.evaluate_paper --paper samples/my_paper.pdf --model deepseekv4pro --reasoning-effort high
    python -m MMBench.cumcm.evaluate_paper --paper samples/my_paper.pdf --ensemble --references-dir references
    python -m MMBench.cumcm.evaluate_paper --paper samples/my_paper.pdf --policy MMBench/cumcm/policy_2026.json --supporting-materials samples/supporting.zip --ai-details "samples/AI 工具使用详情.pdf"
    python -m MMBench.cumcm.evaluate_paper --paper samples/my_paper.md --dry-run
    python -m MMBench.cumcm.score_references --reports-dir reports --output reports/leaderboard.csv

### CUMCM 2026 B 题前两问：证据驱动写作与可视化

`q1_q2_pipeline.py` 将前两问的数值证据、TeX 正文审计和图表生成固定成一个可复现入口。它不调用模型接口，也不写入密钥：先拒绝缺失或非有限数字，再生成带有“主模型/辅助模型”边界的编辑提示，最后输出三张矢量 PDF 及 PNG 预览。主模型必须承担有界误差下的鲁棒极小极大保证；局部条带、等效半径和 CRLB 只能作为解释性辅助，有限外层搜索不能写成连续空间全局最优。

在已经准备好结果 JSON 和论文 TeX 的情况下，可以运行：

    python -m MMBench.cumcm.q1_q2_pipeline `
      --results path/to/Math-Modeling/reports/q1_q2_dual_model_results.json `
      --q1-tex path/to/Cumcm/contents/sections/b_q1_q2.tex `
      --q1-tex path/to/Cumcm/contents/sections/b_q1_q2_validation.tex `
      --output path/to/Cumcm/figures/b_q1_q2 `
      --manifest path/to/Cumcm/figures/b_q1_q2/mmagent_q1_q2_manifest.json `
      --prompt path/to/Cumcm/figures/b_q1_q2/mmagent_q1_q2_editor_prompt.txt

清单记录输入结果的 SHA-256、匹配到的论文主张以及每张图的 PDF/PNG SHA-256。绘图采用 Matplotlib 的 Agg 后端和确定性输入，适合在提交前逐页复核；生成的编辑提示只是草稿，数字、推导、引用、AI 使用披露和最终格式仍须由队伍人工核验。

`--policy`、`--supporting-materials` 和 `--ai-details` 是 2026 合规检查接口：分别指定版本化规则、支撑材料包和供核验使用的 AI 详情 PDF。它们只读取本地证据，不会代替参赛队生成提交文件，也不会把支撑材料或 AI 记录加入训练语料。未使用 AI 时不传 `--ai-details`，但论文仍须包含官方规定的未使用声明；使用 AI 时，详情文件还须按官方名称放入正式支撑材料。缺少适用材料会得到 `UNKNOWN`，不会被推定为合规。

## 2026 规则与判定边界

`policy_2026.json` 把两份官方文件转录为可追溯的简明检查项：格式检查使用 `F01` 至 `F31`，格式治理条款使用 `FG01` 至 `FG03`，AI 规则使用 `AI01` 至 `AI19`，覆盖公开透明、团队主导、人工核验、论文声明和详情材料要求。每项都带官方来源、PDF 物理页码、条款锚点、检查方式和整改建议；仓库不保存或复制 PDF 正文。用户提供文件的 SHA-256 已与官方附件逐字节匹配：格式规范为 `CECE4BB3A900A0435160032B98EA26E03B0F2D7ECA58424B0D023E26085AED26`，AI 规定为 `4CF6F30CDD37D6EF2CDB3439C5DBA4D9F207C12D6AAFE24419E81F3C69ACF59A`。

全国规范没有统一要求字号、字体、行距和颜色；赛区可以在不违反全国规范的前提下增加要求。因此，未提供所属赛区当年通知时，程序不得虚构版式阈值，也不得把 `F20` 判为全国统一的通过或失败，应保留人工核验边界。

四种合规状态只描述当前证据，不是官方处分决定：

* `PASS`：全部适用的强制项都有足够证据，且没有失败项。
* `FAIL`：至少一个适用强制项有可靠证据表明不符合。
* `UNKNOWN`：材料缺失、页面或版式无法可靠抽取，或者条款本身必须人工检查。
* `NOT_APPLICABLE`：某一单项的适用条件明确不成立，例如真实未使用 AI 时，AI 详情文件要求不适用。

确定性失败不能被模型意见覆盖；任一人工检查项、证据冲突或严重 AI 使用风险都应进入人工复核。最终解释与违规处理权属于竞赛组委会。合规状态与四维论文质量分完全分离：高分论文可能不合规，合规论文也不等于达到获奖水平。当前政策没有给格式或 AI 条款规定分值，本模块不会虚构扣分、官方分数线或获奖概率。

报告分别给出 `quality_review_required` 与 `compliance_review_required`；`human_review_required` 是两者的并集。`submission_ready` 只表示当前材料是否通过 2026 规则预检，不受模型的四维分或遗留 `format_score` 控制，也不表示论文质量足以获奖。批量聚合输出两个独立名次：`quality_rank` 只按四维总分排列，`readiness_rank` 优先考虑政策版本、格式、AI 状态和人工裁决；任何输入报告自报的 `submission_ready` 都会被忽略并重新计算。

压缩包路径穿越、符号链接、加密或异常膨胀等属于本工具的 `input_safety_assessment`，不冒充竞赛条款，也不直接生成“赛事违规”结论；为保护本机，存在安全失败时仍会阻止 `submission_ready`。压缩包只列成员名，不解压、不执行，超过安全摄入上限或本机无法检查 RAR 时返回 `UNKNOWN`。

环境变量：

* OPENAI_API_KEY、OPENAI_BASE_URL（兼容 OPENAI_API_BASE）：GPT-5.6 Sol（模型 ID gpt-5.6-sol）。
* DEEPSEEK_API_KEY、DEEPSEEK_BASE_URL（兼容 DEEPSEEK_API_BASE）：DeepSeek V4 Pro（模型 ID deepseek-v4-pro，默认地址 https://api.deepseek.com）。
* MMAGENT_REASONING_EFFORT：none、low、medium、high、xhigh、max；DeepSeek 的 medium/xhigh 会映射为 high。

ensemble 模式会分别读取两个供应商的密钥和地址；也可用
--openai-api-key/--deepseek-api-key 与对应的 --*-base-url 显式覆盖。为避免 shell 历史泄露，优先使用环境变量。
仓库的 dry-run 和确定性测试不会请求供应商；在线双模型评分必须使用你自己的 key，且输出仍须人工核验。

安装可选文档解析依赖：

    pip install openai pypdf python-docx

## 典例来源与版权边界

manifest.json 收录了中国大学生在线/竞赛官网的公开标准与展示页，以及
[CUMCM-Archive](https://github.com/yushugulao/CUMCM-Archive)、
[Final-Math-Modeling](https://github.com/Chen-Jin-Han/Final-Math-Modeling) 等 GitHub 索引。
仓库 README 的“优秀/一等奖”自述统一标记为 award_verified: false，须用官方获奖名单逐篇核验；仓库许可证也不能自动授予第三方论文的再分发权。请仅对本地、已获授权的文件运行评测。

2012 年 A335《葡萄酒的质量分析与评价》和 B017《基于递归算法的建筑外表面光伏电池布局优化分析与设计》
来自官方候选论文展示，且处于竞赛官网所述的“部分全国一等奖论文”集合背景中；但现有公告没有逐篇映射编号，
因此两篇仍标记为 award_verified: false，不能作为已核验一等奖样本。它们只以链接和元数据进入 manifest；
官方展示页限制转载，所以程序不会抓取、缓存或随仓库分发正文。需要模型比较正文时，请在获得处理权后把
本地文件放入 --references-dir；本地典例只用于结构比较，不会自动获得加分。

官方依据链接：

* [竞赛章程](https://dxs.moe.gov.cn/zx/a/hd_sxjm_dsjj/210517/1696591.shtml)
* [全国奖项评阅工作规范（2023年修订稿）](https://www.mcm.edu.cn/html_cn/node/b1f48689659f0660e80a2d6279d7b37d.html)
* [论文格式规范（2026年修订稿）](https://www.mcm.edu.cn/html_cn/node/4cd596519c9eb9fbd866398f6df0caa3.html)
* [人工智能工具使用规定（2026年试行）](https://www.mcm.edu.cn/html_cn/node/fef94648f2836ab6cc81586f4c38512b.html)
* [2024 获奖名单](https://www.mcm.edu.cn/html_cn/node/3aa4e9fc4c5a755da53b343660cdaf59.html)
* [2025 论文展示](https://dxs.moe.gov.cn/zx/hd/sxjm/sxjmlw/2025qgdxssxjmjslwzs/)

模型只生成学习反馈；最终论文仍需人工核验数据、推导、引用、格式和原创性。
