# 交付物 2：推荐码系统 + 看板 PRD

> 版本：v1.0  
> 适用对象：产品经理、研发工程师  
> 输出物：完整产品需求文档 + 数据模型 + API 设计

---

## 1. 业务概述

### 1.1 目标

构建一套完整的预售支撑系统，包括：

1. **推荐码系统**：双角色（老板 / 管理人员）推荐链路，自动追踪、自动结算
2. **定金锁单系统**：3 档定金（¥1 / ¥1000 / ¥6000）
3. **试用申请系统**：14 天免费试用全流程
4. **个人看板**：推荐人查看自己的推荐进度、收益、排名
5. **后台看板**：运营查看预售整体数据

### 1.2 核心用户

| 角色 | 主要操作 |
|------|---------|
| 老板（公司法人 / 实控人）| 推荐、查看公司收益 |
| 管理人员（员工）| 推荐、查看个人收益 |
| 新客户 | 通过推荐码注册、试用、签约 |
| 销售 / 客户经理 | 跟单、协助签约 |
| 运营 / 后台管理员 | 配置规则、监控数据 |

---

## 2. 功能架构

```
┌──────────────────────────────────────────────────────────┐
│              【预售支撑系统】                              │
├──────────────────────────────────────────────────────────┤
│  M1 推荐码管理      │ M2 推荐链路追踪    │ M3 双轨返佣      │
│  M4 定金锁单        │ M5 试用申请        │ M6 个人看板      │
│  M7 后台运营看板    │ M8 防作弊引擎      │ M9 自动结算      │
└──────────────────────────────────────────────────────────┘
```

---

## 3. 详细功能需求

### M1 - 推荐码管理

#### M1.1 推荐码生成规则

```
规则：HD-{角色码}{用户ID编码}
  HD       - 货袋子前缀
  角色码   - B(老板) / M(管理人员)
  用户ID编码 - Base32 加密的用户 ID

示例：
  HD-B12ABC   - 老板（用户 ID 12345）
  HD-M67XYZ   - 管理人员（用户 ID 67890）
```

#### M1.2 推荐码类型

| 类型 | 说明 | 适用 |
|------|------|------|
| **个人推荐码** | 默认，每个用户唯一 | 所有用户 |
| **活动推荐码** | 特定活动专用，如 "618" | 大型活动 |
| **代理推荐码** | 渠道合作专用 | 战略合作伙伴 |
| **临时推荐码** | 24 小时有效 | 一次性场景 |

#### M1.3 推荐码生成入口

- **APP 我的页**：默认显示个人推荐码
- **PC 工作台**：完整推荐管理
- **海报生成器**：一键生成带推荐码的海报
- **链接 / 二维码**：可复制、可分享

#### M1.4 推荐码状态

```
启用 → 暂停 → 失效 → 删除

注：
- 用户离职 30 天后自动暂停
- 暂停期间已推荐的客户仍归属该推荐人
- 离职超过 90 天自动失效
```

---

### M2 - 推荐链路追踪

#### M2.1 推荐归因规则

```
归因优先级（高到低）：
  1. 注册时填写的推荐码
  2. 注册时点击的推荐链接 / 二维码（30 天内）
  3. 销售人员手动绑定（仅运营有权限）
  4. 默认无推荐人

冲突处理：
  • 多渠道接触 → 取首次接触的推荐人
  • 推荐人变更 → 仅运营人工干预

时效：
  • 推荐链接有效期：30 天（点击后 30 天内注册有效）
  • 注册到付款的归属：30 天（注册后 30 天内付款仍归属推荐人）
```

#### M2.2 追踪埋点

| 事件 | 触发时机 | 数据 |
|------|---------|------|
| 推荐码点击 | 用户点击推荐链接/扫码 | 推荐码、IP、设备 |
| 注册成功 | 新用户完成注册 | 推荐码、新用户ID |
| 试用申请 | 提交试用 | 推荐码、新用户ID |
| 定金支付 | ¥1/1000/6000 支付成功 | 金额、支付时间 |
| 合同签署 | 电子签合同 | 合同金额、套餐 |
| 首次付款 | 正式付费 | 付款金额、订单号 |
| 续费 | 续费成功 | 续费金额、周期 |

#### M2.3 推荐链路可视化

```
张总 (老板) HD-B12ABC
   ├─ 推荐 [上海XX建材]  
   │    ├─ 注册 ✓ (10-12)
   │    ├─ 试用 ✓ (10-15)
   │    ├─ 定金 ¥1000 ✓ (10-18)
   │    ├─ 签约 ¥2,800/月 ✓ (10-28)
   │    └─ 返佣 ¥4,200 ✓ 已到账
   │
   ├─ 推荐 [江苏XX工程]
   │    ├─ 注册 ✓ (10-15)
   │    └─ 试用中... (距离签约 5 天)
   │
   └─ 推荐 [浙江XX物资]
        └─ 仅访问，未注册
```

---

### M3 - 双轨返佣

#### M3.1 返佣规则配置

```yaml
返佣规则:
  套餐档位:
    入门版:
      推荐返佣率: 10%       # 每月 ¥800 × 10% × 12 = ¥960/年
      首单奖励: ¥500
    标准版:
      推荐返佣率: 12%       # 每月 ¥1800 × 12% × 12 = ¥2,592/年
      首单奖励: ¥1,000
    专业版:
      推荐返佣率: 15%       # 每月 ¥2800 × 15% × 12 = ¥5,040/年
      首单奖励: ¥2,000
    旗舰版:
      推荐返佣率: 15%       # 每月 ¥3500 × 15% × 12 = ¥6,300/年
      首单奖励: ¥3,000
  
  分配规则:
    推荐人为老板:
      - 公司账户: 100%
    推荐人为管理人员:
      - 公司账户: 70%
      - 个人账户: 30%
  
  累进奖励:
    第 3 家: 额外 ¥3,000
    第 5 家: 额外 ¥10,000
    第 10 家: 额外 ¥30,000
  
  自身续费抵扣:
    每推荐 1 家成功: 续费抵 1 个月（按推荐人当前套餐计算）
    封顶：12 个月（推荐 12 家送 1 年）
```

#### M3.2 返佣结算流程

```
新客户首次付款
    ↓
T+0: 自动计算返佣金额（按规则）
    ↓
T+0: 创建返佣订单（状态：待结算）
    ↓
T+7: 退款风险期结束（无退款）
    ↓
T+7: 自动转入【可提现】状态
    ↓
推荐人申请提现 / 系统自动月结
    ↓
公司账户：对公转账（提供发票）
个人账户：微信/支付宝/银行卡（≥¥800 代扣个税）
礼品兑换：T+7 内寄出
```

#### M3.3 返佣金额示例

| 推荐场景 | 套餐 | 返佣率 | 计算 | 金额 |
|---------|------|--------|------|------|
| 老板推荐 1 家专业版 | 专业版 | 15% | ¥2,800 × 12 × 15% | ¥5,040 |
| 老板推荐 1 家专业版（含首单奖）| 专业版 | 15% | ¥5,040 + ¥2,000 | ¥7,040 |
| 管理人员推荐 1 家专业版 | 专业版 | 15% | ¥5,040 |
|   ↳ 公司分 70% | | | | ¥3,528 |
|   ↳ 个人分 30% | | | | ¥1,512 |
| 老板推荐 5 家（含累进奖）| 专业版 | 15% | ¥7,040 × 5 + ¥3,000 + ¥10,000 | ¥48,200 |

---

### M4 - 定金锁单

#### M4.1 三档定金

| 档位 | 定金 | 抵扣 | 退款 | 锁定权益 |
|------|------|------|------|---------|
| 轻试用 | ¥1 | 0 | 30 天可退 | 名额 + 试用资格 |
| 占席位 | ¥1,000 | 抵 ¥2,000 | 30 天可退 | 名额 + 价格锁 + 试用 |
| 创始锁定 | ¥6,000 | 抵 ¥12,000 | 14 天可退 | 名额 + 永久 7 折 + 试用 + 创始身份 |

#### M4.2 定金状态机

```
[未支付] → [已支付] → [已抵扣]
              │
              ├→ [申请退款] → [退款中] → [已退款]
              │
              └→ [超期失效]（90 天内未签约视为放弃）
```

#### M4.3 定金支付流程

```
用户选择档位 → 跳转支付页
        ↓
确认订单（金额、抵扣、套餐意向）
        ↓
微信/支付宝/银联支付
        ↓
支付成功 → 自动确认名额 + 发放试用资格
        ↓
推送通知（APP + 短信）
        ↓
30 天内：可申请退款
        ↓
正式签约：定金抵扣订单金额
```

---

### M5 - 试用申请

#### M5.1 试用流程

```
申请试用 → 上传企业资质（自动从 ERP 读取）
        ↓
选择智能体（录入/库存/出纳，可多选）
        ↓
试用配置（绑定公司数据范围）
        ↓
分配专属顾问（销售自动分配）
        ↓
开通 14 天试用期
        ↓
使用过程：每天数据收集
        ↓
T+7 中期检查（专属顾问主动联系）
        ↓
T+14 试用结束（自动生成 ROI 报告）
        ↓
转化签约 / 延期试用 / 放弃
```

#### M5.2 ROI 报告自动生成

```markdown
# 您的 14 天 AI 智能体试用报告

【尊敬的张总】

您在 2025-10-15 至 2025-10-29 期间试用了【录入 + 库存】智能体。
以下是您的真实数据成果：

──────────────────────────────────
📊 工作量
  录入处理：2,847 笔
  库存盘点：126 次
  出错率：0.02%（人工平均 1.5%）
  
📊 时间节省
  总节省工时：156 小时
  相当于：1 个全职员工 1 个月工作量
  
💰 成本对比
  人工成本：¥6,500（仓管 + 录入员）
  智能体成本：¥1,800（标准版）
  ───────────────
  您每月节省：¥4,700
  您每年节省：¥56,400
  
📈 ROI
  3.6 倍（首年）
  回本周期：12 天
──────────────────────────────────

【您的下一步建议】

✓ 您已超额满足升级标准
✓ 推荐套餐：专业版（含出纳智能体）
✓ 创始客户特权：永久 7 折 ¥1,960/月
✓ 14 天免费试用已结束，请在 7 天内签约

[立即签约] [咨询客户经理]
```

#### M5.3 试用期管理

- **每日数据收集**：使用次数、节省时长、出错率
- **每周中期检查**：专属顾问电话回访
- **试用前 1 天提醒**：APP Push + 短信
- **试用结束自动报告**：邮件 + APP 推送
- **试用结束后 7 天**：销售上门促单

---

### M6 - 个人看板

#### M6.1 推荐人看板（APP）

```
┌─────────────────────────────────────┐
│  ← 我的推荐                  ⋯     │
├─────────────────────────────────────┤
│                                     │
│  ┌───────────────────────────────┐  │
│  │  📊 我的成绩                    │  │
│  │  ────────────                  │  │
│  │  累计推荐  12 家                │  │
│  │  已成交     8 家                │  │
│  │  累计收益  ¥38,420             │  │
│  │  待结算    ¥6,500              │  │
│  │  可提现    ¥31,920             │  │
│  │                                │  │
│  │  [立即提现]                     │  │
│  └───────────────────────────────┘  │
│                                     │
│  ──────── 我的推荐码 ────────       │
│                                     │
│  ┌───────────────────────────────┐  │
│  │     HD-B12ABC                  │  │
│  │     [复制] [分享]               │  │
│  │                                │  │
│  │     [📷 一键生成海报]           │  │
│  │     [🔗 复制邀请链接]           │  │
│  └───────────────────────────────┘  │
│                                     │
│  ──────── 我的推荐进度 ────────     │
│                                     │
│  [全部] [已成交] [试用中] [未注册]   │
│                                     │
│  ┌───────────────────────────────┐  │
│  │ 上海XX建材        ✅ 已成交     │  │
│  │ 专业版 ¥2,800/月                │  │
│  │ 返佣 ¥5,040 (已到账)            │  │
│  │ 10-28 完成                      │  │
│  └───────────────────────────────┘  │
│                                     │
│  ┌───────────────────────────────┐  │
│  │ 江苏XX工程        🟡 试用中     │  │
│  │ 已试用 7/14 天                  │  │
│  │ 预计返佣 ¥5,040                 │  │
│  │ [催促签约 →]                    │  │
│  └───────────────────────────────┘  │
│                                     │
│  ──────── 排行榜 ────────           │
│                                     │
│  🥇 张总 18 家  ¥85,200             │
│  🥈 李经理 15 家 ¥40,500            │
│  🥉 我  8 家  ¥38,420  ↑           │
│                                     │
└─────────────────────────────────────┘
```

#### M6.2 推荐人看板（PC）

PC 端展示更多详细信息：
- 完整推荐链路图
- 月度趋势图表
- 详细返佣明细
- 礼品兑换记录
- 排行榜（带过滤）

---

### M7 - 后台运营看板

#### M7.1 实时数据大屏

详见 [05-presale-dashboard-prd.md](./05-presale-dashboard-prd.md)

#### M7.2 订单管理

- 全量订单列表（定金 / 试用 / 签约 / 续费）
- 状态筛选 + 多维度搜索
- 批量操作（推送、催单、改状态）

#### M7.3 推荐人管理

- 推荐人列表 + 排行榜
- 推荐链路追踪
- 返佣审核 + 异常处理
- 推荐码暂停 / 解禁

#### M7.4 规则配置

- 返佣规则配置
- 套餐定价配置
- 名额配置（5/6/7/8 折阶梯）
- 防作弊规则配置

---

### M8 - 防作弊引擎

#### M8.1 防作弊规则

| 风险 | 检测 | 处置 |
|------|------|------|
| 自荐自买 | 推荐人 ID = 注册人公司关联 | 拦截 + 警告 |
| 公司内部互推 | 法人 / 信用代码 / 税号查重 | 拦截 |
| 一单多推 | 系统按"首推"锁定 | 自动 |
| 离职后推 | 离职 30 天暂停推荐 | 自动 |
| 假合同套返佣 | 必须真实付款 + T+7 结算 | 自动 |
| 黑产批量注册 | 设备指纹 + IP 限流 | 拦截 |
| 退款套利 | 单推荐人退款率 > 30% | 暂停 + 人工 |

#### M8.2 关联企业识别

```
关联识别维度：
  • 法人是否相同
  • 实控人是否相同
  • 股东是否重叠
  • 注册地址相近
  • 联系方式相同
  • 税号前 N 位一致
```

#### M8.3 风险评级

```
推荐人风险评级：
  低风险：所有指标正常 → 自动结算
  中风险：1-2 个异常 → 人工审核 + 延期结算（T+30）
  高风险：≥ 3 个异常 → 暂停 + 调查
```

---

### M9 - 自动结算

#### M9.1 结算频次

| 类型 | 频次 | 说明 |
|------|------|------|
| 定金抵扣 | 实时 | 签约时自动抵扣 |
| 推荐返佣 | T+7（每周二）| 自动入账可提现余额 |
| 累进奖励 | 月结（次月 5 日）| 达到累计标准自动发放 |
| 续费抵扣 | 每月扣减 | 自动减免推荐人续费 |

#### M9.2 提现规则

| 项 | 规则 |
|----|------|
| 最低提现 | ¥100 |
| 提现频次 | 每月 2 次 |
| 到账时间 | T+1 至 T+3 工作日 |
| 个人代扣个税 | 单次 ≥ ¥800 代扣 20% |
| 公司代发 | 提供发票，转账到对公 |
| 礼品兑换 | T+7 寄出 |

---

## 4. 数据模型

### 4.1 核心表结构

#### referral_code - 推荐码

```sql
CREATE TABLE referral_code (
    id              BIGINT       PRIMARY KEY AUTO_INCREMENT,
    code            VARCHAR(32)  UNIQUE NOT NULL COMMENT '推荐码',
    user_id         BIGINT       NOT NULL COMMENT '推荐人用户ID',
    user_role       VARCHAR(16)  COMMENT 'BOSS / MANAGER',
    company_id      BIGINT       COMMENT '所属公司',
    code_type       VARCHAR(16)  COMMENT 'PERSONAL/CAMPAIGN/AGENT/TEMP',
    
    valid_from      DATE,
    valid_to        DATE,
    
    code_status     VARCHAR(16)  COMMENT 'ACTIVE/PAUSED/EXPIRED/DELETED',
    
    -- 统计冗余（更新频次较低，避免实时聚合）
    total_clicks    INT          DEFAULT 0,
    total_registers INT          DEFAULT 0,
    total_signed    INT          DEFAULT 0,
    total_amount    DECIMAL(14,2) DEFAULT 0,
    total_commission DECIMAL(14,2) DEFAULT 0,
    
    create_time     DATETIME     DEFAULT CURRENT_TIMESTAMP,
    update_time     DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    INDEX idx_user (user_id),
    INDEX idx_code_status (code_status)
) ENGINE=InnoDB COMMENT='推荐码';
```

#### referral_track - 推荐追踪

```sql
CREATE TABLE referral_track (
    id              BIGINT       PRIMARY KEY AUTO_INCREMENT,
    referral_code   VARCHAR(32)  NOT NULL,
    referrer_id     BIGINT       COMMENT '推荐人',
    referee_id      BIGINT       COMMENT '被推荐人(注册后)',
    referee_company VARCHAR(255) COMMENT '被推荐公司',
    referee_phone   VARCHAR(32),
    
    -- 漏斗状态
    click_time      DATETIME     COMMENT '点击时间',
    register_time   DATETIME,
    trial_apply_time DATETIME,
    deposit_time    DATETIME,
    sign_time       DATETIME,
    pay_time        DATETIME,
    
    -- 来源
    source_channel  VARCHAR(32)  COMMENT 'POSTER/LINK/QRCODE/MANUAL',
    source_ip       VARCHAR(64),
    source_device   VARCHAR(255),
    
    -- 当前状态
    track_status    VARCHAR(16)  COMMENT 'CLICKED/REGISTERED/TRIALING/DEPOSITED/SIGNED/PAID/LOST',
    
    create_time     DATETIME     DEFAULT CURRENT_TIMESTAMP,
    update_time     DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    INDEX idx_referrer (referrer_id),
    INDEX idx_referee (referee_id),
    INDEX idx_code (referral_code)
) ENGINE=InnoDB COMMENT='推荐追踪';
```

#### deposit_order - 定金订单

```sql
CREATE TABLE deposit_order (
    id                  BIGINT       PRIMARY KEY AUTO_INCREMENT,
    deposit_no          VARCHAR(32)  UNIQUE NOT NULL,
    user_id             BIGINT       NOT NULL,
    referral_code       VARCHAR(32),
    
    deposit_tier        VARCHAR(16)  COMMENT 'TRIAL/SEAT/FOUNDING',
    deposit_amount      DECIMAL(14,2) COMMENT '¥1/¥1000/¥6000',
    deduct_amount       DECIMAL(14,2) COMMENT '可抵扣金额',
    
    intent_package      VARCHAR(16)  COMMENT '意向套餐 BASIC/STANDARD/PRO/FLAGSHIP',
    
    deposit_status      VARCHAR(16)  COMMENT 'CREATED/PAID/DEDUCTED/REFUND_REQUESTED/REFUNDED/EXPIRED',
    pay_time            DATETIME,
    refund_deadline     DATETIME,
    refund_time         DATETIME,
    deduct_time         DATETIME,
    
    -- 支付信息
    payment_method      VARCHAR(32)  COMMENT 'WECHAT/ALIPAY/UNIONPAY',
    payment_no          VARCHAR(64),
    
    create_time         DATETIME     DEFAULT CURRENT_TIMESTAMP,
    
    INDEX idx_user (user_id),
    INDEX idx_status (deposit_status),
    INDEX idx_pay_time (pay_time)
) ENGINE=InnoDB COMMENT='定金订单';
```

#### trial_application - 试用申请

```sql
CREATE TABLE trial_application (
    id              BIGINT       PRIMARY KEY AUTO_INCREMENT,
    trial_no        VARCHAR(32)  UNIQUE,
    user_id         BIGINT       NOT NULL,
    
    selected_agents JSON         COMMENT '选择的智能体列表',
    intent_package  VARCHAR(16),
    
    advisor_id      BIGINT       COMMENT '专属顾问',
    
    -- 状态
    trial_status    VARCHAR(16)  COMMENT 'APPLIED/APPROVED/IN_TRIAL/COMPLETED/CONVERTED/ABANDONED',
    
    -- 时间
    apply_time      DATETIME,
    start_time      DATETIME,
    end_time        DATETIME     COMMENT 'start_time + 14 days',
    completed_time  DATETIME,
    
    -- ROI 数据
    roi_processed_count INT      COMMENT '处理笔数',
    roi_saved_hours     INT      COMMENT '节省工时',
    roi_saved_amount    DECIMAL(14,2),
    roi_report_url      VARCHAR(500),
    
    create_time     DATETIME     DEFAULT CURRENT_TIMESTAMP,
    update_time     DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    INDEX idx_user (user_id),
    INDEX idx_status (trial_status),
    INDEX idx_advisor (advisor_id)
) ENGINE=InnoDB COMMENT='试用申请';
```

#### subscription_order - 订阅订单（正式签约）

```sql
CREATE TABLE subscription_order (
    id                  BIGINT       PRIMARY KEY AUTO_INCREMENT,
    sub_no              VARCHAR(32)  UNIQUE,
    user_id             BIGINT       NOT NULL,
    referral_code       VARCHAR(32),
    deposit_order_id    BIGINT       COMMENT '关联定金订单',
    trial_id            BIGINT       COMMENT '关联试用',
    
    package_type        VARCHAR(16)  COMMENT 'BASIC/STANDARD/PRO/FLAGSHIP',
    monthly_price       DECIMAL(14,2) COMMENT '月费',
    discount_rate       DECIMAL(5,4)  COMMENT '折扣率(0.5/0.6/0.7/0.8)',
    actual_monthly      DECIMAL(14,2) COMMENT '实际月费',
    
    subscription_period INT          COMMENT '订阅周期(月)',
    total_amount        DECIMAL(14,2),
    deduct_amount       DECIMAL(14,2) COMMENT '定金抵扣',
    actual_paid         DECIMAL(14,2) COMMENT '实际支付',
    
    is_founding_customer TINYINT     DEFAULT 0 COMMENT '是否创始客户',
    
    contract_no         VARCHAR(64)  COMMENT '电子合同号',
    contract_url        VARCHAR(500),
    
    sub_status          VARCHAR(16)  COMMENT 'CREATED/PAID/ACTIVE/EXPIRED/CANCELLED',
    
    sign_time           DATETIME,
    activate_time       DATETIME,
    expire_time         DATETIME,
    
    create_time         DATETIME     DEFAULT CURRENT_TIMESTAMP,
    update_time         DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    INDEX idx_user (user_id),
    INDEX idx_referral (referral_code),
    INDEX idx_status (sub_status)
) ENGINE=InnoDB COMMENT='订阅订单';
```

#### commission_record - 返佣记录

```sql
CREATE TABLE commission_record (
    id                  BIGINT       PRIMARY KEY AUTO_INCREMENT,
    commission_no       VARCHAR(32)  UNIQUE,
    referral_code       VARCHAR(32),
    referrer_id         BIGINT       NOT NULL,
    referrer_role       VARCHAR(16)  COMMENT 'BOSS/MANAGER',
    
    sub_order_id        BIGINT       COMMENT '关联订阅订单',
    referee_user_id     BIGINT,
    
    commission_type     VARCHAR(32)  COMMENT 'FIRST_BUY/RENEWAL/PROGRESSIVE/SELF_DISCOUNT',
    
    base_amount         DECIMAL(14,2) COMMENT '佣金基数',
    commission_rate     DECIMAL(5,4) COMMENT '佣金率',
    total_commission    DECIMAL(14,2) COMMENT '总佣金',
    
    -- 双轨分配
    company_amount      DECIMAL(14,2) COMMENT '公司分成',
    individual_amount   DECIMAL(14,2) COMMENT '个人分成',
    
    -- 结算状态
    commission_status   VARCHAR(16)  COMMENT 'PENDING/AVAILABLE/WITHDRAWING/PAID/REJECTED',
    
    -- 时间
    earn_time           DATETIME     COMMENT '产生时间',
    available_time      DATETIME     COMMENT 'T+7 后变为可提现',
    pay_time            DATETIME     COMMENT '实际付款时间',
    
    create_time         DATETIME     DEFAULT CURRENT_TIMESTAMP,
    update_time         DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    INDEX idx_referrer (referrer_id),
    INDEX idx_status (commission_status),
    INDEX idx_available_time (available_time)
) ENGINE=InnoDB COMMENT='返佣记录';
```

#### withdraw_record - 提现记录

```sql
CREATE TABLE withdraw_record (
    id                  BIGINT       PRIMARY KEY AUTO_INCREMENT,
    withdraw_no         VARCHAR(32)  UNIQUE,
    user_id             BIGINT       NOT NULL,
    
    withdraw_amount     DECIMAL(14,2) NOT NULL,
    tax_amount          DECIMAL(14,2) DEFAULT 0 COMMENT '代扣个税',
    actual_amount       DECIMAL(14,2),
    
    payment_method      VARCHAR(32)  COMMENT 'WECHAT/ALIPAY/BANK/COMPANY_TRANSFER',
    payment_account     VARCHAR(255),
    
    withdraw_status     VARCHAR(16)  COMMENT 'PENDING/PROCESSING/SUCCESS/FAILED',
    apply_time          DATETIME,
    process_time        DATETIME,
    success_time        DATETIME,
    
    bank_serial_no      VARCHAR(64),
    
    create_time         DATETIME     DEFAULT CURRENT_TIMESTAMP,
    
    INDEX idx_user (user_id),
    INDEX idx_status (withdraw_status)
) ENGINE=InnoDB;
```

---

## 5. API 设计

### 5.1 推荐码相关

```
POST   /api/v1/referral/code/generate         生成个人推荐码
GET    /api/v1/referral/code/my               获取我的推荐码
GET    /api/v1/referral/code/poster           获取个性化海报
POST   /api/v1/referral/code/track            点击/扫码追踪上报
```

### 5.2 推荐进度相关

```
GET    /api/v1/referral/track/list            我的推荐列表
GET    /api/v1/referral/track/detail/{id}     推荐详情
GET    /api/v1/referral/leaderboard           排行榜
```

### 5.3 定金相关

```
POST   /api/v1/deposit/create                 创建定金订单
POST   /api/v1/deposit/pay                    支付定金
POST   /api/v1/deposit/refund                 申请退款
GET    /api/v1/deposit/my                     我的定金订单
```

### 5.4 试用相关

```
POST   /api/v1/trial/apply                    申请试用
GET    /api/v1/trial/my                       我的试用
GET    /api/v1/trial/roi-report/{id}          获取 ROI 报告
POST   /api/v1/trial/convert                  试用转签约
```

### 5.5 返佣相关

```
GET    /api/v1/commission/my                  我的返佣
GET    /api/v1/commission/balance             我的余额
POST   /api/v1/commission/withdraw            申请提现
GET    /api/v1/commission/withdraw/list       提现记录
```

### 5.6 后台运营

```
GET    /api/v1/admin/dashboard                运营大屏数据
GET    /api/v1/admin/orders                   订单管理
PUT    /api/v1/admin/commission/audit         返佣审核
PUT    /api/v1/admin/referral/disable         暂停推荐码
```

---

## 6. 关键业务流程

### 6.1 推荐 → 转化流程

```
推荐人分享海报
    ↓
新客户扫码 → 落地页
    ↓
点击「立即抢占名额」→ 注册（绑定推荐码）
    ↓
完成实名 + 公司认证
    ↓
[选择 1] 直接申请试用
[选择 2] 支付 ¥1/¥1000/¥6000 定金
    ↓
14 天试用期
    ↓
ROI 报告生成 → 销售跟进
    ↓
签约 → 支付（定金抵扣）
    ↓
开通服务
    ↓
T+7 退款风险期结束
    ↓
推荐人返佣到账（可提现）
    ↓
推荐人继续邀请（病毒传播）
```

### 6.2 双轨返佣分配流程

```
新客户付款 ¥33,600（专业版年费）
    ↓
计算返佣 = ¥33,600 × 15% = ¥5,040
    ↓
判断推荐人角色
    │
    ├─ 老板 → 公司分 100% = ¥5,040
    │     └→ 入账"推荐人公司账户"
    │
    └─ 管理人员 → 公司分 70% = ¥3,528
                个人分 30% = ¥1,512
        ├→ ¥3,528 入账"推荐人公司账户"
        └→ ¥1,512 入账"推荐人个人账户"
    ↓
T+7 后变为可提现
    ↓
推荐人申请提现 → 微信/银行/对公
```

---

## 7. 非功能需求

### 7.1 性能

- 推荐码点击追踪 < 100ms
- 定金支付 < 2s
- 试用申请 < 1s
- 个人看板加载 < 1s

### 7.2 数据准确性

- 返佣计算 100% 准确
- 定金抵扣 100% 准确
- 退款流程零差错

### 7.3 安全

- 推荐码防伪签名
- 防爬虫 / 防刷
- 资金交易审计日志
- 个人信息加密

---

## 8. 上线节奏

| 版本 | 周期 | 范围 |
|------|------|------|
| V0.5 (MVP) | 2 周 | 推荐码生成 + 追踪 + 个人看板 |
| V1.0 | 2 周 | 定金订单 + 试用申请 + 自动结算 |
| V1.5 | 2 周 | 后台运营 + 防作弊 + 个性化海报 |
| V2.0 | 4 周 | 大数据分析 + AI 推荐 + 排行榜 |

---

## 9. 验收标准

| 模块 | 验收 |
|------|------|
| 推荐码 | 唯一、可追踪、防伪 |
| 推荐追踪 | 全漏斗数据准确 |
| 定金订单 | 支付/退款 100% 准确 |
| 试用申请 | 14 天自动管理 |
| 返佣计算 | 100% 准确 |
| 自动结算 | T+7 准时入账 |
| 防作弊 | 异常拦截率 ≥ 95% |
| 后台 | 实时数据 ≤ 5 秒延迟 |
