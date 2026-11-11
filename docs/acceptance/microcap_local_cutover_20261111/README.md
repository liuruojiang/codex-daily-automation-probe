# 微盘股本地切换验收材料

本目录保存11月11日的真实原件，12日继续收尾。原始路径、字节数及SHA-256见[evidence-index.json](evidence-index.json)。JSON和HTTP响应逐字节复制；[候选原生报告](native-m3-candidate-gate.json.gz)仅作gzip压缩，解压后SHA仍为原报告的`680aabd1…`。原始时间、来源身份和未完成状态均保留。

## 已验证范围

| 材料 | 已验证内容 | 边界 |
|---|---|---|
| [完整原始M3资料只读证明](complete-real-m3-source-proof.json) | 原生历史门槛通过；170,713个可运输数据对象的inventory为`42899f51…`，七份经济CSV哈希不变，18项股票来源源码身份已列出 | 原M3归档截至2026-11-10，完整历史范围仍为5,438只；这是候选读者兼容证据，不能代替正式发布或当天新行情 |
| [M4两次真实main接力](cloud-relay-m4-20261111.json) | [第一次43038784464](https://github.com/liuruojiang/codex-daily-automation-probe/actions/runs/43038784464)发布成品；[第二次43040757263](https://github.com/liuruojiang/codex-daily-automation-probe/actions/runs/43040757263)关闭seed回退并找到第一运行的artifact `13865312028`，两次streams一致 | 原包SHA为`3d9aa183…`，保留M3 release身份；`current_signal_ready=false`，不充当M5新包或11日交付 |
| [P1真实阶段读回](local-P1-readback.json)、[完整阶段回执](local-P1-result.json) | 公告、当前名称、300只metadata及301429股本四阶段真实完成；300只metadata原10日之前的ST/活动历史及证据注册表一致 | 273只股本证书覆盖11日，27只仍停在10日；`whole_run_complete=false`、`export_generated=false`，没有全维护完成或新来源包 |
| [四次等价请求诊断](quote-wire-diagnosis/summary.json) | 跨证券及等价参数编码的四次实际GET均返回502；完整响应和收据在相应子目录，可按summary哈希复核 | 仅诊断；没有调用生产者恢复预算，没有通过原生行情/股本验收 |
| [P1原生缓存读回](P1-native-reuse-original.json)及[日历保护更正](P1-native-reuse-calendar-correction.json) | 价格300、复权300、股本273原生通过；27只按真实截止日期拒绝，3304份生产数据内容不变；仅300次原生锁创建和300次删除，无遗留 | 原结果的`calendar_actual_HTTP_requests=0`不可采信：9次cache miss中1次重放已保存真实原件，另8个原生日历子进程未包装，HTTP总次数/逐次状态未捕获；不能称整段零网络。股票数据复用结论不等于全维护完成 |
| [M5正式归档源码复验](M5-formal-archive-source-recertification.json) | 实际main读者`1634a27c…`已完成正式源码复验，170713对象inventory不变，保留原M3 manifest/producer身份和七份经济CSV | 归档日期仍10日、current_signal_ready=false；新包生成、发布及M5云端接力另需实际完成 |

原候选生命周期`288db1ef7fcc49d7b5500de219782ebf`的报告中，`native_archive.ok`和`whole_inspection.ok`为true，但顶层`ok=false`：随后读取旧快照inventory遇到Windows长路径错误。之后使用已提交的最终读者仅补做只读inventory，生命周期`ca389a4943ab409b8959ae9992adde33`产出上表的完整证明。两段原件均保留；不能把前一次整个运行写成PASS。`native_archive.price_cache_files`中的100项是当期成员输送检查，不代表把5,438只历史范围或300只日常维护范围缩为100。

## 正式准备与未完成项

维护读者M5已随[原生PR #100](https://github.com/liuruojiang/microcap/pull/100)合入main `1634a27c5a609a46ab35fc29ca2c65b94182671b`；来源生产者P1为`f6970d665d5c7a39b37c866cf13dc3d4cb8fdcc5`。[Auto PR #184](https://github.com/liuruojiang/codex-daily-automation-probe/pull/184)保留两个角色及原477日更身份。本地正式源码激活、10日归档源码复验均已退出0，原生pack-archive也已退出0，新包673,248,922字节、SHA-256为`3326bf3adec1cb073d72318991a49e94d2e4599426ef0adcf5025baf2e6aa5d1`，直接整包上传因链路慢主动中止；已核对原上传进程退出且远端draft无整包asset，改用下述固定字节接力，生产启用前仍须核验GitHub发布端digest；源码复验不等于生产owner或自动化切换。

尚待本轮结果：M5新seed发布、M5两次真实云端恢复、本地唯一写入owner切换，以及通过真实自动化工具替换09-00的旧自暂停prompt。当前准备配置仍为`owner=cloud`、`enabled=false`；09-00虽显示ACTIVE，旧prompt仍含“保持暂停”。M4接力不替代这些M5与生产验收。27只报价仍受上游502阻塞，保留成功阶段，不能将其填成完成。

历史重现使用[原M3完整种子](https://github.com/liuruojiang/codex-daily-automation-probe/releases/download/microcap-scoped-whole-20261110-872abe41/microcap-whole-delivery-state-close-20261110.zip)，SHA-256为`3d9aa183b614b272597bf81cfbd3b6fefdde021347fd36586e8047a6dc025e42`。新的M5归档必须保留该源身份及原始证明，不把旧日期改为当前日期。

日常深度维护300只、每周一更新名单，调仓日另核轻量全市场市值边界；15:10调仓接力与16:00原477成员日更/日报分开。这里没有SMTP发送或实际订单。[最终保护回执](final-primary-preservation.json)证明主项目原pin和七份既有文件仍不变；已有长路径枚举提示和其他工作区状态原样记录，不将完整目录可枚举或全工作区clean作超范围声明。

### 一次性字节传输接力

慢速直接上传没有完成发布。固定手动工作流只下载原M3包和11,693,277字节literal gzip，通过五个固定copy/literal范围恢复新ZIP；原包、新包和delta均核验完整SHA及长度，不能修改策略数据或改变消费者格式。[实际本地重建回执](M5-byte-relay-local.json)已由最终脚本生成，与[独立流式证明](M5-byte-relay-stream-verification.json)一致，目标仍为673,248,922字节、SHA `3326bf3a…`。已有同名资产必须字节身份完全相符才复用，不覆盖未知上传结果。该手动传输助手需要先随disabled准备配置合入main，才可dispatch；新包真正发布、两次M5云端恢复和生产切换仍须后续真实回执。
