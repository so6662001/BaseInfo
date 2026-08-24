"""钢铁行情指数的全局配置：品种字典、合法区间、清洗阈值、权重与指数参数。

所有阈值都集中在此处，方便研究员按市场状态（例如极端行情期放宽日波动上限）调参，
而不需要改动算法代码。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Dict, Tuple

# --------------------------------------------------------------------------------------
# 品种字典
# --------------------------------------------------------------------------------------
# 内部统一使用英文 code 作为品种主键，展示层使用中文名。
CATEGORY_NAMES: Dict[str, str] = {
    "rebar": "螺纹钢",
    "wire_rod": "高线",
    "coil_rebar": "盘螺",
    "hrc": "热轧卷板",
    "crc": "冷轧卷板",
    "plate": "中厚板",
    "section": "型钢",
    "gi": "镀锌板卷",
    "ppgi": "彩涂板卷",
    "seamless_pipe": "无缝管",
    "welded_pipe": "焊管",
    "strip": "带钢",
}

# 品种别名 -> 标准 code。键在匹配前会被去空格并转小写。
CATEGORY_ALIASES: Dict[str, str] = {
    # 螺纹钢
    "螺纹钢": "rebar", "螺纹": "rebar", "螺纹钢筋": "rebar", "抗震螺纹": "rebar",
    "hrb400": "rebar", "hrb400e": "rebar", "hrb500": "rebar", "rebar": "rebar",
    "deformed bar": "rebar", "建筑钢材": "rebar",
    # 高线
    "高线": "wire_rod", "线材": "wire_rod", "盘条": "wire_rod", "普线": "wire_rod",
    "hpb300": "wire_rod", "wire_rod": "wire_rod", "wirerod": "wire_rod",
    # 盘螺
    "盘螺": "coil_rebar", "螺纹盘卷": "coil_rebar", "coil_rebar": "coil_rebar",
    # 热轧
    "热轧": "hrc", "热卷": "hrc", "热轧卷板": "hrc", "热轧卷": "hrc", "热轧板卷": "hrc",
    "hrc": "hrc", "q235b热轧": "hrc",
    # 冷轧
    "冷轧": "crc", "冷卷": "crc", "冷轧卷板": "crc", "冷轧板卷": "crc", "crc": "crc",
    # 中厚板
    "中厚板": "plate", "中板": "plate", "厚板": "plate", "普中板": "plate",
    "plate": "plate", "钢板": "plate",
    # 型钢
    "型钢": "section", "h型钢": "section", "工字钢": "section", "角钢": "section",
    "槽钢": "section", "section": "section", "h beam": "section",
    # 镀锌
    "镀锌": "gi", "镀锌板": "gi", "镀锌卷": "gi", "镀锌板卷": "gi", "热镀锌": "gi", "gi": "gi",
    # 彩涂
    "彩涂": "ppgi", "彩涂板": "ppgi", "彩钢板": "ppgi", "彩涂板卷": "ppgi", "ppgi": "ppgi",
    # 管材
    "无缝管": "seamless_pipe", "无缝钢管": "seamless_pipe", "seamless": "seamless_pipe",
    "seamless_pipe": "seamless_pipe",
    "焊管": "welded_pipe", "镀锌管": "welded_pipe", "方管": "welded_pipe",
    "welded_pipe": "welded_pipe",
    # 带钢
    "带钢": "strip", "热轧带钢": "strip", "窄带": "strip", "strip": "strip",
}

# --------------------------------------------------------------------------------------
# 价格硬边界（元/吨，含税现货挂牌价）
# --------------------------------------------------------------------------------------
# 取值为历史极值外扩后的"物理上限/下限"，用来拦截录错和恶意乱填，
# 不承担识别温和异常的职责（那是横截面稳健离群层的工作）。
PRICE_BOUNDS: Dict[str, Tuple[float, float]] = {
    "rebar": (1800.0, 8000.0),
    "wire_rod": (1800.0, 8000.0),
    "coil_rebar": (1800.0, 8200.0),
    "hrc": (1800.0, 8000.0),
    "crc": (2000.0, 9000.0),
    "plate": (1800.0, 8500.0),
    "section": (1800.0, 8500.0),
    "gi": (2200.0, 10000.0),
    "ppgi": (2500.0, 13000.0),
    "seamless_pipe": (2200.0, 12000.0),
    "welded_pipe": (2000.0, 10000.0),
    "strip": (1800.0, 8500.0),
}
DEFAULT_PRICE_BOUNDS: Tuple[float, float] = (1500.0, 15000.0)

# 品种日内最大合理涨跌幅（超过即视为录错或恶意报价）。
MAX_DAILY_MOVE: Dict[str, float] = {
    "rebar": 0.10,
    "wire_rod": 0.10,
    "coil_rebar": 0.10,
    "hrc": 0.10,
    "crc": 0.09,
    "plate": 0.10,
    "section": 0.09,
    "gi": 0.09,
    "ppgi": 0.08,
    "seamless_pipe": 0.08,
    "welded_pipe": 0.09,
    "strip": 0.11,
}
DEFAULT_MAX_DAILY_MOVE: float = 0.12

# --------------------------------------------------------------------------------------
# 品种权重（综合指数用）
# --------------------------------------------------------------------------------------
# 缺省权重按国内钢材表观消费结构给出；若样本本身覆盖度足够，
# 建议在 IndexConfig 中改用 "market_value" 由库存×价格内生推导。
DEFAULT_CATEGORY_WEIGHTS: Dict[str, float] = {
    "rebar": 0.22,
    "wire_rod": 0.09,
    "coil_rebar": 0.04,
    "hrc": 0.20,
    "crc": 0.09,
    "plate": 0.13,
    "section": 0.06,
    "gi": 0.05,
    "ppgi": 0.02,
    "seamless_pipe": 0.04,
    "welded_pipe": 0.04,
    "strip": 0.02,
}

# 城市 -> 区域，用于样本量不足时的分层回退。
CITY_REGIONS: Dict[str, str] = {
    "上海": "华东", "杭州": "华东", "南京": "华东", "无锡": "华东", "苏州": "华东",
    "合肥": "华东", "济南": "华东", "青岛": "华东", "福州": "华东", "南昌": "华东",
    "宁波": "华东", "常州": "华东", "徐州": "华东", "东营": "华东",
    "北京": "华北", "天津": "华北", "石家庄": "华北", "唐山": "华北", "太原": "华北",
    "邯郸": "华北", "呼和浩特": "华北", "包头": "华北",
    "广州": "华南", "深圳": "华南", "佛山": "华南", "南宁": "华南", "海口": "华南",
    "厦门": "华南", "东莞": "华南",
    "武汉": "华中", "长沙": "华中", "郑州": "华中", "南阳": "华中", "宜昌": "华中",
    "重庆": "西南", "成都": "西南", "昆明": "西南", "贵阳": "西南", "西昌": "西南",
    "西安": "西北", "兰州": "西北", "银川": "西北", "西宁": "西北", "乌鲁木齐": "西北",
    "沈阳": "东北", "大连": "东北", "长春": "东北", "哈尔滨": "东北", "鞍山": "东北",
}
DEFAULT_REGION = "其他"


@dataclass(frozen=True)
class CleaningConfig:
    """数据清洗阈值。"""

    # --- L0 结构 ---
    as_of: str | None = None
    """数据截止日（YYYY-MM-DD）。晚于该日的记录一律视为非法未来日期。
    缺省则用样本日期分布的 P99.5 外扩 30 天推断，避免被少量 2099 年的脏数据抬高上界。"""

    max_history_years: float = 15.0
    """允许的最长回溯年限，早于此的记录视为日期录错。"""

    # --- L1 量纲与硬边界 ---
    enable_unit_repair: bool = True
    unit_repair_factors: Tuple[float, ...] = (1000.0, 0.1, 0.001, 10000.0)
    """尝试的量纲修复倍数：元/公斤→×1000、角/吨→×0.1、元/千吨→×0.001、万元/吨→×10000。"""

    max_stock_per_sku: float = 200_000.0
    """单商家单 SKU 库存上限（吨）。超过视为单位填错或乱填，库存作废但价格保留。"""

    # --- L2 去重 ---
    duplicate_agg: str = "median"
    """同一商家同日同 SKU 多次挂牌时的聚合方式：median / last / min。"""

    max_quotes_per_merchant_day: int = 400
    """单商家单日有效报价条数上限，超过视为机器灌水。"""

    # --- L3 横截面稳健离群 ---
    robust_z_threshold: float = 3.5
    """MAD 稳健 Z 分数阈值（3.5 约等于正态下的 4.7 sigma，行业常用）。"""

    min_cell_size: int = 6
    """计算横截面稳健统计量所需的最小样本数，不足则向上一层级回退。"""

    zero_mad_band: float = 0.03
    """当单元内价格高度一致（MAD=0）时改用的相对带宽。"""

    quantile_trim: float = 0.005
    """横截面分位截尾比例（双侧），用于兜住 MAD 失效的极端形态。"""

    # --- L4 纵向跳变 ---
    spike_reversion_tol: float = 0.02
    """尖峰回归判定：t-1 与 t+1 相对偏差小于该值即认定 t 为孤立毛刺。"""

    stale_days: int = 10
    """真实报价连续多少天完全不变即视为僵尸报价。"""

    stale_market_move: float = 0.02
    """僵尸判定的市场侧条件：同期单元市场累计变动超过该幅度。"""

    stale_weight_factor: float = 0.3
    """僵尸报价的权重折减系数。"""

    carry_forward_days: int = 5
    """挂牌价沿用天数上限；超过未更新则视为退出样本，不再参与匹配。"""

    carried_weight_factor: float = 0.6
    """沿用报价（当日未更新）的权重折减。当日真实更新的报价更能代表市场意愿，
    折减可以让指数对新报价更灵敏，同时保留沿用样本以维持连续性。"""

    # --- L5 商家信誉 ---
    credibility_window: int = 60
    """信誉评分的滚动回溯窗口（自然日）。"""

    credibility_min_days: int = 5
    """信誉评分所需的最少活跃天数，不足则给予中性分。"""

    blacklist_threshold: float = 0.35
    """信誉分低于该值的商家当期不参与指数计算。"""

    max_outlier_rate: float = 0.60
    """历史离群率上限。放得比较宽是有意的：报价被横截面规则剔掉多，可能只是这家
    走激进低价策略，不等于恶意，不该据此拉黑。"""

    max_price_bias: float = 0.15
    """允许的长期价格偏离幅度（相对单元中枢的中位偏离）。
    这是识别恶意压价/抬价的主指标——偏离 15% 以上的挂牌价在钢材现货里没有商业解释。"""

    max_bias: float = 6.0
    """允许的系统性偏离（稳健 Z 中位数绝对值）。Z 口径受单元离散度影响大，
    只作辅助指标，阈值放宽。"""

    max_volatility_ratio: float = 5.0
    """自身波动 / 市场波动上限，超过视为乱报价。"""

    clone_detection: bool = True
    clone_min_overlap: int = 10
    """克隆小号检测所需的最小共同报价天数。"""

    clone_identical_ratio: float = 0.98
    """两商家报价完全相同的比例超过该值即判定为同源账号。"""


@dataclass(frozen=True)
class WeightConfig:
    """样本权重配置。"""

    stock_transform: str = "sqrt"
    """库存到权重的变换：raw / sqrt / log / equal。sqrt 可抑制超大库存商家主导指数。"""

    missing_stock_fill: str = "cell_median_half"
    """库存缺失时的填充策略：cell_median_half / cell_median / one。"""

    max_merchant_share: float = 0.10
    """单商家在任一单元内的权重上限，迭代再分配实现，防止刷量操纵。"""

    use_credibility: bool = True
    """是否把信誉分乘进权重。"""

    min_effective_sample: float = 1.0
    """单元有效样本量（Kish ESS）下限，低于该值的单元 link 由同品种均值插补。"""


@dataclass(frozen=True)
class IndexConfig:
    """指数计算配置。"""

    base_value: float = 100.0
    base_date: str | None = None
    """基期日期（YYYY-MM-DD）。缺省取样本首个有效日期。"""

    cell_keys: Tuple[str, ...] = ("category", "spec_group", "city")
    """价格单元的定义维度。"""

    min_skus_per_cell: float = 3.0
    """价格单元的日均 SKU 数下限。低于该值的单元会自动向上合并（规格档→城市→区域→全国），
    避免大量单元因样本过薄而只能靠插补，从而把噪声当成行情。"""

    cell_link_agg: str = "geometric"
    """单元内 SKU 环比的聚合方式：geometric（Jevons，推荐）/ arithmetic。"""

    link_mode: str = "imputed"
    """环比计算方式。
    imputed（默认）: 单元相对插补——当日未更新的挂牌价按同单元平均变动推算，
                     再与下次真实报价比较。累计无偏，且只用当期信息，可当日发布。
    strict         : 只用连续两天都有真实报价的样本。实现更简单、更保守，
                     但会丢弃报价不勤商家的信息，在报价稀疏的数据上样本量偏小。"""

    category_weight_mode: str = "hybrid"
    """品种权重来源：fixed（外部消费结构）/ market_value（库存×价格内生）/ hybrid（几何折中）。"""

    weight_refresh: str = "M"
    """权重刷新频率（pandas 频率别名）；权重更新点之间用链式相乘保证指数连续。"""

    include_stale_in_link: bool = True
    """僵尸报价是否参与环比计算。
    True （默认）: 参与但已降权。长期不动价意味着这家不认同市场调价，
                   这个信息本身是有意义的，指数应当如实反映。
    False        : 完全不参与环比，只计入价格水平与在售规模。
                   适合样本里挂着大量长期不维护报价、指数被拖钝的场景。"""

    max_link_move: float = 0.08
    """单期指数 link 的容忍上限，用于兜底防止清洗漏网数据造成指数跳变。"""

    smooth_alpha: float = 0.5
    """输出平滑指数所用的 EWMA 系数。"""

    bootstrap_rounds: int = 0
    """Bootstrap 置信区间抽样次数，0 表示关闭（默认关闭以保证速度）。"""

    bootstrap_seed: int = 20260824

    low_confidence_min_cells: int = 2
    """当期参与品种指数的单元数低于该值即标记低置信。"""


@dataclass(frozen=True)
class SteelIndexConfig:
    """顶层配置聚合。"""

    cleaning: CleaningConfig = field(default_factory=CleaningConfig)
    weight: WeightConfig = field(default_factory=WeightConfig)
    index: IndexConfig = field(default_factory=IndexConfig)
    category_weights: Dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_CATEGORY_WEIGHTS)
    )

    def with_overrides(self, **kwargs) -> "SteelIndexConfig":
        """返回替换了部分字段的新配置（配置对象不可变，便于复现历史指数）。"""
        return replace(self, **kwargs)


def price_bounds(category: str) -> Tuple[float, float]:
    return PRICE_BOUNDS.get(category, DEFAULT_PRICE_BOUNDS)


def max_daily_move(category: str) -> float:
    return MAX_DAILY_MOVE.get(category, DEFAULT_MAX_DAILY_MOVE)


def region_of(city: str) -> str:
    return CITY_REGIONS.get(city, DEFAULT_REGION)
