# 微盘定时交接核对

09:00 与 11:00 在本地资料维护之外核对上一完成交易日的调仓、发送和新成员实际接收。使用固定控制器的 `scripts/check_microcap_scheduled_delivery.py --root <原生来源运行目录> --date <本轮资料目标日> --out <本轮新回执>`。后续补用同日实际 `microcap_recovery_plan.py` 生成的回执，增加 `--plan <原计划>` 和 `--local-preparation <10:00 正式 install_readback.json>`。11:00 已到 10:00 准备应完成的时点时加 `--require-local-consumer`，若准备任务仍在运行则跟原任务核实。日期以原早间维护目标为准，结束时跨过收盘也保留对这一天的核对。原恢复派发前仍独立确认用于恢复的最新 D。

- `slot` 核对 16:00 触发是否实际到达。只有同一天原定时键的 run 才算该槽。工作流 success、手工无发送验收和日报工件可与原槽并存。
- `delivery` 以原对应事务的 SMTP 接受回执确认完整或部分资料。核对下载大小、外层 SHA、元数据、正文 SHA 和发送步骤。邮箱投递另按原方法查证。
- `handoff` 独立记录原 15:10 调仓尝试、正式当前名单是否已准备及下一次本地入口是否验证了同一检查点。已发部分资料继续保留，等待原调仓恢复。已接到新名单的部分日报回执可关闭交接问题。
- `follow_up` 非空时命令返回 1。状态存在且仍运行时跟同一 run。未触发或尚无可核验发送结果时查原时段和原邮件事务，若仅有 intent 先核 SMTP 和邮箱。当前 16:00 普通入口以实际交易日为目标，不能用次日普通运行代替原缺失日报。
- `resume_original_no_send_native_recovery` 只继续现有 09:00/11:00 共用的原生恢复及发布任务，并保留有效阶段。15:10 至 16:00 的 50 分钟内未能交接时继续依据信息报告实际状况。
- `recover_only_existing_delivery_marker` 可使用在 `microcap-member-daily.yml` 增设的独立标记恢复模式，传同一回执返回的 `reconcile_delivery_date`、`reconcile_delivery_phase`、`reconcile_receipt_id`，保持默认 `validation_only=true`。先查当期回执和同一派发键的原运行，已接受或仍未知的派发只读回原 run。该模式只对已证实原 16:00 run 的唯一接受回执补一个缺失标记，已有标记直接退出，通过互斥组与正式发送串行。

来源复用入口 `check_microcap_morning_publication.py` 以同一个远端 main SHA 读取维护所有者及调仓配置，按云端正式周名单和本地筛选摘要比对候选范围。遇 `reconcile_weekly_roster` 时，在确认 owner 及启用状态后只续接已授权的每周名单准备，并按原发布通道校验名单一致，再启动个股维护。两份名单各自有效但内容不同仍需对齐。来源导出与已发布包复用共用传输角色检查。相关维护控制器变化需重验受影响阶段，11 月 26 日已认证的完成回执按原校验记录保留。
