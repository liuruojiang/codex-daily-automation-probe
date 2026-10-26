# 邮件编号映射与专题记录（离线本地模块）

入口：`scripts/etf_research_workspace.py`。仅依赖 Python 标准库和同目录 `etf_preflight.canonical`。不联网、不调模型、不发送邮件、不改定时任务。此文件位于改造仓库，**尚未安装到 D 盘原任务**；生产接入者必须先部署到任务实际可读的位置。

## 编号先从权威原正文建立

上游先确认正式 run 的可信来源、manifest 与正文一致，再传入包含 `metadata.json` 和 `collection_manifest.json` 的目录。模块核对正文 SHA256、标题日期、正文首行与 subject、selected URL 集合，逐块读取正文 H2 分节、H3 原序号及明确的文章链接。它不会把后台 `selected_items` 的第几项当成邮件原序号。文件内部一致性检查不能代替上游对 run 的真实性校验。

```powershell
python scripts/etf_research_workspace.py map-mail --source-dir SOURCE_DIR
python scripts/etf_research_workspace.py register-mail --source-dir SOURCE_DIR --workspace STATE_DIR --operation-id register-RUN
```

`map-mail` 只读输出 JSON，可由展示层调用 `mail_mapping(metadata, manifest)`。每条包含 `section / number / title / url / original_heading / item_id / global_number / global_id`。真实离线 fresh fixture 中，LOBGrad 是“量化策略影响·1”，稳定编号 `E20261021-39518483125-003`；Optimal Trading Filters 是“量化策略影响·15”，稳定编号 `E20261021-39518483125-017`。稳定编号按冻结原正文顺序分配，后续摘要重排不得重新分配。

摘要导入 JSON：

```json
{
  "mail_id": "register-mail 返回的 mail_id",
  "report_date": "2026-10-21",
  "run_id": "39518483125",
  "version": "v1",
  "text": "用户实际看到的完整总结文本（包括编号和稳定ID）",
  "items": [{
    "section": "量化策略影响",
    "number": "15",
    "title": "Optimal Trading Filters and the Value of Predictive Signals",
    "url": "https://arxiv.org/abs/2610.24558"
  }]
}
```

```powershell
python scripts/etf_research_workspace.py import-summary --workspace STATE_DIR --input summary.json --operation-id summary-RUN-v1
```

必须先注册原邮件，再导入实际展示的摘要快照；模块逐条验证原分节、原序号、原标题、URL。摘要可以选取原邮件的子集。`display_number` 仅表示该摘要快照中的展示位置；展示层必须据返回的行渲染，不能自行另排编号。文本快照由调用者提供，模块不理解或校验任意自然语言文本是否与排列一致。

同一日期/run/version 不可覆盖；改文或重排必须新建 version。`item_id` 绑定日期、run、原分节、原序号、规范 URL，跨摘要版本重排保持不变；摘要 ID 另绑定展示版本。旧快照完整保留。

## 点选与授权

所有 `--input` 均为 UTF-8 JSON。`resolve` 支持精确 `summary_id` 加 `display_number`、`global_id`、`item_id`、URL，或者日期/run/version 加原 `section/number`。跨日或多版本命中超过一条时明确报歧义；不猜“最新的一条”。示例 selector：

```json
{"summary_id":"s_...","global_id":"E20261021-39518483125-017"}
```

```powershell
python scripts/etf_research_workspace.py resolve --workspace STATE_DIR --input selection.json
```

`create-topic` 输入：

```json
{
  "selection": {"summary_id":"s_...","display_number":1},
  "question": "预测信号扣除换手和冲击成本后是否仍有价值？",
  "request": {"id":"用户消息唯一ID","text":"用户要求深入这一条的原话","source":"user","intent":"deep_read"}
}
```

它保存精确摘要与文章锚点，返回稳定 `topic_id`，默认 `once`。相同主题与问题不重复建档；已存在时沿用原 topic_id。

| 状态 | 可执行行为 | 变更要求 |
|---|---|---|
| `once` | 一次交互深读；成功写入一个判断版本后不再进入待处理队列 | 再次深入须新用户请求，`set-status once` |
| `wait` | 暂停；交互和定时写入都拒绝 | 用户明确恢复；不能自动转回 |
| `tracked` | 可由定时任务持续更新 | 明确“继续跟踪”的新用户原话和请求 ID |

`set-status` 输入：

```json
{
  "topic_id":"t_...",
  "expected_state":"once",
  "expected_event_count":1,
  "state":"tracked",
  "request":{"id":"新的用户消息ID","text":"以后继续跟踪这一专题","source":"user","intent":"track"}
}
```

`state=wait/once` 分别要求 `intent=wait/once`。请求 ID 不可在该专题里重复消费。状态与事件数量一起做并发比较；即使 once→wait→once 回到同名状态，旧授权也不能覆盖新决定。请求来源由调用者提供，CLI 记录并核对字段，不能独立认证消息确实来自用户。

```powershell
python scripts/etf_research_workspace.py create-topic --workspace STATE_DIR --input topic.json --operation-id pick-MESSAGE
python scripts/etf_research_workspace.py set-status --workspace STATE_DIR --input authorization.json --operation-id authorize-MESSAGE
python scripts/etf_research_workspace.py pending --workspace STATE_DIR
python scripts/etf_research_workspace.py show --workspace STATE_DIR
```

`pending` 分开返回未完成 `once` 和全部 `tracked` 的 topic_id 数组；wait 和已完成 once 不返回。专题队列独立于邮件 run 去重，所以没有新邮件仍可处理明确授权的专题。`once` 队列供交互请求处理；定时执行只能处理 `tracked`。更新周期和定时调度由上游负责，模块不会自己启动任务。

## 证据与判断的追加版本

`record-revision` 输入：

```json
{
  "topic_id":"t_...",
  "expected_version":0,
  "expected_event_count":1,
  "execution_mode":"interactive",
  "judgment_zh":"基于目前摘要的有限判断。",
  "change_zh":"首次建立基线。",
  "open_questions":["缺少独立复现和净成本数据。"],
  "evidence":[{
    "source_url":"https://arxiv.org/abs/2610.24558",
    "source_version":"v1; 2026-10-21 capture",
    "capture_sha256":"capture_text 的 UTF-8 字节 SHA256（64位小写十六进制）",
    "capture_text":"实际读取并留存的原始摘要或正文文本，不能填自写总结",
    "scope":"abstract",
    "family":"arxiv:2610.24558",
    "stance":"uncertain",
    "note_zh":"作者研究线索；公开摘要不能证明实际策略净收益。"
  }],
  "retired_evidence":{}
}
```

```powershell
python scripts/etf_research_workspace.py record-revision --workspace STATE_DIR --input revision.json --operation-id research-TOPIC-REQUEST-VERSION
```

scope 可选 `metadata/excerpt/abstract/fulltext`，stance 可选 `support/oppose/uncertain`。capture_text 全量保存在该版本中，SHA256 必须与实际文本一致；同 URL 新文本形成新的 evidence_id。原 PDF/网页文件仍由上游按需保存，capture hash 指留存文本，不指原 PDF 二进制。判断、变更原因、未决问题、证据、证据退场原因和原授权引用都按版本追加，以父版本哈希连接。

`family` 必须按原研究/原数据谱系填写；同一研究的转载、产品页或镜像不能被当成多个独立研究。模块按声明的 family 去重计数，**不自动判定谱系是否真实，也不把两条 URL 当作独立证实**。深度综合的共识、分歧、反例和实际读取范围仍需研究者核验；保存模块不能证明内容写得深或结论正确。

新版本应携带仍适用的已有证据。删去上一版本的 evidence_id 时，`retired_evidence` 必须逐项写明原因；旧证据和旧判断不会删除。更新要求当前 `expected_version` 和 `expected_event_count`，禁止陈旧研究结果越过用户刚改的授权。scheduled 模式只有 tracked 可写；一次深读完成不自动升级。

## 重试与边界

所有写操作必须提供稳定 operation-id。相同 ID、相同输入重试返回原收据，不重复写入；同 ID 不同输入拒绝。写入使用进程级 OS 锁、同目录临时文件、flush/fsync 与原子替换；映射、专题、版本和幂等收据一次提交。锁竞争直接失败，进程崩溃由 OS 释放锁。提交前失败维持原状态和 pending 队列；提交后丢失响应时以原 operation-id 重试。孤立临时文件不视为已提交，也不自动清理未知文件。

这些保证针对正常进程崩溃和原子替换语义；不能承诺硬盘损坏或断电后的绝对持久性。Windows 没有 POSIX 目录 fsync。单 JSON 文件适用于轻量本地记录，历史较大时需要另行迁移到事务数据库；本模块不会压缩或截断历史。不要手工覆盖 workspace.json。旧任务已存在的深读成果不自动导入，必须核对原来源后显式注册和追加。

## 离线验证

`python -m pytest -p no:cacheprovider tests/test_etf_research_workspace.py -q`

覆盖实际 fresh fixture 的量化1/15映射、数组重排、摘要重排、跨日歧义、不可变快照、一次授权消费、暂停/跟踪授权、授权 ABA、证据文本哈希、证据谱系计数、反例保留、陈旧版本拒绝、锁竞争、真实子进程崩溃、提交前失败、提交后重试和 CLI。不会联网或发送。
