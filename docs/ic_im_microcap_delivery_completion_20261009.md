# 日报最后交付门禁与 Put 展示补齐（2026-10-09）

本次承接 PR #137。策略固定提交仍为 `408c9badea5351ae7c0021f9b1e9adfdb804d554`，fix11 参数、日期身份、账本规则和正常发送时点不变。

## 真实云端验收

main `06767dbb1d61cdc6f25f0cbededef52db155eae4` 的 [run 37911491710](https://github.com/liuruojiang/codex-daily-automation-probe/actions/runs/37911491710) 实际完成：隔离恢复正式10/08 seq10账本、正式close producer、正常digest/marker CLI、SMTP TLS/auth/MAIL/RCPT/RSET。SMTP没有DATA，`message_sent=false`；未上传正式ledger、send intent或delivered marker。

真实结果为 `close_confirmed` / 2026-10-09 / 下一实际交易日2026-10-12 / seq11 / `b1568e9bd5905a185c9f085c60e3dc6dbc20a6ccb8719da0f9a9a7851a2a98fc`，双腿fix11。恐慌31.12、数据日10/09、状态same_day_post_close。PE/PB与中债源当时只有10/08数据，正式producer明示代理/冻结回退；IV因20分钟容忍无法核验，明确N/A。不能把这些字段称为当日真实PE/PB/利率或有效IV。

readiness工件11607311463为26,142字节，SHA-256 `2cc4878d015083ee9b96f05253727f4a833b4cbe990e0ef5470ed77c30d3fa3e`。76项独立读回通过。新journal未作为工件上传，因此仅说明隔离producer的seq11结果与附件一致，不冒称已下载重算新链或更新正式账本。

## 微盘 CI 零跳过

原PR137的微盘CI为482 passed / 1 skipped；跳过项属于本地累计17文件：`test_real_v23_written_nav_and_signal_identity_when_available`。原因是disposable策略checkout没有正式v2.3 CSV，绿色job不等于零跳过验收。

补齐今日whole manifest通过的完整NAV（3990行）与1行收盘信号，冻结原字节、源manifest及SHA provenance，只复制到disposable regression checkout。实际测试环境使用正式automation源码、无字节码/pytest缓存、60秒硬限及JUnit零跳过门禁；补齐原遗漏的v2.5 realtime artifact invalidation文件。生产与readiness不读取这些fixture。原跳过文件在隔离de6策略checkout实际13 passed / 0 skipped。

## Put 固定展示

从10/09新日报起，在正文和HTML分别新增IC/IM固定表：估值档与实际生效档、MOM120与主导来源、当前/下一期货及Put四袖数量、每1张期货记录数量比例、核心/动量当前及目标合约和编码可核验的行权价/到期月。

IM普通核心保护1.5张与独立生命周期0张是不同字段，明确分开，不互相回填。IC每张20、IM每张3是记录数量之比；IC当日动态重算因缺所选Put绝对Delta保留N/A，不冒称固定参数。实际账户整数张数、逐合约独立报价日期/时点/来源缺项保留N/A，不借用IM的95% IV参考合约填补102%交易选约证据。

复用已成功的同一真实10/09结果，仅重打包并验证展示，不重复抓行情或续账。新203项automation IC/IM测试及12个subtests通过，零失败/跳过，2.81秒pytest；10/08历史主题、正文、HTML仍与已送达邮件逐字相同。最终远端CI、正常发送与收件箱按各自实际证据另行验收。
