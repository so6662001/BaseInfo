"""合成数据生成器：造一个已知"真值"的钢材市场，用来验证指数算法。

真实业务里没有标准答案，无法判断指数是否被脏数据带偏。这里显式构造真实价格路径，
再按现实中常见的方式往里掺垃圾数据（量纲填错、恶意压价、乱跳价、僵尸报价、
小号刷票、机器灌水、字段非法），从而可以定量检验：

    清洗+加权后算出来的指数，能否还原真实价格路径。

这是本算法的回归测试基线，也是调参时的评估依据。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Sequence

import numpy as np
import pandas as pd

from .config import DEFAULT_CATEGORY_WEIGHTS

CATEGORY_SETUP: Dict[str, dict] = {
    "rebar": {"name": "螺纹钢", "p0": 3750.0, "specs": ["Φ16", "Φ18", "Φ20", "Φ22", "Φ25"],
              "material": "HRB400E", "vol": 0.008},
    "hrc": {"name": "热轧卷板", "p0": 3900.0, "specs": ["3.0mm", "4.75mm", "5.75mm", "8.0mm"],
            "material": "Q235B", "vol": 0.009},
    "crc": {"name": "冷轧卷板", "p0": 4550.0, "specs": ["0.5mm", "1.0mm", "1.5mm"],
            "material": "DC01", "vol": 0.006},
    "plate": {"name": "中厚板", "p0": 3980.0, "specs": ["14mm", "20mm", "25mm", "40mm"],
              "material": "Q355B", "vol": 0.007},
    "wire_rod": {"name": "高线", "p0": 3820.0, "specs": ["Φ8", "Φ10"],
                 "material": "HPB300", "vol": 0.008},
    "section": {"name": "H型钢", "p0": 3880.0, "specs": ["200*200", "300*300", "400*200"],
                "material": "Q235B", "vol": 0.007},
}

CITIES: Sequence[str] = ("上海", "北京", "广州", "天津", "杭州", "武汉", "成都", "沈阳")
CITY_PREMIUM: Dict[str, float] = {
    "上海": 0.000, "北京": 0.006, "广州": 0.018, "天津": -0.004,
    "杭州": 0.004, "武汉": 0.008, "成都": 0.022, "沈阳": -0.010,
}
MILLS: Sequence[str] = ("沙钢", "永钢", "中天", "鞍钢", "首钢", "马钢", "日钢", "本钢")

MERCHANT_TYPES = (
    ("normal", 0.66),
    ("unit_error", 0.05),      # 把元/公斤当元/吨填
    ("manipulator_low", 0.05),  # 长期系统性压价
    ("manipulator_high", 0.03),  # 长期系统性抬价
    ("wild", 0.05),            # 每天乱跳价
    ("zombie", 0.08),          # 挂着不动价充数
    ("spammer", 0.03),         # 机器灌水刷条数
    ("clone", 0.05),           # 小号群同步报价
)


@dataclass
class SimulatedMarket:
    """合成市场。

    listings : 原始挂牌价明细（中文列名，含脏数据），直接喂给 run_index
    truth    : 真实品种价格与真实指数（不含任何噪声与脏数据）
    merchants: 商家类型标注，用于检验算法有没有抓出捣乱者
    """

    listings: pd.DataFrame
    truth: pd.DataFrame
    merchants: pd.DataFrame
    meta: Dict[str, object] = field(default_factory=dict)

    @property
    def dirty_merchants(self) -> List[str]:
        """应当被识别出来并排除在指数之外的捣乱商家。

        unit_error 不在其中：它只是把元/公斤当元/吨填，量纲修复后数据是可用的，
        正确的处理是修复而不是拉黑。
        """
        bad = {"manipulator_low", "manipulator_high", "wild", "spammer"}
        return self.merchants.loc[
            self.merchants["type"].isin(bad), "merchant_id"
        ].tolist()

    @property
    def clone_merchants(self) -> List[str]:
        return self.merchants.loc[
            self.merchants["type"] == "clone", "merchant_id"
        ].tolist()

    @property
    def normal_merchants(self) -> List[str]:
        """完全正常的商家。把它们拉黑才算真正的误伤。

        僵尸商家（长期不动价）被拉黑不算误伤：挂着半年不维护的报价本来就不该进指数，
        算法默认对它们降权，若行为持续到触发拉黑也是合理结果。
        """
        return self.merchants.loc[
            self.merchants["type"] == "normal", "merchant_id"
        ].tolist()

    def truth_composite(self) -> pd.DataFrame:
        """真实综合指数（按品种权重链式聚合真实价格路径）。"""
        wide = self.truth.pivot_table(index="date", columns="category",
                                      values="true_price", aggfunc="last").sort_index()
        w = pd.Series({c: DEFAULT_CATEGORY_WEIGHTS.get(c, 0.0) for c in wide.columns})
        w = w / w.sum()
        link = (wide / wide.shift(1)).fillna(1.0)
        comp_link = (link * w).sum(axis=1) / w.sum()
        comp_link.iloc[0] = 1.0
        return pd.DataFrame({"date": wide.index,
                             "true_index": 100.0 * comp_link.cumprod().to_numpy()})


def generate_market(n_days: int = 180, n_merchants: int = 120, seed: int = 20260824,
                    dirty: bool = True, start: str = "2025-01-02") -> SimulatedMarket:
    """生成合成市场数据。

    参数
    ----
    n_days      : 交易日天数
    n_merchants : 商家数量
    dirty       : 是否注入脏数据（False 时产出干净数据，用于验证算法无偏）
    """
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start=start, periods=n_days)
    cats = list(CATEGORY_SETUP)

    # --- 真实价格路径：共同因子 + 品种因子 ---
    common = rng.normal(0.0, 0.0055, size=n_days)
    common[0] = 0.0
    truth_rows = []
    true_paths: Dict[str, np.ndarray] = {}
    for cat in cats:
        setup = CATEGORY_SETUP[cat]
        idio = rng.normal(0.0, setup["vol"], size=n_days)
        idio[0] = 0.0
        path = setup["p0"] * np.exp(np.cumsum(common + idio))
        true_paths[cat] = path
        for d, p in zip(dates, path):
            truth_rows.append({"date": d, "category": cat, "true_price": float(p)})
    truth = pd.DataFrame(truth_rows)

    # --- 商家画像 ---
    types, probs = zip(*MERCHANT_TYPES)
    probs = np.array(probs, dtype="float64")
    probs = probs / probs.sum()
    if not dirty:
        types, probs = ("normal",), np.array([1.0])

    merchant_rows = []
    for i in range(n_merchants):
        mtype = str(rng.choice(types, p=probs))
        merchant_rows.append(
            {
                "merchant_id": f"M{i:04d}",
                "type": mtype,
                "city": str(rng.choice(CITIES)),
                "level": float(rng.normal(0.0, 0.012)),      # 个体价格偏好
                "scale": float(np.exp(rng.normal(6.2, 1.1))),  # 库存规模（吨）
                "activity": float(rng.uniform(0.45, 0.95)),   # 报价活跃度
            }
        )
    merchants = pd.DataFrame(merchant_rows)

    # 克隆小号：把 clone 类型商家分成若干 3 人小组。组内共享城市、库存规模、
    # 活跃度与随机源，报价才会像现实中的小号群一样分毫不差。
    clone_ids = merchants.loc[merchants["type"] == "clone", "merchant_id"].tolist()
    clone_group: Dict[str, str] = {}
    for k in range(0, len(clone_ids) - len(clone_ids) % 3, 3):
        leader = clone_ids[k]
        for m in clone_ids[k:k + 3]:
            clone_group[m] = leader
    if clone_group:
        m_idx = merchants.set_index("merchant_id")
        for member, leader in clone_group.items():
            for col in ("city", "level", "scale", "activity"):
                m_idx.loc[member, col] = m_idx.loc[leader, col]
        merchants = m_idx.reset_index()

    # --- 商家 SKU 组合 ---
    sku_map: Dict[str, List[tuple]] = {}
    for row in merchants.itertuples(index=False):
        leader = clone_group.get(row.merchant_id)
        if leader and leader in sku_map:
            sku_map[row.merchant_id] = list(sku_map[leader])
            continue
        n_cat = int(rng.integers(1, 4))
        picked = rng.choice(cats, size=min(n_cat, len(cats)), replace=False)
        skus = []
        for cat in picked:
            specs = CATEGORY_SETUP[cat]["specs"]
            n_spec = int(rng.integers(1, min(4, len(specs)) + 1))
            for spec in rng.choice(specs, size=n_spec, replace=False):
                skus.append((cat, str(spec), str(rng.choice(MILLS))))
        sku_map[row.merchant_id] = skus

    spec_premium = {
        (cat, spec): float(rng.normal(0.0, 0.012))
        for cat in cats for spec in CATEGORY_SETUP[cat]["specs"]
    }

    # 用商家序号而不是 hash(merchant_id) 派生随机源：Python 的字符串哈希带随机盐，
    # 用它会让同一个 seed 每次生成不同的市场，合成数据就失去可复现性了。
    merchant_order = {m: i for i, m in enumerate(merchants["merchant_id"])}

    records: List[dict] = []
    for row in merchants.itertuples(index=False):
        mid, mtype = row.merchant_id, row.type
        base_city = row.city
        leader = clone_group.get(mid, mid)
        # 同源小号共用一个随机源，报价才会分毫不差
        m_rng = np.random.default_rng([seed, merchant_order[leader]])
        zombie_locked: Dict[tuple, float] = {}

        for cat, spec, mill in sku_map[mid]:
            prem = 1.0 + CITY_PREMIUM.get(base_city, 0.0) + spec_premium[(cat, spec)]
            offset = 1.0 + row.level
            if mtype == "manipulator_low":
                offset *= 0.75
            elif mtype == "manipulator_high":
                offset *= 1.22

            stock_base = row.scale * float(np.exp(m_rng.normal(0.0, 0.4)))
            for t, d in enumerate(dates):
                if m_rng.random() > row.activity:
                    continue

                fair = true_paths[cat][t] * prem * offset
                noise = 1.0 + m_rng.normal(0.0, 0.0035)
                price = fair * noise

                if mtype == "wild":
                    price = fair * (1.0 + m_rng.uniform(-0.22, 0.22))
                elif mtype == "zombie":
                    key = (cat, spec, mill)
                    if key not in zombie_locked:
                        zombie_locked[key] = float(price)
                    price = zombie_locked[key]

                unit = "元/吨"
                if mtype == "unit_error":
                    price = price / 1000.0  # 按元/公斤填，但单位列仍写元/吨

                stock = stock_base * float(np.exp(m_rng.normal(0.0, 0.25)))
                rec = {
                    "日期": d,
                    "商家id": mid,
                    "城市": base_city,
                    "品种": CATEGORY_SETUP[cat]["name"],
                    "规格": spec,
                    "材质": CATEGORY_SETUP[cat]["material"],
                    "钢厂": mill,
                    "挂牌价": round(float(price), 0),
                    "单位": unit,
                    "库存量": round(float(stock), 1),
                }
                records.append(rec)

    if dirty:
        records.extend(_spam_records(merchants, sku_map, true_paths, dates, rng))

    df = pd.DataFrame.from_records(records)

    if dirty:
        df = _inject_record_level_noise(df, rng)

    meta = {
        "n_days": n_days,
        "n_merchants": n_merchants,
        "dirty": dirty,
        "categories": cats,
        "clone_groups": len(set(clone_group.values())),
    }
    return SimulatedMarket(listings=df.reset_index(drop=True), truth=truth,
                           merchants=merchants, meta=meta)


def _spam_records(merchants: pd.DataFrame, sku_map: Dict[str, List[tuple]],
                  true_paths: Dict[str, np.ndarray], dates: pd.DatetimeIndex,
                  rng: np.random.Generator) -> List[dict]:
    """机器灌水：某些商家在部分日子里一次性挂出几百条杂乱资源刷存在感。

    刷的是大量不同规格/钢厂组合而不是同一条重复，所以只靠去重是拦不住的，
    必须靠"单商家单日条数异常"这条规则识别。
    """
    spammers = merchants.loc[merchants["type"] == "spammer"]
    out: List[dict] = []
    for row in spammers.itertuples(index=False):
        skus = sku_map.get(row.merchant_id) or []
        if not skus:
            continue
        cats = sorted({s[0] for s in skus})
        for t, d in enumerate(dates):
            if rng.random() > 0.25:
                continue
            for _ in range(450):
                cat = str(rng.choice(cats))
                setup = CATEGORY_SETUP[cat]
                spec = str(rng.choice(setup["specs"]))
                price = true_paths[cat][t] * (1.0 + rng.normal(0.0, 0.05))
                out.append(
                    {
                        "日期": d,
                        "商家id": row.merchant_id,
                        "城市": row.city,
                        "品种": setup["name"],
                        "规格": spec,
                        "材质": setup["material"],
                        "钢厂": str(rng.choice(MILLS)),
                        "挂牌价": round(float(price), 0),
                        "单位": "元/吨",
                        "库存量": round(float(np.exp(rng.normal(3.0, 1.0))), 1),
                    }
                )
    return out


def _inject_record_level_noise(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """在记录级别掺入各类非法数据。"""
    out = df.copy()
    n = len(out)
    out["挂牌价"] = out["挂牌价"].astype("object")

    def pick(share: float) -> np.ndarray:
        return rng.choice(n, size=max(int(n * share), 1), replace=False)

    # 极端价格：手滑多打一位、少打两位、填 0、填负数、填天价
    idx = pick(0.006)
    factors = rng.choice([10.0, 0.01, 100.0], size=len(idx))
    out.loc[out.index[idx], "挂牌价"] = (
        pd.to_numeric(out.loc[out.index[idx], "挂牌价"]) * factors
    ).to_numpy()

    idx = pick(0.001)
    out.loc[out.index[idx], "挂牌价"] = 0

    idx = pick(0.001)
    out.loc[out.index[idx], "挂牌价"] = -880

    idx = pick(0.001)
    out.loc[out.index[idx], "挂牌价"] = 999999

    # 非数值价格
    idx = pick(0.001)
    out.loc[out.index[idx], "挂牌价"] = rng.choice(["电议", "面谈", "abc", ""], size=len(idx))

    # 商家标识为空
    idx = pick(0.001)
    out.loc[out.index[idx], "商家id"] = ""

    # 日期非法与未来日期
    idx = pick(0.001)
    out.loc[out.index[idx], "日期"] = pd.NaT
    idx = pick(0.0005)
    out.loc[out.index[idx], "日期"] = pd.Timestamp("2099-12-31")

    # 品种无法识别
    idx = pick(0.001)
    out.loc[out.index[idx], "品种"] = rng.choice(["钢材A", "其他", "未知品种"], size=len(idx))

    # 单位写法混乱（其中"元/公斤"是真实量纲，应被正确折算）
    idx = pick(0.004)
    out.loc[out.index[idx], "单位"] = "元/公斤"
    out.loc[out.index[idx], "挂牌价"] = (
        pd.to_numeric(out.loc[out.index[idx], "挂牌价"], errors="coerce") / 1000.0
    ).round(3).to_numpy()

    # 库存非法
    idx = pick(0.004)
    out.loc[out.index[idx], "库存量"] = -120.0
    idx = pick(0.002)
    out.loc[out.index[idx], "库存量"] = 9.9e6

    # 完全重复行
    idx = pick(0.008)
    out = pd.concat([out, out.iloc[idx]], ignore_index=True)

    return out.sample(frac=1.0, random_state=7).reset_index(drop=True)
