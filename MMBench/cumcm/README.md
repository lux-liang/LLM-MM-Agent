# 国赛数模论文学习评测模块

这个模块把“找典例”与“打分”拆成可审计的两步：

1. manifest.json 只保存官方页面和 GitHub 索引链接，不自动下载或再分发论文。
2. 用户把自己有权处理的 PDF、DOCX 或 Markdown 放到本地目录，由模型按 rubric.json 逐维评分。
3. 每条高分意见都要求论文页码、章节和短引；程序会把短引反查到待评论文，证据未命中、页码无效、模型分歧较大或置信度低时自动标记人工复核。

四个核心维度对应竞赛公开章程：基本假设的合理性、建模的创造性、结果的正确性、文字表述的清晰性。四项各 25 分是本项目用于训练的等权操作定义，不是官方奖项分数线，也不能推出“一等奖”。

## 快速使用

    python -m MMBench.cumcm.evaluate_paper --paper samples/my_paper.pdf --model gpt5.6sol --output reports/my_paper.json
    python -m MMBench.cumcm.evaluate_paper --paper samples/my_paper.pdf --model deepseekv4pro --reasoning-effort high
    python -m MMBench.cumcm.evaluate_paper --paper samples/my_paper.pdf --ensemble --references-dir references
    python -m MMBench.cumcm.evaluate_paper --paper samples/my_paper.md --dry-run
    python -m MMBench.cumcm.score_references --reports-dir reports --output reports/leaderboard.csv

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
* [2024 获奖名单](https://www.mcm.edu.cn/html_cn/node/3aa4e9fc4c5a755da53b343660cdaf59.html)
* [2025 论文展示](https://dxs.moe.gov.cn/zx/hd/sxjm/sxjmlw/2025qgdxssxjmjslwzs/)

模型只生成学习反馈；最终论文仍需人工核验数据、推导、引用、格式和原创性。
