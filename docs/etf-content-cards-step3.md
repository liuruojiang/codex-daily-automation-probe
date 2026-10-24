# ETF 改造第三步：逐条语义复核与编辑否决

## 已实现

`scripts/etf_semantic_review.py` 在引用/数字校验之后检查标题、每条事实、
每条限定条件和每个待核对问题。复核记录绑定 `material_id` 与整张卡片的
`card_hash`，遗漏项目、重复项目、未知句号编号、前后冲突的总判断或过期
哈希均拒绝。任何卡片改动（包括问题区）都会使旧复核失效。

复核只评价中文是否忠于所给材料，不验证文章、数据、税法或模型本身是否
正确。`model_review_passed` 不等于外部事实验证，也不代表允许正式发送。
正式邮件流程仍未接入这些模块。

生成沿用 DeepSeek V4.1 Flash；本步通过已有网关使用 Qwen Flash 另起请求
复核。不同模型减少直接自我复述，不保证错误独立，下面的反例已经说明
第二个模型也会错误放行。

## 真实验证与保留的失败

本步共14次真实请求：4次生成、10次模型复核。提供方 usage 合计输入
29,674 tokens、输出9,593、总计39,267；未独立核实美元扣费。凭据没有写入
仓库或复制到 GitHub。无新邮件、无部署、无正式流程切换。

首版复核协议在6个真实复核响应中全部漏掉标题。即使模型说 overall=pass，
程序也因覆盖不完整而拒绝。首版还将 Quarter-Hourly 的错误译法“季度小时”
判成正确。这些响应没有被补填项目后冒充模型成功。

更新协议后显式提供全部 required_claim_ids，要求第一项必须是标题，并
明确“答案未知的合理问题”不是错误事实。模型/提示词/输出上限及卡片集
改变时使用独立实验目录。首轮两篇文章已参与发现协议问题，不再计作
独立预留验证；另选两篇不同的新文章，事先固定材料ID与提示词哈希。

| 样本 | 模型或机械结果 | 编辑后的实际状态 |
|---|---|---|
| 已知反例：把提款率写成年化投资收益率 | 通用指标类型检查拒绝 | 拒绝，未付费重复复核 |
| 已知反例：Quarter-Hourly译成季度小时 | 通用术语检查拒绝 | 拒绝，未付费重复复核 |
| 旧TIPS卡片 | 数字引用依据不足 | 保留拒绝，不调用复核模型 |
| 旧GLD/GLDM卡片 | Qwen逐条判通过 | 编辑否决：税务后半句引用范围不足 |
| 旧Hawkes卡片 | Qwen逐条判通过 | 编辑否决：把未说明事项写进作者限定条件 |
| 新文：Basis-Only Funding Is Not Dividend Fair | 初次被短句引用门槛误拒；通用修复后通过模型核对 | 待审影子稿 |
| 新文：Carbon Pricing’s Impact on Global Industry Is Quietly Growing | 引用校验及模型核对通过 | 待审影子稿 |

“两篇新文通过”是小规模便利样本结果，不是总体准确率。新文测试后没有
调整生成/复核提示词；但短句引用校验作过数据驱动修复，不能把修复后的
结果描述为完全未经调整的端到端盲测。程序未改写这两篇的模型原始输出。

## 引用、术语与覆盖边界

- 旧规则要求每个引用至少20字符，会误拒真实原文短句，例如一个完整的
  否定回答。现仅允许“原文中的完整短句”与同一条的有效长引用配对，短句
  单独出现、任意短片段或脱离上下文仍拒绝。数字和逐字匹配没有放宽。
  单独记录 `etf-evidence-validation-v2`，不因此重新付费生成。
- 三项术语检查只看当前条目自己的引用，避免把样本期的“每季度首月”
  误认为季度小时。规则是通用领域概念，不按文章标题或具体收益数字特判。
  它们仅覆盖少量已知误译，不是完整的翻译验证器。
- 作者限定条件、资料范围和编辑推断需要区分。第二个模型仍可能把材料
  中没有的信息说成作者承认的限制，因此必须保留编辑反例和否决入口。

## 运行方式

离线生成复核请求：

```powershell
python scripts/etf_semantic_review.py prepare --materials materials.json --responses responses.json --out-dir artifacts-review
```

在线复核需在执行环境显式设置 `ETF_REVIEW_API_URL`、`ETF_REVIEW_MODEL`、
`ETF_REVIEW_API_KEY`，然后：

```powershell
python scripts/etf_semantic_review.py review --materials materials.json --responses responses.json --out-dir artifacts-review --max-calls 2
```

结果及本地回执可恢复，不自动重试失败。单篇失败仍生成可诊断报告；机械
或术语检查失败的卡片不占付费复核请求。当前实现供单进程使用，输出目录
不是多写者数据库，也没有新增计划任务或跨Actions运行的持久化机制。

离线复核/编辑否决：

```powershell
python scripts/etf_semantic_review.py replay --materials materials.json --responses responses.json --reviews artifacts-review/reviews.json --editorial-vetoes editorial-vetoes.json --out-dir artifacts-editorial
```

否决文件按 material_id 索引，每条包含 material_id、card_hash、
`decision: "needs_revision"` 和具体 reason_zh。它只能否决，不能强制通过，
且旧卡片的否决不能误套到改过的卡片上。编辑记录是本次对话的源文核对，
不是用户本人审阅、数字签名或外部金融事实核查。

## 验证

76项针对性测试、15个子测试通过。新增覆盖完整检查项、哈希失效、无依据
通过、术语与指标混淆、短句上下文、恢复不重做、配置隔离和编辑不能强制
放行等行为。沿用此前Windows临时目录兼容运行器，未绕过测试断言。
真实编辑否决报告还在禁用模型调用的情况下完整离线重放，确认不需新请求。

根据明确拒绝原因修订旧卡片并重新复查的流程和实际结果，见
[第四步](etf-content-cards-step4.md)。不能只靠增加评分或再问同一问题来清除警报。
