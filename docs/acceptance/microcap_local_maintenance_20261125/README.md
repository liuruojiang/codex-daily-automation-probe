# 11 月 25 日微盘资料维护

本轮目标为 2026-11-24。原生资料维护逐项读回 299/300 只、898/903 项，并校验 69,967 个文件。898 项包括价格、股本、复权各 299 项及当前名称。688511 的正式证券主表、历史价格、交易状态和股本底稿尚未采用，其三个来源阶段及正式公告、ST 元数据事务仍未完成。

当前风险名称观察完成于北京时间 09:07:59。11 月 19 日至 24 日的 65 页公告原件均在本次 09:00 后采集，文件与原观察时钟已封存。11 月 19 日的重新观察增加两条再融资注册批复，原生 ST 投影一致。两份签章 PDF 纳入精确读取修复，原始公告页读回及 78 项针对性测试通过。此轮安装配置仍使用原配置的生产者和消费者。 [原生读取修复](https://github.com/liuruojiang/microcap/pull/120)

688511 的隔离候选已按原生读取器验证至 11 月 24 日。巨潮股本回执通过，原股本 CSV 的 SHA-256 仍为 `eea383e7c56062d9bc4bdf0387031dc7d3fcb876f6aa34407f7d5c6cfb40c0af`。该证券的隔离 ST 候选范围为 `a81137621db0a43dffcd0a1a72ead673afff0bfa7360fe1d56110677ebbbb4bd`。基于已读回的 11 月 12 日云端正式归档，三版历史影响复算涉及 10 期历史成员，现持仓与下一持仓序列均无变化。迁移审阅索引 SHA-256 为 `c6e04903b825cf788e922f42d5d413c118ac5df97f01e120cc3e1eb742bf7091`，正式历史采用仍须有完整原生迁移报告及精确批准。

- **来源发布**。目前正式来源仍为 11 月 19 日的工件 `770356140`，周名单仍为 11 月 13 日。本轮资料包及验收清理受 688511 历史门槛阻断。 [来源发布](https://github.com/liuruojiang/codex-daily-automation-probe/releases/tag/microcap-local-sources-20261119-d09b53af)
- **调仓接力**。11 月 24 日 15:10 的原运行确认没有漏调仓。该日日更已发布成员工件 `14900842351`，独立原生计划器读回后确认无需再推进。现有 100 只均在本地准备池内，其中 300500 与今晨 ST 名单有交集。下一调仓日为 11 月 26 日。 [调仓运行](https://github.com/liuruojiang/codex-daily-automation-probe/actions/runs/45345989086) · [成员检查点运行](https://github.com/liuruojiang/codex-daily-automation-probe/actions/runs/45352339963)
- **完整状态**。已再次核对 11 月 12 日完整云端工件 `13969276381` 的 GitHub 外层摘要、原 ZIP 和三版最终 CSV。当天验收属于其注明的历史交易日。 [完整状态运行](https://github.com/liuruojiang/codex-daily-automation-probe/actions/runs/43261210223)

本轮本地收尾回执的 SHA-256 为 `c6f5b58f18f0ba0b1acfd356265cf6e3acf0df88d704ba6b00fa6c63ecca73b7`。本轮未派发云端恢复、发送邮件或下单。
