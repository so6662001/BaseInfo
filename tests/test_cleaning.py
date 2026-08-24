"""清洗层测试：逐条规则验证"非法数据与捣乱数据被剔除、正常数据被保留"。"""

import numpy as np
import pandas as pd
import pytest

from conftest import make_listings
from steel_index import SteelIndexConfig, clean_listings
from steel_index.schema import COL_PRICE


def _rules(result) -> set:
    return set(result.rejected["reject_rule"].unique())


def _rows_of(result, rule) -> pd.DataFrame:
    return result.rejected.loc[result.rejected["reject_rule"] == rule]


def test_clean_data_passes_untouched(clean_listings_df):
    res = clean_listings(clean_listings_df)
    assert res.rejected.empty, f"干净数据被误杀: {_rules(res)}"
    assert len(res.clean) == len(clean_listings_df)


def test_r001_missing_merchant(clean_listings_df):
    df = clean_listings_df.copy()
    df.loc[0:4, "商家id"] = ""
    res = clean_listings(df)
    assert len(_rows_of(res, "R001")) == 5


def test_r002_bad_and_future_dates(clean_listings_df):
    df = clean_listings_df.copy()
    df.loc[0:2, "日期"] = pd.NaT
    df.loc[3:5, "日期"] = pd.Timestamp("2099-12-31")
    df.loc[6, "日期"] = pd.Timestamp("1990-01-01")
    res = clean_listings(df)
    # 少量 2099 年的脏数据不能把合法上界抬穿
    assert len(_rows_of(res, "R002")) == 7


def test_r002_respects_explicit_as_of(clean_listings_df):
    cfg = SteelIndexConfig()
    cfg = cfg.with_overrides(
        cleaning=type(cfg.cleaning)(**{**cfg.cleaning.__dict__, "as_of": "2025-02-10"})
    )
    res = clean_listings(clean_listings_df, cfg)
    assert not _rows_of(res, "R002").empty
    assert res.clean["date"].max() <= pd.Timestamp("2025-02-10")


def test_r003_non_numeric_price(clean_listings_df):
    df = clean_listings_df.copy()
    df["挂牌价"] = df["挂牌价"].astype(object)
    df.loc[0:3, "挂牌价"] = ["电议", "面谈", "abc", ""]
    res = clean_listings(df)
    assert len(_rows_of(res, "R003")) == 4


def test_r004_unknown_category(clean_listings_df):
    df = clean_listings_df.copy()
    df.loc[0:1, "品种"] = "钢材A"
    res = clean_listings(df)
    assert len(_rows_of(res, "R004")) == 2


def test_r010_non_positive_price(clean_listings_df):
    df = clean_listings_df.copy()
    df["挂牌价"] = df["挂牌价"].astype(object)
    df.loc[0, "挂牌价"] = 0
    df.loc[1, "挂牌价"] = -880
    res = clean_listings(df)
    assert len(_rows_of(res, "R010")) == 2


def test_r011_unit_conversion(clean_listings_df):
    """单位写"元/公斤"的报价应折算成元/吨后保留，而不是当离群剔除。"""
    df = clean_listings_df.copy()
    df["挂牌价"] = df["挂牌价"].astype(object)
    idx = df.index[:3]
    df.loc[idx, "挂牌价"] = (pd.to_numeric(df.loc[idx, "挂牌价"]) / 1000.0).round(3)
    df.loc[idx, "单位"] = "元/公斤"
    res = clean_listings(df)
    assert res.rejected.empty
    kept = res.clean.loc[idx]
    assert (kept[COL_PRICE] > 3000).all()
    assert kept["flags"].str.contains("F011").all()


def test_r012_out_of_bounds_price(clean_listings_df):
    df = clean_listings_df.copy()
    df["挂牌价"] = df["挂牌价"].astype(object)
    df.loc[0, "挂牌价"] = 999999      # 天价
    df.loc[1, "挂牌价"] = 12          # 少打几位又不符合任何量纲
    res = clean_listings(df)
    assert len(_rows_of(res, "R012")) >= 1


def test_scale_repair_recovers_kg_prices(clean_listings_df):
    """单位列写着元/吨、实际填的是元/公斤——应自动修复量纲而不是丢掉数据。"""
    df = clean_listings_df.copy()
    df["挂牌价"] = df["挂牌价"].astype(object)
    idx = df.index[:5]
    df.loc[idx, "挂牌价"] = (pd.to_numeric(df.loc[idx, "挂牌价"]) / 1000.0).round(2)
    res = clean_listings(df)
    repaired = res.clean.loc[res.clean["flags"].str.contains("F012", na=False)]
    assert len(repaired) == 5
    assert (repaired[COL_PRICE] > 3000).all()


def test_r013_r014_invalid_stock_keeps_price(clean_listings_df):
    """库存非法只作废库存，不能牵连价格样本。"""
    df = clean_listings_df.copy()
    df.loc[0, "库存量"] = -120
    df.loc[1, "库存量"] = 5e7
    res = clean_listings(df)
    assert res.rejected.empty
    assert res.clean.loc[[0, 1], "stock_qty"].isna().all()
    assert set(res.clean.loc[[0, 1], "stock_reject"]) == {"R013", "R014"}


def test_r020_exact_duplicates(clean_listings_df):
    df = pd.concat([clean_listings_df, clean_listings_df.head(10)], ignore_index=True)
    res = clean_listings(df)
    assert len(_rows_of(res, "R020")) == 10


def test_r021_same_day_multiple_quotes_keeps_median(clean_listings_df):
    df = clean_listings_df.copy()
    row = df.iloc[0].copy()
    extra = []
    for price in (3700.0, 3800.0, 3900.0):
        r = row.copy()
        r["挂牌价"] = price
        extra.append(r)
    df = pd.concat([df, pd.DataFrame(extra)], ignore_index=True)
    res = clean_listings(df)
    assert len(_rows_of(res, "R021")) == 3
    kept = res.clean.loc[
        (res.clean["date"] == row["日期"]) & (res.clean["merchant_id"] == row["商家id"])
        & (res.clean["spec"] == row["规格"])
    ]
    assert len(kept) == 1


def test_r022_quote_spam_removes_whole_day(clean_listings_df):
    """机器灌水：同一天挂出几百条杂乱资源，整日数据不可信。"""
    df = clean_listings_df.copy()
    day = df["日期"].iloc[0]
    spam = pd.DataFrame(
        {
            "日期": day,
            "商家id": "SPAM",
            "城市": "上海",
            "品种": "螺纹钢",
            "规格": [f"Φ{10 + i % 20}" for i in range(500)],
            "钢厂": [f"厂{i % 30}" for i in range(500)],
            "挂牌价": np.linspace(3700, 3900, 500).round(0),
            "单位": "元/吨",
            "库存量": 100.0,
        }
    )
    res = clean_listings(pd.concat([df, spam], ignore_index=True))
    assert len(_rows_of(res, "R022")) == 500
    assert "SPAM" not in set(res.clean["merchant_id"])


def test_r022_fires_before_dedup(clean_listings_df):
    """灌水数据里本身就有大量重复，先去重再数条数会把刷量商家洗白。"""
    df = clean_listings_df.copy()
    day = df["日期"].iloc[0]
    spam = pd.DataFrame(
        {
            "日期": day,
            "商家id": "SPAM",
            "城市": "上海",
            "品种": "螺纹钢",
            "规格": "Φ20",
            "钢厂": "沙钢",
            "挂牌价": 3800.0,
            "单位": "元/吨",
            "库存量": 100.0,
        },
        index=range(600),
    )
    res = clean_listings(pd.concat([df, spam], ignore_index=True))
    assert len(_rows_of(res, "R022")) == 600


def test_r030_cross_section_outlier(clean_listings_df):
    """同单元内明显偏离中枢的报价应被剔除，正常报价保留。"""
    df = clean_listings_df.copy()
    df["挂牌价"] = df["挂牌价"].astype(object)
    df.loc[0, "挂牌价"] = 5200.0   # 在硬边界内，但横截面上离群
    res = clean_listings(df)
    assert 0 in set(_rows_of(res, "R030").index)


def test_r030_keeps_reasonable_spread(dates, trend_path):
    """同单元内 ±3% 的正常价差不应被误杀。"""
    df = make_listings(dates, trend_path, merchants=10,
                       offsets=np.linspace(-0.03, 0.03, 10))
    res = clean_listings(df)
    assert _rows_of(res, "R030").empty


def test_r040_daily_jump(dates, trend_path):
    df = make_listings(dates, trend_path, merchants=8)
    target = (df["商家id"] == "S00") & (df["规格"] == "Φ20") & (df["日期"] == dates[10])
    df.loc[target, "挂牌价"] = df.loc[target, "挂牌价"] * 1.4
    res = clean_listings(df)
    assert not res.rejected.loc[
        res.rejected["reject_rule"].isin(["R030", "R040", "R041"])
    ].empty


def test_r041_isolated_spike(dates, trend_path):
    """涨完立刻回落的孤立毛刺是典型录错，需要单独识别。"""
    df = make_listings(dates, trend_path, merchants=8,
                       offsets=np.linspace(-0.05, 0.05, 8))
    target = (df["商家id"] == "S03") & (df["规格"] == "Φ20") & (df["日期"] == dates[15])
    df.loc[target, "挂牌价"] = df.loc[target, "挂牌价"] * 1.09
    res = clean_listings(df)
    assert not _rows_of(res, "R041").empty


def test_stale_quotes_flagged_not_dropped(dates):
    """僵尸报价缺乏成交意愿但不是伪造数据，应降权而不是剔除。"""
    n = len(dates)
    path = 3800.0 * (1.004 ** np.arange(n))
    df = make_listings(dates, path, merchants=8)
    frozen = df["商家id"] == "S07"
    df.loc[frozen, "挂牌价"] = 3800.0 * (1 + 0.01)
    res = clean_listings(df)
    stale = res.clean.loc[res.clean["is_stale"]]
    assert not stale.empty
    assert set(stale["merchant_id"]) == {"S07"}
    assert stale["flags"].str.contains("F042").all()


def test_report_and_summary_are_populated(clean_listings_df):
    df = clean_listings_df.copy()
    df.loc[0, "商家id"] = ""
    res = clean_listings(df)
    assert not res.report.empty
    assert res.report["rows"].sum() == len(res.rejected)
    assert "数据清洗汇总" in res.summary()
    assert 0.0 < res.reject_rate < 0.01


def test_rejected_records_are_traceable(clean_listings_df):
    df = clean_listings_df.copy()
    df.loc[0, "商家id"] = ""
    res = clean_listings(df)
    assert {"reject_rule", "reject_reason"} <= set(res.rejected.columns)
    assert res.rejected["reject_reason"].notna().all()
