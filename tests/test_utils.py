import numpy as np
import pytest

from steel_index.utils import (
    cap_weights,
    kish_ess,
    mad,
    robust_z,
    tukey_biweight_location,
    weighted_geometric_mean,
    weighted_median,
    weighted_quantile,
    weighted_trimmed_mean,
)


def test_weighted_median_respects_weights():
    x = [10.0, 20.0, 30.0]
    assert weighted_median(x) == pytest.approx(20.0)
    # 把权重压在低价一侧，中位数应向低价移动
    assert weighted_median(x, [100.0, 1.0, 1.0]) < 15.0


def test_weighted_quantile_edges():
    x = np.arange(1.0, 101.0)
    assert weighted_quantile(x, 0.5) == pytest.approx(50.5, abs=0.6)
    assert np.isnan(weighted_quantile([], 0.5))


def test_mad_and_robust_z():
    x = np.array([100.0, 101.0, 99.0, 100.0, 100.0, 1000.0])
    assert mad(x) == pytest.approx(0.5, abs=0.6)
    z = robust_z(x)
    # 极端值的稳健 Z 必须远超阈值，普通值必须在阈值内
    assert abs(z[-1]) > 100
    assert np.all(np.abs(z[:-1]) < 3.5)


def test_robust_z_handles_zero_dispersion():
    x = np.array([100.0] * 10)
    assert np.allclose(robust_z(x), 0.0)
    # MAD 为 0 时用相对带宽兜底，才不会把整个单元判成离群
    z = robust_z(np.array([100.0] * 9 + [130.0]), fallback_rel=0.03)
    assert abs(z[-1]) > 3.5


def test_biweight_ignores_extreme_outliers():
    base = [3800.0, 3810.0, 3790.0, 3805.0, 3795.0, 3800.0]
    clean = tukey_biweight_location(base)
    polluted = tukey_biweight_location(base + [38000.0, 380.0])
    assert clean == pytest.approx(3800.0, abs=5.0)
    assert polluted == pytest.approx(clean, abs=5.0)


def test_trimmed_mean_drops_tails():
    x = list(range(1, 101))
    assert weighted_trimmed_mean(x, trim=0.1) == pytest.approx(50.5, abs=1.5)


def test_weighted_geometric_mean():
    assert weighted_geometric_mean([1.0, 4.0]) == pytest.approx(2.0)
    assert weighted_geometric_mean([1.0, 4.0], [3.0, 1.0]) == pytest.approx(4.0 ** 0.25)


def test_kish_ess_detects_concentration():
    assert kish_ess([1.0] * 10) == pytest.approx(10.0)
    # 一家独占九成权重时，有效样本量应塌缩到接近 1
    assert kish_ess([90.0] + [1.0] * 10) < 1.5


def test_cap_weights_enforces_ceiling():
    w = np.array([1000.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0])
    capped = cap_weights(w, 0.10)
    share = capped / capped.sum()
    assert share.max() <= 0.10 + 1e-9
    assert capped.sum() == pytest.approx(w.sum())


def test_cap_weights_degrades_to_equal_when_infeasible():
    # 3 个样本无法都满足 10% 上限，只能退化为等权
    capped = cap_weights(np.array([5.0, 1.0, 1.0]), 0.10)
    assert np.allclose(capped, capped[0])
