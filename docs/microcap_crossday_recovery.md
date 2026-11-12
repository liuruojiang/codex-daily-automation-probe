# 微盘股跨日故障恢复

本修复将原调仓日R和最近完成交易日D分开处理。恢复在真实D执行原生完整刷新和独立检查，按原生双周日历覆盖遗漏的所有R，再发布D的当前成员检查点。不会把旧R冒充今天，不修改选股、成本、三版参数或既有正式历史前缀。

核心修复已通过[PR197](https://github.com/liuruojiang/codex-daily-automation-probe/pull/197)合并为`b8d489a7da1f09db5e48ebff387b7042f9508906`。生产Auto、本地11文件读取包及09:00/11:00恢复指令已切换；两次真实云端无发送验收[produce43182489965](https://github.com/liuruojiang/codex-daily-automation-probe/actions/runs/43182489965)与[readback43182714966](https://github.com/liuruojiang/codex-daily-automation-probe/actions/runs/43182714966)均通过。第二次准确命中首轮工件13927066639，未使用fallback，成功阶段全部复用。软件故障后缀与当日真实行情状态分别验收。

上述切换与回执属于已验收的原版本。本轮P3/M8后继候选增加双源股本备用和原公告页自动接续；实现及软件验证不等于新M8归档已发布或D12来源、调仓、日报全链已完成。实际release、包哈希、现场验收和本地安装须分别补记。策略参数及历史经济数据不改，候选恢复保持no-send。

## 09:00与11:00的同槽恢复

资料维护仍由本地唯一写入者处理300只及903项。成功来源按原回执复用；`already_complete`只跳过重复采集，不跳过未完成的调仓或成员检查点接力。`due.target`决定本轮早晨资料的目标日；恢复派发前，须从当前来源运行目录的原生`latest_completed_session()`再次取得恢复日D。若维护延续到收盘后，D可能已前进，不能沿用早晨的旧日期。两个时槽使用同一个持久恢复目录：

```powershell
C:\Python314\python.exe -B -X utf8 D:\Codex\workspaces\auto-maintenance-20261109\scripts\dispatch_microcap_recovery.py --root D:\Codex\microcap-local-maintenance\runtime --date <recovery.D> --state-root D:\Codex\microcap-local-maintenance\recovery\dispatch
```

该入口只允许`rebalance_recovery`，不启动日报。它核对正式main的local owner、原生来源/消费者角色和独立完成日，按固定`microcap-recovery-v1-D`键保留启动意图。已有同工作流运行先跟踪；已成功不重复；明确失败后才能续同槽。请求已接受但响应丢失、进程中断或查询不明时，只读查询原意图，不重复POST。main在请求期间推进时，只有实际运行的精确新提交仍具有同一固定运行合同才接纳，并保留原expected head和实际observed head。

返回`accepted_pending`、`unknown_pending`或`await_existing_workflow`表示继续跟踪已有执行者，不能当作完成或另起一个任务。继续检查原run及其来源、完整状态、当前成员包和发布回执；诊断修复后只续失败阶段。退出0也不能代替实际云端完成结论。

P3股本报价保留原EM事件库存、Sina历史、逐日市值窗口和旧候选前缀。只有主报价已认证的无响应传输故障或HTTP500/502/503/504才选择`tencent_sina_total_shares_v1`双备用；已有原502/耗尽回执直接使用，不重置预算或再补四次EM。腾讯总股数按股、Sina信息字段总股本按万股换算，必须精确相同并等于目标日终值；每个来源保留自己的URL、原raw、回执、交易日和观察时钟，不伪造EM响应或统一成一个报价日期。

备用源按固定查询键先落持久意图，每个原attempt只读保留。成功来源不重抓；初次可恢复故障不sleep，30分钟后最多在独立recovery目录请求一次。原结果未知先保留意图及已有响应；恢复再次未知或失败后不发第三次请求。403、TLS验证错误和HTTP200语义错误均不能借换源或重试通过，后到股本也不能改写目标日终值。

## 云端恢复步骤

普通15:10调仓和显式恢复共用原生计划器及唯一并发组。普通入口在没有遗漏R时跳过重型工作；显式恢复还可以补当前成员包缺失的非R交易日。

1. 先读取最新小检查点和独立日历，识别遗漏R。后来的`awaiting_handoff`是旧篮子参考，不能作为正式历史父包；父包限定在最早遗漏R之前，旧参考原件与发送事务保留。
2. 在重来源采集前，保全R市值分页及真实观察时钟。已有原件先复用；缺失时使用明确`TRADE_DATE=R`的历史查询，逐行校验R，保留实际晚到时间。原历史源缺失或内容冲突仍明确受阻。
3. 复用并核验封闭的来源和阶段回执，在真实D运行原生`refresh-all`和独立`check`。恢复传输只进入独立私有角色；原JSON、价格、来源哈希和观察时钟不改。路径搬迁产生单独接纳记录。
4. 两道正式门槛成功后先保存完整状态包，再构造当前成员包。Native打包会给包内manifest记新核验时间；同时封存原始manifest侧车，恢复时严格核对原成功回执和两个manifest的经济闭包，恢复原字节，不能松哈希或修改候选。
5. 新成员包显式使用`native_formal_gap_recovery_v1`，证明包含完整交易日序列、所有R、原正式历史前缀、原三版状态和精度依据。非R的D仍使用最后真实R的成员目标和执行日，不将D当成调仓日。
6. 日报与本地读取器在采用前独立验证恢复证明。显式缓存与新下载包使用同一证明门槛；只改handoff标识不能绕过period内证明，缺证明、变更历史声明或伪controller均拒绝。相同D时当前新成员包优先旧参考包；相同日期和阶段的不同经济结果仍拒绝，不能用上传时间掩盖冲突。

M8仅允许`native_formal_gap_recovery_v1`及当前已审`recovery_controller`关系，不能套用M5/M6/M7的`native_close_extends_previous`或`same_close_period_switch`正常模式。旧三种生产者的原正常血缘继续保留；M8仍须完整恢复证明，标记、父包类型或controller缺失均拒绝，不把结构性标记检查当作完整证明验收。

成功阶段的命令、原进程日志和输出闭包全部准确匹配才复用。未知进程终态、缺文件、改值、错源、错时钟或错日不转成成功。部分工件始终标记`resumable_partial`；来源准备或历史归档校验不能替代当前D的正式门槛。

## 发布与日报分离

恢复只发布成员检查点和完整状态，不调用SMTP，也不清理或变更日报intent、SMTP receipt和发送marker。上传已被GitHub接收但回执丢失时，按原main/run/步骤资格、API摘要及原对象闭包只读调解；原producer身份和失败结论保留。成员包成功但完整状态上传未完成时，只补同一原包发布，不重新维护903项或重算三版。

477检查点命名、ad4经济核心和AB214本地执行角色保持。原M7/P2生产者标签、旧证明、安装与指针备份保留；P3/M8候选通过精确读取关系接续，不能把旧来源改签成新生产者。新本地包须包含恢复证明校验器并独立验收；已经采用带新证明的数据时，不能单独退回不认识该证明的旧读者或改签检查点。

## 旧格式来源现场的切换接力

当天原15:10运行`43173346897/attempt1`仍使用eec旧代码，在来源准备失败前已完成300个价格、300个复权比例及44个股本阶段；256个股本阶段为HTTP502，公告重核D10边界有未审查事件，metadata等待。原正式handoff/CP/whole均未执行，不能把644个来源成功当作完整资料或信号成功。

`microcap_legacy_source_recovery.py`仅接纳这一固定旧main提交、运行、attempt、日期、M7/P2与两个固定原工件。新sealed现场优先；明确miss后才读取原诊断与producer工件，orphan-only不触发旧来源恢复。双方API摘要、对象路径、原context及所有成功回执的原文件SHA必须闭合。原JSON、时钟、失败/恢复预算和eec身份保留；接收者新建的manifest显式声明`legacy_source_artifact_bridge_v1`、自己的签发身份以及“原运行未sealed”，只作为私有P2来源输入，不能携带consumer/formal成果或伪造旧执行计划。

P3/M8接收这份旧现场时，旧manifest仍按原P2/M7身份验证，再独立核对实际新P3/M8 Git角色；报告的`validation_reader`与原producer分开，原JSON不重标。完整P2暖包的目标日若晚于正式bootstrap基线，只允许冻结P2的18项来源指纹到当前P3的精确两份股本读取器变更；仍逐项执行价格、股本、复权原生读核及历史比较，导入期间实际读取器版本也必须保持一致。已被正式基线覆盖的旧包保留原跳过行为，不扩大为任意旧版本兼容。

专用`microcap-legacy-source-readback.yml`只读接回原两工件，用实际私有admit与P2的price/share/ratio校验逐项验证644个成功来源。独立日历先通过真实原生函数取得并保留完整返回值及真实时钟，随后用同一真实日历读回，验证期间禁止股票HTTP和新子进程。日历采集单独记账，不冒称整个验收零网络。只发布小证据，不发布477成员包，不刷新903项，不派发日报。即使全部644项读回通过，256项和ST差异仍保持未完成，正式D门槛继续生效。

## 已批准公告前缀的独立接力

股票暖包继续排除ST权威。独立`microcap_notice_prefix.json`仅指向已批准D10→D11的原推进回执及D11自复核回执，接收端在股票暖包之后、当日原生维护之前运行`microcap_notice_prefix.py warm`。两份原proof的路径、原SHA、时钟及P2/M7来源身份不变；P3/M8只按精确读者关系接纳，不改签证明或放宽原生审查边界。

侧车完整声明所有32761个历史/增量对象；旧D10闭包以`baseline_files`明确借用并逐字节核验，`payload_files`只传原proof及其必要依赖和新增对象。组合只读视图必须再次通过原生两端source_closure、archived_freshness及verify_notice_chain，不能凭本地碰巧有相同SHA借用新的proof依赖。旧全历史不重复传输或展开。

只在独立私有来源生产者精确核对bootstrap、正式consumer完整锚及指针原值后提交D11原pointer/freshness；全consumer、参考proxy和历史不动。已有同一权威的更晚自复核或合法后继只读核验并保留，不回滚。两文件提交故障保留可恢复事务；不明权威或原件冲突仍拒绝。D11历史前缀绝不能代替D12新鲜度，当日公告、名称、ST metadata及正式三版门槛仍执行。

下一交易日前推前，`select-forward`独立核验当前活动证明。原生连接器沿推进generation查找前序原件；若直接备份同权威自复核证明，其所在generation与权威generation不同，会在下一日连接时报“Original older notice proof is unavailable”。因此只在请求日期晚于已维护权威日期时，原生验证两份原证明及其完整连接后，选择同权威真正的推进证明原字节作为私有来源生产者的活动检查点。权威日期、旧proof、观察时钟、正式consumer和历史数据均不变；同日复核不作这种选择。

选择前保存原活动自复核原件及持久意图，原子替换后读回。替换已成功但完成回执丢失时，只读调解同一事务，不再写入；原自复核追溯缺失、原件冲突、未知权威或来源领先正式consumer却丢失活动证明时拒绝继续。准备流程顺序为股票暖包、独立公告暖包、前推检查点选择、当日原生维护。每次选择的独立回执及命令日志绑定进prepared闭包。

当日维护未显式传入`--resume-notice-generation`时，还会自动扫描本root内常规、未promoted的`refresh_*`目录。当前权威边界到目标日的每一天必须已有完整manifest，并通过原生`verify_day(required_asof=本次09:00)`；目录中不得已有`proof.json`、`archive.json`或`previous_pointer.json`。合格者按原真实观察结束时间选择最新目录，另存原页、回执、权威和源码哈希的接纳记录，再调用原`refresh_notice_archive(resume_generation=原目录, required_asof=原09:00门槛)`，不重采完整原页、不改其时钟。

显式CLI目录仍优先；部分页、过期页和已promoted目录不自动接续，没有完整合格目录时保留原正常采集路径。有完成manifest却坏绑定、来源未知、最新观察同钟歧义或原生边界冲突时明确拒绝，不为通过而退到更旧候选。自动选中只表示资料可送原生门槛，不代表当日公告、ST metadata、三版刷新或当前信号已经完成。

## 验收边界

软件对抗测试覆盖原日期断点、多R、非R漏日、未知上传、重启和跨目录接收；测试报价/正式导出包络明确是夹具，不是当前行情或新生产收益。

专用`microcap-crossday-recovery-acceptance.yml`以独立namespace执行两次真实云端运行：先产出隔离恢复状态，再由下一次准确命中、恢复原状态并验证成功阶段没有重执行。该工件不能被正式477读取器发现，流程不触达正常日报。正式定时任务的当日来源和结果仍分别记账。
