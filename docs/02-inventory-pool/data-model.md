# 方案二：滞销库存共享池 - 数据模型

> 数据库：MySQL 8.0 + Elasticsearch（搜索） + Redis（缓存） + RocketMQ（异步）

---

## 1. ER 图

```
┌─────────────────┐
│  sku_dict       │ ←─── 标准 SKU 字典
└────────┬────────┘
         │
         ▼
┌─────────────────┐    ┌──────────────────┐
│ inventory_listing│←──→│inventory_image   │
│ (库存发布)        │    │ (图片视频)         │
└────────┬─────────┘    └──────────────────┘
         │
         │
         ▼
┌─────────────────┐    ┌──────────────────┐
│ inquiry         │←──→│ inquiry_message │
│ (询价单)         │    │ (议价消息)        │
└────────┬────────┘    └──────────────────┘
         │
         ▼
┌─────────────────┐    ┌──────────────────┐
│ pool_order      │←──→│ pool_order_item │
│ (订单主表)        │    │ (订单明细)         │
└────────┬────────┘    └──────────────────┘
         │
         ├──────────────────────────────┐
         ▼                              ▼
┌─────────────────┐           ┌──────────────────┐
│ pool_payment   │           │ pool_logistics   │
│ (支付/担保)      │           │ (物流跟踪)         │
└─────────────────┘           └──────────────────┘
         │
         ▼
┌─────────────────┐           ┌──────────────────┐
│ pool_invoice   │           │ pool_dispute     │
│ (开票)           │           │ (售后争议)         │
└─────────────────┘           └──────────────────┘

┌─────────────────┐           ┌──────────────────┐
│ price_index     │           │ inventory_index  │
│ (价格指数)        │           │ (库存指数)         │
└─────────────────┘           └──────────────────┘
```

---

## 2. 核心表结构

### 2.1 sku_dict - 标准 SKU 字典

```sql
CREATE TABLE sku_dict (
    id              BIGINT       PRIMARY KEY AUTO_INCREMENT,
    sku_code        VARCHAR(32)  UNIQUE NOT NULL COMMENT 'SKU 编码',
    sku_name        VARCHAR(255) NOT NULL COMMENT 'SKU 全名',
    category_l1     VARCHAR(32)  NOT NULL COMMENT '一级品类',
    category_l2     VARCHAR(32)  COMMENT '二级品类',
    material        VARCHAR(32)  NOT NULL COMMENT '材质',
    spec_diameter   VARCHAR(16)  COMMENT '直径/规格',
    spec_thickness  VARCHAR(16)  COMMENT '厚度',
    spec_width      VARCHAR(16)  COMMENT '宽度',
    spec_length     VARCHAR(16)  COMMENT '长度',
    factory_code    VARCHAR(16)  COMMENT '钢厂代码',
    factory_name    VARCHAR(64)  COMMENT '钢厂名称',
    grade           VARCHAR(16)  COMMENT '等级',
    package_type    VARCHAR(32)  COMMENT '包装',
    weight_per_unit DECIMAL(10,4) COMMENT '单件重量(吨)',
    is_active       TINYINT      DEFAULT 1,
    create_time     DATETIME     DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE KEY uk_sku_code (sku_code),
    INDEX idx_category (category_l1, category_l2),
    INDEX idx_material (material),
    INDEX idx_factory (factory_code)
) ENGINE=InnoDB COMMENT='标准 SKU 字典';
```

### 2.2 inventory_listing - 库存发布主表

```sql
CREATE TABLE inventory_listing (
    id                  BIGINT       PRIMARY KEY AUTO_INCREMENT,
    listing_no          VARCHAR(32)  UNIQUE NOT NULL COMMENT '发布编号',
    seller_id           BIGINT       NOT NULL COMMENT '卖家商家ID',
    seller_name         VARCHAR(255) COMMENT '卖家名称',
    sku_code            VARCHAR(32)  NOT NULL COMMENT 'SKU 编码',
    sku_name            VARCHAR(255) COMMENT 'SKU 名称（冗余）',
    
    -- 数量
    total_quantity      DECIMAL(12,2) NOT NULL COMMENT '总数量(吨)',
    available_quantity  DECIMAL(12,2) NOT NULL COMMENT '可售数量',
    sold_quantity       DECIMAL(12,2) DEFAULT 0 COMMENT '已售数量',
    locked_quantity     DECIMAL(12,2) DEFAULT 0 COMMENT '锁定数量(下单未付款)',
    
    sale_mode           VARCHAR(16)  COMMENT 'WHOLE整批/SPLIT可拆',
    min_split_quantity  DECIMAL(12,2) COMMENT '最小拆分数量',
    
    -- 价格
    unit_price          DECIMAL(12,2) NOT NULL COMMENT '单价(含税)',
    is_negotiable       TINYINT      DEFAULT 0 COMMENT '是否面议',
    market_price        DECIMAL(12,2) COMMENT '市场参考价',
    price_diff          DECIMAL(12,2) COMMENT '价差',
    
    -- 仓库
    warehouse_id        BIGINT       COMMENT '仓库ID',
    warehouse_name      VARCHAR(255) COMMENT '仓库名称',
    warehouse_address   VARCHAR(500) COMMENT '仓库地址',
    province            VARCHAR(32),
    city                VARCHAR(32),
    district            VARCHAR(32),
    longitude           DECIMAL(10,7) COMMENT '经度',
    latitude            DECIMAL(10,7) COMMENT '纬度',
    
    -- 物流
    logistics_modes     JSON         COMMENT '物流方式数组',
    delivery_time       VARCHAR(32)  COMMENT '发货时效',
    
    -- 交易条件
    payment_term        VARCHAR(16)  COMMENT 'NOW/D7/D15/D30',
    invoice_type        VARCHAR(16)  COMMENT 'VAT_13/VAT_GENERAL',
    
    -- 状态
    stock_status        VARCHAR(16)  COMMENT 'INSTOCK/INTRANSIT/PENDING',
    listing_status      VARCHAR(16)  DEFAULT 'PENDING_AUDIT' COMMENT 'PENDING_AUDIT/ON_SALE/SOLD_OUT/EXPIRED/REMOVED',
    audit_status        VARCHAR(16)  DEFAULT 'PENDING',
    audit_remark        VARCHAR(500),
    audit_time          DATETIME,
    
    -- 时效
    publish_time        DATETIME     COMMENT '发布时间',
    expire_time         DATETIME     NOT NULL COMMENT '到期时间',
    valid_days          INT          DEFAULT 7,
    
    -- 推广
    is_top              TINYINT      DEFAULT 0 COMMENT '是否置顶',
    top_expire_time     DATETIME,
    
    -- 数据统计
    view_count          INT          DEFAULT 0,
    inquiry_count       INT          DEFAULT 0,
    favorite_count      INT          DEFAULT 0,
    
    -- 卖家说明
    description         TEXT,
    
    create_time         DATETIME     DEFAULT CURRENT_TIMESTAMP,
    update_time         DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    INDEX idx_seller (seller_id, listing_status),
    INDEX idx_sku_status (sku_code, listing_status, expire_time),
    INDEX idx_status_time (listing_status, expire_time),
    INDEX idx_region (province, city)
) ENGINE=InnoDB COMMENT='库存发布';
```

### 2.3 inventory_image - 图片/视频

```sql
CREATE TABLE inventory_image (
    id              BIGINT       PRIMARY KEY AUTO_INCREMENT,
    listing_id      BIGINT       NOT NULL,
    media_type      VARCHAR(16)  COMMENT 'IMAGE/VIDEO',
    media_url       VARCHAR(500) NOT NULL,
    thumbnail_url   VARCHAR(500),
    media_role      VARCHAR(32)  COMMENT 'PANORAMA/LABEL/WEIGHT_BILL/CERT/OTHER',
    sort_order      INT          DEFAULT 0,
    create_time     DATETIME     DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_listing (listing_id, sort_order)
) ENGINE=InnoDB;
```

### 2.4 inquiry - 询价单

```sql
CREATE TABLE inquiry (
    id              BIGINT       PRIMARY KEY AUTO_INCREMENT,
    inquiry_no      VARCHAR(32)  UNIQUE,
    listing_id      BIGINT       NOT NULL,
    buyer_id        BIGINT       NOT NULL,
    seller_id       BIGINT       NOT NULL,
    intent_quantity DECIMAL(12,2) COMMENT '意向数量',
    target_price    DECIMAL(12,2) COMMENT '目标价',
    initial_message TEXT,
    
    status          VARCHAR(16)  DEFAULT 'OPEN' COMMENT 'OPEN/AGREED/REJECTED/EXPIRED/CONVERTED',
    agreed_price    DECIMAL(12,2) COMMENT '最终达成价格',
    agreed_quantity DECIMAL(12,2),
    agreed_time     DATETIME,
    
    round_count     INT          DEFAULT 0 COMMENT '议价轮次',
    
    create_time     DATETIME     DEFAULT CURRENT_TIMESTAMP,
    update_time     DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    expire_time     DATETIME,
    
    INDEX idx_listing (listing_id),
    INDEX idx_buyer (buyer_id),
    INDEX idx_seller (seller_id),
    INDEX idx_status (status)
) ENGINE=InnoDB;

CREATE TABLE inquiry_message (
    id              BIGINT       PRIMARY KEY AUTO_INCREMENT,
    inquiry_id      BIGINT       NOT NULL,
    sender_id       BIGINT       NOT NULL,
    sender_role     VARCHAR(16)  COMMENT 'BUYER/SELLER',
    message_type    VARCHAR(16)  COMMENT 'TEXT/PRICE_OFFER/IMAGE',
    content         TEXT,
    price_offer     DECIMAL(12,2) COMMENT '价格出价',
    is_read         TINYINT      DEFAULT 0,
    create_time     DATETIME     DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_inquiry (inquiry_id, create_time)
) ENGINE=InnoDB;
```

### 2.5 pool_order - 订单主表

```sql
CREATE TABLE pool_order (
    id                  BIGINT       PRIMARY KEY AUTO_INCREMENT,
    order_no            VARCHAR(32)  UNIQUE NOT NULL,
    listing_id          BIGINT       NOT NULL,
    inquiry_id          BIGINT       COMMENT '关联议价单',
    
    buyer_id            BIGINT       NOT NULL,
    buyer_name          VARCHAR(255),
    buyer_credit_code   VARCHAR(32),
    
    seller_id           BIGINT       NOT NULL,
    seller_name         VARCHAR(255),
    seller_credit_code  VARCHAR(32),
    
    -- 商品
    sku_code            VARCHAR(32),
    sku_name            VARCHAR(255),
    quantity            DECIMAL(12,2) NOT NULL,
    unit_price          DECIMAL(12,2) NOT NULL,
    goods_amount        DECIMAL(14,2) NOT NULL COMMENT '货款金额',
    tax_rate            DECIMAL(5,4)  DEFAULT 0.13,
    tax_amount          DECIMAL(14,2),
    
    -- 平台费用
    platform_fee_rate   DECIMAL(5,4)  DEFAULT 0.01 COMMENT '撮合费率',
    platform_fee        DECIMAL(14,2),
    logistics_fee       DECIMAL(14,2) COMMENT '物流费',
    other_fee           DECIMAL(14,2),
    total_amount        DECIMAL(14,2) NOT NULL COMMENT '总金额',
    
    -- 收货
    receiver_name       VARCHAR(64),
    receiver_phone      VARCHAR(32),
    receiver_address    VARCHAR(500),
    
    -- 开票
    invoice_type        VARCHAR(16),
    invoice_title       VARCHAR(255),
    invoice_credit_code VARCHAR(32),
    
    -- 物流方式
    logistics_mode      VARCHAR(16)  COMMENT 'SELF_PICKUP/PLATFORM_TMS/OTHER',
    
    -- 账期
    payment_term        VARCHAR(16),
    pay_due_date        DATE,
    
    -- 状态机
    order_status        VARCHAR(32)  COMMENT 'CREATED/PAID/SHIPPED/IN_TRANSIT/SIGNED/CONFIRMED/INVOICED/COMPLETED/CANCELLED/DISPUTING',
    cancel_reason       VARCHAR(500),
    
    -- 时间节点
    create_time         DATETIME     DEFAULT CURRENT_TIMESTAMP,
    pay_time            DATETIME,
    ship_time           DATETIME,
    sign_time           DATETIME,
    confirm_time        DATETIME,
    invoice_time        DATETIME,
    complete_time       DATETIME,
    
    update_time         DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    INDEX idx_buyer_status (buyer_id, order_status),
    INDEX idx_seller_status (seller_id, order_status),
    INDEX idx_listing (listing_id),
    INDEX idx_status_time (order_status, create_time)
) ENGINE=InnoDB COMMENT='订单主表';
```

### 2.6 pool_payment - 支付与担保

```sql
CREATE TABLE pool_payment (
    id              BIGINT       PRIMARY KEY AUTO_INCREMENT,
    payment_no      VARCHAR(32)  UNIQUE,
    order_id        BIGINT       NOT NULL,
    order_no        VARCHAR(32),
    
    payer_id        BIGINT       NOT NULL,
    payee_id        BIGINT       NOT NULL,
    
    amount          DECIMAL(14,2) NOT NULL,
    payment_method  VARCHAR(32)  COMMENT 'BANK/NETBANK/ACCEPT_DRAFT/BALANCE',
    payment_channel VARCHAR(64)  COMMENT '渠道',
    bank_serial_no  VARCHAR(64)  COMMENT '银行流水号',
    
    -- 担保账户
    escrow_account  VARCHAR(64)  COMMENT '担保账户',
    escrow_status   VARCHAR(16)  COMMENT 'PENDING/HELD/RELEASED/REFUNDED',
    
    -- 状态
    payment_status  VARCHAR(16)  COMMENT 'PENDING/SUCCESS/FAILED/REFUND',
    
    pay_time        DATETIME,
    release_time    DATETIME     COMMENT '放款给卖家时间',
    refund_time     DATETIME,
    
    create_time     DATETIME     DEFAULT CURRENT_TIMESTAMP,
    update_time     DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    INDEX idx_order (order_id),
    INDEX idx_payer (payer_id),
    INDEX idx_status (payment_status)
) ENGINE=InnoDB;
```

### 2.7 pool_logistics - 物流跟踪

```sql
CREATE TABLE pool_logistics (
    id                  BIGINT       PRIMARY KEY AUTO_INCREMENT,
    waybill_no          VARCHAR(32)  UNIQUE,
    order_id            BIGINT       NOT NULL,
    order_no            VARCHAR(32),
    
    logistics_mode      VARCHAR(16)  COMMENT 'SELF/PLATFORM_TMS/OTHER',
    carrier_id          BIGINT       COMMENT '承运商',
    carrier_name        VARCHAR(255),
    driver_name         VARCHAR(64),
    driver_phone        VARCHAR(32),
    vehicle_no          VARCHAR(32),
    
    -- 起止
    pickup_address      VARCHAR(500),
    pickup_longitude    DECIMAL(10,7),
    pickup_latitude     DECIMAL(10,7),
    delivery_address    VARCHAR(500),
    delivery_longitude  DECIMAL(10,7),
    delivery_latitude   DECIMAL(10,7),
    
    -- 数据
    quantity            DECIMAL(12,2),
    weight              DECIMAL(12,2),
    
    -- 状态
    logistics_status    VARCHAR(32)  COMMENT 'WAITING/LOADED/TRANSIT/ARRIVED/SIGNED/COMPLETED',
    estimated_arrival   DATETIME,
    actual_arrival      DATETIME,
    
    -- 单据
    weight_bill_url     VARCHAR(500) COMMENT '过磅单',
    sign_receipt_url    VARCHAR(500) COMMENT '签收单',
    
    create_time         DATETIME     DEFAULT CURRENT_TIMESTAMP,
    update_time         DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    INDEX idx_order (order_id),
    INDEX idx_status (logistics_status)
) ENGINE=InnoDB;

CREATE TABLE pool_logistics_track (
    id              BIGINT       PRIMARY KEY AUTO_INCREMENT,
    waybill_no      VARCHAR(32)  NOT NULL,
    track_time      DATETIME     NOT NULL,
    longitude       DECIMAL(10,7),
    latitude        DECIMAL(10,7),
    location_desc   VARCHAR(255),
    event_type      VARCHAR(32)  COMMENT 'GPS/EVENT',
    event_desc      VARCHAR(500),
    create_time     DATETIME     DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_waybill_time (waybill_no, track_time)
) ENGINE=InnoDB;
```

### 2.8 pool_invoice - 开票

```sql
CREATE TABLE pool_invoice (
    id              BIGINT       PRIMARY KEY AUTO_INCREMENT,
    invoice_no      VARCHAR(32)  UNIQUE COMMENT '发票编号',
    invoice_code    VARCHAR(32)  COMMENT '发票代码',
    order_id        BIGINT       NOT NULL,
    order_no        VARCHAR(32),
    
    invoice_type    VARCHAR(16)  COMMENT 'VAT_13/VAT_GENERAL/SMALL_1',
    
    seller_id       BIGINT,
    seller_name     VARCHAR(255),
    seller_tax_no   VARCHAR(32),
    
    buyer_id        BIGINT,
    buyer_name      VARCHAR(255),
    buyer_tax_no    VARCHAR(32),
    
    items_json      JSON         COMMENT '开票明细',
    amount_excl_tax DECIMAL(14,2),
    tax_amount      DECIMAL(14,2),
    amount_incl_tax DECIMAL(14,2),
    
    invoice_status  VARCHAR(16)  COMMENT 'PENDING/ISSUED/RED_FLUSHED/INVALID',
    issue_time      DATETIME,
    
    pdf_url         VARCHAR(500),
    ofd_url         VARCHAR(500),
    
    create_time     DATETIME     DEFAULT CURRENT_TIMESTAMP,
    
    INDEX idx_order (order_id),
    INDEX idx_buyer (buyer_id)
) ENGINE=InnoDB;
```

### 2.9 pool_dispute - 争议

```sql
CREATE TABLE pool_dispute (
    id                  BIGINT       PRIMARY KEY AUTO_INCREMENT,
    dispute_no          VARCHAR(32)  UNIQUE,
    order_id            BIGINT       NOT NULL,
    order_no            VARCHAR(32),
    initiator_id        BIGINT       NOT NULL,
    initiator_role      VARCHAR(16)  COMMENT 'BUYER/SELLER',
    dispute_type        VARCHAR(32)  COMMENT 'GOODS_MISMATCH/QUANTITY_SHORT/QUALITY/LOGISTICS_DAMAGE/INVOICE/OTHER',
    
    description         TEXT,
    expected_solution   VARCHAR(500),
    evidence_files      JSON,
    
    status              VARCHAR(16)  COMMENT 'OPEN/UNDER_REVIEW/RESOLVED/CLOSED',
    resolution          VARCHAR(500),
    resolution_amount   DECIMAL(14,2) COMMENT '赔付金额',
    
    create_time         DATETIME     DEFAULT CURRENT_TIMESTAMP,
    closed_time         DATETIME,
    
    INDEX idx_order (order_id),
    INDEX idx_status (status)
) ENGINE=InnoDB;
```

### 2.10 price_index - 价格指数

```sql
CREATE TABLE price_index (
    id              BIGINT       PRIMARY KEY AUTO_INCREMENT,
    index_date      DATE         NOT NULL,
    sku_code        VARCHAR(32),
    region_code     VARCHAR(20),
    
    avg_price       DECIMAL(12,2),
    min_price       DECIMAL(12,2),
    max_price       DECIMAL(12,2),
    transaction_volume DECIMAL(14,2) COMMENT '成交量(吨)',
    transaction_count INT,
    transaction_amount DECIMAL(18,2),
    
    price_change    DECIMAL(12,2) COMMENT '相比上日变化',
    
    create_time     DATETIME     DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE KEY uk_date_sku_region (index_date, sku_code, region_code),
    INDEX idx_sku_date (sku_code, index_date DESC)
) ENGINE=InnoDB;
```

---

## 3. ES 索引

### 3.1 inventory_listing_index

```json
{
  "mappings": {
    "properties": {
      "id":               { "type": "long" },
      "sku_code":         { "type": "keyword" },
      "category_l1":      { "type": "keyword" },
      "material":         { "type": "keyword" },
      "factory_name":     { "type": "keyword" },
      "title":            { "type": "text", "analyzer": "ik_max_word" },
      "available_quantity": { "type": "double" },
      "unit_price":       { "type": "double" },
      "warehouse_location": { "type": "geo_point" },
      "city":             { "type": "keyword" },
      "province":         { "type": "keyword" },
      "seller_credit":    { "type": "keyword" },
      "publish_time":     { "type": "date" },
      "expire_time":      { "type": "date" },
      "listing_status":   { "type": "keyword" }
    }
  }
}
```

支持地理位置查询：

```
{
  "query": {
    "bool": {
      "must": [
        { "term": { "sku_code": "LWHRB400E0200012SHA" }},
        { "term": { "listing_status": "ON_SALE" }}
      ],
      "filter": {
        "geo_distance": {
          "distance": "300km",
          "warehouse_location": { "lat": 31.23, "lon": 121.47 }
        }
      }
    }
  }
}
```

---

## 4. Redis 缓存

| Key | 用途 | TTL |
|-----|------|-----|
| `pool:listing:{id}` | 库存详情缓存 | 1h |
| `pool:hot:listings` | 热门库存 | 10min |
| `pool:price:{sku}:{region}` | 实时市场价 | 5min |
| `pool:order:lock:{listing_id}` | 库存锁定 | 30min |
| `pool:user:cart:{user_id}` | 购物车 | 7d |
| `pool:escrow:balance` | 担保账户余额 | 实时 |

---

## 5. 关键索引性能策略

| 查询场景 | 索引 | 性能 |
|---------|------|------|
| SKU + 地区 + 状态查询 | ES + geo_point | < 200ms |
| 商家库存列表 | `idx_seller` | < 100ms |
| 订单状态机查询 | `idx_*_status` | < 50ms |
| 物流轨迹（按时间） | `idx_waybill_time` | < 100ms |
| 价格指数日数据 | `uk_date_sku_region` | < 30ms |

---

## 6. 数据归档

| 表 | 在线保留 | 归档 |
|----|---------|------|
| pool_logistics_track | 6 个月 | 月度归档 |
| inquiry_message | 3 个月 | 月度归档 |
| pool_order（已完成）| 3 年 | 年度归档 |
| pool_invoice | 永久 | —— |

---

## 7. 数据一致性策略

| 数据 | 策略 |
|------|------|
| 库存数量扣减 | Redis Lua + DB 事务（强一致） |
| 担保账户金额 | 严格事务 + 银行对账 |
| 订单状态机 | Spring StateMachine + 幂等 |
| 物流轨迹 | 最终一致（异步同步） |
| 价格指数 | T+1 批处理 |
