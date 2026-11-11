# 微盘股本地切换验收材料

本目录保存11月11日的真实原件及12日完成切换的实际回执。原始路径、字节数及SHA-256见[evidence-index.json](evidence-index.json)。JSON和HTTP响应逐字节复制；[候选原生报告](native-m3-candidate-gate.json.gz)仅作gzip压缩，解压后SHA仍为原报告的`680aabd1…`。原始时间、来源身份和未完成状态均保留。

## 当前生产状态（12日已完成）

[PR185](https://github.com/liuruojiang/codex-daily-automation-probe/pull/185)已合并至`538169e67ce5f71cd4b4a13fde13c3d85348eb80`，owner=local、scoped enabled=true。[真实应用更新](automation09-tool-update-20261112.json)及[配置文件读回](automation09-actual-final-20261112.json)证明原09-00任务已为ACTIVE且移除自暂停，原项目、本地环境、工作日9点/11点、模型及通知设置保持；应用只裁去prompt的一个末尾换行。

[云端维护实跑43070342596](https://github.com/liuruojiang/codex-daily-automation-probe/actions/runs/43070342596)成功，[写入门控回执](cloud-local-owner-guard-20261112.json)及[原始步骤列表](cloud-local-owner-guard-jobs-20261112.json)证明旧维护和旧调仓写步骤均跳过。[最终生产切换回执](local-production-cutover-final-20261112.json)保存原pin、七文件及既有ST改动的保护证明，以及当时专用交付工作区clean、HEAD=origin/main=远端main、ahead/behind均0。其他历史工作区保持原样，不据此宣称全局clean。

深度维护300只，每周一更新名单，保留实际100只；调仓日仍另核轻量全市场市值边界。10日完整归档已通过两次真实M5恢复，第二次无seed回退。11日仍有27只报价HTTP502，source11及当前信号未完成，不能把10日恢复基线改签为当天数据。本次未发送策略邮件或下单。

## 已验证范围

| 材料 | 已验证内容 | 边界 |
|---|---|---|
| [完整原始M3资料只读证明](complete-real-m3-source-proof.json) | 原生历史门槛通过；170,713个可运输数据对象的inventory为`42899f51…`，七份经济CSV哈希不变，18项股票来源源码身份已列出 | 原M3归档截至2026-11-10，完整历史范围仍为5,438只；这是候选读者兼容证据，不能代替正式发布或当天新行情 |
| [M4两次真实main接力](cloud-relay-m4-20261111.json) | [第一次43038784464](https://github.com/liuruojiang/codex-daily-automation-probe/actions/runs/43038784464)发布成品；[第二次43040757263](https://github.com/liuruojiang/codex-daily-automation-probe/actions/runs/43040757263)关闭seed回退并找到第一运行的artifact `13865312028`，两次streams一致 | 原包SHA为`3d9aa183…`，保留M3 release身份；`current_signal_ready=false`，不充当M5新包或11日交付 |
| [P1真实阶段读回](local-P1-readback.json)、[完整阶段回执](local-P1-result.json) | 公告、当前名称、300只metadata及301429股本四阶段真实完成；300只metadata原10日之前的ST/活动历史及证据注册表一致 | 273只股本证书覆盖11日，27只仍停在10日；`whole_run_complete=false`、`export_generated=false`，没有全维护完成或新来源包 |
| [四次等价请求诊断](quote-wire-diagnosis/summary.json) | 跨证券及等价参数编码的四次实际GET均返回502；完整响应和收据在相应子目录，可按summary哈希复核 | 仅诊断；没有调用生产者恢复预算，没有通过原生行情/股本验收 |
| [P1原生缓存读回](P1-native-reuse-original.json)及[日历保护更正](P1-native-reuse-calendar-correction.json) | 价格300、复权300、股本273原生通过；27只按真实截止日期拒绝，3304份生产数据内容不变；仅300次原生锁创建和300次删除，无遗留 | 原结果的`calendar_actual_HTTP_requests=0`不可采信：9次cache miss中1次重放已保存真实原件，另8个原生日历子进程未包装，HTTP总次数/逐次状态未捕获；不能称整段零网络。股票数据复用结论不等于全维护完成 |
| [M5正式归档源码复验](M5-formal-archive-source-recertification.json) | 实际main读者`1634a27c…`已完成正式源码复验，170713对象inventory不变，保留原M3 manifest/producer身份和七份经济CSV | 归档日期仍10日、current_signal_ready=false；新包发布见下项，M5两次云端原生恢复均已通过，见下表 |
| [M5云端字节接力](M5-byte-relay-cloud.json)、[发布回执](M5-archive-publication.json) | 实际运行[43065027595](https://github.com/liuruojiang/codex-daily-automation-probe/actions/runs/43065027595)重建准确新包，release `441957202`、asset `736257558`服务器digest与本地包SHA `3326bf3a…`一致 | 仅传输并发布原已生成的673,248,922字节，不是原生恢复，也不更新行情、经济数据或当前信号 |
| [切换候选配置验证](local-cutover-configuration-validation.json) | 两份实际配置由正式load入口通过，现有workflow guard退出旧云维护分支；三个scoped settings CLI均给出准确M5/P1/seed，原10日来源包身份和原477日更文件不变 | 仅本地分支配置验证，不合并、不派发、不改自动化；27只11日失败仍未完成 |
| [M5两次真实main恢复](cloud-relay-m5-20261112.json) | [43065299195](https://github.com/liuruojiang/codex-daily-automation-probe/actions/runs/43065299195)发布准确M5成品；[43067983093](https://github.com/liuruojiang/codex-daily-automation-probe/actions/runs/43067983093)关闭seed回退后找到第一运行artifact `13876647441`，bundle SHA `3326bf3a…`、source manifest和三版streams一致 | 两轮均完成原生archive检查；日期仍10日、current_signal_ready=false，不等于11日新资料或生产自动化已启用 |

原候选生命周期`288db1ef7fcc49d7b5500de219782ebf`的报告中，`native_archive.ok`和`whole_inspection.ok`为true，但顶层`ok=false`：随后读取旧快照inventory遇到Windows长路径错误。之后使用已提交的最终读者仅补做只读inventory，生命周期`ca389a4943ab409b8959ae9992adde33`产出上表的完整证明。两段原件均保留；不能把前一次整个运行写成PASS。`native_archive.price_cache_files`中的100项是当期成员输送检查，不代表把5,438只历史范围或300只日常维护范围缩为100。

## 正式交付与剩余数据项

维护读者M5已随[原生PR #100](https://github.com/liuruojiang/microcap/pull/100)合入main `1634a27c5a609a46ab35fc29ca2c65b94182671b`；来源生产者P1为`f6970d665d5c7a39b37c866cf13dc3d4cb8fdcc5`。[Auto PR #184](https://github.com/liuruojiang/codex-daily-automation-probe/pull/184)已以main `50387f2…`合并，保留两个角色及原477日更身份。本地正式源码激活、10日归档源码复验和原生pack-archive均已退出0。新包673,248,922字节、SHA-256为`3326bf3adec1cb073d72318991a49e94d2e4599426ef0adcf5025baf2e6aa5d1`。直接上传因链路慢中止，核对原进程退出及当时draft无整包资产后，改用下述固定字节接力；实际接力和正式发布现在已完成，服务器digest一致。源码与归档验收不等于生产owner或自动化切换。

M5两次真实main云端恢复现已完整通过，第一次`43065299195`、第二次`43067983093`，第二次fallback=false，source_run准确指向第一次，artifact `13876647441`名称为`microcap-scoped-whole-v1-1634a27c5a609a46ab35fc29ca2c65b94182671b`。GitHub外层artifact SHA为`d53e41de2c3981c4d7bd1ee96eb75d1710bb8b7614e1bc56f1d342db91aada01`；内层原生bundle SHA为`3326bf3adec1cb073d72318991a49e94d2e4599426ef0adcf5025baf2e6aa5d1`。两个reader、源manifest和三版streams一致，原始restore与archive检查回执随本证明保留。生产配置及09-00真实工具更新现已完成，实际回执见上方当前状态。11日27只502仍未完成，不生成新source或信号；本次主机切换基于完整10日恢复基线。

历史重现使用[原M3完整种子](https://github.com/liuruojiang/codex-daily-automation-probe/releases/download/microcap-scoped-whole-20261110-872abe41/microcap-whole-delivery-state-close-20261110.zip)，SHA-256为`3d9aa183b614b272597bf81cfbd3b6fefdde021347fd36586e8047a6dc025e42`。新的M5归档必须保留该源身份及原始证明，不把旧日期改为当前日期。

日常深度维护300只、每周一更新名单，调仓日另核轻量全市场市值边界；15:10调仓接力与16:00原477成员日更/日报分开。这里没有SMTP发送或实际订单。[最终保护回执](final-primary-preservation.json)证明主项目原pin和七份既有文件仍不变；已有长路径枚举提示和其他工作区状态原样记录，不将完整目录可枚举或全工作区clean作超范围声明。

### 一次性字节传输接力

慢速直接上传当时未完成发布。固定手动工作流下载原M3包和11,693,277字节literal gzip，通过五个固定copy/literal范围恢复新ZIP；原包、新包和delta均核验完整SHA及长度，不能修改策略数据或改变消费者格式。[实际本地重建回执](M5-byte-relay-local.json)、[独立流式证明](M5-byte-relay-stream-verification.json)与[实际云端重建](M5-byte-relay-cloud.json)一致，目标为673,248,922字节、SHA `3326bf3a…`。[正式M5归档](https://github.com/liuruojiang/codex-daily-automation-probe/releases/download/microcap-scoped-whole-20261110-1634a27c/microcap-whole-delivery-state-archive-20261110-1634a27c.zip)已经发布，服务器digest已核对。该传输工作流已经main部署并实际执行，但它只解决字节传输；两次M5原生恢复已另行通过并保留独立回执；生产配置与真实自动化更新也已完成，并保留上方独立回执。
