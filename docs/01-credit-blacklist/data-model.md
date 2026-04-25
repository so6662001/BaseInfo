# 方案一：信用查询黑名单系统 - 数据模型

> 数据库：MySQL 8.0（业务） + Elasticsearch（搜索） + Redis（缓存） + Doris（分析）

---

## 1. ER 图（核心实体关系）

```
                        ┌────────────────────┐
                        │   credit_company    │
                        │  (客户档案主表)      │
                        └─────────┬──────────┘
                                  │
                ┌─────────────────┼──────────────────┐
                │                 │                  │
                ▼                 ▼                  ▼
       ┌──────────────┐  ┌──────────────┐   ┌──────────────┐
       │credit_score  │  │company_relation│   │credit_event  │
       │ (信用评分)    │  │ (关联企业)     │   │ (信用事件)   │
       └──────┬───────┘  └──────────────┘   └──────┬───────┘
              │                                       │
              │                                       │
              ▼                                       ▼
       ┌──────────────┐                      ┌──────────────┐
       │credit_score_ │                      │ credit_      │
       │  history     │                      │  blacklist   │
       │(评分历史)     │                      │ (黑名单)      │
       └──────────────┘                      └──────────────┘

       ┌──────────────────┐    ┌──────────────────┐
       │ credit_query_log │    │credit_user_quota │
       │   (查询日志)      │    │  (用户额度)       │
       └──────────────────┘    └──────────────────┘

       ┌──────────────────┐    ┌──────────────────┐
       │credit_overdue_   │    │ credit_evaluation│
       │  report(欠款上报) │    │  (客户评价)       │
       └──────────────────┘    └──────────────────┘

       ┌──────────────────┐    ┌──────────────────┐
       │credit_user_point │    │credit_focus_list │
       │   (信用积分)      │    │   (我的关注)      │
       └──────────────────┘    └──────────────────┘
```

---

## 2. 核心表结构

### 2.1 credit_company - 客户档案主表

```sql
CREATE TABLE credit_company (
    id              BIGINT       PRIMARY KEY AUTO_INCREMENT,
    company_name    VARCHAR(255) NOT NULL COMMENT '企业全称',
    short_name      VARCHAR(100) COMMENT '企业简称',
    credit_code     VARCHAR(32)  UNIQUE NOT NULL COMMENT '统一社会信用代码',
    legal_person    VARCHAR(64)  COMMENT '法定代表人',
    register_capital DECIMAL(18,2) COMMENT '注册资本(万元)',
    establish_date  DATE         COMMENT '成立日期',
    register_addr   VARCHAR(500) COMMENT '注册地址',
    business_addr   VARCHAR(500) COMMENT '经营地址',
    phone           VARCHAR(32)  COMMENT '联系电话',
    industry_code   VARCHAR(32)  COMMENT '行业编码',
    industry_name   VARCHAR(100) COMMENT '行业名称',
    business_status VARCHAR(32)  COMMENT '经营状态:存续/吊销/注销/异常',
    business_scope  TEXT         COMMENT '经营范围',
    region_code     VARCHAR(20)  COMMENT '地区编码(国标6位)',
    province        VARCHAR(32)  COMMENT '省份',
    city            VARCHAR(32)  COMMENT '城市',
    district        VARCHAR(32)  COMMENT '区县',
    data_source     VARCHAR(32)  COMMENT '数据来源:erp/qcc/tyc/manual',
    last_sync_time  DATETIME     COMMENT '最后同步时间',
    create_time     DATETIME     DEFAULT CURRENT_TIMESTAMP,
    update_time     DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    is_deleted      TINYINT      DEFAULT 0,
    
    INDEX idx_credit_code (credit_code),
    INDEX idx_company_name (company_name),
    INDEX idx_legal_person (legal_person),
    INDEX idx_region (province, city, district),
    INDEX idx_industry (industry_code)
) ENGINE=InnoDB COMMENT='客户档案主表';
```

### 2.2 credit_score - 信用评分

```sql
CREATE TABLE credit_score (
    id                  BIGINT       PRIMARY KEY AUTO_INCREMENT,
    company_id          BIGINT       NOT NULL,
    total_score         INT          NOT NULL COMMENT '综合分0-1000',
    credit_grade        VARCHAR(8)   NOT NULL COMMENT 'AAA/AA/A/B/C/D',
    payment_score       INT          COMMENT '付款行为分',
    stability_score     INT          COMMENT '交易稳定分',
    reputation_score    INT          COMMENT '同行口碑分',
    business_risk_score INT          COMMENT '工商风险分',
    behavior_risk_score INT          COMMENT '行为风险分',
    score_version       VARCHAR(16)  COMMENT '评分模型版本',
    last_calc_time      DATETIME     COMMENT '最后计算时间',
    next_calc_time      DATETIME     COMMENT '下次计算时间',
    create_time         DATETIME     DEFAULT CURRENT_TIMESTAMP,
    update_time         DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    UNIQUE KEY uk_company (company_id),
    INDEX idx_grade_score (credit_grade, total_score)
) ENGINE=InnoDB COMMENT='信用评分';
```

### 2.3 credit_score_history - 评分历史

```sql
CREATE TABLE credit_score_history (
    id                  BIGINT       PRIMARY KEY AUTO_INCREMENT,
    company_id          BIGINT       NOT NULL,
    snapshot_date       DATE         NOT NULL COMMENT '快照日期',
    total_score         INT          NOT NULL,
    credit_grade        VARCHAR(8)   NOT NULL,
    score_change        INT          COMMENT '相比上次变化',
    change_reason       VARCHAR(500) COMMENT '主要变化原因',
    detail_json         JSON         COMMENT '详细评分数据',
    create_time         DATETIME     DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE KEY uk_company_date (company_id, snapshot_date),
    INDEX idx_company_date (company_id, snapshot_date DESC)
) ENGINE=InnoDB COMMENT='信用评分历史';
```

### 2.4 credit_event - 信用事件流水

```sql
CREATE TABLE credit_event (
    id                  BIGINT       PRIMARY KEY AUTO_INCREMENT,
    company_id          BIGINT       NOT NULL,
    event_type          VARCHAR(32)  NOT NULL COMMENT '事件类型: TRADE/OVERDUE/PAYMENT/JUDGMENT/...',
    event_subtype       VARCHAR(32)  COMMENT '子类型',
    event_time          DATETIME     NOT NULL COMMENT '事件发生时间',
    source_type         VARCHAR(32)  COMMENT '数据来源:erp/qcc/court/manual',
    source_id           BIGINT       COMMENT '数据源ID',
    related_merchant_id BIGINT       COMMENT '相关商家ID',
    amount              DECIMAL(18,2) COMMENT '相关金额',
    description         VARCHAR(1000) COMMENT '事件描述',
    score_impact        INT          COMMENT '对信用分的影响',
    extra_data          JSON         COMMENT '扩展数据',
    create_time         DATETIME     DEFAULT CURRENT_TIMESTAMP,
    
    INDEX idx_company_time (company_id, event_time DESC),
    INDEX idx_type_time (event_type, event_time DESC),
    INDEX idx_merchant (related_merchant_id)
) ENGINE=InnoDB COMMENT='信用事件流水';
```

### 2.5 credit_blacklist - 黑名单

```sql
CREATE TABLE credit_blacklist (
    id                  BIGINT       PRIMARY KEY AUTO_INCREMENT,
    company_id          BIGINT       NOT NULL,
    list_type           VARCHAR(16)  NOT NULL COMMENT 'GREY灰名单/BLACK黑名单',
    reason_code         VARCHAR(32)  NOT NULL COMMENT '上榜原因代码',
    reason_desc         VARCHAR(500) COMMENT '上榜原因描述',
    severity            TINYINT      COMMENT '严重程度1-5',
    affected_count      INT          DEFAULT 0 COMMENT '受影响商家数',
    total_amount        DECIMAL(18,2) COMMENT '相关总金额',
    add_source          VARCHAR(32)  COMMENT '添加来源:auto/manual/court',
    add_time            DATETIME     NOT NULL,
    expire_time         DATETIME     COMMENT '过期时间(可选)',
    status              VARCHAR(16)  DEFAULT 'ACTIVE' COMMENT 'ACTIVE/EXPIRED/REVOKED',
    revoke_reason       VARCHAR(500) COMMENT '撤销原因',
    revoke_time         DATETIME     COMMENT '撤销时间',
    create_time         DATETIME     DEFAULT CURRENT_TIMESTAMP,
    update_time         DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    INDEX idx_company (company_id),
    INDEX idx_type_status (list_type, status),
    INDEX idx_add_time (add_time DESC)
) ENGINE=InnoDB COMMENT='黑名单';
```

### 2.6 credit_overdue_report - 欠款上报

```sql
CREATE TABLE credit_overdue_report (
    id                  BIGINT       PRIMARY KEY AUTO_INCREMENT,
    report_no           VARCHAR(32)  UNIQUE COMMENT '上报编号',
    reporter_id         BIGINT       NOT NULL COMMENT '上报商家ID',
    reporter_name       VARCHAR(255) COMMENT '上报商家名称',
    company_id          BIGINT       NOT NULL COMMENT '被上报客户ID',
    overdue_amount      DECIMAL(18,2) NOT NULL COMMENT '欠款金额',
    overdue_form_date   DATE         NOT NULL COMMENT '欠款形成日期',
    pay_due_date        DATE         NOT NULL COMMENT '应付款日期',
    overdue_status      VARCHAR(16)  COMMENT 'NEGOTIATING/REFUSING/MISSING/PARTIAL/RECOVERED',
    related_order_no    VARCHAR(64)  COMMENT '关联订单号',
    description         TEXT         COMMENT '说明',
    evidence_files      JSON         COMMENT '证据文件列表',
    audit_status        VARCHAR(16)  DEFAULT 'PENDING' COMMENT 'PENDING/APPROVED/REJECTED/APPEALED/REVOKED',
    audit_time          DATETIME     COMMENT '审核时间',
    audit_user_id       BIGINT       COMMENT '审核人',
    audit_remark        VARCHAR(500) COMMENT '审核备注',
    appeal_status       VARCHAR(16)  COMMENT '申诉状态',
    appeal_content      TEXT         COMMENT '申诉内容',
    appeal_time         DATETIME     COMMENT '申诉时间',
    final_result        VARCHAR(32)  COMMENT '最终结论',
    create_time         DATETIME     DEFAULT CURRENT_TIMESTAMP,
    update_time         DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    INDEX idx_reporter (reporter_id),
    INDEX idx_company (company_id),
    INDEX idx_audit_status (audit_status, create_time)
) ENGINE=InnoDB COMMENT='欠款上报';
```

### 2.7 credit_evaluation - 客户评价

```sql
CREATE TABLE credit_evaluation (
    id                  BIGINT       PRIMARY KEY AUTO_INCREMENT,
    evaluator_id        BIGINT       NOT NULL COMMENT '评价方商家ID',
    company_id          BIGINT       NOT NULL COMMENT '被评价客户ID',
    overall_rating      TINYINT      NOT NULL COMMENT '综合星级1-5',
    payment_rating      TINYINT      COMMENT '付款及时性',
    cooperation_rating  TINYINT      COMMENT '配合度',
    will_recooperate    TINYINT      COMMENT '是否再合作0/1',
    content             VARCHAR(500) COMMENT '评价内容',
    tags                JSON         COMMENT '标签列表',
    audit_status        VARCHAR(16)  DEFAULT 'PENDING',
    audit_time          DATETIME,
    create_time         DATETIME     DEFAULT CURRENT_TIMESTAMP,
    
    INDEX idx_company (company_id),
    INDEX idx_evaluator (evaluator_id),
    UNIQUE KEY uk_evaluator_company (evaluator_id, company_id)
) ENGINE=InnoDB COMMENT='客户评价';
```

### 2.8 credit_query_log - 查询日志

```sql
CREATE TABLE credit_query_log (
    id                  BIGINT       PRIMARY KEY AUTO_INCREMENT,
    user_id             BIGINT       NOT NULL COMMENT '查询商家ID',
    company_id          BIGINT       COMMENT '被查询客户ID',
    query_type          VARCHAR(32)  COMMENT 'SEARCH/REPORT/SCORE/BATCH/API',
    query_keyword       VARCHAR(255) COMMENT '查询关键词',
    quota_consumed      DECIMAL(8,2) DEFAULT 1 COMMENT '消耗额度',
    result_status       VARCHAR(16)  COMMENT 'SUCCESS/NO_RESULT/ERROR',
    response_time_ms    INT          COMMENT '响应时间ms',
    client_ip           VARCHAR(64),
    user_agent          VARCHAR(500),
    api_key             VARCHAR(64)  COMMENT 'API调用时的key',
    create_time         DATETIME     DEFAULT CURRENT_TIMESTAMP,
    
    INDEX idx_user_time (user_id, create_time DESC),
    INDEX idx_company (company_id),
    INDEX idx_create_time (create_time)
) ENGINE=InnoDB
PARTITION BY RANGE (TO_DAYS(create_time)) (
    PARTITION p202510 VALUES LESS THAN (TO_DAYS('2025-11-01')),
    PARTITION p202511 VALUES LESS THAN (TO_DAYS('2025-12-01'))
    -- 后续按月添加
)
COMMENT='查询日志';
```

### 2.9 credit_user_quota - 用户额度

```sql
CREATE TABLE credit_user_quota (
    id                  BIGINT       PRIMARY KEY AUTO_INCREMENT,
    user_id             BIGINT       NOT NULL COMMENT '商家ID',
    quota_level         VARCHAR(16)  COMMENT 'TRIAL/STANDARD/PRO/FLAGSHIP',
    monthly_quota       INT          COMMENT '月查询额度',
    used_quota          INT          DEFAULT 0 COMMENT '已用',
    extra_quota         INT          DEFAULT 0 COMMENT '额外购买/积分兑换',
    period_start        DATE         COMMENT '当前周期开始',
    period_end          DATE         COMMENT '当前周期结束',
    expire_time         DATETIME     COMMENT '会员到期时间',
    update_time         DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    UNIQUE KEY uk_user (user_id)
) ENGINE=InnoDB COMMENT='用户额度';
```

### 2.10 credit_user_point - 信用积分

```sql
CREATE TABLE credit_user_point (
    id                  BIGINT       PRIMARY KEY AUTO_INCREMENT,
    user_id             BIGINT       NOT NULL,
    total_point         INT          DEFAULT 0 COMMENT '累计积分',
    available_point     INT          DEFAULT 0 COMMENT '可用积分',
    update_time         DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_user (user_id)
) ENGINE=InnoDB;

CREATE TABLE credit_point_log (
    id                  BIGINT       PRIMARY KEY AUTO_INCREMENT,
    user_id             BIGINT       NOT NULL,
    change_type         VARCHAR(32)  COMMENT 'EARN/SPEND/EXPIRE/PUNISH',
    change_amount       INT          COMMENT '变动数量(正负)',
    related_business    VARCHAR(64)  COMMENT '相关业务',
    related_id          BIGINT       COMMENT '相关业务ID',
    description         VARCHAR(500),
    create_time         DATETIME     DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_user_time (user_id, create_time DESC)
) ENGINE=InnoDB COMMENT='积分流水';
```

### 2.11 credit_focus_list - 我的关注

```sql
CREATE TABLE credit_focus_list (
    id                  BIGINT       PRIMARY KEY AUTO_INCREMENT,
    user_id             BIGINT       NOT NULL,
    company_id          BIGINT       NOT NULL,
    group_name          VARCHAR(64)  DEFAULT '默认' COMMENT '分组',
    remark              VARCHAR(500),
    notify_setting      JSON         COMMENT '通知设置',
    create_time         DATETIME     DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE KEY uk_user_company (user_id, company_id),
    INDEX idx_user_group (user_id, group_name)
) ENGINE=InnoDB;
```

### 2.12 company_relation - 关联企业图谱

```sql
CREATE TABLE company_relation (
    id                  BIGINT       PRIMARY KEY AUTO_INCREMENT,
    company_id          BIGINT       NOT NULL,
    related_company_id  BIGINT       NOT NULL,
    relation_type       VARCHAR(32)  COMMENT 'INVEST/SAME_LEGAL/SAME_SHAREHOLDER/...',
    relation_detail     VARCHAR(500) COMMENT '具体描述',
    risk_propagation    TINYINT      COMMENT '风险传导系数1-10',
    create_time         DATETIME     DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE KEY uk_relation (company_id, related_company_id, relation_type),
    INDEX idx_related (related_company_id)
) ENGINE=InnoDB COMMENT='关联企业';
```

---

## 3. ES 索引设计（搜索引擎）

### 3.1 credit_company_index

```json
{
  "mappings": {
    "properties": {
      "id":            { "type": "long" },
      "company_name":  { "type": "text", "analyzer": "ik_max_word", 
                         "fields": { "keyword": { "type": "keyword" }}},
      "credit_code":   { "type": "keyword" },
      "legal_person":  { "type": "keyword" },
      "phone":         { "type": "keyword" },
      "industry_name": { "type": "keyword" },
      "province":      { "type": "keyword" },
      "city":          { "type": "keyword" },
      "credit_grade":  { "type": "keyword" },
      "total_score":   { "type": "integer" },
      "tags":          { "type": "keyword" },
      "register_capital": { "type": "double" },
      "establish_date":{ "type": "date" },
      "business_status":{ "type": "keyword" },
      "is_blacklist":  { "type": "boolean" }
    }
  }
}
```

### 3.2 性能要求

- 模糊搜索响应 ≤ 200ms
- 支持拼音搜索（PinyinAnalyzer）
- 支持简繁体互转

---

## 4. Redis 缓存设计

| Key | 数据 | TTL |
|-----|------|-----|
| `credit:report:{company_id}` | 信用报告 JSON | 24h |
| `credit:score:{company_id}` | 信用分数 | 6h |
| `credit:blacklist:bloom` | 布隆过滤器 | 持久 |
| `credit:user:quota:{user_id}` | 用户额度 | 5min（穿透） |
| `credit:hot:companies` | 今日热门 | 1h |
| `credit:focus:{user_id}` | 关注列表 | 1h |

---

## 5. Doris OLAP 表（数据分析）

```sql
-- 商家查询行为分析
CREATE TABLE dws_credit_query_daily (
    dt DATE,
    user_id BIGINT,
    query_count INT,
    company_count INT,
    -- 其他指标
    AGGREGATE KEY (dt, user_id)
) DUPLICATE KEY (dt, user_id);

-- 客户信用变化分析
CREATE TABLE dws_credit_score_daily (
    dt DATE,
    company_id BIGINT,
    region VARCHAR(20),
    industry VARCHAR(32),
    avg_score DOUBLE,
    grade_distribution JSON
) AGGREGATE KEY (dt, region, industry);
```

---

## 6. 关键索引性能策略

| 查询场景 | 索引 | 预期 QPS |
|---------|------|---------|
| 按信用代码精确查 | `uk_credit_code` | 5000+ |
| 按名称模糊搜索 | ES | 1000+ |
| 信用分排序 | `idx_grade_score` | 500 |
| 查询日志按商家+时间 | `idx_user_time` | 200 |
| 黑名单查询（布隆过滤） | Redis | 10000+ |

---

## 7. 数据归档策略

| 表 | 在线保留 | 归档方式 |
|----|---------|---------|
| credit_query_log | 3 个月 | 月度归档至冷库 |
| credit_event | 24 个月 | 年度归档 |
| credit_score_history | 36 个月 | 季度归档 |
| credit_overdue_report | 永久 | —— |

---

## 8. 数据一致性策略

| 数据 | 一致性策略 |
|------|----------|
| 信用分计算 | 异步更新，最终一致（10 分钟内） |
| 黑名单状态 | 强一致（事务） |
| 查询额度扣减 | 强一致（Redis Lua + DB 事务） |
| 评分历史 | 每日 00:30 批处理快照 |
