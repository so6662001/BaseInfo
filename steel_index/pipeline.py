"""端到端流水线：原始挂牌价 + 库存 → 清洗 → 商家评分 → 加权 → 指数。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from . import config as cfg
from .cleaning import CleaningResult, clean_listings
from .credibility import CredibilityPanel, build_credibility_panel
from .diagnostics import build_diagnostics
from .index import IndexResult, compute_indices
from .schema import (
    COL_CATEGORY,
    COL_CITY,
    COL_DATE,
    COL_MERCHANT,
    COL_SPEC,
    COL_SPEC_GROUP,
    COL_STOCK,
    normalize_columns,
    normalize_category,
    extract_size,
    prepare_frame,
    spec_group_of,
)
from .weights import assign_sample_weights


@dataclass
class SteelIndexOutput:
    """一次完整计算的全部产出。"""

    indices: IndexResult
    cleaning: CleaningResult
    credibility: CredibilityPanel
    weighted: pd.DataFrame
    diagnostics: pd.DataFrame
    config: cfg.SteelIndexConfig

    @property
    def composite(self) -> pd.DataFrame:
        return self.indices.composite

    @property
    def variety(self) -> pd.DataFrame:
        return self.indices.variety

    def summary(self) -> str:
        parts = [self.cleaning.summary(), ""]
        comp = self.indices.composite
        if comp.empty:
            parts.append("综合指数：无可用数据")
            return "\n".join(parts)

        last = comp.iloc[-1]
        meta = self.indices.meta
        parts += [
            "钢铁综合行情指数（不分品类）",
            f"  区间       : {meta['date_start']:%Y-%m-%d} ~ {meta['date_end']:%Y-%m-%d}"
            f"  基期 {meta['base_date']:%Y-%m-%d} = {meta['base_value']:g}",
            f"  最新指数   : {last['index_value']:.2f}"
            f"（环比 {last['chg_1d']:+.2%}，周 {last['chg_5d']:+.2%}，月 {last['chg_22d']:+.2%}）",
            f"  加权均价   : {last['price_level']:.0f} 元/吨",
            f"  当日样本   : 匹配 {int(last['n_matched'])} 条 / 商家 {int(last['n_merchants'])} 家"
            f" / 单元 {int(last['n_cells'])} 个",
            f"  涨跌广度   : 涨 {int(last.get('n_up', 0))} 家 跌 {int(last.get('n_down', 0))} 家"
            f" 平 {int(last.get('n_flat', 0))} 家",
            "",
            "分品种行情指数（最新一期）",
        ]
        var = self.indices.variety
        if not var.empty:
            last_date = var[COL_DATE].max()
            snap = var.loc[var[COL_DATE] == last_date].sort_values("index_value",
                                                                   ascending=False)
            for row in snap.itertuples(index=False):
                flag = " [低置信]" if getattr(row, "low_confidence", False) else ""
                parts.append(
                    f"    {row.category_name:<8} 指数 {row.index_value:8.2f}"
                    f"  环比 {row.chg_1d:+.2%}"
                    f"  均价 {row.price_level:7.0f} 元/吨"
                    f"  单元 {int(row.n_cells):>3} 个{flag}"
                )
        return "\n".join(parts)


def attach_inventory(listings: pd.DataFrame, inventory: pd.DataFrame) -> pd.DataFrame:
    """把独立的库存表并入挂牌价明细。

    库存表的粒度常常比挂牌价粗（例如只到商家×品种×城市），这里自动探测
    双方共有的连接键，按最细可用粒度左连接。
    """
    inv = normalize_columns(inventory).copy()
    if COL_DATE not in inv or COL_STOCK not in inv:
        raise ValueError("库存表至少需要日期与库存量两列")

    inv[COL_DATE] = pd.to_datetime(inv[COL_DATE], errors="coerce")
    inv[COL_STOCK] = pd.to_numeric(inv[COL_STOCK], errors="coerce")
    if COL_CATEGORY in inv:
        inv[COL_CATEGORY] = inv[COL_CATEGORY].map(normalize_category).astype("string")
    if COL_SPEC in inv and COL_CATEGORY in inv:
        sizes = inv[COL_SPEC].map(extract_size)
        inv[COL_SPEC_GROUP] = [
            spec_group_of(c if isinstance(c, str) else "", s)
            for c, s in zip(inv[COL_CATEGORY], sizes)
        ]
    for col in (COL_MERCHANT, COL_CITY, COL_SPEC_GROUP):
        if col in inv:
            inv[col] = inv[col].astype("string").fillna("").str.strip()

    candidate_keys = [COL_DATE, COL_MERCHANT, COL_CATEGORY, COL_SPEC_GROUP, COL_CITY]
    keys = [k for k in candidate_keys if k in inv.columns and k in listings.columns]
    if COL_DATE not in keys:
        raise ValueError("库存表缺少可用于连接的日期列")

    inv_agg = inv.groupby(keys, observed=True)[COL_STOCK].sum().rename("_stock_ext")
    out = listings.join(inv_agg, on=keys)
    out[COL_STOCK] = out[COL_STOCK].fillna(out["_stock_ext"])
    return out.drop(columns=["_stock_ext"])


def run_index(listings: pd.DataFrame,
              inventory: Optional[pd.DataFrame] = None,
              config: cfg.SteelIndexConfig | None = None) -> SteelIndexOutput:
    """端到端计算钢铁行情指数。

    参数
    ----
    listings  : 商家挂牌价明细（列名支持中文别名）
    inventory : 可选的独立库存表，用于补全权重口径
    config    : 可选配置
    """
    config = config or cfg.SteelIndexConfig()

    prepared = prepare_frame(listings)
    if inventory is not None:
        prepared = attach_inventory(prepared, inventory)

    cleaned = clean_listings(prepared, config, already_prepared=True)
    panel = build_credibility_panel(cleaned.clean, cleaned.rejected, config)
    weighted = assign_sample_weights(cleaned.clean, panel, config)
    indices = compute_indices(weighted, config)
    diagnostics = build_diagnostics(cleaned, panel, weighted, indices)

    return SteelIndexOutput(
        indices=indices,
        cleaning=cleaned,
        credibility=panel,
        weighted=weighted,
        diagnostics=diagnostics,
        config=config,
    )
