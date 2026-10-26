# 第21步：新生产快照的小批量影子验收

本轮使用2026-10-21已成功完成的正式采集，不触发采集或邮件发送。新记录与10月20日旧10篇核对稿、5份后补研究分开保存；正式发送逻辑未切换。

## 固定来源

- production run：39518483125，attempt 1，main，schedule，workflow `.github/workflows/etf-allocation-digest.yml`。
- 生产代码：`b69e296d21b63f40d5b6e46726c555a85b1cc5b0`。
- artifact：12308593073，`us-etf-allocation-digest`，ZIP SHA256 `380c653419854065e9a9c936adee355f7b9d9bc1aaf57ce42ee33d56cdcd818d`。
- cutoff：2026-10-20T23:59:26.975166+00:00；正文SHA256 `ab81c66b0f27481fc5701f553d2ada8b6e43c2470dbce660cf27b6c950ebaab3`。
- 32条已选、269条候选、1001条运行前历史；行情截至10月20日。原审计仍为FAILED，16项警报、132项缺口。

`config/etf-fresh-shadow-record.json`保存生产者及制品身份、三个输入文件的字节摘要、独立新登记、冻结协议与初始模型用量。旧登记和第20步研究索引的摘要用于检测历史输入漂移，不改写原记录。

## 范围与核对

只选择已选、具有本轮原始捕获、符合现有日期及历史门槛的两篇摘要：

| 来源 | 范围 | 编辑检查 |
| --- | --- | --- |
| arXiv 2610.25495，LOBGrad | 捕获摘要；理论假设、三个实验标的与消融比较均保留作者归属 | 10项 |
| arXiv 2610.24558，Optimal Trading Filters | 捕获摘要；无摩擦和有交易成本结论区分，未声称实测利润 | 6项 |

两条RSS日期是交叉收录公告字段，不能当作首次提交或首次发表日期。捕获材料包含重复摘要和页面附带文字；为维持原捕获身份未删改。压缩引句时保留不同句子ID，展开后与实际已复核卡片逐字相同。重复引句不是独立研究或复现。追问中的未知仅指本次摘要未提供，不能推断全文没有回答。未核验全文、代码、数据、推导或外部金融事实。

初始协议：deepseek-v4.1-flash生成、qwen-flash完整复核，thinking disabled；4次初始请求/11200输出token预留。仅在实际改稿时才允许额外一次3200输出token完整复核，总上限5次/14400。本轮实际4次、供应商报告9741总token、未知请求0、改稿追加0。16项编辑检查独立核对原句；没有因模型通过而跳过编辑。

## 确定性回放

| 案例 | 替换 | 解释 |
| --- | --- | --- |
| 新快照＋新2篇登记 | 2 | 其余30条保留原文，保留原警报与行情 |
| 新快照＋旧10篇登记 | 0 | 登记快照stale；实际已选链接没有重合，不能将此当同链接拒绝证据 |
| 新快照＋无登记 | 0 | 保留完整原邮件 |
| 模拟旧登记链接对齐新已选链接 | 0 | registry_snapshot_mismatch |
| 模拟再将旧登记快照改绑本轮 | 0 | stale_material，旧材料身份仍不匹配 |

所有零替换案例必须保持原正文摘要；新卡也须经过原材料、模型、逐项编辑、日期/历史及唯一已选正文块门槛。坏输入、历史记录漂移、重复登记或覆盖计数变化导致验收报错，不能静默略过。实际邮件替换继续复用既有严格适配器，其他邮件区域及原链接保持原样。没有发送器metadata、历史写入或模型密钥输出。

新增独立PR job `etf-fresh-shadow-preview`：只读GitHub元数据验证run路径/事件/attempt/仓库及head仓库身份、main分支、成功状态、artifact生产者与digest；凭证不跟随重定向。按artifact ID下载后，三个输入字节摘要再次验证，再离线回放上述案例。制品保留30天。CI不调用模型、不发送；旧快照与后补研究验收任务保留。

本地生成：

```powershell
python scripts/etf_fresh_shadow.py replay --record config/etf-fresh-shadow-record.json --source-dir ../etf-source-20261021 --old-registry config/etf-shadow-reviewed-registry.json --old-index config/etf-research-preview-index.json --out-dir artifacts-step21/replay
```

HTML提供完整未发送候选邮件及逐条覆盖清单；只做静态结构/链接检查，未完成浏览器或邮件客户端视觉验收。本轮并未证明32条全部可发送，也未启用每日新解读。
