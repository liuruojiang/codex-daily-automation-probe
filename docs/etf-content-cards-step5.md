# 独立影子批次、累计预算与恢复

`scripts/etf_shadow_run.py` 将生成、引用/数字检查和逐项模型复核串成独立批次。
影子结果始终待编辑审阅；没有发送、生产历史或发布许可接口。正式邮件未改。

## 批次和预算

输入保存的 collection_manifest，或已有 materials JSON。按 canonical URL
保留较丰富的材料版本，优先选可读材料，随后按 material_id 排序取 limit。
这不是投资价值排名，也不能识别所有“篇幅够长但只是目录”的材料。
保留 captured_excerpt 等原有证据级别，不把字符长度当作全文认证。

同一批次绑定源文件SHA256、所选完整材料、run-label、模型、端点、提示词、
协议版本、输入长度和累计上限。材料或配置改变必须另建目录；API密钥不保存。
run.json 是恢复入口，materials.json 保存所选材料快照。目录没有 checkpoint
但已有输出时拒绝覆盖。操作系统文件锁防止同目录并发写入，进程退出后释放。

- max-requests：本批次累计请求预留数，包含生成和复核，恢复不会归零。
- max-reserved-output-tokens：累计预留的 max_completion_tokens。
- max-new-requests：本次命令最多新发多少请求，可在恢复时调整。
- max-input-chars：每次完整请求的字符数上限，超出时保留失败，不静默截断。

这些上限约束请求数和配置的输出长度，不是精确美元预算，也不是输入与输出
总tokens封顶。提供方仍对输入计费，用量缺失或格式异常会明确计入未知请求。
检查不通过的稿件不会调用复核；所有失败保留，不自动重新生成或付费重审。

## 恢复边界

调用前先写持久预留，随后沿用原有一次请求一次回执的 call_json。
本地单篇状态与原始回执相互独立：

| 恢复时状态 | 处理 |
| --- | --- |
| 已解析 | 复用结果，重新执行报告检查 |
| 已预留、回执 completed 且身份一致 | 从回执重新解析，不发新请求 |
| 已预留但无完整回执，或回执损坏 | 结果未知，保留预算，不自动重试 |
| 已知解析失败、证据拒绝、复核拒绝 | 保留失败；继续其他稿件 |

操作系统锁只保护同一目录。JSON和摘要并非数字签名或跨主机事务；调用方必须
恢复完整 run.json、材料和 receipts，不能删除 checkpoint 或另建目录来声称
恢复同一批次。API超时后的实际扣费可能未知，未知请求仍占用累计预留。

输出 responses.json、reviews.json 可直接送入前三步的编辑否决和第四步的
受控修订流程。shadow-report.json 保留模型状态；模型通过仍标 editorial pending。
本脚本不将编辑完成或外部事实核验自动设为通过。

## 本地用法

先配置 ETF_SHADOW_API_URL、ETF_SHADOW_GENERATOR、ETF_SHADOW_REVIEWER；只有
run 模式还需 ETF_SHADOW_API_KEY。密钥仅通过环境变量传入。

```powershell
python scripts/etf_shadow_run.py plan --manifest collection_manifest.json --run-label source-123 --out-dir artifacts-shadow --max-requests 12 --max-reserved-output-tokens 33600 --thinking disabled
python scripts/etf_shadow_run.py run --manifest collection_manifest.json --run-label source-123 --out-dir artifacts-shadow --max-requests 12 --max-reserved-output-tokens 33600 --thinking disabled --max-new-requests 3
# 相同参数继续，累计额度不重置；未知结果不重试。
python scripts/etf_shadow_run.py replay --manifest collection_manifest.json --run-label source-123 --out-dir artifacts-shadow --max-requests 12 --max-reserved-output-tokens 33600 --thinking disabled
```

thinking 参数是提供方特定配置，示例对应已验证的 DeepSeek 网关；其他提供方
不应无条件照搬。plan/replay不发模型请求。存在待处理或拒绝稿件时退出码为1，
不表示所有结果丢失；协议、身份或文件错误仍直接失败。

## 每日工作流草稿

新增 `.github/workflows/etf-shadow-digest.yml`，在正式ETF工作流成功完成后，
验证源运行的仓库、工作流、事件与默认分支，从指定run读取已保存的采集结果。
也支持手动输入生产run ID，以及可选的旧影子run ID恢复记录。

**当前云端只执行 plan/离线重放，不持有模型API密钥，不自动产生付费请求。**
仅有 contents:read 和 actions:read，无邮件或代码写入步骤。状态无论成功或失败
均尝试上传，保留30天；缺失/过期artifact会失败，不能当作成功恢复。
同一source run的并发排队，不取消正在写入的运行。

草稿尚未合并，因此每日云端流程尚未启用。本地真实模型运行及目录恢复已验证；
GitHub下载源产物、上传状态和跨run恢复尚未在托管运行器实际演练。不能把本地
复制目录的恢复结果说成云端已验证。自动付费运行还需要单独验证持久记录和
跨run预算，当前并未提供该路径。

官方接口依据：[download-artifact](https://github.com/actions/download-artifact)、
[workflow_run事件](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_run)。

## 扩大样本的真实结果

同一2026-10-20生产采集快照（run 39347818936）中，先冻结选择方法：
此前未出现于模型响应记录的不同canonical URL，材料可读、捕获长度1000–9000
字符，按material_id取前6篇，另加2条实际材料不足样本。按3、3、6次请求分段。
生成及复核提示词在本次试验中保持不变，没有按结果重抽。

| 阶段 | 结果 |
| --- | --- |
| 六篇生成 | 均返回可解析、机械检查通过的卡片 |
| 六篇Qwen复核 | 5篇通过，1篇要求修订 |
| 助手定点来源回核 | 又否决1篇模型通过稿，其余4篇仍待编辑审阅 |
| 两条材料不足 | 未调用模型 |

6次DeepSeek生成、6次Qwen复核，总预留12次、33,600输出tokens。提供方报告
29,150实际总tokens（22,575输入、6,575输出），没有缺失用量或未知结果。
生成使用10,747总tokens，复核使用18,403；美元扣费未独立核实。

两个需要保留的负面结果：

1. 趋势跟踪播客稿将“仅捕获宣传与目录”的编辑观察写进原文限制，Qwen漏过，
   助手已否决。另一个养老金播客也主要是议程；忠实概述目录不等于得到实质观点。
2. Morningstar提款率稿被拒绝，部分理由疑似误拒：源标题本来就有2026，正文
   明确注明2025研究；询问未披露支出调整规则的问题也因缺少答案被拒，与提示词
   允许合理未知问题的原则冲突。保留拒绝，未强制通过或改提示词重抽结果。

这些是有选择条件的便利样本，不是盲测或总体准确率。捕获内容不等于全文；
忠实复述也不能证明来源的金融、税务或研究结论正确。

## 验证

109项本地回归测试和15个子测试通过。新增20项覆盖累计请求/输出预留、每次
命令上限、完成回执恢复、缺失/损坏回执、网络未知、单篇失败继续、坏JSON、
引用数字拒绝、完整复核、输入不截断、配置隔离、文件锁和只读云端边界。
沿用Windows临时目录兼容运行器，未修改断言。Linux行为由仓库CI另行检查。

真实批次在额度用尽后以 max-new-requests=99 继续运行，新增请求为0。复制
完整目录到新路径，禁用请求后重放，与原报告完全一致，12份回执哈希未变。
未知结果路径只进行合成故障测试，本次真实提供方请求没有发生故障。

下一步先改进材料类型识别与可审计的误拒绝处理，再演练云端恢复。尚不具备
取消编辑审阅、自动修订至通过或切入正式邮件的依据。
