"""指数数学核心：匹配样本链式环比（matched-sample chain link）。

为什么不能直接用"当日全市场加权均价 / 昨日全市场加权均价"作为指数？
因为商家每天在变、SKU 每天在变、清洗剔除的样本每天也在变。样本一换，
均价就跳，跳出来的幅度往市场涨跌上一记，指数就废了。

正确做法是逐个可比样本算价比，只用今天和昨天都有有效报价的同一 SKU（匹配样本），
再按权重聚合成环比 link，最后用 link 连乘成指数：

    单元环比   L_c(t) = exp( Σ_i w_i · ln(p_i,t / p_i,t-1) / Σ_i w_i )     （加权 Jevons）
    品种环比   L_v(t) = Σ_c W_c · L_c(t) / Σ_c W_c                        （库存加权 Laspeyres）
    综合环比   L(t)   = Σ_v W_v · L_v(t) / Σ_v W_v
    指数       I(t)   = I(t-1) · L(t)，    I(基期) = 100

这样任何"样本进出"都不会污染指数，权重也可以定期更新而不产生断点。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Sequence

import numpy as np
import pandas as pd

from . import config as cfg
from .schema import (
    COL_CATEGORY,
    COL_CELL,
    COL_DATE,
    COL_MERCHANT,
    COL_PRICE,
    COL_SKU,
)
from .utils import kish_ess, tukey_biweight_location, weighted_median, weighted_trimmed_mean
from .weights import COL_WEIGHT

COL_QUOTED = "is_quoted"
COL_STALE = "is_stale"
COL_LINK = "link"


# --------------------------------------------------------------------------------------
# 日频面板
# --------------------------------------------------------------------------------------
def build_daily_panel(df: pd.DataFrame, config: cfg.SteelIndexConfig | None = None,
                      calendar: Sequence[pd.Timestamp] | None = None) -> pd.DataFrame:
    """把清洗后的报价整理成 (日期 × SKU) 稠密面板。

    挂牌价不是每天都更新，所以在报价日历上做有限期沿用（默认最多 5 天）：
    沿用期内视为该 SKU 仍在市，超过则视为退出样本、不再参与匹配。
    沿用行会标记 is_quoted=False，供上层降权或诊断使用。
    """
    config = config or cfg.SteelIndexConfig()
    limit = int(config.cleaning.carry_forward_days)
    if df.empty:
        return df.assign(**{COL_QUOTED: pd.Series(dtype="bool")})

    grid = pd.DatetimeIndex(sorted(pd.unique(df[COL_DATE]))) if calendar is None \
        else pd.DatetimeIndex(sorted(pd.unique(pd.DatetimeIndex(calendar))))

    meta_cols = [COL_SKU, COL_CELL, COL_CATEGORY, COL_MERCHANT]
    meta = df[meta_cols].drop_duplicates(COL_SKU).set_index(COL_SKU)

    frames: List[pd.DataFrame] = []
    # 按品种分批 pivot，避免一次性构造超大稀疏矩阵。
    has_stale = COL_STALE in df.columns
    for category, part in df.groupby(COL_CATEGORY, observed=True, sort=False):
        price = part.pivot_table(index=COL_DATE, columns=COL_SKU, values=COL_PRICE,
                                 aggfunc="last")
        weight = part.pivot_table(index=COL_DATE, columns=COL_SKU, values=COL_WEIGHT,
                                  aggfunc="last")
        price = price.reindex(grid)
        weight = weight.reindex(grid)

        quoted = price.notna()
        price_f = price.ffill(limit=limit) if limit > 0 else price
        weight_f = weight.ffill(limit=limit) if limit > 0 else weight

        long = (
            price_f.stack(future_stack=True).rename(COL_PRICE).to_frame()
            .join(weight_f.stack(future_stack=True).rename(COL_WEIGHT))
            .join(quoted.stack(future_stack=True).rename(COL_QUOTED))
        )
        if has_stale:
            stale = part.assign(_s=part[COL_STALE].fillna(False).astype("float64")) \
                .pivot_table(index=COL_DATE, columns=COL_SKU, values="_s", aggfunc="last")
            stale = stale.reindex(grid)
            stale_f = stale.ffill(limit=limit) if limit > 0 else stale
            long = long.join(stale_f.stack(future_stack=True).rename(COL_STALE))
        long = long.loc[long[COL_PRICE].notna()]
        if long.empty:
            continue
        long.index = long.index.set_names([COL_DATE, COL_SKU])
        long = long.reset_index()
        long[COL_CATEGORY] = category
        frames.append(long)

    if not frames:
        return df.iloc[0:0].assign(**{COL_QUOTED: pd.Series(dtype="bool")})

    panel = pd.concat(frames, ignore_index=True)
    panel[COL_CELL] = panel[COL_SKU].map(meta[COL_CELL]).astype("string")
    panel[COL_MERCHANT] = panel[COL_SKU].map(meta[COL_MERCHANT]).astype("string")
    panel[COL_QUOTED] = panel[COL_QUOTED].fillna(False).astype(bool)
    if COL_STALE in panel.columns:
        panel[COL_STALE] = panel[COL_STALE].fillna(0.0).astype("float64").gt(0.5)
    else:
        panel[COL_STALE] = False
    panel[COL_WEIGHT] = panel[COL_WEIGHT].astype("float64").fillna(0.0).clip(lower=1e-9)
    carried_factor = float(config.cleaning.carried_weight_factor)
    if carried_factor != 1.0:
        panel[COL_WEIGHT] = panel[COL_WEIGHT] * np.where(
            panel[COL_QUOTED], 1.0, carried_factor
        )
    panel["date_rank"] = pd.Series(
        pd.Index(grid).get_indexer(pd.DatetimeIndex(panel[COL_DATE])), index=panel.index
    )
    return panel.sort_values([COL_SKU, COL_DATE], ignore_index=True)


# --------------------------------------------------------------------------------------
# 匹配样本环比
# --------------------------------------------------------------------------------------
def compute_sku_returns(panel: pd.DataFrame,
                        config: cfg.SteelIndexConfig | None = None) -> pd.DataFrame:
    """计算每个 SKU 两次真实报价之间的对数价比。

    关键点：环比只能用真实更新过的报价来算，沿用价必须排除。
    如果把"今天没改价"当成"今天价格没变"计入环比，那么每天都会有一大批
    零变动样本把 link 往 1 拉，指数会被系统性钝化——商家报价越不勤，指数越迟钝，
    甚至会出现"退出市场的商家还在压低指数"这种荒谬结果。

    被排除的沿用样本并没有被浪费：它们仍然进入单元价格水平与权重，
    其价格变动在下一次真实报价时通过区间价比一次性计入，累计涨跌不会漏。
    """
    config = config or cfg.SteelIndexConfig()
    max_gap = int(config.cleaning.carry_forward_days)
    if panel.empty:
        return panel.assign(log_ret=pd.Series(dtype="float64"),
                            pair_weight=pd.Series(dtype="float64"),
                            gap_days=pd.Series(dtype="float64"))

    out = panel.sort_values([COL_SKU, "date_rank"]).copy()
    out["log_ret"] = np.nan
    out["pair_weight"] = np.nan
    out["gap_days"] = np.nan

    quoted = out.loc[out[COL_QUOTED]]
    if quoted.empty:
        return out

    g = quoted.groupby(COL_SKU, observed=True, sort=False)
    prev_price = g[COL_PRICE].shift(1)
    prev_rank = g["date_rank"].shift(1)
    prev_weight = g[COL_WEIGHT].shift(1)
    gap = quoted["date_rank"] - prev_rank

    usable = (
        prev_price.gt(0)
        & quoted[COL_PRICE].gt(0)
        & gap.ge(1)
        & gap.le(max_gap)
    )
    with np.errstate(divide="ignore", invalid="ignore"):
        log_ret = np.log(quoted[COL_PRICE] / prev_price)

    out.loc[quoted.index, "log_ret"] = np.where(usable, log_ret, np.nan)
    out.loc[quoted.index, "pair_weight"] = np.where(
        usable, (quoted[COL_WEIGHT] + prev_weight) / 2.0, np.nan
    )
    out.loc[quoted.index, "gap_days"] = np.where(usable, gap, np.nan)
    return out


def compute_links_imputed(panel: pd.DataFrame,
                          config: cfg.SteelIndexConfig | None = None
                          ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """逐日推进的单元相对插补法（cell-relative imputation），指数编制的标准做法。

    需要解决的问题：商家不是每天都改价。对于当日没有更新的挂牌价，
    - 沿用昨天的价格（carry forward），等于宣称"今天没涨没跌"，会把指数钝化；
    - 用"上次报价到今天"的区间价比全额计入当日，又会把多天的涨跌算成一天的，
      隔天报价的样本会让涨幅直接翻倍。

    正确做法是：当日没有真实报价的资源，其价格按同单元当日的平均变动推算
    （即假设它跟随市场走），再让它的下一次真实报价与这个推算价格比较——
    比出来的就是它相对市场的超额变动。这样：
      · 每一期的环比只由当日真实报价决定，累计涨跌无偏；
      · 只使用当期信息，指数可以当日发布，不需要事后修订；
      · 长期不跟涨的挂牌价会如实体现为相对市场下跌，不会被"沿用"掩盖。

    返回 (单元环比明细, SKU 环比明细)。
    """
    config = config or cfg.SteelIndexConfig()
    ic = config.index
    max_gap = int(config.cleaning.carry_forward_days)
    geometric = ic.cell_link_agg == "geometric"
    cap = ic.max_link_move
    include_stale = ic.include_stale_in_link

    cell_cols = [COL_DATE, COL_CELL, COL_CATEGORY, COL_LINK, "n_matched", "ess",
                 "cell_price", "n_quotes", "n_quoted", "weight_sum", "link_capped"]
    ret_cols = [COL_DATE, COL_SKU, COL_CELL, COL_CATEGORY, COL_MERCHANT, COL_PRICE,
                COL_WEIGHT, COL_QUOTED, "log_ret", "pair_weight", "gap_days"]
    if panel.empty:
        return pd.DataFrame(columns=cell_cols), pd.DataFrame(columns=ret_cols)

    work = panel.sort_values(["date_rank", COL_SKU])
    # ref: 每个在市 SKU 的参考价（含插补）、最后一次真实报价的时点与权重
    ref = pd.DataFrame(columns=["ref_price", "ref_rank", "ref_weight"], dtype="float64")

    cell_frames = []
    ret_frames = []
    for rank, today in work.groupby("date_rank", observed=True, sort=True):
        today = today.set_index(COL_SKU)
        joined = today.join(ref, how="left")

        gap = float(rank) - joined["ref_rank"]
        usable = (
            joined[COL_QUOTED]
            & joined["ref_price"].gt(0)
            & joined[COL_PRICE].gt(0)
            & gap.ge(1)
            & gap.le(max_gap)
        )
        if not include_stale and COL_STALE in joined.columns:
            usable = usable & ~joined[COL_STALE].fillna(False)
        with np.errstate(divide="ignore", invalid="ignore"):
            log_ret = np.log(joined[COL_PRICE] / joined["ref_price"])
        joined["log_ret"] = np.where(usable, log_ret, np.nan)
        joined["pair_weight"] = np.where(
            usable, (joined[COL_WEIGHT] + joined["ref_weight"]) / 2.0, np.nan
        )
        joined["gap_days"] = np.where(usable, gap, np.nan)

        links = _cell_links_for_day(joined, geometric, cap)
        cell_frames.append(links)
        ret_frames.append(joined.reset_index())

        # 更新参考价：真实报价直接采用；未报价的按本单元平均变动推算。
        link_map = links.set_index(COL_CELL)[COL_LINK]
        cell_link = joined[COL_CELL].map(link_map).astype("float64").fillna(1.0)
        quoted = joined[COL_QUOTED].to_numpy()
        new_price = np.where(
            quoted,
            joined[COL_PRICE].to_numpy(dtype="float64"),
            joined["ref_price"].to_numpy(dtype="float64") * cell_link.to_numpy(),
        )
        # 首次出现且当日未报价的资源没有参考价可推算，直接采用面板价格
        new_price = np.where(np.isfinite(new_price), new_price,
                             joined[COL_PRICE].to_numpy(dtype="float64"))
        new_rank = np.where(quoted, float(rank), joined["ref_rank"].to_numpy())
        new_rank = np.where(np.isfinite(new_rank), new_rank, float(rank))
        new_weight = np.where(quoted, joined[COL_WEIGHT].to_numpy(dtype="float64"),
                              joined["ref_weight"].to_numpy(dtype="float64"))
        new_weight = np.where(np.isfinite(new_weight), new_weight,
                              joined[COL_WEIGHT].to_numpy(dtype="float64"))

        # 不在今天面板里的 SKU 视为已退出，直接从参考表移除
        ref = pd.DataFrame(
            {"ref_price": new_price, "ref_rank": new_rank, "ref_weight": new_weight},
            index=joined.index,
        )

    cells = pd.concat(cell_frames, ignore_index=True)
    cells[COL_DATE] = pd.to_datetime(cells[COL_DATE])
    returns = pd.concat(ret_frames, ignore_index=True)
    return (cells.sort_values([COL_DATE, COL_CELL], ignore_index=True),
            returns[[c for c in ret_cols if c in returns.columns]])


def _cell_links_for_day(day: pd.DataFrame, geometric: bool, cap: float) -> pd.DataFrame:
    """单日的单元级环比与价格水平。"""
    records = []
    for cell, part in day.groupby(COL_CELL, observed=True, sort=True):
        r = part["log_ret"].to_numpy(dtype="float64")
        pw = part["pair_weight"].to_numpy(dtype="float64")
        matched = np.isfinite(r) & np.isfinite(pw) & (pw > 0)
        n_matched = int(matched.sum())

        if n_matched > 0:
            rr, ww = r[matched], pw[matched]
            if geometric:
                link = float(np.exp(np.sum(ww * rr) / np.sum(ww)))
            else:
                link = float(np.sum(ww * np.exp(rr)) / np.sum(ww))
            ess = kish_ess(ww)
        else:
            link, ess = np.nan, 0.0

        capped = False
        if np.isfinite(link) and cap > 0:
            lo, hi = 1.0 - cap, 1.0 + cap
            if link < lo or link > hi:
                link = float(np.clip(link, lo, hi))
                capped = True

        records.append(
            {
                COL_DATE: part[COL_DATE].iloc[0],
                COL_CELL: cell,
                COL_CATEGORY: part[COL_CATEGORY].iloc[0],
                COL_LINK: link,
                "n_matched": n_matched,
                "ess": ess,
                "cell_price": _cell_price(part[COL_PRICE].to_numpy(dtype="float64"),
                                          part[COL_WEIGHT].to_numpy(dtype="float64")),
                "n_quotes": int(len(part)),
                "n_quoted": int(part[COL_QUOTED].sum()),
                "weight_sum": float(np.nansum(part[COL_WEIGHT].to_numpy(dtype="float64"))),
                "link_capped": capped,
            }
        )
    return pd.DataFrame.from_records(records)


def _group_positions(df: pd.DataFrame, keys: Sequence[str]) -> Dict[tuple, np.ndarray]:
    return df.groupby(list(keys), observed=True, sort=True).indices


def aggregate_cell_links(returns: pd.DataFrame,
                         config: cfg.SteelIndexConfig | None = None) -> pd.DataFrame:
    """把 SKU 级价比聚合成单元级环比 link，并给出单元价格水平与样本质量指标。"""
    config = config or cfg.SteelIndexConfig()
    ic = config.index
    cols = [COL_DATE, COL_CELL, COL_CATEGORY, COL_LINK, "n_matched", "ess",
            "cell_price", "n_quotes", "n_quoted", "weight_sum", "link_capped"]
    if returns.empty:
        return pd.DataFrame(columns=cols)

    df = returns.reset_index(drop=True)
    positions = _group_positions(df, [COL_DATE, COL_CELL])

    date_v = df[COL_DATE].to_numpy()
    cell_v = df[COL_CELL].to_numpy()
    cat_v = df[COL_CATEGORY].to_numpy()
    price_v = df[COL_PRICE].to_numpy(dtype="float64")
    w_v = df[COL_WEIGHT].to_numpy(dtype="float64")
    ret_v = df["log_ret"].to_numpy(dtype="float64")
    pw_v = df["pair_weight"].to_numpy(dtype="float64")
    quoted_v = df[COL_QUOTED].to_numpy()

    geometric = ic.cell_link_agg == "geometric"
    cap = ic.max_link_move
    records = []
    for _, pos in positions.items():
        p = price_v[pos]
        w = w_v[pos]
        r = ret_v[pos]
        pw = pw_v[pos]

        matched = np.isfinite(r) & np.isfinite(pw) & (pw > 0)
        n_matched = int(matched.sum())
        if n_matched > 0:
            rr, ww = r[matched], pw[matched]
            if geometric:
                link = float(np.exp(np.sum(ww * rr) / np.sum(ww)))
            else:
                link = float(np.sum(ww * np.exp(rr)) / np.sum(ww))
            ess = kish_ess(ww)
        else:
            link = np.nan
            ess = 0.0

        capped = False
        if np.isfinite(link) and cap > 0:
            lo, hi = 1.0 - cap, 1.0 + cap
            if link < lo or link > hi:
                link = float(np.clip(link, lo, hi))
                capped = True

        records.append(
            {
                COL_DATE: date_v[pos[0]],
                COL_CELL: cell_v[pos[0]],
                COL_CATEGORY: cat_v[pos[0]],
                COL_LINK: link,
                "n_matched": n_matched,
                "ess": ess,
                "cell_price": _cell_price(p, w),
                "n_quotes": int(len(pos)),
                "n_quoted": int(quoted_v[pos].sum()),
                "weight_sum": float(np.nansum(w)),
                "link_capped": capped,
            }
        )
    out = pd.DataFrame.from_records(records, columns=cols)
    out[COL_DATE] = pd.to_datetime(out[COL_DATE])
    return out.sort_values([COL_DATE, COL_CELL], ignore_index=True)


def _cell_price(prices: np.ndarray, weights: np.ndarray) -> float:
    """单元价格水平：Tukey 双权重加权位置估计，小样本退化为加权中位数。"""
    ok = np.isfinite(prices) & (prices > 0) & np.isfinite(weights) & (weights > 0)
    if not ok.any():
        return float("nan")
    p, w = prices[ok], weights[ok]
    if p.size < 4:
        return weighted_median(p, w)
    return tukey_biweight_location(p, w)


# --------------------------------------------------------------------------------------
# 层级聚合
# --------------------------------------------------------------------------------------
def impute_cell_links(cell_links: pd.DataFrame,
                      config: cfg.SteelIndexConfig | None = None) -> pd.DataFrame:
    """给缺失或样本不足的单元 link 做插补。

    单元当日没有匹配样本时不能直接丢掉该单元的权重——否则指数的品种结构会随
    数据到达情况漂移。这里用同品种其他单元的加权平均 link 代替，并标记 imputed。
    """
    config = config or cfg.SteelIndexConfig()
    min_ess = config.weight.min_effective_sample
    if cell_links.empty:
        return cell_links.assign(link_imputed=pd.Series(dtype="bool"))

    df = cell_links.copy()
    reliable = df[COL_LINK].notna() & (df["ess"] >= min_ess) & (df["n_matched"] > 0)

    ref = df.loc[reliable].copy()
    ref["_num"] = ref[COL_LINK] * ref["ess"]
    agg = ref.groupby([COL_DATE, COL_CATEGORY], observed=True).agg(
        _num=("_num", "sum"), _den=("ess", "sum")
    )
    cat_link = (agg["_num"] / agg["_den"]).rename("_cat_link")

    all_agg = ref.groupby(COL_DATE, observed=True).agg(_num=("_num", "sum"), _den=("ess", "sum"))
    day_link = (all_agg["_num"] / all_agg["_den"]).rename("_day_link")

    df = df.join(cat_link, on=[COL_DATE, COL_CATEGORY]).join(day_link, on=COL_DATE)
    fallback = df["_cat_link"].fillna(df["_day_link"]).fillna(1.0)

    df["link_imputed"] = ~reliable
    df[COL_LINK] = np.where(reliable, df[COL_LINK], fallback)
    return df.drop(columns=["_cat_link", "_day_link"])


def aggregate_links(cell_links: pd.DataFrame, cell_weights: pd.DataFrame,
                    group_key: str) -> pd.DataFrame:
    """按给定权重把单元 link 加权聚合到上一层（Laspeyres 形式的加权算术平均）。"""
    if cell_links.empty:
        return pd.DataFrame(columns=[COL_DATE, group_key, COL_LINK, "n_cells",
                                     "n_matched", "weight_sum"])

    df = cell_links.merge(cell_weights, on=[COL_DATE, COL_CELL], how="left",
                          suffixes=("", "_w"))
    w = df["cell_weight"].astype("float64")
    w = w.where(np.isfinite(w) & (w > 0), 0.0)
    # 权重全缺的情况下退化为等权，保证仍能产出指数。
    if w.sum() <= 0:
        w = pd.Series(np.ones(len(df)), index=df.index)
    df["_w"] = w
    df["_num"] = df["_w"] * df[COL_LINK]

    out = (
        df.groupby([COL_DATE, group_key], observed=True)
        .agg(
            _num=("_num", "sum"),
            _den=("_w", "sum"),
            n_cells=(COL_CELL, "nunique"),
            n_matched=("n_matched", "sum"),
            n_imputed=("link_imputed", "sum"),
            price_weight=("_w", "sum"),
        )
        .reset_index()
    )
    out[COL_LINK] = np.where(out["_den"] > 0, out["_num"] / out["_den"], 1.0)
    return out.drop(columns=["_num", "_den"])


def chain_index(links: pd.DataFrame, key: str | None = None,
                base_value: float = 100.0,
                base_date: pd.Timestamp | None = None) -> pd.DataFrame:
    """把环比 link 连乘成定基指数。

    基期指数为 base_value；基期当日的 link 不参与连乘（视为 1），
    因此指数的第一期恒等于基期值，符合定基指数的常规读法。
    """
    if links.empty:
        return links.assign(index_value=pd.Series(dtype="float64"))

    df = links.sort_values([key, COL_DATE] if key else [COL_DATE]).copy()

    def _chain(part: pd.DataFrame) -> pd.DataFrame:
        part = part.sort_values(COL_DATE).copy()
        link = part[COL_LINK].astype("float64").fillna(1.0).to_numpy()
        dates = pd.DatetimeIndex(part[COL_DATE])
        start = 0
        if base_date is not None:
            hit = np.searchsorted(dates.to_numpy(), np.datetime64(base_date), side="left")
            start = int(min(max(hit, 0), len(dates) - 1))
        eff = link.copy()
        eff[: start + 1] = 1.0
        values = base_value * np.cumprod(eff)
        if start > 0:
            # 基期之前的历史按逆向链式回推，保证整段序列可比。
            back = np.cumprod(1.0 / link[1 : start + 1][::-1])[::-1]
            values[:start] = base_value * back
        part["index_value"] = values
        return part

    if key is None:
        return _chain(df).reset_index(drop=True)
    parts = [_chain(part) for _, part in df.groupby(key, observed=True, sort=False)]
    return pd.concat(parts, ignore_index=True)


def add_change_columns(df: pd.DataFrame, key: str | None = None,
                       value_col: str = "index_value") -> pd.DataFrame:
    """补充环比、周环比、月环比、同比与 EWMA 平滑列。"""
    if df.empty:
        return df
    out = df.sort_values([key, COL_DATE] if key else [COL_DATE]).copy()
    g = out.groupby(key, observed=True, sort=False)[value_col] if key else out[value_col]

    def _pct(shift_n: int) -> np.ndarray:
        prev = g.shift(shift_n)
        with np.errstate(divide="ignore", invalid="ignore"):
            return (out[value_col] / prev - 1.0).to_numpy()

    out["chg_1d"] = _pct(1)
    out["chg_5d"] = _pct(5)
    out["chg_22d"] = _pct(22)
    out["chg_250d"] = _pct(250)
    return out


def ewma_smooth(df: pd.DataFrame, alpha: float, key: str | None = None,
                value_col: str = "index_value", out_col: str = "index_smooth"
                ) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.sort_values([key, COL_DATE] if key else [COL_DATE]).copy()
    if key:
        out[out_col] = (
            out.groupby(key, observed=True, sort=False)[value_col]
            .transform(lambda s: s.ewm(alpha=alpha, adjust=False).mean())
        )
    else:
        out[out_col] = out[value_col].ewm(alpha=alpha, adjust=False).mean()
    return out


def market_breadth(returns: pd.DataFrame, key: Sequence[str] = (COL_DATE,)
                   ) -> pd.DataFrame:
    """涨跌广度：上涨/下跌/持平的匹配样本家数与涨跌比。

    指数只告诉你市场往哪走，广度告诉你这个走势是普涨还是被少数样本拉起来的。
    """
    if returns.empty:
        return pd.DataFrame(columns=list(key) + ["n_up", "n_down", "n_flat",
                                                 "up_down_ratio", "diffusion"])
    df = returns.loc[returns["log_ret"].notna()].copy()
    if df.empty:
        return pd.DataFrame(columns=list(key) + ["n_up", "n_down", "n_flat",
                                                 "up_down_ratio", "diffusion"])
    eps = 1e-9
    df["_up"] = df["log_ret"] > eps
    df["_down"] = df["log_ret"] < -eps
    df["_flat"] = ~df["_up"] & ~df["_down"]
    out = (
        df.groupby(list(key), observed=True)
        .agg(n_up=("_up", "sum"), n_down=("_down", "sum"), n_flat=("_flat", "sum"))
        .reset_index()
    )
    out["up_down_ratio"] = out["n_up"] / out["n_down"].replace(0, np.nan)
    total = out[["n_up", "n_down", "n_flat"]].sum(axis=1)
    out["diffusion"] = np.where(total > 0, (out["n_up"] - out["n_down"]) / total, np.nan)
    return out


def bootstrap_link_ci(returns: pd.DataFrame, rounds: int, seed: int,
                      key: Sequence[str] = (COL_DATE,), alpha: float = 0.05
                      ) -> pd.DataFrame:
    """对匹配样本按商家整簇重抽样，给出每期 link 的置信区间。

    按商家（而非按报价）重抽样，是因为同一商家的多条报价高度相关，
    按条重抽样会低估不确定性。
    """
    cols = list(key) + ["link_lo", "link_hi"]
    if rounds <= 0 or returns.empty:
        return pd.DataFrame(columns=cols)

    df = returns.loc[returns["log_ret"].notna()].copy()
    if df.empty:
        return pd.DataFrame(columns=cols)

    rng = np.random.default_rng(seed)
    out = []
    for gkey, part in df.groupby(list(key), observed=True):
        merchants = part[COL_MERCHANT].to_numpy()
        uniq = pd.unique(merchants)
        if len(uniq) < 3:
            continue
        by_merchant = {m: part.loc[merchants == m] for m in uniq}
        draws = np.empty(rounds, dtype="float64")
        for b in range(rounds):
            pick = rng.choice(uniq, size=len(uniq), replace=True)
            sample = pd.concat([by_merchant[m] for m in pick])
            w = sample["pair_weight"].to_numpy(dtype="float64")
            r = sample["log_ret"].to_numpy(dtype="float64")
            draws[b] = np.exp(np.sum(w * r) / np.sum(w)) if w.sum() > 0 else np.nan
        lo, hi = np.nanquantile(draws, [alpha / 2, 1 - alpha / 2])
        row = dict(zip(key, gkey if isinstance(gkey, tuple) else (gkey,)))
        row.update({"link_lo": float(lo), "link_hi": float(hi)})
        out.append(row)
    return pd.DataFrame(out, columns=cols)
