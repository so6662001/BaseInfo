# 方案四：安全版询价竞价 - 数据模型

> 数据库：MySQL 8.0 + Elasticsearch（搜索） + Redis（缓存）

---

## 1. ER 图

```
┌──────────────────┐
│ rfq_buyer_       │ (买家准入)
│  qualification   │
└────────┬─────────┘
         │
         │
         ▼
┌──────────────────┐    ┌──────────────────┐
│ rfq_request      │←──→│ rfq_request_item │
│ (询价单主表)      │    │ (询价商品明细)     │
└────────┬─────────┘    └──────────────────┘
         │
         │ 一对多
         ▼
┌──────────────────┐    ┌──────────────────┐
│ rfq_quotation    │←──→│ rfq_quotation_   │
│ (商家报价)         │    │  item (报价明细)  │
└────────┬─────────┘    └──────────────────┘
         │
         │
         ▼
┌──────────────────┐    ┌──────────────────┐
│ rfq_quote_score  │    │ rfq_winner       │
│ (评分明细)         │    │ (中标记录)         │
└──────────────────┘    └──────────────────┘

┌──────────────────┐    ┌──────────────────┐
│ rfq_protected_   │    │ rfq_negotiation  │
│  customer        │    │ (议价记录)         │
│ (老客户保护名单)   │    └──────────────────┘
└──────────────────┘
```

---

## 2. 核心表结构

### 2.1 rfq_buyer_qualification - 买家准入

```sql
CREATE TABLE rfq_buyer_qualification (
    id                  BIGINT       PRIMARY KEY AUTO_INCREMENT,
    buyer_id            BIGINT       UNIQUE NOT NULL COMMENT '买家ID',
    company_name        VARCHAR(255),
    
    -- 资质信息
    business_years      INT          COMMENT '经营年限',
    annual_purchase     DECIMAL(18,2) COMMENT '年采购量(吨)',
    annual_purchase_amt DECIMAL(18,2) COMMENT '年采购额',
    industry            VARCHAR(64),
    
    -- 信用
    credit_grade        VARCHAR(8)   COMMENT '平台信用等级',
    
    -- 保证金
    deposit_amount      DECIMAL(14,2),
    deposit_status      VARCHAR(16)  COMMENT 'PAID/REFUNDED',
    
    -- 准入状态
    qualification_status VARCHAR(16) COMMENT 'PENDING/APPROVED/REJECTED/SUSPENDED',
    approval_time       DATETIME,
    rejection_reason    VARCHAR(500),
    
    -- 子账号
    has_sub_accounts    TINYINT      DEFAULT 0,
    sub_account_count   INT          DEFAULT 0,
    
    create_time         DATETIME     DEFAULT CURRENT_TIMESTAMP,
    update_time         DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    INDEX idx_status (qualification_status)
) ENGINE=InnoDB COMMENT='买家准入';
```

### 2.2 rfq_request - 询价单主表

```sql
CREATE TABLE rfq_request (
    id                  BIGINT       PRIMARY KEY AUTO_INCREMENT,
    rfq_no              VARCHAR(32)  UNIQUE NOT NULL COMMENT '询价单号',
    buyer_id            BIGINT       NOT NULL,
    buyer_name          VARCHAR(255),
    
    -- 基本信息
    title               VARCHAR(255) NOT NULL,
    rfq_type            VARCHAR(32)  COMMENT 'ONE_TIME/ANNUAL/LONG_TERM',
    description         TEXT,
    
    -- 预算
    budget_amount       DECIMAL(14,2) COMMENT '预算总额',
    budget_public       TINYINT      DEFAULT 0 COMMENT '预算是否公开',
    
    -- 紧急程度
    urgency             VARCHAR(16)  COMMENT 'NORMAL/URGENT/EMERGENCY',
    
    -- 商品总览
    total_items         INT          COMMENT '商品项数',
    total_quantity      DECIMAL(14,2) COMMENT '总数量(吨)',
    
    -- 交货
    delivery_address    VARCHAR(500),
    delivery_longitude  DECIMAL(10,7),
    delivery_latitude   DECIMAL(10,7),
    delivery_mode       VARCHAR(16)  COMMENT 'ONCE/BATCH',
    unloading_condition VARCHAR(32),
    
    -- 商务
    payment_term        VARCHAR(16)  COMMENT 'CASH/ACCEPT_DRAFT/D30/D60',
    invoice_type        VARCHAR(16)  COMMENT 'VAT_13/VAT_GENERAL',
    quality_grade       VARCHAR(16)  COMMENT 'FIRST/SECOND/AGREEMENT',
    quality_check       JSON,
    
    -- 评分权重
    score_weight_price      INT      DEFAULT 60,
    score_weight_delivery   INT      DEFAULT 20,
    score_weight_credit     INT      DEFAULT 10,
    score_weight_service    INT      DEFAULT 10,
    
    -- 老供应商保护
    enable_old_supplier_bonus TINYINT DEFAULT 1,
    
    -- 时间
    publish_time        DATETIME,
    quote_deadline      DATETIME     NOT NULL COMMENT '报价截止',
    evaluation_time     DATETIME     COMMENT '评标时间',
    
    -- 状态
    rfq_status          VARCHAR(16)  COMMENT 'DRAFT/PENDING_AUDIT/QUOTING/EVALUATING/AWARDED/CLOSED/CANCELLED',
    
    -- 推送
    pushed_supplier_count INT,
    received_quote_count  INT,
    
    -- 中标方式
    award_mode          VARCHAR(32)  COMMENT 'AUTO_HIGHEST_SCORE/MANUAL/SPLIT_BY_ITEM',
    
    create_time         DATETIME     DEFAULT CURRENT_TIMESTAMP,
    update_time         DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    INDEX idx_buyer_status (buyer_id, rfq_status),
    INDEX idx_status_time (rfq_status, quote_deadline)
) ENGINE=InnoDB COMMENT='询价单';
```

### 2.3 rfq_request_item - 询价商品明细

```sql
CREATE TABLE rfq_request_item (
    id                  BIGINT       PRIMARY KEY AUTO_INCREMENT,
    rfq_id              BIGINT       NOT NULL,
    item_no             INT          COMMENT '序号',
    
    sku_code            VARCHAR(32),
    sku_name            VARCHAR(255),
    category            VARCHAR(32),
    material            VARCHAR(32),
    spec                VARCHAR(64),
    factory_preference  JSON         COMMENT '钢厂偏好',
    
    quantity            DECIMAL(12,2) NOT NULL,
    delivery_days       INT          COMMENT '要求交期天数',
    
    note                VARCHAR(500),
    
    INDEX idx_rfq (rfq_id)
) ENGINE=InnoDB;
```

### 2.4 rfq_quotation - 商家报价主表

```sql
CREATE TABLE rfq_quotation (
    id                  BIGINT       PRIMARY KEY AUTO_INCREMENT,
    quotation_no        VARCHAR(32)  UNIQUE,
    rfq_id              BIGINT       NOT NULL,
    
    supplier_id         BIGINT       NOT NULL,
    supplier_name       VARCHAR(255),
    supplier_credit     VARCHAR(8),
    
    -- 总报价
    total_amount        DECIMAL(14,2) NOT NULL,
    
    -- 加分项
    is_old_supplier     TINYINT      DEFAULT 0,
    old_supplier_amount DECIMAL(14,2) COMMENT '累计交易额',
    old_supplier_bonus  INT          DEFAULT 0 COMMENT '老供应商加分',
    additional_services JSON         COMMENT '增值服务及加分',
    additional_bonus    INT          DEFAULT 0,
    
    -- 报价说明
    quotation_remark    TEXT,
    
    -- 评分
    price_score         DECIMAL(8,2),
    delivery_score      DECIMAL(8,2),
    credit_score_calc   DECIMAL(8,2),
    service_score       DECIMAL(8,2),
    weighted_score      DECIMAL(8,2) COMMENT '加权基础分',
    bonus_score         DECIMAL(8,2) COMMENT '加分项',
    final_score         DECIMAL(8,2) COMMENT '最终综合分',
    rank_position       INT          COMMENT '排名',
    
    -- 状态
    quotation_status    VARCHAR(16)  COMMENT 'DRAFT/SUBMITTED/AMENDED/AWARDED/LOST',
    
    -- 时间
    submit_time         DATETIME,
    amend_count         INT          DEFAULT 0,
    last_amend_time     DATETIME,
    
    create_time         DATETIME     DEFAULT CURRENT_TIMESTAMP,
    update_time         DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    UNIQUE KEY uk_rfq_supplier (rfq_id, supplier_id),
    INDEX idx_supplier_status (supplier_id, quotation_status),
    INDEX idx_rfq_score (rfq_id, final_score DESC)
) ENGINE=InnoDB COMMENT='报价主表';
```

### 2.5 rfq_quotation_item - 报价明细

```sql
CREATE TABLE rfq_quotation_item (
    id                  BIGINT       PRIMARY KEY AUTO_INCREMENT,
    quotation_id        BIGINT       NOT NULL,
    request_item_id     BIGINT       NOT NULL,
    
    sku_code            VARCHAR(32),
    quoted_unit_price   DECIMAL(12,2) NOT NULL,
    quoted_quantity     DECIMAL(12,2) NOT NULL,
    quoted_delivery_days INT,
    quoted_amount       DECIMAL(14,2),
    
    note                VARCHAR(500),
    
    INDEX idx_quotation (quotation_id),
    INDEX idx_request_item (request_item_id)
) ENGINE=InnoDB;
```

### 2.6 rfq_winner - 中标记录

```sql
CREATE TABLE rfq_winner (
    id                  BIGINT       PRIMARY KEY AUTO_INCREMENT,
    rfq_id              BIGINT       NOT NULL,
    quotation_id        BIGINT       NOT NULL,
    request_item_id     BIGINT       COMMENT '分项中标时填该项ID',
    
    supplier_id         BIGINT       NOT NULL,
    supplier_name       VARCHAR(255),
    
    awarded_amount      DECIMAL(14,2),
    awarded_quantity    DECIMAL(12,2),
    awarded_at          DATETIME,
    
    -- 商家确认
    confirm_status      VARCHAR(16)  COMMENT 'PENDING/CONFIRMED/REFUSED/EXPIRED',
    confirm_time        DATETIME,
    confirm_deadline    DATETIME     COMMENT '确认截止时间',
    
    -- 关联订单（确认后生成）
    order_id            BIGINT,
    
    create_time         DATETIME     DEFAULT CURRENT_TIMESTAMP,
    
    INDEX idx_rfq (rfq_id),
    INDEX idx_supplier (supplier_id),
    INDEX idx_confirm (confirm_status, confirm_deadline)
) ENGINE=InnoDB COMMENT='中标记录';
```

### 2.7 rfq_protected_customer - 老客户保护名单

```sql
CREATE TABLE rfq_protected_customer (
    id                  BIGINT       PRIMARY KEY AUTO_INCREMENT,
    supplier_id         BIGINT       NOT NULL COMMENT '商家ID',
    customer_company_id BIGINT       NOT NULL COMMENT '客户ID',
    
    cumulative_amount   DECIMAL(14,2) COMMENT '累计交易额',
    cumulative_orders   INT          COMMENT '累计订单数',
    last_trade_date     DATE,
    
    -- 保护级别
    protection_level    VARCHAR(16)  COMMENT 'BASIC/SILVER/GOLD',
    bonus_score         INT          COMMENT '保护加分',
    
    -- 加白请求
    whitelist_status    VARCHAR(16)  COMMENT 'AUTO/MANUAL_REQUESTED/APPROVED/REJECTED',
    whitelist_evidence  JSON,
    
    create_time         DATETIME     DEFAULT CURRENT_TIMESTAMP,
    update_time         DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    UNIQUE KEY uk_supplier_customer (supplier_id, customer_company_id),
    INDEX idx_customer (customer_company_id)
) ENGINE=InnoDB COMMENT='老客户保护';
```

### 2.8 rfq_negotiation - 议价记录

```sql
CREATE TABLE rfq_negotiation (
    id                  BIGINT       PRIMARY KEY AUTO_INCREMENT,
    quotation_id        BIGINT       NOT NULL,
    round_no            INT          COMMENT '轮次',
    
    initiator_role      VARCHAR(16)  COMMENT 'BUYER/SUPPLIER',
    message             TEXT,
    proposed_price      DECIMAL(12,2),
    
    response_status     VARCHAR(16)  COMMENT 'PENDING/ACCEPTED/REJECTED/COUNTERED',
    response_deadline   DATETIME,
    
    create_time         DATETIME     DEFAULT CURRENT_TIMESTAMP,
    
    INDEX idx_quotation (quotation_id, round_no)
) ENGINE=InnoDB;
```

### 2.9 rfq_recommendation_log - 推送记录

```sql
CREATE TABLE rfq_recommendation_log (
    id                  BIGINT       PRIMARY KEY AUTO_INCREMENT,
    rfq_id              BIGINT       NOT NULL,
    supplier_id         BIGINT       NOT NULL,
    
    match_score         DECIMAL(8,2) COMMENT '匹配分',
    rank_position       INT,
    is_old_supplier     TINYINT,
    
    -- 推送方式
    push_methods        JSON         COMMENT '推送方式数组',
    push_status         VARCHAR(16)  COMMENT 'PUSHED/READ/QUOTED/IGNORED',
    
    push_time           DATETIME,
    read_time           DATETIME,
    quote_time          DATETIME,
    
    create_time         DATETIME     DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE KEY uk_rfq_supplier (rfq_id, supplier_id)
) ENGINE=InnoDB COMMENT='推送记录';
```

### 2.10 rfq_violation - 违约记录

```sql
CREATE TABLE rfq_violation (
    id                  BIGINT       PRIMARY KEY AUTO_INCREMENT,
    rfq_id              BIGINT       NOT NULL,
    quotation_id        BIGINT,
    user_id             BIGINT       NOT NULL,
    user_role           VARCHAR(16)  COMMENT 'BUYER/SUPPLIER',
    
    violation_type      VARCHAR(32)  COMMENT 'NO_CONFIRM/NO_DELIVER/QUALITY/QUANTITY/...',
    violation_desc      TEXT,
    
    penalty_amount      DECIMAL(14,2),
    penalty_status      VARCHAR(16),
    credit_impact       INT,
    
    create_time         DATETIME     DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;
```

---

## 3. Redis 缓存

| Key | 用途 | TTL |
|-----|------|-----|
| `rfq:hot:requests` | 热门询价 | 10min |
| `rfq:supplier:rank:{rfq_id}` | 商家排名（实时刷新）| 1min |
| `rfq:supplier:protected:{supplier_id}` | 商家保护客户名单 | 1h |
| `rfq:cost_warning:{sku_code}` | SKU 成本警戒线 | 1h |
| `rfq:market_price:{sku_code}` | 市场参考价 | 5min |

---

## 4. ES 索引

```json
{
  "mappings": {
    "properties": {
      "id": { "type": "long" },
      "rfq_no": { "type": "keyword" },
      "title": { "type": "text", "analyzer": "ik_max_word" },
      "buyer_name": { "type": "keyword" },
      "category": { "type": "keyword" },
      "material": { "type": "keyword" },
      "total_quantity": { "type": "double" },
      "budget_amount": { "type": "double" },
      "delivery_location": { "type": "geo_point" },
      "city": { "type": "keyword" },
      "rfq_status": { "type": "keyword" },
      "publish_time": { "type": "date" },
      "quote_deadline": { "type": "date" }
    }
  }
}
```

---

## 5. 评分计算

### 5.1 实时评分 SQL

```sql
-- 计算单个报价的综合分
WITH rfq_stats AS (
    SELECT 
        rfq_id,
        MAX(total_amount) AS max_amount,
        MIN(total_amount) AS min_amount
    FROM rfq_quotation
    WHERE rfq_id = ?
    GROUP BY rfq_id
)
UPDATE rfq_quotation q
SET 
    price_score = 100 * (s.max_amount - q.total_amount) / NULLIF(s.max_amount - s.min_amount, 0),
    -- 其他评分计算
    final_score = (price_score * 0.6 + delivery_score * 0.2 + credit_score_calc * 0.1 + service_score * 0.1)
                  + bonus_score
FROM rfq_stats s
WHERE q.rfq_id = s.rfq_id AND q.id = ?;
```

---

## 6. 关键索引性能

| 场景 | 索引 |
|------|------|
| 商家报价查询 | `idx_supplier_status` |
| 评标排序 | `idx_rfq_score` |
| 中标确认时效 | `idx_confirm` |
| 老客户保护 | `uk_supplier_customer` |

---

## 7. 数据一致性

| 数据 | 策略 |
|------|------|
| 报价提交 | 强一致 + 截止时间锁 |
| 评分计算 | 截止后批处理 + Redis 缓存 |
| 中标公告 | 事务 + 通知队列 |
| 商家排名 | 准实时（5 秒延迟可接受） |
