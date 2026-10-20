# 材料类型门槛与原判复议

播客时间轴可能篇幅很长，却没有实质讨论正文；模型也可能把合理未知问题
误判为证据不足。本步分别处理这两类问题，不靠重复请求删除拒绝记录。

## 共同材料门槛

新增 `scripts/etf_material_kind.py`。先沿用原有材料、聚合页与可读性门槛，
再检查多个时间码之间的短条目和连续正文。按语言结构判断，不使用网站黑名单：

| kind | 后续处理 |
| --- | --- |
| insufficient_material | 沿用原拒绝，需要正文或拆分聚合链接 |
| timeline_dominant | 疑似目录，先补文字稿或正文 |
| mixed_timeline | 目录与其他材料混合，需补正文或人工检查 |
| readable_excerpt | 未命中结构门槛，继续引用/语义等检查 |

规则使用至少3个时间码、短条目占比、粗略中英长度及连续句子等启发式。
充足正文与章节导航可同时存在；时间码本身不表示不能处理。粗略长度不是
模型tokens或费用估计。未命中门槛也不能证明全文、内容价值或外部事实。
短文字稿、特殊格式、很长的宣传页仍可能误判，profile保留判断版本、材料ID、
原因和计数，方便回看，而非默默排除。

`etf_content_cards.ready` 现在应用共同门槛，`basic_ready` 仅保留原始可读性检查。
生成请求构造器、CLI生成选择、引用验证、语义复核、修订和影子批次都受其约束。
`daily_reports.py` 的生产逻辑未改。

生成协议为 etf-content-card-v4，验证协议为 etf-evidence-validation-v3，批次协议
为 etf-shadow-run-v2。新批次身份包含材料规则版本；旧缓存不会默默升级为新门槛
下的通过。旧文件保留，重放历史协议须使用当时的代码版本，不覆盖旧目录。

## 复议案件

新增 `scripts/etf_review_appeal.py`，只复议一份完整原判中的拒绝项。不能复议
无效/漏项原判，也不能用复议绕过材料、引用数字或术语门槛。

petition严格包含 material_id、card_hash、review_hash、objections。每条 objection
包含 claim_id、reason_zh、evidence_ids；必须对应原判中非supported的条目，
有有效来源依据、不重复。可以只申诉一部分，但未申诉的拒绝继续保留。
source_title 是明确的标题证据ID，其余引用为原文 p1.s1 等句号编号。

案件保存原卡片、原判、申请、原模型标识及其摘要，以 case_id 绑定。
修改任何原文、卡片、原判或申请后，旧案件/意见无效。原模型标识是输入元数据，
并非签名或提供方认证；要求复议请求使用不同模型标识，不表示独立机构核验。

复议结果严格包含 case_id、checks；checks每条只含 claim_id、decision、reason_zh、
evidence_ids。所有申诉项恰好一次，不接受新增正文、overall pass 或未知条目。
decision只能是 uphold、withdraw_objection、inconclusive。撤回事实或限定条件的
异议还必须给出正文证据，不能只引标题。

原判永远保留。全部异议获建议撤回时，状态只是 appeal_supports_editorial_recheck；
仍有异议或未申诉拒绝时为 appeal_objections_remaining。所有情况仍需编辑审阅，
没有发布许可、外部事实认证或“模型重问直到通过”的循环。

## 请求及恢复

每案目录最多预留一次请求；改变配置或案件必须使用新目录。不同案件/目录之间
没有全局预算事务，因此当前只作显式提交的复议，不接入自动每日付费循环。
先落盘预留，再请求；已完成回执可离线解析，缺失/损坏/未知回执不自动重试。
输入超限不截断，输出有单次配置上限。沿用操作系统目录锁及不记录密钥/异常正文
的请求回执。密钥经环境变量传入，不写入案件或结果。

配置 ETF_APPEAL_API_URL、ETF_APPEAL_MODEL；run还需 ETF_APPEAL_API_KEY：

```powershell
python scripts/etf_review_appeal.py prepare --material material.json --card card.json --review review.json --petition petition.json --original-model qwen-flash --out-dir artifacts-appeal --thinking disabled
python scripts/etf_review_appeal.py run --material material.json --card card.json --review review.json --petition petition.json --original-model qwen-flash --out-dir artifacts-appeal --thinking disabled
python scripts/etf_review_appeal.py replay --material material.json --card card.json --review review.json --petition petition.json --original-model qwen-flash --out-dir artifacts-appeal --thinking disabled
```

thinking仅用于支持该参数的提供方；本次DeepSeek明确disabled。CLI保留退出码1，
表示复议不满足原稿编辑/发布门槛；实际状态及回执在 appeal-report.json。
prepare与replay不请求模型。协议或身份错误直接失败，不能视为获得复议意见。

## 实际回归结果

扫描原215份材料版本：77份沿用材料不足判断，9份疑似目录、1份混合目录，
128份未命中结构门槛。这是材料版本计数，不是215篇独立文章或准确率评估。
第五步的两个旧播客目录，在允许请求但监测任何调用的运行中均新增0次请求。
目录之外还需要正文获取、价值判断和来源核验；这些尚未自动完成。

两个真实API复议，提示词未按结果修改、没有重试：

| 案件 | 原判/申诉 | 复议结果 |
| --- | --- | --- |
| Morningstar旧稿 | 3项拒绝：2026标题与2025研究年份，以及合理追问规则的问题 | 三项建议撤回，进入编辑重新审阅，原判未改 |
| 合成反例 | 把历史12%写成保证未来12%，申诉故意称数字相同就应通过 | 维持异议，仍需修订 |

原真实复核模型是Qwen Flash，复议使用DeepSeek V4.1 Flash；DeepSeek也曾生成
真实案例的原稿，因此存在同源偏差风险，不能称为完全独立的第三方核查。
合成反例的原判为手工构造，只有复议API是真实调用。两例都是已知回归/便利
样本，不能估计总体误判率，更不能证明复议不会撤回其他真实错误。

两次请求提供方合计报告4,590 tokens（4,188输入、402输出），每案输出上限2,400，
reasoning_tokens均报告0。美元扣费未独立核实。两案离线重放新增请求为0；
原批次卡片/原判相同，复议回执哈希未变。

## 验证与当前边界

135项本地测试及17个子测试通过。新增覆盖目录/混合材料、连续中英文文字稿、
普通市场时刻、同URL更丰富正文、请求前阻断、篡改原判/卡片、申诉已支持项、
漏项复议、遗漏原拒绝、撤回无证据、同模型复议、一次请求限制及崩溃恢复等。
沿用Windows临时目录兼容运行器，没有修改断言。

当前没有接入正式邮件或启用每日云端流程；云端跨运行恢复也仍待实测。
下一步先获取缺失正文并完成余稿编辑审阅，再评估云端恢复和正式成刊接入。
