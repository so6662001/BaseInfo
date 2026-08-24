"""样本权重体系。

指数要反映的是"市场资源的价格"，而不是"报价条数的价格"，所以权重以库存量为主。
但纯库存加权极易被刷量操纵——只要挂一个天量库存就能主导指数，因此叠加三重约束：

1. 库存变换（默认 sqrt）压缩超大库存商家的边际影响；
2. 可信度加权，把长期乱报价的商家权重压低，黑名单直接出局；
3. 单商家权重封顶（默认 10%），超出份额按比例回流给其他商家。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import config as cfg
from .credibility import NEUTRAL_SCORE, CredibilityPanel
from .schema import COL_CATEGORY, COL_CELL, COL_DATE, COL_MERCHANT, COL_STOCK
from .utils import cap_weights

COL_WEIGHT = "weight"
COL_CRED = "credibility"


def _transform_stock(stock: pd.Series, mode: str) -> pd.Series:
    s = stock.astype("float64").clip(lower=0.0)
    if mode == "raw":
        return s
    if mode == "sqrt":
        return np.sqrt(s)
    if mode == "log":
        return np.log1p(s)
    if mode == "equal":
        return pd.Series(np.ones(len(s)), index=s.index)
    raise ValueError(f"未知的库存权重变换: {mode}")


def _fill_missing_stock(df: pd.DataFrame, mode: str) -> pd.Series:
    """库存缺失填充。默认取同单元中位数的一半：保留样本，但不给未申报库存的商家溢价。"""
    stock = df[COL_STOCK].astype("float64")
    if mode == "one":
        return stock.fillna(1.0)

    cell_med = df.groupby([COL_DATE, COL_CELL], observed=True)[COL_STOCK].transform("median")
    cat_med = df.groupby([COL_DATE, COL_CATEGORY], observed=True)[COL_STOCK].transform("median")
    global_med = float(stock.median()) if stock.notna().any() else 1.0
    fill = cell_med.fillna(cat_med).fillna(global_med)
    if mode == "cell_median_half":
        fill = fill * 0.5
    elif mode != "cell_median":
        raise ValueError(f"未知的库存填充策略: {mode}")
    return stock.fillna(fill).fillna(1.0)


def assign_sample_weights(clean: pd.DataFrame,
                          panel: CredibilityPanel | None = None,
                          config: cfg.SteelIndexConfig | None = None) -> pd.DataFrame:
    """给每条有效报价计算权重，并剔除黑名单商家。

    返回的 DataFrame 已经过单元内封顶处理，可直接用于 link 计算。
    """
    config = config or cfg.SteelIndexConfig()
    wc = config.weight
    out = clean.copy()

    if panel is not None:
        info = panel.lookup(out[COL_DATE], out[COL_MERCHANT])
        out[COL_CRED] = info["credibility"].to_numpy()
        out["blacklisted"] = info["blacklisted"].to_numpy()
        out["clone_size"] = info["clone_size"].to_numpy()
    else:
        out[COL_CRED] = NEUTRAL_SCORE
        out["blacklisted"] = False
        out["clone_size"] = 1

    out = out.loc[~out["blacklisted"]].copy()
    if out.empty:
        out[COL_WEIGHT] = pd.Series(dtype="float64")
        return out

    stock = _fill_missing_stock(out, wc.missing_stock_fill)
    base = _transform_stock(stock, wc.stock_transform)
    base = base.where(base > 0, base[base > 0].median() if (base > 0).any() else 1.0)

    # 第一步：只对"规模"权重封顶。封顶针对的是刷库存量带来的影响力，
    # 必须在质量折减之前完成——否则封顶回流会把质量差商家的权重重新抬回来，
    # 信誉与僵尸降权就白做了。
    out[COL_WEIGHT] = base.astype("float64").clip(lower=1e-9)
    out = _cap_by_merchant(out, wc.max_merchant_share)

    # 第二步：叠加质量折减。这一步不再封顶：如果一个单元里只剩一家可信商家，
    # 它就该主导这个单元的价格，而不是被迫和不可信报价平分话语权。
    quality = pd.Series(np.ones(len(out)), index=out.index, dtype="float64")
    if wc.use_credibility:
        quality *= out[COL_CRED].astype("float64").clip(lower=0.0, upper=1.0)
    if "is_stale" in out:
        quality *= np.where(out["is_stale"].fillna(False),
                            config.cleaning.stale_weight_factor, 1.0)
    # 同源账号按组规模摊薄，等效于把一组小号合并成一个样本。
    quality /= out["clone_size"].astype("float64").clip(lower=1.0)

    out[COL_WEIGHT] = (out[COL_WEIGHT] * quality).clip(lower=1e-9)
    return out


def _cap_by_merchant(df: pd.DataFrame, max_share: float) -> pd.DataFrame:
    """在每个 (日期, 价格单元) 内把单商家权重份额封顶。

    先把同一商家在该单元的多条报价权重合并成商家级份额做封顶，
    再按原比例分摊回各条报价，避免"一家多挂几条规格"绕过封顶。
    """
    if df.empty or max_share <= 0 or max_share >= 1:
        return df

    key = [COL_DATE, COL_CELL]
    merchant_w = df.groupby(key + [COL_MERCHANT], observed=True)[COL_WEIGHT].sum()

    scaled = merchant_w.groupby(level=[0, 1], observed=True).transform(
        lambda s: pd.Series(cap_weights(s.to_numpy(), max_share), index=s.index)
    )
    factor = (scaled / merchant_w).replace([np.inf, -np.inf], np.nan).fillna(1.0)
    factor.name = "_cap_factor"

    out = df.join(factor, on=key + [COL_MERCHANT])
    out[COL_WEIGHT] = out[COL_WEIGHT] * out["_cap_factor"].fillna(1.0)
    return out.drop(columns=["_cap_factor"])


def cell_weight_table(df: pd.DataFrame, config: cfg.SteelIndexConfig | None = None
                      ) -> pd.DataFrame:
    """按 (日期, 单元) 汇总出单元的库存规模，用于品种内的单元间加权。"""
    config = config or cfg.SteelIndexConfig()
    if df.empty:
        return pd.DataFrame(columns=[COL_DATE, COL_CELL, COL_CATEGORY, "cell_stock",
                                     "cell_weight_raw"])
    stock = _fill_missing_stock(df, config.weight.missing_stock_fill)
    tmp = df.assign(_stock=stock)
    agg = (
        tmp.groupby([COL_DATE, COL_CELL, COL_CATEGORY], observed=True)
        .agg(cell_stock=("_stock", "sum"), n_quotes=(COL_WEIGHT, "size"),
             weight_sum=(COL_WEIGHT, "sum"))
        .reset_index()
    )
    agg["cell_weight_raw"] = _transform_stock(agg["cell_stock"], config.weight.stock_transform)
    return agg
