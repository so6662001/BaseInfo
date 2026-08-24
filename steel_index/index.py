"""指数编排：分品种行情指数与不分品类的综合行情指数。

两个指数共用同一套底层 link，是自上而下可分解的：
综合指数的日环比恰好等于各品种日环比的加权平均，因此"今天综合指数涨了 0.3%，
其中螺纹贡献 0.12 个百分点"这类归因可以直接给出，不会出现口径打架。

权重按期（默认按月）刷新，且刷新时用的是上一期的库存结构（point-in-time），
期间通过链式相乘衔接，既跟得上市场结构变化，又不会在换权日产生台阶。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

import numpy as np
import pandas as pd

from . import config as cfg
from .core import (
    COL_LINK,
    add_change_columns,
    aggregate_cell_links,
    aggregate_links,
    bootstrap_link_ci,
    build_daily_panel,
    chain_index,
    compute_sku_returns,
    ewma_smooth,
    impute_cell_links,
    market_breadth,
)
from .schema import (
    COL_CATEGORY,
    COL_CELL,
    COL_CITY,
    COL_DATE,
    COL_MERCHANT,
    COL_REGION,
    COL_SKU,
    COL_SPEC_GROUP,
)
from .config import CATEGORY_NAMES

COL_INDEX = "index_value"


@dataclass
class IndexResult:
    """指数计算结果集合。"""

    composite: pd.DataFrame
    """不分品类的钢铁综合行情指数（日频）。"""

    variety: pd.DataFrame
    """分品种行情指数（日频，长表，一行一个品种一天）。"""

    cells: pd.DataFrame
    """单元级明细（品种×规格档×城市），含 link、价格水平与样本质量。"""

    weights: Dict[str, pd.DataFrame] = field(default_factory=dict)
    breadth: pd.DataFrame = field(default_factory=pd.DataFrame)
    contribution: pd.DataFrame = field(default_factory=pd.DataFrame)
    meta: Dict[str, object] = field(default_factory=dict)

    def latest(self) -> pd.Series:
        if self.composite.empty:
            return pd.Series(dtype="float64")
        return self.composite.sort_values(COL_DATE).iloc[-1]

    def variety_pivot(self, value: str = COL_INDEX) -> pd.DataFrame:
        if self.variety.empty:
            return pd.DataFrame()
        return self.variety.pivot_table(index=COL_DATE, columns="category_name",
                                        values=value, aggfunc="last")


# --------------------------------------------------------------------------------------
# 权重（按期刷新 + point-in-time）
# --------------------------------------------------------------------------------------
def _period_start(dates: pd.Series, freq: str) -> pd.Series:
    per = pd.PeriodIndex(pd.DatetimeIndex(dates), freq=freq)
    return pd.Series(per.start_time, index=dates.index)


def periodic_weights(df: pd.DataFrame, keys: list[str], value_col: str,
                     freq: str = "M") -> pd.DataFrame:
    """按期计算权重：第 k 期的权重来自第 k-1 期的规模均值。

    用上一期的结构给本期加权，是 Laspeyres 指数的标准做法，
    也顺带避免了"当期规模突然放大 → 当期权重放大 → 当期价格被放大"的自我强化。
    """
    out_cols = ["weight_period", *keys, "weight_raw"]
    if df.empty:
        return pd.DataFrame(columns=out_cols)

    work = df.copy()
    work["weight_period"] = _period_start(work[COL_DATE], freq)
    per = (
        work.groupby(["weight_period", *keys], observed=True)[value_col]
        .mean()
        .rename("weight_raw")
        .reset_index()
    )
    periods = sorted(per["weight_period"].unique())
    if len(periods) == 0:
        return pd.DataFrame(columns=out_cols)

    prev_map = {periods[i]: periods[i - 1] for i in range(1, len(periods))}
    frames = []
    for p in periods:
        src = prev_map.get(p, p)  # 首期没有上一期，只能用本期结构
        block = per.loc[per["weight_period"] == src].copy()
        block["weight_period"] = p
        frames.append(block)
    return pd.concat(frames, ignore_index=True)[out_cols]


def _attach_periodic_weights(target: pd.DataFrame, weights: pd.DataFrame,
                             keys: list[str], freq: str, out_col: str) -> pd.DataFrame:
    out = target.copy()
    out["weight_period"] = _period_start(out[COL_DATE], freq)
    if weights.empty:
        out[out_col] = 1.0
        return out.drop(columns=["weight_period"])
    out = out.merge(weights, on=["weight_period", *keys], how="left")
    out[out_col] = out["weight_raw"].astype("float64")
    # 权重缺失（该期新出现的单元/品种）先给同期中位数，避免直接掉出指数。
    med = out.groupby(COL_DATE, observed=True)[out_col].transform("median")
    out[out_col] = out[out_col].fillna(med).fillna(1.0).clip(lower=0.0)
    return out.drop(columns=["weight_period", "weight_raw"])


def category_weights_table(cell_links: pd.DataFrame, config: cfg.SteelIndexConfig
                           ) -> pd.DataFrame:
    """品种权重表。

    fixed        : 采用外部给定的表观消费结构（默认），跨样本可比性最好；
    market_value : 由样本自身的库存×价格内生推导，更贴合本平台的资源结构；
    hybrid       : 两者几何折中，既锚定行业结构又反映样本实际（默认推荐）。
    """
    ic = config.index
    mode = ic.category_weight_mode
    freq = ic.weight_refresh

    if cell_links.empty:
        return pd.DataFrame(columns=["weight_period", COL_CATEGORY, "weight_raw"])

    mv = cell_links.assign(
        _mv=cell_links["cell_weight"].astype("float64") * cell_links["cell_price"].astype("float64")
    )
    endo = periodic_weights(mv, [COL_CATEGORY], "_mv", freq)
    endo["weight_raw"] = endo.groupby("weight_period", observed=True)["weight_raw"].transform(
        lambda s: s / s.sum() if s.sum() > 0 else s
    )

    if mode == "market_value":
        return endo

    fixed = config.category_weights
    endo["_fixed"] = endo[COL_CATEGORY].map(fixed).astype("float64")
    # 固定权重表里没有的品种（例如新增品种）回退到内生权重。
    endo["_fixed"] = endo["_fixed"].fillna(endo["weight_raw"])

    if mode == "fixed":
        endo["weight_raw"] = endo["_fixed"]
    elif mode == "hybrid":
        endo["weight_raw"] = np.sqrt(
            endo["weight_raw"].clip(lower=1e-9) * endo["_fixed"].clip(lower=1e-9)
        )
    else:
        raise ValueError(f"未知的品种权重模式: {mode}")

    endo["weight_raw"] = endo.groupby("weight_period", observed=True)["weight_raw"].transform(
        lambda s: s / s.sum() if s.sum() > 0 else s
    )
    return endo[["weight_period", COL_CATEGORY, "weight_raw"]]


# --------------------------------------------------------------------------------------
# 自适应价格单元
# --------------------------------------------------------------------------------------
_CELL_LADDER = (
    (COL_CATEGORY, COL_SPEC_GROUP, COL_CITY),
    (COL_CATEGORY, COL_CITY),
    (COL_CATEGORY, COL_REGION),
    (COL_CATEGORY,),
)


def adaptive_cells(df: pd.DataFrame, config: cfg.SteelIndexConfig) -> pd.DataFrame:
    """按样本密度自适应确定价格单元的粒度。

    理想的价格单元是"同品种同规格档同城市"，可比性最强；但真实数据里很多这样的
    单元一天只有一两条报价，算出来的环比全是噪声，只能靠插补，指数就失真了。
    这里对每条记录选择"日均 SKU 数达标的最细粒度"，从规格档逐级放宽到城市、区域、全国。
    """
    thr = config.index.min_skus_per_cell
    if df.empty or thr <= 0:
        return df

    out = df.copy()
    chosen = pd.Series(pd.NA, index=out.index, dtype="object")
    level = pd.Series(len(_CELL_LADDER) - 1, index=out.index, dtype="int64")

    for lvl, keys in enumerate(_CELL_LADDER):
        pending = chosen.isna()
        if not pending.any():
            break
        label = _cell_label(out, keys)
        per_day = out.groupby([label.rename("_k"), out[COL_DATE]], observed=True)[
            COL_SKU
        ].nunique()
        density = per_day.groupby(level="_k", observed=True).mean()
        ok = label.map(density).fillna(0.0) >= thr
        last = lvl == len(_CELL_LADDER) - 1
        take = pending & (ok | last)
        chosen[take] = label[take]
        level[take] = lvl

    out[COL_CELL] = chosen.astype("string")
    out["cell_level"] = level
    return out


def _cell_label(df: pd.DataFrame, keys: tuple[str, ...]) -> pd.Series:
    """生成形如 "rebar|18-22|上海" 的单元标签，被放宽的维度用 * 占位。"""
    wildcard = pd.Series("*", index=df.index, dtype="string")

    def col(name: str) -> pd.Series:
        return df[name].astype("string").fillna("NA")

    category = col(COL_CATEGORY)
    spec = col(COL_SPEC_GROUP) if COL_SPEC_GROUP in keys else wildcard
    if COL_CITY in keys:
        geo = col(COL_CITY)
    elif COL_REGION in keys:
        geo = col(COL_REGION)
    else:
        geo = wildcard
    return category + "|" + spec + "|" + geo


# --------------------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------------------
def compute_indices(weighted: pd.DataFrame,
                    config: cfg.SteelIndexConfig | None = None) -> IndexResult:
    """从已清洗、已加权的报价明细计算分品种指数与综合指数。"""
    config = config or cfg.SteelIndexConfig()
    ic = config.index

    weighted = adaptive_cells(weighted, config)
    panel = build_daily_panel(weighted, config)
    returns = compute_sku_returns(panel)
    cells = aggregate_cell_links(returns, config)

    if cells.empty:
        return IndexResult(composite=pd.DataFrame(), variety=pd.DataFrame(), cells=cells,
                           meta={"reason": "无可用匹配样本"})

    cells = impute_cell_links(cells, config)

    # --- 单元权重（品种内） ---
    cell_w = periodic_weights(cells, [COL_CELL], "weight_sum", ic.weight_refresh)
    cells = _attach_periodic_weights(cells, cell_w, [COL_CELL], ic.weight_refresh,
                                     "cell_weight")

    base_date = pd.Timestamp(ic.base_date) if ic.base_date else \
        pd.Timestamp(cells[COL_DATE].min())

    # --- 品种指数 ---
    cat_links = aggregate_links(
        cells[[COL_DATE, COL_CELL, COL_CATEGORY, COL_LINK, "n_matched", "link_imputed"]],
        cells[[COL_DATE, COL_CELL, "cell_weight"]],
        group_key=COL_CATEGORY,
    )
    cat_price = _weighted_level(cells, [COL_DATE, COL_CATEGORY], "cell_price", "cell_weight")
    cat_links = cat_links.merge(cat_price, on=[COL_DATE, COL_CATEGORY], how="left")

    variety = chain_index(cat_links, key=COL_CATEGORY, base_value=ic.base_value,
                          base_date=base_date)
    variety = add_change_columns(variety, key=COL_CATEGORY)
    variety = ewma_smooth(variety, ic.smooth_alpha, key=COL_CATEGORY)
    variety["category_name"] = variety[COL_CATEGORY].map(CATEGORY_NAMES).fillna(
        variety[COL_CATEGORY]
    )
    variety["low_confidence"] = (
        (variety["n_cells"] < ic.low_confidence_min_cells)
        | (variety["n_matched"] < config.weight.min_effective_sample)
    )

    # --- 综合指数（不分品类） ---
    cat_w = category_weights_table(cells, config)
    comp_src = _attach_periodic_weights(cat_links, cat_w, [COL_CATEGORY],
                                        ic.weight_refresh, "category_weight")
    comp_src["_w"] = comp_src["category_weight"].astype("float64").clip(lower=0.0)
    comp_src["_num"] = comp_src["_w"] * comp_src[COL_LINK].astype("float64")
    comp_src["_pnum"] = comp_src["_w"] * comp_src["price_level"].astype("float64")

    composite = (
        comp_src.groupby(COL_DATE, observed=True)
        .agg(_num=("_num", "sum"), _den=("_w", "sum"), _pnum=("_pnum", "sum"),
             n_categories=(COL_CATEGORY, "nunique"), n_cells=("n_cells", "sum"),
             n_matched=("n_matched", "sum"), n_imputed=("n_imputed", "sum"))
        .reset_index()
    )
    composite[COL_LINK] = np.where(composite["_den"] > 0,
                                   composite["_num"] / composite["_den"], 1.0)
    composite["price_level"] = np.where(composite["_den"] > 0,
                                        composite["_pnum"] / composite["_den"], np.nan)
    composite = composite.drop(columns=["_num", "_pnum"])

    composite = chain_index(composite, key=None, base_value=ic.base_value,
                            base_date=base_date)
    composite = add_change_columns(composite, key=None)
    composite = ewma_smooth(composite, ic.smooth_alpha, key=None)

    merchants = (
        returns.loc[returns["log_ret"].notna()]
        .groupby(COL_DATE, observed=True)[COL_MERCHANT].nunique()
        .rename("n_merchants")
    )
    composite = composite.join(merchants, on=COL_DATE)
    composite["n_merchants"] = composite["n_merchants"].fillna(0).astype("int64")
    composite["imputed_share"] = np.where(
        composite["n_cells"] > 0, composite["n_imputed"] / composite["n_cells"], np.nan
    )
    composite["low_confidence"] = (
        (composite["n_matched"] < 10)
        | (composite["n_merchants"] < 3)
        | (composite["imputed_share"] > 0.5)
    )
    composite = composite.drop(columns=["_den"])

    # --- 品种对综合指数的贡献度分解 ---
    contribution = comp_src[[COL_DATE, COL_CATEGORY, "category_weight", COL_LINK]].copy()
    den = contribution.groupby(COL_DATE, observed=True)["category_weight"].transform("sum")
    share = np.where(den > 0, contribution["category_weight"] / den, 0.0)
    contribution["weight_share"] = share
    contribution["contribution_pct"] = share * (contribution[COL_LINK] - 1.0)
    contribution["category_name"] = contribution[COL_CATEGORY].map(CATEGORY_NAMES).fillna(
        contribution[COL_CATEGORY]
    )

    breadth_all = market_breadth(returns, key=(COL_DATE,))
    breadth_cat = market_breadth(returns, key=(COL_DATE, COL_CATEGORY))
    composite = composite.merge(breadth_all, on=COL_DATE, how="left")
    variety = variety.merge(breadth_cat, on=[COL_DATE, COL_CATEGORY], how="left")

    if ic.bootstrap_rounds > 0:
        ci = bootstrap_link_ci(returns, ic.bootstrap_rounds, ic.bootstrap_seed,
                               key=(COL_DATE,))
        composite = composite.merge(ci, on=COL_DATE, how="left")

    meta = {
        "base_date": base_date,
        "base_value": ic.base_value,
        "date_start": pd.Timestamp(cells[COL_DATE].min()),
        "date_end": pd.Timestamp(cells[COL_DATE].max()),
        "n_cells_total": int(cells[COL_CELL].nunique()),
        "n_categories": int(cells[COL_CATEGORY].nunique()),
        "category_weight_mode": ic.category_weight_mode,
        "weight_refresh": ic.weight_refresh,
    }

    return IndexResult(
        composite=composite.sort_values(COL_DATE, ignore_index=True),
        variety=variety.sort_values([COL_CATEGORY, COL_DATE], ignore_index=True),
        cells=cells,
        weights={"cell": cell_w, "category": cat_w},
        breadth=breadth_all,
        contribution=contribution,
        meta=meta,
    )


def _weighted_level(df: pd.DataFrame, keys: list[str], value_col: str,
                    weight_col: str) -> pd.DataFrame:
    """加权价格水平（元/吨）。"""
    work = df.loc[df[value_col].notna()].copy()
    if work.empty:
        return pd.DataFrame(columns=keys + ["price_level"])
    work["_w"] = work[weight_col].astype("float64").clip(lower=0.0)
    work["_num"] = work["_w"] * work[value_col].astype("float64")
    out = (
        work.groupby(keys, observed=True)
        .agg(_num=("_num", "sum"), _den=("_w", "sum"))
        .reset_index()
    )
    out["price_level"] = np.where(out["_den"] > 0, out["_num"] / out["_den"], np.nan)
    return out.drop(columns=["_num", "_den"])
