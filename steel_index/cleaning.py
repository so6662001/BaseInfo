"""多层数据清洗：把商家挂牌价里的非法数据与人为捣乱数据剔除干净。

清洗按由硬到软、由个体到群体的顺序分层执行，每层只在上一层的存活样本上工作，
保证横截面/纵向统计量本身不被明显的垃圾数据污染：

    L0 结构层    字段缺失、日期非法、价格非数值、品种无法识别
    L1 量纲层    非正价格、计价单位折算与量纲修复、品种价格硬边界、库存合法性
    L2 去重层    完全重复、同日同 SKU 多次挂牌、单商家单日刷条数
    L3 横截面层  同品种同规格同城市内的稳健离群（MAD-Z + 分位截尾），样本不足时分层回退
    L4 纵向层    单日异常跳变、孤立毛刺（涨完立刻回落）、僵尸报价标记

每条被剔除的记录都会带上规则编号与人可读原因，便于研究员回溯与申诉复核。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

import numpy as np
import pandas as pd

from . import config as cfg
from .schema import (
    COL_CATEGORY,
    COL_CELL,
    COL_DATE,
    COL_MERCHANT,
    COL_PRICE,
    COL_REGION,
    COL_SKU,
    COL_SPEC_GROUP,
    COL_STOCK,
    COL_UNIT,
    COL_CITY,
    prepare_frame,
    unit_factor,
)

RULE_DESCRIPTIONS: Dict[str, str] = {
    "R001": "关键字段缺失（商家标识为空）",
    "R002": "日期非法或超出合理区间（无法解析/未来日期）",
    "R003": "价格非数值",
    "R004": "品种无法识别（不在品种字典内）",
    "R010": "价格非正数",
    "R011": "计价单位无法识别且价格量纲异常",
    "R012": "价格超出品种物理边界（录错或恶意乱填）",
    "R013": "库存为负数（库存作废，价格保留）",
    "R014": "库存超出单商家单SKU上限（库存作废，价格保留）",
    "R020": "完全重复记录",
    "R021": "同商家同日同SKU重复挂牌（保留最贴近组内中位数的一条）",
    "R022": "单商家单日报价条数异常（疑似机器灌水刷量）",
    "R030": "横截面稳健离群（同品种同规格同城市内 MAD-Z 超阈值）",
    "R031": "横截面分位截尾（大样本单元两端极值）",
    "R040": "单日涨跌幅超品种上限（异常跳变）",
    "R041": "孤立毛刺（跳变后立即回归前值，典型录错或试探性乱报）",
}

FLAG_DESCRIPTIONS: Dict[str, str] = {
    "F011": "按计价单位折算到元/吨",
    "F012": "价格量纲已自动修复（如元/公斤误填为元/吨）",
    "F013": "计价单位未识别，按元/吨处理",
    "F042": "僵尸报价（长期不动价，参与计算但已降权）",
}

_REJECT = "_reject_rule"
_FLAGS = "_flags"
_PRICE_RAW = "price_raw"
_N_QUOTES = "n_quotes"
_STALE = "is_stale"


@dataclass
class CleaningResult:
    """清洗产出。

    clean    : 通过全部规则的记录（价格已折算为元/吨），带 is_stale 等标记列
    rejected : 被剔除的记录，含 reject_rule / reject_reason，用于复核
    report   : 按规则汇总的剔除条数与占比
    """

    clean: pd.DataFrame
    rejected: pd.DataFrame
    report: pd.DataFrame
    stats: Dict[str, float] = field(default_factory=dict)

    @property
    def reject_rate(self) -> float:
        total = len(self.clean) + len(self.rejected)
        return len(self.rejected) / total if total else 0.0

    def summary(self) -> str:
        lines = [
            "数据清洗汇总",
            f"  原始记录数: {int(self.stats.get('rows_raw', 0)):,}",
            f"  有效记录数 : {len(self.clean):,}",
            f"  剔除记录数 : {len(self.rejected):,}（{self.reject_rate:.2%}）",
            f"  僵尸报价   : {int(self.stats.get('stale_rows', 0)):,} 条（降权处理）",
            f"  量纲修复   : {int(self.stats.get('repaired_rows', 0)):,} 条",
            "  分规则明细 :",
        ]
        for row in self.report.itertuples(index=False):
            lines.append(
                f"    {row.rule} {row.description:<38} {row.rows:>8,} 条 "
                f"({row.share:.2%})"
            )
        return "\n".join(lines)


def _mark(df: pd.DataFrame, mask: pd.Series, rule: str) -> None:
    """给尚未被剔除的行打上剔除规则。"""
    target = mask & df[_REJECT].isna()
    df.loc[target, _REJECT] = rule


def _add_flag(df: pd.DataFrame, mask: pd.Series, flag: str) -> None:
    if not mask.any():
        return
    existing = df.loc[mask, _FLAGS]
    df.loc[mask, _FLAGS] = np.where(
        existing.eq("") | existing.isna(), flag, existing.astype(str) + "," + flag
    )


def _active(df: pd.DataFrame) -> pd.Series:
    return df[_REJECT].isna()


# --------------------------------------------------------------------------------------
# L0 结构层
# --------------------------------------------------------------------------------------
def _layer_structure(df: pd.DataFrame, c: cfg.CleaningConfig) -> None:
    _mark(df, df[COL_MERCHANT].isna() | df[COL_MERCHANT].eq(""), "R001")

    bad_date = df[COL_DATE].isna()
    valid = ~bad_date
    if valid.any():
        dates = df.loc[valid, COL_DATE]
        # 用 max() 定上界会被"2099-12-31"这类脏数据直接抬穿，因此取 P99.5 再外扩。
        if c.as_of:
            horizon = pd.Timestamp(c.as_of)
        else:
            horizon = pd.Timestamp(dates.quantile(0.995)) + pd.Timedelta(days=30)
        floor = pd.Timestamp(dates.quantile(0.005)) - pd.Timedelta(
            days=int(365.25 * c.max_history_years)
        )
        bad_date = bad_date | (df[COL_DATE] > horizon) | (df[COL_DATE] < floor)
    _mark(df, bad_date, "R002")

    _mark(df, df[COL_PRICE].isna(), "R003")
    _mark(df, df[COL_CATEGORY].isna(), "R004")


# --------------------------------------------------------------------------------------
# L1 量纲与硬边界层
# --------------------------------------------------------------------------------------
def _layer_units_and_bounds(df: pd.DataFrame, c: cfg.CleaningConfig) -> None:
    act = _active(df)
    _mark(df, act & (df[COL_PRICE] <= 0), "R010")

    act = _active(df)
    if not act.any():
        return

    factors = df.loc[act, COL_UNIT].map(unit_factor)
    unknown_unit = factors.isna()
    _add_flag(df, act & unknown_unit.reindex(df.index, fill_value=False), "F013")
    factors = factors.fillna(1.0)
    converted = df.loc[act, COL_PRICE] * factors
    _add_flag(
        df,
        act & (factors != 1.0).reindex(df.index, fill_value=False),
        "F011",
    )
    df.loc[act, COL_PRICE] = converted

    # 品种硬边界（向量化：把边界按品种映射成两列）
    act = _active(df)
    lo = df.loc[act, COL_CATEGORY].map(lambda k: cfg.price_bounds(k)[0]).astype("float64")
    hi = df.loc[act, COL_CATEGORY].map(lambda k: cfg.price_bounds(k)[1]).astype("float64")
    price = df.loc[act, COL_PRICE].astype("float64")
    out_of_range = (price < lo) | (price > hi)

    if c.enable_unit_repair and out_of_range.any():
        idx = out_of_range[out_of_range].index
        repaired = pd.Series(np.nan, index=idx, dtype="float64")
        center = (lo.loc[idx] + hi.loc[idx]) / 2.0
        best_dist = pd.Series(np.inf, index=idx, dtype="float64")
        for f in c.unit_repair_factors:
            cand = price.loc[idx] * f
            ok = (cand >= lo.loc[idx]) & (cand <= hi.loc[idx])
            dist = (cand - center).abs()
            take = ok & (dist < best_dist)
            repaired[take] = cand[take]
            best_dist[take] = dist[take]
        fixed = repaired.notna()
        if fixed.any():
            fix_idx = idx[fixed.to_numpy()]
            df.loc[fix_idx, COL_PRICE] = repaired.loc[fix_idx].to_numpy()
            _add_flag(df, df.index.isin(fix_idx), "F012")
            out_of_range.loc[fix_idx] = False

    _mark(df, out_of_range.reindex(df.index, fill_value=False), "R012")

    # 库存非法只作废库存本身，不牵连价格样本：价格与库存是两个独立的可信度问题。
    bad_stock = df[COL_STOCK] < 0
    df.loc[bad_stock, COL_STOCK] = np.nan
    df.loc[bad_stock, "stock_reject"] = "R013"

    too_big = df[COL_STOCK] > c.max_stock_per_sku
    df.loc[too_big, COL_STOCK] = np.nan
    df.loc[too_big, "stock_reject"] = "R014"


# --------------------------------------------------------------------------------------
# L2 去重层
# --------------------------------------------------------------------------------------
def _layer_dedup(df: pd.DataFrame, c: cfg.CleaningConfig) -> None:
    # 刷量判定必须放在去重之前：灌水数据里本身就有大量重复，
    # 先去重再数条数，刷量商家反而会被"洗白"成正常商家。
    act = _active(df)
    if not act.any():
        return
    counts = df.loc[act].groupby([COL_DATE, COL_MERCHANT], observed=True)[COL_PRICE].transform("size")
    spam = counts > c.max_quotes_per_merchant_day
    _mark(df, spam.reindex(df.index, fill_value=False), "R022")

    act = _active(df)
    sub = df.loc[act]

    dup_cols = [COL_DATE, COL_SKU, COL_PRICE]
    exact_dup = sub.duplicated(subset=dup_cols, keep="first")
    _mark(df, exact_dup.reindex(df.index, fill_value=False), "R020")

    # 同日同 SKU 的多条不同报价：保留最贴近组内中位数的一条，其余标记重复。
    act = _active(df)
    sub = df.loc[act, [COL_DATE, COL_SKU, COL_PRICE]].copy()
    grp = sub.groupby([COL_DATE, COL_SKU], observed=True)[COL_PRICE]
    n = grp.transform("size")
    med = grp.transform("median")
    df.loc[act, _N_QUOTES] = n.to_numpy()

    multi = n > 1
    if multi.any():
        sub["_dist"] = (sub[COL_PRICE] - med).abs()
        sub["_order"] = np.arange(len(sub))
        keep_idx = (
            sub.loc[multi]
            .sort_values(["_dist", "_order"])
            .groupby([COL_DATE, COL_SKU], observed=True)
            .head(1)
            .index
        )
        drop_mask = pd.Series(False, index=df.index)
        drop_mask.loc[sub.index[multi]] = True
        drop_mask.loc[keep_idx] = False
        _mark(df, drop_mask, "R021")


# --------------------------------------------------------------------------------------
# L3 横截面稳健离群层
# --------------------------------------------------------------------------------------
_CELL_LEVELS = (
    (COL_DATE, COL_CATEGORY, COL_SPEC_GROUP, COL_CITY),
    (COL_DATE, COL_CATEGORY, COL_CITY),
    (COL_DATE, COL_CATEGORY, COL_REGION),
    (COL_DATE, COL_CATEGORY),
)


def _layer_cross_section(df: pd.DataFrame, c: cfg.CleaningConfig) -> None:
    act = _active(df)
    if not act.any():
        return
    sub = df.loc[act, list({col for lvl in _CELL_LEVELS for col in lvl} | {COL_PRICE})].copy()
    price = sub[COL_PRICE].astype("float64")

    center = pd.Series(np.nan, index=sub.index, dtype="float64")
    scale = pd.Series(np.nan, index=sub.index, dtype="float64")
    level_used = pd.Series(len(_CELL_LEVELS), index=sub.index, dtype="int64")

    # 分层回退：细单元样本不足时，逐级放宽到城市 / 区域 / 全国，
    # 保证既贴近可比口径，又有足够样本支撑稳健统计。
    for lvl_no, keys in enumerate(_CELL_LEVELS):
        pending = center.isna()
        if not pending.any():
            break
        g = sub.groupby(list(keys), observed=True)[COL_PRICE]
        n = g.transform("size")
        med = g.transform("median")
        absdev = (price - med).abs()
        mad = absdev.groupby([sub[k] for k in keys], observed=True).transform("median")
        s = mad * 1.4826
        s = s.where(s > 0, med.abs() * c.zero_mad_band)

        enough = n >= c.min_cell_size
        take = pending & enough & med.notna() & s.notna() & (s > 0)
        center[take] = med[take]
        scale[take] = s[take]
        level_used[take] = lvl_no

    usable = center.notna() & scale.notna() & (scale > 0)
    z = pd.Series(0.0, index=sub.index, dtype="float64")
    z[usable] = (price[usable] - center[usable]) / scale[usable]
    df.loc[sub.index, "robust_z"] = z.to_numpy()
    df.loc[sub.index, "cell_center_price"] = center.to_numpy()
    df.loc[sub.index, "cell_level"] = level_used.to_numpy()

    outlier = usable & (z.abs() > c.robust_z_threshold)
    _mark(df, outlier.reindex(df.index, fill_value=False), "R030")

    # 分位截尾只在大样本单元启用，小样本下截尾会误杀正常报价。
    if c.quantile_trim > 0:
        act = _active(df)
        sub2 = df.loc[act, [COL_DATE, COL_CATEGORY, COL_SPEC_GROUP, COL_CITY, COL_PRICE]]
        keys = [COL_DATE, COL_CATEGORY, COL_SPEC_GROUP, COL_CITY]
        g = sub2.groupby(keys, observed=True)[COL_PRICE]
        n = g.transform("size")
        min_n = max(int(np.ceil(2.0 / c.quantile_trim)), 50)
        lo = g.transform(lambda s: s.quantile(c.quantile_trim))
        hi = g.transform(lambda s: s.quantile(1.0 - c.quantile_trim))
        trimmed = (n >= min_n) & ((sub2[COL_PRICE] < lo) | (sub2[COL_PRICE] > hi))
        _mark(df, trimmed.reindex(df.index, fill_value=False), "R031")


# --------------------------------------------------------------------------------------
# L4 纵向跳变与僵尸报价层
# --------------------------------------------------------------------------------------
def _layer_time_series(df: pd.DataFrame, c: cfg.CleaningConfig) -> None:
    act = _active(df)
    if not act.any():
        return
    cols = [COL_DATE, COL_SKU, COL_CATEGORY, COL_CELL, COL_PRICE]
    sub = df.loc[act, cols].sort_values([COL_SKU, COL_DATE])

    g = sub.groupby(COL_SKU, observed=True)
    prev_price = g[COL_PRICE].shift(1)
    prev_date = g[COL_DATE].shift(1)
    next_price = g[COL_PRICE].shift(-1)
    next_date = g[COL_DATE].shift(-1)

    gap_prev = (sub[COL_DATE] - prev_date).dt.days
    gap_next = (next_date - sub[COL_DATE]).dt.days

    thr = sub[COL_CATEGORY].map(cfg.max_daily_move).astype("float64")
    # 报价有间隔时按随机游走放宽阈值，避免把"停报一周后正常调价"误判为异常。
    span = gap_prev.clip(lower=1).fillna(1).astype("float64")
    thr_prev = thr * np.sqrt(np.minimum(span, float(c.carry_forward_days)))

    with np.errstate(divide="ignore", invalid="ignore"):
        ret = sub[COL_PRICE] / prev_price - 1.0

    comparable = prev_price.notna() & (gap_prev <= c.carry_forward_days)
    jump = comparable & (ret.abs() > thr_prev)
    _mark(df, jump.reindex(df.index, fill_value=False), "R040")

    # 孤立毛刺：t 点明显偏离，而 t-1 与 t+1 基本一致 —— 典型的手滑或试探性乱报。
    spike_thr = np.maximum(thr * 0.6, 0.03)
    with np.errstate(divide="ignore", invalid="ignore"):
        around = next_price / prev_price - 1.0
        dev_prev = (sub[COL_PRICE] / prev_price - 1.0).abs()
        dev_next = (sub[COL_PRICE] / next_price - 1.0).abs()
    spike = (
        prev_price.notna()
        & next_price.notna()
        & (gap_prev <= c.carry_forward_days)
        & (gap_next <= c.carry_forward_days)
        & (dev_prev > spike_thr)
        & (dev_next > spike_thr)
        & (around.abs() <= c.spike_reversion_tol)
    )
    _mark(df, spike.reindex(df.index, fill_value=False), "R041")

    _flag_stale(df, c)


def _flag_stale(df: pd.DataFrame, c: cfg.CleaningConfig) -> None:
    """标记僵尸报价：长期一动不动、而同期市场明显在走的挂牌价。

    这类报价不是伪造数据，但缺乏成交意愿，直接等权计入会让指数变钝，
    因此只降权而不剔除。
    """
    act = _active(df)
    df[_STALE] = False
    if not act.any():
        return

    sub = df.loc[act, [COL_DATE, COL_SKU, COL_CELL, COL_PRICE]].sort_values([COL_SKU, COL_DATE])
    changed = sub.groupby(COL_SKU, observed=True)[COL_PRICE].transform(
        lambda s: s.ne(s.shift()).cumsum()
    )
    run_keys = [sub[COL_SKU], changed]
    run_start = sub.groupby(run_keys, observed=True)[COL_DATE].transform("min")
    run_days = (sub[COL_DATE] - run_start).dt.days

    # 同期市场变动：用单元中位价的首尾比值衡量。
    cell_med = sub.groupby([COL_CELL, COL_DATE], observed=True)[COL_PRICE].median()
    now_ref = pd.MultiIndex.from_arrays([sub[COL_CELL], sub[COL_DATE]])
    start_ref = pd.MultiIndex.from_arrays([sub[COL_CELL], run_start])
    p_now = pd.Series(cell_med.reindex(now_ref).to_numpy(), index=sub.index)
    p_start = pd.Series(cell_med.reindex(start_ref).to_numpy(), index=sub.index)
    with np.errstate(divide="ignore", invalid="ignore"):
        market_move = (p_now / p_start - 1.0).abs()

    stale = (run_days >= c.stale_days) & (market_move.fillna(0.0) > c.stale_market_move)
    stale = stale.reindex(df.index, fill_value=False)
    df.loc[stale, _STALE] = True
    _add_flag(df, stale, "F042")


# --------------------------------------------------------------------------------------
# 对外入口
# --------------------------------------------------------------------------------------
def clean_listings(df: pd.DataFrame, config: cfg.SteelIndexConfig | None = None,
                   already_prepared: bool = False) -> CleaningResult:
    """执行完整清洗流水线。

    参数
    ----
    df : 原始挂牌价数据（列名可为中文别名）
    config : 配置；缺省使用默认阈值
    already_prepared : 输入是否已经过 schema.prepare_frame 处理
    """
    config = config or cfg.SteelIndexConfig()
    c = config.cleaning

    work = df if already_prepared else prepare_frame(df)
    work = work.copy()
    work[_REJECT] = pd.Series(pd.NA, index=work.index, dtype="string")
    work[_FLAGS] = ""
    work[_PRICE_RAW] = work[COL_PRICE]
    work[_N_QUOTES] = 1
    work["stock_reject"] = pd.Series(pd.NA, index=work.index, dtype="string")
    rows_raw = len(work)

    _layer_structure(work, c)
    _layer_units_and_bounds(work, c)
    _layer_dedup(work, c)
    _layer_cross_section(work, c)
    _layer_time_series(work, c)

    rejected_mask = work[_REJECT].notna()
    rejected = work.loc[rejected_mask].copy()
    rejected = rejected.rename(columns={_REJECT: "reject_rule", _FLAGS: "flags"})
    rejected["reject_reason"] = rejected["reject_rule"].map(RULE_DESCRIPTIONS)

    clean = work.loc[~rejected_mask].drop(columns=[_REJECT]).copy()
    clean = clean.rename(columns={_FLAGS: "flags"})

    counts = rejected["reject_rule"].value_counts() if len(rejected) else pd.Series(dtype="int64")
    report_rows: List[dict] = []
    for rule in RULE_DESCRIPTIONS:
        n = int(counts.get(rule, 0))
        if n == 0:
            continue
        report_rows.append(
            {
                "rule": rule,
                "description": RULE_DESCRIPTIONS[rule],
                "rows": n,
                "share": n / rows_raw if rows_raw else 0.0,
            }
        )
    report = pd.DataFrame(report_rows, columns=["rule", "description", "rows", "share"])

    stats = {
        "rows_raw": float(rows_raw),
        "rows_clean": float(len(clean)),
        "rows_rejected": float(len(rejected)),
        "stale_rows": float(clean[_STALE].sum()) if _STALE in clean else 0.0,
        "repaired_rows": float(clean["flags"].str.contains("F012", na=False).sum()),
        "merchants_clean": float(clean[COL_MERCHANT].nunique()) if len(clean) else 0.0,
    }
    return CleaningResult(clean=clean, rejected=rejected, report=report, stats=stats)
