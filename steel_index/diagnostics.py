"""指数质量诊断。

指数本身只是一条曲线，能不能用取决于它背后的样本。这里输出逐日的样本质量指标，
让研究员在发布前就能判断某一天的指数是"市场真的动了"还是"样本不够导致的噪声"。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from .schema import COL_CATEGORY, COL_CELL, COL_CITY, COL_DATE, COL_MERCHANT
from .utils import kish_ess
from .weights import COL_WEIGHT

if TYPE_CHECKING:  # pragma: no cover
    from .cleaning import CleaningResult
    from .credibility import CredibilityPanel
    from .index import IndexResult


def build_diagnostics(cleaning: "CleaningResult", panel: "CredibilityPanel",
                      weighted: pd.DataFrame, indices: "IndexResult") -> pd.DataFrame:
    """逐日质量诊断表。"""
    if weighted.empty:
        return pd.DataFrame()

    raw_all = pd.concat(
        [cleaning.clean.assign(_kept=True), cleaning.rejected.assign(_kept=False)],
        ignore_index=True,
    )
    daily_raw = (
        raw_all.groupby(COL_DATE, observed=True)
        .agg(rows_raw=("_kept", "size"), rows_kept=("_kept", "sum"))
        .reset_index()
    )
    daily_raw["reject_rate"] = 1.0 - daily_raw["rows_kept"] / daily_raw["rows_raw"]

    daily_w = (
        weighted.groupby(COL_DATE, observed=True)
        .agg(
            merchants=(COL_MERCHANT, "nunique"),
            cells=(COL_CELL, "nunique"),
            categories=(COL_CATEGORY, "nunique"),
            cities=(COL_CITY, "nunique"),
            stale_rows=("is_stale", "sum"),
        )
        .reset_index()
    )
    ess = (
        weighted.groupby(COL_DATE, observed=True)[COL_WEIGHT]
        .apply(lambda s: kish_ess(s.to_numpy()))
        .rename("weight_ess")
        .reset_index()
    )
    top_share = (
        weighted.groupby([COL_DATE, COL_MERCHANT], observed=True)[COL_WEIGHT].sum()
        .groupby(level=0, observed=True)
        .apply(lambda s: float(s.max() / s.sum()) if s.sum() > 0 else np.nan)
        .rename("top_merchant_share")
        .reset_index()
    )

    out = daily_raw.merge(daily_w, on=COL_DATE, how="outer") \
                   .merge(ess, on=COL_DATE, how="left") \
                   .merge(top_share, on=COL_DATE, how="left")
    # 某些日期的记录可能被全部剔除（例如整日都是脏数据），补 0 而不是留 NaN。
    for col in ("rows_raw", "rows_kept", "merchants", "cells", "categories", "cities",
                "stale_rows", "weight_ess"):
        if col in out.columns:
            out[col] = out[col].fillna(0)

    comp = indices.composite
    if not comp.empty:
        keep = [c for c in [COL_DATE, "index_value", "chg_1d", "price_level", "n_matched",
                            "n_imputed", "imputed_share", "low_confidence",
                            "diffusion"] if c in comp.columns]
        out = out.merge(comp[keep], on=COL_DATE, how="left")

    return out.sort_values(COL_DATE, ignore_index=True)


def credibility_report(panel: "CredibilityPanel", top_n: int = 15) -> pd.DataFrame:
    """商家可信度画像：最新一期评分，按分数升序（最可疑的排前面）。"""
    if panel.scores.empty:
        return pd.DataFrame()
    last = panel.scores["effective_from"].max()
    snap = panel.scores.loc[panel.scores["effective_from"] == last].copy()
    cols = [COL_MERCHANT, "credibility", "blacklisted", "price_bias", "outlier_rate",
            "vol_ratio", "spam_days", "stale_ratio", "clone_size", "bias",
            "n_days_total", "n_quotes_hist"]
    cols = [c for c in cols if c in snap.columns]
    return snap[cols].sort_values("credibility").head(top_n).reset_index(drop=True)


def format_diagnostics(diag: pd.DataFrame, tail: int = 10) -> str:
    if diag.empty:
        return "无诊断数据"
    show = diag.tail(tail)

    def num(row, name: str, default: float = 0.0) -> float:
        v = getattr(row, name, default)
        try:
            v = float(v)
        except (TypeError, ValueError):
            return default
        return default if not np.isfinite(v) else v

    lines = ["逐日样本质量（最近 %d 天）" % len(show),
             "  日期        原始   保留   剔除率  商家  单元  有效样本  头部商家占比  插补占比"]
    for r in show.itertuples(index=False):
        imp = num(r, "imputed_share", np.nan)
        lines.append(
            "  {d:%Y-%m-%d} {raw:>6} {kept:>6} {rej:>7.1%} {m:>5} {c:>5} {ess:>9.1f}"
            " {top:>12.1%} {imp:>9}".format(
                d=r.date,
                raw=int(num(r, "rows_raw")),
                kept=int(num(r, "rows_kept")),
                rej=num(r, "reject_rate"),
                m=int(num(r, "merchants")),
                c=int(num(r, "cells")),
                ess=num(r, "weight_ess"),
                top=num(r, "top_merchant_share"),
                imp="{:.1%}".format(imp) if np.isfinite(imp) else "-",
            )
        )
    return "\n".join(lines)
