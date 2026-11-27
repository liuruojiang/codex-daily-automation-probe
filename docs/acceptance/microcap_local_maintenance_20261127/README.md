# 11 月 27 日微盘早晨维护

本轮维护 11 月 26 日已完成交易。正式周名单包含 300 只，覆盖当前 100 只成员。来源事务通过 903 项，900 项股票来源全部复用并读回。当前名称使用 11 月 27 日 09:04 的原始观察。

首批公告在第 9 页出现服务端分页总数变化，保留原始失败请求。原生入口续用 09:08 完整观察的 17 页和 1,648 个唯一公告。原权威中的 1,168 个公告完整保留。已审的 480 个后到公告中，ST 凯利仅披露撤销风险警示的申请进展。东方财富与巨潮资讯的原 PDF 字节一致，发行人原有 ST 区间没有变化。

16 只新入池股票的旧元数据仍绑定 11 月 12 日。修复恢复正式锚中的原权威闭包，使用原生消费证明中逐证券的真实历史边界，再提交同一批原生 ST 事务。已通过的元数据不再次构造。受保护快照包含 5,439 只。当期三版既有记录继续受发布收益保护。

来源代码、正式接收器及成员兼容读取关系均按不可变提交核对。本地成员策略读取仍使用此前的已批准版本。接收端的完整状态和资料包分别校验，名单接力沿用 11 月 26 日已经验收的原检查点。 [原生修复](https://github.com/liuruojiang/microcap/pull/124)

[11 月 26 日早晨资料包](https://github.com/liuruojiang/codex-daily-automation-probe/releases/download/microcap-local-sources-20261126-163f2081/microcap-local-sources-morning-20261126-163f2081.zip)的 SHA-256 为 `d0988eb859b4071d75437457acebb5166b516971093a145282188a32c075823e`。接收方逐文件核对了原清单的 41,608 个对象。[11 月 26 日三版正式状态包](https://github.com/liuruojiang/codex-daily-automation-probe/releases/download/microcap-scoped-whole-20261126-c169efff/microcap-whole-delivery-state-20261126-c169efff.zip)的 SHA-256 为 `e9ae47ee0c317f83c27aed7523ffb5060209f04f0c431e7de7f849f829b1b47f`。原生刷新和独立检查均通过，16 个既有经济输出逐字节保持。
