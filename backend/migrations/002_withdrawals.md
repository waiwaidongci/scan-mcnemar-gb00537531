# 002 票据撤回与报告修订版

财务撤回误入验收的票据后，含该票的报告立即停用并按原门槛重算。应用启动时自动执行：
`withdrawals` 由 `create_all` 补建；`runs.revision` 对既有表执行一次幂等
`ALTER TABLE runs ADD COLUMN revision INTEGER NOT NULL DEFAULT 1`（列已存在则跳过）。

- `withdrawals` 记录撤回票据，以扫描批次和票据编号唯一去重，只增不减。旧文件再次导入同一批次不影响撤回认定，计算时逐次剔除。
- `runs.revision` 报告修订版号，初始为 1。报告被撤回停用时递增，结果、导出文件（`analysis-{运行}-{修订版}.csv`）与复核状态随之重置；验收页面与导出始终读取同一修订版，报告仍按原运行编号查询。
