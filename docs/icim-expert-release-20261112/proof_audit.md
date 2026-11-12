# 来源降级 P2 最终关闭核验

执行者：`/root/evidence_takeover`。结论：此前新提交可退用旧29清单的 P2，在最终云端 r3 已关闭。本角色复核现有原始工件，没有重复成功pytest、策略1571或行情生产，没有修改产品、旧工件、账本或发送状态。

## 最终字节与针对性证据

`cloud_candidate_pin_r3.json` SHA 为 `8516ffe5b66c70ee33c87bf796197b4c184377a5f00923dd76e8c482edd892fe`，其12文件逐项复算与当前候选一致。策略41aa对应的31模块、3support及云端manifest镜像仍精确一致。

`cloud_proof_r6` 的原始 collection 与 XML 用例身份逐一匹配，18项通过、0失败/错误/跳过、receipt与worker退出均0、32项源前后相同，60秒watchdog保持。实际节点包括原缺新清单降级反例、旧release配当前SHA的renderer拒绝、两个正式入口exact release、5个逐源/支持/清单负控与10个旧证明兼容用例。

r6之后只有测试文件加强了daily shell续行检查；生产verifier、renderer、workflow及其支持数据与r6绑定均相同。`cloud_workflow_flag_r7` 在最终32项字节上真实执行该严格检查，1项通过，退出0，XML与collection一致。该1项是18项中一个节点的加强复验，不能相加声称19项独立覆盖。机器回执含所有原始工件哈希。

## 原反例已堵住

- verifier 先从 expected commit 的 `git ls-tree` 获取注册manifest集合，再按登记顺序选最高版本。工作树缺该清单时在读取旧来源前硬拒绝，不能因旧文件仍在而降级。篡改所选清单仍与同一expected commit的git show逐字节对照。
- renderer 增加可选 `expected_release` 校验。正式daily和readiness均明确传 `--expected-source-release 20261112-expert-review-v5-r2`；daily的续行恰为一个反斜杠，因此该flag确实属于同一打包命令。
- 旧commit只包含旧29清单时仍按其实际提交选择；无expected_release的历史只读API保持兼容。正式当前入口的exact release不能被旧API兼容方式替代。

原红例 `missing_new_manifest_counterexample.json` 保留，不改写为通过。真实远端旧manifest存在性由根提供的GitHub API tree回执证明；新负控的git HEAD、ls-tree、show是明确受控读者，不声称本地已fetch41aa或在真实远端工作树执行。该边界已写进summary。

本结论仅关闭来源证明P2，配合根其它角色证据用于决定云端发布。它不证明当日数据已生成、正式账本已续接、SMTP发送或INBOX送达。
