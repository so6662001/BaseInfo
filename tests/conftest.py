import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def dates():
    return pd.bdate_range("2025-01-02", periods=40)


def make_listings(dates, path, merchants=8, category="螺纹钢", specs=("Φ20", "Φ22", "Φ25"),
                  city="上海", stock=1000.0, offsets=None, mill="沙钢"):
    """构造一份干净的挂牌价数据：每个商家每天对每个规格报一次价。

    价格 = 市场路径 × (1 + 商家偏移)，不含噪声，便于精确断言。
    """
    if offsets is None:
        offsets = np.linspace(-0.01, 0.01, merchants)
    rows = []
    for i in range(merchants):
        for spec in specs:
            for t, d in enumerate(dates):
                rows.append(
                    {
                        "日期": d,
                        "商家id": f"S{i:02d}",
                        "城市": city,
                        "品种": category,
                        "规格": spec,
                        "钢厂": mill,
                        "挂牌价": round(float(path[t] * (1 + offsets[i])), 2),
                        "单位": "元/吨",
                        "库存量": stock,
                    }
                )
    return pd.DataFrame(rows)


@pytest.fixture
def trend_path(dates):
    """一条确定的上行价格路径：每天涨 0.2%。"""
    return 3800.0 * (1.002 ** np.arange(len(dates)))


@pytest.fixture
def clean_listings_df(dates, trend_path):
    return make_listings(dates, trend_path)
