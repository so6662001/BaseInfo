"""指数数学核心测试：面板沿用、匹配样本环比、链式连乘、涨跌广度。"""

import numpy as np
import pandas as pd
import pytest

from steel_index import SteelIndexConfig
from steel_index.core import (
    add_change_columns,
    aggregate_cell_links,
    build_daily_panel,
    chain_index,
    compute_sku_returns,
    impute_cell_links,
    market_breadth,
)


def make_panel(rows):
    """rows: (date_rank, sku, price, weight) —— 直接构造面板，绕开清洗层。"""
    dates = pd.bdate_range("2025-01-02", periods=10)
    df = pd.DataFrame(rows, columns=["date_rank", "sku_id", "price", "weight"])
    df["date"] = [dates[i] for i in df["date_rank"]]
    df["cell_id"] = "rebar|18-22|上海"
    df["category"] = "rebar"
    df["merchant_id"] = df["sku_id"]
    df["is_quoted"] = True
    return df


def test_carry_forward_within_limit_and_exit_after(dates, trend_path):
    """挂牌价可以沿用几天，但超过上限就必须退出样本，不能永远挂在指数里。"""
    from conftest import make_listings
    from steel_index.cleaning import clean_listings
    from steel_index.weights import assign_sample_weights

    df = make_listings(dates[:20], trend_path[:20], merchants=8)
    # 让 S00 只在前 3 天报价，之后彻底消失
    drop = (df["商家id"] == "S00") & (df["日期"] > dates[2])
    df = df.loc[~drop]

    cleaned = clean_listings(df)
    weighted = assign_sample_weights(cleaned.clean, None, SteelIndexConfig())
    panel = build_daily_panel(weighted, SteelIndexConfig())

    s00 = panel.loc[panel["merchant_id"] == "S00"]
    # 最后一次报价是第 3 天（index 2），沿用 5 天 → 最多出现到 index 7
    assert s00["date"].max() == dates[7]
    carried = s00.loc[~s00["is_quoted"]]
    assert len(carried) > 0
    assert carried["date"].min() == dates[3]


def test_carried_quotes_are_down_weighted(dates, trend_path):
    from conftest import make_listings
    from steel_index.cleaning import clean_listings
    from steel_index.weights import assign_sample_weights

    df = make_listings(dates[:10], trend_path[:10], merchants=8)
    df = df.loc[~((df["商家id"] == "S00") & (df["日期"] > dates[2]))]
    cleaned = clean_listings(df)
    weighted = assign_sample_weights(cleaned.clean, None, SteelIndexConfig())
    panel = build_daily_panel(weighted, SteelIndexConfig())

    s00 = panel.loc[panel["merchant_id"] == "S00"]
    quoted_w = s00.loc[s00["is_quoted"], "weight"].mean()
    carried_w = s00.loc[~s00["is_quoted"], "weight"].mean()
    assert carried_w < quoted_w


def test_returns_span_reporting_gaps_within_limit():
    """停报几天后再报价，区间价比仍要计入，否则这段涨跌就凭空消失了。"""
    panel = make_panel([
        (0, "A", 100.0, 1.0),
        (1, "A", 110.0, 1.0),
        (5, "A", 120.0, 1.0),   # 与上一条隔 4 天，仍在沿用上限内
    ])
    out = compute_sku_returns(panel).sort_values("date_rank")
    rets = out["log_ret"].to_numpy()
    assert np.isnan(rets[0])
    assert rets[1] == pytest.approx(np.log(1.1))
    assert rets[2] == pytest.approx(np.log(120 / 110))
    assert out["gap_days"].to_numpy()[2] == pytest.approx(4.0)


def test_returns_ignore_gaps_beyond_limit():
    """停报太久再回来，中间可能已经换了行情，不能把整段变动塞进一天。"""
    panel = make_panel([
        (0, "A", 100.0, 1.0),
        (9, "A", 150.0, 1.0),
    ])
    out = compute_sku_returns(panel).sort_values("date_rank")
    assert out["log_ret"].isna().all()


def test_carried_prices_do_not_contribute_returns():
    """沿用价不能贡献环比——否则"今天没改价"会被当成"今天价格没变"。"""
    panel = make_panel([
        (0, "A", 100.0, 1.0),
        (1, "A", 100.0, 1.0),
        (2, "A", 110.0, 1.0),
    ])
    panel.loc[panel["date_rank"] == 1, "is_quoted"] = False
    out = compute_sku_returns(panel).sort_values("date_rank")
    rets = out["log_ret"].to_numpy()
    assert np.isnan(rets[0])
    assert np.isnan(rets[1])
    assert rets[2] == pytest.approx(np.log(1.1))


def test_matched_sample_excludes_new_entrants():
    """新进场的商家当天只提供价格水平，不提供环比——否则样本一换指数就跳。"""
    panel = make_panel([
        (0, "A", 100.0, 1.0),
        (1, "A", 102.0, 1.0),
        (1, "B", 500.0, 1.0),   # B 第 1 天才出现
    ])
    out = compute_sku_returns(panel)
    matched = out.loc[out["log_ret"].notna()]
    assert set(matched["sku_id"]) == {"A"}


def test_cell_link_is_weighted_geometric_mean():
    panel = make_panel([
        (0, "A", 100.0, 1.0), (1, "A", 110.0, 1.0),
        (0, "B", 100.0, 1.0), (1, "B", 90.0, 1.0),
    ])
    cells = aggregate_cell_links(compute_sku_returns(panel), SteelIndexConfig())
    row = cells.loc[cells["n_matched"] > 0].iloc[0]
    assert row["link"] == pytest.approx(np.sqrt(1.1 * 0.9))
    assert row["n_matched"] == 2


def test_cell_link_respects_weights():
    """权重大的样本对环比影响更大。"""
    panel = make_panel([
        (0, "A", 100.0, 9.0), (1, "A", 110.0, 9.0),
        (0, "B", 100.0, 1.0), (1, "B", 90.0, 1.0),
    ])
    cells = aggregate_cell_links(compute_sku_returns(panel), SteelIndexConfig())
    row = cells.loc[cells["n_matched"] > 0].iloc[0]
    assert row["link"] == pytest.approx(np.exp(0.9 * np.log(1.1) + 0.1 * np.log(0.9)))


def test_link_is_capped_as_last_resort():
    """清洗漏网的极端数据不能让指数一天跳 50%，兜底截断必须生效。"""
    panel = make_panel([(0, "A", 100.0, 1.0), (1, "A", 150.0, 1.0)])
    cfg = SteelIndexConfig()
    cells = aggregate_cell_links(compute_sku_returns(panel), cfg)
    row = cells.loc[cells["n_matched"] > 0].iloc[0]
    assert row["link"] == pytest.approx(1.0 + cfg.index.max_link_move)
    assert bool(row["link_capped"])


def test_cell_price_is_robust_to_outliers():
    panel = make_panel([
        (0, "A", 3800.0, 1.0), (0, "B", 3810.0, 1.0), (0, "C", 3790.0, 1.0),
        (0, "D", 3805.0, 1.0), (0, "E", 3795.0, 1.0), (0, "F", 38000.0, 1.0),
    ])
    cells = aggregate_cell_links(compute_sku_returns(panel), SteelIndexConfig())
    assert cells.iloc[0]["cell_price"] == pytest.approx(3800.0, abs=15.0)


def test_impute_fills_missing_links_from_same_category():
    cells = pd.DataFrame(
        {
            "date": pd.to_datetime(["2025-01-02"] * 3),
            "cell_id": ["c1", "c2", "c3"],
            "category": ["rebar"] * 3,
            "link": [1.02, 1.04, np.nan],
            "n_matched": [10, 10, 0],
            "ess": [10.0, 10.0, 0.0],
        }
    )
    out = impute_cell_links(cells, SteelIndexConfig())
    assert out.loc[out["cell_id"] == "c3", "link"].iloc[0] == pytest.approx(1.03)
    assert bool(out.loc[out["cell_id"] == "c3", "link_imputed"].iloc[0])
    assert not out.loc[out["cell_id"] == "c1", "link_imputed"].iloc[0]


def test_chain_index_starts_at_base_and_compounds():
    dates = pd.bdate_range("2025-01-02", periods=5)
    links = pd.DataFrame({"date": dates, "link": [1.5, 1.01, 1.02, 0.99, 1.03]})
    out = chain_index(links, key=None, base_value=100.0, base_date=dates[0])
    assert out["index_value"].iloc[0] == pytest.approx(100.0)
    assert out["index_value"].iloc[-1] == pytest.approx(
        100.0 * 1.01 * 1.02 * 0.99 * 1.03
    )


def test_chain_index_supports_mid_sample_base_date():
    """基期设在样本中间时，基期之前的历史应被逆向回推，整段仍可比。"""
    dates = pd.bdate_range("2025-01-02", periods=5)
    links = pd.DataFrame({"date": dates, "link": [1.0, 1.10, 1.05, 1.02, 1.01]})
    out = chain_index(links, key=None, base_value=100.0, base_date=dates[2])
    assert out["index_value"].iloc[2] == pytest.approx(100.0)
    assert out["index_value"].iloc[1] == pytest.approx(100.0 / 1.05)
    assert out["index_value"].iloc[0] == pytest.approx(100.0 / 1.05 / 1.10)
    assert out["index_value"].iloc[3] == pytest.approx(102.0)


def test_add_change_columns():
    dates = pd.bdate_range("2025-01-02", periods=8)
    df = pd.DataFrame({"date": dates, "index_value": 100.0 * 1.01 ** np.arange(8)})
    out = add_change_columns(df)
    assert out["chg_1d"].iloc[1] == pytest.approx(0.01)
    assert out["chg_5d"].iloc[5] == pytest.approx(1.01 ** 5 - 1)


def test_market_breadth_counts_directions():
    panel = make_panel([
        (0, "A", 100.0, 1.0), (1, "A", 101.0, 1.0),
        (0, "B", 100.0, 1.0), (1, "B", 99.0, 1.0),
        (0, "C", 100.0, 1.0), (1, "C", 100.0, 1.0),
    ])
    out = market_breadth(compute_sku_returns(panel))
    row = out.iloc[0]
    assert (row["n_up"], row["n_down"], row["n_flat"]) == (1, 1, 1)
    assert row["up_down_ratio"] == pytest.approx(1.0)
    assert row["diffusion"] == pytest.approx(0.0)
