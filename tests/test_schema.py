import numpy as np
import pandas as pd
import pytest

from steel_index.schema import (
    COL_CATEGORY,
    COL_DATE,
    COL_MERCHANT,
    COL_PRICE,
    COL_SPEC_GROUP,
    extract_size,
    normalize_category,
    normalize_columns,
    prepare_frame,
    spec_group_of,
    unit_factor,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("螺纹钢", "rebar"),
        ("螺纹", "rebar"),
        ("HRB400E", "rebar"),
        ("热轧卷板", "hrc"),
        ("热卷", "hrc"),
        ("冷轧", "crc"),
        ("中厚板", "plate"),
        ("H型钢", "section"),
        ("镀锌卷", "gi"),
        ("无缝钢管", "seamless_pipe"),
        (" 高线 ", "wire_rod"),
        ("热轧带钢", "strip"),
    ],
)
def test_normalize_category(raw, expected):
    assert normalize_category(raw) == expected


@pytest.mark.parametrize("raw", ["钢材A", "未知品种", "", None, np.nan])
def test_normalize_category_rejects_unknown(raw):
    assert normalize_category(raw) is None


def test_normalize_columns_handles_chinese_aliases():
    df = pd.DataFrame({"报价日期": [], "店铺ID": [], "钢材品种": [], "挂牌价格": [],
                       "可售库存": []})
    out = normalize_columns(df)
    assert {COL_DATE, COL_MERCHANT, COL_CATEGORY, COL_PRICE, "stock_qty"} <= set(out.columns)


@pytest.mark.parametrize(
    "spec,size",
    [("Φ20", 20.0), ("φ25mm", 25.0), ("5.75mm", 5.75), ("200*200", 200.0),
     ("HRB400 16", 400.0), ("电议", float("nan"))],
)
def test_extract_size(spec, size):
    got = extract_size(spec)
    if np.isnan(size):
        assert np.isnan(got)
    else:
        assert got == pytest.approx(size)


def test_spec_group_bins():
    assert spec_group_of("rebar", 20.0) == "18-22"
    assert spec_group_of("rebar", 8.0) == "0-10"
    assert spec_group_of("hrc", 5.75) == "3-5.75"
    assert spec_group_of("rebar", float("nan")) == "ALL"
    assert spec_group_of("unknown_cat", 20.0) == "ALL"


@pytest.mark.parametrize(
    "unit,factor",
    [("元/吨", 1.0), ("元/公斤", 1000.0), ("元/kg", 1000.0), ("万元/吨", 10000.0)],
)
def test_unit_factor(unit, factor):
    assert unit_factor(unit) == pytest.approx(factor)


def test_unit_factor_unknown_is_nan():
    assert np.isnan(unit_factor("包/件"))


def test_prepare_frame_derives_keys(clean_listings_df):
    out = prepare_frame(clean_listings_df)
    assert out[COL_CATEGORY].iloc[0] == "rebar"
    assert out[COL_SPEC_GROUP].iloc[0] in {"18-22", "22-28"}
    assert out["region"].iloc[0] == "华东"
    assert out["sku_id"].str.contains("|", regex=False).all()
    assert out["cell_id"].nunique() >= 1


def test_prepare_frame_requires_core_columns():
    with pytest.raises(ValueError, match="缺少必需列"):
        prepare_frame(pd.DataFrame({"日期": [], "商家id": []}))
