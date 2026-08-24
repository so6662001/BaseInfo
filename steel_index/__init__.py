"""钢铁行情指数算法库。

提供两类指数：
  1. 钢铁综合行情指数（不分品类）—— compute_indices(...).composite
  2. 分品种行情指数           —— compute_indices(...).variety

典型用法::

    from steel_index import run_index

    out = run_index(listings_df, inventory_df)
    print(out.summary())
    out.composite.to_csv("composite_index.csv", index=False)
    out.variety.to_csv("variety_index.csv", index=False)
    out.cleaning.rejected.to_csv("rejected.csv", index=False)   # 被剔除数据可追溯
"""

from .config import (
    CATEGORY_NAMES,
    CleaningConfig,
    IndexConfig,
    SteelIndexConfig,
    WeightConfig,
)
from .cleaning import RULE_DESCRIPTIONS, CleaningResult, clean_listings
from .credibility import CredibilityPanel, build_credibility_panel, score_merchants
from .diagnostics import credibility_report, format_diagnostics
from .index import IndexResult, compute_indices
from .pipeline import SteelIndexOutput, run_index
from .schema import prepare_frame
from .simulate import generate_market
from .weights import assign_sample_weights

__version__ = "1.0.0"

__all__ = [
    "run_index",
    "SteelIndexOutput",
    "compute_indices",
    "IndexResult",
    "clean_listings",
    "CleaningResult",
    "RULE_DESCRIPTIONS",
    "build_credibility_panel",
    "score_merchants",
    "CredibilityPanel",
    "assign_sample_weights",
    "prepare_frame",
    "generate_market",
    "credibility_report",
    "format_diagnostics",
    "SteelIndexConfig",
    "CleaningConfig",
    "WeightConfig",
    "IndexConfig",
    "CATEGORY_NAMES",
    "__version__",
]
