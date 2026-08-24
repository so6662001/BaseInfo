"""输入数据规范化：列名对齐、品种归一、规格分组。

商家挂牌数据在采集端往往列名不统一（中英文混杂）、品种写法五花八门、规格是自由文本。
本模块把这些异构输入折叠成算法可以直接消费的标准列。
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, Optional

import numpy as np
import pandas as pd

from .config import CATEGORY_ALIASES, region_of

# 标准列名
COL_DATE = "date"
COL_MERCHANT = "merchant_id"
COL_CITY = "city"
COL_CATEGORY = "category"
COL_SPEC = "spec"
COL_MATERIAL = "material"
COL_BRAND = "brand"
COL_PRICE = "price"
COL_UNIT = "unit"
COL_STOCK = "stock_qty"

REQUIRED_COLUMNS = (COL_DATE, COL_MERCHANT, COL_CATEGORY, COL_PRICE)

# 派生列
COL_REGION = "region"
COL_SPEC_GROUP = "spec_group"
COL_SPEC_KEY = "spec_key"
COL_SIZE = "size_mm"
COL_SKU = "sku_id"
COL_CELL = "cell_id"

COLUMN_ALIASES: Dict[str, str] = {
    # 日期
    "日期": COL_DATE, "报价日期": COL_DATE, "挂牌日期": COL_DATE, "统计日期": COL_DATE,
    "dt": COL_DATE, "trade_date": COL_DATE, "quote_date": COL_DATE, "ds": COL_DATE,
    # 商家
    "商家": COL_MERCHANT, "商家id": COL_MERCHANT, "商家编号": COL_MERCHANT,
    "店铺id": COL_MERCHANT, "供应商": COL_MERCHANT, "shop_id": COL_MERCHANT,
    "seller_id": COL_MERCHANT, "vendor_id": COL_MERCHANT, "merchant": COL_MERCHANT,
    # 城市
    "城市": COL_CITY, "地区": COL_CITY, "市场": COL_CITY, "所在城市": COL_CITY,
    "market": COL_CITY, "region_city": COL_CITY,
    # 品种
    "品种": COL_CATEGORY, "品类": COL_CATEGORY, "钢材品种": COL_CATEGORY,
    "产品类型": COL_CATEGORY, "品名": COL_CATEGORY, "variety": COL_CATEGORY,
    "product": COL_CATEGORY, "cat": COL_CATEGORY,
    # 规格
    "规格": COL_SPEC, "规格型号": COL_SPEC, "尺寸": COL_SPEC, "spec_name": COL_SPEC,
    "specification": COL_SPEC,
    # 材质
    "材质": COL_MATERIAL, "钢种": COL_MATERIAL, "grade": COL_MATERIAL,
    # 品牌
    "钢厂": COL_BRAND, "品牌": COL_BRAND, "产地": COL_BRAND, "mill": COL_BRAND,
    "factory": COL_BRAND,
    # 价格
    "价格": COL_PRICE, "挂牌价": COL_PRICE, "报价": COL_PRICE, "单价": COL_PRICE,
    "挂牌价格": COL_PRICE, "含税价": COL_PRICE, "listing_price": COL_PRICE,
    "quote_price": COL_PRICE, "unit_price": COL_PRICE,
    # 单位
    "单位": COL_UNIT, "计价单位": COL_UNIT, "price_unit": COL_UNIT,
    # 库存
    "库存": COL_STOCK, "库存量": COL_STOCK, "可售库存": COL_STOCK, "资源量": COL_STOCK,
    "吨位": COL_STOCK, "stock": COL_STOCK, "inventory": COL_STOCK, "qty": COL_STOCK,
    "quantity": COL_STOCK,
}

# 常见计价单位 -> 折算到 元/吨 的倍数
UNIT_FACTORS: Dict[str, float] = {
    "元/吨": 1.0, "元/t": 1.0, "yuan/ton": 1.0, "cny/t": 1.0, "元每吨": 1.0,
    "rmb/t": 1.0, "元/公吨": 1.0,
    "元/公斤": 1000.0, "元/kg": 1000.0, "yuan/kg": 1000.0, "元/千克": 1000.0,
    "万元/吨": 10000.0, "万元/t": 10000.0,
    "元/克": 1_000_000.0,
}

# 规格尺寸分箱（mm），按品种给出行业惯用的档位。
SPEC_BINS: Dict[str, Iterable[float]] = {
    "rebar": (0, 10, 14, 18, 22, 28, 40, np.inf),
    "wire_rod": (0, 7, 9, 11, 14, np.inf),
    "coil_rebar": (0, 8, 10, 12, 14, np.inf),
    "hrc": (0, 2.0, 3.0, 5.75, 8.0, 12.0, np.inf),
    "crc": (0, 0.5, 1.0, 1.5, 2.0, 3.0, np.inf),
    "plate": (0, 12, 20, 30, 50, 100, np.inf),
    "section": (0, 100, 200, 300, 400, np.inf),
    "gi": (0, 0.5, 1.0, 1.5, 2.0, np.inf),
    "ppgi": (0, 0.4, 0.6, 1.0, np.inf),
    "seamless_pipe": (0, 60, 114, 219, 325, np.inf),
    "welded_pipe": (0, 50, 100, 200, 400, np.inf),
    "strip": (0, 2.0, 3.0, 5.0, np.inf),
}

_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)")


def _clean_key(name: str) -> str:
    return re.sub(r"[\s_\-]+", "", str(name)).strip().lower()


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """把输入列名映射到标准列名；未识别的列原样保留。"""
    alias = {_clean_key(k): v for k, v in COLUMN_ALIASES.items()}
    rename = {}
    for col in df.columns:
        key = _clean_key(col)
        if key in alias:
            rename[col] = alias[key]
        elif key in {_clean_key(c) for c in
                     (COL_DATE, COL_MERCHANT, COL_CITY, COL_CATEGORY, COL_SPEC,
                      COL_MATERIAL, COL_BRAND, COL_PRICE, COL_UNIT, COL_STOCK)}:
            rename[col] = key
    return df.rename(columns=rename)


def normalize_category(value) -> Optional[str]:
    """品种归一。识别不出来的返回 None，由清洗层按"未知品种"剔除。"""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    key = _clean_key(value)
    if not key:
        return None
    if key in CATEGORY_ALIASES:
        return CATEGORY_ALIASES[key]
    # 退化为包含匹配：优先匹配更长的别名，避免"热轧"抢走"热轧带钢"。
    for alias in sorted(CATEGORY_ALIASES, key=len, reverse=True):
        if alias and alias in key:
            return CATEGORY_ALIASES[alias]
    return None


def extract_size(spec) -> float:
    """从自由文本规格里抽出主尺寸（mm）。抽不到返回 NaN。"""
    if spec is None or (isinstance(spec, float) and np.isnan(spec)):
        return float("nan")
    text = str(spec).replace("Φ", " ").replace("φ", " ").replace("*", "x")
    m = _SIZE_RE.search(text)
    if not m:
        return float("nan")
    try:
        return float(m.group(1))
    except ValueError:
        return float("nan")


def spec_group_of(category: str, size: float) -> str:
    """把尺寸落入品种档位，形成价格单元的规格维度。

    分组而非用原始规格文本，是为了让每个价格单元有足够样本支撑稳健统计。
    """
    bins = SPEC_BINS.get(category)
    if bins is None or not np.isfinite(size):
        return "ALL"
    edges = list(bins)
    for i in range(len(edges) - 1):
        if edges[i] < size <= edges[i + 1]:
            lo, hi = edges[i], edges[i + 1]
            hi_txt = "+" if not np.isfinite(hi) else f"{hi:g}"
            return f"{lo:g}-{hi_txt}"
    return "ALL"


def normalize_unit(value) -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "元/吨"
    key = _clean_key(value)
    for unit in UNIT_FACTORS:
        if _clean_key(unit) == key:
            return unit
    return str(value)


def unit_factor(value) -> float:
    """返回把报价折算成元/吨的倍数；未知单位返回 NaN 交由清洗层处理。"""
    key = _clean_key(value) if value is not None else ""
    if not key:
        return 1.0
    for unit, factor in UNIT_FACTORS.items():
        if _clean_key(unit) == key:
            return factor
    return float("nan")


def prepare_frame(df: pd.DataFrame) -> pd.DataFrame:
    """列名归一 + 补齐缺省列 + 派生 region/spec_group/sku_id/cell_id。

    这一步只做结构整理，不丢弃任何行；合法性判定全部交给 cleaning 模块，
    以保证每条被剔除的数据都有明确的规则出处。
    """
    out = normalize_columns(df).copy()

    missing = [c for c in REQUIRED_COLUMNS if c not in out.columns]
    if missing:
        raise ValueError(f"输入数据缺少必需列: {missing}（已尝试列名别名映射）")

    for col, default in ((COL_CITY, "未知"), (COL_SPEC, ""), (COL_MATERIAL, ""),
                         (COL_BRAND, ""), (COL_UNIT, "元/吨")):
        if col not in out.columns:
            out[col] = default

    if COL_STOCK not in out.columns:
        out[COL_STOCK] = np.nan

    out[COL_DATE] = pd.to_datetime(out[COL_DATE], errors="coerce")
    for col in (COL_MERCHANT, COL_CITY, COL_SPEC, COL_MATERIAL, COL_BRAND):
        out[col] = out[col].astype("string").fillna("").str.strip()
    out[COL_PRICE] = pd.to_numeric(out[COL_PRICE], errors="coerce")
    out[COL_STOCK] = pd.to_numeric(out[COL_STOCK], errors="coerce")

    out["category_raw"] = out[COL_CATEGORY].astype("string")
    out[COL_CATEGORY] = out["category_raw"].map(normalize_category).astype("string")

    out[COL_REGION] = out[COL_CITY].map(region_of).astype("string")
    out[COL_SIZE] = out[COL_SPEC].map(extract_size)
    out[COL_SPEC_GROUP] = [
        spec_group_of(c if isinstance(c, str) else "", s)
        for c, s in zip(out[COL_CATEGORY], out[COL_SIZE])
    ]
    out[COL_SPEC_GROUP] = out[COL_SPEC_GROUP].astype("string")

    # SKU 用规范化后的原始规格，而不是规格档。
    # 用规格档会把同一档内的不同规格（如 Φ20 与 Φ22）当成同一条资源，
    # 既会被误判为重复挂牌白丢样本，也会让环比比较错对象。
    out[COL_SPEC_KEY] = [
        f"{s:g}" if np.isfinite(s) else _clean_key(txt)
        for s, txt in zip(out[COL_SIZE], out[COL_SPEC])
    ]
    out[COL_SPEC_KEY] = out[COL_SPEC_KEY].astype("string")

    out[COL_SKU] = (
        out[COL_MERCHANT] + "|" + out[COL_CATEGORY].fillna("NA") + "|"
        + out[COL_SPEC_KEY] + "|" + out[COL_CITY] + "|" + out[COL_BRAND]
    ).astype("string")
    out[COL_CELL] = (
        out[COL_CATEGORY].fillna("NA") + "|" + out[COL_SPEC_GROUP] + "|" + out[COL_CITY]
    ).astype("string")

    return out
