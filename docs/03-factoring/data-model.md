# 方案三：应收账款保理 - 数据模型

> 数据库：MySQL 8.0（业务） + PostgreSQL（OLAP 风控） + Redis（缓存） + Doris（数据仓库）

---

## 1. ER 图

```
┌──────────────────┐      ┌────────────────────┐
│ fin_user_credit  │      │  fin_funder        │
│ (商家授信)        │      │  (资金方管理)       │
└────────┬─────────┘      └──────────┬─────────┘
         │                            │
         │                            │
         ▼                            ▼
┌──────────────────────────────────────────────┐
│         fin_loan_application                 │
│         (融资申请主表)                        │
└──────────┬───────────────────────────────────┘
           │
           ├──────────────────────┬──────────────────┐
           ▼                      ▼                  ▼
   ┌────────────────┐    ┌──────────────┐   ┌──────────────────┐
   │ fin_receivable │    │ fin_contract │   │ fin_evidence     │
   │  (应收账款)     │    │  (合同)        │   │  (凭证)            │
   └────────┬───────┘    └──────────────┘   └──────────────────┘
            │
            ▼
   ┌────────────────┐
   │ fin_confirmation│
   │ (应收确权)       │
   └────────────────┘
           
           
           ▼ (申请通过后)
┌────────────────┐    ┌──────────────────┐    ┌──────────────────┐
│ fin_loan       │←──→│ fin_payment      │    │ fin_repayment    │
│ (借款合约)       │    │ (放款记录)         │    │ (还款记录)         │
└────────────────┘    └──────────────────┘    └──────────────────┘
        │
        ▼
┌─────────────────────┐
│ fin_repayment_plan  │
│ (还款计划)            │
└─────────────────────┘

┌─────────────────────┐    ┌──────────────────┐
│ fin_escrow_account  │    │ fin_risk_event   │
│ (监管账户)            │    │ (风控事件)         │
└─────────────────────┘    └──────────────────┘
```

---

## 2. 核心表结构

### 2.1 fin_user_credit - 商家授信主表

```sql
CREATE TABLE fin_user_credit (
    id                  BIGINT       PRIMARY KEY AUTO_INCREMENT,
    user_id             BIGINT       UNIQUE NOT NULL COMMENT '商家ID',
    
    -- 综合评分
    credit_score        INT          COMMENT '综合授信分0-1000',
    credit_grade        VARCHAR(8)   COMMENT 'AAA/AA/A/B/C',
    
    -- 各产品额度
    loan_total          DECIMAL(14,2) COMMENT '总授信额度',
    loan_credit_amount  DECIMAL(14,2) COMMENT '信用贷额度',
    loan_factoring_amount DECIMAL(14,2) COMMENT '保理额度',
    loan_warehouse_amount DECIMAL(14,2) COMMENT '仓单额度',
    loan_order_amount   DECIMAL(14,2) COMMENT '订单融资额度',
    
    -- 已用额度
    used_credit         DECIMAL(14,2) DEFAULT 0,
    used_factoring      DECIMAL(14,2) DEFAULT 0,
    used_warehouse      DECIMAL(14,2) DEFAULT 0,
    used_order          DECIMAL(14,2) DEFAULT 0,
    
    -- 利率配置
    base_rate           DECIMAL(8,6)  COMMENT '基础利率',
    risk_premium        DECIMAL(8,6)  COMMENT '风险溢价',
    
    -- 状态
    credit_status       VARCHAR(16)  COMMENT 'ACTIVE/SUSPENDED/EXPIRED/REVOKED',
    valid_from          DATE,
    valid_to            DATE,
    
    -- 风控评分各维度
    operation_score     INT,
    finance_score       INT,
    transaction_score   INT,
    industry_score      INT,
    
    last_review_time    DATETIME,
    next_review_time    DATETIME,
    
    create_time         DATETIME     DEFAULT CURRENT_TIMESTAMP,
    update_time         DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    INDEX idx_user (user_id),
    INDEX idx_grade (credit_grade)
) ENGINE=InnoDB COMMENT='商家授信';
```

### 2.2 fin_loan_application - 融资申请

```sql
CREATE TABLE fin_loan_application (
    id                  BIGINT       PRIMARY KEY AUTO_INCREMENT,
    application_no      VARCHAR(32)  UNIQUE NOT NULL,
    user_id             BIGINT       NOT NULL,
    
    product_type        VARCHAR(32)  COMMENT 'CREDIT_LOAN/FACTORING/ORDER_FIN/WAREHOUSE',
    
    -- 申请要素
    apply_amount        DECIMAL(14,2) NOT NULL COMMENT '申请金额',
    apply_term_days     INT          COMMENT '期限(天)',
    apply_purpose       VARCHAR(500) COMMENT '资金用途',
    
    -- 审批结果
    approval_status     VARCHAR(16)  COMMENT 'PENDING/AUTO_APPROVED/MANUAL_REVIEW/APPROVED/REJECTED/RETURNED',
    approval_amount     DECIMAL(14,2) COMMENT '审批金额',
    approval_term_days  INT,
    approval_rate       DECIMAL(8,6) COMMENT '审批利率(月利率)',
    risk_score          INT          COMMENT '风控分',
    risk_level          VARCHAR(16)  COMMENT '风险等级',
    
    -- 资金方
    funder_id           BIGINT       COMMENT '资金方ID',
    funder_name         VARCHAR(255),
    
    -- 审批流程
    auto_review_time    DATETIME,
    auto_review_result  VARCHAR(32),
    manual_reviewer_id  BIGINT,
    manual_review_time  DATETIME,
    manual_review_remark VARCHAR(1000),
    
    -- 拒绝/退回
    reject_reason       VARCHAR(500),
    return_reason       VARCHAR(500),
    
    -- 时间
    submit_time         DATETIME,
    approval_time       DATETIME,
    expire_time         DATETIME,
    
    create_time         DATETIME     DEFAULT CURRENT_TIMESTAMP,
    update_time         DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    INDEX idx_user_status (user_id, approval_status),
    INDEX idx_status_time (approval_status, create_time)
) ENGINE=InnoDB COMMENT='融资申请';
```

### 2.3 fin_receivable - 应收账款

```sql
CREATE TABLE fin_receivable (
    id                  BIGINT       PRIMARY KEY AUTO_INCREMENT,
    receivable_no       VARCHAR(32)  UNIQUE COMMENT '应收编号',
    user_id             BIGINT       NOT NULL COMMENT '商家ID',
    
    -- 应收方
    debtor_company_id   BIGINT       NOT NULL COMMENT '应收方公司ID(关联信用查询库)',
    debtor_name         VARCHAR(255),
    debtor_credit_code  VARCHAR(32),
    debtor_credit_grade VARCHAR(8)   COMMENT '应收方信用等级',
    
    -- 应收金额
    receivable_amount   DECIMAL(14,2) NOT NULL COMMENT '应收金额(含税)',
    
    -- 应收日期
    contract_date       DATE,
    delivery_date       DATE,
    invoice_date        DATE,
    pay_due_date        DATE         NOT NULL COMMENT '应付款日期',
    actual_pay_date     DATE         COMMENT '实际付款日期',
    
    -- 业务关联
    contract_no         VARCHAR(64),
    invoice_no          VARCHAR(32),
    business_source     VARCHAR(32)  COMMENT 'ERP/MANUAL/POOL_ORDER',
    business_id         BIGINT,
    
    -- 状态
    receivable_status   VARCHAR(16)  COMMENT 'PENDING/AVAILABLE/FACTORING/REPAID/OVERDUE/WRITE_OFF',
    factoring_status    VARCHAR(16)  COMMENT 'NONE/APPLIED/CONFIRMED/FUNDED/REPAID',
    
    -- 凭证
    evidence_files      JSON,
    
    -- 数据来源
    data_source         VARCHAR(32),
    last_sync_time      DATETIME,
    
    create_time         DATETIME     DEFAULT CURRENT_TIMESTAMP,
    update_time         DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    INDEX idx_user_status (user_id, receivable_status),
    INDEX idx_debtor (debtor_company_id),
    INDEX idx_due_date (pay_due_date)
) ENGINE=InnoDB COMMENT='应收账款';
```

### 2.4 fin_confirmation - 应收确权

```sql
CREATE TABLE fin_confirmation (
    id                  BIGINT       PRIMARY KEY AUTO_INCREMENT,
    receivable_id       BIGINT       NOT NULL,
    application_id      BIGINT       COMMENT '关联申请',
    
    -- 应收方
    debtor_company_id   BIGINT,
    debtor_contact_name VARCHAR(64),
    debtor_contact_phone VARCHAR(32),
    debtor_contact_email VARCHAR(255),
    
    -- 确权方式
    confirm_method      VARCHAR(32)  COMMENT 'ELECTRONIC/PAPER/PHONE/ONSITE',
    
    -- 确权状态
    confirm_status      VARCHAR(16)  COMMENT 'PENDING/AGREED/REJECTED/EXPIRED/DEFAULT_AGREED',
    confirm_time        DATETIME,
    confirm_evidence    JSON         COMMENT '确权凭证',
    confirm_remark      TEXT,
    reject_reason       VARCHAR(500),
    
    -- 通知
    notify_count        INT          DEFAULT 0,
    notify_history      JSON,
    
    -- 默认确权（超时自动）
    auto_confirm_time   DATETIME,
    
    create_time         DATETIME     DEFAULT CURRENT_TIMESTAMP,
    update_time         DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    INDEX idx_receivable (receivable_id),
    INDEX idx_status (confirm_status)
) ENGINE=InnoDB COMMENT='应收确权';
```

### 2.5 fin_loan - 借款合约

```sql
CREATE TABLE fin_loan (
    id                  BIGINT       PRIMARY KEY AUTO_INCREMENT,
    loan_no             VARCHAR(32)  UNIQUE NOT NULL,
    application_id      BIGINT       NOT NULL,
    
    user_id             BIGINT       NOT NULL,
    funder_id           BIGINT       NOT NULL,
    
    product_type        VARCHAR(32),
    
    -- 借款要素
    principal           DECIMAL(14,2) NOT NULL COMMENT '本金',
    monthly_rate        DECIMAL(8,6)  NOT NULL COMMENT '月利率',
    annual_rate         DECIMAL(8,6)  COMMENT '年化利率',
    term_days           INT          COMMENT '期限(天)',
    term_months         INT          COMMENT '期限(月)',
    repayment_method    VARCHAR(32)  COMMENT 'INTEREST_FIRST/EQUAL/BALLOON',
    
    -- 时间
    contract_date       DATE,
    loan_start_date     DATE         COMMENT '起息日',
    loan_end_date       DATE         COMMENT '到期日',
    actual_complete_date DATE,
    
    -- 状态
    loan_status         VARCHAR(16)  COMMENT 'PENDING/ACTIVE/OVERDUE/COMPLETED/BAD_DEBT',
    
    -- 应还/已还
    total_principal     DECIMAL(14,2) COMMENT '应还本金',
    total_interest      DECIMAL(14,2) COMMENT '应还利息',
    total_due_amount    DECIMAL(14,2) COMMENT '总应还',
    total_repaid_principal DECIMAL(14,2) DEFAULT 0,
    total_repaid_interest  DECIMAL(14,2) DEFAULT 0,
    total_repaid        DECIMAL(14,2) DEFAULT 0,
    
    -- 罚息
    penalty_amount      DECIMAL(14,2) DEFAULT 0,
    overdue_days        INT          DEFAULT 0,
    
    -- 合同
    contract_url        VARCHAR(500),
    
    -- 银行存管
    escrow_loan_account VARCHAR(64),
    escrow_repay_account VARCHAR(64),
    
    create_time         DATETIME     DEFAULT CURRENT_TIMESTAMP,
    update_time         DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    INDEX idx_user_status (user_id, loan_status),
    INDEX idx_due_date (loan_end_date),
    INDEX idx_funder (funder_id)
) ENGINE=InnoDB COMMENT='借款合约';
```

### 2.6 fin_repayment_plan - 还款计划

```sql
CREATE TABLE fin_repayment_plan (
    id                  BIGINT       PRIMARY KEY AUTO_INCREMENT,
    loan_id             BIGINT       NOT NULL,
    period_no           INT          NOT NULL COMMENT '期数',
    
    due_date            DATE         NOT NULL,
    due_principal       DECIMAL(14,2),
    due_interest        DECIMAL(14,2),
    due_total           DECIMAL(14,2),
    remaining_principal DECIMAL(14,2) COMMENT '本期后剩余本金',
    
    -- 实际还款
    actual_pay_date     DATE,
    actual_principal    DECIMAL(14,2) DEFAULT 0,
    actual_interest     DECIMAL(14,2) DEFAULT 0,
    actual_penalty      DECIMAL(14,2) DEFAULT 0,
    actual_total        DECIMAL(14,2) DEFAULT 0,
    
    period_status       VARCHAR(16)  COMMENT 'PENDING/PAID/OVERDUE/PARTIAL',
    overdue_days        INT          DEFAULT 0,
    
    create_time         DATETIME     DEFAULT CURRENT_TIMESTAMP,
    update_time         DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    UNIQUE KEY uk_loan_period (loan_id, period_no),
    INDEX idx_due_date_status (due_date, period_status)
) ENGINE=InnoDB COMMENT='还款计划';
```

### 2.7 fin_payment - 放款记录

```sql
CREATE TABLE fin_payment (
    id              BIGINT       PRIMARY KEY AUTO_INCREMENT,
    payment_no      VARCHAR(32)  UNIQUE,
    loan_id         BIGINT       NOT NULL,
    
    funder_id       BIGINT,
    funder_account  VARCHAR(64),
    
    payee_user_id   BIGINT,
    payee_account   VARCHAR(64),
    payee_bank      VARCHAR(64),
    
    amount          DECIMAL(14,2) NOT NULL,
    payment_status  VARCHAR(16)  COMMENT 'PENDING/PROCESSING/SUCCESS/FAILED',
    
    bank_serial_no  VARCHAR(64),
    pay_time        DATETIME,
    failed_reason   VARCHAR(500),
    
    create_time     DATETIME     DEFAULT CURRENT_TIMESTAMP,
    
    INDEX idx_loan (loan_id),
    INDEX idx_status (payment_status)
) ENGINE=InnoDB COMMENT='放款记录';
```

### 2.8 fin_repayment - 还款记录

```sql
CREATE TABLE fin_repayment (
    id                  BIGINT       PRIMARY KEY AUTO_INCREMENT,
    repayment_no        VARCHAR(32)  UNIQUE,
    loan_id             BIGINT       NOT NULL,
    plan_period_no      INT          COMMENT '对应还款计划期数',
    
    payer_user_id       BIGINT,
    
    pay_amount          DECIMAL(14,2) NOT NULL,
    principal_amount    DECIMAL(14,2),
    interest_amount     DECIMAL(14,2),
    penalty_amount      DECIMAL(14,2) DEFAULT 0,
    
    repay_method        VARCHAR(32)  COMMENT 'AUTO_DEDUCT/MANUAL/RECEIVABLE_OFFSET',
    repay_source        VARCHAR(32)  COMMENT '资金来源',
    
    repay_status        VARCHAR(16)  COMMENT 'SUCCESS/FAILED/REVERSED',
    repay_time          DATETIME,
    bank_serial_no      VARCHAR(64),
    
    create_time         DATETIME     DEFAULT CURRENT_TIMESTAMP,
    
    INDEX idx_loan (loan_id),
    INDEX idx_repay_time (repay_time)
) ENGINE=InnoDB COMMENT='还款记录';
```

### 2.9 fin_escrow_account - 监管账户

```sql
CREATE TABLE fin_escrow_account (
    id                  BIGINT       PRIMARY KEY AUTO_INCREMENT,
    user_id             BIGINT       NOT NULL,
    account_type        VARCHAR(32)  COMMENT 'LOAN_RECEIVE/REPAY_SPECIAL/RECEIVABLE_COLLECT',
    
    bank_name           VARCHAR(64),
    bank_account        VARCHAR(64)  UNIQUE NOT NULL,
    account_holder      VARCHAR(255),
    
    balance             DECIMAL(14,2) DEFAULT 0,
    frozen_amount       DECIMAL(14,2) DEFAULT 0,
    available_amount    DECIMAL(14,2) DEFAULT 0,
    
    account_status      VARCHAR(16)  COMMENT 'ACTIVE/FROZEN/CLOSED',
    
    last_sync_time      DATETIME,
    
    create_time         DATETIME     DEFAULT CURRENT_TIMESTAMP,
    update_time         DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    INDEX idx_user_type (user_id, account_type)
) ENGINE=InnoDB COMMENT='监管账户';

CREATE TABLE fin_escrow_transaction (
    id                  BIGINT       PRIMARY KEY AUTO_INCREMENT,
    account_id          BIGINT       NOT NULL,
    transaction_no      VARCHAR(32)  UNIQUE,
    
    transaction_type    VARCHAR(32)  COMMENT 'IN/OUT/FROZEN/UNFROZEN',
    amount              DECIMAL(14,2) NOT NULL,
    balance_after       DECIMAL(14,2),
    
    counterparty        VARCHAR(255) COMMENT '对方账户/对方名称',
    business_type       VARCHAR(32)  COMMENT '业务类型',
    business_id         BIGINT,
    
    bank_serial_no      VARCHAR(64),
    transaction_time    DATETIME,
    
    description         VARCHAR(500),
    create_time         DATETIME     DEFAULT CURRENT_TIMESTAMP,
    
    INDEX idx_account_time (account_id, transaction_time DESC)
) ENGINE=InnoDB COMMENT='账户流水';
```

### 2.10 fin_funder - 资金方管理

```sql
CREATE TABLE fin_funder (
    id                  BIGINT       PRIMARY KEY AUTO_INCREMENT,
    funder_code         VARCHAR(32)  UNIQUE,
    funder_name         VARCHAR(255) NOT NULL,
    funder_type         VARCHAR(32)  COMMENT 'BANK/FACTORING/INTERNET_BANK',
    funder_level        VARCHAR(8)   COMMENT 'A/B/C',
    
    -- 合作模式
    cooperation_mode    VARCHAR(32)  COMMENT 'INTRODUCE/CO_LENDING/SELF_OPERATION',
    
    -- 资金额度
    total_quota         DECIMAL(18,2) COMMENT '合作总额度',
    used_quota          DECIMAL(18,2) DEFAULT 0,
    daily_quota         DECIMAL(18,2),
    
    -- 风险偏好
    preferred_industries JSON COMMENT '偏好行业',
    preferred_regions   JSON COMMENT '偏好地区',
    min_amount          DECIMAL(14,2),
    max_amount          DECIMAL(14,2),
    min_term_days       INT,
    max_term_days       INT,
    min_credit_grade    VARCHAR(8),
    
    -- 利率
    base_rate           DECIMAL(8,6),
    
    -- 处理时效
    sla_review_hours    INT,
    sla_funding_hours   INT,
    
    -- 分成
    profit_sharing_rate DECIMAL(5,4) COMMENT '平台利差分成比例',
    
    -- 接口
    api_endpoint        VARCHAR(500),
    api_credentials     VARCHAR(500),
    
    funder_status       VARCHAR(16)  COMMENT 'ACTIVE/SUSPENDED',
    
    create_time         DATETIME     DEFAULT CURRENT_TIMESTAMP,
    update_time         DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB COMMENT='资金方';
```

### 2.11 fin_risk_event - 风控事件

```sql
CREATE TABLE fin_risk_event (
    id                  BIGINT       PRIMARY KEY AUTO_INCREMENT,
    user_id             BIGINT       COMMENT '相关商家',
    loan_id             BIGINT       COMMENT '相关借款',
    
    event_type          VARCHAR(32)  COMMENT 'OVERDUE/REVENUE_DROP/PRICE_ANOMALY/...',
    event_level         VARCHAR(16)  COMMENT 'INFO/WARNING/CRITICAL',
    event_source        VARCHAR(32)  COMMENT 'AUTO_MONITOR/MANUAL/EXTERNAL',
    
    description         TEXT,
    detail_data         JSON,
    
    handle_status       VARCHAR(16)  COMMENT 'OPEN/HANDLING/RESOLVED/IGNORED',
    handle_time         DATETIME,
    handle_remark       VARCHAR(1000),
    
    create_time         DATETIME     DEFAULT CURRENT_TIMESTAMP,
    
    INDEX idx_user_time (user_id, create_time DESC),
    INDEX idx_loan (loan_id),
    INDEX idx_level_status (event_level, handle_status)
) ENGINE=InnoDB COMMENT='风控事件';
```

### 2.12 fin_evidence - 凭证

```sql
CREATE TABLE fin_evidence (
    id                  BIGINT       PRIMARY KEY AUTO_INCREMENT,
    business_type       VARCHAR(32)  COMMENT 'APPLICATION/RECEIVABLE/CONTRACT',
    business_id         BIGINT       NOT NULL,
    
    evidence_type       VARCHAR(32)  COMMENT 'CONTRACT/INVOICE/SHIPPING/RECEIPT/BANK_FLOW/OTHER',
    file_name           VARCHAR(255),
    file_url            VARCHAR(500),
    file_size           BIGINT,
    file_md5            VARCHAR(32),
    
    -- 验真
    verification_status VARCHAR(16)  COMMENT 'PENDING/VERIFIED/FAILED',
    verification_method VARCHAR(32)  COMMENT 'OCR/TAX_API/MANUAL',
    verification_result JSON,
    
    upload_user_id      BIGINT,
    upload_time         DATETIME     DEFAULT CURRENT_TIMESTAMP,
    
    INDEX idx_business (business_type, business_id)
) ENGINE=InnoDB;
```

---

## 3. Redis 缓存

| Key | 用途 | TTL |
|-----|------|-----|
| `fin:credit:{user_id}` | 商家授信信息 | 1h |
| `fin:rate:current` | 当前利率配置 | 24h |
| `fin:funder:available` | 可用资金方列表 | 10min |
| `fin:risk:score:{user_id}` | 风控评分 | 6h |
| `fin:account:balance:{account_id}` | 账户余额（穿透） | 1min |

---

## 4. 风控数据仓库（Doris/PostgreSQL）

### 4.1 商家行为汇总（每日）

```sql
CREATE TABLE dws_user_behavior_daily (
    dt                  DATE,
    user_id             BIGINT,
    
    -- 交易行为
    sales_amount        DECIMAL(14,2),
    sales_count         INT,
    avg_order_amount    DECIMAL(14,2),
    customer_count      INT,
    customer_concentration DECIMAL(8,4) COMMENT 'Top5占比',
    
    -- 应收
    receivable_total    DECIMAL(14,2),
    receivable_overdue  DECIMAL(14,2),
    avg_collection_days INT,
    
    -- 库存
    inventory_value     DECIMAL(14,2),
    inventory_turnover  INT,
    slow_inventory_value DECIMAL(14,2),
    
    -- 物流
    logistics_count     INT,
    sign_rate           DECIMAL(8,4),
    
    -- 平台行为
    login_count         INT,
    inquiry_count       INT,
    
    DUPLICATE KEY (dt, user_id)
) ENGINE=OLAP;
```

### 4.2 风控特征宽表

```sql
CREATE TABLE dws_risk_features (
    user_id             BIGINT,
    snapshot_date       DATE,
    
    -- 经营稳定特征
    business_age_months INT,
    monthly_revenue_avg DECIMAL(14,2),
    monthly_revenue_volatility DECIMAL(8,4),
    employee_count      INT,
    
    -- 财务健康特征
    debt_ratio          DECIMAL(8,4),
    current_ratio       DECIMAL(8,4),
    growth_rate         DECIMAL(8,4),
    bank_flow_stability DECIMAL(8,4),
    
    -- 交易真实特征
    logistics_evidence_rate DECIMAL(8,4),
    sign_evidence_rate  DECIMAL(8,4),
    contract_e_sign_rate DECIMAL(8,4),
    upstream_diversity  INT,
    downstream_diversity INT,
    
    -- 行业风险特征
    industry_index      DECIMAL(8,2),
    region_risk_score   INT,
    price_volatility    DECIMAL(8,4),
    
    -- 综合分
    operation_score     INT,
    finance_score       INT,
    transaction_score   INT,
    industry_score      INT,
    total_credit_score  INT
) ENGINE=OLAP;
```

---

## 5. 关键性能策略

| 场景 | 策略 |
|------|------|
| 授信计算 | 离线 T+1 计算 + Redis 缓存 |
| 借款申请 | 同步处理 + 30 秒内秒批 |
| 还款计算 | 实时计算 + 定时校验 |
| 监管账户余额 | 银行实时回调 + 平台缓存 |
| 风控监控 | Flink 实时流处理 |

---

## 6. 数据一致性

| 数据 | 策略 |
|------|------|
| 借款合约状态 | 严格事务 + 状态机 |
| 还款记录 | 强一致 + 银行对账 |
| 监管账户余额 | 银行为准（实时回调） |
| 应收转让 | 事务 + 应收方确权 |
| 风控决策 | 决策树 + 审计日志 |

---

## 7. 安全要求

| 项 | 要求 |
|----|------|
| 数据加密 | 敏感字段 AES-256 |
| 审计日志 | 全量记录，10 年保存 |
| 数据备份 | 异地双活 + 每日全量 |
| 访问控制 | RBAC + 数据权限 |
| 合规 | 等保三级 + 金融合规审计 |
