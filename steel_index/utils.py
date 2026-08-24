"""稳健统计工具。

指数编制中的均值/离散度必须对少量恶意极值免疫，因此这里统一使用
中位数、MAD、Tukey biweight 与加权截尾均值，而不使用普通算术均值与标准差。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

MAD_TO_SIGMA = 1.4826
"""正态分布下 sigma ≈ 1.4826 × MAD。"""


def _as_arrays(x, w=None):
    x = np.asarray(x, dtype="float64")
    if w is None:
        w = np.ones_like(x)
    else:
        w = np.asarray(w, dtype="float64")
    ok = np.isfinite(x) & np.isfinite(w) & (w > 0)
    return x[ok], w[ok]


def weighted_median(x, w=None) -> float:
    return weighted_quantile(x, 0.5, w)


def weighted_quantile(x, q: float, w=None) -> float:
    """加权分位数（线性插值版），空样本返回 NaN。"""
    x, w = _as_arrays(x, w)
    if x.size == 0:
        return float("nan")
    order = np.argsort(x)
    x, w = x[order], w[order]
    cum = np.cumsum(w) - 0.5 * w
    cum /= w.sum()
    return float(np.interp(q, cum, x))


def mad(x, center: float | None = None) -> float:
    """中位数绝对偏差。"""
    x = np.asarray(x, dtype="float64")
    x = x[np.isfinite(x)]
    if x.size == 0:
        return float("nan")
    if center is None:
        center = float(np.median(x))
    return float(np.median(np.abs(x - center)))


def robust_scale(x, center: float | None = None, fallback_rel: float = 0.0) -> float:
    """稳健标准差估计。MAD 为 0 时退化为按中枢的相对带宽（避免除零把整单元清空）。"""
    m = mad(x, center)
    if not np.isfinite(m):
        return float("nan")
    scale = m * MAD_TO_SIGMA
    if scale <= 0 and fallback_rel > 0:
        c = center if center is not None else float(np.nanmedian(np.asarray(x, "float64")))
        scale = abs(c) * fallback_rel
    return float(scale)


def robust_z(x, center: float | None = None, fallback_rel: float = 0.0) -> np.ndarray:
    """稳健 Z 分数：(x - median) / (1.4826 * MAD)。"""
    x = np.asarray(x, dtype="float64")
    c = center if center is not None else float(np.nanmedian(x))
    s = robust_scale(x, c, fallback_rel)
    if not np.isfinite(s) or s <= 0:
        return np.zeros_like(x)
    return (x - c) / s


def tukey_biweight_location(x, w=None, c: float = 6.0, iters: int = 12,
                            tol: float = 1e-8) -> float:
    """Tukey 双权重位置估计：兼顾中位数的抗污染性与均值的有效性。

    这是单元价格水平（元/吨）的默认估计量：距离中枢超过 c 倍稳健尺度的报价权重
    被压到 0，温和偏离的报价按 (1-u^2)^2 平滑降权。
    """
    x, w = _as_arrays(x, w)
    if x.size == 0:
        return float("nan")
    if x.size <= 2:
        return float(np.average(x, weights=w))

    mu = weighted_median(x, w)
    scale = robust_scale(x, mu, fallback_rel=0.01)
    if not np.isfinite(scale) or scale <= 0:
        return float(np.average(x, weights=w))

    for _ in range(iters):
        u = (x - mu) / (c * scale)
        k = np.where(np.abs(u) < 1.0, (1.0 - u ** 2) ** 2, 0.0)
        ww = w * k
        if ww.sum() <= 0:
            return mu
        new_mu = float(np.sum(ww * x) / np.sum(ww))
        if abs(new_mu - mu) < tol * max(1.0, abs(mu)):
            mu = new_mu
            break
        mu = new_mu
    return float(mu)


def weighted_trimmed_mean(x, w=None, trim: float = 0.1) -> float:
    """加权截尾均值：按权重累积分布切掉两端 trim 比例。"""
    x, w = _as_arrays(x, w)
    if x.size == 0:
        return float("nan")
    if trim <= 0 or x.size < 5:
        return float(np.average(x, weights=w))
    order = np.argsort(x)
    x, w = x[order], w[order]
    cw = np.cumsum(w) / w.sum()
    keep = (cw > trim) & (cw < 1.0 - trim)
    if not keep.any():
        return weighted_median(x, w)
    return float(np.average(x[keep], weights=w[keep]))


def weighted_geometric_mean(x, w=None) -> float:
    """加权几何平均。仅接受正数（价格与价比天然为正）。"""
    x, w = _as_arrays(x, w)
    ok = x > 0
    x, w = x[ok], w[ok]
    if x.size == 0:
        return float("nan")
    return float(np.exp(np.sum(w * np.log(x)) / np.sum(w)))


def kish_ess(w) -> float:
    """Kish 有效样本量 (Σw)² / Σw²：衡量权重集中度。

    10 个等权样本 ESS=10；若其中一家占 90% 权重，ESS 会掉到 1.2 附近，
    据此可以识别"指数被单一商家绑架"的单元。
    """
    w = np.asarray(w, dtype="float64")
    w = w[np.isfinite(w) & (w > 0)]
    if w.size == 0:
        return 0.0
    return float(w.sum() ** 2 / np.sum(w ** 2))


def cap_weights(w, max_share: float, iters: int = 50) -> np.ndarray:
    """把单个样本的权重份额封顶到 max_share，超出部分按比例再分配给其余样本。

    这是反刷量的关键一步：即使某商家挂出天量库存，其对单元价格的影响也被限制在上限内。
    """
    w = np.asarray(w, dtype="float64").copy()
    w[~np.isfinite(w)] = 0.0
    w[w < 0] = 0.0
    total = w.sum()
    if total <= 0:
        return w
    n = w.size
    if max_share <= 0 or max_share >= 1 or n * max_share <= 1.0:
        # 上限低于等权份额时无法满足，直接退化为等权。
        return np.full(n, total / n)

    share = w / total
    for _ in range(iters):
        over = share > max_share + 1e-12
        if not over.any():
            break
        excess = (share[over] - max_share).sum()
        share[over] = max_share
        free = ~over
        free_sum = share[free].sum()
        if free_sum <= 0:
            share[free] = excess / max(free.sum(), 1)
            break
        share[free] += excess * share[free] / free_sum
    return share * total


def normalize_weights(s: pd.Series) -> pd.Series:
    total = s.sum()
    if total <= 0 or not np.isfinite(total):
        n = len(s)
        return pd.Series(np.full(n, 1.0 / n if n else 0.0), index=s.index)
    return s / total


def safe_pct_change(new, old):
    """百分比变化，分母非正时返回 NaN。"""
    new = np.asarray(new, dtype="float64")
    old = np.asarray(old, dtype="float64")
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(old > 0, new / old - 1.0, np.nan)
    return out
