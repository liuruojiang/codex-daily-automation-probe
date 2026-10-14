# IC/IM 不发邮件的正式账本恢复

本次收盘恐慌修复后，no-send readiness已经验证取数和报告，但它按原约定不上传正式账本。正式云端仍停在10/13；10/15收盘时缺失的10/14已不是最近完成session，若历史包仍断连会再次阻断。为持久保存这次正式续接，在原日报工作流增加显式手动 `ledger_only=true` 恢复模式。

默认false；正常20:00调度、策略pin和发送去重不变。恢复只允许手动、close_confirmed，拒绝correction、external_schedule、realtime和定时事件混用。相同并发锁、原交易日历、官方工件恢复、迁移/全链核验、正式runner及消费者门禁全部保留。

恢复模式的send-intent、SMTP及delivered marker各自明确禁用；失败也不发送警告。额外核对续接前旧journal/迁移原字节、完整无孤儿链、result与最新记录完全一致后，才上传正式 `ic-im-v1-4-r1-ledger`。上传正常官方工件不等于邮件已发送，正常send-intent和marker不因恢复而产生、覆盖或删除。

readiness工作流继续不上传正式账本，不能将其报告当成正式状态。恢复后的官方链可由既有正式恢复器识别；本地canonical合法不同来源分叉继续保留，不以云端恢复覆盖。本次恢复结果必须独立下载原ZIP/API证据、全链/迁移/旧前缀核验，并确认发送相关步骤实际skipped。
