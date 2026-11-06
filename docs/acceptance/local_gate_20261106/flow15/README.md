> 最终状态：正常补发与本地D5整包同步均已完成；见[最终交付验收](normal_delivery/README.md)。以下运行中描述是此前验收时点记录。

# flow15 ST市场范围修复验收

runtime ad4de441d35442616f91aa613146ffdf8cd479f3；test05fd1ae961f726b7efe71acc2f5eb43ab55ac25d；AUTO152f6ecfa40ab495d94e7e4de42b6ac0d3aa6669。

本地native gate07:23:21至07:28:48：core30 1100/227.176秒，release6 271/41.850秒，share_source2 144/21.562秒，零失败/错误/跳过。同日ICIM1193+105以完整原依赖/源码/fixture/环境/生命周期按flow10证据复用，daily仍执行当次全组。nativeactivate真实exit0，active独立读回flow15。lifecycle e984ed72349f4fa69f4450d7d9630f56真实exit0、保留69项日志/清单/JUnit并清理自有临时目录，释放2,660,220,928字节。

上一run42120733132通过102/102真实股票输入，但随后Current name and ST snapshots disagree，未生成合格D5整包或正常邮件。该云端运行没有保存原名称/ST表。此目录scope文件是11/06补充取数，不冒充原云端快照：Sina原生SDK解析5577行形成5223条沪深A名称；东财ST板块387条含192条BJ/NEEQ/B股，沪深A范围无冲突。原完整SDK解析表与采集回执在本地postclose_delay_review/name_st_conflict_probe，哈希见scope_sources.json。最初诊断进程错误重定向了嵌入模块属性而非函数全局，读到了旧ST缓存；修正仅诊断隔离路径后，原生loader+publication函数实际通过，CSV字节未变，见scope_native_fix_readback.json。

修复仅比较同一沪深A市场；不是按交集丢失缺失A股。A股缺失/名称冲突/非法身份/空有效ST集合继续失败，原ST表及历史不改。新增6例在161项针对验证中通过；恢复/静态诊断66、发布绑定52、本地兼容与ICIM复用24通过。兼容增加第7个精确执行文件v20；旧原批准包及原始manifest不改。

仅补采两份小表的失败诊断留存，不引入全缓存打包。当前正常run42128741954的1922策略/718AUTO完整专项已通过，仍在原批准包恢复；发送、D5整包、本地经济状态尚待真实验收。软件PASS不代表今日行情或邮件送达。只补Nov5Micro正常邮件，已送Nov4Micro与Nov5ICIM不重发。
