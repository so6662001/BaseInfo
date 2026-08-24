"""指数层测试：精确还原能力、样本变动不变性、抗操纵能力、两级指数一致性。"""

import numpy as np
import pandas as pd
import pytest
from dataclasses import replace

from conftest import make_listings
from steel_index import SteelIndexConfig, run_index


def _cfg(**index_kwargs) -> SteelIndexConfig:
    base = SteelIndexConfig()
    if index_kwargs:
        base = base.with_overrides(index=replace(base.index, **index_kwargs))
    return base


def test_index_recovers_known_uniform_trend(dates, trend_path):
    """所有报价同步每天涨 0.2% 时，指数必须精确复刻这个涨幅。"""
    df = make_listings(dates, trend_path, merchants=8)
    out = run_index(df, config=_cfg())
    comp = out.composite

    assert comp["index_value"].iloc[0] == pytest.approx(100.0)
    expected = 100.0 * 1.002 ** (len(dates) - 1)
    assert comp["index_value"].iloc[-1] == pytest.approx(expected, rel=1e-6)
    # 容差留给构造数据时的挂牌价取整（元/吨保留 2 位）
    assert comp["chg_1d"].dropna().to_numpy() == pytest.approx(0.002, abs=1e-5)


def test_index_recovers_known_downtrend(dates):
    path = 4200.0 * (0.997 ** np.arange(len(dates)))
    df = make_listings(dates, path, merchants=8)
    comp = run_index(df, config=_cfg()).composite
    assert comp["index_value"].iloc[-1] == pytest.approx(
        100.0 * 0.997 ** (len(dates) - 1), rel=1e-6
    )


def test_base_date_can_be_set_mid_sample(dates, trend_path):
    df = make_listings(dates, trend_path, merchants=8)
    base = dates[10]
    comp = run_index(df, config=_cfg(base_date=str(base.date()))).composite
    at_base = comp.loc[comp["date"] == base, "index_value"].iloc[0]
    assert at_base == pytest.approx(100.0)


def test_new_merchants_entering_do_not_shift_index(dates, trend_path):
    """核心不变性：中途进场一批价格水平明显不同的商家，指数不能跳。

    这是"匹配样本"设计的意义所在——直接用全市场均价做指数，
    这里会因为样本换血而出现一个假的台阶。
    """
    baseline = make_listings(dates, trend_path, merchants=8)
    newcomers = make_listings(
        dates, trend_path * 1.15, merchants=4,
        offsets=np.linspace(-0.005, 0.005, 4),
    )
    newcomers["商家id"] = "NEW" + newcomers["商家id"]
    newcomers = newcomers.loc[newcomers["日期"] >= dates[20]]

    comp_a = run_index(baseline, config=_cfg()).composite.set_index("date")
    comp_b = run_index(pd.concat([baseline, newcomers], ignore_index=True),
                       config=_cfg()).composite.set_index("date")

    diff = (comp_b["index_value"] - comp_a["index_value"]).abs()
    assert diff.max() < 0.05, f"样本变动导致指数偏移 {diff.max():.4f} 点"


def test_merchants_leaving_do_not_shift_index(dates, trend_path):
    baseline = make_listings(dates, trend_path, merchants=10,
                            offsets=np.linspace(-0.03, 0.03, 10))
    # 让报价最高的两家在中途退出市场
    leaving = baseline["商家id"].isin(["S08", "S09"]) & (baseline["日期"] > dates[20])
    comp_a = run_index(baseline, config=_cfg()).composite.set_index("date")
    comp_b = run_index(baseline.loc[~leaving], config=_cfg()).composite.set_index("date")

    diff = (comp_b["index_value"] - comp_a["index_value"]).abs()
    assert diff.max() < 0.05


def test_single_merchant_with_huge_stock_cannot_hijack_index(dates, trend_path):
    """反操纵：一家挂天量库存并把价格往下砸，指数不能被它带走。"""
    honest = make_listings(dates, trend_path, merchants=10, stock=800.0)

    # 捣乱者：库存是别人的 500 倍，价格从第 20 天起一路砸到 -30%
    attack_path = trend_path.copy()
    attack_path[20:] = attack_path[20:] * np.linspace(0.98, 0.70, len(dates) - 20)
    attacker = make_listings(dates, attack_path, merchants=1, stock=400_000.0,
                             offsets=[0.0])
    attacker["商家id"] = "ATTACKER"

    comp_clean = run_index(honest, config=_cfg()).composite.set_index("date")
    comp_attacked = run_index(pd.concat([honest, attacker], ignore_index=True),
                              config=_cfg()).composite.set_index("date")

    drift = (comp_attacked["index_value"] - comp_clean["index_value"]).abs().max()
    assert drift < 0.5, f"指数被单个天量库存商家带偏 {drift:.3f} 点"


def test_protections_matter(dates, trend_path):
    """对照实验：关掉封顶与库存变换后，同一次攻击造成的偏移应显著变大。

    这条测试用来证明防护措施真的在起作用，而不是攻击本身没威胁。
    """
    honest = make_listings(dates, trend_path, merchants=10, stock=800.0)
    attack_path = trend_path.copy()
    attack_path[20:] = attack_path[20:] * np.linspace(0.98, 0.70, len(dates) - 20)
    attacker = make_listings(dates, attack_path, merchants=1, stock=400_000.0,
                             offsets=[0.0])
    attacker["商家id"] = "ATTACKER"
    polluted = pd.concat([honest, attacker], ignore_index=True)

    protected = SteelIndexConfig()
    naive = protected.with_overrides(
        weight=replace(protected.weight, stock_transform="raw", max_merchant_share=1.0,
                       use_credibility=False),
        cleaning=replace(protected.cleaning, robust_z_threshold=1e9,
                         spike_reversion_tol=0.0),
    )

    base = run_index(honest, config=protected).composite.set_index("date")["index_value"]
    with_protection = run_index(polluted, config=protected).composite.set_index("date")["index_value"]
    without = run_index(polluted, config=naive).composite.set_index("date")["index_value"]

    drift_protected = (with_protection - base).abs().max()
    drift_naive = (without - base).abs().max()
    assert drift_naive > drift_protected * 5


def test_variety_and_composite_are_consistent(dates):
    """综合指数的日环比必须等于各品种日环比的加权平均，否则两个口径会打架。"""
    frames = []
    rng = np.random.default_rng(3)
    for name, p0 in (("螺纹钢", 3800.0), ("热轧卷板", 3950.0), ("中厚板", 4050.0)):
        drift = rng.normal(0.001, 0.004, len(dates))
        drift[0] = 0.0
        path = p0 * np.exp(np.cumsum(drift))
        specs = {"螺纹钢": ("Φ18", "Φ20", "Φ22"),
                 "热轧卷板": ("3.0mm", "4.75mm", "5.75mm"),
                 "中厚板": ("14mm", "20mm", "25mm")}[name]
        frames.append(make_listings(dates, path, merchants=8, category=name,
                                    specs=specs))
    out = run_index(pd.concat(frames, ignore_index=True), config=_cfg())

    contrib = out.indices.contribution.groupby("date")["contribution_pct"].sum()
    comp = out.composite.set_index("date")
    recomputed = comp["link"] - 1.0
    aligned = contrib.reindex(comp.index)
    assert (aligned - recomputed).abs().max() < 1e-9

    assert set(out.variety["category"]) == {"rebar", "hrc", "plate"}
    for _, part in out.variety.groupby("category"):
        assert part["index_value"].iloc[0] == pytest.approx(100.0)


def test_variety_indices_track_their_own_category(dates):
    """分品种指数只能反映本品种走势，不能被其他品种带偏。"""
    up = 3800.0 * 1.003 ** np.arange(len(dates))
    down = 3950.0 * 0.997 ** np.arange(len(dates))
    df = pd.concat(
        [
            make_listings(dates, up, merchants=8, category="螺纹钢",
                          specs=("Φ18", "Φ20", "Φ22")),
            make_listings(dates, down, merchants=8, category="热轧卷板",
                          specs=("3.0mm", "4.75mm", "5.75mm")),
        ],
        ignore_index=True,
    )
    out = run_index(df, config=_cfg())
    var = out.variety.set_index(["category", "date"])["index_value"]
    n = len(dates) - 1
    assert var.loc[("rebar", dates[-1])] == pytest.approx(100 * 1.003 ** n, rel=1e-6)
    assert var.loc[("hrc", dates[-1])] == pytest.approx(100 * 0.997 ** n, rel=1e-6)


def test_zombie_quotes_do_not_freeze_index(dates):
    """一半商家挂着不动价时，指数仍应跟上真实在动的那一半。"""
    path = 3800.0 * 1.004 ** np.arange(len(dates))
    moving = make_listings(dates, path, merchants=6)
    frozen = make_listings(dates, np.full(len(dates), 3800.0), merchants=6)
    frozen["商家id"] = "Z" + frozen["商家id"]
    out = run_index(pd.concat([moving, frozen], ignore_index=True), config=_cfg())
    total = out.composite["index_value"].iloc[-1] / 100.0 - 1.0
    truth = 1.004 ** (len(dates) - 1) - 1.0
    # 僵尸报价被降权，指数涨幅应该保留真实涨幅的大部分
    assert total > truth * 0.6


def test_diagnostics_flags_weight_concentration(dates, trend_path):
    df = make_listings(dates, trend_path, merchants=8)
    out = run_index(df, config=_cfg())
    diag = out.diagnostics
    assert not diag.empty
    assert diag["top_merchant_share"].max() <= 0.15 + 1e-9
    assert (diag["weight_ess"] > 1.0).all()


def test_bootstrap_produces_confidence_interval(dates, trend_path):
    df = make_listings(dates[:15], trend_path[:15], merchants=8,
                       offsets=np.linspace(-0.02, 0.02, 8))
    out = run_index(df, config=_cfg(bootstrap_rounds=30))
    comp = out.composite
    assert {"link_lo", "link_hi"} <= set(comp.columns)
    valid = comp.dropna(subset=["link_lo", "link_hi"])
    assert not valid.empty
    assert (valid["link_lo"] <= valid["link_hi"]).all()
