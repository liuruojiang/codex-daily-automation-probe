# ETF 改造第一步：有证据的中文内容卡片（影子版）

正式日报当前的 `etf_chinese_fact` 返回固定占位说明，普通文章的中文正文
细节函数返回空字符串。第一步建立一个独立的中文理解与回放入口，供逐条
核对原文后再决定如何接入正式成刊。生产采集、选文、行情、发送和去重历史
不依赖此入口。没有修改定时工作流，也没有把影子卡片当成已发送内容。

## 内容与验收边界

- 材料包含标题、原始 URL、发现来源、日期、证据范围和有编号的完整段落。
  哈希绑定全部字段。正文、来源或范围改变，旧卡片即失效。
- 旧 `collection_manifest.json` 的 `summary` 统一标为 `captured_excerpt`。
  不因抓过网页就推断获得全文，不用补送队列的中文解读冒充原文。
- 每条中文事实、原文限定条件都附逐字引用及段落编号。数字仅允许沿用
  所引片段中的写法；方向、单位换算、文字数字和语义仍需人工/模型复核。
- 资料不足、登录页、仅标题和聚合列表不生成事实。聚合列表先拆子文章，
  本步不把多个文章混为一篇。材料就绪门槛复用现有证据检测，不代表相关性。
- 具体缺失信息放在“我们的待核对问题”，与作者陈述分开。
- `evidence_linked_pending_review` 仅表示结构、引用和部分数字检查通过。
  **它不证明中文翻译正确、结论完整、因果成立、数字单位正确或观点已验证。**
  反例测试刻意保留“引用正确、数字相同但语义错误”的样本，防止把机械校验
  当成事实验证。首次真实内容应逐条复核，尚不能据此替换正式邮件。

## 离线回放

在仓库根目录运行（Python 3.11+，沿用项目已有运行依赖）：

```powershell
python scripts/etf_content_cards.py prepare --manifest artifacts-original/collection_manifest.json --out-dir artifacts-cards
python scripts/etf_content_cards.py replay --materials artifacts-cards/materials.json --responses responses.json --out-dir artifacts-cards
```

`prepare` 输出 `materials.json`、绑定输入文件哈希/运行版本的 `provenance.json`
和可提交给模型的 `requests.json`。同时保留原始候选及出刊时已补全的材料；
相同材料去重，同 URL 的不同内容版本保留。`responses.json` 是以完整 `material_id` 为键、
结构化模型/编辑回应为值的对象。响应结构由 `SYSTEM_PROMPT` 定义。此方式可
先人工核对小样本，不需要 API 密钥。离线回应必须标明真实制作方式，不能
将编辑整理的样本宣称为在线 API 生成。

`replay` 输出 `cards.json` 与 `preview.html`。缺回应显示等待，证据不匹配显示
拒绝并以非零状态退出；不静默丢弃失败条目。输出不是发信 metadata，不能
直接交给 `send_report.py`。HTML 对全部来源与模型文本转义。

## 可选的显式模型调用

仓库当前没有现成的 ETF 模型接入；本次没有假定或复制任何账户密钥。
`generate` 接受支持 Chat Completions JSON 模式的端点。必须在执行环境配置
`ETF_CARD_API_URL`（完整 HTTPS 地址）、`ETF_CARD_MODEL`、`ETF_CARD_API_KEY`。
不设默认付费模型，不将密钥写入源码、命令参数或日志。

```powershell
python scripts/etf_content_cards.py generate --materials artifacts-cards/materials.json --out-dir artifacts-cards --max-calls 1 --max-input-chars 24000 --max-output-tokens 1600
```

默认最多处理一个待完成材料；输入字符与输出 token 另有限制。请求上限
**不是金额预算**，真实价格、模型可用性和 JSON 参数兼容性须在选定服务后验证。
超限材料拒绝调用而不裁掉正文。上游模型可能因思考 token 等原因截断输出，
此时拒绝解析；不能把不完整生成当成功。

每次请求先独占建立回执。逻辑键包含材料、完整提示词、模型、端点、版本和
输出限制。成功响应和 usage 原样缓存，重复运行复用。超时/连接异常标为
unknown，不自动重试；需核查提供方调用记录，再明确处置回执。完成但格式
错误的响应也保留，避免重跑重复付费。此处是本地回执，不声称解决 Actions
跨运行持久化；未配置计划运行、服务端任务队列、并发批量生成或自动重试。

API 格式参考：
https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create

## 验证

```powershell
python -m pytest -p no:cacheprovider tests/test_etf_content_cards.py tests/test_etf_evidence_quality.py tests/test_etf_evidence_adversarial.py tests/test_etf_build_adversarial.py tests/test_validate_etf_delivery.py -q
```

覆盖旧材料不升级、原文版本绑定、跨文章引用、造数/符号变化、缺引用、模型
格式错误、HTML 注入、超时未知状态、成功回执复用、离线运行不联网及不写
投递历史。当前仅接通适配器；没有真实 API 凭据时，mock 测试不等于在线验收。

下一步先用真实日报候选对照中文内容，核对语义、遗漏和成本；通过后才把
已审阅卡片接入 `etf_chinese_fact`，并保留缺材料时的明确降级说明。

## 本次验证记录（2026-10-20）

实现基于主分支 `b69e296d21b63f40d5b6e46726c555a85b1cc5b0`。
回放使用正式运行 `39347818936` 的不可变 artifact（运行代码为
`dc34eab7dcd3d44a487bc18ff3807472fdcac14b`），压缩包 SHA-256 为
`2a7f7b9c124aedb6a37647af7622784cf76eabf4d15968b3ac8786027b3f56b2`。
226 个原始候选和 22 个已出刊材料快照合并后得到 215 个不同材料记录，
其中 138 个通过可读材料门槛、77 个材料不足或需拆分。这些数字是材料版本数，
不是独立文章数，也不是确认遗漏数。

从留存材料逐项整理 TIPS 提款、黄金基金费用、电力市场 Hawkes 研究三个
中文样本，通过引用检查，并保留聚合列表及薄摘要两个降级样本。样本为
本次对话的编辑整理，未使用在线 API，未独立重验金融事实。TIPS 原文在
旧采集器中已被截断，因此仍标为节选，不能声称覆盖整篇文章。

上述针对性命令共 57 项测试通过，另有 15 个子测试通过。Windows Python
3.14 的临时目录 0700 模式导致沙箱账户丢失继承访问权限；本地测试运行器
仅将目录创建模式恢复为通常的继承权限，未改测试断言或产品代码。模型
适配器通过 mock 响应验证，没有在线模型调用或费用/质量实测。
