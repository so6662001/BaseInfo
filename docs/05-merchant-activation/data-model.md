# 方案五：商家分层激活模型 - 数据模型

> 数据库：MySQL 8.0 + Doris (CDP) + Redis + ClickHouse（行为分析）

---

## 1. ER 图

```
┌──────────────────┐
│ merchant         │ (商家主表，复用现有用户表)
└────────┬─────────┘
         │
         ├────────────────┬────────────────┬────────────┐
         ▼                ▼                ▼            ▼
┌──────────────────┐ ┌──────────────────┐ ┌─────────┐ ┌───────────┐
│merchant_profile  │ │merchant_tier     │ │tag_ref  │ │mcm_record │
│ (360 画像)        │ │ (分层历史)         │ │(标签)   │ │(跟进记录)  │
└──────────────────┘ └──────────────────┘ └─────────┘ └───────────┘
         │
         ├────────────────┬─────────────────┐
         ▼                ▼                 ▼
┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐
│merchant_score    │ │merchant_quest    │ │opportunity       │
│(评分)             │ │(任务)              │ │(商机)              │
└──────────────────┘ └──────────────────┘ └──────────────────┘

┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐
│marketing_campaign│ │invitation_record │ │nps_survey        │
│(营销活动)          │ │(邀请记录)          │ │(NPS 调研)         │
└──────────────────┘ └──────────────────┘ └──────────────────┘
```

---

## 2. 核心表结构

### 2.1 merchant_profile - 商家 360° 画像

```sql
CREATE TABLE merchant_profile (
    id                  BIGINT       PRIMARY KEY AUTO_INCREMENT,
    merchant_id         BIGINT       UNIQUE NOT NULL,
    
    -- 基本信息
    company_name        VARCHAR(255),
    company_size        VARCHAR(32)  COMMENT 'SMALL/MEDIUM/LARGE',
    employee_count      INT,
    industry            VARCHAR(64),
    
    -- 经营数据
    annual_gmv          DECIMAL(18,2),
    annual_orders       INT,
    avg_order_amount    DECIMAL(14,2),
    customer_count      INT,
    warehouse_count     INT,
    
    -- 平台行为
    register_date       DATE,
    last_login_date     DATE,
    monthly_active_days INT,
    monthly_features_used INT,
    cumulative_orders   INT,
    
    -- 价值分层
    current_tier        VARCHAR(8)   COMMENT 'A/B/C/D',
    tier_score          DECIMAL(8,2),
    tier_updated_at     DATETIME,
    
    -- 偏好洞察
    preferred_categories JSON,
    purchase_preferences JSON,
    customer_industries  JSON,
    price_sensitivity   VARCHAR(16)  COMMENT 'LOW/MEDIUM/HIGH',
    
    -- 风险标识
    credit_risk         VARCHAR(16),
    business_risk       VARCHAR(16),
    churn_risk          VARCHAR(16)  COMMENT '流失风险',
    complaint_count     INT          DEFAULT 0,
    
    -- 客户经理
    kam_id              BIGINT       COMMENT '客户经理ID',
    kam_assigned_at     DATETIME,
    
    create_time         DATETIME     DEFAULT CURRENT_TIMESTAMP,
    update_time         DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    INDEX idx_tier (current_tier),
    INDEX idx_kam (kam_id),
    INDEX idx_churn_risk (churn_risk)
) ENGINE=InnoDB COMMENT='商家 360 画像';
```

### 2.2 merchant_tier - 分层历史

```sql
CREATE TABLE merchant_tier (
    id              BIGINT       PRIMARY KEY AUTO_INCREMENT,
    merchant_id     BIGINT       NOT NULL,
    tier_date       DATE         NOT NULL,
    
    tier_level      VARCHAR(8)   COMMENT 'A/B/C/D',
    total_score     DECIMAL(8,2),
    business_score  DECIMAL(8,2),
    activity_score  DECIMAL(8,2),
    payment_score   DECIMAL(8,2),
    influence_score DECIMAL(8,2),
    
    previous_tier   VARCHAR(8),
    tier_change     VARCHAR(16)  COMMENT 'UP/DOWN/STABLE',
    change_reason   VARCHAR(500),
    
    create_time     DATETIME     DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE KEY uk_merchant_date (merchant_id, tier_date),
    INDEX idx_tier_change (tier_change, tier_date)
) ENGINE=InnoDB COMMENT='分层历史';
```

### 2.3 merchant_score - 评分明细

```sql
CREATE TABLE merchant_score (
    id              BIGINT       PRIMARY KEY AUTO_INCREMENT,
    merchant_id     BIGINT       NOT NULL,
    snapshot_date   DATE,
    
    -- 业务规模
    monthly_gmv     DECIMAL(14,2),
    monthly_orders  INT,
    business_score  DECIMAL(8,2),
    
    -- 平台活跃
    weekly_login_days INT,
    monthly_features_used INT,
    last_login_days_ago INT,
    activity_score  DECIMAL(8,2),
    
    -- 付费贡献
    member_level    VARCHAR(16),
    paid_amount_monthly DECIMAL(14,2),
    payment_score   DECIMAL(8,2),
    
    -- 影响力
    invited_count   INT,
    nps_score       INT,
    case_publications INT,
    influence_score DECIMAL(8,2),
    
    total_score     DECIMAL(8,2),
    
    create_time     DATETIME     DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_merchant_date (merchant_id, snapshot_date)
) ENGINE=InnoDB;
```

### 2.4 merchant_tag - 标签

```sql
CREATE TABLE merchant_tag_definition (
    id              BIGINT       PRIMARY KEY AUTO_INCREMENT,
    tag_code        VARCHAR(64)  UNIQUE,
    tag_name        VARCHAR(64),
    tag_category    VARCHAR(32)  COMMENT 'ATTRIBUTE/BEHAVIOR/VALUE/RISK',
    tag_type        VARCHAR(16)  COMMENT 'AUTO/MANUAL',
    auto_rule       JSON,
    description     VARCHAR(500),
    is_active       TINYINT      DEFAULT 1,
    create_time     DATETIME     DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE merchant_tag_ref (
    id              BIGINT       PRIMARY KEY AUTO_INCREMENT,
    merchant_id     BIGINT       NOT NULL,
    tag_id          BIGINT       NOT NULL,
    tag_code        VARCHAR(64),
    tag_value       VARCHAR(255) COMMENT '可携带值',
    confidence      DECIMAL(5,4) COMMENT '置信度',
    source          VARCHAR(32)  COMMENT 'AUTO/MANUAL/IMPORT',
    operator_id     BIGINT,
    create_time     DATETIME     DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE KEY uk_merchant_tag (merchant_id, tag_id),
    INDEX idx_tag (tag_id)
) ENGINE=InnoDB COMMENT='商家标签';
```

### 2.5 mcm_record - 客户管理跟进记录

```sql
CREATE TABLE mcm_record (
    id              BIGINT       PRIMARY KEY AUTO_INCREMENT,
    merchant_id     BIGINT       NOT NULL,
    kam_id          BIGINT       NOT NULL COMMENT '客户经理',
    
    record_type     VARCHAR(32)  COMMENT 'VISIT/CALL/MEETING/WECHAT/EMAIL/...',
    record_date     DATE,
    record_time     TIME,
    
    title           VARCHAR(255),
    content         TEXT,
    
    -- 商家阶段
    customer_stage  VARCHAR(32)  COMMENT 'TRUST_BUILDING/OPPORTUNITY/CLOSING/MAINTENANCE',
    
    -- 跟进结果
    next_action     VARCHAR(500),
    next_date       DATE,
    
    create_time     DATETIME     DEFAULT CURRENT_TIMESTAMP,
    
    INDEX idx_merchant_date (merchant_id, record_date DESC),
    INDEX idx_kam_date (kam_id, record_date DESC)
) ENGINE=InnoDB COMMENT='客户跟进';
```

### 2.6 opportunity - 商机

```sql
CREATE TABLE opportunity (
    id              BIGINT       PRIMARY KEY AUTO_INCREMENT,
    merchant_id     BIGINT       NOT NULL,
    kam_id          BIGINT,
    
    title           VARCHAR(255),
    description     TEXT,
    
    opportunity_type VARCHAR(32) COMMENT 'UPGRADE/CROSS_SELL/UP_SELL/RENEWAL',
    related_product VARCHAR(64)  COMMENT '相关产品',
    estimated_value DECIMAL(14,2) COMMENT '预估价值',
    
    stage           VARCHAR(32)  COMMENT 'IDENTIFIED/QUALIFIED/PROPOSAL/NEGOTIATION/CLOSED_WON/CLOSED_LOST',
    probability     INT          COMMENT '成交概率',
    
    created_date    DATE,
    closed_date     DATE,
    
    closed_value    DECIMAL(14,2),
    closed_reason   VARCHAR(500),
    
    create_time     DATETIME     DEFAULT CURRENT_TIMESTAMP,
    update_time     DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    INDEX idx_merchant (merchant_id),
    INDEX idx_kam_stage (kam_id, stage)
) ENGINE=InnoDB;
```

### 2.7 merchant_quest - 成长任务

```sql
CREATE TABLE quest_definition (
    id              BIGINT       PRIMARY KEY AUTO_INCREMENT,
    quest_code      VARCHAR(64)  UNIQUE,
    quest_name      VARCHAR(255),
    quest_category  VARCHAR(32)  COMMENT 'NEWBIE/GROWTH/ACHIEVEMENT',
    target_tier     VARCHAR(8)   COMMENT '目标层级',
    
    description     TEXT,
    rule_json       JSON         COMMENT '完成规则',
    
    reward_points   INT,
    reward_badge    VARCHAR(64),
    reward_cash     DECIMAL(14,2),
    
    is_active       TINYINT      DEFAULT 1,
    create_time     DATETIME     DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB COMMENT='任务定义';

CREATE TABLE merchant_quest (
    id              BIGINT       PRIMARY KEY AUTO_INCREMENT,
    merchant_id     BIGINT       NOT NULL,
    quest_id        BIGINT       NOT NULL,
    quest_code      VARCHAR(64),
    
    progress_data   JSON         COMMENT '进度数据',
    completion_pct  DECIMAL(5,2),
    
    quest_status    VARCHAR(16)  COMMENT 'IN_PROGRESS/COMPLETED/EXPIRED',
    started_at      DATETIME,
    completed_at    DATETIME,
    
    reward_status   VARCHAR(16)  COMMENT 'PENDING/CLAIMED',
    reward_claimed_at DATETIME,
    
    create_time     DATETIME     DEFAULT CURRENT_TIMESTAMP,
    update_time     DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    UNIQUE KEY uk_merchant_quest (merchant_id, quest_id),
    INDEX idx_status (quest_status)
) ENGINE=InnoDB;
```

### 2.8 marketing_campaign - 营销活动

```sql
CREATE TABLE marketing_campaign (
    id              BIGINT       PRIMARY KEY AUTO_INCREMENT,
    campaign_code   VARCHAR(64)  UNIQUE,
    campaign_name   VARCHAR(255),
    campaign_type   VARCHAR(32)  COMMENT 'TRIGGER/SCHEDULED/MANUAL',
    
    -- 触达对象
    target_audience JSON         COMMENT '目标商家筛选条件',
    audience_count  INT,
    
    -- 触发条件
    trigger_event   VARCHAR(64),
    trigger_rules   JSON,
    
    -- 流程编排
    workflow_json   JSON         COMMENT '完整流程编排',
    
    -- 时间
    start_time      DATETIME,
    end_time        DATETIME,
    
    -- 数据效果
    sent_count      INT          DEFAULT 0,
    open_count      INT          DEFAULT 0,
    click_count     INT          DEFAULT 0,
    convert_count   INT          DEFAULT 0,
    
    campaign_status VARCHAR(16)  COMMENT 'DRAFT/ACTIVE/PAUSED/COMPLETED',
    
    creator_id      BIGINT,
    create_time     DATETIME     DEFAULT CURRENT_TIMESTAMP,
    update_time     DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE campaign_message (
    id              BIGINT       PRIMARY KEY AUTO_INCREMENT,
    campaign_id     BIGINT       NOT NULL,
    merchant_id     BIGINT       NOT NULL,
    
    channel         VARCHAR(32)  COMMENT 'PUSH/SMS/EMAIL/CALL/IN_APP',
    template_code   VARCHAR(64),
    content         TEXT,
    
    send_status     VARCHAR(16)  COMMENT 'PENDING/SENT/FAILED/READ/CLICKED',
    sent_time       DATETIME,
    read_time       DATETIME,
    click_time      DATETIME,
    
    create_time     DATETIME     DEFAULT CURRENT_TIMESTAMP,
    
    INDEX idx_campaign (campaign_id),
    INDEX idx_merchant_time (merchant_id, sent_time DESC)
) ENGINE=InnoDB;
```

### 2.9 invitation_record - 邀请记录

```sql
CREATE TABLE invitation_record (
    id                  BIGINT       PRIMARY KEY AUTO_INCREMENT,
    inviter_id          BIGINT       NOT NULL COMMENT '邀请方',
    invitee_id          BIGINT       COMMENT '被邀请方(注册后)',
    invitee_company     VARCHAR(255),
    invitee_phone       VARCHAR(32),
    
    invitation_code     VARCHAR(32)  COMMENT '邀请码',
    invitation_link     VARCHAR(500),
    
    register_status     VARCHAR(16)  COMMENT 'PENDING/REGISTERED',
    register_time       DATETIME,
    first_query_time    DATETIME,
    
    -- 奖励
    reward_status       VARCHAR(16)  COMMENT 'PENDING/CLAIMED',
    reward_points       INT,
    reward_amount       DECIMAL(14,2),
    
    create_time         DATETIME     DEFAULT CURRENT_TIMESTAMP,
    
    INDEX idx_inviter (inviter_id),
    INDEX idx_invitation_code (invitation_code)
) ENGINE=InnoDB;
```

### 2.10 nps_survey - NPS 调研

```sql
CREATE TABLE nps_survey (
    id              BIGINT       PRIMARY KEY AUTO_INCREMENT,
    survey_code     VARCHAR(32)  UNIQUE,
    survey_name     VARCHAR(255),
    quarter         VARCHAR(16)  COMMENT '2025-Q3',
    
    target_count    INT,
    response_count  INT,
    nps_score       DECIMAL(8,2),
    promoter_count  INT,
    detractor_count INT,
    
    create_time     DATETIME     DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE nps_response (
    id              BIGINT       PRIMARY KEY AUTO_INCREMENT,
    survey_id       BIGINT       NOT NULL,
    merchant_id     BIGINT       NOT NULL,
    
    score           INT          COMMENT '0-10',
    score_type      VARCHAR(16)  COMMENT 'PROMOTER/PASSIVE/DETRACTOR',
    feedback        TEXT,
    follow_up_status VARCHAR(16) COMMENT '回访状态',
    
    create_time     DATETIME     DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_survey_merchant (survey_id, merchant_id)
) ENGINE=InnoDB;
```

---

## 3. CDP 数据宽表 (Doris)

```sql
CREATE TABLE dws_merchant_cdp (
    merchant_id     BIGINT,
    snapshot_date   DATE,
    
    -- 基本属性
    company_name    VARCHAR(255),
    industry        VARCHAR(64),
    region          VARCHAR(20),
    register_days   INT,
    
    -- 业务指标
    monthly_gmv     DECIMAL(14,2),
    cumulative_gmv  DECIMAL(18,2),
    monthly_orders  INT,
    avg_order_amount DECIMAL(14,2),
    customer_count  INT,
    
    -- 行为指标
    monthly_logins  INT,
    monthly_active_days INT,
    last_login_days_ago INT,
    monthly_searches    INT,
    monthly_inquiries   INT,
    monthly_quotes      INT,
    
    -- 付费指标
    member_level    VARCHAR(16),
    paid_amount_total DECIMAL(14,2),
    paid_amount_monthly DECIMAL(14,2),
    
    -- 影响力
    invited_count   INT,
    nps_score       INT,
    
    -- 综合
    total_score     DECIMAL(8,2),
    tier_level      VARCHAR(8),
    
    DUPLICATE KEY (merchant_id, snapshot_date)
) ENGINE=OLAP;
```

---

## 4. Redis 缓存

| Key | 用途 | TTL |
|-----|------|-----|
| `mcm:profile:{merchant_id}` | 商家画像 | 1h |
| `mcm:tier:{merchant_id}` | 当前分层 | 24h |
| `mcm:tags:{merchant_id}` | 商家标签 | 1h |
| `mcm:kam:todo:{kam_id}` | 客户经理待办 | 5min |
| `mcm:campaign:running` | 进行中活动 | 10min |

---

## 5. 关键查询场景

### 5.1 找出可升级商家

```sql
-- 找出 30 天内可能升级 B 层的 C 层商家
SELECT merchant_id, total_score, monthly_gmv
FROM dws_merchant_cdp
WHERE snapshot_date = CURRENT_DATE
  AND tier_level = 'C'
  AND total_score >= 55  -- 接近 B 层阈值
  AND monthly_gmv >= 1500000  -- GMV 接近
ORDER BY total_score DESC
LIMIT 50;
```

### 5.2 找出流失风险商家

```sql
SELECT merchant_id, last_login_days_ago, monthly_gmv
FROM dws_merchant_cdp
WHERE snapshot_date = CURRENT_DATE
  AND tier_level IN ('A', 'B')
  AND last_login_days_ago >= 14
ORDER BY monthly_gmv DESC;
```

---

## 6. 数据一致性

| 数据 | 策略 |
|------|------|
| 分层计算 | 每周日凌晨批处理 |
| 标签计算 | 实时（行为标签）+ 每日（属性标签） |
| 画像聚合 | 每日 ETL |
| 营销触达 | 异步队列（RocketMQ） |
