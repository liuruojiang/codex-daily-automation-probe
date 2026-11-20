# 11 月 20 日微盘资料维护

## 批准后的验收

用户批准精确迁移清单 `235eeee4b80303a6c4c62a67b60effbd60b2380fddb57c9b35e57b71ba5f3266`。
原生流程从 11 月 16 日前序认证重放，生成股本 CSV `d1b95ea5b542cbeda6aa88f03f9c27d0941696ef86910841de20d5a2833a379f`，
保留原 CSV 字节前缀和原观察时钟。原 11 月 18 日认证和旧指针已归档，
原闭包归档 SHA-256 为 `315b315c776b7b1e221a2155366c5216c9bbc9c36b6e93b5d0bc1bb7f71df36d`。

普通维护入口读回 300/300 只、903/903 项，
校验 58,565 个文件，复用 902 个原成功文件映射。
公告和当前 ST 名称原观察时钟保留。独立 scoped 读取器从品渥食品的
11 月 16 日真实前序凭证接收来源，300 只来源均通过原生复验。
读取器仍拒绝向已认证 11 月 18 日旧股本的输入直接导入改写。
独立读回回执 SHA-256 为 `28510b41fa076b6ad24bfe3b2fe8ce31c80460cf11f8d72ef3561633bcbfb1e2`。

11 月 19 日收盘来源包等于 GitHub 上完整下载读回的内容。
其 SHA-256 为 `570e26a661734bdadbec63a3841b6f7eb52a1691454e00b26569c6edf7a2057f`，
原始 manifest SHA-256 为 `622aa319463d8d60527c39d5bb5b4f3da063ed5d402e6c5b8c877188c40b0338`。
[正式来源包](https://github.com/liuruojiang/codex-daily-automation-probe/releases/download/microcap-local-sources-20261119-d09b53af/microcap-local-sources-close-20261119-d09b53af.zip)

11 月 19 日成员检查点维持同一成员期，100 只均在维护池内且与当前 ST 名单零交集。
这次发布的范围是来源资料。完整调仓状态的日期仍按原验收单独记录。

## 初次读回与迁移阻塞记录

11 月 19 日收盘目标已验 299/300 只、902/903 项。
复核 58,506 个绑定文件并逐项复用 898 项原成功文件映射。
公告和当前 ST 名称原观察时间分别为 `2026-11-20T02:33:15.633952+00:00`、`2026-11-20T02:36:58.632346+00:00`。

[公告修复](https://github.com/liuruojiang/microcap/pull/114)绑定 11 月 18 日两份 1,420 条原始目录和五份公告。
两份目录的原生 ST 事件一致。[股本修复](https://github.com/liuruojiang/microcap/pull/115)
绑定 002858、300535 的晚到结构记录。[运行配置](https://github.com/liuruojiang/codex-daily-automation-probe/pull/232)
绑定原生资料生产者 `d09b53af78767370a7ecb2bf4ebe6e3527d2eacc` 和调仓消费者 `df384741530a6fcd27c7526057b552c5ce138d53`，五项 CI 通过。

仍被原生门槛拦截的阶段

- `300892-share` — Unchanged share tail: dated window: previously certified boundary conflicts; exact historical migration required

品渥食品 [发行人公告](https://money.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php?id=12713104&stockid=300892)
说明 11 月 17 日注销 996,252 股。最新逐日市值和新浪股本历史都支持 99,003,748 股。
旧 11 月 18 日认证延用 100,000,000 股。以 11 月 16 日已认证状态为锚，
只读原生回放得到候选哈希 `d1b95ea5b542cbeda6aa88f03f9c27d0941696ef86910841de20d5a2833a379f`。
精确迁移清单哈希为 `235eeee4b80303a6c4c62a67b60effbd60b2380fddb57c9b35e57b71ba5f3266`，等待授权。

正式来源指针日期为 11 月 18 日，哈希 `e3b34e8a96bc9522814d6d1915459d024c41810389227621ea01e8ff515542a1`。
[11 月 19 日的 15:10 调仓计划](https://github.com/liuruojiang/codex-daily-automation-probe/actions/runs/44477609574)
显示无漏调仓。后续同日成员工件 `14510385689` 已通过读取政策，
100 只均在 300 只范围内且 ST 交集为零。下一调仓日为 11 月 26 日。
最近一次已核验的调仓完整状态日期为 11 月 12 日，工件 `13969276381`。
本次没有新增来源包、重派调仓、发送邮件或下单。
