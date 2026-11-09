# 微盘股本地维护准备（2026-11-09）

当前状态：已实现逐证券原生维护、持久检查点、独立资料打包/接收校验和补跑判断。
真实 300 只维护正在独立长期目录执行；以当次 `runs/<日期>/result.json` 为实际完成凭据。
生产主机和触发器尚未切换，不能把来源维护等同三版正式交付。
本地 09:00、11:00 补跑任务以暂停状态保存。旧云端 09:00 与 15:10、日报、邮件均未改动。

## 已落地的准备入口

`scripts/prepare_microcap_local.py` 读取原生验证过的小检查点和指定收盘日的
Eastmoney `RPT_VALUEANALYSIS_DET`，校验分页总数、日期、证券代码、交易所、
总股数整数单位以及 `原始收盘价 × 总股数 = 总市值`，形成 300 个维护优先项。
现有 100 只成员为必留项，其余按总市值从小到大补齐。沪深 A 股之外的股票
不进入此准备池；参考 ST 名单用于安排优先级，不能代替原生时点 ST 门槛。
缺失数据不会变成零市值，现有成员缺失会使本次准备失败。

数据按日期留在独立长期目录，已完成页面附带查询参数、TLS、时间和 SHA-256
回执；第二页失败后只重取第二页。已有页面哈希或分页总数冲突时保留证据并
报出原因。每次运行单独保存结果，失败不覆盖上次成功结果。操作系统文件锁
限制同一本地准备目录只有一个写入者，进程退出会自动释放。
该锁仅保护本地准备，不是跨云端/本地互斥锁。

2026-11-09 实际准备验收固定在 **2026-11-06 收盘**：源 5,579 行、2 页，
5,033 个具有有效数值且未命中参考 ST 的沪深 A 股；生成 300 项，保留成员
100/100，按该快照和参考筛选得到的前 100 项没有落在池外。这不是原生调仓
一致性证明，也不是 11 月 9 日收盘维护结果。
13 项局部测试覆盖缺页续接、篡改拒绝、错日/代码/单位/重复拒绝、成员闭包和
单写入锁；真实原始页面及运行结果留在本机，不写入 Git 源码库。

## 本机位置与重现

- 长期数据：`D:\Codex\microcap-local-maintenance`
- 准备代码：`D:\Codex\workspaces\auto-maintenance-20261109`
- 只读原生验证器：`D:\动量策略\微盘股对冲策略`，原安装 pin 保持 `ab214c20…`。
- 原有七个未提交数据文件的内容哈希保持不变。
- 小种子由 `config/microcap_member_checkpoint.json` 指定的正式发布下载，按其
  原始 SHA-256 校验后解包；未从本地未提交结果构造新种子。

```powershell
C:\Python314\python.exe -B -X utf8 D:\Codex\workspaces\auto-maintenance-20261109\scripts\prepare_microcap_local.py --strategy-root D:\动量策略\微盘股对冲策略 --state-root D:\Codex\microcap-local-maintenance --checkpoint D:\Codex\microcap-local-maintenance\checkpoints\approved-20261106\checkpoint.json --st-snapshot D:\Codex\workspaces\microcap-maintenance-20261109\.microcap_index_cache\current_st.csv --as-of 2026-11-06
```

这里的 ST CSV 是带内容哈希保存的准备参考，脚本明确记录其时效尚未正式认证。
不要以其替换公告归档、ST 历史、交易状态或总股本原生证明。
准备结果永远带 `production_ready=false`，不产生信号、正式 NAV、邮件或交易。

## 目标运行合同及启用条件

1. 每个交易日北京时间 09:00，由本地 Codex 处理最近完成 session 的维护。
   电脑与 Codex 应用保持运行；不得声称产品自带漏跑补执行。
2. 深度资料目标为约 300 只，包含现有成员。每周重新筛选名单；实际调仓日
   重新取得轻量全市场快照，检查边界，必要时先纳入新候选再做原生选择。
   300 是维护预算，不是“池外永远不能入选”的策略假设。名单时效、边界和
   暂停上市/涨跌停等条件须由正式消费者核验，不能以准备筛选替代。
3. `maintain_microcap_local.py bootstrap/run` 按证券/来源/日期记账，价格、股本、
   复权分别保留原生证明，公告和 300 只 ST 资料通过原生冻结事务更新。成功来源
   先原生读回复用；失败只重跑受影响步骤。两个数据进程处理独立证券，价格写入
   串行，股本/复权仍受原并发限制。一只失败继续其他证券；全部 300 只以及公告、
   ST 事务通过才产生 complete 回执。运行目录保留每次尝试、原始错误及字节哈希。
4. 软件错误由当次 Codex 定位、修复、运行对应测试并继续；网络故障沿用有界
   请求和已验证备用源。真实来源冲突、历史迁移或权限阻断不能用填零、改日、
   改指纹或移除门槛绕过。能继续的证券继续处理，缺失必需项不标为全部完成。
5. 原有基础状态把全市场元数据/来源绑定进指纹。必须实现正式的范围生产者与
   消费者绑定，并验证既有历史不变、原生调仓选择一致及三版完整交付，才能
   启用范围维护。不能仅把原循环的 5,000 改成 300，也不能冒充已经验收。
   `microcap_scoped_inputs.py export/import` 的资料接收先在嵌套独立目录完成原生
   校验，不预先覆盖任何生产活动指针；接收回执明确保留
   `native_late_adoption_required=true`。这防止在原生旧状态检查之前改坏全局指纹，
   不能把 staged 资料冒充原生调仓消费者已经采用。
6. 实施可核验的漏跑恢复和去重：按目标 session 查询完成/运行记录，补跑仅限
   未完成目标；本地/远端未知运行先核状态，不能另起重复写入。恢复槽位及
   `microcap_local_due.py` 按交易日历和目标日期的 900 项来源步骤及公告/ST回执、
   实际文件哈希判断是否完成；09:00 与 11:00 使用同一目标。当前日期即使已经
   收盘，手工恢复仍补当天早上应处理的上一 session。没有自动开机补跑承诺，
   电脑和 Codex 应用需要运行，不能把“任务已保存”当作补跑实测成功。
7. 本地完整无发送试跑通过后，协调停用云端 **09:00 maintenance 分支**，保留
   15:10 rebalance 和日报各自必要流程；核实无在途云端写入后才激活本地任务。
   切换前旧流程仍是唯一生产维护路径。暂停新任务即可撤回本次准备。
8. 通过验收的代码、维护记录和必要数据按既有 Git/工件通道同步 GitHub。
   先核远端状态再重试写入，不提交令牌、登录凭据或整套本地缓存。当前代码
   仅推送准备分支，不更新生产 main，不宣称大状态已同步或云端已切换。

本次范围为“做本地运行准备”，不是生产历史迁移或实际邮件/交易授权。
后续启用依赖上述真实验收结果，无需为常规实现另设用户确认关卡。

## 第二阶段固定入口

原生资料运行版本固定于 `config/microcap_local_runtime.json`，与既有云端
`config/microcap_maintenance_runtime.json` 分开；更改本地 pin 不会悄悄切换生产。
本地运行目录为 `D:\Codex\microcap-local-maintenance\runtime`。
启用的完整股本日期窗口逐日要求实际市值原始行，并与 Sina 历史、完整事件清单和
报价交叉核对，正常的认证边界后股本变化可以接续，历史字节不改。原有 STOP 与
两只精确批准证券的历史路线保留。交易日历内存缓存为 32 个日期，避免递归校验
六个交易日时重复下载相同日历；来源、参数、超时和日期校验保持原样。

```powershell
C:\Python314\python.exe -B -X utf8 D:\Codex\workspaces\auto-maintenance-20261109\scripts\microcap_local_due.py --root D:\Codex\microcap-local-maintenance\runtime --state-root D:\Codex\microcap-local-maintenance
C:\Python314\python.exe -B -X utf8 D:\Codex\workspaces\auto-maintenance-20261109\scripts\maintain_microcap_local.py run --root D:\Codex\microcap-local-maintenance\runtime --protected-root D:\动量策略\微盘股对冲策略 --state-root D:\Codex\microcap-local-maintenance --pool D:\Codex\microcap-local-maintenance\screens\2026-11-06\latest.json --as-of 2026-11-06
```

命令中的日期仅复现本次周一早上应维护的 11 月 6 日收盘，不可复制为其他日期的
“当前数据”。每周名单重选必须使用当期正式成员检查点；旧候选缓存保留但不必
每天刷新。报价、股本、交易状态、复权及 ST 的任何必需证明缺失都不能标记完成。

资料包只含选中证券的原始输入及来源闭包、生产者版本/计算代码身份和证券主档
证据；没有 NAV、信号、策略源码、ST 生产权限或凭据。GitHub release 的受限链接、
整包/逐文件哈希、日期和运行版本先核验，再在接收端重新跑原生来源证明。
包发布、接收成功、原生采用及三版交付分别验收。
