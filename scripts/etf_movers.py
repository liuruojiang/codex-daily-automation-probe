from __future__ import annotations

import json
import math
import re
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo


MIN_PRICE = 5.0
MIN_AVG_DAILY_VOLUME = 50_000
MIN_AVG_DAILY_DOLLAR_VOLUME = 5_000_000.0
MAX_UNIVERSE_SCAN = 6_000
MAX_CHART_WORKERS = 16

QUERY_URL = "https://query1.finance.yahoo.com/v1/finance/screener"
QUERY_PARAMS = {
    "corsDomain": "finance.yahoo.com",
    "formatted": "false",
    "lang": "en-US",
    "region": "US",
}


LEVERAGED_OR_INVERSE_PATTERNS = (
    r"\b\d+(?:\.\d+)?x\b",
    r"\b-\s?1x\b",
    r"\bultra(?:pro|short)?\b",
    r"\binverse\b",
    r"\bshort\b(?![- ]?(?:term|duration|maturity|treasury|bond))",
    r"\bbull\b",
    r"\bbear\b",
    r"\bleverag(?:e|ed|es)\b",
    r"\bdaily\b.*\b(?:bull|bear|long|short)\b",
    r"\b(?:bull|bear)\b.*\b[23]x\b",
    r"\bmicrosectors\b",
)

OPTION_INCOME_PATTERNS = (
    r"\byieldmax\b",
    r"\bweeklypay\b",
    r"\bincomemax\b",
    r"\boption income\b",
    r"\bcrypto income\b",
    r"\bincome strategy\b",
    r"\bgrowth\s*(?:&|and)\s*income\b",
    r"\byield\s*boost\b",
    r"\bpremium income\b",
    r"\benhanced income\b",
    r"\bcovered call\b",
    r"\bbuy[- ]?write\b",
    r"\bput[- ]?write\b",
    r"\bcall writing\b",
    r"\boption strategy\b",
    r"\btarget income\b",
    r"\bdefined outcome\b",
    r"\bbuffer(?:ed)?\b",
    r"\bkurv\b",
)

SINGLE_CRYPTO_PATTERNS = (
    r"\bbitcoin\b",
    r"\bether(?:eum)?\b",
    r"\bsolana\b",
    r"\bchainlink\b",
    r"\bzcash\b",
    r"\bbittensor\b",
    r"\bxrp\b",
    r"\bdoge(?:coin)?\b",
    r"\bhyperliquid\b",
    r"\bsui\b",
    r"\bcanton network\b",
    r"\bstaking\b",
)

THEME_PATTERNS = (
    ("volatility_futures", r"\b(?:vix|volatility)\b"),
    ("managed_futures", r"\b(?:managed futures?|trend following|trend strategy)\b"),
    ("broad_commodities", r"\b(?:diversified commodity|broad commodity|commodity index|commodity strategy|optimum yield)\b"),
    ("crude_oil_futures", r"\b(?:crude oil|brent oil|oil fund|oil futures?)\b"),
    ("natural_gas_futures", r"\b(?:natural gas|gasoline)\b"),
    ("agriculture_futures", r"\b(?:agriculture|corn|wheat|soybean|sugar|coffee)\b"),
    ("gold_miners", r"\bgold\b.*\b(?:miners?|mining)\b"),
    ("silver_miners", r"\bsilver\b.*\b(?:miners?|mining)\b"),
    ("copper_miners", r"\bcopper\b.*\b(?:miners?|mining)\b"),
    ("critical_materials", r"\b(?:rare earth|strategic metals?|critical materials?|critical minerals?|metals\s*(?:&|and)\s*mining)\b"),
    ("natural_resources", r"\b(?:natural resources?|upstream resources?)\b"),
    ("gold_futures", r"\bgold\b"),
    ("silver_futures", r"\bsilver\b"),
    ("copper_futures", r"\bcopper\b"),
    ("uranium_nuclear", r"\b(?:uranium|nuclear)\b"),
    ("shipping", r"\b(?:shipping|tanker|freight)\b"),
    ("airlines", r"\b(?:airlines?|jets? etf)\b"),
    ("semiconductors", r"\b(?:semiconductors?|chips?)\b"),
    ("photonics", r"\b(?:photonics?|optical technology|laser technology)\b"),
    ("cybersecurity", r"\b(?:cybersecurity|cyber security)\b"),
    ("software_cloud", r"\b(?:software|cloud computing|saas|neocloud)\b"),
    ("ai_robotics", r"\b(?:ai|artificial intelligence|robotics|humanoid|automation)\b"),
    ("biotech_genomics", r"\b(?:biotech|biotechnology|genomic)\b"),
    ("healthcare", r"\b(?:health care|healthcare|medical|pharmaceuticals?|pharma)\b"),
    ("financials", r"\b(?:financial|bank|insurance|broker)\b"),
    ("real_estate", r"\b(?:real estate|reit)\b"),
    ("consumer_discretionary", r"\b(?:consumer discretionary|retail)\b"),
    ("consumer_staples", r"\b(?:consumer staples|staples)\b"),
    ("defense_aerospace", r"\b(?:aerospace|defense|drone|warfare)\b"),
    ("industrials", r"\bindustrials?\b"),
    ("materials", r"\b(?:materials|metals\s*(?:&|and)\s*mining)\b"),
    ("utilities", r"\butilit(?:y|ies)\b"),
    ("energy_infrastructure", r"\b(?:energy infrastructure|mlp.*energy|midstream|pipeline)\b"),
    ("power_infrastructure", r"\b(?:power infrastructure|electrification|electric grid|grid infrastructure)\b"),
    ("broad_infrastructure", r"\b(?:u\.s\. infrastructure|global infrastructure|infrastructure index)\b"),
    ("energy_equity", r"\b(?:energy equity|energy sector|exploration)\b"),
    ("clean_energy", r"\b(?:clean energy|solar|wind|hydrogen|battery)\b"),
    ("communications", r"\bcommunication services?\b"),
    ("technology", r"\btechnology\b"),
    ("innovation_growth", r"\b(?:innovation|disruptive|spear alpha)\b"),
    ("active_concentrated_equity", r"\bfocus etf\b"),
    ("free_cash_flow_factor", r"\bfree cash flow\b"),
    ("inflation_beneficiaries", r"\binflation beneficiaries?\b"),
    ("space_economy", r"\b(?:space innovators?|space economy|space exploration)\b"),
    ("china_equity", r"\b(?:china|chinese|csi 300)\b"),
    ("japan_equity", r"\b(?:japan|nikkei)\b"),
    ("india_equity", r"\b(?:india|nifty)\b"),
    ("korea_equity", r"\b(?:south korea|korea)\b"),
    ("taiwan_equity", r"\btaiwan\b"),
    ("brazil_equity", r"\bbrazil\b"),
    ("germany_equity", r"\bgermany\b"),
    ("uk_equity", r"\b(?:united kingdom|uk equity)\b"),
    ("vietnam_equity", r"\bvietnam\b"),
    ("peru_equity", r"\bperu\b"),
    ("south_africa_equity", r"\bsouth africa\b"),
    ("mexico_equity", r"\bmexico\b"),
    ("canada_equity", r"\bcanada\b"),
    ("australia_equity", r"\baustralia\b"),
    ("latin_america_equity", r"\blatin america\b"),
    ("emerging_markets", r"\bemerging markets?\b"),
    ("developed_ex_us", r"\b(?:developed markets?|eafe)\b"),
    ("small_cap_equity", r"\b(?:small cap|small-cap|russell 2000)\b"),
    ("mid_cap_equity", r"\b(?:mid cap|mid-cap)\b"),
    ("large_cap_growth", r"\b(?:large cap growth|large-cap growth|nasdaq[- ]?100)\b"),
    ("large_cap_value", r"\b(?:large cap value|large-cap value)\b"),
    ("large_cap_equity", r"\b(?:s&p\s*500|sp 500|large cap|large-cap)\b"),
    ("momentum_factor", r"\bmomentum\b"),
    ("quality_factor", r"\bquality\b"),
    ("value_factor", r"\bvalue factor\b"),
    ("low_vol_factor", r"\b(?:low|min(?:imum)?) volatility\b"),
    ("dividend_equity", r"\bdividend\b"),
    ("treasury_long", r"\b(?:20\+ year|long[- ]term treasury|long treasury)\b"),
    ("treasury_intermediate", r"\b(?:7[- ]10 year|intermediate treasury)\b"),
    ("treasury_short", r"\b(?:short[- ]term treasury|1[- ]3 year|treasury bill|t[- ]bill)\b"),
    ("tips", r"\b(?:tips|inflation[- ]protected)\b"),
    ("investment_grade_credit", r"\b(?:investment grade|corporate bond)\b"),
    ("high_yield_credit", r"\b(?:high yield|junk bond)\b"),
    ("municipal_bonds", r"\b(?:municipal|muni)\b"),
    ("emerging_market_bonds", r"\bemerging market.*bond\b"),
    ("preferreds", r"\bpreferred\b"),
    ("us_dollar_futures", r"\b(?:us dollar|u\.s\. dollar|dollar index)\b"),
    ("currency_futures", r"\b(?:currency|euro|yen|swiss franc|australian dollar)\b"),
    ("crypto_index", r"\b(?:crypto|digital asset|blockchain|digital transformation)\b"),
)

ISSUER_WORDS = {
    "advisorshares", "amplify", "ark", "bitwise", "canary", "capital", "direxion", "dimensional",
    "etf", "first", "fidelity", "franklin", "fund", "global", "goldman", "graniteshares", "index",
    "invesco", "ishares", "jpmorgan", "kfa", "pacer", "proshares", "rex", "roundhill", "schwab",
    "shares", "spdr", "state", "street", "strategy", "tuttle", "vanguard", "vaneck", "wisdomtree",
}

THEME_INFO: dict[str, tuple[str, str]] = {
    "volatility_futures": ("波动率期货 ETF", "跟踪 VIX 期货，收益同时受波动率变化、期货曲线和展期损益影响。"),
    "managed_futures": ("管理期货趋势 ETF", "通过股指、债券、商品和外汇期货实施多资产趋势策略。"),
    "broad_commodities": ("多商品期货 ETF", "通过能源、金属和农产品期货提供分散的商品敞口。"),
    "crude_oil_futures": ("原油期货 ETF", "跟踪原油期货，除油价外还会受到期货升贴水和展期损益影响。"),
    "natural_gas_futures": ("天然气期货 ETF", "跟踪天然气期货，波动较高且展期损益可能显著。"),
    "agriculture_futures": ("农产品期货 ETF", "通过农产品期货反映供需、天气及期货曲线变化。"),
    "gold_futures": ("黄金期货 ETF", "通过黄金期货提供金价敞口，并承担期货展期影响。"),
    "silver_futures": ("白银期货 ETF", "通过白银期货提供贵金属与工业需求敞口。"),
    "copper_futures": ("铜期货 ETF", "通过铜期货反映工业金属周期、全球制造业需求及展期损益。"),
    "gold_miners": ("黄金矿业股票 ETF", "持有黄金矿企，表现同时受金价、开采成本和股票市场风险影响。"),
    "silver_miners": ("白银矿业股票 ETF", "持有白银矿企，表现受银价、开采成本和矿企盈利影响。"),
    "copper_miners": ("铜矿业股票 ETF", "持有铜矿企业，主要受铜价、资本开支和矿企盈利影响。"),
    "critical_materials": ("关键矿产股票 ETF", "持有稀土、战略金属和关键材料企业，受金属价格、资源政策和矿企盈利影响。"),
    "natural_resources": ("全球自然资源股票 ETF", "持有能源、金属和其他上游资源企业，主要反映资源价格与生产商盈利。"),
    "uranium_nuclear": ("铀矿与核能股票 ETF", "持有铀矿和核能产业链公司，受铀价、核电政策和矿企盈利影响。"),
    "shipping": ("航运与油轮股票 ETF", "持有航运和油轮公司，主要受运价、船队供给和全球贸易流影响。"),
    "airlines": ("航空公司股票 ETF", "持有航空公司，主要受客运需求、燃油成本和运力供给影响。"),
    "semiconductors": ("半导体股票 ETF", "持有芯片设计、制造和设备公司，受半导体周期与科技资本开支影响。"),
    "photonics": ("光子与光通信股票 ETF", "持有光学、激光和光通信公司，受数据中心和通信设备需求影响。"),
    "cybersecurity": ("网络安全股票 ETF", "持有安全软件、身份管理和云安全公司，主要反映企业安全支出。"),
    "software_cloud": ("软件与云计算股票 ETF", "持有软件、云计算和数据中心相关公司，受企业 IT 支出与成长股估值影响。"),
    "ai_robotics": ("人工智能与机器人股票 ETF", "持有人工智能、自动化和机器人产业链公司，主题集中度较高。"),
    "biotech_genomics": ("生物科技与基因组股票 ETF", "持有生物科技和基因组公司，受研发、审批和融资环境影响。"),
    "healthcare": ("医疗保健股票 ETF", "持有制药、医疗设备和医疗服务公司，兼具防御与研发风险。"),
    "financials": ("金融行业股票 ETF", "持有银行、保险和资本市场公司，受利率曲线、信用周期和监管影响。"),
    "real_estate": ("房地产与 REITs ETF", "持有房地产公司和 REITs，主要受利率、融资条件与租金变化影响。"),
    "consumer_discretionary": ("可选消费股票 ETF", "持有零售、汽车和休闲消费公司，受就业、收入和消费周期影响。"),
    "consumer_staples": ("必需消费股票 ETF", "持有食品、日用品等防御型消费公司。"),
    "defense_aerospace": ("航空航天与防务股票 ETF", "持有军工、航空航天和无人系统公司，受国防预算与订单周期影响。"),
    "industrials": ("工业行业股票 ETF", "持有资本品、制造和运输设备公司，主要受经济与资本开支周期影响。"),
    "materials": ("基础材料行业股票 ETF", "持有化工、建材和材料公司，受商品价格及工业需求影响。"),
    "utilities": ("公用事业股票 ETF", "持有电力、燃气和水务公司，偏防御但对利率较敏感。"),
    "energy_infrastructure": ("能源基础设施股票 ETF", "持有管道、中游和 MLP 公司，主要受能源运输量和分配现金流影响。"),
    "power_infrastructure": ("电力与电气化基础设施 ETF", "持有电网、电力设备和电气化公司，受电力需求与基础设施投资影响。"),
    "broad_infrastructure": ("综合基础设施股票 ETF", "持有交通、公用事业和基础设施运营商，受资本开支和利率影响。"),
    "energy_equity": ("能源行业股票 ETF", "持有油气生产与服务公司，主要受油气价格和资本开支周期影响。"),
    "clean_energy": ("清洁能源股票 ETF", "持有太阳能、风能、储能等公司，受政策、利率和产业供需影响。"),
    "communications": ("通信服务股票 ETF", "持有互联网平台、媒体和通信服务公司。"),
    "technology": ("科技行业股票 ETF", "持有广泛科技公司，受科技盈利、资本开支和成长股估值影响。"),
    "innovation_growth": ("创新成长主题股票 ETF", "集中持有颠覆式创新和高成长公司，波动及估值敏感度较高。"),
    "active_concentrated_equity": ("主动精选股票 ETF", "由基金经理主动选择并集中持有高确信度股票，需关注持仓集中度和经理风险。"),
    "free_cash_flow_factor": ("自由现金流因子 ETF", "偏向自由现金流较强的公司，属于基本面质量与估值筛选策略。"),
    "inflation_beneficiaries": ("通胀受益股票 ETF", "持有能从通胀或实物资产价格上升中受益的公司，常偏向资源与定价权行业。"),
    "space_economy": ("太空产业股票 ETF", "持有卫星、航天和太空基础设施公司，主题集中度较高。"),
    "china_equity": ("中国股票 ETF", "跟踪中国股票市场，受国内增长、政策和人民币变化影响。"),
    "japan_equity": ("日本股票 ETF", "跟踪日本股票市场，受企业盈利、日元和政策变化影响。"),
    "india_equity": ("印度股票 ETF", "跟踪印度股票市场，受经济增长、估值和卢比变化影响。"),
    "korea_equity": ("韩国股票 ETF", "跟踪韩国股票市场，科技与出口企业权重较高。"),
    "taiwan_equity": ("台湾股票 ETF", "跟踪台湾股票市场，半导体权重通常较高。"),
    "brazil_equity": ("巴西股票 ETF", "跟踪巴西股票市场，受商品、利率和巴西雷亚尔影响。"),
    "germany_equity": ("德国股票 ETF", "跟踪德国股票市场，工业与出口企业权重较高。"),
    "uk_equity": ("英国股票 ETF", "跟踪英国股票市场，金融、能源和必需消费权重较高。"),
    "vietnam_equity": ("越南股票 ETF", "跟踪越南股票市场，受制造业、金融和外资流动影响。"),
    "peru_equity": ("秘鲁股票 ETF", "跟踪秘鲁股票市场，矿业和资源公司权重通常较高。"),
    "south_africa_equity": ("南非股票 ETF", "跟踪南非股票市场，受资源、金融和兰特汇率影响。"),
    "mexico_equity": ("墨西哥股票 ETF", "跟踪墨西哥股票市场，受美国周期、制造业和比索影响。"),
    "canada_equity": ("加拿大股票 ETF", "跟踪加拿大股票市场，金融和资源行业权重较高。"),
    "australia_equity": ("澳大利亚股票 ETF", "跟踪澳大利亚股票市场，金融和资源行业权重较高。"),
    "latin_america_equity": ("拉丁美洲股票 ETF", "覆盖拉丁美洲主要股票市场，资源、金融和当地货币影响较大。"),
    "emerging_markets": ("新兴市场股票 ETF", "覆盖多个新兴市场国家，受全球增长、美元和资本流动影响。"),
    "developed_ex_us": ("美国以外发达市场 ETF", "覆盖欧洲、日本等发达市场股票。"),
    "small_cap_equity": ("美国小盘股票 ETF", "持有美国小盘公司，对经济和融资环境较敏感。"),
    "mid_cap_equity": ("美国中盘股票 ETF", "持有美国中盘公司，风险特征介于大盘与小盘之间。"),
    "large_cap_growth": ("美国大盘成长 ETF", "偏向大型成长和科技公司，对利率及估值变化较敏感。"),
    "large_cap_value": ("美国大盘价值 ETF", "偏向估值较低的成熟行业公司。"),
    "large_cap_equity": ("美国大盘股票 ETF", "跟踪美国大盘股市场。"),
    "momentum_factor": ("动量因子 ETF", "偏向近期相对强势股票，需关注趋势反转风险。"),
    "quality_factor": ("质量因子 ETF", "偏向盈利稳定、资产负债表较强的公司。"),
    "value_factor": ("价值因子 ETF", "偏向估值较低的公司，受价值风格周期影响。"),
    "low_vol_factor": ("低波动因子 ETF", "偏向历史波动较低的股票，通常防御性较强。"),
    "dividend_equity": ("股息股票 ETF", "偏向高股息公司，需同时观察分红质量和行业集中度。"),
    "treasury_long": ("美国长期国债 ETF", "持有长期美国国债，对利率变化和久期风险较敏感。"),
    "treasury_intermediate": ("美国中期国债 ETF", "持有中期美国国债，久期风险低于长期国债。"),
    "treasury_short": ("美国短期国债 ETF", "持有短期美国国债或国库券，利率风险较低。"),
    "tips": ("美国通胀保值债券 ETF", "持有 TIPS，收益受实际利率和通胀预期共同影响。"),
    "investment_grade_credit": ("投资级公司债 ETF", "持有投资级公司债，同时承担利率与信用利差风险。"),
    "high_yield_credit": ("高收益公司债 ETF", "持有高收益债，对信用周期和风险偏好较敏感。"),
    "municipal_bonds": ("美国市政债 ETF", "持有美国市政债，主要受利率和地方信用影响。"),
    "emerging_market_bonds": ("新兴市场债券 ETF", "持有新兴市场债券，受美元、利率和主权信用影响。"),
    "preferreds": ("优先股 ETF", "持有优先股，兼具利率、信用和金融行业风险。"),
    "us_dollar_futures": ("美元指数期货 ETF", "通过美元指数期货提供美元相对一篮子货币的敞口。"),
    "currency_futures": ("外汇期货 ETF", "通过货币期货反映相关汇率变化及展期影响。"),
    "crypto_index": ("加密产业股票 ETF", "持有区块链、交易平台和加密基础设施公司，不是单一加密资产现货产品。"),
}

MINING_RESOURCE_THEMES = {
    "gold_miners",
    "silver_miners",
    "copper_miners",
    "critical_materials",
    "natural_resources",
    "uranium_nuclear",
}


@dataclass(frozen=True)
class RankingResult:
    universe_count: int
    eligible_count: int
    excluded_counts: dict[str, int]
    gainers: list[dict[str, Any]]
    losers: list[dict[str, Any]]


def _name(row: dict[str, Any]) -> str:
    return str(row.get("longName") or row.get("shortName") or row.get("symbol") or "")


def _text(row: dict[str, Any]) -> str:
    return f"{row.get('symbol') or ''} {_name(row)}".lower()


def _matches(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text, flags=re.I) for pattern in patterns)


def exclusion_reason(row: dict[str, Any]) -> str | None:
    text = _text(row)
    if _matches(text, LEVERAGED_OR_INVERSE_PATTERNS):
        return "leveraged_or_inverse"
    if _matches(text, OPTION_INCOME_PATTERNS):
        return "option_income_or_defined_outcome"
    if re.search(r"\b(?:single[- ]stock|single stock|daily target)\b", text):
        return "single_stock"
    if re.search(r"\b(?:etn|exchange traded note|exchange-traded note)\b", text):
        return "etn"
    if _matches(text, SINGLE_CRYPTO_PATTERNS):
        return "single_crypto"
    if re.search(r"\b(?:physical|bullion|spot)\b", text):
        return "physical_or_spot_trust"
    if re.search(r"\b(?:gold|silver)\b.*\b(?:trust|shares|minishares)\b", text):
        return "physical_or_spot_trust"
    return None


def average_daily_volume(row: dict[str, Any]) -> int:
    value = row.get("averageDailyVolume3Month") or row.get("averageDailyVolume10Day") or row.get("regularMarketVolume") or 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def average_daily_dollar_volume(row: dict[str, Any]) -> float:
    try:
        price = float(row.get("regularMarketPrice") or 0)
    except (TypeError, ValueError):
        price = 0.0
    return price * average_daily_volume(row)


def is_liquid(row: dict[str, Any]) -> bool:
    return (
        average_daily_volume(row) >= MIN_AVG_DAILY_VOLUME
        and average_daily_dollar_volume(row) >= MIN_AVG_DAILY_DOLLAR_VOLUME
    )


def theme_key(row: dict[str, Any]) -> str:
    text = _text(row)
    for key, pattern in THEME_PATTERNS:
        if re.search(pattern, text, flags=re.I):
            return key
    tokens = [token for token in re.findall(r"[a-z0-9]+", _name(row).lower()) if token not in ISSUER_WORDS]
    return "name_" + "_".join(tokens[:4]) if tokens else "symbol_" + str(row.get("symbol") or "").lower()


def dedupe_family(row: dict[str, Any], key: str) -> str:
    if key in MINING_RESOURCE_THEMES:
        return "mining_and_critical_materials_equity"
    if key in {"power_infrastructure", "broad_infrastructure"}:
        return "infrastructure_equity"
    return key


def display_name(key: str) -> str:
    return THEME_INFO.get(key, ("特色主题股票 ETF", ""))[0]


def description(row: dict[str, Any], key: str) -> str:
    if key in THEME_INFO:
        return THEME_INFO[key][1]
    return "聚焦基金名称所示的细分股票主题，主要风险来自主题集中度和成分股波动。"


def _screener_body(offset: int, size: int, min_price: float) -> dict[str, Any]:
    return {
        "offset": offset,
        "size": size,
        "sortType": "DESC",
        "sortField": "percentchange",
        "quoteType": "ETF",
        "query": {
            "operator": "and",
            "operands": [
                {"operator": "gt", "operands": ["intradayprice", min_price]},
                {"operator": "EQ", "operands": ["region", "us"]},
            ],
        },
    }


def _fetch_page(offset: int, size: int, min_price: float) -> list[dict[str, Any]]:
    try:
        from yfinance.data import YfData
    except ImportError as exc:
        raise RuntimeError("yfinance is required for broad ETF rankings") from exc
    response = YfData().post(
        QUERY_URL,
        body=_screener_body(offset, size, min_price),
        params=QUERY_PARAMS,
    )
    response.raise_for_status()
    result = response.json().get("finance", {}).get("result") or []
    return (result[0].get("quotes") or []) if result else []


def fetch_universe(max_scan: int = MAX_UNIVERSE_SCAN, min_price: float = MIN_PRICE) -> list[dict[str, Any]]:
    rows_by_symbol: dict[str, dict[str, Any]] = {}
    page_size = 250
    for offset in range(0, max_scan, page_size):
        page = _fetch_page(offset, min(page_size, max_scan - offset), min_price)
        if not page:
            break
        for row in page:
            symbol = str(row.get("symbol") or "")
            if symbol:
                rows_by_symbol[symbol] = row
    return list(rows_by_symbol.values())


def eligible_universe(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    eligible: list[dict[str, Any]] = []
    excluded: dict[str, int] = {}
    for row in rows:
        reason = exclusion_reason(row)
        if reason:
            excluded[reason] = excluded.get(reason, 0) + 1
            continue
        if not is_liquid(row):
            excluded["insufficient_liquidity"] = excluded.get("insufficient_liquidity", 0) + 1
            continue
        eligible.append(row)
    return eligible, excluded


def _rank(records: list[dict[str, Any]], reverse: bool, limit: int) -> list[dict[str, Any]]:
    picked: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in sorted(records, key=lambda item: float(item["change"]), reverse=reverse):
        if reverse and float(record["change"]) <= 0:
            continue
        if not reverse and float(record["change"]) >= 0:
            continue
        key = str(record["dedupe_family"])
        if key in seen:
            continue
        seen.add(key)
        picked.append(record)
        if len(picked) >= limit:
            break
    return picked


def _record(row: dict[str, Any], date_s: str, change: float) -> dict[str, Any]:
    key = theme_key(row)
    return {
        "symbol": str(row.get("symbol") or ""),
        "name": display_name(key),
        "original_name": _name(row),
        "description": description(row, key),
        "category": key,
        "dedupe_family": dedupe_family(row, key),
        "date": date_s,
        "change": change,
        "average_daily_volume": average_daily_volume(row),
        "average_daily_dollar_volume": average_daily_dollar_volume(row),
    }


def daily_rankings(rows: list[dict[str, Any]], limit: int = 10) -> RankingResult:
    eligible, excluded = eligible_universe(rows)
    ny = ZoneInfo("America/New_York")
    records: list[dict[str, Any]] = []
    for row in eligible:
        value = row.get("regularMarketChangePercent")
        if value is None:
            continue
        timestamp = int(row.get("regularMarketTime") or 0)
        date_s = datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone(ny).date().isoformat() if timestamp else ""
        records.append(_record(row, date_s, float(value)))
    return RankingResult(len(rows), len(eligible), excluded, _rank(records, True, limit), _rank(records, False, limit))


def _chart_rows(symbol: str) -> list[tuple[str, float]]:
    encoded = urllib.parse.quote(symbol)
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?range=3mo&interval=1d&events=div%2Csplits"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                payload = json.load(resp)
            result = payload.get("chart", {}).get("result") or []
            if not result:
                return []
            data = result[0]
            timestamps = data.get("timestamp") or []
            closes = ((data.get("indicators", {}).get("quote") or [{}])[0]).get("close") or []
            values: list[tuple[str, float]] = []
            for ts, close in zip(timestamps, closes):
                if close is None or not math.isfinite(float(close)):
                    continue
                values.append((datetime.fromtimestamp(int(ts), tz=timezone.utc).date().isoformat(), float(close)))
            return values
        except Exception as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(0.25)
    if last_error:
        raise last_error
    return []


def period_rankings(rows: list[dict[str, Any]], limit: int = 10) -> dict[str, Any]:
    eligible, excluded = eligible_universe(rows)
    chart_by_symbol: dict[str, list[tuple[str, float]]] = {}
    chart_errors = 0
    with ThreadPoolExecutor(max_workers=MAX_CHART_WORKERS) as pool:
        futures = {pool.submit(_chart_rows, str(row.get("symbol") or "")): str(row.get("symbol") or "") for row in eligible}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                chart_by_symbol[symbol] = future.result()
            except Exception:
                chart_errors += 1

    result: dict[str, Any] = {
        "universe_count": len(rows),
        "eligible_count": len(eligible),
        "excluded_counts": excluded,
        "chart_errors": chart_errors,
    }
    for label, bars_back in (("one_week", 5), ("one_month", 21)):
        records: list[dict[str, Any]] = []
        for row in eligible:
            chart = chart_by_symbol.get(str(row.get("symbol") or ""), [])
            if len(chart) <= bars_back:
                continue
            start_date, start_close = chart[-1 - bars_back]
            end_date, end_close = chart[-1]
            if start_close <= 0:
                continue
            records.append(_record(row, end_date, (end_close / start_close - 1.0) * 100.0))
        result[label] = {
            "gainers": _rank(records, True, limit),
            "losers": _rank(records, False, limit),
        }
    return result
