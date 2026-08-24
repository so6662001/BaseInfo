"""商家可信度评分与同源账号（克隆小号）识别。

清洗层解决的是"单条数据是否合法"，本模块解决的是"这个商家整体是否可信"。
现实中的捣乱通常不是一条离群价，而是某个商家长期系统性偏离市场、乱跳价、
挂着不动价充数，或注册多个小号同步报同一个价格来放大自己对指数的影响。

评分严格使用截至评分时点之前的历史数据（point-in-time），
避免用未来信息给过去的指数打分，保证指数可复现、可回测。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Tuple

import numpy as np
import pandas as pd

from . import config as cfg
from .schema import COL_CELL, COL_DATE, COL_MERCHANT, COL_PRICE, COL_STOCK

NEUTRAL_SCORE = 0.70
"""新商家或样本不足时的中性分：不奖励也不拉黑。"""

SHRINK_STRENGTH = 8.0
"""贝叶斯收缩强度（等效样本天数），避免小样本商家分数剧烈摆动。
取两周左右的量级：够长以压住偶发噪声，又不至于让证据充分的捣乱商家靠"样本少"蒙混过关。"""

_BEHAVIOR_RULES = ("R020", "R021", "R022", "R030", "R031", "R040", "R041")
"""价格本身合法、但被行为类规则剔除的记录。

衡量商家行为（偏离度、波动率）必须把这些记录算进来：乱跳价的商家，
极端报价恰恰都被清洗剔掉了，只看存活数据的话它反而显得规规矩矩。"""


@dataclass
class CredibilityPanel:
    """商家可信度面板。

    scores : 每个评分生效日 × 商家的可信度与明细指标
    clones : 同源账号分组（clone_group -> 商家列表）
    """

    scores: pd.DataFrame
    clones: pd.DataFrame

    def as_of(self, date) -> pd.DataFrame:
        """取某个日期生效的评分快照。"""
        date = pd.Timestamp(date)
        if self.scores.empty:
            return self.scores
        eff = self.scores.loc[self.scores["effective_from"] <= date]
        if eff.empty:
            return self.scores.iloc[0:0]
        last = eff["effective_from"].max()
        return eff.loc[eff["effective_from"] == last]

    def lookup(self, dates: pd.Series, merchants: pd.Series) -> pd.DataFrame:
        """按 (日期, 商家) 向量化查询生效评分，未覆盖到的商家给中性分。"""
        # 统一成 object 字符串：上游列可能是 string[python] 或 object，
        # merge_asof 对 by 键的 dtype 要求严格一致。
        left = pd.DataFrame({COL_DATE: pd.to_datetime(dates).to_numpy(),
                             COL_MERCHANT: np.asarray(merchants, dtype=object).astype(str)})
        left["_order"] = np.arange(len(left))
        if self.scores.empty:
            left["credibility"] = NEUTRAL_SCORE
            left["blacklisted"] = False
            left["clone_size"] = 1
            return left.drop(columns=["_order"])

        right = self.scores[["effective_from", COL_MERCHANT, "credibility",
                             "blacklisted", "clone_size"]].copy()
        right[COL_MERCHANT] = np.asarray(right[COL_MERCHANT], dtype=object).astype(str)
        right = right.sort_values("effective_from")
        left = left.sort_values(COL_DATE)
        merged = pd.merge_asof(
            left,
            right,
            left_on=COL_DATE,
            right_on="effective_from",
            by=COL_MERCHANT,
            direction="backward",
        )
        merged["credibility"] = merged["credibility"].fillna(NEUTRAL_SCORE)
        merged["blacklisted"] = merged["blacklisted"].fillna(False).astype(bool)
        merged["clone_size"] = merged["clone_size"].fillna(1).astype("int64")
        return merged.sort_values("_order").drop(columns=["_order", "effective_from"])


# --------------------------------------------------------------------------------------
# 同源账号（克隆小号）识别
# --------------------------------------------------------------------------------------
class _DisjointSet:
    def __init__(self) -> None:
        self._parent: Dict[str, str] = {}

    def find(self, x: str) -> str:
        self._parent.setdefault(x, x)
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]
            x = self._parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[rb] = ra

    def groups(self) -> Dict[str, List[str]]:
        out: Dict[str, List[str]] = {}
        for node in list(self._parent):
            out.setdefault(self.find(node), []).append(node)
        return out


def detect_clone_groups(clean: pd.DataFrame, c: cfg.CleaningConfig) -> pd.DataFrame:
    """识别报价高度雷同的商家群，判定为同一控制人的多个账号。

    做法：先按 (价格单元, 日期, 价格) 聚合，只有报价完全相同的商家才成为候选对
    （真实独立商家极少长期分毫不差），再用共同活跃天数做归一化得到雷同率。
    这样避免了 O(商家数²) 的全对比较。
    """
    empty = pd.DataFrame(columns=[COL_MERCHANT, "clone_group", "clone_size"])
    if not c.clone_detection or clean.empty:
        return empty

    df = clean[[COL_CELL, COL_DATE, COL_MERCHANT, COL_PRICE]].copy()
    df["_p"] = df[COL_PRICE].round(2)

    active_days = df.groupby(COL_MERCHANT, observed=True)[COL_DATE].nunique()

    pair_hits: Dict[Tuple[str, str], int] = {}
    grouped = df.groupby([COL_CELL, COL_DATE, "_p"], observed=True)[COL_MERCHANT]
    for _, merchants in grouped:
        uniq = sorted(set(merchants.tolist()))
        if len(uniq) < 2 or len(uniq) > 12:
            # 单元内所有人同价属于正常的价格趋同，不作为同源证据。
            continue
        for i in range(len(uniq)):
            for j in range(i + 1, len(uniq)):
                key = (uniq[i], uniq[j])
                pair_hits[key] = pair_hits.get(key, 0) + 1

    ds = _DisjointSet()
    linked = False
    for (a, b), hits in pair_hits.items():
        overlap = min(int(active_days.get(a, 0)), int(active_days.get(b, 0)))
        if overlap < c.clone_min_overlap or hits < c.clone_min_overlap:
            continue
        if hits / overlap >= c.clone_identical_ratio:
            ds.union(a, b)
            linked = True

    if not linked:
        return empty

    rows = []
    for root, members in ds.groups().items():
        if len(members) < 2:
            continue
        for m in members:
            rows.append({COL_MERCHANT: m, "clone_group": root, "clone_size": len(members)})
    return pd.DataFrame(rows, columns=[COL_MERCHANT, "clone_group", "clone_size"])


# --------------------------------------------------------------------------------------
# 单期评分
# --------------------------------------------------------------------------------------
_REJECT_RULES_FOR_SCORE = ("R012", "R022", "R030", "R031", "R040", "R041")


def score_merchants(clean: pd.DataFrame, rejected: pd.DataFrame,
                    config: cfg.SteelIndexConfig | None = None) -> pd.DataFrame:
    """基于给定历史窗口的数据，为每个商家打出 0~1 的可信度分。

    分数是各维度惩罚因子的乘积，再按活跃天数向中性分收缩。
    """
    config = config or cfg.SteelIndexConfig()
    c = config.cleaning

    cols = ["n_quotes_hist", "n_days", "n_days_total", "outlier_rate", "price_bias",
            "bias", "dispersion", "stale_ratio", "vol_ratio", "spam_days",
            "stock_missing_ratio", "credibility", "blacklisted"]
    has_rejected = rejected is not None and not rejected.empty and "reject_rule" in rejected
    if clean.empty and not has_rejected:
        return pd.DataFrame(columns=[COL_MERCHANT] + cols)

    # 评分对象必须覆盖"数据被全部剔除"的商家：恶意压价者的报价几乎条条离群，
    # 如果只对存活数据分组，这类商家反而会因为没有记录而逃过评分。
    keep_cols = [COL_MERCHANT, COL_DATE]
    pieces = [clean[keep_cols]] if not clean.empty else []
    if has_rejected:
        pieces.append(rejected[keep_cols])
    universe = pd.concat(pieces, ignore_index=True)
    all_days = universe.groupby(COL_MERCHANT, observed=True)[COL_DATE].nunique()
    index = all_days.index

    behavior = _behavior_frame(clean, rejected)
    if not clean.empty:
        g = clean.groupby(COL_MERCHANT, observed=True)
        n_quotes = g[COL_PRICE].size()
        n_days = g[COL_DATE].nunique()
        stale = g["is_stale"].mean() if "is_stale" in clean else None
        stock_missing = g[COL_STOCK].apply(lambda s: float(s.isna().mean()))
    else:
        n_quotes = n_days = stale = stock_missing = None
    vol = _volatility_ratio(behavior, clean) if behavior is not None else None

    # 系统性偏离要用全量报价（含被剔除的）来看，否则被剔掉的偏离恰好看不见了。
    z_cols = [COL_MERCHANT, "robust_z", "cell_center_price", COL_PRICE]
    z_frames = [d[[c for c in z_cols if c in d]] for d in (clean, rejected)
                if d is not None and not d.empty and "robust_z" in d]
    bias = dispersion = price_bias = None
    if z_frames:
        zs = pd.concat(z_frames, ignore_index=True)
        zs["robust_z"] = pd.to_numeric(zs["robust_z"], errors="coerce")
        zg = zs.dropna(subset=["robust_z"]).groupby(COL_MERCHANT, observed=True)["robust_z"]
        bias = zg.median()
        dispersion = zg.apply(lambda s: float(s.abs().median()))

        # 相对中枢的价格偏离百分比：比 Z 分数更有业务含义，
        # 在单元内价格高度一致（离散度极小）时也不会虚高。
        if "cell_center_price" in zs and COL_PRICE in zs:
            center = pd.to_numeric(zs["cell_center_price"], errors="coerce")
            price = pd.to_numeric(zs[COL_PRICE], errors="coerce")
            rel = (price / center - 1.0).where(center > 0)
            price_bias = (
                zs.assign(_rel=rel).dropna(subset=["_rel"])
                .groupby(COL_MERCHANT, observed=True)["_rel"].median()
            )

    def _col(s, default=0.0) -> pd.Series:
        if s is None:
            return pd.Series(np.full(len(index), default), index=index, dtype="float64")
        return s.reindex(index).astype("float64").fillna(default)

    stat = pd.DataFrame(
        {
            "n_quotes_hist": _col(n_quotes),
            "n_days": _col(n_days),
            "n_days_total": all_days.astype("float64"),
            "bias": _col(bias),
            "price_bias": _col(price_bias),
            "dispersion": _col(dispersion),
            "stale_ratio": _col(stale),
            "stock_missing_ratio": _col(stock_missing),
            "vol_ratio": _col(vol, 1.0),
        }
    )

    if has_rejected:
        rej = rejected.loc[rejected["reject_rule"].isin(_REJECT_RULES_FOR_SCORE)]
        rej_cnt = rej.groupby(COL_MERCHANT, observed=True)[COL_DATE].size()
        spam_days = (
            rejected.loc[rejected["reject_rule"].eq("R022")]
            .groupby(COL_MERCHANT, observed=True)[COL_DATE].nunique()
        )
    else:
        rej_cnt = spam_days = None

    stat["rejected_hist"] = _col(rej_cnt)
    stat["spam_days"] = _col(spam_days)
    denom = stat["n_quotes_hist"] + stat["rejected_hist"]
    stat["outlier_rate"] = np.where(denom > 0, stat["rejected_hist"] / denom, 0.0)

    # --- 惩罚因子 ---
    # 主指标（恶意行为的直接证据）罚得狠，辅助指标（可能只是经营策略差异）罚得轻。
    p_price_bias = _penalty(stat["price_bias"].abs(), free=0.05,
                            limit=c.max_price_bias, floor=0.05)
    p_vol = _penalty(stat["vol_ratio"], free=2.0, limit=c.max_volatility_ratio, floor=0.08)
    # 灌水是明确的机器行为，只要出现过就重罚。
    p_spam = np.where(stat["spam_days"] > 0, 0.15, 1.0)

    p_outlier = _penalty(stat["outlier_rate"], free=0.15, limit=c.max_outlier_rate, floor=0.35)
    p_bias = _penalty(stat["bias"].abs(), free=3.0, limit=c.max_bias, floor=0.4)
    p_disp = _penalty(stat["dispersion"], free=3.0, limit=8.0, floor=0.5)
    # 僵尸报价只降权不拉黑，所以这里的惩罚很轻。
    p_stale = 1.0 - 0.2 * stat["stale_ratio"].clip(0.0, 1.0)
    p_stock = 1.0 - 0.1 * stat["stock_missing_ratio"].clip(0.0, 1.0)

    raw = p_price_bias * p_vol * p_spam * p_outlier * p_bias * p_disp * p_stale * p_stock
    raw = pd.Series(np.clip(raw, 0.0, 1.0), index=stat.index)

    # 小样本向中性分收缩：报价天数越少，越不该给出极端评价。
    # 这里用"含被剔除记录"的活跃天数，否则整月都在乱报的商家会被当成新商家放过。
    n = stat["n_days_total"].astype("float64")
    shrunk = (n * raw + SHRINK_STRENGTH * NEUTRAL_SCORE) / (n + SHRINK_STRENGTH)
    too_short = n < c.credibility_min_days
    stat["credibility"] = np.where(too_short, NEUTRAL_SCORE, shrunk)
    stat["blacklisted"] = stat["credibility"] < c.blacklist_threshold

    stat = stat.reset_index().rename(columns={"index": COL_MERCHANT})
    return stat


def _behavior_frame(clean: pd.DataFrame, rejected: pd.DataFrame) -> pd.DataFrame | None:
    """合并存活数据与"价格合法但行为异常"的被剔除数据，用于衡量商家行为。"""
    need = [COL_MERCHANT, COL_CELL, COL_DATE, COL_PRICE, "sku_id"]
    parts = []
    if clean is not None and not clean.empty and set(need) <= set(clean.columns):
        parts.append(clean[need])
    if (rejected is not None and not rejected.empty
            and set(need) <= set(rejected.columns) and "reject_rule" in rejected):
        keep = rejected.loc[rejected["reject_rule"].isin(_BEHAVIOR_RULES), need]
        if not keep.empty:
            parts.append(keep)
    if not parts:
        return None
    merged = pd.concat(parts, ignore_index=True)
    # 同一 SKU 同一天可能同时出现在存活数据和被剔除数据里（例如重复挂牌、离群报价）。
    # 不折叠成一条的话，按时间排序后的相邻差分会变成"同一天内两个报价的差"，
    # 算出来的就不是日间波动了。
    return (
        merged.groupby(["sku_id", COL_DATE], observed=True)
        .agg(
            **{
                COL_MERCHANT: (COL_MERCHANT, "first"),
                COL_CELL: (COL_CELL, "first"),
                COL_PRICE: (COL_PRICE, "median"),
            }
        )
        .reset_index()
    )


def _penalty(x: pd.Series, free: float, limit: float, floor: float) -> pd.Series:
    """线性惩罚：<= free 不罚，>= limit 罚到 floor，中间线性过渡。"""
    x = pd.Series(x).astype("float64").fillna(0.0)
    if limit <= free:
        return pd.Series(np.ones(len(x)), index=x.index)
    ratio = ((x - free) / (limit - free)).clip(lower=0.0, upper=1.0)
    return 1.0 - ratio * (1.0 - floor)


def _abs_returns(df: pd.DataFrame) -> pd.DataFrame:
    """按 SKU 计算相邻报价之间的绝对变动幅度。"""
    out = df[[COL_MERCHANT, COL_CELL, COL_DATE, COL_PRICE, "sku_id"]].sort_values(
        ["sku_id", COL_DATE]
    ).copy()
    g = out.groupby("sku_id", observed=True)[COL_PRICE]
    with np.errstate(divide="ignore", invalid="ignore"):
        out["_ret"] = (out[COL_PRICE] / g.shift(1) - 1.0).abs()
    return out


def _volatility_ratio(behavior: pd.DataFrame, clean: pd.DataFrame) -> pd.Series:
    """商家自身报价波动 / 所在单元的市场波动。远大于 1 说明在乱跳价。

    分子用全量行为数据（含被剔除的离群报价），否则乱跳价商家的极端报价恰好都被
    清洗掉了，它反而显得很稳。

    分母必须用**清洗后**的数据衡量市场波动：如果分母也用全量数据，
    那些离群报价会把"市场波动"一起撑大，比值又被拉回 1 附近，指标就失灵了。
    """
    if behavior is None or behavior.empty or clean is None or clean.empty:
        return pd.Series(dtype="float64")

    own_df = _abs_returns(behavior)
    market = (
        _abs_returns(clean)
        .groupby([COL_CELL, COL_DATE], observed=True)["_ret"].median().rename("_mkt")
    )
    own_df = own_df.join(market, on=[COL_CELL, COL_DATE])

    own = own_df.groupby(COL_MERCHANT, observed=True)["_ret"].median()
    mkt = own_df.groupby(COL_MERCHANT, observed=True)["_mkt"].median()
    floor = 0.002  # 市场几乎不动时，避免比值爆炸
    return (own / mkt.fillna(floor).clip(lower=floor)).replace([np.inf, -np.inf], np.nan)


# --------------------------------------------------------------------------------------
# 滚动面板
# --------------------------------------------------------------------------------------
def build_credibility_panel(clean: pd.DataFrame, rejected: pd.DataFrame,
                            config: cfg.SteelIndexConfig | None = None,
                            freq: str = "MS") -> CredibilityPanel:
    """构建 point-in-time 可信度面板。

    在每个刷新时点 d，只用 [d - window, d) 的历史数据评分，评分从 d 起生效。
    首个刷新时点之前的样本没有历史可依，统一按中性分处理。
    """
    config = config or cfg.SteelIndexConfig()
    c = config.cleaning

    clones = detect_clone_groups(clean, c)

    if clean.empty:
        return CredibilityPanel(
            scores=pd.DataFrame(columns=["effective_from", COL_MERCHANT, "credibility",
                                         "blacklisted", "clone_size"]),
            clones=clones,
        )

    start = pd.Timestamp(clean[COL_DATE].min())
    end = pd.Timestamp(clean[COL_DATE].max())
    window = pd.Timedelta(days=c.credibility_window)

    refresh_dates: Iterable[pd.Timestamp] = pd.date_range(
        start=start.normalize(), end=end.normalize() + pd.Timedelta(days=1), freq=freq
    )
    refresh_dates = [d for d in refresh_dates if d > start]
    if not refresh_dates:
        refresh_dates = [end + pd.Timedelta(days=1)]

    clone_size = (
        clones.set_index(COL_MERCHANT)["clone_size"] if not clones.empty
        else pd.Series(dtype="int64")
    )

    frames = []
    for d in refresh_dates:
        lo = d - window
        hist_clean = clean.loc[(clean[COL_DATE] >= lo) & (clean[COL_DATE] < d)]
        if hist_clean.empty:
            continue
        hist_rej = (
            rejected.loc[(rejected[COL_DATE] >= lo) & (rejected[COL_DATE] < d)]
            if rejected is not None and not rejected.empty else rejected
        )
        s = score_merchants(hist_clean, hist_rej, config)
        if s.empty:
            continue
        s.insert(0, "effective_from", d)
        frames.append(s)

    if not frames:
        scores = pd.DataFrame(columns=["effective_from", COL_MERCHANT, "credibility",
                                       "blacklisted", "clone_size"])
    else:
        scores = pd.concat(frames, ignore_index=True)
        scores["clone_size"] = (
            scores[COL_MERCHANT].map(clone_size).fillna(1).astype("int64")
        )
        # 同源账号：可信度打折，并在权重层按组规模摊薄，双重抑制刷票。
        multi = scores["clone_size"] > 1
        scores.loc[multi, "credibility"] = scores.loc[multi, "credibility"] * 0.7
        scores["blacklisted"] = scores["blacklisted"] | (
            scores["credibility"] < config.cleaning.blacklist_threshold
        )

    return CredibilityPanel(scores=scores, clones=clones)
