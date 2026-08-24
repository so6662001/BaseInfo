"""端到端测试：在含各类捣乱数据的合成市场上，验证指数能否还原真实行情。"""

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from conftest import make_listings
from steel_index import SteelIndexConfig, generate_market, run_index
from steel_index.cli import main as cli_main
from steel_index.diagnostics import credibility_report, format_diagnostics


@pytest.fixture(scope="module")
def market():
    return generate_market(n_days=100, n_merchants=70, seed=99)


@pytest.fixture(scope="module")
def result(market):
    cfg = SteelIndexConfig()
    cfg = cfg.with_overrides(index=replace(cfg.index, category_weight_mode="fixed"))
    return run_index(market.listings, config=cfg)


def test_pipeline_outputs_have_expected_shape(result):
    comp, var = result.composite, result.variety
    assert not comp.empty and not var.empty
    assert comp["date"].is_unique
    assert {"index_value", "chg_1d", "price_level", "n_matched", "n_merchants",
            "low_confidence", "index_smooth"} <= set(comp.columns)
    assert {"category", "category_name", "index_value", "chg_1d",
            "price_level"} <= set(var.columns)
    assert comp["index_value"].gt(0).all()
    assert var.groupby("category")["date"].count().gt(0).all()


def test_index_tracks_truth_despite_dirty_data(market, result):
    """核心验收：面对量纲错误、恶意压价、乱跳价、刷量、小号等干扰，
    指数仍要贴住真实价格路径。"""
    truth = market.truth_composite()
    comp = result.composite[["date", "index_value"]].merge(truth, on="date")
    assert len(comp) > 50

    level_corr = comp["index_value"].corr(comp["true_index"])
    ret_corr = comp["index_value"].pct_change().corr(comp["true_index"].pct_change())
    mape = ((comp["index_value"] - comp["true_index"]).abs() / comp["true_index"]).mean()

    assert level_corr > 0.97, f"指数水平相关性过低: {level_corr:.4f}"
    assert ret_corr > 0.85, f"日涨跌相关性过低: {ret_corr:.4f}"
    assert mape < 0.02, f"相对误差过大: {mape:.4%}"


def test_variety_indices_track_their_own_truth(market, result):
    """分品种指数要各自贴住本品种的真实路径，不能互相串味。"""
    truth = market.truth.copy()
    var = result.variety[["date", "category", "index_value"]]
    merged = var.merge(truth, on=["date", "category"], how="inner")
    assert not merged.empty

    for cat, part in merged.groupby("category"):
        part = part.sort_values("date")
        true_index = 100.0 * part["true_price"] / part["true_price"].iloc[0]
        corr = part["index_value"].corr(true_index)
        mape = ((part["index_value"] - true_index).abs() / true_index).mean()
        assert corr > 0.95, f"{cat} 指数相关性过低: {corr:.4f}"
        assert mape < 0.03, f"{cat} 相对误差过大: {mape:.4%}"


def test_dirty_merchants_are_caught(market, result):
    """恶意压价、抬价、乱跳价、灌水的商家应被识别并排除在指数之外。"""
    scores = result.credibility.scores
    blacklisted = set(scores.loc[scores["blacklisted"], "merchant_id"])
    truth_bad = set(market.dirty_merchants)
    assert truth_bad, "合成数据里应当存在捣乱商家"

    recall = len(blacklisted & truth_bad) / len(truth_bad)
    assert recall > 0.75, f"捣乱商家召回过低: {recall:.1%}"

    # 只有把完全正常的商家拉黑才算误伤。僵尸商家（长期不动价）被拉黑不算：
    # 挂着长期不维护的报价本就不该进指数。
    normal = set(market.normal_merchants)
    false_positive = len(blacklisted & normal) / len(normal)
    assert false_positive < 0.10, f"正常商家误伤率过高: {false_positive:.1%}"

    # 拉黑是 point-in-time 的：只从评分生效日起排除，不追溯修改历史指数
    assert not result.weighted["blacklisted"].any()
    last_eff = scores["effective_from"].max()
    last = scores.loc[scores["effective_from"] == last_eff]
    banned_now = set(last.loc[last["blacklisted"], "merchant_id"])
    still_in = result.weighted.loc[result.weighted["date"] >= last_eff, "merchant_id"]
    assert not (banned_now & set(still_in)), "评分生效后被拉黑商家仍参与了指数计算"


def test_clone_accounts_are_grouped(market, result):
    """同源小号应被归组并摊薄权重，避免一个人用多个账号放大话语权。"""
    clones = result.credibility.clones
    assert not clones.empty
    truth_clone = set(market.clone_merchants)
    found = set(clones["merchant_id"])
    assert found <= truth_clone, f"误判为同源账号: {found - truth_clone}"
    assert len(found) >= 3
    assert (clones["clone_size"] >= 2).all()


def test_unit_errors_are_repaired_not_discarded(market, result):
    """单位填错（元/公斤当元/吨）应当修复量纲后继续使用，而不是把商家一棍子打死。"""
    unit_bad = set(market.merchants.loc[
        market.merchants["type"] == "unit_error", "merchant_id"])
    assert unit_bad
    kept = result.weighted.loc[result.weighted["merchant_id"].isin(unit_bad)]
    assert len(kept) > 100, "量纲填错的商家数据被过度剔除"
    assert kept["price"].between(1800, 8000).all()
    assert result.cleaning.stats["repaired_rows"] > 0


def test_spammers_are_removed(market, result):
    spam_ids = set(market.merchants.loc[
        market.merchants["type"] == "spammer", "merchant_id"])
    if not spam_ids:
        pytest.skip("本次抽样未生成灌水商家")
    rejected = result.cleaning.rejected
    r022 = rejected.loc[rejected["reject_rule"] == "R022", "merchant_id"]
    assert set(r022) & spam_ids


def test_illegal_records_are_all_rejected(result):
    """注入的各类非法数据都应被对应规则拦下。"""
    rules = set(result.cleaning.report["rule"])
    for rule in ("R001", "R002", "R003", "R004", "R010", "R012", "R022"):
        assert rule in rules, f"规则 {rule} 未拦到任何数据"

    clean = result.cleaning.clean
    assert clean["price"].gt(0).all()
    assert clean["date"].notna().all()
    assert clean["date"].max() < pd.Timestamp("2030-01-01"), "未来日期未被剔除"
    assert clean["category"].notna().all()
    assert clean["merchant_id"].ne("").all()


def test_weight_concentration_is_bounded(result):
    # 全天数据都被剔除的日期（例如整日都是脏数据）不参与集中度检查
    diag = result.diagnostics.loc[result.diagnostics["rows_kept"] > 0]
    assert not diag.empty
    assert diag["top_merchant_share"].max() < 0.25
    assert diag["weight_ess"].min() > 5.0


def test_strict_link_mode_gives_similar_index(market):
    """严格匹配样本模式（只用连续两日真实报价）应给出方向一致的指数。"""
    base = SteelIndexConfig()
    cfg_a = base.with_overrides(index=replace(base.index, category_weight_mode="fixed"))
    cfg_b = base.with_overrides(
        index=replace(base.index, category_weight_mode="fixed", link_mode="strict")
    )
    a = run_index(market.listings, config=cfg_a).composite.set_index("date")["index_value"]
    b = run_index(market.listings, config=cfg_b).composite.set_index("date")["index_value"]
    assert a.corr(b) > 0.95


def test_inventory_table_can_be_attached(dates, trend_path):
    """库存可以来自独立表，按共有维度自动连接。"""
    listings = make_listings(dates, trend_path, merchants=8, stock=np.nan)
    inv = pd.DataFrame(
        [
            {"日期": d, "商家id": f"S{i:02d}", "品种": "螺纹钢", "城市": "上海",
             "库存量": 500.0 * (i + 1)}
            for i in range(8) for d in dates
        ]
    )
    out = run_index(listings, inventory=inv)
    assert out.weighted["stock_qty"].notna().all()
    # 库存大的商家权重应更高，但受封顶约束
    w = out.weighted.groupby("merchant_id")["weight"].mean()
    assert w["S07"] > w["S00"]


def test_summary_and_reports_render(result):
    text = result.summary()
    assert "钢铁综合行情指数" in text
    assert "分品种行情指数" in text
    assert "数据清洗汇总" in text
    assert format_diagnostics(result.diagnostics)
    assert not credibility_report(result.credibility).empty


def test_empty_input_does_not_crash():
    empty = pd.DataFrame(columns=["日期", "商家id", "品种", "挂牌价"])
    out = run_index(empty)
    assert out.composite.empty
    assert out.variety.empty


def test_all_data_illegal_does_not_crash():
    df = pd.DataFrame(
        {
            "日期": [pd.NaT] * 5,
            "商家id": [""] * 5,
            "品种": ["钢材A"] * 5,
            "挂牌价": ["电议"] * 5,
        }
    )
    out = run_index(df)
    assert out.composite.empty
    assert len(out.cleaning.rejected) == 5


def test_cli_demo_runs(tmp_path, capsys):
    code = cli_main(["demo", "--days", "30", "--merchants", "25",
                     "--outdir", str(tmp_path)])
    assert code == 0
    out = capsys.readouterr().out
    assert "钢铁综合行情指数" in out
    for name in ("composite_index.csv", "variety_index.csv", "rejected_records.csv",
                 "merchant_credibility.csv", "daily_diagnostics.csv"):
        assert (tmp_path / name).exists()


def test_cli_run_reads_csv(tmp_path, dates, trend_path, capsys):
    path = tmp_path / "listings.csv"
    make_listings(dates, trend_path, merchants=8).to_csv(path, index=False)
    code = cli_main(["run", "--listings", str(path), "--outdir", str(tmp_path / "o")])
    assert code == 0
    assert "钢铁综合行情指数" in capsys.readouterr().out
