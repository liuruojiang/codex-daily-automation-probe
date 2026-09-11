# 每周股票时间序列动量边界监控

这是一个独立的、只读的 GitHub Actions 研究监控任务。它每周一北京时间 09:00（GitHub cron 的 `01:00 UTC`）运行一次，生成 JSON、纯文本和 HTML 报告，然后调用本仓库已有的 `scripts/send_report.py` 发邮件。它不新增 SMTP 逻辑，也不生成仓位、订单或生产信号。

## 监控内容

### 核心论文式指标

- Shiller CAPE：市场实际价格除以约十年实际盈利均值。
- 市场股息率：Shiller 月度股息除以市场价格。
- 期限利差公开代理：优先使用 FRED `GS10 - TB3MS`，即10年国债收益率减3个月国债收益率；FRED 图表端点不可用时，使用官方 Federal Reserve H.15 的10年和3个月恒定期限收益率，并在邮件中披露 fallback。论文使用 Ibbotson 长期债券利率减短期 Treasury；因此本任务将代理明确标为 proxy，不冒充论文原始序列。
- 每个变量先取12个月移动平均，再按滚动10年和20年最小值/最大值映射到 `[-1, +1]`。边界分数为两个缩放分量的平方和，范围 `0—2`。
- 变量位于滚动历史第10百分位以下或第90百分位以上时标记为 `extreme`。这只是论文式监控分类，不是交易阈值。

### 补充状态指标

- S&P 500 价格指数的1、3、6、12个月动量。
- 20日年化实现波动率，以及12个月趋势与最近1个月方向相反的反转标记。
- VIX 水平及其一年分位数。
- Baa 公司债收益率减10年国债收益率（`BAA10Y`）及其一年分位数。
- FRED 10Y-3M 与10Y-2Y 作为期限利差的日频方向参考；FRED 不可用时由官方 H.15 10Y-3M/10Y-2Y 计算。

补充指标用于解释状态，不会改变核心分数，也不会产生交易建议。

## 邮件发送

工作流复用本仓库日常日报已经在使用的 `scripts/send_report.py` 和以下 Actions Secrets：

| Secret | 作用 |
|---|---|
| `MAIL_SERVER` | 现有邮件服务器 |
| `MAIL_PORT` | 现有邮件端口 |
| `MAIL_USERNAME` | 现有邮件登录用户名 |
| `MAIL_PASSWORD` | 现有邮件服务密码 |
| `MAIL_FROM` | 发件地址 |
| `MAIL_TO` | 收件地址 |
| `MAIL_USE_SSL` | 现有 SSL 开关 |

无需新增 `SMTP_HOST`、`SMTP_USER` 或其他第二套邮件 Secrets。监控脚本只负责写出统一的 `artifacts/metadata.json`；真正的发送、HTML/纯文本 MIME 组装和认证仍由既有日报发送器完成。

## 本地复现

```powershell
python -m pip install -r monitoring/equity_momentum_boundary/requirements.txt
python -m unittest discover monitoring/equity_momentum_boundary -p "test_*.py"
python monitoring/equity_momentum_boundary/monitor.py --output-dir artifacts/equity_momentum_boundary
```

报告会写入 `artifacts/equity_momentum_boundary/`。GitHub Actions 会把该目录作为保留期90天的构件上传。

在 GitHub Actions 中，工作流将输出目录设为 `artifacts/`，随后执行 `python scripts/send_report.py artifacts/metadata.json`，从而沿用现有日报的邮件通道。手动触发时可将 `send_email` 设为 `false`，只收集并验证报告。

## 数据边界

- Shiller 官网的 CAPE 和股息数据可能在最近月份出现尚未填充的盈利/股息字段；报告会保留数据日期并标注缺口。
- FRED 图表端点在某些 GitHub runner 网络环境可能超时；期限利差会切换到官方 H.15，S&P 500 与 VIX 会切换到明确标注为 proxy 的 Yahoo Finance chart API。Baa−10Y 没有配置第二个来源时会显示缺失，不会静默填充。
- 当前读数是研究描述，论文样本到2024年12月；本监控对2024年以后是样本外延伸。
- 任务失败、数据缺失或代理口径不一致时，邮件会显示具体警告；报告不会用旧值静默填充。
