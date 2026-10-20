# 旧稿受控修订与重新复查

三篇已知问题稿件现在可在保留原稿、引用、旧否决和修改理由的情况下修订。
新脚本 `scripts/etf_card_repair.py` 完全离线，不生成整篇、不发送邮件。
这是影子流程，尚未改变生产采集、排期、邮件渲染或历史记录。

## 修订协议

输入原始 materials、responses、plans，以及原始 editorial-vetoes。
plans 按 material_id 索引，每条严格包含：

- material_id、parent_card_hash：绑定原文版本和旧卡片。
- parent_veto_hash：有原否决时必须匹配；没有时为 null。
- issues：非空数组，每项包含唯一 id 和 reason_zh。
- edits：非空数组，每项包含 field、value、issue_ids。

仅允许替换 title_zh、facts、limitations、questions 四个字段。每个字段最多
修改一次，每个问题必须对应修改，不接受空操作。原编辑否决的完整理由必须
作为一个待解决问题保留，不能用其他问题替代。事实与限制可用 evidence_ids
选择原句，由程序复制引用；不可改 material_id 或提升材料完整程度。
新卡仍执行引用、数字、术语和完整语义检查。修订操作本身不表示检查通过。

revision-records.json 保存两版卡片、两版哈希、原始否决、计划、字段差异和
两版机械检查结果；revision_id 对整份记录计算摘要。重放时重建并比对记录。
同一输出目录拒绝覆盖不同修订，也拒绝覆盖缺少修订记录的既有 responses。
这是单进程本地审计记录，并非签名、不可篡改数据库或跨运行的否决登记服务。
调用方必须提供完整的原始否决文件，不能将删除输入文件当作解决问题。

## 旧否决如何处理

旧模型结果不继承到新稿。即使仅修改问题，新稿也需要独立绑定新 card_hash
的全部模型复核。旧否决保留在修订记录，不直接套到新版，也不自动消失。

新版需要 dispositions，按 material_id 索引，每条仅包含 revision_id、
card_hash、checks；checks 每项仅包含 issue_id、decision、reason_zh。
所有原问题必须恰好覆盖一次，decision 只允许 resolved 或 needs_revision。
理由由编辑在对照新稿与来源后记录，不由“发生过修改”推导。

最终状态：

- 任一引用、术语或模型检查失败：保留原失败状态，编辑不能覆盖。
- 新模型通过但编辑未复查：awaiting_editorial_recheck。
- 编辑仍要求修订：editorial_revision_required。
- 新模型通过且全部原问题经复查解决：shadow_review_passed。

shadow_review_passed 只代表本次来源忠实度审阅完成，不能授权发布或声称外部
事实已验证。编辑记录是本次助手的来源核对，不是用户本人审阅。

```powershell
python scripts/etf_card_repair.py --materials materials.json --responses parent-responses.json --plans plans.json --editorial-vetoes parent-vetoes.json --out-dir artifacts-repair
# 上一步非零退出表示尚未完成全部门槛；保留修订稿供独立复核。
python scripts/etf_semantic_review.py review --materials materials.json --responses artifacts-repair/responses.json --out-dir artifacts-review --max-calls 3
python scripts/etf_card_repair.py --materials materials.json --responses parent-responses.json --plans plans.json --editorial-vetoes parent-vetoes.json --reviews artifacts-review/reviews.json --dispositions editorial-dispositions.json --out-dir artifacts-repair
```

复核 API 配置、请求回执、预算、失败不自动重试规则沿用第三步文档。

## 本次实际回归

材料及原稿与第三步完全相同，由助手明确编辑两类字段：limitations、questions。
facts 和标题未改；未重新调用生成模型。

| 旧稿 | 修改 | 新版结果 |
| --- | --- | --- |
| TIPS | 限定条件缩小为回报数据拼接方法，补引 p1.s106；删除可能重复已回答情景的问题 | 引用、Qwen、助手编辑复查通过 |
| GLD/GLDM | 补引 p1.s35–s36，限定作者的美国 IRS 语境；删除取整矛盾问题，保留官方核验与账户适用性问题 | 三层检查通过 |
| Hawkes | 删除作者未提出的市场覆盖判断，改为编辑待核对问题；保留原状态划分方法 | 三层检查通过 |

三次全新的 Qwen Flash 请求，提供方报告共 16,670 tokens（14,127 输入、
2,543 输出），没有失败重试；美元扣费未独立核实。保留原始回执与说明。
Qwen 的 GLD facts.1 复核理由擅自将 September 2026 fact sheet 写成具体日，
原文对应句和卡片只写月份。助手核对并记录此缺陷，没有将模型说明写回正文。
这也说明模型通过不能替代编辑逐项核对。

三篇均为已知问题回归样本，不是盲测或整体准确率。TIPS 仍是截断节选；
法规与基金数字尚未对照官方资料独立核验。已知原文错误也可能被忠实复述。
本地最终修订报告在禁止 call_json 的情况下成功重放，不新增 API 请求。

## 验证与后续

89 项针对性测试及 15 个子测试通过，覆盖旧稿/否决绑定、错用旧复核、
遗漏编辑问题、空修改、错误数字、编辑不能覆盖拒绝、记录篡改及目录隔离。
沿用 Windows 临时目录权限兼容运行器，断言未更改。

下一步可设计独立的每日影子运行与更广样本评估：把材料、请求回执、修订、
编辑结果和失败原因持久保存，并先验证预算和恢复路径。正式邮件仍需单独
评估接入；当前没有新增工作流或计划任务。
