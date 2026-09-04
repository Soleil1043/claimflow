"""风控工具（Phase 8 T083）：黑名单查询、理赔频率、欺诈规则评分。

数据口径：
- 黑名单：data/mock/blacklist.json（Mock 起步，prod 换风控系统 API Adapter）
- 理赔频率：claim_records 表 join policies 按持有人证件号统计近 N 天申请次数
- 评分：rules.evaluate_fraud_rules 纯函数（与 deterministic fraud_check 节点共用）
"""
