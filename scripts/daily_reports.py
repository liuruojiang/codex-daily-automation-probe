from __future__ import annotations

import argparse
import csv
import email.utils
import html
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import etf_movers as broad_etf_movers


BJ = ZoneInfo("Asia/Shanghai")
UA = "Mozilla/5.0 (Codex daily digest; +https://github.com/liuruojiang/codex-daily-automation-probe)"


@dataclass
class Item:
    source: str
    title: str
    url: str
    published: str
    summary: str


@dataclass(frozen=True)
class ResearchFeed:
    source: str
    url: str
    tier: str
    role: str
    default_sections: tuple[str, ...]
    limit: int = 5


@dataclass(frozen=True)
class FixedMonitorFeed:
    source: str
    url: str
    medium: str
    limit: int = 5


@dataclass(frozen=True)
class FixedPageMonitor:
    source: str
    url: str
    href_pattern: str
    medium: str = "页面"
    limit: int = 5


@dataclass(frozen=True)
class ScoredResearchItem:
    item: Item
    score: int
    tier: str
    role: str
    sections: tuple[str, ...]
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class LifeDigestFeed:
    source: str
    url: str
    category: str
    tier: str
    role: str
    limit: int = 5


LIFE_DIGEST_FEEDS: tuple[LifeDigestFeed, ...] = (
    LifeDigestFeed("Morningstar Retirement", "https://www.morningstar.com/retirement/rss", "财务自由", "专业机构", "退休收入、安全提款率、动态提款"),
    LifeDigestFeed("Kitces", "https://www.kitces.com/feed/", "财务自由", "专业机构", "顾问级退休规划、税务和提款策略"),
    LifeDigestFeed("Early Retirement Now", "https://earlyretirementnow.com/feed/", "财务自由", "高质量博客", "安全提款率、长退休期和序列风险"),
    LifeDigestFeed("Portfolio Charts", "https://portfoliocharts.com/feed/", "财务自由", "高质量工具", "全球组合、提款率和历史回撤"),
    LifeDigestFeed("Bogleheads Blog", "https://www.bogleheads.org/blog/feed/", "财务自由", "高质量社区知识库", "低成本投资、提款纪律和配置原则"),
    LifeDigestFeed("Retirement Researcher", "https://retirementresearcher.com/feed/", "财务自由", "专业机构", "退休收入理论、收入地板和年金框架"),
    LifeDigestFeed("Of Dollars And Data", "https://ofdollarsanddata.com/feed/", "财务自由", "高质量博客", "财富、消费和退休数据分析"),
    LifeDigestFeed("Collaborative Fund", "https://feeds.feedburner.com/collabfund", "生活方式", "高质量博客", "财富心理、消费心理和长期生活选择"),
    LifeDigestFeed("HumbleDollar", "https://humbledollar.com/feed/", "生活方式", "高质量博客", "退休后金钱、时间、家庭和意义感"),
    LifeDigestFeed("OECD Tax Residency", "https://www.oecd.org/tax/automatic-exchange/crs-implementation-and-assistance/tax-residency/", "税务居留", "官方", "CRS 辖区税务居民规则入口"),
    LifeDigestFeed("PwC Worldwide Tax Summaries", "https://taxsummaries.pwc.com/", "税务居留", "专业机构", "全球个人税和公司税摘要"),
    LifeDigestFeed("EY Personal Tax Guide", "https://www.ey.com/en_gl/tax-guides/worldwide-personal-tax-and-immigration-guide", "税务居留", "专业机构", "个人税和移民规则初筛"),
    LifeDigestFeed("Nomad Capitalist", "https://nomadcapitalist.com/feed/", "税务居留", "高质量博客", "第二居留、Plan B 和全球生活方式想法"),
    LifeDigestFeed("International Living", "https://internationalliving.com/feed/", "目的地", "高质量博客", "海外退休、目的地生活质量和长期居住经验"),
    LifeDigestFeed("Expatica", "https://www.expatica.com/global/feed/", "目的地", "高质量博客", "海外生活、医疗、住房和搬家指南"),
    LifeDigestFeed("UK FCDO", "https://www.gov.uk/foreign-travel-advice", "签证入境", "官方", "入境、安全、健康和当地法律旅行建议"),
    LifeDigestFeed("Smartraveller", "https://www.smartraveller.gov.au/consular-services/resources", "安全医疗", "官方", "澳洲官方旅行建议和安全提醒"),
    LifeDigestFeed("Canada Travel Advisories", "https://travel.gc.ca/travelling/advisories", "安全医疗", "官方", "加拿大官方目的地安全建议"),
    LifeDigestFeed("CDC Travelers Health", "https://wwwnc.cdc.gov/travel", "安全医疗", "官方", "旅行疫苗、健康和疾病风险"),
    LifeDigestFeed("WHO Travel Advice", "https://www.who.int/travel-advice", "安全医疗", "官方", "国际旅行健康和疫苗建议"),
    LifeDigestFeed("Nomadic Matt", "https://www.nomadicmatt.com/feed/", "生活方式", "高质量博客", "长期旅行规划和路线经验"),
    LifeDigestFeed("Wandering Earl", "https://wanderingearl.com/feed/", "生活方式", "经验源", "长期环球旅行经验"),
    LifeDigestFeed("Legal Nomads", "https://www.legalnomads.com/feed/", "生活方式", "经验源", "长期旅行、食物和健康反思"),
    LifeDigestFeed("Frequent Miler", "https://frequentmiler.com/feed/", "积分", "高质量博客", "里程、转点、酒店和航司机会"),
    LifeDigestFeed("One Mile at a Time", "https://onemileatatime.com/feed/", "积分", "高质量博客", "商务舱、航司、酒店和会员权益"),
    LifeDigestFeed("LoyaltyLobby", "https://loyaltylobby.com/feed/", "积分", "高质量博客", "酒店忠诚计划、促销和条款变化"),
    LifeDigestFeed("The Points Guy", "https://thepointsguy.com/feed/", "积分", "中等可信", "积分估值、信用卡和旅行奖励策略"),
    LifeDigestFeed("r/ExpatFIRE", "https://www.reddit.com/r/ExpatFIRE/hot/.rss", "生活方式", "论坛经验", "跨境 FIRE 和海外生活案例"),
    LifeDigestFeed("r/fatFIRE", "https://www.reddit.com/r/fatFIRE/hot/.rss", "生活方式", "论坛经验", "高净值生活方式和退休案例"),
    LifeDigestFeed("r/digitalnomad", "https://www.reddit.com/r/digitalnomad/hot/.rss", "生活方式", "论坛经验", "数字游民和长期旅居经验"),
)


LIFE_COMMUNITY_FALLBACK_FEEDS: tuple[LifeDigestFeed, ...] = (
    LifeDigestFeed("Reddit r/FATTravel", "https://www.reddit.com/r/FATTravel/hot/.rss", "生活方式", "论坛经验", "高预算慢旅、奢华酒店和家庭旅行案例", 8),
    LifeDigestFeed("Reddit r/chubbytravel", "https://www.reddit.com/r/chubbytravel/hot/.rss", "生活方式", "论坛经验", "微胖预算旅行、舒适路线和酒店选择案例", 8),
    LifeDigestFeed("Reddit r/luxurytravel", "https://www.reddit.com/r/luxurytravel/hot/.rss", "生活方式", "论坛经验", "奢华旅行、度假村、预订渠道和高端体验案例", 8),
)


ASSET_ALLOCATION_SECTION = "资产配置影响"
QUANT_STRATEGY_SECTION = "量化策略影响"
CHINA_HK_SECTION = "A 股 / 港股专项"


ETF_RESEARCH_FEEDS: tuple[ResearchFeed, ...] = (
    ResearchFeed("arXiv q-fin.PM", "https://rss.arxiv.org/rss/q-fin.PM", "核心论文源", "组合优化、资产配置、绩效评估", (QUANT_STRATEGY_SECTION,), 8),
    ResearchFeed("arXiv q-fin.TR", "https://rss.arxiv.org/rss/q-fin.TR", "核心论文源", "交易机制、流动性、执行与自动交易", (QUANT_STRATEGY_SECTION,), 8),
    ResearchFeed("arXiv q-fin.ST", "https://rss.arxiv.org/rss/q-fin.ST", "核心论文源", "统计金融、预测与模型验证", (QUANT_STRATEGY_SECTION,), 8),
    ResearchFeed("arXiv q-fin.RM", "https://rss.arxiv.org/rss/q-fin.RM", "核心论文源", "风险管理、尾部风险与组合约束", (QUANT_STRATEGY_SECTION,), 8),
    ResearchFeed("Quantocracy", "https://quantocracy.com/feed/", "核心策略灵感源", "量化博客聚合与策略线索", (QUANT_STRATEGY_SECTION,), 8),
    ResearchFeed("Quantpedia", "https://quantpedia.com/feed", "核心策略灵感源", "因子、动量、择时与论文复现", (QUANT_STRATEGY_SECTION,), 8),
    ResearchFeed("Alpha Architect", "https://alphaarchitect.com/feed/", "高质量量化研究源", "因子、动量、趋势与行为金融", (ASSET_ALLOCATION_SECTION, QUANT_STRATEGY_SECTION), 6),
    ResearchFeed("Robot Wealth", "https://robotwealth.com/feed/", "实践型量化研究源", "交易成本、no-trade region、数据挖掘风险", (QUANT_STRATEGY_SECTION,), 6),
    ResearchFeed("Meb Faber", "https://mebfaber.com/feed/", "资产配置研究源", "全球资产配置、趋势跟踪与估值", (ASSET_ALLOCATION_SECTION, QUANT_STRATEGY_SECTION), 6),
    ResearchFeed("Allocate Smartly", "https://allocatesmartly.com/feed/", "TAA 框架源", "战术资产配置策略跟踪", (ASSET_ALLOCATION_SECTION, QUANT_STRATEGY_SECTION), 5),
    ResearchFeed("S&P DJI Indexology", "https://www.indexologyblog.com/feed/", "指数与因子研究源", "指数、因子、SPIVA、多资产与 ETF 结构", (ASSET_ALLOCATION_SECTION, QUANT_STRATEGY_SECTION), 6),
    ResearchFeed("FRED Blog", "https://fredblog.stlouisfed.org/feed/", "宏观数据源", "利率、通胀、就业与金融条件数据解释", (ASSET_ALLOCATION_SECTION,), 5),
    ResearchFeed("A Wealth of Common Sense", "https://awealthofcommonsense.com/feed/", "辅助资产配置源", "市场行为、长期配置与投资者行为", (ASSET_ALLOCATION_SECTION,), 5),
    ResearchFeed("ETF Trends", "https://www.etftrends.com/feed/", "辅助 ETF 产品源", "ETF 资金流、产品结构与行业配置", (ASSET_ALLOCATION_SECTION,), 5),
    ResearchFeed("ETF Database", "https://etfdb.com/feed/", "辅助 ETF 产品源", "ETF 产品、资金流与资产类别观察", (ASSET_ALLOCATION_SECTION,), 5),
    ResearchFeed("HKEX News Releases", "https://www.hkex.com.hk/Services/RSS-Feeds/News-Releases?sc_lang=zh-HK", "港交所官方源", "新闻稿、市场结构与互联互通", (CHINA_HK_SECTION,), 6),
    ResearchFeed("HKEX Regulatory Announcements", "https://www.hkex.com.hk/Services/RSS-Feeds/regulatory-announcements?sc_lang=zh-HK", "港交所官方源", "监管通讯与市场规则", (CHINA_HK_SECTION,), 6),
    ResearchFeed("HKEX Market Communications", "https://www.hkex.com.hk/Services/RSS-Feeds/market-communications?sc_lang=zh-HK", "港交所官方源", "市场通讯、交易安排与互联互通", (CHINA_HK_SECTION,), 6),
    ResearchFeed("HKEX Listing Rules", "https://www.hkex.com.hk/Services/RSS-Feeds/Updates-to-Rules-and-Guidance-on-Listing-Matters?sc_lang=zh-HK", "港交所官方源", "上市规则与指引修订", (CHINA_HK_SECTION,), 6),
    ResearchFeed("RSSHub SSE Inquiries", "https://rsshub.app/sse/inquire", "A 股准官方聚合源", "上交所监管问询", (CHINA_HK_SECTION,), 6),
    ResearchFeed("RSSHub SSE Rules", "https://rsshub.app/sse/renewal", "A 股准官方聚合源", "上交所规则与项目动态", (CHINA_HK_SECTION,), 6),
    ResearchFeed("RSSHub SZSE Inquiries", "https://rsshub.app/szse/inquire", "A 股准官方聚合源", "深交所问询函件", (CHINA_HK_SECTION,), 6),
    ResearchFeed("RSSHub SZSE Rules", "https://rsshub.app/szse/rule", "A 股准官方聚合源", "深交所规则变化", (CHINA_HK_SECTION,), 6),
)


ETF_FIXED_MONITOR_FEEDS: tuple[FixedMonitorFeed, ...] = (
    FixedMonitorFeed("Meb Faber", "https://mebfaber.com/feed/", "博客", 5),
    FixedMonitorFeed("The Meb Faber Show", "https://www.youtube.com/feeds/videos.xml?channel_id=UCKvWzzrVUA_DSCoKXL6GU2w", "视频/播客", 5),
    FixedMonitorFeed("A Wealth of Common Sense", "https://awealthofcommonsense.com/feed/", "博客", 5),
    FixedMonitorFeed("The Compound / Animal Spirits", "https://www.youtube.com/feeds/videos.xml?channel_id=UCBRpqrzuuqE8TZcWw75JSdw", "视频/播客", 5),
    FixedMonitorFeed("Portfolio Charts", "https://portfoliocharts.com/feed/", "博客", 5),
    FixedMonitorFeed("Flirting with Models", "https://feeds.captivate.fm/flirting-with-models/", "播客", 5),
    FixedMonitorFeed("Newfound Research", "https://blog.thinknewfound.com/feed/", "博客", 5),
    FixedMonitorFeed("ReSolve Asset Management", "https://investresolve.com/feed/", "博客", 5),
    FixedMonitorFeed("Return Stacked", "https://www.returnstacked.com/feed/", "博客", 5),
    FixedMonitorFeed("Alpha Architect", "https://alphaarchitect.com/feed/", "博客", 5),
    FixedMonitorFeed("Allocate Smartly", "https://allocatesmartly.com/feed/", "博客", 5),
    FixedMonitorFeed("Rational Reminder", "https://www.youtube.com/feeds/videos.xml?channel_id=UCOErWFfNOQzXsgE7f5S_ULw", "视频/播客", 5),
    FixedMonitorFeed("Ben Felix", "https://www.youtube.com/feeds/videos.xml?channel_id=UCDXTQ8nWmx_EhZ2v-kp7QxA", "视频", 5),
    FixedMonitorFeed("Early Retirement Now", "https://earlyretirementnow.com/feed/", "博客", 5),
    FixedMonitorFeed("Of Dollars And Data", "https://ofdollarsanddata.com/feed/", "博客", 5),
    FixedMonitorFeed("Paul Merriman", "https://www.paulmerriman.com/feed/rss2", "博客/播客", 5),
    FixedMonitorFeed("Optimal Momentum", "https://www.optimalmomentum.com/feed/", "博客", 5),
)


ETF_FIXED_PAGE_MONITORS: tuple[FixedPageMonitor, ...] = (
    FixedPageMonitor("AQR Perspectives", "https://www.aqr.com/Insights/Perspectives", r"/Insights/Perspectives/[^\\\"'<>#? ]+", "页面", 5),
    FixedPageMonitor("AQR Research", "https://www.aqr.com/Insights/Research", r"/Insights/Research/(?:Working-Paper|Tax-Aware-Investing|Alternative-Thinking|Journal-Article|White-Papers)/[^\\\"'<>#? ]+", "页面", 5),
    FixedPageMonitor("Research Affiliates", "https://www.researchaffiliates.com/insights/publications", r"/insights/publications/articles/[^\\\"'<>#? ]+", "页面", 5),
)


ETF_CORE_PAGE_MONITORS: tuple[tuple[str, str, str], ...] = (
    ("AQR Insights / Cliff's Perspectives", "https://www.aqr.com/Insights/Perspectives", "因子、动量、价值、另类风险溢价与组合构建"),
    ("AQR Data Library", "https://www.aqr.com/insights/datasets/about-the-aqr-data-library", "因子数据集与月度更新说明"),
    ("Research Affiliates AAI", "https://www.researchaffiliates.com/aai-hub", "估值驱动资本市场预期与 Smart Beta"),
    ("BlackRock Capital Market Assumptions", "https://www.blackrock.com/us/financial-professionals/insights/capital-market-assumptions", "长期资本市场假设、风险预算与相关性"),
    ("Vanguard VCMM Forecasts", "https://corporate.vanguard.com/content/corporatesite/us/en/corp/vemo/vemo-return-forecasts.html", "10 年期收益与波动率预测"),
    ("J.P. Morgan LTCMA", "https://am.jpmorgan.com/us/en/asset-management/adv/insights/portfolio-insights/ltcma/", "10-15 年资本市场假设和战略配置图表"),
    ("GMO Research", "https://www.gmo.com/", "估值敏感型资产配置、国际价值与质量股"),
)

ETF_EXTERNAL_FORUM_FEEDS: tuple[tuple[str, str, int], ...] = (
    ("Bogleheads.org Forum", "https://www.bogleheads.org/forum/feed/topics_active", 36),
    ("Bogleheads.org Forum", "https://www.bogleheads.org/forum/feed/topics", 36),
    ("Rational Reminder Community", "https://community.rationalreminder.ca/latest.rss", 24),
)
BOGLEBLOG_BEST_OF_BOGLEHEADS_URL = "https://bogleblog.com/best-of-bogleheads-forum/"
ETF_FORUM_SUBREDDITS = ("ETFs", "Bogleheads", "investing", "portfolios")
ETF_REDDIT_FORUM_SORTS = ("hot", "top", "new")
ETF_REDDIT_LISTING_LIMIT = 24

ETF_ARTICLE_MAX_AGE_HOURS = 36
ETF_FIXED_MONITOR_DISPLAY_LIMIT = 12
ETF_ARTICLE_BACKFILL_MAX_AGE_HOURS = 14 * 24
ETF_MIN_RESEARCH_ITEMS = 5
ETF_DEDUPE_DAYS = 45
ETF_BACKFILL_DEDUPE_DAYS = 7
ETF_FORUM_BACKFILL_DEDUPE_DAYS = ETF_DEDUPE_DAYS
ETF_MIN_VISIBLE_FORUM_ITEMS = 5
ETF_MIN_FORUM_ITEMS = 8
ETF_FORUM_DISPLAY_LIMIT = 10
ETF_FORUM_MIN_ENGAGEMENT_SCORE = 100
ETF_HISTORY_DAYS = 60


def now_bj() -> datetime:
    return datetime.now(BJ)


def report_date() -> str:
    return now_bj().date().isoformat()


def fetch_bytes(url: str, timeout: int = 30, headers: dict[str, str] | None = None) -> bytes:
    req_headers = {"User-Agent": UA}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def clean_text(text: str | None, max_len: int = 900) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_len:
        return text[: max_len - 1].rstrip() + "..."
    return text


def parse_date(text: str | None) -> datetime | None:
    if not text:
        return None
    text = text.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except Exception:
        try:
            parsed = email.utils.parsedate_to_datetime(text)
        except Exception:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def feed_forum_engagement_text(summary: str | None) -> str:
    text = clean_text(summary, 5000)
    match = re.search(r"Replies\s+([\d,]+).{0,40}?Views\s+([\d,]+)", text, flags=re.I)
    if not match:
        return ""
    return f"comments/replies {match.group(1)}; views {match.group(2)}"


def reddit_hot_rss_ranked_items(items: list[Item]) -> list[Item]:
    out: list[Item] = []
    for rank, item in enumerate(items, start=1):
        if "reddit" not in item.source.lower() or "hot rss rank" in f"{item.source} {item.summary}".lower():
            out.append(item)
            continue
        source = f"{item.source} (hot RSS rank {rank})"
        summary = clean_text(f"{item.summary} Ranking signal: old Reddit hot RSS rank {rank}.", 1200)
        out.append(Item(source, item.title, item.url, item.published, summary))
    return out


def parse_feed(source: str, url: str, limit: int = 12) -> list[Item]:
    try:
        root = ET.fromstring(fetch_bytes(url))
    except Exception:
        return []
    ns = {"atom": "http://www.w3.org/2005/Atom", "content": "http://purl.org/rss/1.0/modules/content/"}
    nodes = root.findall(".//item")
    atom = False
    if not nodes:
        nodes = root.findall(".//atom:entry", ns)
        atom = True
    out: list[Item] = []
    for node in nodes[:limit]:
        if atom:
            title = clean_text(node.findtext("atom:title", default="", namespaces=ns), 180)
            link_node = node.find("atom:link", ns)
            link = link_node.attrib.get("href", "") if link_node is not None else ""
            published = node.findtext("atom:published", default="", namespaces=ns) or node.findtext(
                "atom:updated", default="", namespaces=ns
            )
            summary = node.findtext("atom:summary", default="", namespaces=ns) or node.findtext(
                "atom:content", default="", namespaces=ns
            )
        else:
            title = clean_text(node.findtext("title", default=""), 180)
            link = clean_text(node.findtext("link", default=""), 500)
            published = node.findtext("pubDate", default="") or node.findtext("dc:date", default="")
            summary = node.findtext("description", default="") or node.findtext("content:encoded", default="", namespaces=ns)
        if title and link:
            dt = parse_date(published)
            engagement = feed_forum_engagement_text(summary)
            item_source = source
            item_summary = clean_text(summary, 700)
            if engagement and "comments/replies" not in item_source.lower():
                item_source = f"{item_source} ({engagement})"
                item_summary = clean_text(f"{item_summary} Engagement: {engagement}.", 1200)
            out.append(
                Item(
                    source=item_source,
                    title=title,
                    url=link,
                    published=dt.isoformat() if dt else "",
                    summary=item_summary,
                )
            )
    return out


def reddit_listing_url(subreddit: str, sort: str, limit: int = 12) -> str:
    query = {"limit": str(limit)}
    if sort == "top":
        query["t"] = "week"
    return f"https://api.reddit.com/r/{urllib.parse.quote(subreddit)}/{sort}?{urllib.parse.urlencode(query)}"


def reddit_listing_items_from_payload(payload: object, default_subreddit: str = "") -> list[Item]:
    children = (((payload or {}).get("data") or {}).get("children") or []) if isinstance(payload, dict) else []
    out: list[Item] = []
    for child in children:
        data = (child or {}).get("data") or {}
        if not isinstance(data, dict) or data.get("stickied"):
            continue
        title = clean_text(str(data.get("title") or ""), 220)
        permalink = str(data.get("permalink") or "")
        if not title or not permalink:
            continue
        subreddit = clean_text(str(data.get("subreddit") or default_subreddit), 60)
        score = int(data.get("score") or 0)
        comments = int(data.get("num_comments") or 0)
        created = data.get("created_utc")
        published = ""
        try:
            published = datetime.fromtimestamp(float(created), timezone.utc).isoformat()
        except Exception:
            pass
        summary_bits = [
            clean_text(str(data.get("selftext") or ""), 900),
            f"互动数据：score/upvotes {score}；comments/replies {comments}。",
        ]
        out.append(
            Item(
                source=f"Reddit r/{subreddit}（score/upvotes {score}；comments/replies {comments}）",
                title=title,
                url="https://www.reddit.com" + permalink,
                published=published,
                summary=clean_text(" ".join(bit for bit in summary_bits if bit), 1200),
            )
        )
    out.sort(key=forum_engagement_score, reverse=True)
    return out


def fetch_reddit_listing_items(subreddit: str, sort: str = "hot", limit: int = 12) -> list[Item]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 ETFAllocationDigest/1.0"
        )
    }
    try:
        payload = json.loads(fetch_bytes(reddit_listing_url(subreddit, sort, limit), timeout=20, headers=headers).decode("utf-8", "ignore"))
    except Exception:
        return []
    return reddit_listing_items_from_payload(payload, default_subreddit=subreddit)


def article_paragraphs(url: str, limit: int = 8) -> list[str]:
    try:
        page = fetch_bytes(url, timeout=20).decode("utf-8", "ignore")
    except Exception:
        return []
    raw_blocks: list[str] = []
    for tag in ("p", "li", "tr"):
        raw_blocks.extend(re.findall(rf"<{tag}[^>]*>(.*?)</{tag}>", page, flags=re.I | re.S))
    out: list[str] = []
    noise = (
        "expert insights content hubs",
        "nothing in this blog constitutes",
        "please read the alpha architect disclosures",
        "was originally published",
        "check out our t-shirts",
        "posted may ",
        "subscribe",
        "advertisement",
        "cookie",
        "privacy policy",
    )
    for block in raw_blocks:
        text = clean_text(block, 900)
        lower = text.lower()
        if len(text) < 55:
            continue
        if any(x in lower for x in noise):
            continue
        if "data:image" in lower or "wp-image" in lower:
            continue
        out.append(text)
        if len(out) >= limit:
            break
    return out


def reddit_json_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    path = parts.path.rstrip("/")
    if path.endswith(".json"):
        path = path[:-5]
    return urllib.parse.urlunsplit(("https", "api.reddit.com", path, "", ""))


def reddit_thread_payload(url: str) -> object | None:
    host = urllib.parse.urlsplit(url).netloc.lower()
    if "reddit.com" not in host:
        return None
    candidates = [reddit_json_url(url)]
    parts = urllib.parse.urlsplit(url)
    legacy_path = parts.path.rstrip("/")
    if not legacy_path.endswith(".json"):
        legacy_path += ".json"
    candidates.append(urllib.parse.urlunsplit((parts.scheme or "https", parts.netloc or "www.reddit.com", legacy_path, "", "")))
    for candidate in dict.fromkeys(candidates):
        try:
            return json.loads(fetch_bytes(candidate, timeout=20).decode("utf-8", "ignore"))
        except Exception:
            continue
    return None


def old_reddit_thread_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    path = parts.path.rstrip("/") + "/"
    return urllib.parse.urlunsplit(("https", "old.reddit.com", path, "", ""))


def old_reddit_thread_metadata(url: str) -> tuple[int, int] | None:
    try:
        html_text = fetch_bytes(
            old_reddit_thread_url(url),
            timeout=20,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 ETFAllocationDigest/1.0"
                )
            },
        ).decode("utf-8", "ignore")
    except Exception:
        return None
    score_match = re.search(r'data-score="([\d-]+)"', html_text)
    comments_match = re.search(r'data-comments-count="([\d,]+)"', html_text)
    if not score_match and not comments_match:
        return None
    score = int(score_match.group(1).replace(",", "")) if score_match else 0
    comments = int(comments_match.group(1).replace(",", "")) if comments_match else 0
    return score, comments


def reddit_thread_metadata(url: str) -> tuple[int, int] | None:
    if urllib.parse.urlsplit(url).netloc.lower().startswith("old."):
        return old_reddit_thread_metadata(url)
    payload = reddit_thread_payload(url)
    if not isinstance(payload, list) or not payload:
        return old_reddit_thread_metadata(url)
    try:
        data = payload[0]["data"]["children"][0]["data"]
        return int(data.get("score") or 0), int(data.get("num_comments") or 0)
    except Exception:
        return old_reddit_thread_metadata(url)


def reddit_source_with_engagement(source: str, url: str) -> str:
    if "score/upvotes" in source:
        return source
    meta = reddit_thread_metadata(url)
    if meta is None:
        return f"{source}（score/upvotes 未抓取；comments/replies 未抓取）"
    score, comments = meta
    return f"{source}（score/upvotes {score}；comments/replies {comments}）"


def reddit_thread_paragraphs(url: str, limit: int = 8) -> list[str]:
    payload = reddit_thread_payload(url)
    if payload is None:
        return []
    out: list[str] = []

    def add_text(text: str | None) -> None:
        cleaned = clean_text(text, 1800)
        if len(cleaned) < 45:
            return
        if cleaned.lower() in {"[deleted]", "[removed]"}:
            return
        out.append(cleaned)

    if isinstance(payload, list):
        for listing in payload:
            children = (((listing or {}).get("data") or {}).get("children") or []) if isinstance(listing, dict) else []
            for child in children:
                data = (child or {}).get("data") or {}
                if not isinstance(data, dict):
                    continue
                add_text(data.get("selftext"))
                add_text(data.get("body"))
                if len(out) >= limit:
                    return out[:limit]
    return out[:limit]


def article_links(url: str, limit: int = 5) -> list[tuple[str, str]]:
    try:
        page = fetch_bytes(url, timeout=20).decode("utf-8", "ignore")
    except Exception:
        return []
    links: list[tuple[str, str]] = []
    seen: set[str] = set()
    preferred_hosts = (
        "allocatesmartly.com",
        "quantpedia.com",
        "alphaarchitect.com",
        "robotwealth.com",
        "mebfaber.com",
        "indexologyblog.com",
    )
    for href, label in re.findall(r"<a\s+[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", page, flags=re.I | re.S):
        absolute = urllib.parse.urljoin(url, html.unescape(href))
        parts = urllib.parse.urlsplit(absolute)
        host = parts.netloc.lower()
        if not any(h in host for h in preferred_hosts):
            continue
        if "quantocracy.com" in host:
            continue
        canonical = canonical_url(absolute)
        if canonical in seen:
            continue
        title = clean_text(label, 180)
        if len(title) < 8:
            continue
        seen.add(canonical)
        links.append((title, absolute))
        if len(links) >= limit:
            break
    return links


def bogleblog_bestof_forum_items(limit: int = 8) -> list[Item]:
    try:
        page = fetch_bytes(
            BOGLEBLOG_BEST_OF_BOGLEHEADS_URL,
            timeout=20,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125 Safari/537.36"},
        ).decode("utf-8", "ignore")
    except Exception:
        page = ""
    items: list[Item] = []
    seen: set[str] = set()
    def add_item(title: str, absolute: str) -> None:
        if len(items) >= limit:
            return
        canonical = canonical_url(absolute)
        if canonical in seen:
            return
        title = clean_text(title, 180)
        if len(title) < 8:
            return
        seen.add(canonical)
        summary = (
            "Bogleblog curated Bogleheads forum index item about portfolio allocation, ETF core holdings, "
            "Bogleheads-style diversification, and rebalancing."
        )
        items.append(Item("Bogleblog Best of Bogleheads Forum", title, absolute, "", summary))

    for href, label in re.findall(r"<a\s+[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", page, flags=re.I | re.S):
        absolute = urllib.parse.urljoin(BOGLEBLOG_BEST_OF_BOGLEHEADS_URL, html.unescape(href))
        parts = urllib.parse.urlsplit(absolute)
        if "bogleheads.org" not in parts.netloc.lower() or "/forum/" not in parts.path:
            continue
        if "viewtopic" not in parts.path:
            continue
        title = clean_text(label, 180)
        if title.startswith("http"):
            before = clean_text(page[max(0, page.find(href) - 220) : page.find(href)], 220)
            match = re.search(r"(?:<li>|>|^)([^<>:]{8,120}):?\s*(?:I found|This|https|$)", before, flags=re.I)
            title = clean_text(match.group(1), 180) if match else title
        add_item(title, absolute)
        if len(items) >= limit:
            break
    if not items:
        static_links = [
            ("Transition to 3 Fund Portfolio", "https://www.bogleheads.org/forum/viewtopic.php?t=407430"),
            ("Lazy Portfolios", "https://www.bogleheads.org/wiki/Lazy_portfolios"),
            ("Overall index of portfolios", "https://www.bogleheads.org/wiki/Template:Portfolios"),
        ]
        for title, url in static_links:
            add_item(title, url)
    return items


def enrich_aggregator_item(item: Item, base_summary: str) -> str:
    if item.source != "Quantocracy":
        return base_summary
    child_bits: list[str] = []
    for title, url in article_links(item.url, limit=3):
        paras = article_paragraphs(url, limit=4)
        if not paras:
            continue
        child_bits.append(f"子链接《{title}》：{' '.join(paras)}")
    if not child_bits:
        return base_summary
    return clean_text(" ".join([base_summary, *child_bits]), 20000)


def enrich_article_item(item: Item) -> Item:
    host = urllib.parse.urlsplit(item.url).netloc.lower()
    is_reddit = item.source.startswith("r/") or "reddit" in item.source.lower() or "reddit.com" in host
    forum_paras = reddit_thread_paragraphs(item.url, limit=20) if is_reddit else []
    paras = forum_paras or article_paragraphs(item.url, limit=60)
    base = clean_text(" ".join([item.summary, *paras]), 20000) if paras else item.summary
    summary = enrich_aggregator_item(item, base)
    source = reddit_source_with_engagement(item.source, item.url) if is_reddit else item.source
    return Item(source, item.title, item.url, item.published, summary)


def sort_recent(items: list[Item]) -> list[Item]:
    return sorted(items, key=lambda x: parse_date(x.published) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)


def filter_recent_published(items: list[Item], max_age_hours: int) -> list[Item]:
    cutoff = now_bj().astimezone(timezone.utc) - timedelta(hours=max_age_hours)
    return [item for item in items if (parse_date(item.published) or datetime.min.replace(tzinfo=timezone.utc)) >= cutoff]


def chinese_topic(title: str, summary: str = "") -> str:
    text = (title + " " + summary).lower()
    rules = [
        (("tax", "roth", "ira", "401", "irmaa", "medicare"), "税务/账户/医保阈值"),
        (("withdraw", "retire", "payday", "principal", "cash flow"), "退休现金流/提款"),
        (("trust", "estate", "charitable", "giving"), "信托/遗产/慈善"),
        (("real estate", "rental", "housing", "mortgage"), "房地产/居住成本"),
        (("concentrated", "direct indexing", "capital gain", "stock"), "集中持仓/税损收割"),
        (("retire", "retired", "retiring", "fire", "fatfire", "chubbyfire", "advisor", "cobra", "aca"), "高净值退休执行"),
        (("physician", "doctor", "career", "part-time", "mentor"), "高收入职业过渡"),
        (("travel", "hotel", "airline", "lounge", "resort", "suite", "business class"), "高质量旅行体验"),
        (("etf", "allocation", "portfolio", "bond", "treasury", "commodity"), "资产配置/ETF"),
        (("ai", "model", "openai", "anthropic", "google", "agent"), "AI 模型/产品"),
    ]
    for keys, label in rules:
        if any(k in text for k in keys):
            return label
    return "规划/执行风险"


def fat_fire_chinese_fact(item: Item) -> str:
    title = clean_text(item.title, 220)
    text = clean_text(item.summary, 3200)
    lower = f"{title} {text}".lower()
    is_community = item.source.startswith("r/")

    if "coastal ca cities" in lower:
        return (
            "帖子在比较适合 very FAT FIRE 家庭长期居住的加州海滨城市，重点候选包括 North County San Diego 和 Newport Beach 一带。"
            "发帖人的筛选条件很明确：年轻家庭友好、适合养狗、靠近海滩、环境相对安静，同时仍要有不错的餐厅和进入大城市的便利性。"
            "这类讨论本质上不是旅游目的地推荐，而是在做高预算家庭的长期居住地筛选，核心变量是社区气质、学校/家庭环境、生活便利度和城市可达性。"
        )
    if "financial advisor fee" in lower:
        return (
            "帖子讨论 ChubbyFIRE 阶段是否值得聘请财务顾问。发帖人过去一直自己管理资产，靠指数基金已经接近 S&P 500 回报；现在临近退休，担心税务、提款、保险和退休转换细节有盲区。"
            "他咨询了 4 位顾问后发现报价多在 AUM 的 0.8%-1% 左右，对 ChubbyFIRE 资产规模来说可能等于每年 5 万到 10 万美元费用。"
            "讨论焦点不是“顾问有没有用”，而是持续按资产收费是否能覆盖其提供的退休规划、税务协调、行为约束和执行价值。"
        )
    if "back in the game" in lower or "rusty after 10 years" in lower:
        return (
            "帖子来自一位已经 FAT FIRE 约 10 年的人，核心问题是长期退休后如果想重新进入工作或创业状态，是否还能恢复强度和竞争力。"
            "发帖人特别提到科技行业技能迭代很快，离开职场太久会让技术和执行节奏变生疏。"
            "这类讨论反映 FAT FIRE 的一个非财务风险：退出工作后，身份、技能、社交网络和高强度工作能力会逐步折旧，未来想重返赛场并不是只看钱够不够。"
        )
    if "two paths to chubbyfire" in lower:
        return (
            "帖子比较两条接近 ChubbyFIRE 时的路径：一种是在接近目标资产和年龄时逐步降低组合风险，类似标准 glide path；另一种是继续高风险投资直到达到目标。"
            "前者优点是达到特定退休时间的确定性更高，代价是可能更慢；后者可能更快达到数字，但也可能在最后阶段遇到大回撤，甚至因为贪心越过目标后没有及时收手。"
            "这个问题本质上是在讨论“冲刺收益”和“退休日期确定性”之间的取舍。"
        )
    if "fear of pulling the ripcord" in lower or "safely retire" in lower:
        return (
            "帖子是一组接近退休的家庭数字：丈夫 44 岁、妻子 42 岁，无子女，有一只狗；现金和投资约 380 万美元，其中接近一半在税优退休账户，现金约 25 万美元，其余在 taxable brokerage。"
            "家庭只剩约 20 万美元、3% 利率、9 年到期的房贷；妻子收入约 13 万美元且工作稳定，继续工作的一大原因是医疗保险。"
            "这类案例的核心不是单纯资产是否够，而是退休前后医保、税优账户取用时点、现金缓冲、房贷保留和一方继续工作的组合安排。"
        )
    if "cobra" in lower and "workable" in lower:
        return (
            "帖子比较旧金山四口之家早退后的医疗保险选择。发帖人估算，如果把 AGI 优化到 10.7 万美元，当地 ACA HMO 年保费约 4000 美元、最高自付约 1.4 万美元；而继续使用公司低免赔 COBRA 年费约 3.6 万美元。"
            "考虑医疗费用税前扣除后，COBRA 有效成本约 2.8 万美元，也就是多花约 1 万美元换取更好的覆盖和继续使用现有医生。"
            "这说明高净值早退家庭的医保选择不一定只看最低保费，医生网络、免赔额、最大自付、税务扣除和转换成本都可能影响最终选择。"
        )
    if "high-earning chubbies" in lower or "no matter what" in lower:
        return (
            "帖子讨论高收入 ChubbyFIRE 家庭在未来约 15 年是否具有较强容错空间。发帖家庭 40 岁出头，401(k)/投资账户超过 400 万美元，总净资产约 600 万美元，家庭年收入超过 80 万美元，并计划工作到 50 多岁中后期。"
            "发帖人把未来分成几种情景：牛市延续则资产继续复利；市场下跌则用高收入继续买入；最坏情形是 AI 冲击工作收入。"
            "这个案例的重点是劳动收入、投资资产和人力资本风险之间的关系，而不是简单相信高收入家庭一定能穿越所有情景。"
        )
    if "retired at 47" in lower and "first six months" in lower:
        return (
            "帖子是一位科技行业从业者的退休适应记录：工作 22 年，44 岁离开公司后做了 3 年咨询，47 岁时在净资产略高于 700 万美元、房子已还清、两个孩子上高中、年支出约 18 万美元的情况下正式退休。"
            "财务数字看起来很干净，但发帖人认为最困难的是退休后前 6 个月的心理和生活结构调整。"
            "这类案例强调，FAT FIRE 的难点不只在安全提款率，还包括身份转换、家庭节奏、时间结构和从高强度职业身份退出后的空缺感。"
        )
    if "tax season wrap up" in lower:
        return (
            "文章来自一位完成第 8 个报税季志愿服务的人，作者在宾夕法尼亚和新泽西两州、3 个县、7 个中心做过报税志愿者，大多通过 AARP TaxAide 项目服务。"
            "今年的服务地点在新泽西 Monmouth County 的本地图书馆，每周开放 3 天，通常有 5 到 6 名报税人员。"
            "这篇文章的价值在于从一线报税经验观察普通家庭税务问题，对高净值早退家庭也有提醒：税表复杂度、州税差异、退休账户取款、社保和医保相关税务都需要提前规划。"
        )
    if "investing in securitization" in lower:
        return (
            "文章是关于证券化投资的访谈，嘉宾是 Janus Henderson Investors 的 Mike Laughlin。讨论内容包括证券化产品如何运作、CLO 投资、证券化市场规模，以及固定收益投资方式的变化。"
            "对 FIRE 家庭来说，这类资产属于信用和现金流配置问题，不是单纯追求更高收益；需要理解底层贷款质量、结构分层、流动性和利率环境。"
        )
    if "life is simply one financial quest" in lower:
        return (
            "Financial Samurai 这篇文章围绕责任、传承和死亡意识展开，作者提到自己将在 2027 年中接近 50 岁，因此更频繁思考家庭责任和长期后果。"
            "文章把人生描述成一连串财务任务：每个阶段如果处理不好，可能会把问题传导到下一个阶段。"
            "对 FAT FIRE 家庭而言，这类内容更偏生命周期规划，关注保险、遗产、家庭支持、教育、长期护理和风险转移，而不是单一投资收益。"
        )
    if "mentor monday" in lower:
        return (
            "这是 r/fatFIRE 的每周 Mentor Monday 讨论帖，用于集中讨论早期阶段问题，包括职业建议、计划评估、数字测算、是否负担得起某项支出等。"
            "社区也欢迎更有经验的成员以 AMA 形式分享，例如 FAANG、风投、大律所等高收入路径。"
            "这类帖子适合作为案例池补充，但单条回复通常是个人经验，不能当作通用规划规则。"
        )
    if "part-time physician" in lower:
        return (
            "White Coat Investor 文章讨论医生如何设计兼职工作。核心观点是兼职本身可以改善工作生活平衡，但要满足预期，工作结构、排班、收入下降、福利和职业责任都需要提前谈清楚。"
            "对高收入专业人士来说，兼职常常是从全职职业到完全退休之间的过渡方案，重点不是只减少工时，而是控制收入、保险、倦怠和身份转换。"
        )
    if "weekly discussion thread" in lower:
        return (
            "这是 r/ChubbyFIRE 的每周自由讨论帖，允许社区成员讨论与 ChubbyFIRE 或中高收入生活方式相关的宽泛话题，包括早期问题、政治经济影响和日常规划。"
            "它更像社区情绪和问题池，不是一篇结构化研究文章；可用于发现当前高净值早退人群正在反复讨论的焦虑点。"
        )

    if is_community:
        return (
            f"这是一条来自 {item.source} 的社区讨论，主题是“{title}”。"
            f"帖子摘要显示，讨论重点包括：{chinese_topic(title, text)}。"
            "这类内容应作为真实家庭案例和行为偏差观察，而不是经过验证的财务结论。"
        )
    if text:
        return (
            f"这篇来自 {item.source} 的文章主题是“{title}”。"
            f"原文摘要和正文片段显示，它主要关联 {chinese_topic(title, text)}。"
            "由于当前云端版本没有接入 LLM 翻译服务，未命中特定模板的文章会先保留为中文事实概述，避免直接粘贴英文段落。"
        )
    return f"来源发布了“{title}”这篇内容，但 RSS 没有提供足够摘要，需打开原文确认细节。"


def fat_fire_implication(title: str, summary: str = "") -> str:
    text = (title + " " + summary).lower()
    if "back in the game" in text or "rusty" in text:
        return "长期退休会带来技能、身份和执行节奏折旧，若未来可能重返工作或创业，需要提前保留项目、网络和学习节奏。"
    if "ripcord" in text or "safely retire" in text:
        return "退休决策要同时看可投资资产、账户位置、医保来源、房贷、现金缓冲和一方继续工作的稳定性，而不是只看总净资产。"
    if "high-earning chubbies" in text or "no matter what" in text:
        return "高收入本身是强缓冲，但人力资本也可能是集中风险；需要把失业、AI 冲击、市场下跌和高支出同时纳入压力测试。"
    if "retired at 47" in text or "first six months" in text:
        return "财务自由之后仍需要生活结构设计，尤其是时间安排、家庭角色、社交圈和个人成就感，否则前几个月可能比数字测算更难。"
    if "tax season wrap" in text:
        return "报税季经验提醒高净值家庭不要低估税务执行细节，尤其是州税、退休账户取款、社保、医保和慈善/遗产安排。"
    if "securitization" in text:
        return "证券化资产可作为固定收益之外的信用暴露，但高净值家庭需要先理解底层现金流、结构分层和流动性，而不是只看收益率。"
    if "financial quest" in text:
        return "FAT FIRE 后仍会面对责任、传承、家庭支持和风险转移问题，规划边界应从退休扩展到生命周期和下一代安排。"
    if "mentor monday" in text:
        return "这类社区帖适合发现反复出现的早期问题，但不能把单个回复当作成熟规划建议。"
    if "part-time physician" in text:
        return "兼职是高收入专业人士降低倦怠和测试退休节奏的工具，但收入、保险、责任边界和排班要同步设计。"
    if "advisor" in text or "fee" in text:
        return "重点看顾问费用是否被具体服务抵消，例如税务协调、提款顺序、保险、遗产文件、行为约束和跨账户再平衡，而不是只比较投资收益。"
    if "cobra" in text or "aca" in text or "health" in text or "insurance" in text:
        return "医保是早退家庭的核心现金流变量，保费、最大自付、医生网络、AGI 控制和税务扣除要放在同一张表里比较。"
    if "retired" in text or "back in the game" in text or "part-time" in text:
        return "退休不是单次财务动作，还会改变身份、时间结构、技能折旧和家庭关系，适合用兼职、咨询或项目制工作作为过渡。"
    if "city" in text or "coastal" in text or "family" in text:
        return "高预算居住地选择应从税负、学校、医疗、机场、社区气质、房产流动性和日常生活半径一起评估。"
    if "risk" in text or "glide" in text or "portfolio" in text:
        return "接近退休目标时，组合风险应围绕退休日期确定性、现金流缺口和最大可承受回撤来设计，而不是只追求最快到达数字。"
    if "tax" in text or "401" in text or "roth" in text:
        return "税务和账户结构会影响真实可花现金流，尤其是 taxable、税延账户、Roth、州税和医保补贴之间的联动。"
    return "把它作为高净值早退的执行风险提示，重点落到现金流、税务、医保、家庭约束、职业退出和生活方式稳定性。"


def fat_fire_validation(title: str, summary: str = "") -> str:
    text = (title + " " + summary).lower()
    if "back in the game" in text or "rusty" in text:
        return "列出未来可能重返的行业、技能缺口、可维持的项目节奏和人脉维护计划，避免完全断开职业选择权。"
    if "ripcord" in text or "safely retire" in text:
        return "用家庭实际支出、税后现金流、账户位置、医保方案、房贷和 3-5 年现金缓冲做退休压力测试。"
    if "high-earning chubbies" in text or "no matter what" in text:
        return "把收入中断、市场下跌、教育/家庭支出和 AI 职业风险放进同一张情景表，不只看投资账户复利。"
    if "retired at 47" in text or "first six months" in text:
        return "提前设计退休后 6-12 个月的日程、项目、运动、社交和家庭分工，并给自己留出心理适应预算。"
    if "tax season wrap" in text:
        return "按所在州和账户类型复核报税复杂度，必要时提前和 CPA 做 Roth conversion、资本利得和医保补贴联动测算。"
    if "securitization" in text:
        return "检查产品的底层资产、久期、信用评级、费用、流动性和压力时期表现，再决定是否适合进入固定收益桶。"
    if "financial quest" in text:
        return "列出家庭责任清单，包括保险、遗嘱/信托、长期护理、教育支持和遗产执行人安排。"
    if "mentor monday" in text:
        return "只把社区回复作为问题清单来源，关键数字仍需用个人资产、税率、支出和家庭约束复算。"
    if "part-time physician" in text:
        return "测算兼职后的收入、福利、malpractice 覆盖、排班稳定性和是否还能保留长期职业选择权。"
    if "advisor" in text or "fee" in text:
        return "列出顾问实际交付清单和年度费用金额，比较一次性计费、小时计费、retainer 和 AUM 模式的长期成本。"
    if "cobra" in text or "aca" in text or "health" in text:
        return "用本州 ACA 计划、COBRA 报价、预期 AGI、最大自付和现有医生网络做逐年测算。"
    if "retire" in text or "ripcord" in text or "withdraw" in text:
        return "用家庭实际支出、税后现金流、账户位置、医保方案和 3-5 年现金缓冲做压力测试。"
    if "city" in text or "coastal" in text:
        return "把候选城市做成评分表，至少纳入税负、房价、保险、医疗、机场、学校、气候风险和日常便利性。"
    if "portfolio" in text or "glide" in text or "risk" in text:
        return "回测不同股债比例、现金桶、再平衡规则和退休前最后 5 年大跌情景对退休日期的影响。"
    return "用自己的家庭税率、账户结构、保险安排、支出预算和所在州规则复算，不直接套用作者或社区案例。"


def travel_relevant(item: Item) -> bool:
    text = f"{item.title} {item.summary} {item.url}".lower()
    exclusions = [
        "jeffrey epstein",
        "decoy",
        "frontier fly one",
        "prepaid fuel",
        "whoop",
        "fitness wearable",
        "strong approvals",
        "citi strata elite",
        "citi merchant offer",
        "sapphire preferred vs. reserve",
        "barclays spending offer",
        "targeted barclays",
        "hyatt find experiences auctions",
        "rakuten promotion",
        "frontier scolded",
        "save money on dining",
        "bonus points & rate promotions",
        "merchant offer",
        "save at amazon",
        "dying loved one",
        "brand badges",
        "atmos rewards summit visa",
        "amazing deal",
        "sale ends",
        "chatgpt business",
        "openai",
        "ai subscription",
    ]
    if any(x in text for x in exclusions):
        return False
    inclusions = [
        "hotel",
        "resort",
        "airline",
        "flying blue",
        "airport",
        "lounge",
        "points",
        "miles",
        "hyatt",
        "hilton",
        "marriott",
        "ihg",
        "four seasons",
        "belmond",
        "villa",
        "italy",
        "bali",
        "mendoza",
        "restaurant",
        "spring break",
        "kids",
        "family",
        "bedbug",
        "compensation",
        "lounger",
        "pool chair",
        "europe",
        "travel",
        "trip",
    ]
    return item.source.startswith("r/") or any(x in text for x in inclusions)


def travel_heading(title: str, summary: str = "") -> str:
    text = f"{title} {summary}".lower()
    if "amanvari" in text:
        return "Amanvari 将于 2026 年 8 月开业"
    if "berkshire bets" in text and "delta" in text:
        return "巴菲特重仓达美航空：航司稳定性与行业风险观察"
    if "commercial flights back to naples" in text or "naples, florida" in text:
        return "Naples 机场是否恢复商业航班：高端目的地交通可达性"
    if "current amex offers" in text:
        return "Amex 旅行优惠与定向返现更新"
    if "four seasons maui" in text and "lana" in text:
        return "毛伊岛与拉奈岛四季酒店蜜月选择"
    if "favorite family resort" in text and "kiawah" in text:
        return "亲子度假村选择：Kiawah、Sea Island 还是 Montage"
    if "four seasons mallorca" in text:
        return "马略卡四季酒店真实体验复盘"
    if "shinta mani mustang" in text or "upper mustang" in text:
        return "尼泊尔 Upper Mustang 与 Shinta Mani Mustang 是否值得"
    if "taking dog" in text and "caribbean" in text:
        return "冬季带狗去加勒比度假需要考虑什么"
    if "caribbean recommendations" in text:
        return "加勒比高端度假目的地推荐与筛选"
    if "waldorf astoria amsterdam" in text:
        return "阿姆斯特丹华尔道夫酒店体验复盘"
    if "fairmont mayakoba" in text:
        return "Fairmont Mayakoba 长住体验复盘"
    if "emirates skywards" in text and "devalu" in text:
        return "阿联酋 Skywards 再次贬值：里程价值风险"
    if "alaska airlines elite upgrades" in text:
        return "阿拉斯加航空精英升舱可能只到登机口确认"
    if "airline dress codes" in text or "offensive clothing" in text:
        return "航司着装规定与登机执行边界"
    if "isla palenque" in text or "chiriqui" in text:
        return "巴拿马 Isla Palenque 住后体验复盘"
    if "points and miles alive" in text:
        return "积分和里程有效期管理"
    if "spring break" in text and "kids" in text:
        return "2027 年春假亲子国际目的地选择"
    if "bali alternative" in text or "solo travel" in text:
        return "独自高端度假停留地选择"
    if "dulles" in text or "mobile lounges" in text:
        return "华盛顿 Dulles 机场改造"
    if "mendoza" in text:
        return "Mendoza 餐厅与葡萄酒体验"
    if "st tropez" in text or "restaurant" in text:
        return "目的地餐厅口碑与避坑"
    if "italy trip" in text or "taormina" in text or "palermo" in text or "positano" in text:
        return "意大利高端酒店线路规划"
    if "flying blue" in text:
        return "Flying Blue 会员计划变化"
    if "pool chair" in text or "lounger" in text or "towel hog" in text:
        return "欧洲度假村躺椅规则与退款风险"
    if "bedbug" in text:
        return "高端酒店虫害事故与补偿"
    if "explora journeys" in text or "cruises" in text:
        return "高端邮轮产品与私享价"
    if "antagonistic about the way i travel" in text or "just do you" in text:
        return "旅行方式与个人偏好边界"
    if "barclays" in text or "atmos" in text:
        return "航空联名卡定向消费任务"
    if "sixt" in text:
        return "租车燃油预付促销"
    if "sapphire" in text or "chase" in text:
        return "高端信用卡旅行权益"
    return "旅行体验案例：具体行程与服务复盘"


def travel_chinese_fact(item: Item) -> str:
    title = clean_text(item.title, 220)
    text = clean_text(item.summary, 3200)
    lower = f"{title} {text}".lower()

    if "points and miles alive" in lower:
        return (
            "Frequent Miler 这篇文章更新了 2026 年主要美国航空和酒店会员计划的积分/里程有效期规则。"
            "核心问题是哪些账户活动可以延长里程有效期，以及哪些项目可以通过信用卡可转点体系间接保持活跃。"
            "对长期环球旅行而言，这类信息不是为了薅小羊毛，而是防止多年积累的航司里程、酒店积分在不用时过期。"
        )
    if "spring break" in lower and "kids" in lower:
        return (
            "社区帖在规划 2027 年 3 月底四口之家、两个小孩分别 3 岁和 5 岁的约一周国际春假旅行。"
            "发帖人的条件包括目的地步行友好或交通简单、有适合家庭的餐厅和博物馆、从费城出发直飞或最多转机一次。"
            "候选方向包括葡萄牙、瑞士、布拉格、阿姆斯特丹和爱尔兰，也愿意接受其他适合小孩节奏的目的地。"
        )
    if "bali alternative" in lower or "fly and flop" in lower:
        return (
            "社区帖来自一位独自旅行者，计划从悉尼出发、之后去日本、最后回美国，中间想安排 5-7 天的高端停留。"
            "需求不是深度探索，而是“fly and flop”式放松：高端酒店、舒适躺平、尽量少折腾。"
            "原本考虑 Four Seasons Bali 的组合住宿优惠，在 Jimbaran Bay 和另一家巴厘岛物业之间分配时间，但也在寻找类似体验的替代目的地。"
        )
    if "dulles" in lower or "mobile lounges" in lower:
        return (
            "View from the Wing 报道称，Washington Dulles 机场可能推进旅客期待已久的重建方案。"
            "方案方向包括用真正连接铁路的航站楼大厅替代长期被视为临时方案的 C/D 区，并逐步减少移动休息车。"
            "问题在于成本已上升到约 220 亿美元，可能把机场成本推高到每名旅客 90 美元以上，除非由纳税人或部分私有化方案承担相当一部分费用。"
        )
    if "mendoza" in lower:
        return (
            "社区帖询问阿根廷 Mendoza 的非米其林餐厅建议。发帖人已经会安排几家高端或 tasting menu 餐厅，但希望第一天在 Mendoza 市区吃更轻松的普通餐厅。"
            "需求是有好食物和葡萄酒的 casual a la carte 或 parrilla，最好不要太游客化。"
            "这类信息适合长期环球旅行的“到达日策略”：第一晚避免过重行程，选择当地感强、交通轻松、用餐压力低的餐厅。"
        )
    if "italy trip" in lower or "taormina" in lower or "villa igiea" in lower:
        return (
            "社区帖在规划 2027 年意大利 12 晚高端旅行，时间考虑 4-5 月或 9-10 月的肩季。"
            "目前候选包括 FS Taormina 或 Belmond Villa Sant’Andrea 住 3-4 晚，Palermo 的 Villa Igiea 住 4 晚，再考虑 FS Florence 或 Il San Pietro Positano 住 4 晚。"
            "问题核心是西西里、佛罗伦萨和 Positano 如何组合，以及哪个肩季更适合避开旺季人流和高温。"
        )
    if "flying blue" in lower:
        return (
            "文章关注 Air France KLM Flying Blue 的管理层变化：Tiffany Funk 接任 Flying Blue 负责人，原负责人 Ben Lipsey 的职责扩大到 Loyalty、Digital 与 Data。"
            "报道强调 Tiffany Funk 长期站在会员和积分使用者视角，熟悉奖励票搜索、会员体验和项目导航。"
            "对常飞欧洲和跨大西洋航线的人来说，这可能影响 Flying Blue 未来在奖励票、伙伴航司、燃油附加费和会员体验上的取向。"
        )
    if "pool chair" in lower or "lounger" in lower or "towel" in lower:
        return (
            "报道提到，欧洲度假村正在更严格处理用毛巾占躺椅的问题。背景是一名德国游客因酒店躺椅不足而获得退款，法院认为广告承诺的度假体验受损。"
            "案例涉及希腊 Kos 的 Grecotel Kos Imperial，一家德国四口之家在 2024 年 8 月通过 TUI Deutschland 预订 11 晚套餐，价格为 7186 欧元，约合 8500 美元。"
            "这说明高端度假村的泳池/海滩容量、躺椅管理和服务兑现，可能从体验问题变成退款和法律风险。"
        )
    if "bedbug" in lower:
        return (
            "社区帖讨论一家昂贵五星酒店出现 bedbugs 后的补偿问题。发帖人称妻子和孩子被咬得很严重，身体多处出现大量叮咬，原本特殊旅行被彻底破坏。"
            "酒店道歉并免除了住宿费用，但发帖人认为这没有覆盖更广泛的影响，例如医疗、行程损失、心理压力和后续处理成本。"
            "这类案例提醒高端旅行也要保留证据、照片、医疗记录、酒店沟通记录和保险材料。"
        )
    if "explora journeys" in lower or "cruises" in lower:
        return (
            "Travel Codex 提到 Explora Journeys 邮轮出现 private fares，折扣最高可达 30%。"
            "Explora Journeys 属于更偏高端、慢节奏的邮轮产品，适合把邮轮当成移动酒店和目的地串联方式来评估。"
            "这类信息的重点不是折扣本身，而是邮轮是否符合你们未来多年环球旅行中的节奏：少换酒店、减少交通摩擦、但也牺牲一部分目的地自由度。"
        )
    if "airline dress codes" in lower or "offensive clothing" in lower:
        return (
            "文章讨论航司着装规定中“冒犯性服装”的执行边界。"
            "这类规则通常写得比较模糊，实际执行取决于航司员工、机场现场判断和乘客沟通方式。"
            "对长期旅行者来说，这不是穿衣审美问题，而是登机风险、现场争议和行程中断风险。"
        )
    if "isla palenque" in lower or "chiriqui" in lower:
        return (
            "帖子复盘巴拿马 Chiriqui 的 Isla Palenque 入住体验。"
            "这类私人岛屿/偏远度假酒店的重点不只是房价和景观，还包括抵达交通、餐饮稳定性、活动安排、服务响应和雨季/虫蚊等现场变量。"
            "适合纳入未来中美洲高端海岛目的地筛选清单，但需要继续核验季节、航班和真实住客评论。"
        )
    if "antagonistic about the way i travel" in lower or "just do you" in lower:
        return (
            "One Mile at a Time 这篇文章讨论不同旅行方式之间的评价和冲突。"
            "核心事实是，高端旅行、积分旅行、独自旅行、家庭旅行和慢旅行之间没有统一答案，适合别人的节奏未必适合自己。"
            "对你们未来 10 年以上的环球旅行来说，这类内容的价值在于提醒：需要建立自己的体验标准，而不是被外部评价、网红路线或积分最大化牵着走。"
        )
    if "barclays" in lower and "atmos" in lower:
        return (
            "Frequent Miler 报道 Barclays 针对部分 Hawaiian Airlines Atmos 持卡人推出定向消费任务。"
            "活动要求在注册后到 2026 年 6 月 8 日之间完成 5 笔每笔 50 美元消费，可获得 2500 点 Atmos 里程。"
            "这类活动适合顺手完成，不值得为了小额里程改变消费结构。"
        )
    if "sixt" in lower:
        return (
            "Frequent Miler 报道 Sixt 对 Sixt ONE 会员提供美国租车 49.99 美元预付燃油活动，适用于即日起至 2026 年 5 月 31 日取车的租赁。"
            "文章也指出，这不一定是好交易，具体取决于车型油箱大小和当地油价。"
            "对高质量旅行而言，这类信息只能作为租车结算便利性提示，不应取代对车辆等级、保险、取还车效率和服务稳定性的评估。"
        )
    if "sapphire preferred vs. reserve" in lower:
        return (
            "One Mile at a Time 比较 Chase Sapphire Preferred 与 Chase Sapphire Reserve 两张旅行卡。"
            "这类内容主要影响旅行保险、点数转移、机场/酒店权益和年费回收，而不是目的地体验本身。"
            "如果已有成熟信用卡体系，应把它作为权益盘点，而不是因为单篇文章频繁换卡。"
        )
    if "sapphire reserve" in lower and "whoop" in lower:
        return (
            "Frequent Miler 报道 Chase Sapphire Reserve 持卡人可获得约一年的 WHOOP 健身穿戴会员权益。"
            "这更偏高端卡生活方式权益，而不是核心旅行体验。"
            "如果已经持卡且会使用 WHOOP，可以视为附加价值；否则不应为这个权益单独调整信用卡策略。"
        )
    if "st tropez" in lower and "restaurant" in lower:
        return (
            "社区帖讨论 St Tropez 一些被反复推荐但评价不佳的餐厅，发帖人想判断哪些餐厅是名气大于体验。"
            "这类内容对高端旅行很实用：热门度、网红推荐和真实用餐体验可能脱节，尤其在旺季海滨目的地。"
            "选择餐厅时应同时看近期评论、订位难度、地理位置、服务稳定性和是否符合当天行程节奏。"
        )

    if item.source.startswith("r/"):
        return (
            f"这是一条来自 {item.source} 的旅行社区讨论，主题是“{title}”。"
            f"帖子摘要显示，重点围绕 {travel_heading(title, text)} 展开。"
            "社区内容适合作为真实体验和踩坑案例来源，但需要结合评论质量和个人偏好判断。"
        )
    if text:
        return (
            f"这篇来自 {item.source} 的文章主题是“{title}”。"
            f"当前可确认它主要关联 {travel_heading(title, text)}。"
            "云端脚本会优先保留中文事实概述，避免直接把英文 RSS 摘要或广告免责声明放入报告。"
        )
    return f"来源发布了“{title}”这篇内容，但 RSS 没有提供足够摘要，需打开原文确认细节。"


def travel_implication(title: str, summary: str = "") -> str:
    text = f"{title} {summary}".lower()
    if "bedbug" in text or "compensation" in text:
        return "高价酒店也会出现严重服务事故，预订时要重视保险、酒店响应机制、证据留存和升级补偿路径。"
    if "explora journeys" in text or "cruises" in text:
        return "高端邮轮可以作为慢旅行工具评估，重点看航线质量、岸上体验、套房空间、餐饮、船上密度和是否降低转场疲劳。"
    if "antagonistic about the way i travel" in text or "just do you" in text:
        return "长期环球旅行要有自己的节奏和取舍，避免为了迎合别人眼中的高端、深度或性价比而牺牲真实体验。"
    if "pool chair" in text or "lounger" in text:
        return "度假村体验要看实际服务容量，尤其是躺椅、泳池、早餐、儿童活动和旺季管理，而不是只看房间照片。"
    if "st tropez" in text or "restaurant" in text:
        return "餐厅选择要区分名气、位置、景观和食物本身，尤其在高端度假区，热门推荐不一定等于稳定体验。"
    if "points and miles alive" in text or "flying blue" in text or "atmos" in text:
        return "对长期环球旅行，会员计划管理的重点是可用性和稳定性：里程不过期、奖励票好查、伙伴航司覆盖好、改签规则清楚。"
    if "spring break" in text or "kids" in text or "family" in text:
        return "亲子旅行应优先看直飞/少转机、步行友好、餐厅容错、博物馆和短行程节奏，而不是只看目的地名气。"
    if "bali" in text or "solo" in text or "resort" in text:
        return "高端度假停留更应关注酒店硬件、餐饮、泳池/海滩、服务一致性和机场接驳，而不是塞满景点。"
    if "dulles" in text or "airport" in text:
        return "机场改造会影响转机体验、步行距离、航站楼拥堵和贵宾室可达性，长期路线规划要关注枢纽质量。"
    if "mendoza" in text or "restaurant" in text:
        return "慢旅行的餐饮体验不应只追求米其林，第一天和转场日更适合低压力、当地感强、酒单稳定的餐厅。"
    if "italy" in text or "taormina" in text or "positano" in text:
        return "高端意大利线路要把季节、人流、酒店位置、交通转场和每站停留天数一起设计，避免频繁换酒店拖累体验。"
    return "用于沉淀长期环球旅行的筛选标准：舒适度、位置、服务稳定性、航线便利性、权益兑现和低摩擦转场。"


def travel_standard(title: str, summary: str = "") -> str:
    text = f"{title} {summary}".lower()
    if "bedbug" in text or "compensation" in text:
        return "保留照片、医疗记录、酒店沟通、费用损失和保险材料；高端酒店事故处理要有证据链。"
    if "explora journeys" in text or "cruises" in text:
        return "记录航线停靠、岸上时间、套房面积、餐饮稳定性、儿童/成人氛围、网络质量和与陆地酒店组合的便利度。"
    if "antagonistic about the way i travel" in text or "just do you" in text:
        return "写清你们自己的旅行原则：每站停留时长、酒店等级、餐饮偏好、转场频率、是否接受邮轮/团队体验和休息日比例。"
    if "pool chair" in text or "lounger" in text:
        return "记录房型、泳池/沙滩容量、躺椅规则、早餐、餐饮水平、服务响应、Spa、接送机和旺季拥挤度。"
    if "st tropez" in text or "restaurant" in text:
        return "建立餐厅筛选表：近期差评原因、菜品稳定性、服务节奏、景观溢价、订位难度和离酒店距离。"
    if "points and miles alive" in text or "flying blue" in text or "atmos" in text:
        return "维护一张会员计划清单，记录里程有效期、延长期动作、常用转点伙伴、奖励票搜索规律和改退票成本。"
    if "kids" in text or "family" in text:
        return "记录飞行时长、转机次数、步行便利度、亲子餐厅、天气、儿童友好博物馆和酒店房型适配。"
    if "resort" in text or "bali" in text or "pool chair" in text:
        return "记录房型、泳池/沙滩容量、躺椅规则、早餐、餐饮水平、服务响应、Spa、接送机和旺季拥挤度。"
    if "airport" in text:
        return "记录常用枢纽的航站楼动线、贵宾室、安检、转机时间、延误韧性和长期改造影响。"
    if "restaurant" in text or "mendoza" in text:
        return "建立到达日餐厅标准：离酒店近、订位简单、菜品稳定、酒单好、氛围轻松、不以打卡为主。"
    if "italy" in text or "taormina" in text or "positano" in text:
        return "用表格比较每个候选酒店的季节、交通、房型、景观、餐饮、周边可玩性和换酒店成本。"
    return "记录舒适度、位置、服务稳定性、航线便利性、权益兑现、餐饮质量和长期在路上的疲劳成本。"


def etf_research_heading(title: str, summary: str = "") -> str:
    title_l = title.lower()
    text = (title + " " + summary).lower()
    if "active etfs win the liquidity race" in title_l:
        return "主动 ETF 流动性、成交量与买卖价差"
    if "economic policy uncertainty and aggregate economic activity in india" in title_l:
        return "印度经济政策不确定性与经济活动"
    if "stock market prediction using node transformer" in title_l:
        return "Node Transformer + BERT 情绪分析的股市预测论文"
    if "shortening hong kong stock settlement cycle" in text or "縮短香港股票現貨市場結算週期" in title_l:
        return "港股现货结算周期缩短咨询"
    if "world markets watchlist" in title_l:
        return "全球市场与跨区域股票表现"
    if "weekly economic snapshot" in title_l:
        return "美国经济数据与利率预期"
    if "dram hits" in title_l or "record pace" in title_l:
        return "AI 主题 ETF 资金流与产品热度"
    if "nuclear" in title_l:
        return "能源主题与产业链 ETF"
    if "securitization" in title_l:
        return "证券化资产与信用配置"
    if "melt-up" in title_l:
        return "风险偏好与市场过热"
    if "hedge fund" in title_l or "bearish" in title_l:
        return "机构情绪与风险叙事"
    if "trend following" in title_l or "regime-dependent" in title_l:
        return "趋势跟踪与状态依赖配置"
    if "private equity" in title_l or "401k" in title_l.replace(" ", ""):
        return "私募股权与退休账户配置"
    if "institutional investor attention" in text or ("macro news" in text and "volatility" in text):
        return "机构注意力、波动率与宏观新闻"
    if "animal spirits" in title_l or "stock market is doing something" in title_l:
        return "市场结构与风险偏好"
    if "capital market" in text or "expected return" in text or "valuation" in text:
        return "长期资本市场假设与估值"
    if "treasury" in text or "duration" in text or "bond" in text or "yield" in text:
        return "债券久期、收益率与信用风险"
    if re.search(r"\b(factor|momentum|value|quality)\b", text):
        return "因子配置与轮动"
    if "commodity" in text or "gold" in text or "inflation" in text:
        return "商品、黄金与通胀对冲"
    if "nuclear" in text or "uranium" in text or "energy" in text:
        return "能源主题与产业链 ETF"
    if "securitization" in text or "asset-backed" in text:
        return "证券化资产与信用配置"
    if "melt-up" in text:
        return "风险偏好与市场过热"
    if "hedge fund" in text or "bearish" in text:
        return "机构情绪与风险叙事"
    if "flow" in text or "aum" in text or "expense ratio" in text:
        return "ETF 资金流与产品结构"
    if "rebalance" in text or "allocation" in text or "portfolio" in text:
        return "组合再平衡与资产配置"
    return chinese_topic(title, summary)


def etf_research_relevant(item: Item) -> bool:
    text = f"{item.title} {item.summary} {item.url}".lower()
    exclusions = [
        "leveraged-inverse",
        "single-stock",
        "bitcoin dominance",
        "altcoins",
        "crypto-etf-hub",
        "529",
        "rocket labs",
        "surge boosts",
    ]
    if any(x in text for x in exclusions):
        return False
    if re.search(r"\([A-Z]{2,5}\)", item.title) and ("surge" in text or "boosts" in text):
        return False
    if re.search(r"\b(stock|shares)\b", text) and "market" not in text and "sector" not in text:
        return False
    inclusions = [
        "allocation",
        "portfolio",
        "market",
        "markets",
        "economic",
        "inflation",
        "treasury",
        "bond",
        "yield",
        "duration",
        "factor",
        "momentum",
        "value",
        "quality",
        "commodity",
        "gold",
        "currency",
        "dollar",
        "etf",
        "fund",
        "flow",
        "aum",
        "volatility",
        "macro",
        "rebalance",
        "expected return",
        "valuation",
    ]
    return any(x in text for x in inclusions)


def dedupe_items(items: list[Item]) -> list[Item]:
    seen: set[str] = set()
    out: list[Item] = []
    for item in items:
        title_key = re.sub(r"\W+", " ", item.title.lower()).strip()
        path_key = urllib.parse.urlsplit(item.url).path.rstrip("/").lower()
        key = title_key or path_key
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def canonical_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=False)
    query = [(k, v) for k, v in query if not k.lower().startswith("utm_") and k.lower() not in {"source", "rss"}]
    return urllib.parse.urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), urllib.parse.urlencode(query), "")
    )


def norm_title(title: str) -> str:
    return re.sub(r"\W+", " ", title.lower()).strip()


def load_digest_history(kind: str) -> dict[str, object]:
    path = Path("digest_history") / f"{kind}.json"
    if not path.exists():
        return {"items": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"items": []}
    return payload if isinstance(payload, dict) else {"items": []}


def filter_previously_sent(kind: str, items: list[Item], days: int = 7, ignore_dates: set[str] | None = None) -> list[Item]:
    history = load_digest_history(kind)
    cutoff = now_bj().date() - timedelta(days=days)
    ignore_dates = ignore_dates or set()
    sent_urls: set[str] = set()
    sent_titles: set[str] = set()
    for rec in history.get("items", []):
        if not isinstance(rec, dict):
            continue
        sent_date = str(rec.get("sent_date", ""))
        if sent_date in ignore_dates:
            continue
        if sent_date and sent_date < cutoff.isoformat():
            continue
        if rec.get("url"):
            sent_urls.add(canonical_url(str(rec["url"])))
        if rec.get("title"):
            sent_titles.add(norm_title(str(rec["title"])))
    out: list[Item] = []
    for item in items:
        if canonical_url(item.url) in sent_urls:
            continue
        if norm_title(item.title) in sent_titles:
            continue
        out.append(item)
    return out


def update_digest_history(kind: str, items: list[Item], days: int = 10) -> None:
    path = Path("digest_history") / f"{kind}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    history = load_digest_history(kind)
    cutoff = now_bj().date() - timedelta(days=days)
    records: list[dict[str, str]] = []
    for rec in history.get("items", []):
        if not isinstance(rec, dict):
            continue
        sent_date = str(rec.get("sent_date", ""))
        if sent_date and sent_date < cutoff.isoformat():
            continue
        records.append({k: str(v) for k, v in rec.items() if k in {"sent_date", "source", "title", "url"}})
    today = report_date()
    existing = {(canonical_url(r.get("url", "")), norm_title(r.get("title", ""))) for r in records}
    for item in items:
        key = (canonical_url(item.url), norm_title(item.title))
        if key in existing:
            continue
        records.append({"sent_date": today, "source": item.source, "title": item.title, "url": item.url})
    path.write_text(json.dumps({"items": records}, ensure_ascii=False, indent=2), encoding="utf-8")


def meta_title_label(item: Item) -> str:
    return "原帖标题" if item.source.startswith("r/") else "原文标题"


def fat_fire_heading(title: str, summary: str = "") -> str:
    text = f"{title} {summary}".lower()
    if "$2m burnout" in text or "pulling the plug" in text:
        return "200 万美元倦怠帖一年后：正式退休前的最后检查"
    if "dual income with kids" in text or "burnt out" in text:
        return "双职工有娃家庭高度倦怠：是否该降速"
    if "pay off house" in text or "kids trusts" in text:
        return "先还清房贷还是先给孩子设信托"
    if "nerf gun incident" in text:
        return "沉没成本、家庭消费和决策质量"
    if "planning apps" in text:
        return "高净值家庭常用规划工具清单"
    if "iced coffee hour" in text:
        return "fatFIRE 被播客讨论：公众认知和标签风险"
    if "4 abilities every investor" in text:
        return "成功投资者需要的四种能力"
    if "not there yet" in text:
        return "尚未达标家庭的 Chubby/FAT FIRE 规划检查"
    if "$7m nw" in text or "fire or wait another" in text:
        return "45 岁 700 万美元净值：现在 FIRE 还是再工作几年"
    if "happiness" in text and "travel" in text:
        return "频繁旅行能否带来长期满足感"
    if "glp1" in text or "copay" in text:
        return "GLP-1 药物自付成本与医疗保障可得性"
    if "3 million" in text:
        return "300 万美元里程碑后的下一步规划"
    if "convince anyone" in text and "fire" in text:
        return "现实生活中很难说服别人接受 FIRE"
    if "accountants" in text and "financial advice" in text:
        return "为什么不能把会计当作财务规划顾问"
    if "coastal ca cities" in text:
        return "适合高预算家庭长期居住的加州海滨城市"
    if "financial advisor fee" in text:
        return "临近退休是否值得为财务顾问支付 AUM 费用"
    if "back in the game" in text:
        return "退休多年后如何重新回到工作或创业状态"
    if "two paths to chubbyfire" in text:
        return "接近 ChubbyFIRE 时：降低风险还是继续冲刺"
    if "fear of pulling the ripcord" in text or "safely retire" in text:
        return "接近退休却不敢按下按钮：家庭现金流与医保案例"
    if "cobra" in text:
        return "COBRA 医保为什么可能比低保费 ACA 更合适"
    if "high-earning chubbies" in text:
        return "高收入 ChubbyFIRE 家庭是否真的有足够容错"
    if "first six months" in text and "retired" in text:
        return "退休后最难的可能是前六个月适应"
    if "tax season" in text:
        return "报税季一线观察：退休家庭别低估税务执行"
    if "securitization" in text:
        return "证券化资产如何进入退休固定收益桶"
    if "financial quest" in text:
        return "人生是一连串财务任务：责任与传承"
    if "part-time physician" in text:
        return "高收入专业人士如何把兼职作为退休过渡"
    if "mentor monday" in text:
        return "r/fatFIRE 导师周一：早期问题和经验池"
    if "weekly discussion" in text:
        return "ChubbyFIRE 每周讨论：把社区问题当风险清单"
    return f"{chinese_topic(title, summary)}相关新条目"


def asset_display_name(asset: "MarketAsset") -> str:
    overrides = {
        "QQQM": "纳斯达克100 ETF",
        "EMXC": "新兴市场 ex China ETF",
        "VEA": "发达市场 ETF",
        "GLDM": "黄金ETF",
        "VGLT": "美国长期国债ETF",
        "PDBC": "多商品期货ETF",
        "IBIT": "比特币现货ETF",
        "UUP": "美元指数多头基金",
        "DBMF": "管理期货ETF",
        "KMLM": "管理期货ETF",
        "RSP": "标普500等权 ETF",
        "VWO": "新兴市场 ETF",
        "EWJ": "日本股票 ETF",
        "EWG": "德国股票 ETF",
        "EWU": "英国股票 ETF",
        "EWZ": "巴西股票 ETF",
        "INDA": "印度股票 ETF",
        "EWT": "台湾股票 ETF",
        "EWY": "韩国股票 ETF",
        "FXI": "中国大盘股 ETF",
        "ASHR": "沪深300 A股 ETF",
        "XLK": "美国科技行业 ETF",
        "XLY": "美国可选消费行业 ETF",
        "XLP": "美国必需消费行业 ETF",
        "XLE": "美国能源行业 ETF",
        "XLF": "美国金融行业 ETF",
        "XLV": "美国医疗保健行业 ETF",
        "XLI": "美国工业行业 ETF",
        "XLB": "美国材料行业 ETF",
        "XLRE": "美国房地产行业 ETF",
        "XLU": "美国公用事业行业 ETF",
        "XLC": "美国通信服务行业 ETF",
        "SMH": "半导体产业链 ETF",
        "IGV": "美国软件行业 ETF",
        "BUG": "网络安全主题 ETF",
        "XBI": "生物科技行业 ETF",
        "KRE": "美国区域银行 ETF",
        "XRT": "美国零售行业 ETF",
        "XHB": "美国住宅建筑 ETF",
        "JETS": "航空公司 ETF",
        "IYT": "美国运输行业 ETF",
        "URA": "铀矿和核燃料 ETF",
        "TAN": "太阳能产业链 ETF",
        "ICLN": "全球清洁能源 ETF",
        "MTUM": "美国动量因子 ETF",
        "VLUE": "美国价值因子 ETF",
        "QUAL": "美国质量因子 ETF",
        "USMV": "美国低波动因子 ETF",
        "SHY": "美国短期国债 ETF",
        "IEF": "美国7-10年国债 ETF",
        "TIP": "美国TIPS债券 ETF",
        "LQD": "美元投资级公司债 ETF",
        "HYG": "美元高收益债 ETF",
        "EMB": "美元新兴市场主权债 ETF",
        "MUB": "美国市政债 ETF",
        "VNQ": "美国REITs ETF",
    }
    if asset.code in overrides:
        return overrides[asset.code]
    if re.search(r"[\u4e00-\u9fff]", asset.name):
        return asset.name
    first = re.split(r"[。；，]", asset.description)[0].strip()
    return first or asset.name


def append_strategy_price_table(lines: list[str], rows: list[dict[str, object]]) -> None:
    lines += ["| 代码 | 涨跌幅 | 名称 |", "|---|---:|---|"]
    for row in rows:
        asset = row["asset"]
        assert isinstance(asset, MarketAsset)
        lines.append(f"| {asset.code} | {fmt_change(row['change'])} | {asset_display_name(asset)} |")


def append_mover_table(lines: list[str], rows: list[dict[str, object]]) -> None:
    lines += ["| 排名 | 代码 | 涨跌幅 | 名称 | 说明 |", "|---:|---|---:|---|---|"]
    for i, row in enumerate(rows, 1):
        asset = row["asset"]
        assert isinstance(asset, MarketAsset)
        lines.append(
            f"| {i} | {asset.code} | {fmt_change(row['change'])} | {asset_display_name(asset)} | {asset.description} |"
        )


def etf_forum_relevant(item: Item) -> bool:
    text = f"{item.title} {item.summary}".lower()
    if any(k in text for k in ["conference", "register now", "meetup", "moderator"]):
        return False
    if "spam" in text and ("mods" in text or "this sub" in text):
        return False
    return any(
        k in text
        for k in [
            "etf",
            "portfolio",
            "allocation",
            "dividend",
            "bond",
            "treasury",
            "bogle",
            "vti",
            "vt",
            "voo",
            "spy",
            "qqq",
            "schd",
            "smh",
            "cash",
            "market",
        ]
    )


def etf_public_heading(title: str, summary: str = "") -> str:
    title_lower = title.lower()
    text = f"{title} {summary}".lower()
    if "tactical yield" in title_lower:
        return "Meb Faber Tactical Yield：收益率驱动的债券/现金切换"
    if "commodity futures returns since 1871" in title_lower:
        return "1871 年以来商品期货收益指数：长期商品风险溢价"
    if "dual momentum allocation between physical gold and bitcoin" in title_lower:
        return "黄金与比特币的双动量配置"
    if "attention factor" in title_lower and ("crypto" in title_lower or "bitcoin" in text or "btc" in text):
        return "投机情绪因子：加密资产如何联动股票市场"
    if "recent quant links from quantocracy" in title_lower:
        return "Quantocracy 量化链接池：只做策略线索筛选"
    if "surfing the equity curve" in text:
        return "用权益曲线趋势决定策略开关"
    if "s&p 500 snapshot" in text:
        return "标普500七周连涨后周五回落"
    if "treasury yields snapshot" in text:
        return "美国国债收益率快照与久期压力"
    if "xlk passes" in text or "100 billion" in text:
        return "XLK 规模突破 1000 亿美元：科技 ETF 拥挤度"
    if "emerging markets bonds" in text:
        return "新兴市场债相对美债仍有吸引力"
    if "4 abilities every investor" in text:
        return "成功投资者需要的四种能力"
    if "inflation-resilient portfolios" in text:
        return "通胀韧性组合与多资产配置"
    if "dram pr" in text:
        return "DRAM 主题 ETF 是否被过度营销"
    if "are etfs always the solution" in text:
        return "ETF 是否总是最优解决方案"
    if "how’s it looking" in text or "how's it looking" in text:
        return "24 岁投资组合求评"
    if "rate my portfolio" in text:
        return "31 岁投资组合配置求评"
    if "bond allocation during zirp" in text:
        return "零利率时期是否仍应配置债券"
    if "bonds being safe" in text and "bond market is down" in text:
        return "债券被称为安全资产但债市为何下跌"
    return etf_research_heading(title, summary)


def etf_title_translation(title: str, summary: str = "") -> str:
    title_lower = title.lower()
    text = f"{title} {summary}".lower()
    if "tactical yield" in title_lower:
        return "Meb Faber 的 Tactical Yield：简单直观的收益率切换框架"
    if "commodity futures returns since 1871" in title_lower:
        return "1871 年以来商品期货收益指数"
    if "dual momentum allocation between physical gold and bitcoin" in title_lower:
        return "实物黄金与比特币（数字黄金）的双动量配置"
    if "attention factor" in title_lower and ("crypto" in title_lower or "bitcoin" in text or "btc" in text):
        return "注意力因子：连接加密资产与公开股票市场的共同风险线索"
    if "active etfs win the liquidity race" in title_lower:
        return "主动 ETF 流动性、成交量与买卖价差"
    if "economic policy uncertainty and aggregate economic activity in india" in title_lower:
        return "印度经济政策不确定性与经济活动"
    if "stock market prediction using node transformer" in title_lower:
        return "Node Transformer + BERT 情绪分析的股市预测论文"
    if "縮短香港股票現貨市場結算週期" in title or "shortening hong kong stock settlement cycle" in text:
        return "港股现货结算周期缩短咨询"
    return etf_public_heading(title, summary)


def paired_title(original: str, chinese: str) -> str:
    original_clean = clean_text(original, 180)
    chinese_clean = clean_text(chinese, 180)
    if not original_clean:
        return chinese_clean
    if not chinese_clean or norm_title(original_clean) == norm_title(chinese_clean):
        return original_clean
    return f"{original_clean}｜{chinese_clean}"


def etf_display_title(item: Item) -> str:
    return paired_title(item.title, etf_title_translation(item.title, item.summary))


def etf_chinese_fact(item: Item) -> str:
    title = clean_text(item.title, 220)
    text = clean_text(item.summary, 3000)
    lower = f"{title} {text}".lower()
    title_lower = title.lower()
    parts: list[str] = []

    if "world markets watchlist" in lower:
        parts.append(
            "文章跟踪全球九个主要股票指数，截至 2026 年 5 月 11 日，其中六个指数年内仍为正收益。"
            "日本 Nikkei 225 年内上涨约 24.0%，领先观察清单；美国 S&P 500 上涨约 8.3%，加拿大 TSX 上涨约 7.7%。"
            "表现较弱的是印度 BSE SENSEX，年内下跌约 10.8%；德国 DAXK 和法国 CAC 40 分别下跌约 2.6% 和 1.1%。"
        )
    elif "tactical yield" in lower:
        parts.append(
            "文章测试 Meb Faber 的 Tactical Yield 思路：在 T-Bills、美国国债久期和公司债信用风险之间做切换。"
            "核心依据是收益率本身对未来债券回报的解释力，尤其是 10 年期初始收益率对后续长期回报的预测作用。"
            "这类策略的重点不是追逐债券单日涨跌，而是判断现金收益率是否已经足够高，是否值得少承担久期或信用风险。"
        )
    elif "attention factor" in lower and ("crypto" in lower or "bitcoin" in lower or "btc" in lower):
        parts.append(
            "文章讨论“投机注意力/投机情绪”这一共同风险因子：BTC、0DTE 期权、零佣金券商、社交情绪股票和部分加密相关股票可能受同一批边际投机资金影响。"
            "核心主旨不是问组合有没有直接买加密资产，而是检查股票和 ETF 里是否已经隐含了加密情绪、投机参与度和风险偏好传导。"
            "因此配置判断应从二元的“有无 crypto”转向连续的投机情绪暴露评估。"
        )
    elif "volatility forecasts lead to better portfolios" in lower or (
        "graph neural networks" in lower and "realized volatility" in lower and "portfolio performance" in lower
    ):
        parts.append(
            "文章检验更好的波动率预测是否真的能转化为更好的组合表现，而不是只停留在预测误差更低。"
            "样本层面，摘要给出 2015-2025 年 465 只 S&P 500 股票的周度已实现波动率，并把 HAR、LSTM 基准与基于滚动相关性、行业相似度和供应链网络特征的 GraphSAGE 模型比较。"
            "对 ETF/组合研究的意义在于：这类论文只有在能改善权重、目标波动率缩放或风险预算后的样本外收益回撤时，才有配置价值。"
        )
    elif "weekly economic snapshot" in lower and "labor market" in lower:
        parts.append(
            "文章的核心是美国劳动力市场仍然强于预期：4 月新增就业 11.5 万，高于市场预期的 4.6 万。"
            "3 月就业数据被上修至 18.5 万，2 月则下修为减少 15.6 万，失业率维持在 4.3%。"
            "文章认为，这组数据让美联储在降息时点上仍有等待空间，同时市场把它解读为增长韧性信号，S&P 500 因此延续周度上涨并刷新高位。"
        )
    elif "hits $6.5 billion" in lower or "record pace" in lower:
        parts.append(
            "文章关注 Roundhill Memory ETF（DRAM）的资产规模扩张：截至 2026 年 5 月 11 日，该 ETF 上市 36 天内达到 65 亿美元 AUM。"
            "文中引用 Bloomberg 的 Eric Balchunas 观点称，这一速度快于 IBIT 达到同一规模所用的 43 天。"
            "DRAM 的卖点是聚焦 AI 基础设施所需的存储芯片、内存与数据存储相关公司，因此这条信息更多反映 AI 硬件主题 ETF 的资金拥挤度。"
        )
    elif "private equity" in lower and ("401k" in lower or "401 k" in lower):
        parts.append(
            "文章讨论私募股权是否适合进入 401K 这类退休账户。事实层面，私募股权与普通公募基金的主要差异在于流动性更低、估值频率更慢、费用层级更复杂，且底层资产透明度较弱。"
            "这类产品如果进入退休账户，核心问题不是“是否另类资产更高级”，而是普通退休投资者是否理解锁定期、估值滞后和费用拖累。"
        )
    elif "trend following" in lower or "regime-dependent" in lower:
        parts.append(
            "文章讨论趋势跟踪与状态依赖配置，重点是组合权重不必始终维持固定比例，而可以根据市场状态改变风险资产、避险资产和现金类资产的暴露。"
            "这类框架通常需要明确趋势指标、状态划分、再平衡频率和信号滞后，否则容易把事后解释误当成可执行规则。"
        )
    elif "animal spirits" in lower or "stock market is doing something" in lower:
        parts.append(
            "文章讨论股市出现少见强势走势时的市场心理。核心事实是，当行情快速走强时，投资者容易把近期上涨外推成后续收益预期，风险偏好会随价格本身上升而强化。"
            "这类内容适合作为市场情绪观察，而不应直接等同于买入或卖出信号。"
        )
    elif "institutional investor attention" in lower or ("macro news" in lower and "volatility" in lower):
        parts.append(
            "文章基于机构投资者实际在线阅读行为来研究“注意力”如何影响基金决策。"
            "论文发现，当总体波动率升高时，机构会把注意力从个股新闻更多转向宏观和市场层面的新闻。"
        )
        if "0.48%" in lower or "1.9%" in lower:
            parts.append("文中还给出量化结果：宏观注意力切换能力较高的基金，未来表现约高出 0.48%/季，折合约 1.9% 年化，且这种差异在高波动环境中更明显。")
        if "stocks they own" in lower or "position and trading decisions" in lower:
            parts.append("文章还指出，基金会更关注自己持有的股票，这种注意力有助于提升仓位管理和交易决策的价值。")
    elif "nuclear" in lower:
        parts.append(
            "文章关注美国核能主题的产业进展：Brookfield Asset Management 与 The Nuclear Company 合作推进 Westinghouse AP1000 和 AP300 反应堆部署，Blue Energy 与 GE Vernova 则推进天然气加核能的混合方案。"
            "文章还提到新的 Gallup 民调显示美国公众对核能支持度处于高位，这些因素共同构成核能主题 ETF 的基本面叙事。"
            "VettaFi Nuclear Renaissance Index（NUKZX）覆盖反应堆技术、设备供应和服务公司，并作为 Range Nuclear Renaissance Index ETF（NUKZ）的底层指数。"
        )
    elif "securitization" in lower:
        parts.append(
            "文章是关于证券化投资的访谈，嘉宾来自 Janus Henderson Investors，主题包括证券化产品如何运作、CLO 投资、证券化市场规模，以及固定收益投资方式的变化。"
            "从资产配置角度看，证券化资产本质上是把贷款、应收账款或其他现金流资产打包后形成的信用暴露，收益来源和风险都不同于单纯持有国债。"
        )
    elif "melt-up" in lower:
        parts.append(
            "文章讨论美股可能进入 melt-up（快速上冲）阶段：S&P 500 在 3 月底年内仍下跌约 7%，随后反弹到 2026 年内上涨接近 9%。"
            "作者把这种行情与 AI 交易升温、市场迅速消化地缘政治担忧联系起来，重点是价格上涨本身可能继续吸引追涨资金，形成短期动量强化。"
        )
    elif "hedge fund" in lower or "bearish" in lower:
        parts.append(
            "文章讨论为什么许多知名对冲基金经理经常公开表达偏空观点。文中提到 Ray Dalio、Paul Tudor Jones、Stanley Druckenmiller 等人长期有过谨慎或偏空预测，但这并不代表他们的基金完全按这些宏观判断单边下注。"
            "文中引用 Tudor Jones 对市场估值的担忧，包括美国股市总市值/GDP 达到约 252%、S&P 500 在 22 倍 PE 附近买入时十年前瞻收益可能偏低等观点。"
            "作者强调，这些人更像交易者而非买入并长期持有的投资者，因此他们的公开叙事、实际仓位和长期资产配置含义需要分开看。"
        )
    elif "recent quant links from quantocracy" in title_lower:
        parts.append(
            "Quantocracy 是量化文章聚合入口，本条本身不是一篇完整研究，而是一组近期量化链接。日报应把它当作线索池：只挑出能落到信号定义、数据源、交易成本、回测窗口或风险控制的子议题。"
            "如果链接池没有明确可测试问题，就不应把它写成市场结论。"
        )
    elif "commodity futures returns since 1871" in lower:
        parts.append(
            "Quantpedia 文章围绕 1871 年以来商品期货收益指数展开，价值在于把商品暴露放到更长历史中观察，而不是只看近几十年的 ETF 样本。"
            "这类材料适合检查商品风险溢价、通胀对冲、股票/债券相关性和滚动收益在不同制度环境中的表现。"
        )
    elif "dual momentum allocation between physical gold and bitcoin" in lower:
        parts.append(
            "Quantpedia 文章讨论在实物黄金与比特币之间做双动量配置。核心不是把比特币简单视为“数字黄金”，而是检验两类资产在趋势、波动率、回撤和危机相关性上的差异。"
            "对 ETF 组合而言，相关代理通常会落到 GLDM/IAU 一类黄金 ETF 与 IBIT 等现货比特币产品。"
        )
    elif "surfing the equity curve" in lower:
        parts.append(
            "Allocate Smartly 文章讨论用策略自身权益曲线的趋势来决定策略开关。这个想法本质上是对策略做二级趋势过滤：策略表现处于上行状态时启用，权益曲线走弱时暂停或降权。"
            "风险点在于权益曲线过滤容易过拟合，且可能在震荡期反复开关，必须把延迟、换手和错过反弹一起纳入回测。"
        )
    elif "active etfs win the liquidity race" in title_lower:
        parts.append(
            "文章讨论主动 ETF 相比传统共同基金在交易流动性上的优势，重点变量包括盘中交易、成交量、买卖价差和 ETF 包装本身带来的交易便利。"
            "它不是资本市场预期文章，不能据此推断股债长期收益；更适合放在 ETF 产品结构、交易执行和流动性评估框架里。"
        )
    elif "economic policy uncertainty and aggregate economic activity in india" in title_lower:
        parts.append(
            "FRED Blog 文章讨论印度经济政策不确定性与总体经济活动之间的关系，属于宏观数据解释而不是因子轮动研究。"
            "对资产配置的用途是观察印度/新兴市场风险溢价、增长预期和政策不确定性是否影响区域股票或债券配置。"
        )
    elif "stock market prediction using node transformer" in title_lower:
        parts.append(
            "arXiv 论文标题显示其研究 Node Transformer 架构结合 BERT 情绪分析用于股市预测。"
            "日报只能把它作为机器学习预测方法线索；在没有复现代码、样本外结果和交易成本验证前，不能把它写成有效因子结论。"
        )
    elif "縮短香港股票現貨市場結算週期" in title or "shortening hong kong stock settlement cycle" in lower:
        parts.append(
            "HKEX 发布缩短香港股票现货市场结算周期的咨询文件。核心事实是交易后结算安排可能变化，影响券商、托管、资金调拨和跨市场交易流程。"
            "这属于市场结构和交易规则变化，不应被写成一般资产配置观点。"
        )
    elif "capital market" in lower or "expected return" in lower:
        parts.append("文章围绕长期资本市场假设、估值和预期收益展开，重点是不同资产类别未来回报与风险补偿的变化。")
    elif "treasury" in lower or "duration" in lower or "bond" in lower or "yield" in lower:
        parts.append("文章关注债券、收益率或久期变化，核心事实是利率路径会直接影响长债、短债和信用债 ETF 的价格弹性。")
    elif "factor" in lower or "momentum" in lower or "value" in lower or "quality" in lower:
        parts.append("文章讨论因子或风格表现，重点在价值、动量、质量等风险因子是否继续获得市场补偿。")
    elif "commodity" in lower or "gold" in lower or "inflation" in lower:
        parts.append("文章关注商品、黄金或通胀相关资产，核心事实是实物资产的表现通常与通胀预期、美元和实际利率有关。")
    elif text:
        parts.append("正文未提取到足够可核验内容；日报不据此归纳文章主旨。")
    else:
        parts.append("RSS 未提供可核验摘要；日报不据此归纳文章主旨。")

    return "".join(parts)


def etf_follow_up_point(title: str, summary: str = "") -> str:
    text = (title + " " + summary).lower()
    title_lower = title.lower()
    if "active etfs win the liquidity race" in title_lower:
        return "可把它用于 ETF 执行质量观察：比较主动 ETF 与同类基金的成交量、买卖价差、折溢价和大额申赎压力，而不是推导长期资产收益。"
    if "economic policy uncertainty and aggregate economic activity in india" in title_lower:
        return "可作为印度/新兴市场宏观风险变量，后续应和 INDA、EPI、印度债券或新兴市场 ETF 的收益、波动和资金流交叉验证。"
    if "stock market prediction using node transformer" in title_lower:
        return "只作为机器学习预测论文线索；需要先复现数据、样本外窗口、交易成本和基准比较，不能直接进入组合信号。"
    if "縮短香港股票現貨市場結算週期" in title or "shortening hong kong stock settlement cycle" in text:
        return "可跟踪结算周期变化对港股 ETF、互联互通、现金调拨和交易执行的影响，尤其是跨市场再平衡时的资金占用。"
    if "tactical yield" in title_lower:
        return "可把短债/现金、IEF/VGLT 久期暴露和 LQD/HYG 信用暴露放到同一收益率门槛框架里，检验现金收益率升高时是否应降低久期或信用风险。"
    if "recent quant links from quantocracy" in title_lower:
        return "只作为研究线索池使用，后续需要打开子链接并提取可复现规则；没有明确规则的条目不进入策略结论。"
    if "commodity futures returns since 1871" in title_lower:
        return "可用于复核商品袖珍仓位的长期角色：通胀冲击、股债双跌、美元走强和期限结构变化下，PDBC/商品代理是否仍有分散化价值。"
    if "dual momentum allocation between physical gold and bitcoin" in title_lower:
        return "可把 GLDM 与 IBIT 放入同一动量/波动率框架，比较双动量是否比固定权重或单纯黄金避险更稳健。"
    if "attention factor" in title_lower and ("crypto" in title_lower or "bitcoin" in text or "btc" in text):
        return "可检查组合中是否存在间接加密/投机情绪暴露，例如社交情绪 ETF、零佣金券商、加密交易平台或高投机小盘主题。"
    if "surfing the equity curve" in text:
        return "可测试策略级别的权益曲线开关，但必须把信号滞后、反复开关和交易成本纳入，否则容易只是在拟合历史回撤。"
    if "world markets watchlist" in text:
        return "可用来检查美股、海外股票、债券、商品和美元是否出现同步转向，避免只从单一 ETF 解释市场状态。"
    if "weekly economic snapshot" in text and "labor market" in text:
        return "可重点观察就业数据是否改变降息预期，并映射到长债、信用债、小盘股和周期行业的相对表现。"
    if "hits $6.5 billion" in text or "record pace" in text:
        return "可把它当作主题 ETF 资金拥挤度线索，和估值、成交量及主题成分股集中度一起看。"
    if "private equity" in text and "401k" in text.replace(" ", ""):
        return "可作为另类资产进入退休账户的风险提示，重点看锁定期、估值滞后、费用层级和资产透明度。"
    if "trend following" in text or "regime-dependent" in text:
        return "可与现有动量/趋势规则对照，关注状态定义、再平衡频率和换手成本是否可复现。"
    if "animal spirits" in text or "stock market is doing something" in text:
        return "可作为风险偏好温度计，重点看上涨是否由盈利、估值扩张还是流动性推动。"
    if "institutional investor attention" in text or ("macro news" in text and "volatility" in text):
        return "如果把它转成组合研究问题，重点不是直接交易新闻，而是测试高波动阶段宏观变量是否能改善风险开关或仓位调整。"
    if "nuclear" in text:
        return "可观察核能主题 ETF 的上涨是否由产业订单、政策支持或 AI 电力需求驱动，而不是只看主题热度。"
    if "securitization" in text:
        return "可把它放在信用配置框架里看，重点比较收益补偿、底层资产质量和流动性风险。"
    if "melt-up" in text:
        return "可作为风险偏好升温信号，重点检查组合是否因追涨而偏离原来的再平衡纪律。"
    if "hedge fund" in text or "bearish" in text:
        return "可把机构观点和实际价格、持仓、资金流分开看，避免把公开表态直接当成可交易信号。"
    if "treasury" in text or "duration" in text or "bond" in text or "yield" in text:
        return "可跟踪长久期国债、短债、投资级信用债和高收益债之间的相对表现，观察利率风险和信用风险哪个在主导。"
    if "factor" in text or "momentum" in text or "value" in text or "quality" in text:
        return "可把它归入风格轮动观察，重点比较因子 ETF 相对宽基指数的持续性，而不是只看单日涨跌。"
    if "commodity" in text or "gold" in text or "inflation" in text:
        return "可与美元、实际利率和通胀预期一起观察，判断商品或黄金表现来自避险、通胀还是流动性因素。"
    if "flow" in text or "aum" in text or "expense ratio" in text:
        return "可作为产品热度和结构变化线索，但需要和价格表现、成交量及持仓暴露分开看。"
    if "rebalance" in text or "allocation" in text or "portfolio" in text:
        return "可用于更新再平衡观察清单，重点看资产类别权重变化是否来自价格漂移还是主动配置判断。"
    return "可先作为研究线索保留，后续只有在能落到具体资产类别、可观测指标和回测窗口时再进入策略验证。"


def split_article_sentences(text: str) -> list[str]:
    text = clean_text(text, 20000)
    if not text:
        return []
    chunks = re.split(r"(?<=[.!?。！？])\s+", text)
    out: list[str] = []
    for chunk in chunks:
        sentence = clean_text(chunk, 420)
        if len(sentence) < 35:
            continue
        lower = sentence.lower()
        if lower.startswith(("subscribe", "advertisement", "share this", "copyright")):
            continue
        if any(noise in lower for noise in ["appeared first on", "the post ", "privacy policy", "cookie"]):
            continue
        out.append(sentence)
    return out


def detail_sentence_score(sentence: str) -> int:
    lower = sentence.lower()
    score = 0
    keyword_weights = [
        (("backtest", "test", "tested", "sample", "out-of-sample", "historical"), 10),
        (("turnover", "transaction cost", "cost", "fee", "slippage", "rebalance"), 10),
        (("drawdown", "volatility", "risk", "whipsaw", "missed rebound", "overfit", "overfitting"), 10),
        (("annualized", "cagr", "sharpe ratio", "max drawdown", "calmar ratio"), 12),
        (("momentum", "trend", "moving average", "lookback", "signal", "filter", "allocation"), 8),
        (("expected return", "valuation", "yield", "duration", "credit spread", "inflation"), 8),
        (("etf", "portfolio", "commodity", "gold", "bitcoin", "treasury", "equity", "bond"), 6),
        (("conclusion", "find", "found", "result", "reports", "warns"), 6),
    ]
    for keys, weight in keyword_weights:
        if any(k in lower for k in keys):
            score += weight
    if "benchmarks reveal" in lower or "46.66%" in lower or "77.49%" in lower:
        score += 35
    if "79.91%" in lower or "43.94%" in lower or "44.14%" in lower:
        score += 25
    if re.search(r"\b\d+(?:\.\d+)?(?:\s*|-)?(?:%|bps|bp|years?|months?|days?|x)\b", lower):
        score += 12
    if re.search(r"\b(19|20)\d{2}\b", lower):
        score += 8
    return score


def chinese_detail_prefix(sentence: str) -> str:
    lower = sentence.lower()
    labels: list[str] = []
    if any(k in lower for k in ["backtest", "tested", "sample", "out-of-sample", "historical"]):
        labels.append("回测/样本")
    if any(k in lower for k in ["moving average", "lookback", "signal", "filter", "trend", "momentum"]):
        labels.append("信号定义")
    if any(k in lower for k in ["turnover", "transaction cost", "fee", "slippage", "rebalance"]):
        labels.append("交易成本")
    if any(k in lower for k in ["drawdown", "volatility", "risk", "whipsaw", "overfit", "missed rebound"]):
        labels.append("风险控制")
    if any(k in lower for k in ["expected return", "valuation", "yield", "duration", "credit spread", "inflation"]):
        labels.append("配置变量")
    if any(k in lower for k in ["warn", "caution", "limitation", "overfit"]):
        labels.append("限制条件")
    return " / ".join(dict.fromkeys(labels)) or "正文要点"


def translate_detail_terms(sentence: str) -> str:
    replacements = [
        ("maximum drawdown", "最大回撤"),
        ("max drawdown", "最大回撤"),
        ("drawdown", "回撤"),
        ("turnover", "换手"),
        ("transaction costs", "交易成本"),
        ("transaction cost", "交易成本"),
        ("whipsaw risk", "反复开关风险"),
        ("missed rebound", "错过反弹"),
        ("overfitting", "过拟合"),
        ("overfit", "过拟合"),
        ("moving average", "移动均线"),
        ("lookback", "回看窗口"),
        ("trend filter", "趋势过滤器"),
        ("equity-curve", "权益曲线"),
        ("equity curve", "权益曲线"),
        ("tactical allocation", "战术资产配置"),
        ("volatility", "波动率"),
        ("rebalance", "再平衡"),
        ("rebalancing", "再平衡"),
    ]
    out = sentence
    for old, new in replacements:
        out = re.sub(re.escape(old), new, out, flags=re.I)
    return out


def detail_sentence_chinese_summary(sentence: str) -> str:
    lower = sentence.lower()
    nums = ", ".join(re.findall(r"-?\d+(?:\.\d+)?(?:\s*|-)?(?:%|bps|bp|years?|months?|days?|x)", sentence, flags=re.I))
    if "weekly realized" in lower and "465" in lower and "s&p 500" in lower:
        return "论文摘要给出明确样本：使用 2015-2025 年 465 只 S&P 500 股票的周度已实现波动率，检验波动率预测能否改善组合表现。"
    if "graphsage" in lower or ("har" in lower and "lstm" in lower and "baselines" in lower):
        return "论文把 HAR、LSTM 等基准模型与 GraphSAGE 网络模型比较，网络特征包括滚动相关性、行业相似度和供应链关系。"
    if "backtested results from 1930" in lower:
        return "文章回测从 1930 年开始，并把结果与 50% IEF / 50% LQD 的中期国债加公司债基准组合比较。"
    if "results are net of" in lower and ("transaction" in lower or "cost" in lower):
        return "文章报告的回测结果已经扣除交易成本，因此更接近可执行策略评估，而不是无成本纸面收益。"
    if "initial 10-year yield" in lower and "86%" in lower:
        return "文章强调 10 年期初始收益率对后续 10 年总回报有很强解释力，文中给出的解释度约为 86%。"
    if "tactical yield" in lower and ("t-bills" in lower or "duration" in lower or "credit" in lower):
        return "文章的核心规则是用 Tactical Yield 判断何时持有 T-Bills，何时承担久期或信用风险。"
    if "commodity" in lower and "risk-free" in lower and ("5.4%" in lower or "6%" in lower):
        return "文章给出商品期货相对无风险收益率的长期回报证据：年化风险溢价约为 5.4%，真实收益溢价超过 6%。"
    if ("equities" in lower or "equity" in lower or "stocks" in lower) and ("cash" in lower or "6.8%" in lower):
        return "文章把商品风险溢价与股票收益做对比：同期股票相对现金的超额收益约为 6.8%，用于衡量商品风险溢价的量级。"
    if "commodity" in lower and "risk-free" in lower and ("equities" in lower or "equity" in lower or "stocks" in lower):
        return f"文章把商品期货与无风险收益率和股票收益做横向比较：商品期货相对无风险收益率的年化风险溢价约为 5.4%，真实收益溢价超过 6%，同期股票相对现金约为 6.8%。"
    if "average annual risk premium" in lower and "commodity" in lower:
        suffix = f"关键数字：{nums}。" if nums else ""
        return f"文章给出商品期货长期风险溢价证据，强调它不是单纯现货价格暴露；{suffix}"
    if "futures returns" in lower and "spot" in lower:
        return "文章把期货收益拆成现货价格变化和利息调整后的基差，认为商品期货相对现货的超额部分具有跨周期持续性。"
    if "uncorrelated" in lower and "equity risk" in lower:
        return "文章强调商品期货收益驱动与传统股票风险因子相关性较低，因此更适合作为分散化风险溢价观察。"
    if "macro news" in lower and ("volatility" in lower or "uncertainty" in lower):
        return "文章发现高波动/高不确定性阶段，机构投资者会把注意力从个股新闻转向宏观和市场层面新闻。"
    if "0.48%" in lower or "1.9%" in lower:
        return "文章报告宏观注意力切换能力较强的基金未来表现更好，量级约为每季 0.48%、年化约 1.9%。"
    if "stocks they own" in lower or "positions" in lower:
        return "文章指出基金更关注自己持有的股票，这种持仓相关注意力可能改善仓位管理和交易决策。"
    if "regressing bitcoin returns" in lower and "residual connectedness" in lower:
        return "文章在控制全球股票收益和风险偏好后，仍发现 Bitcoin 与部分投机相关股票之间存在残余联动。"
    if "speculative participation" in lower and any(k in lower for k in ["coinbase", "robinhood", "draftkings", "buzz"]):
        return "文章把 Coinbase、Robinhood、DraftKings、BUZZ 等标的视为投机参与度暴露较高的股票/ETF，用来解释加密情绪向股票市场传导。"
    if "spectrum-based assessment" in lower or "binary crypto yes/no" in lower:
        return "文章主张用连续谱评估投机情绪暴露，而不是把组合简单分成“有加密/无加密”的二元判断。"
    if "speculative cohort" in lower or "0dte" in lower or "commission-free brokerages" in lower:
        return "文章认为边际投机资金的情绪变化会在 BTC、0DTE 期权、零佣金券商和社交情绪股票之间传播，形成共同风险因子。"
    if "switched on and off" in lower and "trend" in lower:
        return "文章测试把每个 TAA 策略按趋势跟踪规则打开或关闭，也就是用策略自身权益曲线做二级过滤。"
    if "combining multiple taa strategies" in lower or "model portfolios" in lower:
        return "文章建议把多个 TAA 策略组合成模型组合，以降低单一策略阶段性失效对总组合的影响。"
    if "dual momentum" in lower and "annualized return" in lower and "maximum drawdown" in lower:
        pairs = re.findall(r"(dual momentum|bitcoin buy-and-hold|gold alone)[^.]*?annualized return of (-?\d+(?:\.\d+)?%)[^.]*?maximum drawdown of (-?\d+(?:\.\d+)?%)", lower, flags=re.I)
        if pairs:
            labels = {
                "dual momentum": "双动量策略",
                "bitcoin buy-and-hold": "比特币买入持有",
                "gold alone": "单独黄金",
            }
            parts = [f"{labels.get(name.lower(), name)}年化收益 {ret}、最大回撤 {dd}" for name, ret, dd in pairs]
            return "文章比较不同情景的收益回撤：" + "；".join(parts) + "。"
        return f"文章比较不同情景的年化收益和最大回撤，关键数字包括：{nums}。"
    if "bitcoin buy-and-hold" in lower and "annualized return" in lower and "maximum drawdown" in lower:
        return f"比特币买入持有情景的收益更高但回撤更深，文中给出的年化收益和最大回撤为：{nums}。"
    if "gold alone" in lower and "annualized return" in lower and "maximum drawdown" in lower:
        return f"单独黄金情景的收益和回撤更低，文中给出的年化收益和最大回撤为：{nums}。"
    if "annualized return" in lower and "maximum drawdown" in lower and ("bitcoin" in lower or "gold" in lower):
        return f"文章围绕黄金、比特币或双动量组合比较年化收益和最大回撤，关键数字包括：{nums}。"
    if "benchmarks reveal" in lower and "bitcoin" in lower and "gold" in lower:
        return "文章的基准对比显示：Bitcoin 绝对收益更高但波动和回撤极大，黄金收益较低但更稳定；关键数字包括：" + nums + "。"
    if "max drawdown" in lower and ("sharpe" in lower or "calmar" in lower or "volatility" in lower):
        if not nums:
            return ""
        return f"文章表格给出多种情景的绩效指标，包括波动率、Sharpe Ratio、最大回撤和 Calmar Ratio；关键数字包括：{nums}。"
    if ("cagr" in lower or "annualized" in lower) and ("bitcoin" in lower or "gold" in lower or "50/50" in lower):
        return f"文章表格比较 Bitcoin、黄金和组合情景的年化收益/波动率等指标；关键数字包括：{nums}。"
    if "drawdown" in lower or "whipsaw" in lower or "missed rebound" in lower:
        suffix = f"关键度量：{nums}。" if nums else ""
        return f"文章把最大回撤、反复开关风险、错过反弹和换手成本作为评估重点。{suffix}"
    if "moving average" in lower or "lookback" in lower:
        suffix = f"文中涉及的窗口/参数包括：{nums}。" if nums else ""
        return f"文章围绕移动均线、回看窗口或趋势信号定义策略开关规则。{suffix}"
    if "overfit" in lower and ("equity-curve" in lower or "equity curve" in lower or "strategy's own" in lower):
        return "文章警告权益曲线开关容易过拟合，因为过滤器使用的是策略自身历史表现，而不是独立的市场变量。"
    if "recent quant links from quantocracy" in lower or "summary of links" in lower:
        return "这是 Quantocracy 的近期量化链接汇总，本身只适合做线索池；需要继续打开子链接才能形成策略规则。"
    if "bitcoin" in lower and "gold" in lower and "momentum" in lower:
        return "文章比较黄金与比特币的动量配置关系，重点应落到趋势持续性、波动率、回撤和危机相关性。"
    if "bitfinex" in lower or "btc/usd" in lower or "gld" in lower:
        return "文章说明比特币与黄金测试使用 BTC/USD 与 GLD 等可交易代理，并把样本起点、频率对齐和数据连续性作为回测前提。"
    if "risk-adjusted returns" in lower:
        return "文章把讨论落到实际组合构建和风险调整后收益，而不是停留在“比特币是否是数字黄金”的叙事层。"
    if "correlation with risk assets" in lower:
        return "文章提醒比特币在压力阶段可能与风险资产相关性上升，因此不能直接替代黄金的避险角色。"
    if "annual return" in lower and "sharpe" in lower:
        return "文章会用年化收益和 Sharpe Ratio 等指标评估策略效果，不能只看是否降低回撤。"
    if "managing losses" in lower and "trend" in lower:
        return "文章把趋势跟踪的主要价值定位为管理损失，因此权益曲线开关的重点应看回撤和错过反弹的权衡。"
    if "geopolitical" in lower or "supply-chain" in lower or "resource nationalism" in lower:
        return "文章把地缘政治、通胀不确定性、供应链碎片化和资源民族主义列为重新重视商品资产的宏观背景。"

    entities = re.findall(r"\b[A-Z][A-Za-z0-9&./-]{1,12}\b", sentence)
    tickers = [x for x in entities if x.isupper() or x in {"Bitcoin", "Treasuries"}][:5]
    suffix = ""
    if nums:
        suffix += f"关键数字：{nums}。"
    if tickers:
        suffix += f"涉及标的/变量：{', '.join(dict.fromkeys(tickers))}。"
    label = chinese_detail_prefix(sentence)
    if "风险" in label:
        return f"文章这一段强调风险来源、相关性或回撤控制，需要在回测中单独验证。{suffix}"
    if "信号" in label:
        return f"文章这一段涉及信号定义或策略开关规则，需要明确窗口、触发条件和调仓频率。{suffix}"
    if "配置" in label:
        return f"文章这一段讨论影响配置判断的变量，需要映射到可观察数据再使用。{suffix}"
    if "交易成本" in label:
        return f"文章这一段涉及交易成本或再平衡摩擦，不能按无成本策略理解。{suffix}"
    return f"文章这一段提供背景或方法信息；日报只保留其可验证含义，避免直接照搬英文叙述。{suffix}"


def etf_article_detail_points(item: Item, limit: int = 4) -> list[str]:
    if "recent quant links from quantocracy" in item.title.lower():
        return ["这是 Quantocracy 的近期量化链接汇总，本身只适合做线索池；需要继续打开子链接才能形成策略规则。"]
    sentences = split_article_sentences(item.summary)
    if not sentences:
        return []
    ranked = sorted(enumerate(sentences), key=lambda x: (detail_sentence_score(x[1]), -x[0]), reverse=True)
    points: list[str] = []
    seen: set[str] = set()
    seen_details: set[str] = set()
    title_lower = item.title.lower()
    for _idx, sentence in ranked:
        if detail_sentence_score(sentence) < 8 and points:
            continue
        sentence_lower = sentence.lower()
        if "commodity futures returns since 1871" in title_lower and any(
            marker in sentence_lower
            for marker in ["bitcoin", "digital gold", "btc/usd", "gld", "bitfinex", "dual momentum"]
        ):
            continue
        if "dual momentum allocation between physical gold and bitcoin" in title_lower and "commodity futures" in sentence_lower:
            continue
        normalized = norm_title(sentence)
        if normalized in seen:
            continue
        seen.add(normalized)
        detail = detail_sentence_chinese_summary(sentence)
        if not detail:
            continue
        if "commodity futures returns since 1871" in title_lower and any(
            marker in detail for marker in ["比特币", "数字黄金", "黄金与比特币"]
        ):
            continue
        if "attention factor" in title_lower and "gold" in sentence_lower and "momentum" in sentence_lower:
            continue
        if detail.startswith("文章这一段"):
            continue
        detail_key = norm_title(detail)
        if detail_key in seen_details:
            continue
        seen_details.add(detail_key)
        points.append(detail)
        if len(points) >= limit:
            break
    if "commodity futures returns since 1871" in title_lower and not any("43%" in point for point in points):
        points.append("文章还把商品期货与股票做横向比较：商品期货约在 43% 的年份跑赢股票，并在每五个十年中约两个十年跑赢股票。")
    return points[:limit]


def low_information_fact(text: str) -> bool:
    return any(
        marker in text
        for marker in [
            "正文未提取到足够可核验内容",
            "RSS 未提供可核验摘要",
            "RSS 摘要只提供有限信息",
            "当前可确认文章围绕",
        ]
    )


def generic_etf_fact(text: str) -> bool:
    return any(
        marker in text
        for marker in [
            "文章围绕长期资本市场假设、估值和预期收益展开",
            "文章关注债券、收益率或久期变化",
            "文章讨论因子或风格表现",
            "文章关注商品、黄金或通胀相关资产",
        ]
    )


def etf_title_has_specific_signal(item: Item) -> bool:
    title = item.title.lower()
    text = f"{item.title} {item.summary}".lower()
    return any(
        marker in title
        for marker in [
            "tactical yield",
            "commodity futures returns since 1871",
            "dual momentum allocation between physical gold and bitcoin",
            "attention factor",
            "surfing the equity curve",
            "selecting taa strategies based on recent performance",
            "world markets watchlist",
            "active etfs win the liquidity race",
            "economic policy uncertainty and aggregate economic activity in india",
            "stock market prediction using node transformer",
            "volatility forecasts lead to better portfolios",
            "recent quant links from quantocracy",
        ]
    ) or "縮短香港股票現貨市場結算週期" in item.title or "shortening hong kong stock settlement cycle" in text


def etf_has_enough_summary_evidence(item: Item) -> bool:
    fact = etf_chinese_fact(item)
    if low_information_fact(fact) or generic_etf_fact(fact):
        return False
    if etf_title_has_specific_signal(item):
        return True
    return len(etf_article_detail_points(item, limit=2)) >= 2


def forum_thread_summary_points(item: Item, limit: int = 6) -> list[str]:
    if not any(marker in item.source.lower() for marker in ["reddit", "bogleheads", "forum"]):
        return []
    text = clean_text(item.summary, 6000)
    sentences = split_article_sentences(text)
    points: list[str] = []
    seen: set[str] = set()

    def add(point: str) -> None:
        key = norm_title(point)
        if key in seen:
            return
        seen.add(key)
        points.append(point)

    allocation_matches = re.findall(r"\b\d+(?:\.\d+)?%\s*[A-Z][A-Z0-9.-]{1,8}\b", text)
    if allocation_matches:
        add("发帖人给出的当前组合权重包括：" + "、".join(allocation_matches[:8]) + "。")

    lower_text = text.lower()
    if "emergency cash" in lower_text or "emergency fund" in lower_text or "money market" in lower_text:
        cash_match = re.search(r"(\w+\s+months?)\s+of\s+emergency\s+cash", text, flags=re.I)
        if cash_match and cash_match.group(1).lower().startswith("six"):
            add("发帖人另有六个月应急现金，放在货币市场基金或类似现金工具中。")
        else:
            add("发帖人单独提到应急现金或货币市场基金，这部分应与长期投资组合分开看。")
    if "buying a house in three years" in lower_text or "house in three years" in lower_text:
        add("发帖人有三年内买房这一资金用途，因此首付资金不应和长期股票仓位混在一起评价。")
    if "401(k)" in text or "401k" in lower_text or "tax-advantaged" in lower_text or "tax advantaged" in lower_text:
        add("帖子讨论 taxable account 与 401(k) 的协调，尤其是债券仓位是否优先放在税优账户。")
    if "international allocation" in lower_text or "vxus" in lower_text:
        add("讨论焦点之一是海外股票比例是否偏低，以及是否因为美股近期强势而低配国际资产。")
    if "schg" in lower_text and "qqqm" in lower_text:
        add("帖子围绕 SCHG 与 QQQM 的长期持有选择展开，核心是成长风格 ETF 与纳斯达克 100 ETF 的重叠和集中度。")
    if "overlap" in lower_text and ("concentrated" in lower_text or "growth exposure" in lower_text):
        add("回复关注两只 ETF 的持仓重叠是否会造成成长股暴露过度集中，而不是简单比较近期收益。")
    if "long-term core" in lower_text or "long term core" in lower_text or "rebalance" in lower_text:
        add("讨论还涉及这类 ETF 是否适合作为长期核心仓位，以及未来再平衡时如何处理风格漂移。")
    if "not to chase" in lower_text or "recent us stock outperformance" in lower_text:
        add("回复中的主要提醒是不要因为近期美股跑赢就追涨或放弃既定的全球分散配置。")
    if "100%" in lower_text and "0%" in lower_text and ("stocks/funds" in lower_text or "stocks" in lower_text) and (
        "bonds" in lower_text or "treasuries" in lower_text
    ):
        add("发帖人的核心问题是：离退休还很远时，退休储蓄是否可以几乎 100% 放在股票/基金、0% 放在债券或国债。")
    if "far from retirement" in lower_text or "time horizon" in lower_text:
        add("讨论把“离退休还很远”和投资期限作为主要前提：期限越长，股票波动的可承受度通常越高，但不等于可以忽略大回撤。")
    if "risk tolerance" in lower_text:
        add("回复把风险承受能力放在核心位置：能否在深度回撤中坚持 100% 股票，比理论上的退休年限更关键。")
    if "reducing drawdowns" in lower_text or "drawdowns" in lower_text:
        add("债券或国债的作用被归纳为降低组合回撤、提供再平衡资金和心理缓冲，而不是追求最高长期收益。")
    if "near-term cash" in lower_text or "cash needs" in lower_text:
        add("近期用钱应与退休资产分开管理，不能用 100% 股票配置去承担短期资金需求。")
    if "cash" in lower_text and ("short term bonds" in lower_text or "short-term bonds" in lower_text):
        add("发帖人的核心问题是：现金仓位是否该转入短期债券，需要比较现金收益率、短债久期风险、税后收益和资金使用期限。")
    if "money market" in lower_text or "cash yield" in lower_text:
        add("讨论把货币市场基金或现金收益率作为基准，短债只有在税后收益和流动性补偿足够时才值得替代现金。")
    if "duration risk" in lower_text and ("short term bond" in lower_text or "short-term bond" in lower_text):
        add("短期债券仍有久期风险，利率上行时可能出现价格波动；它不是无风险现金等价物。")
    if "liquidity" in lower_text and ("needed soon" in lower_text or "needed" in lower_text):
        add("如果资金近期要用，流动性和本金稳定性应优先于多拿一点短债收益。")
    if "glide path" in lower_text or "retirement approaches" in lower_text:
        add("一种可研究路径是随临近退休逐步增加债券，形成退休前逐步降风险路径，而不是永久维持 0% 债券。")

    def forum_sentence_point(sentence: str) -> str:
        lower = sentence.lower()
        if "do not own real estate" in lower and "us/international/bonds" in lower:
            return "发帖人质疑 Bogleheads 常见的 US/International/Bonds 三分法是否忽略了未持有房产者的 REIT 暴露。"
        if "rick ferri" in lower or "paul merriman" in lower or "model portfolios" in lower:
            return "回复提到 Rick Ferri、Paul Merriman 等模型组合会单列 REIT 或小盘等资产，用来说明 Bogleheads 实践并不只有三类资产。"
        if "after setting aside emergency funds" in lower:
            return "发帖人已预留应急资金和近期支出，讨论对象是剩余资金是否适合一次性投入长期组合。"
        if "diversified enough" in lower or "good to go" in lower:
            return "发帖人主要想确认拟定组合是否已经足够分散、是否可以作为长期核心配置。"
        if "ucits-based moderate core" in lower or "global aggregate bonds" in lower:
            return "回复建议把 UCITS 组合简化为发达市场、新兴市场、小盘和全球综合债的核心结构，而不是堆太多重叠 ETF。"
        if "what stocks to sell" in lower and "portfolio" in lower:
            return "发帖人请求根据当前持仓判断哪些个股应卖出，说明该帖更偏组合清理而不是单纯 ETF 配置讨论。"
        if "shift" in lower and "portfolio" in lower and "dividends" in lower:
            return "发帖人想知道达到目标后，如何把组合从资本增长逐步转向依靠分红或现金流。"
        if "portfolio" in lower and ("allocation" in lower or "bond" in lower or "cash" in lower or "taxable" in lower or "401" in lower):
            return "原帖围绕组合权重、债券/现金比例、税务账户摆放或再平衡问题展开。"
        return ""

    for sentence in sentences:
        if len(points) >= limit:
            break
        lower = sentence.lower()
        if "rss only says" in lower:
            continue
        if any(k in lower for k in ["portfolio", "allocation", "bond", "cash", "taxable", "401", "expense", "rebalance"]):
            point = forum_sentence_point(sentence)
            if point:
                add(point)
    return points[:limit]


def forum_title_subject(title: str) -> str:
    subject = clean_text(title, 220)
    match = re.match(r"(.{2,80}?)(?:\s*[•?]\s*|\s+)Re:\s*(.+)", subject, flags=re.I)
    if match:
        section = clean_text(match.group(1), 80)
        topic = clean_text(match.group(2), 180)
        if section and topic:
            return f"{section} • Re: {topic}"
    return subject


def forum_subject_text(title: str) -> str:
    subject = forum_title_subject(title)
    if "• Re:" in subject:
        return clean_text(subject.split("• Re:", 1)[1], 180)
    return subject


GENERIC_FORUM_HEADINGS = {
    "寻求投资组合建议",
    "税优账户中是否应单列 REIT/VNQ",
    "30 多岁中等风险组合求评",
    "投资组合持仓清理求建议",
    "组合目标达成后如何转向现金流",
    "投资组合配置求评",
    "债券配置与风险认知讨论",
    "投资组合配置问题求评",
    "Bogleheads 配置问题求评",
    "税务账户与退休账户配置问题",
    "税务/账户/医保阈值相关问题",
}
LIGHTWEIGHT_FORUM_TITLE_ONLY_BLOCKLIST = {
    "投资组合建议请求",
}


def generic_forum_heading(heading: str) -> bool:
    return heading in GENERIC_FORUM_HEADINGS or heading.endswith("相关问题")


def specific_forum_title_translation(title: str, summary: str = "") -> str:
    subject = forum_title_subject(title)
    topic = forum_subject_text(title)
    title_lower = topic.lower()
    subject_lower = subject.lower()
    text = f"{topic} {summary}".lower()
    exact_title_translations = {
        "my parents have paid their financial advisor roughly $47k in fees over 15 years for market returns": "父母 15 年来为接近市场回报向财务顾问支付约 4.7 万美元费用",
        "my sister has been an edward jones broker for more than two decades and not one family member has even $1 invested there.": "姐姐做了二十多年 Edward Jones 经纪人，但家人没有一美元投在那里",
        "real estate or s&p 500. honestly, what‘s the better investment for you?": "房地产还是标普 500：对你来说哪个投资更好？",
        "s&p 500 index not so diversified": "标普 500 指数是否并没有那么分散？",
        "psa- mega ipos are nothing to worry about as an index investor": "提醒：作为指数投资者不必过度担心大型 IPO",
        "people who bought stocks early when they were still risky, unpopular, or getting hated on, what made you buy?": "早期买入仍有风险、不受欢迎或被嫌弃股票的人，当初为什么买？",
        "protecting ourselves from spacex ipo": "如何防范 SpaceX IPO 对指数组合的影响？",
        "spacex ipo and nasdaq violating its own methodology": "SpaceX IPO 与纳斯达克是否违背自身指数方法论",
        "should i stop contributing to 401k and ira": "我是否应该停止向 401(k) 和 IRA 供款？",
        "how much cash is too much?": "现金持有多少算太多？",
        "what would the collapse of the bond market mean for stocks?": "债券市场崩溃对股票意味着什么？",
        "i don't want to rebalance, what's your take?": "我不想再平衡组合，你怎么看？",
        "19yo incoming college freshman; doubled money earned from internship last summer within first year of investing": "19 岁准大学新生：入市第一年把去年实习收入翻倍",
        "how’s the portfolio looking now? 26m": "26 岁男性：现在这个投资组合看起来怎么样？",
        "how to get over the fomo from all the other investing subreddits?": "如何克服其他投资社区带来的错失恐惧？",
        "what should i change or upgrade on my portfolio?": "我的投资组合应该调整或升级什么？",
        "so where do i convert to bonds?": "我应该在什么位置转向债券？",
        "re-balance question": "关于投资组合再平衡的问题",
        "age 22 any recommendations?": "22 岁投资组合有什么建议？",
        "the latest morningstar report shows how to invest in 2026": "Morningstar 最新报告：2026 年应如何投资？",
        "transition to 3 fund portfolio": "过渡到三基金组合",
        "overall index of portfolios": "投资组合样本总索引",
        "lazy portfolios": "懒人投资组合样本",
        "22m taxable brokerage portfolio review, inquisitive about barbell growth and macro diversifier strategy": "22 岁男性应税券商账户组合求评：杠铃式成长与宏观分散策略是否合适？",
        "recently opened self-managed brokerage and my roth ira strategy": "新开自主管理券商账户与 Roth IRA 策略求评",
        "help diversify portfolio": "如何让投资组合更加分散？",
        "personal investments • re: dividend investing or not?": "个人投资：是否应该做股息投资？",
        "personal investments • re: when should i create tips ladder?now or wait": "个人投资：什么时候建立 TIPS 阶梯？现在还是等待？",
        "personal investments • re: time to move \"cash\" to short term bonds?": "个人投资：是否该把现金转到短期债券？",
        "voo vs. voo/vxus?": "VOO 单独持有，还是 VOO + VXUS 加入国际股票？",
        "what are the best bonds for high income earners?": "高收入者适合配置哪些债券？",
        "80k to invest + no debt how would you invest it?": "无债且有 8 万美元待投资资金，该如何配置？",
        "rebuilding entire portfolio with boglehead strategy as primary influence.": "以 Bogleheads 策略为核心重建整个投资组合",
        "rebuilding portfolio and need help (46m/44f) 3.5m investable assets": "46 岁/44 岁家庭重建 350 万美元可投资资产组合求助",
        "help me analyzing this portfolio and suggestions for improvement": "请帮我分析这个投资组合并给出改进建议",
        "unwind unrealized gains with taxable account": "应税账户里如何处理未实现资本利得？",
        "where to invest next?": "下一步应该投向哪里？",
        "target date fund etf in brokerage account?": "应税券商账户里能否买目标日期基金 ETF？",
        "need help consolidating my beginner portfolio": "新手投资组合需要合并整理，求帮助",
        "54 and finally waking up": "54 岁终于开始认真规划投资组合",
        "at what point did you diversify not only in vtsax/voo": "什么时候开始从 VTSAX/VOO 之外进一步分散配置？",
        "will sptm/sphq and active etfs get forced into hype ipos like spacex?": "SPTM/SPHQ 和主动 ETF 会被迫买入 SpaceX 这类热门 IPO 吗？",
        "personal investments • re: $407k net worth, large cash position, looking for advice on an aggressive investing strategy": "个人投资：净资产 40.7 万美元、现金仓位较大，如何制定更积极的投资策略？",
        "hit $200k milestone this week! also please judge my financial snapshot and budget.": "本周资产达到 20 万美元里程碑，也请评估我的财务快照和预算",
        "personal investments • re: experiences with vanguard \"situational advisor\" (advice-only, one-time)?": "个人投资：Vanguard “Situational Advisor” 一次性建议服务体验如何？",
        "401k advice": "401(k) 配置建议",
        "need advice, starting a portfolio": "刚开始建立投资组合，求建议",
        "three fund advice before ditching robo advisor": "放弃智能投顾前，三基金组合配置求建议",
        "my first pie ;3": "我的第一个投资饼图组合",
        "my first pie ;3 19f": "19 岁女性的第一个投资饼图组合",
        "personal investments • re: buying ishares ibonds term tips etf": "个人投资：是否购买 iShares iBonds 期限 TIPS ETF？",
    }
    if subject_lower in exact_title_translations:
        return exact_title_translations[subject_lower]
    if title_lower in exact_title_translations:
        return exact_title_translations[title_lower]
    forum_prefix = ""
    if "• Re:" in subject:
        forum_prefix = "个人投资：" if subject_lower.startswith("personal investments") else ""
    if "does this make sense" in title_lower and "overengineered portfolio" in title_lower:
        return "这样配置合理吗？组合是否过度设计，还是稳健理性的方案？"
    if "where to invest next" in title_lower:
        return f"{forum_prefix}下一步应该投向哪里？"
    if "target date fund" in title_lower and ("brokerage" in title_lower or "taxable" in text):
        return f"{forum_prefix}应税券商账户里能否买目标日期基金 ETF？"
    if "beginner portfolio" in title_lower and ("consolidating" in title_lower or "help" in title_lower):
        return "新手投资组合需要合并整理，求帮助"
    if re.search(r"\b54\b", title_lower) and "waking up" in title_lower:
        return "54 岁终于开始认真规划投资组合"
    if "diversify" in title_lower and ("vtsax" in title_lower or "voo" in title_lower):
        return "什么时候开始从 VTSAX/VOO 之外进一步分散配置？"
    if "sptm" in title_lower and "sphq" in title_lower and "active etfs" in title_lower and "spacex" in title_lower:
        return "SPTM/SPHQ 和主动 ETF 会被迫买入 SpaceX 这类热门 IPO 吗？"
    if "large cash position" in title_lower and "aggressive investing strategy" in title_lower:
        return f"{forum_prefix}净资产 40.7 万美元、现金仓位较大，如何制定更积极的投资策略？"
    if "$200k" in title_lower and "financial snapshot" in title_lower and "budget" in title_lower:
        return "本周资产达到 20 万美元里程碑，也请评估我的财务快照和预算"
    if "vanguard" in title_lower and "situational advisor" in title_lower:
        return f"{forum_prefix}Vanguard “Situational Advisor” 一次性建议服务体验如何？"
    if "ishares ibonds" in title_lower and "tips etf" in title_lower:
        return f"{forum_prefix}是否购买 iShares iBonds 期限 TIPS ETF？"
    if re.fullmatch(r"\s*401\s*k\s+advice\s*", title_lower):
        return "401(k) 配置建议"
    if "starting a portfolio" in title_lower and "advice" in title_lower:
        return "刚开始建立投资组合，求建议"
    if "three fund" in title_lower and "robo advisor" in title_lower:
        return "放弃智能投顾前，三基金组合配置求建议"
    if "three fund" in title_lower and "advice" in title_lower:
        return "三基金组合配置求建议"
    first_pie_match = re.search(r"\bmy\s+first\s+pie\b", title_lower)
    if first_pie_match:
        age_gender = re.search(r"\b(\d{2})\s*([fm])\b", title_lower)
        if age_gender:
            gender = "女性" if age_gender.group(2) == "f" else "男性"
            return f"{age_gender.group(1)} 岁{gender}的第一个投资饼图组合"
        return "我的第一个投资饼图组合"
    if "tips ladder" in title_lower or "tips ladder" in text:
        return f"{forum_prefix}什么时候建立 TIPS 阶梯？现在还是等待？"
    feedback_age_match = re.search(r"\bportfolio\s+feedback\s+for\s+a\s+(\d{2})\s+year\s+old\b", title_lower)
    if feedback_age_match:
        return f"{feedback_age_match.group(1)} 岁投资组合反馈求评"
    if "managing portfolio investments over time" in title_lower and "signals" in title_lower:
        return "如何长期管理组合投资：信号、直觉还是策略？"
    living_match = re.search(r"\bbogleheads?\s+living\s+in\s+(.+?)\s+-\s+how\s+are\s+we\s+doing\b", title_lower)
    if living_match:
        place_map = {
            "south korea": "韩国",
            "korea": "韩国",
            "canada": "加拿大",
            "uk": "英国",
            "the uk": "英国",
            "japan": "日本",
            "singapore": "新加坡",
            "australia": "澳大利亚",
        }
        place = place_map.get(living_match.group(1).strip(), living_match.group(1).strip().title())
        return f"生活在{place}的 Bogleheads：我们的配置做得怎么样？"
    if "spmo" in title_lower and "vfmo" in title_lower and "voo" in title_lower:
        return "用 SPMO + VFMO 作为美股核心、比典型 VOO 核心多承担一点风险是否合理？"
    if "advice on how to improve my portfolio" in title_lower and "25m" in title_lower:
        return "25 岁男性如何改进当前投资组合？"
    if re.search(r"\b18\b", title_lower) and "$1,000" in title_lower and "what do i do" in title_lower:
        return "18 岁有 1,000 美元，应该怎么开始投资？"
    if "rate the portfolio" in title_lower and "advice" in title_lower:
        return "请评价这个投资组合并给些建议"
    age_match = re.search(r"\bportfolio\s+age\s+(\d{2})\b", title_lower)
    if age_match:
        return f"{age_match.group(1)} 岁投资组合求评"
    if "roth" in title_lower and "traditional" in title_lower and "401" in title_lower:
        return "Roth 401(k) 与传统 401(k) 如何选择？"
    if "sold everything" in title_lower and "rebalance" in title_lower and "etf portfolio" in title_lower:
        return "为重新平衡 ETF 组合卖出全部持仓是否合适？"
    if "dividend investing or not" in title_lower:
        return "是否应该做股息投资？"
    if "what" in title_lower and "etf" in title_lower and "don" in title_lower and "selling" in title_lower:
        return "你不打算长期卖出的 ETF 是哪只？"
    if "90/10 split" in title_lower:
        return "按 WSJ 评论调整为 90/10 股债配置是否合适？"
    if "24m" in title_lower and "new to investing" in title_lower:
        return "24 岁投资新手组合求评"
    if "rate my ind brokerage automatic contributions" in title_lower:
        return "个人券商账户自动定投配置求评"
    if "beginner portfolio help" in title_lower:
        return "新手投资组合求助"
    if "26m" in title_lower and "etf advice" in title_lower:
        return "26 岁投资者 ETF 配置求建议"
    if "best way to migrate" in title_lower and "boglehead portfolio" in title_lower:
        return "如何把多个混乱组合迁移成 Bogleheads 风格组合"
    if re.search(r"\bhow long until i (?:hit|reach|get to|have|make)\s+(?:a\s+)?(?:\$?1\s*million|\$?1m|million)\b", title_lower):
        return "我还要多久才能达到 100 万美元？"
    if "new in etf" in title_lower and "looking for advice" in title_lower:
        return "ETF 新手寻求投资建议"
    if title_lower.strip(" .") == "investment allocations":
        return "投资配置比例讨论"
    if "roast/help my portfolio" in title_lower or ("help my portfolio" in title_lower and "not great at this" in title_lower):
        return "请吐槽或帮我改进投资组合：我不太擅长配置"
    if re.search(r"\b30m\b", title_lower) and "falling behind" in title_lower:
        return "30 岁男性觉得自己的投资进度落后"
    if "allocation across account types" in title_lower:
        return "不同账户类型之间如何分配资产"
    if re.search(r"\brate my portfolio\s*2 months in\b", title_lower):
        return "入市两个月，请评价我的投资组合"
    if "started a taxable investment account" in title_lower:
        return "刚开始应税投资账户，欢迎反馈"
    if "disregard" in title_lower and "bond" in title_lower and ("all equities" in title_lower or "portfolio" in title_lower):
        return "你们中有人完全忽略组合中的债券部分、全仓股票吗？"
    if "portfolio planning" in title_lower and "simplest" in title_lower:
        return "最简单的投资组合规划"
    if "taxable brokerage" in title_lower and ("suggestion" in title_lower or "advice" in title_lower):
        return "应税券商账户有什么建议？"
    if re.search(r"\brate (?:this|my) portfolio(?: please)?\b", title_lower):
        return "请评价我的投资组合"
    if title_lower.strip(" .") == "advice on portfolio":
        return "投资组合建议请求"
    if "personal investments" in subject_lower and "retire at 55" in title_lower and "taxable" in title_lower:
        return "个人投资：55 岁退休且应税账户占比较高"
    if re.search(r"\badd\s+spmo\s+or\s+fmtm\b", title_lower):
        return "添加 SPMO 还是 FMTM？"
    if title_lower.strip(" .?") == "investment suggestion":
        return "投资建议请求"
    if "employer" in title_lower and "safe harbor" in title_lower and "negative" in title_lower:
        return "雇主通过负向缴款撤回了 2026 年 Safe Harbor 匹配缴款"
    if "vanguard advised redundancy" in title_lower:
        return "Vanguard 顾问服务是否重复多余"
    if "really bad" in title_lower and "market events" in title_lower and "test your portfolio" in title_lower:
        return "应该用哪些极端市场事件来压力测试投资组合？"
    if "better performing international funds" in title_lower:
        return "表现更好的国际基金是否值得关注？"
    if "simple portfolio tracking" in title_lower and "right tool" in title_lower:
        return "还在寻找合适的简洁投资组合跟踪工具"
    if "one less boomer getting fleeced by ej" in title_lower:
        return "又少一位被 Edward Jones 高费率产品收割的长辈"
    if "51m at 95/5" in title_lower and "wife wants 70/30" in title_lower:
        return "51 岁 95/5 配置，伴侣想改成 70/30：同龄人实际怎么取舍？"
    if "vti and chill" in title_lower:
        return "还有谁在坚持“VTI and Chill”？"
    if "wealthpie" in title_lower and "spreadsheet" in title_lower:
        return "我做了 WealthPie，因为我的投资表格变得过于复杂"
    if "moving on from aum" in title_lower and "rebalancing" in title_lower:
        return "不再依赖 AUM 顾问后如何再平衡"
    if "vnq" in title_lower and "tax-advantaged" in title_lower:
        return "为什么不在税优账户中用 VNQ 做 REIT 分散？"
    if "schg" in title_lower and "qqqm" in title_lower and "long term" in title_lower:
        return "长期持有选 SCHG 还是 QQQM？"
    if "100%" in text and "0%" in text and ("stocks/funds" in text or "stocks" in text) and (
        "bonds" in text or "treasuries" in text
    ):
        return "离退休还很远，退休储蓄几乎 100% 股票/基金、0% 债券或国债是否可以？"
    return ""


def forum_public_heading(item: Item) -> str:
    specific_heading = specific_forum_title_translation(item.title, item.summary)
    if specific_heading:
        return specific_heading
    subject = forum_title_subject(item.title)
    topic = forum_subject_text(item.title)
    text = f"{topic} {item.summary}".lower()
    title_lower = topic.lower()
    if "looking for portfolio advice" in text:
        return "寻求投资组合建议"
    if "vnq" in title_lower or "reit" in title_lower or "real estate" in title_lower:
        return "税优账户中是否应单列 REIT/VNQ"
    if "portfolio for 30s" in text or "moderate risk" in text:
        return "30 多岁中等风险组合求评"
    if "advice on portfolio" in text or "what stocks to sell" in text:
        return "投资组合持仓清理求建议"
    if "visualize portfolio" in text or "living off dividends" in text:
        return "组合目标达成后如何转向现金流"
    if "rate my portfolio" in text:
        return "投资组合配置求评"
    if "bond allocation" in text or "bonds being safe" in text:
        return "债券配置与风险认知讨论"
    if "portfolio" in text:
        return "投资组合配置问题求评"
    if "bogle" in text:
        return "Bogleheads 配置问题求评"
    if "401" in text or "ira" in text or "roth" in text:
        return "税务账户与退休账户配置问题"
    return f"{chinese_topic(item.title, item.summary)}相关问题"


def forum_display_title(item: Item) -> str:
    original = clean_text(item.title, 180)
    chinese = clean_text(forum_public_heading(item), 180)
    if generic_forum_heading(chinese):
        chinese = ""
    if not original:
        return chinese
    if not chinese or norm_title(original) == norm_title(chinese):
        return original
    return f"{original}（{chinese}）"


def forum_engagement_score(item: Item) -> int:
    text = f"{item.source} {item.summary}".lower()
    score_match = re.search(r"(?:score/upvotes|score|upvotes?)\s*[:：]?\s*([\d,]+)", text)
    comments_match = re.search(r"(?:comments/replies|comments?|replies)\s*[:：]?\s*([\d,]+)", text)
    views_match = re.search(r"(?:views?)\s*[:：]?\s*([\d,]+)", text)
    hot_rank_match = re.search(r"hot rss rank\s*[:#]?\s*(\d+)", text)
    score = int(score_match.group(1).replace(",", "")) if score_match else 0
    comments = int(comments_match.group(1).replace(",", "")) if comments_match else 0
    views = int(views_match.group(1).replace(",", "")) if views_match else 0
    hot_rank = int(hot_rank_match.group(1)) if hot_rank_match else 0
    hot_rank_score = max(0, ETF_FORUM_MIN_ENGAGEMENT_SCORE + 60 - hot_rank * 8) if hot_rank else 0
    return score + comments * 3 + views // 50 + hot_rank_score


def is_reddit_forum_item(item: Item) -> bool:
    return "reddit" in f"{item.source} {item.url}".lower()


def reddit_forum_item_with_engagement(item: Item) -> Item:
    if not is_reddit_forum_item(item) or forum_engagement_score(item) > 0:
        return item
    meta = reddit_thread_metadata(item.url)
    if meta is None:
        return item
    score, comments = meta
    base_source = re.sub(r"\s*\([^)]*score/upvotes[^)]*comments/replies[^)]*\)\s*$", "", item.source).strip()
    source = f"{base_source} (score/upvotes {score}; comments/replies {comments})"
    summary = clean_text(f"{item.summary} Engagement: score/upvotes {score}; comments/replies {comments}.", 1200)
    return Item(source, item.title, item.url, item.published, summary)


def reddit_forum_meets_engagement_bar(item: Item) -> bool:
    if not is_reddit_forum_item(item):
        return True
    return forum_engagement_score(item) >= ETF_FORUM_MIN_ENGAGEMENT_SCORE


def forum_item_meets_quality_bar(item: Item) -> bool:
    if is_reddit_forum_item(item):
        return reddit_forum_meets_engagement_bar(item)
    source = item.source.lower()
    if any(marker in source for marker in ["bogleheads.org forum", "rational reminder community"]):
        return forum_engagement_score(item) >= ETF_FORUM_MIN_ENGAGEMENT_SCORE or "curated" in source
    return True


def low_signal_forum_title(item: Item) -> bool:
    title = item.title.lower()
    low_signal_title_markers = [
        "rate my portfolio",
        "rate my portfolio weekly",
        "rate this portfolio",
        "my first pie",
        "401k advice",
        "need advice",
        "staying on-topic",
        "rude &/or off-topic",
        "rude or off-topic",
        "what are your thoughts on this portfolio",
        "weekly update",
        "add to portfolio",
        "created this percentage system",
        "first year teacher",
    ]
    return any(marker in title for marker in low_signal_title_markers)


def forum_has_specific_summary_evidence(item: Item, points: list[str] | None = None) -> bool:
    points = points if points is not None else forum_thread_summary_points(item)
    text = f"{item.title} {item.summary}".lower()
    if low_signal_forum_title(item):
        return False
    if not points:
        return False
    if not forum_item_meets_quality_bar(item):
        return False
    allocation_matches = re.findall(r"\b\d+(?:\.\d+)?%\s*[A-Z][A-Z0-9.-]{1,8}\b", item.summary)
    if allocation_matches:
        if len(allocation_matches) < 2:
            return False
        return len(points) >= 2
    if "100%" in text and "0%" in text and ("bonds" in text or "treasuries" in text):
        return len(points) >= 3
    if any(k in text for k in ["vnq", "reit", "real estate", "buying a house", "emergency cash", "401(k)", "401k"]):
        return len(points) >= 2
    if "schg" in text and "qqqm" in text:
        return len(points) >= 2
    if "dca" in text and ("treasury" in text or "treasuries" in text):
        return len(points) >= 2
    if "cash" in text and ("short term bonds" in text or "short-term bonds" in text):
        return len(points) >= 2
    return False


def forum_engagement_label(item: Item) -> str:
    text = f"{item.source} {item.summary}".lower()
    score_match = re.search(r"(?:score/upvotes|score|upvotes?)\s*[:：]?\s*([\d,]+)", text)
    comments_match = re.search(r"(?:comments/replies|comments?|replies)\s*[:：]?\s*([\d,]+)", text)
    views_match = re.search(r"(?:views?)\s*[:：]?\s*([\d,]+)", text)
    hot_rank_match = re.search(r"hot rss rank\s*[:#]?\s*(\d+)", text)
    bits: list[str] = []
    if score_match:
        bits.append(f"点赞/评分 {score_match.group(1)}")
    if comments_match:
        bits.append(f"回复 {comments_match.group(1)}")
    if views_match:
        bits.append(f"浏览 {views_match.group(1)}")
    if hot_rank_match:
        bits.append(f"old Reddit hot RSS rank {hot_rank_match.group(1)}")
    return "、".join(bits)


def forum_has_topic_signal(item: Item) -> bool:
    title = item.title.lower()
    text = f"{item.title} {item.summary}".lower()
    if not etf_forum_relevant(item):
        return False
    if low_signal_forum_title(item):
        return False
    if re.fullmatch(r"\s*(?:best\s+)?etfs?\s+to\s+invest\s+in\s*", title):
        return False
    title_markers = [
        "portfolio",
        "allocation",
        "boglehead",
        "schg",
        "qqqm",
        "vti",
        "vxus",
        "bond",
        "rebalance",
        "rate my",
        "beginner portfolio",
        "etf advice",
        "active etf",
        "target date",
        "diversify",
        "vtsax",
        "cash position",
        "aggressive investing",
        "sptm",
        "sphq",
        "don",
        "selling",
        "etfs only",
        "international funds",
        "market events",
        "index inclusion",
        "s&p 500",
        "sp 500",
        "what are you buying",
    ]
    summary_markers = [
        "portfolio",
        "allocation",
        "risk tolerance",
        "rebalance",
        "contribution",
        "long-term",
        "market cycles",
        "boglehead",
        "tax",
        "vti",
        "vxus",
        "schg",
        "qqqm",
        "index fund",
        "s&p 500",
        "sp 500",
        "international funds",
        "withdrawal rate",
    ]
    title_hit = any(marker in title for marker in title_markers)
    summary_hits = sum(1 for marker in summary_markers if marker in text)
    return title_hit or summary_hits >= 2


def forum_has_specific_translated_title_signal(item: Item) -> bool:
    if not forum_has_topic_signal(item):
        return False
    heading = forum_public_heading(item)
    if not heading or generic_forum_heading(heading):
        return False
    if heading in LIGHTWEIGHT_FORUM_TITLE_ONLY_BLOCKLIST:
        return False
    return bool(re.search(r"[\u4e00-\u9fff]", heading))


def forum_has_lightweight_summary_evidence(item: Item, points: list[str] | None = None) -> bool:
    points = points if points is not None else forum_thread_summary_points(item)
    if forum_has_specific_summary_evidence(item, points):
        return True
    if not forum_has_topic_signal(item):
        return False
    if not forum_item_meets_quality_bar(item):
        return False
    return forum_engagement_score(item) >= 100 or bool(points)


def forum_lightweight_summary_points(item: Item, limit: int = 4) -> list[str]:
    points = forum_thread_summary_points(item, limit=limit)
    if forum_has_specific_summary_evidence(item, points):
        return points[:limit]
    if not forum_has_lightweight_summary_evidence(item, points):
        return points[:limit]

    out = list(points)
    heading = forum_public_heading(item)
    if heading and not generic_forum_heading(heading) and not any(heading in point for point in out):
        out.append(f"标题和摘要显示，该帖围绕“{heading}”征集社区观点，适合作为 ETF/资产配置日报的待验证选题。")
    engagement = forum_engagement_label(item)
    if engagement:
        out.append(f"来源显示该帖互动较高（{engagement}）；收录目的只是补充社区关注点，不把回复当作投资结论。")
    return out[:limit]


def forum_history_backfill_summary(title: str) -> str:
    lower = title.lower()
    bits = [
        "Historical Reddit forum backfill item about portfolio allocation, ETF core holdings, risk tolerance, and rebalance decisions.",
    ]
    if "401" in lower or "ira" in lower or "roth" in lower:
        bits.append("The thread also points to retirement account placement, taxable account coordination, and contribution choices.")
    if "cash" in lower:
        bits.append("The discussion is relevant to cash allocation and whether short-term liquidity is too high or too low.")
    if "bond" in lower:
        bits.append("The discussion is relevant to bond allocation, drawdowns, and equity-bond balance.")
    if "bogle" in lower:
        bits.append("The discussion is relevant to Bogleheads-style low-cost diversified portfolio construction.")
    return " ".join(bits)


def forum_history_backfill_items(limit: int = 30) -> list[Item]:
    history = load_digest_history("etf")
    items: list[Item] = []
    seen: set[tuple[str, str]] = set()
    for rec in reversed(history.get("items", [])):
        if not isinstance(rec, dict):
            continue
        source = str(rec.get("source", ""))
        title = str(rec.get("title", ""))
        url = str(rec.get("url", ""))
        if not title or not url or "reddit" not in source.lower():
            continue
        key = (canonical_url(url), norm_title(title))
        if key in seen:
            continue
        seen.add(key)
        sent_date = str(rec.get("sent_date", ""))
        published = f"{sent_date}T00:00:00+00:00" if sent_date else ""
        items.append(Item(source, title, url, published, forum_history_backfill_summary(title)))
        if len(items) >= limit:
            break
    return items


def same_day_forum_history_items(limit: int = 30) -> list[Item]:
    history = load_digest_history("etf")
    today = report_date()
    items: list[Item] = []
    seen: set[tuple[str, str]] = set()
    for rec in reversed(history.get("items", [])):
        if not isinstance(rec, dict) or str(rec.get("sent_date", "")) != today:
            continue
        source = str(rec.get("source", ""))
        title = str(rec.get("title", ""))
        url = str(rec.get("url", ""))
        if not title or not url:
            continue
        if "bogleblog" in source.lower():
            continue
        if not any(marker in source.lower() for marker in ["reddit", "bogleheads", "forum"]):
            continue
        key = (canonical_url(url), norm_title(title))
        if key in seen:
            continue
        seen.add(key)
        items.append(Item(source, title, url, f"{today}T00:00:00+00:00", forum_history_backfill_summary(title)))
        if len(items) >= limit:
            break
    return items


def supplement_forum_items_with_same_day_new_history(
    picked: list[Item],
    minimum: int = ETF_MIN_VISIBLE_FORUM_ITEMS,
    limit: int = ETF_FORUM_DISPLAY_LIMIT,
) -> list[Item]:
    if len(picked) >= minimum:
        return picked[:limit]
    today = report_date()
    same_day_candidates = same_day_forum_history_items(limit=max(limit * 3, 18))
    same_day_not_prior_sent = filter_previously_sent(
        "etf",
        same_day_candidates,
        days=ETF_FORUM_BACKFILL_DEDUPE_DAYS,
        ignore_dates={today},
    )
    return extend_forum_items_to_minimum(picked, same_day_not_prior_sent, minimum=minimum, limit=limit)


def forum_research_question(item: Item) -> str:
    text = f"{item.title} {item.summary}".lower()
    if "vnq" in text or "reit" in text or "real estate" in text:
        return "可检验在税优账户里单列 REIT/VNQ 是否改善长期组合的收益回撤、通胀敏感性和与股债的相关性。"
    if "moderate risk" in text or "ucits" in text or "global aggregate bonds" in text:
        return "可把拟定组合拆成发达市场、新兴市场、小盘和全球债券暴露，检查是否存在重复持仓、风险过度集中或债券比例不足。"
    if "what stocks to sell" in text or "advice on portfolio" in text:
        return "可把个股持仓映射到行业、风格和单一公司集中度，再比较是否用宽基或行业 ETF 替代能降低非系统性风险。"
    if "dividends" in text or "living off" in text:
        return "可区分总回报提款和分红现金流两种退休取现方式，回测税后现金流、回撤和再平衡压力。"
    if "cash" in text and ("short term bonds" in text or "short-term bonds" in text):
        return "可把现金、货币市场基金、短债 ETF 和短期国债放到同一税后收益/久期风险框架里比较。"
    return "可把帖子里的配置问题转成权重、账户位置、资金用途和风险承受期四类变量，再用实际 ETF 收益、波动率和回撤数据验证。"


def append_article_detail_points(lines: list[str], item: Item, limit: int = 6) -> None:
    points = etf_article_detail_points(item, limit=limit)
    if not points:
        return
    lines.append("**正文细节**：")
    for point in points:
        lines.append(f"- {point}")
    lines.append("")


def etf_feed_profile(source: str) -> ResearchFeed | None:
    for feed in ETF_RESEARCH_FEEDS:
        if feed.source == source:
            return feed
    aliases = {
        "AQR Insights": ResearchFeed("AQR Insights", "https://www.aqr.com/Insights", "核心机构研究源", "因子、动量、另类风险溢价与组合构建", (ASSET_ALLOCATION_SECTION, QUANT_STRATEGY_SECTION)),
        "BlackRock Investment Institute": ResearchFeed(
            "BlackRock Investment Institute",
            "https://www.blackrock.com/us/financial-professionals/insights/capital-market-assumptions",
            "核心机构研究源",
            "资本市场假设、宏观情景与风险预算",
            (ASSET_ALLOCATION_SECTION,),
        ),
        "Vanguard": ResearchFeed("Vanguard", "https://corporate.vanguard.com", "核心机构研究源", "长期收益预测、估值和利率框架", (ASSET_ALLOCATION_SECTION,)),
        "Research Affiliates": ResearchFeed("Research Affiliates", "https://www.researchaffiliates.com/aai-hub", "核心机构研究源", "估值驱动配置与 Smart Beta", (ASSET_ALLOCATION_SECTION,)),
        "J.P. Morgan": ResearchFeed("J.P. Morgan", "https://am.jpmorgan.com/us/en/asset-management/adv/insights/portfolio-insights/ltcma/", "核心机构研究源", "LTCMA、Guide to the Markets 与配置图表", (ASSET_ALLOCATION_SECTION,)),
        "GMO Research": ResearchFeed("GMO Research", "https://www.gmo.com/", "核心机构研究源", "估值敏感型配置与反方观点", (ASSET_ALLOCATION_SECTION,)),
        "Man Institute": ResearchFeed("Man Institute", "https://www.man.com/maninstitute", "高频机构观点源", "系统化投资、趋势跟踪与宏观", (ASSET_ALLOCATION_SECTION, QUANT_STRATEGY_SECTION)),
    }
    return aliases.get(source)


def etf_item_sections(item: Item) -> tuple[str, ...]:
    text = f"{item.source} {item.title} {item.summary} {item.url}".lower()
    headline_text = f"{item.source} {item.title} {item.url}".lower()
    sections: list[str] = []
    profile = etf_feed_profile(item.source)
    if profile:
        sections.extend(profile.default_sections)
    allocation_text = text
    if profile and ASSET_ALLOCATION_SECTION not in profile.default_sections:
        allocation_text = headline_text
    if any(
        k in allocation_text
        for k in [
            "capital market",
            "expected return",
            "asset allocation",
            "risk budget",
            "valuation",
            "vanguard",
            "blackrock",
            "research affiliates",
            "j.p. morgan",
            "gmo",
            "fred",
            "yield curve",
            "credit spread",
            "strategic allocation",
        ]
    ):
        if ASSET_ALLOCATION_SECTION not in sections:
            sections.append(ASSET_ALLOCATION_SECTION)
    if any(
        k in text
        for k in [
            "arxiv",
            "q-fin",
            "factor",
            "momentum",
            "trend following",
            "value",
            "quality",
            "low vol",
            "volatility",
            "backtest",
            "turnover",
            "transaction cost",
            "no-trade",
            "capacity",
            "portfolio optimization",
            "overfitting",
            "statistical",
            "machine learning",
        ]
    ):
        if QUANT_STRATEGY_SECTION not in sections:
            sections.append(QUANT_STRATEGY_SECTION)
    if any(
        k in text
        for k in [
            "hkex",
            "stock connect",
            "shanghai stock exchange",
            "shenzhen",
            "sse",
            "szse",
            "a-share",
            "china a",
            "listing rule",
            "监管",
            "问询",
            "港股",
            "a股",
            "互联互通",
        ]
    ) and (
        item.source.startswith("HKEX")
        or item.source.startswith("RSSHub")
        or any(k in headline_text for k in ["hkex", "stock connect", "sse", "szse", "a-share", "china a", "港股", "a股", "互联互通"])
    ):
        if CHINA_HK_SECTION not in sections:
            sections.append(CHINA_HK_SECTION)
    return tuple(sections or (ASSET_ALLOCATION_SECTION,))


def score_etf_research_item(item: Item) -> ScoredResearchItem | None:
    text = f"{item.source} {item.title} {item.summary} {item.url}".lower()
    hard_exclusions = [
        "single-stock",
        "single stock",
        "leveraged",
        "inverse etf",
        "yieldmax",
        "weeklypay",
        "incomemax",
        "kurv",
        "meme stock",
        "rocket labs",
        "celebrity",
        "sports betting",
    ]
    if any(k in text for k in hard_exclusions):
        return None

    profile = etf_feed_profile(item.source)
    score = 35
    tier = "未分级来源"
    role = "待人工判断相关性"
    reasons: list[str] = []
    if profile:
        tier = profile.tier
        role = profile.role
        if "核心" in tier:
            score += 35
        elif "高质量" in tier or "官方" in tier or "论文" in tier:
            score += 30
        elif "实践型" in tier or "TAA" in tier or "指数" in tier:
            score += 25
        else:
            score += 15
        reasons.append(tier)

    keyword_groups = [
        (("expected return", "capital market", "valuation", "risk budget", "asset allocation"), 18, "战略配置/估值锚"),
        (("factor", "momentum", "trend following", "value", "quality", "low vol"), 18, "因子/趋势/风格"),
        (("backtest", "reproducible", "transaction cost", "turnover", "capacity", "no-trade", "overfitting"), 20, "可回测方法"),
        (("treasury", "yield curve", "fed", "inflation", "credit spread", "dollar", "volatility"), 12, "宏观数据/regime"),
        (("hkex", "stock connect", "sse", "szse", "a-share", "listing rule", "china a", "hong kong"), 16, "中港市场结构"),
        (("flow", "aum", "expense ratio", "etf structure", "spiva", "index"), 8, "ETF/指数结构"),
    ]
    for keys, weight, reason in keyword_groups:
        if any(k in text for k in keys):
            score += weight
            reasons.append(reason)

    if re.search(r"\b[A-Z]{2,5}\b", item.title) and "single" in text and "market" not in text:
        score -= 30
    if "product launch" in text or "passes $" in text:
        score -= 8
        reasons.append("产品热度，降低权重")
    if "opinion" in text and not any(k in text for k in ["data", "backtest", "evidence", "valuation"]):
        score -= 10

    sections = etf_item_sections(item)
    if score < 55:
        return None
    return ScoredResearchItem(item=item, score=min(score, 100), tier=tier, role=role, sections=sections, reasons=tuple(dict.fromkeys(reasons)))


def rank_etf_research_items(items: list[Item], limit: int = 8, require_evidence: bool = False) -> list[ScoredResearchItem]:
    scored: list[ScoredResearchItem] = []
    for item in dedupe_items(items):
        val = score_etf_research_item(item)
        if val and require_evidence and not etf_has_enough_summary_evidence(item):
            continue
        if val:
            scored.append(val)
    ranked = sorted(
        scored,
        key=lambda x: (
            x.score,
            parse_date(x.item.published) or datetime.min.replace(tzinfo=timezone.utc),
            x.item.source,
        ),
        reverse=True,
    )
    out: list[ScoredResearchItem] = []
    seen_public_titles: set[tuple[str, str]] = set()
    for val in ranked:
        display_key = (val.item.source, etf_public_heading(val.item.title, val.item.summary))
        if display_key in seen_public_titles:
            continue
        seen_public_titles.add(display_key)
        out.append(val)
        if len(out) >= limit:
            break
    return out


def enrich_ranked_research_items(candidates: list[Item], limit: int) -> list[ScoredResearchItem]:
    pre_ranked = rank_etf_research_items(candidates, limit=max(limit * 2, 14))
    enriched = [enrich_article_item(x.item) for x in pre_ranked]
    return rank_etf_research_items(enriched, limit=limit, require_evidence=True)


def combine_scored_research_items(
    primary: list[ScoredResearchItem], backfill: list[ScoredResearchItem], limit: int
) -> list[ScoredResearchItem]:
    out: list[ScoredResearchItem] = []
    seen: set[tuple[str, str]] = set()
    for scored in [*primary, *backfill]:
        key = (canonical_url(scored.item.url), norm_title(scored.item.title))
        if key in seen:
            continue
        seen.add(key)
        out.append(scored)
        if len(out) >= limit:
            break
    return out


def select_etf_research_items(items: list[Item], limit: int = 9) -> list[ScoredResearchItem]:
    relevant = dedupe_items([x for x in sort_recent(items) if etf_research_relevant(x)])
    recent_pool = filter_recent_published(relevant, ETF_ARTICLE_MAX_AGE_HOURS)
    primary_candidates = filter_previously_sent("etf", recent_pool, days=ETF_DEDUPE_DAYS)
    primary = enrich_ranked_research_items(primary_candidates, limit=limit)
    if len(primary) >= ETF_MIN_RESEARCH_ITEMS:
        return primary

    backfill_pool = filter_recent_published(relevant, ETF_ARTICLE_BACKFILL_MAX_AGE_HOURS)
    backfill_candidates = filter_previously_sent("etf", backfill_pool, days=ETF_BACKFILL_DEDUPE_DAYS)
    backfill = enrich_ranked_research_items(backfill_candidates, limit=limit)
    return combine_scored_research_items(primary, backfill, limit=limit)


def renderable_forum_item(item: Item) -> bool:
    points = forum_thread_summary_points(item)
    return forum_has_lightweight_summary_evidence(item, points)


def renderable_or_enriched_forum_item(item: Item) -> Item | None:
    item = reddit_forum_item_with_engagement(item)
    if not forum_item_meets_quality_bar(item):
        return None
    if renderable_forum_item(item):
        return item
    enriched = enrich_article_item(item)
    return enriched if renderable_forum_item(enriched) else None


def select_etf_forum_items(items: list[Item], limit: int = ETF_FORUM_DISPLAY_LIMIT) -> list[Item]:
    relevant = dedupe_items([x for x in items if etf_forum_relevant(x)])
    relevant.sort(
        key=lambda x: (
            forum_engagement_score(x),
            parse_date(x.published) or datetime.min.replace(tzinfo=timezone.utc),
        ),
        reverse=True,
    )
    today = report_date()
    primary_raw = filter_previously_sent("etf", relevant, days=ETF_DEDUPE_DAYS, ignore_dates={today})[: max(limit * 2, 12)]
    primary = [x for x in (renderable_or_enriched_forum_item(item) for item in primary_raw) if x is not None]
    primary.sort(key=forum_engagement_score, reverse=True)
    if len(primary) >= ETF_MIN_FORUM_ITEMS:
        return primary[:limit]

    backfill_raw = filter_previously_sent("etf", relevant, days=ETF_FORUM_BACKFILL_DEDUPE_DAYS, ignore_dates={today})[: max(limit * 3, 18)]
    backfill = [x for x in (renderable_or_enriched_forum_item(item) for item in backfill_raw) if x is not None]
    backfill.sort(key=forum_engagement_score, reverse=True)
    out: list[Item] = []
    seen: set[tuple[str, str]] = set()
    for item in [*primary, *backfill]:
        key = (canonical_url(item.url), norm_title(item.title))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= limit:
            break
    if len(out) >= ETF_MIN_FORUM_ITEMS:
        return out
    recovery_raw = filter_previously_sent("etf", relevant, days=ETF_DEDUPE_DAYS, ignore_dates={today})[: max(limit * 3, 18)]
    recovery = [x for x in (renderable_or_enriched_forum_item(item) for item in recovery_raw) if x is not None]
    recovery.sort(key=forum_engagement_score, reverse=True)
    for item in recovery:
        key = (canonical_url(item.url), norm_title(item.title))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= limit:
            break
    return out


def extend_forum_items_to_minimum(
    picked: list[Item],
    candidates: list[Item],
    minimum: int = ETF_MIN_FORUM_ITEMS,
    limit: int = ETF_FORUM_DISPLAY_LIMIT,
) -> list[Item]:
    out = list(picked[:limit])
    if len(out) >= minimum:
        return out
    seen = {(canonical_url(item.url), norm_title(item.title)) for item in out}
    seen_titles = {norm_title(item.title) for item in out}
    for item in candidates:
        if len(out) >= limit:
            break
        item = reddit_forum_item_with_engagement(item)
        key = (canonical_url(item.url), norm_title(item.title))
        title_key = norm_title(item.title)
        if key in seen or title_key in seen_titles:
            continue
        if not forum_item_meets_quality_bar(item):
            continue
        enriched = item if renderable_forum_item(item) else enrich_article_item(item)
        if not renderable_forum_item(enriched):
            continue
        seen.add(key)
        seen_titles.add(title_key)
        out.append(enriched)
        if len(out) >= minimum:
            break
    return out


def is_non_reddit_forum_item(item: Item) -> bool:
    return "reddit" not in item.source.lower()


def ensure_non_reddit_forum_mix(
    picked: list[Item],
    candidates: list[Item],
    min_non_reddit: int = 2,
    limit: int = ETF_FORUM_DISPLAY_LIMIT,
) -> list[Item]:
    out = list(picked[:limit])
    if sum(1 for item in out if is_non_reddit_forum_item(item)) >= min_non_reddit:
        return out
    seen = {(canonical_url(item.url), norm_title(item.title)) for item in out}
    additions: list[Item] = []
    for item in candidates:
        if not is_non_reddit_forum_item(item):
            continue
        key = (canonical_url(item.url), norm_title(item.title))
        if key in seen:
            continue
        enriched = item if renderable_forum_item(item) else enrich_article_item(item)
        if not renderable_forum_item(enriched):
            continue
        seen.add(key)
        additions.append(enriched)
        if sum(1 for x in out if is_non_reddit_forum_item(x)) + len(additions) >= min_non_reddit:
            break
    for item in additions:
        if len(out) < limit:
            out.append(item)
        else:
            replace_idx = next((idx for idx in range(len(out) - 1, -1, -1) if not is_non_reddit_forum_item(out[idx])), -1)
            if replace_idx == -1:
                break
            out[replace_idx] = item
    return out[:limit]


def collect_etf_forum_items() -> list[Item]:
    forum_items: list[Item] = []
    for subreddit in ETF_FORUM_SUBREDDITS:
        for sort in ETF_REDDIT_FORUM_SORTS:
            forum_items.extend(fetch_reddit_listing_items(subreddit, sort, limit=ETF_REDDIT_LISTING_LIMIT))
            time.sleep(0.2)
        reddit_rss_items = parse_feed(f"Reddit r/{subreddit}", f"https://old.reddit.com/r/{subreddit}/hot/.rss", limit=ETF_REDDIT_LISTING_LIMIT)
        forum_items.extend(reddit_hot_rss_ranked_items(reddit_rss_items))
        time.sleep(0.2)
    for source, url, limit in ETF_EXTERNAL_FORUM_FEEDS:
        forum_items.extend(parse_feed(source, url, limit=limit))
        time.sleep(0.2)
    if not forum_items:
        forum_feeds = {
            "Reddit r/ETFs": "https://old.reddit.com/r/ETFs/hot/.rss",
            "Reddit r/Bogleheads": "https://old.reddit.com/r/Bogleheads/hot/.rss",
            "Reddit r/investing": "https://old.reddit.com/r/investing/hot/.rss",
            "Reddit r/portfolios": "https://old.reddit.com/r/portfolios/hot/.rss",
        }
        for source, url in forum_feeds.items():
            forum_items.extend(reddit_hot_rss_ranked_items(parse_feed(source, url, limit=12)))
            time.sleep(0.2)
    return forum_items


def etf_hypothesis_for_item(scored: ScoredResearchItem) -> str:
    title = etf_public_heading(scored.item.title, scored.item.summary)
    text = f"{scored.item.title} {scored.item.summary}".lower()
    title_lower = scored.item.title.lower()
    if "active etfs win the liquidity race" in title_lower:
        return f"{title} -> 比较主动 ETF 与同类指数 ETF/共同基金的成交量、买卖价差、折溢价和大额交易冲击。"
    if "economic policy uncertainty and aggregate economic activity in india" in title_lower:
        return f"{title} -> 测试印度政策不确定性指标与 INDA/EPI、美元和新兴市场 ETF 回撤之间的关系。"
    if "stock market prediction using node transformer" in title_lower:
        return f"{title} -> 先复现论文数据和样本外预测，再加入交易成本、延迟和基准比较。"
    if "縮短香港股票現貨市場結算週期" in scored.item.title or "shortening hong kong stock settlement cycle" in text:
        return f"{title} -> 验证结算周期缩短对港股 ETF、互联互通资金调拨和跨市场再平衡执行的影响。"
    if "tactical yield" in title_lower:
        return f"{title} -> 回测 T-Bills、IEF/LQD、长债和信用债之间的收益率门槛切换，比较全周期与高利率窗口的收益回撤。"
    if "recent quant links from quantocracy" in title_lower:
        return f"{title} -> 先拆解子链接，只有能写出信号、数据、调仓和成本假设的条目才进入回测队列。"
    if "commodity futures returns since 1871" in title_lower:
        return f"{title} -> 用 PDBC/商品期货代理检验商品袖珍仓位在股债双跌、美元走强和高通胀窗口中的分散化效果。"
    if "dual momentum allocation between physical gold and bitcoin" in title_lower:
        return f"{title} -> 回测 GLDM/IBIT 双动量、固定权重和单一黄金配置在 1Y/3Y/5Y 窗口的收益回撤差异。"
    if "attention factor" in title_lower and ("crypto" in title_lower or "bitcoin" in text or "btc" in text):
        return f"{title} -> 统计 BTC、BUZZ、COIN/HOOD/DKNG、0DTE 代理和高 beta 股票在风险偏好冲击中的相关性与共同回撤。"
    if "surfing the equity curve" in text:
        return f"{title} -> 对现有 TAA/动量策略回测权益曲线过滤器，单独统计减少回撤、错过反弹和额外换手成本。"
    if QUANT_STRATEGY_SECTION in scored.sections:
        if "transaction cost" in text or "turnover" in text or "no-trade" in text:
            return f"{title} -> 回测再平衡带/no-trade region 是否能在扣除换手成本后改善 ETF 或微盘策略的收益回撤。"
        if "portfolio optimization" in text or "risk" in text:
            return f"{title} -> 检验新的组合约束是否能改善目标波动率缩放、风险预算或极端回撤控制。"
        return f"{title} -> 把文章中的信号定义、调仓频率、成本假设和样本窗口拆出来做可复现回测。"
    if CHINA_HK_SECTION in scored.sections:
        return f"{title} -> 验证规则/互联互通/指数样本变化是否影响 A 股、港股、ETF 流动性或可交易池。"
    return f"{title} -> 用 ETF 收益、估值、利率、信用利差和资金流数据验证其对战略/战术配置的实际影响。"


def build_etf_testable_hypotheses(scored_items: list[ScoredResearchItem], limit: int = 3) -> list[str]:
    hypotheses: list[str] = []
    seen: set[str] = set()
    for scored in scored_items:
        hypothesis = etf_hypothesis_for_item(scored)
        key = norm_title(hypothesis)
        if key in seen:
            continue
        seen.add(key)
        hypotheses.append(hypothesis)
        if len(hypotheses) >= limit:
            break
    if not hypotheses:
        hypotheses.append("今日没有足够高相关新文章进入假设池；不从低质量新闻或论坛结论硬生成交易假设。")
    return hypotheses


def etf_regime_observation(rows: list[dict[str, object]], data_date_s: str = "") -> list[str]:
    by_code: dict[str, float] = {}
    for row in rows:
        asset = row.get("asset")
        if isinstance(asset, MarketAsset):
            try:
                by_code[asset.code] = float(row["change"])
            except (KeyError, TypeError, ValueError):
                continue

    def avg(codes: tuple[str, ...]) -> float | None:
        vals = [by_code[c] for c in codes if c in by_code]
        return sum(vals) / len(vals) if vals else None

    observations: list[str] = []
    if data_date_s:
        observations.append(f"- 数据层：最新收盘数据日期为 {data_date_s}；这里先判断 regime 线索，再进入文章解读。")
    equity = avg(("^GSPC", "^NDX", "^RUT", "RSP"))
    duration = avg(("IEF", "VGLT"))
    credit = avg(("LQD", "HYG", "EMB"))
    real_assets = avg(("PDBC", "VNQ", "GLDM"))
    china = avg(("000300", "000905", "000852", "399006", "FXI", "ASHR"))
    dollar = by_code.get("UUP")
    if equity is not None:
        tone = "偏风险偏好改善" if equity > 0.25 else "偏风险收缩" if equity < -0.25 else "股市宽基变化不大"
        observations.append(f"- 股票风险偏好：宽基股票代理平均 {equity:+.2f}%，{tone}。")
    if duration is not None:
        tone = "久期资产获得支撑" if duration > 0.2 else "久期资产承压" if duration < -0.2 else "久期信号中性"
        observations.append(f"- 利率/久期：中长久期国债代理平均 {duration:+.2f}%，{tone}。")
    if credit is not None:
        tone = "信用风险偏好尚可" if credit > 0.15 else "信用资产走弱" if credit < -0.15 else "信用信号不强"
        observations.append(f"- 信用：信用债代理平均 {credit:+.2f}%，{tone}。")
    if dollar is not None:
        observations.append(f"- 美元：UUP {dollar:+.2f}%，用于辅助判断海外资产、黄金和商品压力。")
    if real_assets is not None:
        observations.append(f"- 商品/实物资产：相关代理平均 {real_assets:+.2f}%，需和美元、实际利率一起看。")
    if china is not None:
        observations.append(f"- A 股/港股/中概：相关代理平均 {china:+.2f}%，单独进入中国市场事实层。")
    if len(observations) <= 1:
        observations.append("- 数据层：今日可用市场代理不足，文章部分只做事实和假设过滤，不硬判断 regime 改变。")
    return observations


def append_scored_item(lines: list[str], scored: ScoredResearchItem, idx: int) -> None:
    item = scored.item
    lines += [
        f"### {idx}. {etf_display_title(item)}",
        f"- 来源：{item.source} | {scored.tier} | score={scored.score}",
        f"- 角色：{scored.role}",
        f"- 标题：{etf_display_title(item)}",
        f"- 链接：{item.url}",
        "",
        f"**事实层**：{etf_chinese_fact(item)}",
        "",
    ]
    append_article_detail_points(lines, item)
    lines += [
        f"**配置/策略映射**：{etf_follow_up_point(item.title, item.summary)}",
        "",
        f"**可测试假设**：{etf_hypothesis_for_item(scored)}",
        "",
    ]


def primary_etf_section(scored: ScoredResearchItem) -> str:
    if CHINA_HK_SECTION in scored.sections and (
        scored.item.source.startswith("HKEX") or scored.item.source.startswith("RSSHub")
    ):
        return CHINA_HK_SECTION
    return scored.sections[0] if scored.sections else ASSET_ALLOCATION_SECTION


def append_etf_research_sections(
    lines: list[str],
    scored_items: list[ScoredResearchItem],
    forum_items: list[Item],
    strategy_rows: list[dict[str, object]] | None = None,
    mover_rows: list[dict[str, object]] | None = None,
    data_date_s: str = "",
) -> int:
    market_rows = [*(strategy_rows or []), *(mover_rows or [])]
    lines += ["", "---", "", "## 市场 regime 是否变化", ""]
    lines.extend(etf_regime_observation(market_rows, data_date_s))

    for section in [ASSET_ALLOCATION_SECTION, QUANT_STRATEGY_SECTION, CHINA_HK_SECTION]:
        section_items = [x for x in scored_items if primary_etf_section(x) == section]
        lines += ["", "---", "", f"## {section}", ""]
        if not section_items:
            lines.append("今日没有足够高相关、未重复的新内容进入本段；不从低质量新闻里硬写。")
            continue
        for i, scored in enumerate(section_items[:4], 1):
            append_scored_item(lines, scored, i)

    lines += ["", "---", "", "## 论坛与社区 idea mining", "", "论坛和社区只用于发现待验证问题，不是事实结论，也不进入一句话结论。", ""]
    if not forum_items:
        lines.append("今日没有进入筛选口径的论坛补充。")
    visible_forum_count = 0
    for item in forum_items[:ETF_FORUM_DISPLAY_LIMIT]:
        full_summary = forum_thread_summary_points(item)
        has_specific_summary = forum_has_specific_summary_evidence(item, full_summary)
        summary_points = full_summary if has_specific_summary else forum_lightweight_summary_points(item)
        if not forum_has_lightweight_summary_evidence(item, summary_points):
            continue
        visible_forum_count += 1
        lines += [
            f"### {visible_forum_count}. {forum_display_title(item)}",
            f"- 来源：{item.source}",
            f"- 标题：{forum_display_title(item)}",
            f"- 链接：{item.url}",
            "",
        ]
        lines.append("**全文总结**：" if has_specific_summary else "**线索摘要**：")
        for point in summary_points:
            lines.append(f"- {point}")
        lines.append("")
        lines += [
            f"**可研究问题**：{forum_research_question(item)}",
            "",
        ]
    if forum_items and visible_forum_count == 0:
        lines.append("今日论坛/RSS 补充在打开链接后仍缺少可核验正文细节，已从正文剔除；不再用标题硬写总结。")

    lines += ["", "---", "", "## 待验证假设", ""]
    for i, hypothesis in enumerate(build_etf_testable_hypotheses(scored_items, limit=3), 1):
        lines.append(f"{i}. {hypothesis}")

    lines += [
        "",
        "---",
        "",
        "## 源分级与筛选审计",
        "",
        "- 文章筛选规则：优先机构研究、官方数据、学术论文和可复现量化研究；降权 ETF 产品营销、单股新闻、重复转载和不可验证宏观预测。",
        "- 核心中低频页面源：AQR、Research Affiliates、BlackRock、Vanguard、J.P. Morgan、GMO 等不强行 RSS 化；有新内容才写入正文。",
        f"- 本次进入研究框架的文章数量：{len(scored_items)}；论坛补充入正文数量：{visible_forum_count}。",
    ]

    return visible_forum_count


def fixed_monitor_source(source: str, medium: str) -> str:
    return f"{source}（{medium}）"


def fixed_page_title_from_url(url: str) -> str:
    path = urllib.parse.urlsplit(url).path.strip("/")
    slug = path.split("/")[-1] if path else url
    slug = re.sub(r"^\d+-", "", slug)
    words = [w for w in re.split(r"[-_]+", slug) if w]
    special = {
        "ai": "AI",
        "aqr": "AQR",
        "cio": "CIO",
        "cfa": "CFA",
        "etf": "ETF",
        "etfs": "ETFs",
        "isnt": "Isn't",
        "aint": "Ain't",
        "thats": "That's",
        "whats": "What's",
    }
    return clean_text(" ".join(special.get(w.lower(), w.capitalize()) for w in words), 180)


def fixed_page_text(body: str) -> str:
    text = re.sub(r"<script\b.*?</script>", " ", body, flags=re.I | re.S)
    text = re.sub(r"<style\b.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return clean_text(html.unescape(text), 600)


def fixed_page_items(monitor: FixedPageMonitor) -> list[Item]:
    try:
        page = fetch_bytes(monitor.url).decode("utf-8", "ignore")
    except Exception:
        return []
    anchor_re = re.compile(
        r"<a\s+[^>]*href=[\"'](?P<href>" + monitor.href_pattern + r")[\"'][^>]*>(?P<body>.*?)</a>",
        flags=re.I | re.S,
    )
    out: list[Item] = []
    seen: set[str] = set()
    for match in anchor_re.finditer(page):
        href = html.unescape(match.group("href")).rstrip("\\")
        url = urllib.parse.urljoin(monitor.url, href)
        key = canonical_url(url)
        if key in seen:
            continue
        seen.add(key)
        title = fixed_page_title_from_url(url)
        text = fixed_page_text(match.group("body"))
        if not title:
            continue
        out.append(
            Item(
                fixed_monitor_source(monitor.source, monitor.medium),
                title,
                url,
                "",
                text or "页面监控源发现了新的文章链接，但页面没有提供标准 RSS 摘要。",
            )
        )
        if len(out) >= monitor.limit:
            break
    return out


def collect_etf_fixed_monitor_updates(exclude_urls: set[str] | None = None) -> list[Item]:
    exclude_urls = exclude_urls or set()
    today = report_date()
    feed_items: list[Item] = []
    for feed in ETF_FIXED_MONITOR_FEEDS:
        for item in parse_feed(feed.source, feed.url, limit=feed.limit):
            feed_items.append(
                Item(
                    fixed_monitor_source(item.source, feed.medium),
                    item.title,
                    item.url,
                    item.published,
                    item.summary,
                )
            )
        time.sleep(0.1)

    recent_feed_items = filter_recent_published(sort_recent(dedupe_items(feed_items)), ETF_ARTICLE_MAX_AGE_HOURS)
    recent_feed_items = filter_previously_sent("etf", recent_feed_items, days=ETF_DEDUPE_DAYS, ignore_dates={today})

    page_items: list[Item] = []
    for monitor in ETF_FIXED_PAGE_MONITORS:
        page_items.extend(fixed_page_items(monitor))
        time.sleep(0.1)
    page_items = filter_previously_sent("etf", dedupe_items(page_items), days=ETF_DEDUPE_DAYS, ignore_dates={today})

    out: list[Item] = []
    seen = set(exclude_urls)
    for item in [*recent_feed_items, *page_items]:
        key = canonical_url(item.url)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= ETF_FIXED_MONITOR_DISPLAY_LIMIT:
            break
    return out


def fixed_monitor_update_summary(item: Item) -> str:
    source = item.source
    lower = f"{item.title} {item.summary}".lower()
    if any(marker in source for marker in ["视频", "播客"]):
        return "这是固定关注清单里的新一期视频或播客；日报只把它作为资产配置研究线索，不直接当作交易结论。"
    if "page" in source.lower() or "页面" in source:
        return "这是没有稳定 RSS 的机构页面中新发现、且近期未推送过的文章链接；需要打开原文确认发布日期和完整论据。"
    if any(k in lower for k in ["portfolio", "allocation", "asset class", "expected return", "capital market"]):
        return "这是一条固定关注源的新文章，主题靠近组合构建、资产类别预期收益或长期配置框架。"
    if any(k in lower for k in ["trend", "momentum", "factor", "value", "managed futures"]):
        return "这是一条固定关注源的新文章，主题靠近趋势、动量、因子或另类风险溢价。"
    return "这是一条固定关注源的新内容，已通过发布时间或 URL 历史去重，适合作为后续阅读入口。"


def append_etf_fixed_monitor_section(lines: list[str], updates: list[Item]) -> int:
    lines += [
        "",
        "---",
        "",
        "## 固定关注博客/播客更新",
        "",
        f"> 监控范围：前面那 14 个资产配置博客/播客及其相关页面源。RSS/YouTube 条目按最近 {ETF_ARTICLE_MAX_AGE_HOURS} 小时过滤；页面型源按 URL 历史去重，只在新发现或未推送过时出现。",
        "",
    ]
    if not updates:
        lines.append(f"过去 {ETF_ARTICLE_MAX_AGE_HOURS} 小时没有发现未推送过的新内容；不补旧文。")
        lines.append("")
        return 0

    for idx, item in enumerate(updates[:ETF_FIXED_MONITOR_DISPLAY_LIMIT], 1):
        published = parse_date(item.published)
        published_s = published.astimezone(BJ).strftime("%Y-%m-%d %H:%M %Z") if published else "无标准 RSS 日期"
        lines += [
            f"### {idx}. {etf_display_title(item)}",
            f"- 来源：{item.source}",
            f"- 发布时间：{published_s}",
            f"- 链接：{item.url}",
            f"- 更新线索：{fixed_monitor_update_summary(item)}",
            f"- 配置关注点：{etf_follow_up_point(item.title, item.summary)}",
            "",
        ]
    return len(updates[:ETF_FIXED_MONITOR_DISPLAY_LIMIT])


def write_meta(
    out_dir: Path,
    subject: str,
    body: str,
    attachment: Path | None,
    html_body: str | None = None,
) -> None:
    meta = {
        "subject": subject,
        "body": body,
        "attachment": str(attachment) if attachment else None,
    }
    if html_body:
        meta["html_body"] = html_body
    (out_dir / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def email_inline_markdown(text: str) -> str:
    placeholders: list[str] = []

    def stash(value: str) -> str:
        placeholders.append(value)
        return f"\x00EMAILTOKEN{len(placeholders) - 1}\x00"

    def markdown_link(match: re.Match[str]) -> str:
        label = html.escape(match.group(1))
        url = html.escape(match.group(2), quote=True)
        return stash(f'<a href="{url}" class="email-link">{label}</a>')

    def inline_code(match: re.Match[str]) -> str:
        value = html.escape(match.group(1))
        return stash(f'<code class="email-code">{value}</code>')

    def bare_link(match: re.Match[str]) -> str:
        url = html.escape(match.group(0), quote=True)
        return stash(f'<a href="{url}" class="email-link">{url}</a>')

    protected = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", markdown_link, text)
    protected = re.sub(r"`([^`]+)`", inline_code, protected)
    protected = re.sub(r"https?://[^\s<>\"'，。；、）]+", bare_link, protected)
    rendered = html.escape(protected)
    rendered = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", rendered)
    rendered = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", rendered)
    for index, value in enumerate(placeholders):
        rendered = rendered.replace(f"\x00EMAILTOKEN{index}\x00", value)
    return rendered


def markdown_to_email_html(markdown_text: str, preheader: str) -> str:
    lines = markdown_text.splitlines()
    blocks: list[str] = []
    index = 0
    open_list: str | None = None

    def close_list() -> None:
        nonlocal open_list
        if open_list:
            blocks.append(f"</{open_list}>")
            open_list = None

    while index < len(lines):
        raw = lines[index]
        line = raw.strip()
        if not line:
            close_list()
            index += 1
            continue

        if line.startswith("|") and index + 1 < len(lines):
            separator = lines[index + 1].strip()
            if separator.startswith("|") and re.fullmatch(r"\|?[\s:|-]+\|?", separator):
                close_list()
                table_rows: list[list[str]] = []
                while index < len(lines) and lines[index].strip().startswith("|"):
                    table_line = lines[index].strip()
                    if not re.fullmatch(r"\|?[\s:|-]+\|?", table_line):
                        table_rows.append([cell.strip() for cell in table_line.strip("|").split("|")])
                    index += 1
                if table_rows:
                    header_cells = "".join(
                        f'<th class="report-th">{email_inline_markdown(cell)}</th>'
                        for cell in table_rows[0]
                    )
                    body_rows = "".join(
                        "<tr>"
                        + "".join(
                            f'<td class="report-td">{email_inline_markdown(cell)}</td>'
                            for cell in row
                        )
                        + "</tr>"
                        for row in table_rows[1:]
                    )
                    blocks.append(
                        '<div class="table-wrap">'
                        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" class="report-table">'
                        f"<thead><tr>{header_cells}</tr></thead><tbody>{body_rows}</tbody></table></div>"
                    )
                continue

        heading = re.match(r"^(#{1,3})\s+(.+)$", line)
        if heading:
            close_list()
            level = len(heading.group(1))
            content = email_inline_markdown(heading.group(2))
            if level == 1:
                blocks.append(
                    '<div class="report-hero"><div class="report-kicker">每日资产配置简报</div>'
                    f'<h1 class="report-h1">{content}</h1></div>'
                )
            elif level == 2:
                blocks.append(f'<h2 class="report-h2">{content}</h2>')
            else:
                blocks.append(f'<h3 class="report-h3">{content}</h3>')
            index += 1
            continue

        if re.fullmatch(r"-{3,}", line):
            close_list()
            blocks.append('<div class="report-rule"></div>')
            index += 1
            continue

        if line.startswith(">"):
            close_list()
            quote_lines: list[str] = []
            while index < len(lines) and lines[index].strip().startswith(">"):
                quote_lines.append(lines[index].strip().lstrip(">").strip())
                index += 1
            blocks.append(
                '<div class="report-quote">'
                f'{"<br>".join(email_inline_markdown(item) for item in quote_lines)}</div>'
            )
            continue

        bullet = re.match(r"^[-*]\s+(.+)$", line)
        numbered = re.match(r"^\d+[.)]\s+(.+)$", line)
        if bullet or numbered:
            list_type = "ul" if bullet else "ol"
            if open_list != list_type:
                close_list()
                blocks.append(f'<{list_type} class="report-list">')
                open_list = list_type
            content = (bullet or numbered).group(1)
            blocks.append(f'<li class="report-li">{email_inline_markdown(content)}</li>')
            index += 1
            continue

        close_list()
        paragraph_lines = [line]
        index += 1
        while index < len(lines):
            candidate = lines[index].strip()
            if (
                not candidate
                or candidate.startswith(("#", ">", "|"))
                or re.fullmatch(r"-{3,}", candidate)
                or re.match(r"^[-*]\s+", candidate)
                or re.match(r"^\d+[.)]\s+", candidate)
            ):
                break
            paragraph_lines.append(candidate)
            index += 1
        blocks.append(
            '<p class="report-p">'
            f'{"<br>".join(email_inline_markdown(item) for item in paragraph_lines)}</p>'
        )

    close_list()
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{{margin:0;padding:0;background:#f3f5f9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',Arial,sans-serif;color:#172033}}
.email-link{{color:#4f46e5;text-decoration:none;font-weight:600;word-break:break-word}}
.email-code{{padding:2px 5px;background:#f2f4f7;border-radius:4px;color:#344054;font-family:Consolas,monospace;font-size:.92em}}
.email-shell{{max-width:920px;margin:0 auto;padding:24px 12px 40px}}
.email-main{{background:#fff;padding:24px 22px;border:1px solid #e4e7ec;border-radius:16px}}
.report-hero{{background:#25335f;padding:28px 24px;border-radius:16px;color:#fff;margin-bottom:18px}}
.report-kicker{{font-size:13px;letter-spacing:1.3px;opacity:.82;margin-bottom:7px}}
.report-h1{{margin:0;font-size:28px;line-height:1.35;color:#fff}}
.report-h2{{margin:30px 0 14px;padding-bottom:9px;border-bottom:2px solid #dbe1f8;font-size:22px;line-height:1.4;color:#27306b}}
.report-h3{{margin:22px 0 10px;font-size:17px;line-height:1.5;color:#344054}}
.report-rule{{height:1px;background:#e4e7ec;margin:24px 0}}
.report-quote{{margin:12px 0 18px;padding:14px 16px;background:#fff8e8;border-left:4px solid #e0a82e;border-radius:8px;color:#694b09;font-size:14px;line-height:1.75}}
.report-list{{margin:8px 0 16px;padding-left:24px;color:#344054;font-size:14px;line-height:1.75}}
.report-li{{margin:0 0 6px}}
.report-p{{margin:0 0 14px;color:#344054;font-size:14px;line-height:1.8}}
.table-wrap{{margin:14px 0 22px;overflow-x:auto}}
.report-table{{width:100%;border-collapse:collapse;table-layout:auto}}
.report-th{{padding:10px 8px;background:#eef1ff;border:1px solid #d9def5;text-align:left;color:#27306b;font-size:12px;line-height:1.5}}
.report-td{{padding:9px 8px;border:1px solid #e4e7ec;vertical-align:top;color:#344054;font-size:12px;line-height:1.55;word-break:break-word}}
@media(max-width:640px){{.email-shell{{padding:10px 4px 24px}}.email-main{{padding:16px 12px;border-radius:10px}}.report-hero{{padding:22px 16px}}.report-h1{{font-size:23px}}.report-h2{{font-size:19px}}.report-th,.report-td{{padding:7px 5px;font-size:11px}}}}
</style></head><body>
<div style="display:none;max-height:0;overflow:hidden;">{html.escape(preheader)}</div>
<div class="email-shell">
  <main class="email-main">
    {''.join(blocks)}
  </main>
</div></body></html>'''


def life_feed_profile(source: str) -> LifeDigestFeed | None:
    for feed in (*LIFE_DIGEST_FEEDS, *LIFE_COMMUNITY_FALLBACK_FEEDS):
        if feed.source == source:
            return feed
    return None


def life_is_travel_community_item(item: Item) -> bool:
    source = item.source.lower()
    return any(k in source for k in ["fattravel", "chubbytravel", "luxurytravel"])


def life_item_type(item: Item) -> str:
    text = f"{item.title} {item.summary}".lower()
    title_lower = item.title.lower()
    profile = life_feed_profile(item.source)
    if life_is_thailand_visa_item(item):
        return "签证入境"
    if life_is_world_hyatt_award_cost_increase_item(item):
        return "积分"
    if life_is_hyatt_award_chart_item(item) or life_is_emirates_devaluation_item(item) or life_is_all_americas_sale_item(item):
        return "积分"
    if life_is_specific_credit_card_item(item):
        return "积分"
    if life_is_travel_community_item(item):
        if any(k in text for k in ["points", "miles", "award", "hyatt", "marriott", "hilton", "amex", "fhr", "virtuoso"]):
            return "积分"
        if any(k in text for k in ["hotel", "resort", "suite", "villa", "lodge", "breakfast", "late checkout", "upgrade"]):
            return "住宿"
        if any(k in text for k in ["maldives", "bora bora", "tokyo", "kyoto", "safari", "japan", "family trip", "parents"]):
            return "目的地"
        return "生活方式"
    if item.source.startswith("r/") and "destination" in text:
        return "目的地"
    if profile and profile.category in {"税务居留", "安全医疗", "财务自由", "目的地"}:
        return profile.category
    if profile and profile.category == "签证入境" and any(k in text for k in ["entry requirement", "travel advice", "visa", "immigration", "passport", "border"]):
        return "签证入境"
    if "gha discovery double d$" in title_lower or "bilt points with rakuten" in title_lower:
        return "积分"
    if any(k in text for k in ["best rate guarantee", "monthly stay", "long stay", "airbnb", "booking.com", "homeexchange"]):
        return "住宿"
    if profile and profile.category == "积分" and any(k in text for k in ["transfer bonus", "award", "business class", "first class", "hotel points", "airline miles", "devaluation", "elite status", "credit card", "annual fee", "points earning", "travel benefits"]):
        return "积分"
    if any(k in text for k in ["outsite", "place to stay", "digital nomads", "monthly stay", "long stay", "coliving"]):
        return "住宿"
    if any(k in text for k in ["tax residency", "crs", "non-resident", "residence", "visa", "nomad visa", "immigration"]):
        return "税务居留" if "tax" in text or "crs" in text else "签证入境"
    if any(k in text for k in ["travel advice", "entry requirement", "safety", "health", "vaccine", "medical"]):
        return "医疗安全"
    if any(k in text for k in ["safe withdrawal", "withdrawal rate", "retirement income", "sequence risk", "spending"]):
        return "财务自由"
    if any(k in text for k in ["hotel points", "airline miles", "business class", "first class", "award space", "transfer bonus", "elite status", "credit card", "annual fee", "points earning", "travel benefits"]):
        return "积分"
    if any(k in text for k in ["city", "country", "expat", "retire overseas", "cost of living", "quality of life"]):
        return "目的地"
    return "生活方式"


def life_source_tier(item: Item) -> str:
    profile = life_feed_profile(item.source)
    return profile.tier if profile else ("论坛经验" if item.source.startswith("r/") else "高质量博客")


def life_title_has_decision_signal(item: Item) -> bool:
    title = item.title.lower()
    return any(
        k in title
        for k in [
            "withdrawal",
            "retirement",
            "portfolio",
            "tax residency",
            "crs",
            "travel advice",
            "entry requirement",
            "visa",
            "residence",
            "resident",
            "cost of living",
            "healthcare",
            "health insurance",
            "long-stay",
            "long stay",
            "digital nomad",
            "transfer bonus",
            "conversion bonus",
            "award chart",
            "award night",
            "award flight",
            "hotel status",
            "hotel promotion",
            "best rate guarantee",
            "lifemiles",
            "world of hyatt",
            "credit card",
            "card guide",
            "card review",
            "annual fee",
            "admirals club",
            "points",
            "miles",
            "fhr",
            "virtuoso",
            "under canvas",
        ]
    )


def life_decision_impact(item: Item) -> str:
    typ = life_item_type(item)
    text = f"{item.title} {item.summary}".lower()
    if typ in {"税务居留", "签证入境", "医疗安全"}:
        return "高"
    if any(k in text for k in ["deadline", "devaluation", "warning", "risk", "tax", "residency", "visa"]):
        return "高"
    if typ in {"财务自由", "目的地", "积分"}:
        return "中"
    return "低"


def life_needs_human_check(item: Item) -> str:
    typ = life_item_type(item)
    if typ in {"税务居留", "签证入境", "医疗安全"}:
        return "是，涉及官方规则或个人适用性"
    if life_source_tier(item) in {"论坛经验", "低可信来源", "中等可信"}:
        return "是，需用官方或专业来源交叉核实"
    return "视是否进入个人路线/资产计划而定"


def life_candidate_status(item: Item) -> str:
    typ = life_item_type(item)
    if typ in {"目的地", "税务居留", "签证入境", "医疗安全"}:
        return "可能进入候选目的地或排除清单"
    if typ in {"积分", "住宿"}:
        return "可能进入待办清单"
    if typ == "财务自由":
        return "可能进入财务自由/提款策略备忘"
    return "仅作生活方式观察"


def life_relevant(item: Item) -> bool:
    text = f"{item.title} {item.summary}".lower()
    if (
        life_source_tier(item) not in {"官方", "专业机构"}
        and not life_is_travel_community_item(item)
        and not life_title_has_decision_signal(item)
    ):
        return False
    if any(k in text for k in ["giveaway", "sponsored", "coupon", "black friday", "celebrity", "viral"]):
        return False
    if any(k in text for k in ["itinerary", "things to do", "must to do", "must do in", "guide to culture", "restaurants and local culture"]):
        return False
    if any(k in text for k in ["veers off runway", "smashes signs", "dramatic aviation incident", "crash footage"]):
        return False
    if "whoop" in text and not any(k in text for k in ["travel health", "medical insurance", "destination", "hospital", "vaccine"]):
        return False
    if "easy-to-forget debts" in text or "embarrassing debt reminder" in text:
        return False
    if "monthly megathread" in text or "pivoting to photography" in text:
        return False
    if "portfolio charts just got a huge upgrade" in text:
        return False
    if "nerf gun incident" in text or "take a look in the mirror" in text:
        return False
    # US credit-card reviews are relevant to the user when they affect points, hotel, airline or premium travel value.
    us_only = any(k in text for k in ["medicare", "aca", "social security", "state tax", "roth ira", "401(k)", "401k"])
    cross_border = any(k in text for k in ["hong kong", "non-us", "expat", "cross-border", "international", "global", "residency"])
    if us_only and not cross_border:
        return False
    return any(
        k in text
        for k in [
            "withdrawal",
            "retirement",
            "spending",
            "tax",
            "residency",
            "visa",
            "immigration",
            "entry requirement",
            "travel advice",
            "safety",
            "health",
            "medical",
            "cost of living",
            "quality of life",
            "expat",
            "nomad",
            "hotel",
            "airline",
            "points",
            "miles",
            "business class",
            "transfer bonus",
            "award",
            "credit card",
            "annual fee",
            "best rate guarantee",
            "long stay",
            "long-stay",
            "monthly stay",
            "healthcare",
            "health insurance",
            "portfolio",
            "lifestyle",
            "luxury",
            "resort",
            "suite",
            "villa",
            "lodge",
            "breakfast",
            "late checkout",
            "upgrade",
            "fhr",
            "virtuoso",
            "amex",
            "safari",
            "cruise",
            "family trip",
            "parents",
            "overwater",
            "maldives",
            "bora bora",
            "cash rate",
            "award availability",
            "cents per point",
            "refundable",
            "cancellation",
        ]
    )


def life_score(item: Item) -> int:
    typ = life_item_type(item)
    tier = life_source_tier(item)
    score = 20
    score += {"官方": 45, "专业机构": 40, "高质量工具": 38, "高质量博客": 30, "高质量社区知识库": 30, "经验源": 18, "论坛经验": 12, "中等可信": 8}.get(tier, 10)
    score += {"税务居留": 25, "签证入境": 25, "医疗安全": 24, "财务自由": 22, "目的地": 18, "积分": 16, "住宿": 16, "生活方式": 10}.get(typ, 8)
    if life_decision_impact(item) == "高":
        score += 12
    if item.source.startswith("r/"):
        score -= 8
    return score


def life_travel_community_heading(item: Item) -> str:
    text = f"{item.title} {item.summary}".lower()
    if "maldives" in text and "bora bora" in text:
        return "Maldives 与 Bora Bora 奢华海岛慢旅比较"
    if "seoul" in text and ("four seasons" in text or "josun palace" in text):
        return "首尔四季与 Josun Palace 高端酒店体验复盘"
    if ("tokyo" in text or "kyoto" in text or "japan" in text) and any(
        k in text for k in ["hotel", "hyatt", "marriott", "points", "award", "breakfast"]
    ):
        return "日本高端酒店与积分兑换讨论"
    if "fhr" in text or "virtuoso" in text:
        return "FHR / Virtuoso 预订渠道与酒店权益讨论"
    if "safari" in text:
        return "Safari 高端住宿与安全医疗保障讨论"
    if "cruise" in text:
        return "高端邮轮与积分/套房体验讨论"
    if any(k in text for k in ["family", "parents", "kids", "children", "elderly"]):
        return "高端家庭慢旅：舒适度、节奏与酒店选择"
    if any(k in text for k in ["points", "miles", "award", "hyatt", "marriott", "hilton"]):
        return "高端旅行积分兑换案例"
    if any(k in text for k in ["resort", "suite", "villa", "lodge", "overwater"]):
        return "高端度假酒店选择与长住舒适度讨论"
    return "高端旅行社区案例：住宿、预算与体验取舍"


def life_is_hyatt_award_chart_item(item: Item) -> bool:
    title = item.title.lower()
    return "hyatt" in title and "award chart" in title and not life_is_world_hyatt_award_cost_increase_item(item)


def life_is_world_hyatt_award_cost_increase_item(item: Item) -> bool:
    title = item.title.lower()
    return "world of hyatt updates award chart" in title and "67%" in title


def life_is_emirates_devaluation_item(item: Item) -> bool:
    title = item.title.lower()
    return "emirates skywards" in title and any(k in title for k in ["devalue", "devalues", "devaluation"])


def life_is_all_americas_sale_item(item: Item) -> bool:
    title = item.title.lower()
    return "all americas" in title and "40% off" in title


def life_is_thailand_visa_item(item: Item) -> bool:
    title = item.title.lower()
    return "thailand" in title and "visa-free" in title


def life_is_priority_pass_credit_card_item(item: Item) -> bool:
    title = item.title.lower()
    return "priority pass" in title and ("credit card" in title or "credit cards" in title)


def life_is_fanatics_amex_credit_card_item(item: Item) -> bool:
    title = item.title.lower()
    return "fanatics" in title and "amex membership rewards" in title and "credit card" in title


def life_is_hilton_amex_card_offer_item(item: Item) -> bool:
    title = item.title.lower()
    return "hilton american express" in title and "card" in title and "bonus points" in title


def life_is_specific_credit_card_item(item: Item) -> bool:
    return (
        life_is_priority_pass_credit_card_item(item)
        or life_is_fanatics_amex_credit_card_item(item)
        or life_is_hilton_amex_card_offer_item(item)
    )


def life_title_zh(item: Item) -> str:
    title = item.title.strip()
    title_lower = item.title.lower()
    text = f"{item.title} {item.summary}".lower()
    if "last call" in title_lower and "hyatt award chart" in title_lower:
        return "Hyatt 奖励表与酒店类别调整 5 月 20 日生效：旧价预订最后窗口"
    if life_is_world_hyatt_award_cost_increase_item(item):
        return "World of Hyatt 奖励表已更新：部分兑换成本最高上调 67%"
    if life_is_hyatt_award_chart_item(item):
        return "Hyatt 奖励表调整初评：小幅震动而非大地震"
    if life_is_emirates_devaluation_item(item):
        return "Emirates Skywards 再次贬值里程，但仍有一个有限亮点"
    if life_is_all_americas_sale_item(item):
        return "Accor ALL 美洲最高 40% 折扣和 2x/3x 积分，需在 May 21 前预订"
    if life_is_thailand_visa_item(item):
        return "泰国结束 60 天免签停留：长期旅居需重新核实签证路径"
    if life_is_priority_pass_credit_card_item(item):
        return "Priority Pass 机场贵宾室权益信用卡：年费、访客和使用频率要逐项核算"
    if life_is_fanatics_amex_credit_card_item(item):
        return "Fanatics 将加入 Amex Membership Rewards 转点伙伴并推出新信用卡"
    if life_is_hilton_amex_card_offer_item(item):
        return "Hilton American Express 新卡奖励：最高 175,000 点，需核算年费和真实兑换价值"
    if "maldives" in text and "bora bora" in text:
        return "Maldives 与 Bora Bora 奢华海岛家庭积分旅行比较"
    if "tokyo and kyoto hotels" in title_lower:
        return "东京和京都酒店选择：早餐、套房升级与延迟退房"
    if "fhr" in title_lower and "virtuoso" in title_lower and "safari" in title_lower:
        return "Safari Lodge 预订：Amex FHR 还是 Virtuoso 更合适？"
    if "seoul" in title_lower and ("four seasons" in title_lower or "josun palace" in title_lower):
        return "首尔酒店评测：Four Seasons 与 Josun Palace"
    if "sail to a good life" in title_lower and "richer retirement portfolio" in title_lower:
        return "用更富足退休组合驶向高质量退休生活"
    if "how to “lie” with personal finance" in title_lower or "how to \"lie\" with personal finance" in title_lower:
        return "如何用个人理财数据“说谎”：第三部分，多元化"
    if "safe withdrawal rate" in title_lower and ("momentum" in title_lower or "trend-following" in title_lower):
        return "能否用动量/趋势跟踪提高安全提款率？SWR 系列第 63 篇"
    if "kempinski" in title_lower and "best rate guarantee" in title_lower:
        return "读者来信：马耳他 Gozo Kempinski 酒店最优价格保证争议"
    if "gha discovery double d$" in title_lower and "almanac hotels" in title_lower:
        return "GHA Discovery Almanac 酒店双倍 D$ 促销：入住至 2026-12-31，预订至 2026-08-15"
    if "chase ultimate rewards" in title_lower and "southwest" in title_lower and "30%" in title_lower:
        return "Chase Ultimate Rewards 转 Southwest Rapid Rewards 30% 奖励，截止 2026-06-05"
    if "best western rewards" in title_lower and "1,000" in title_lower:
        return "Best Western 在意大利和马耳他每晚 1,000 奖励积分促销"
    if "how to earn bilt points with rakuten" in title_lower:
        return "如何通过 Rakuten 网购赚取 Bilt 积分，是否值得？"
    if "coastfire plans in asia" in title_lower:
        return "亚洲 CoastFIRE 计划：我是不是判断错了？"
    if "english speaking expatfire destinations" in title_lower:
        return "英语环境 ExpatFIRE 目的地选择"
    if "retirement spending and lifestyle after financial independence" in title_lower:
        return "财务自由后的退休消费与生活方式取舍"
    if "housing is not an afterthought" in title_lower:
        return "退休住房不是附属问题"
    if "withdrawal rates and global retirement portfolios" in title_lower:
        return "提款率与全球退休组合"
    if "digital nomad visa and residence planning" in title_lower:
        return "数字游民签证与居留规划"
    if "world of hyatt promotion" in title_lower:
        return "World of Hyatt 长住促销"
    if "world of hyatt updates award chart" in title_lower:
        return "World of Hyatt 奖励表更新：部分兑换成本最高上调 67%"
    if "last call" in title_lower and "hyatt award chart" in title_lower:
        return "Hyatt 奖励表与酒店类别调整 5 月 20 日生效：旧价预订最后窗口"
    if "wells fargo rewards transfer partners" in title_lower:
        return "Wells Fargo Rewards 转点伙伴与积分兑换方法"
    if "credit card transfer bonuses" in title_lower:
        return "5 月信用卡转点奖励：Marriott 55%、Southwest 30%、Aeroplan 25% 等"
    if "avianca lifemiles" in title_lower and "fraud" in title_lower:
        return "Avianca LifeMiles 风控/欺诈标记争议：里程兑换执行风险"
    if "french" in title_lower and "palace" in title_lower and "hotel status" in title_lower:
        return "法国 Palace 酒店评级更新：3 家失去资格、约 5 家受益"
    if "6 nights in shanghai" in title_lower:
        return "上海 6 晚高端住宿与行程取舍"
    if "caribbean/mexico" in title_lower and "anniversary" in title_lower:
        return "加勒比/墨西哥 10 周年纪念旅行：高端度假选择"
    if "grand hotel tremezzo" in title_lower:
        return "Lake Como Grand Hotel Tremezzo 住宿评测"
    if "alternatives to aman" in title_lower:
        return "Aman 替代酒店选择：高端住宿体验与价格取舍"
    if "park hyatt vienna" in title_lower:
        return "Park Hyatt Vienna 住宿评测"
    if "rosewood vienna" in title_lower:
        return "Rosewood Vienna 住宿评测"
    if "family friendly national parks" in title_lower and "under canvas" in title_lower:
        return "适合家庭的美国国家公园高端住宿：Under Canvas 是否合适？"
    if "credit card review" in title_lower:
        return "信用卡评测：" + life_heading(item)
    translated = life_heading(item)
    if translated and translated != title:
        return translated
    return "待人工复核的标题翻译"


def life_display_title(item: Item) -> str:
    return f"{item.title}｜{life_title_zh(item)}"


def life_heading(item: Item) -> str:
    title = item.title
    text = f"{item.title} {item.summary}".lower()
    if life_is_travel_community_item(item):
        return life_travel_community_heading(item)
    if life_is_world_hyatt_award_cost_increase_item(item):
        return "World of Hyatt 奖励表上调：最高 67% 成本增加"
    if life_is_hyatt_award_chart_item(item):
        return "Hyatt 奖励表调整初评"
    if life_is_emirates_devaluation_item(item):
        return "Emirates Skywards 里程再次贬值"
    if life_is_all_americas_sale_item(item):
        return "Accor ALL 美洲促销：折扣、倍数积分与预订截止日"
    if life_is_thailand_visa_item(item):
        return "泰国 60 天免签停留变化"
    if life_is_priority_pass_credit_card_item(item):
        return "信用卡贵宾室权益：Priority Pass 使用价值"
    if life_is_fanatics_amex_credit_card_item(item):
        return "信用卡与转点伙伴：Fanatics 加入 Amex Membership Rewards"
    if life_is_hilton_amex_card_offer_item(item):
        return "Hilton American Express 信用卡奖励"
    if "withdrawal" in text and ("3.9%" in text or "safe" in text):
        return "动态提款率与退休收入安全边际"
    if "housing is not an afterthought" in text:
        return "退休住房不是附属问题：居住地影响现金流与生活质量"
    if "6m nw" in text and ("zero debt" in text or "12 months" in text):
        return "600 万美元净资产退休一年复盘：无债家庭的生活节奏与支出检查"
    if "tax residency" in text or "crs" in text:
        return "跨境慢旅的税务居民规则提醒"
    if "portugal" in text and ("travel advice" in text or "entry" in text):
        return "Portugal 入境、安全与健康建议更新"
    if "nomad visa" in text or "residence planning" in text:
        return "数字游民签证与第二生活基地规划"
    if "transfer bonus" in text or "conversion bonus" in text or "business class" in text:
        return "商务舱/酒店积分机会与截止日期"
    if "best rate guarantee" in text:
        return "酒店最优价格保证与权益兑现风险"
    if "bonus points" in text or "best western rewards" in text or "hotel promotion" in text:
        return "酒店积分促销：长住路线是否值得参与"
    if "credit card" in text or "card review" in text or "annual fee" in text:
        return "美国信用卡评测：积分、年费与旅行权益"
    if "outsite" in text or ("place to stay" in text and "digital nomad" in text):
        return "Outsite / 共居长住：数字游民住宿是否适合慢旅"
    if "english speaking expatfire destinations" in text:
        return "英语环境 ExpatFIRE 目的地讨论"
    if "coastfire" in text and "asia" in text:
        return "亚洲 CoastFIRE 旅居计划风险讨论"
    if "cost of living" in text or "quality of life" in text:
        return "慢旅目的地生活成本与生活质量观察"
    typ = life_item_type(item)
    if typ == "财务自由":
        return "财务自由与退休收入规划线索"
    if typ == "目的地":
        return "慢旅目的地候选线索"
    if typ == "生活方式":
        return "环球慢旅生活方式观察"
    if typ == "积分":
        return "航空酒店积分实操线索"
    return f"{typ}相关变化"


def life_summary(item: Item) -> str:
    text = clean_text(item.summary, 1200)
    lower = f"{item.title} {text}".lower()
    if life_is_world_hyatt_award_cost_increase_item(item):
        return "文章讨论 World of Hyatt 奖励表正式更新，标题明确给出的核心变化是部分兑换成本最高上调 67%。这属于酒店积分体系重新定价或贬值风险，重点应放在具体酒店、日期、新旧点数差异、现金价、取消/改订规则，以及是否需要提前锁定已有住宿计划。"
    if life_is_hyatt_award_chart_item(item):
        return "文章讨论 Hyatt 奖励房类别和 award chart 调整，核心是比较新旧 category、每晚点数、现金价和取消/改订弹性。它不是转点 bonus 文章，也不应被写成商务舱机会。"
    if life_is_emirates_devaluation_item(item):
        return "文章讨论 Emirates Skywards 里程再次贬值，重点是部分兑换需要更多 miles，仍需单独核实哪些航线、舱位或伙伴规则保留价值。它不是酒店早餐或套房升级权益文章。"
    if life_is_all_americas_sale_item(item):
        return "文章讨论 Accor ALL 美洲促销：最高 40% 折扣、2x/3x points、入住窗口 June 4 至 December 17, 2026，并要求在 May 21 前预订。判断价值时要比较现金价、预付/取消条款、适用酒店和路线匹配度。"
    if life_is_thailand_visa_item(item):
        return "帖子讨论 Thailand 结束 60-day visa-free stay 对数字游民和长期旅居者的影响。重点是重新核实官方入境规则、visa run 风险、保险要求和长住可行性，而不是 Portugal 或 Spain 的目的地建议。"
    if life_is_priority_pass_credit_card_item(item):
        return "文章比较提供 Priority Pass airport lounge access 的信用卡，重点不是酒店积分促销，而是年费、访客权益、餐厅额度、注册要求和你实际进入贵宾室的频率。对经常跨洲飞行或在美国机场中转的人，这类权益要按真实使用次数折算，而不是按宣传估值照单全收。"
    if life_is_fanatics_amex_credit_card_item(item):
        return "文章讨论 Fanatics 将加入 Amex Membership Rewards 转点伙伴，并计划推出一张新信用卡。核心问题是转点比例、Fanatics 积分的真实使用场景、新卡年费和开卡奖励是否能形成可兑现价值；它不是普通酒店积分促销，也不应被写成住宿折扣。"
    if life_is_hilton_amex_card_offer_item(item):
        return "文章讨论 Hilton American Express 信用卡新 offer，最高可获得 175,000 Hilton bonus points。判断价值时要同时看年费、消费门槛、免费房晚券、Hilton 会籍、点数真实兑换价值和你未来是否有 Hilton 住宿路线，而不是把 bonus points 直接归类为酒店促销。"
    if "3.9%" in lower and "withdrawal" in lower:
        return "内容关注退休收入和安全提款率，提到 3.9% 起始安全提款率，以及通过弹性支出提高终身消费能力。对宽裕财务自由而言，重点不是极限省钱，而是把提款规则、风险缓冲和高质量消费结合起来。"
    if "withdrawal" in lower and ("sequence risk" in lower or "flexible spending" in lower or "retirement portfolio" in lower):
        return "内容讨论退休提款率、序列风险和弹性支出规则。对宽裕财务自由而言，重点是把未来慢旅预算、现金缓冲和资产配置放在同一套提款框架里。"
    if "housing is not an afterthought" in lower or ("housing" in lower and "retirement" in lower):
        return "内容强调住房不是退休规划的附属项。对未来环球慢旅而言，住房会同时影响现金流、税务居民风险、医疗可达性、家庭稳定感和是否需要保留一个长期基地。"
    if "6m nw" in lower and ("zero debt" in lower or "12 months" in lower):
        return "社区案例来自 55/56 岁、约 600 万美元净资产且无债家庭的退休一年复盘。对宽裕财务自由的参考价值在于观察从积累期转向消费期后，真实支出、心理安全感、现金缓冲和生活节奏是否匹配，而不是只看资产数字。"
    if "tax residency" in lower or "crs" in lower:
        return "内容提醒跨境旅居不能只看停留天数，税务居民身份通常由当地国内法决定，并可能影响 CRS 信息交换、投资账户披露和长期停留安排。"
    if "entry requirements" in lower or "travel advice" in lower:
        return "官方旅行建议涉及入境要求、安全和健康信息，适合在计划 1-3 个月慢旅前作为第一层核验，而不是依赖旅游博客或论坛经验。"
    if "transfer bonus" in lower or "conversion bonus" in lower or "business class" in lower:
        return "内容涉及转点 bonus、航司/酒店积分或商务舱兑换机会。对慢旅的价值在于提高长途移动舒适度和现金效率，但需要核实截止日期、可用航线和真实兑换空间。"
    if "hotel promotion" in lower or "elite night" in lower or "hotel points promotion" in lower:
        return "内容涉及酒店积分促销或会员权益。对慢旅的价值取决于能否匹配 1-3 个月路线、是否有可订房、现金价是否过高以及积分/房晚是否真能兑现。"
    if "bonus points" in lower or "best western rewards" in lower:
        return "内容涉及酒店积分促销。对慢旅的价值取决于是否覆盖你的路线、现金价是否偏高、每晚额外积分是否超过机会成本，以及条款是否允许长住连续累计。"
    if "best rate guarantee" in lower:
        return "内容涉及酒店最优价格保证或会员权益兑现争议。对长期慢旅的意义不是单次省钱，而是提醒高端酒店预订要保留条款截图、比价证据和取消窗口。"
    if "credit card" in lower or "card review" in lower or "annual fee" in lower:
        return "内容是美国信用卡评测，应从积分获取、年费、酒店/航司权益、转点伙伴、境外使用成本和实际可兑换价值来判断，而不是只看开卡奖励或返佣文案。"
    if "outsite" in lower or ("place to stay" in lower and "digital nomad" in lower):
        return "内容介绍面向数字游民的共居/长住住宿。对环球慢旅的价值在于降低找房、网络、社群和短租切换摩擦；需要进一步核实城市覆盖、月租价格、取消规则、安静程度和是否适合 1-3 个月停留。"
    if "english speaking expatfire destinations" in lower:
        return "社区讨论围绕英语环境下的海外 FIRE 目的地选择。它适合作为候选国家/城市的线索池，但关于医疗、签证、税务和安全的结论必须回到官方资料核实。"
    if "coastfire" in lower and "asia" in lower:
        return "社区案例围绕在亚洲执行 CoastFIRE 或半退休计划的可行性，重点不是短期旅行，而是收入持续性、签证/居留、医疗保险、税务居民风险和生活成本是否同时成立。它适合作为海外慢旅与工作强度切换的反面检查清单。"
    if life_is_travel_community_item(item):
        if any(k in lower for k in ["points", "miles", "award", "hyatt", "marriott", "hilton"]):
            return "这是 Reddit 高端旅行社区的积分/酒店案例讨论，正文重点通常在真实兑换成本、现金价、房型权益、取消规则和家庭舒适度。它适合作为未来慢旅路线和积分使用的实操线索，但不能替代酒店官网、航司库存和条款核验。"
        if any(k in lower for k in ["fhr", "virtuoso", "amex", "breakfast", "suite", "late checkout", "upgrade"]):
            return "这是 Reddit 高端旅行社区的酒店权益和预订渠道讨论，重点在早餐、套房升级、延迟退房、度假村 credit、取消政策和现金价差。对宽裕慢旅的价值在于判断多花现金是否换来确定的舒适度，而不是只看账面折扣。"
        if any(k in lower for k in ["maldives", "bora bora", "safari", "family", "parents", "children"]):
            return "这是 Reddit 高端旅行社区的目的地和家庭慢旅案例，正文围绕舒适度、转机/接驳疲劳、季节天气、儿童或父母适配度、医疗安全和取消政策展开。它适合作为候选目的地的体验层线索，后续仍要用官方签证、保险和医疗信息核实。"
        return "这是 Reddit 高端旅行社区的经验帖，适合补充普通 RSS 不覆盖的真实预算、酒店体验和路线取舍。结论只能作为案例观察，涉及安全、医疗、签证、税务或保险时需要另行核验。"
    if "portugal" in lower and ("long-stay" in lower or "cost of living" in lower or "healthcare" in lower):
        return "内容把 Portugal 作为长期停留候选地，关注居留选项、医疗可达性、生活成本和飞行连接。适合进入慢旅候选池，但仍要核实签证、税务居民和保险规则。"
    if "residence" in lower or "second base" in lower or "nomad" in lower:
        return "内容适合作为第二生活基地或数字游民签证线索，但不能直接当成税务或移民结论；需要回到官方签证、税务居民和医疗保险规则核实。"
    if item.source.startswith("r/"):
        return "这是社区经验或案例讨论，只能用于发现问题和生活方式盲点。涉及税务、签证、医疗或保险时，必须再用官方和专业来源核实。"
    if life_generic_detail_points(item):
        return f"正文已抓取到可用信息，主要影响{life_item_type(item)}判断；具体要点见下方。"
    return ""


def life_sentence_point(sentence: str, item_type: str = "") -> str:
    lower = sentence.lower()
    raw_nums = re.findall(r"-?\d+(?:\.\d+)?(?:%|x| nights?| days?| years?| months?|\+)?", sentence, flags=re.I)
    clean_nums: list[str] = []
    for num in raw_nums:
        digits = re.sub(r"\D", "", num)
        if len(digits) > 4 and not num.endswith("%"):
            continue
        clean_nums.append(num)
        if len(clean_nums) >= 4:
            break
    nums = ", ".join(clean_nums)
    if any(k in lower for k in ["points per night", "per night", "award availability", "cents per point", "cpp"]):
        suffix = f" 关键数字：{nums}。" if nums else ""
        return "正文涉及每晚点数、奖励房库存、现金价或点数估值，应把兑换价值和可取消性一起看。" + suffix
    if any(k in lower for k in ["breakfast", "suite upgrade", "late checkout", "resort credit", "fhr", "virtuoso"]):
        suffix = f" 关键数字：{nums}。" if nums else ""
        return "正文讨论早餐、套房升级、延迟退房或酒店礼遇，需要判断这些权益是否真实提升长住舒适度，而不只是账面价值。" + suffix
    if any(k in lower for k in ["parents", "children", "kids", "elderly", "family-friendly", "family friendly"]):
        return "正文涉及父母、孩子或多代家庭出行，重点应放在接驳疲劳、房型、餐饮、医疗可达性和每天活动节奏。"
    if any(k in lower for k in ["maldives", "bora bora", "overwater", "villa", "resort", "lodge", "safari"]):
        suffix = f" 关键数字：{nums}。" if nums else ""
        return "正文涉及高端度假村、海岛别墅或 safari lodge，适合比较季节天气、接驳复杂度、取消规则和医疗转运安排。" + suffix
    if any(k in lower for k in ["cash rate", "refundable", "cancellation", "travel insurance", "medical evacuation"]):
        suffix = f" 关键数字：{nums}。" if nums else ""
        return "正文涉及现金价、可取消条款、旅行保险或医疗转运，适合放入高端慢旅预订前检查清单。" + suffix
    if "flat bed" in lower or "business class" in lower or "premium cabin" in lower:
        suffix = f" 关键时间/数字：{nums}。" if nums else ""
        return "正文涉及商务舱/平躺座椅或高端舱位供给变化，可能影响未来长途航线舒适度和里程兑换价值。" + suffix
    if item_type != "住宿" and ("annual fee" in lower or "credit card" in lower or "admirals club" in lower):
        suffix = f" 关键数字：{nums}。" if nums else ""
        return "正文涉及美国信用卡的年费、权益或贵宾室使用规则，需要按真实使用频率和可替代权益估值。" + suffix
    points_context = item_type in {"积分", "住宿"} or any(
        k in lower for k in ["marriott", "hyatt", "southwest", "airline", "hotel", "credit card", "award flight", "award night"]
    )
    if points_context and (
        "transfer bonus" in lower
        or "conversion bonus" in lower
        or "hotel points" in lower
        or "credit card points" in lower
        or "airline miles" in lower
        or "award night" in lower
        or "award flight" in lower
        or re.search(r"\bmiles\b", lower)
    ):
        suffix = f" 关键数字：{nums}。" if nums else ""
        return "正文涉及积分、里程、转点或奖励兑换，应核实截止日期、可用库存、现金价和点数估值。" + suffix
    if "best rate guarantee" in lower or "rate guarantee" in lower:
        return "正文涉及酒店最优价格保证或权益兑现争议，适合沉淀为高端酒店预订和维权的检查清单。"
    if "retirement" in lower and ("housing" in lower or ("home" in lower and "home country" not in lower)):
        return "正文把住房纳入退休规划，提示居住地会影响现金流、生活质量、医疗可达性和长期基地选择。"
    if "withdrawal" in lower or "sequence risk" in lower:
        suffix = f" 关键数字：{nums}。" if nums else ""
        return "正文讨论退休提款、序列风险或长期支出规则，适合纳入慢旅预算和资产配置压力测试。" + suffix
    if "tax residency" in lower or "tax residence" in lower or "crs" in lower:
        return "正文涉及税务居民或 CRS 相关规则，慢旅停留天数和账户申报风险需要用官方资料核实。"
    if "visa" in lower or "residence permit" in lower or "digital nomad" in lower:
        return "正文涉及签证、居留或数字游民安排，不能只看可入境，还要核实税务、保险和停留天数后果。"
    if "healthcare" in lower or "health insurance" in lower or "medical" in lower:
        return "正文涉及医疗或保险条件，适合纳入目的地评分中的医疗质量、保险覆盖和紧急处理能力。"
    if "long stay" in lower or "monthly" in lower or "coliving" in lower:
        return "正文涉及长住或共居安排，重点应看月租、网络、取消规则、噪音、厨房洗衣和生活便利性。"
    if "cost of living" in lower or "quality of life" in lower:
        return "正文涉及生活成本或生活质量，适合进入候选城市评分，但需与 Numbeo/OECD/官方医疗安全数据交叉核对。"
    return ""


def life_generic_detail_points(item: Item, limit: int = 4) -> list[str]:
    sentences = split_article_sentences(item.summary)
    points: list[str] = []
    seen: set[str] = set()
    item_type = life_item_type(item)
    title_lower = item.title.lower()
    for sentence in sentences:
        point = life_sentence_point(sentence, item_type)
        if not point:
            continue
        if point.startswith("正文涉及美国信用卡") and not any(
            marker in title_lower for marker in ["credit card", "card guide", "card review", "admirals club", "aadvantage globe"]
        ):
            continue
        if ("退休提款" in point or "提款率" in point or "序列风险" in point) and item_type != "财务自由":
            continue
        if "酒店最优价格保证" in point and "best rate guarantee" not in title_lower:
            continue
        if "长住或共居" in point and item_type not in {"住宿", "目的地"}:
            continue
        key = norm_title(point.split("。")[0])
        if key in seen:
            continue
        seen.add(key)
        points.append(point)
        if len(points) >= limit:
            break
    return points


def life_summary_points(item: Item, limit: int = 5) -> list[str]:
    text = clean_text(item.summary, 4000)
    lower = f"{item.title} {text}".lower()
    title_lower = item.title.lower()
    points: list[str] = []
    seen: set[str] = set()

    def add(point: str) -> None:
        key = norm_title(point)
        if key in seen or not point:
            return
        seen.add(key)
        points.append(point)

    specific_title = False
    if life_is_world_hyatt_award_cost_increase_item(item):
        specific_title = True
        add("文章主题是 World of Hyatt 奖励表正式更新；标题给出的关键数字是部分兑换成本最高上调 67%。")
        add("对酒店积分策略的影响是：若已有 Hyatt 住宿计划，需要逐家核实具体酒店、日期、新旧点数、现金价、取消和改订规则。")
    if life_is_hyatt_award_chart_item(item):
        specific_title = True
        add("文章主题是 Hyatt award chart / category changes；应比较新旧 category、每晚点数、现金价和取消/改订弹性。")
        add("作者把这次变化形容为 tremor rather than a seismic shift，说明它更像局部奖励房重新定价，而不是整个 Hyatt 体系的全面贬值。")
    if life_is_emirates_devaluation_item(item):
        specific_title = True
        add("文章主题是 Emirates Skywards miles devaluation；重点是部分兑换需要更多 miles，里程购买力下降。")
        add("标题里的 silver lining 只表示仍可能有少数规则或兑换场景保留价值，不能抵消整体贬值风险。")
    if life_is_all_americas_sale_item(item):
        specific_title = True
        add("文章主题是 Accor ALL Americas sale：最高 40% off，并提供 2x/3x points。")
        add("关键时间窗是 stays June 4 - December 17, 2026，book by May 21；需要核实预付/取消条款、适用酒店和路线匹配度。")
    if life_is_thailand_visa_item(item):
        specific_title = True
        add("帖子主题是 Thailand ends 60-day visa-free stay；对数字游民和长期旅居者，核心影响是停留天数和入境路径需要重新核实。")
        add("进入路线规划前应查泰国官方入境规则、visa run 风险、旅行保险要求和是否仍适合 1-3 个月慢旅。")
    if life_is_priority_pass_credit_card_item(item):
        specific_title = True
        add("文章主题是提供 Priority Pass airport lounge access 的信用卡；核心比较项是年费、访客权益、餐厅额度、注册要求和贵宾室网络覆盖。")
        add("这类权益的真实价值取决于你每年实际使用次数、常用机场是否有可用贵宾室、是否带同行者，以及权益是否足以抵消信用卡年费。")
    if life_is_fanatics_amex_credit_card_item(item):
        specific_title = True
        add("文章主题是 Fanatics 将加入 Amex Membership Rewards 转点伙伴，并推出一张新的联名或相关信用卡。")
        add("判断价值时要看 Amex 到 Fanatics 的转点比例、Fanatics 积分的真实兑换用途、新卡年费、开卡奖励和你是否真的会消费相关商品。")
    if life_is_hilton_amex_card_offer_item(item):
        specific_title = True
        add("文章主题是 Hilton American Express 信用卡新 offer，标题给出的核心数字是最高 175,000 Hilton bonus points。")
        add("评估重点包括年费、消费门槛、免费房晚券、Hilton 会籍、点数真实兑换价值和未来路线中是否有 Hilton 住宿需求。")
    if "tax residency" in title_lower or "crs jurisdictions" in title_lower:
        specific_title = True
        add("内容聚焦 CRS 参与辖区的税务居民规则；跨境慢旅不能只看入境停留天数，还要看当地国内法如何认定税务居民。")
        add("对香港读者和未来环球慢旅而言，重点是记录每个候选国家的税务居民触发条件、CRS 信息交换、资本利得和账户申报后果。")
    if "portugal travel advice" in title_lower:
        specific_title = True
        add("这是 Portugal 官方旅行建议更新线索，应作为入境、安全和健康要求的第一层核验来源。")
        add("进入路线规划前，需要复核入境要求、安全提示、医疗/保险要求和页面最后更新时间，而不是依赖旅游博客经验。")
    if "how to “lie” with personal finance" in title_lower or "how to \"lie\" with personal finance" in title_lower:
        specific_title = True
        add("文章讨论个人理财文章如何通过选择口径、时间窗口或案例来制造看似正确的结论；这一篇聚焦多元化叙事。")
        add("对宽裕财务自由规划的启发是：不要只看单一组合、单一国家或单一历史窗口下的漂亮结果，要看不同市场环境下的失败场景。")
    if "safe withdrawal rate" in title_lower and ("momentum" in title_lower or "trend-following" in title_lower):
        specific_title = True
        add("文章把动量/趋势跟踪放进安全提款率框架，核心问题是趋势信号能否改善退休提款期的序列风险。")
        add("这类结论需要看回测窗口、交易成本、税务摩擦和规则稳定性，不能只因为提高了历史 SWR 就直接用于真实提款。")
    if "gha discovery double d$" in title_lower and "almanac hotels" in title_lower:
        specific_title = True
        add("文章介绍 GHA Discovery 在 Almanac Hotels 的双倍 D$ 促销，入住窗口为 May 15 至 December 31, 2026。")
        add("预订窗口为 May 15 至 August 15；是否值得参与取决于 Almanac 酒店是否落在你的路线内、现金价是否合理、D$ 是否容易在后续住宿中用掉。")
    if "how to earn bilt points with rakuten" in title_lower:
        specific_title = True
        add("文章讨论通过 Rakuten 网购赚取 Bilt points 的方法，重点不是信用卡年费，而是购物门户返利和可转点积分之间的取舍。")
        add("判断是否值得要比较 Rakuten 现金返利、其他航空里程门户、Bilt 积分可转伙伴和你实际会不会使用这些积分。")
    if "transfer bonus" in title_lower:
        specific_title = True
        add("文章涉及转点或兑换 bonus；只有在目标酒店/航司现金价较高、奖励库存可订且点数估值合理时才有实际价值。")
        add("需要核实截止日期、可转点账户、目标酒店/航班库存和取消政策，避免为了 bonus 囤低流动性积分。")
    if "admirals club" in title_lower or "credit card guide" in title_lower or "card review" in title_lower:
        specific_title = True
        add("文章是美国信用卡/联名卡评测，核心要看年费、积分获取、权益兑现和境外使用成本。")
        if "admirals club" in lower:
            add("Admirals Club passes 一类权益要按实际航线和使用频率估值，不能只按宣传价计算。")
        if "foreign transaction" in lower:
            add("对香港读者还要单独看境外交易费、汇率成本和是否容易持有/还款。")
    if "world of hyatt updates award chart" in title_lower:
        specific_title = True
        add("文章讨论 World of Hyatt 奖励表调整，标题明确指出部分兑换成本最高上调 67%；这属于酒店积分贬值或重新定价风险。")
        add("对慢旅和酒店积分策略的影响是：若已有确定 Hyatt 住宿计划，需要在生效前核实具体酒店、日期、点数变化、取消和改订规则，而不是盲目囤点。")
    if "last call" in title_lower and "hyatt award chart" in title_lower:
        specific_title = True
        add("文章提醒 Hyatt 奖励表和酒店类别调整将在 May 20 生效，核心是旧价预订窗口即将关闭。")
        add("实操价值在于：如果已有确定 Hyatt 住宿计划，应在生效前逐家核实新旧点数、现金价、取消规则和是否允许后续改订。")
    if "wells fargo rewards transfer partners" in title_lower:
        specific_title = True
        add("文章梳理 Wells Fargo Rewards 的转点伙伴和积分兑换方式，重点是把银行积分转成航司/酒店积分后的实际可用性。")
        add("判断价值时应比较转点比例、目标伙伴奖励库存、现金价、税费、取消政策和你是否能在未来路线中真实使用。")
    if "credit card transfer bonuses" in title_lower:
        specific_title = True
        add("文章汇总 5 月信用卡转点奖励，标题列出的关键优惠包括 Marriott 55%、Southwest 30%、Aeroplan 25% 等。")
        add("转点前应逐项核实截止日期、目标积分价值、奖励库存和现金价；除非已有明确用途，否则 bonus 本身不构成转点理由。")
    if "avianca lifemiles" in title_lower and "fraud" in title_lower:
        specific_title = True
        add("文章来自读者评论，核心是 Avianca LifeMiles 在账户风控、欺诈标记或客服处理上的执行风险，而不是普通转点促销。")
        add("对里程策略的启发是：低价里程和复杂兑换可能伴随账户审核、出票失败、客服沟通和关键行程中断风险，重要行程不应只依赖单一里程计划。")
    if "french" in title_lower and "palace" in title_lower and "hotel status" in title_lower:
        specific_title = True
        add("文章讨论法国 Palace 酒店评级变化，标题给出的关键信息是 3 家酒店失去资格、约 5 家酒店受益。")
        add("对高端慢旅的价值在于把 Palace 评级当作酒店质量和定价信号之一，但仍要结合位置、真实房价、会员权益、取消规则和个人路线判断。")
    if "6 nights in shanghai" in title_lower:
        specific_title = True
        add("帖子围绕上海 6 晚停留展开，适合作为高端城市慢旅的住宿和行程取舍案例。")
        add("需要重点比较酒店位置、交通半径、早餐/升级权益、现金价或积分价、洗衣便利和每天移动强度；涉及具体酒店结论时仍要回到官网价格和近期评价核实。")
    if "family friendly national parks" in title_lower and "under canvas" in title_lower:
        specific_title = True
        add("帖子讨论适合家庭的美国国家公园目的地，并特别提到 Under Canvas 这类高端露营/住宿选择。")
        add("对家庭慢旅的关键不是景点清单，而是孩子适配度、天气、营地位置、夜间舒适度、取消规则、医疗可达性和是否值得为体验支付溢价。")
    if "sail to a good life" in title_lower and "richer retirement portfolio" in title_lower:
        specific_title = True
        add("Portfolio Charts 文章围绕“更富足退休组合”展开，重点是退休组合不只追求最低可行提款率，还要提高长期生活质量和支出弹性。")
        add("对宽裕版财务自由而言，关键问题是资产配置能否支持更高质量消费、长期慢旅预算和心理安全边际，而不是只证明退休数字勉强够用。")
    if "chase ultimate rewards" in title_lower and "southwest" in title_lower and "30%" in title_lower:
        specific_title = True
        add("文章介绍 Chase Ultimate Rewards 转 Southwest Rapid Rewards 的 30% 转点奖励，截止日期为 June 5, 2026。")
        add("Southwest 点数通常与现金票价联动，转点前应比较现金价、奖励票可用性、取消政策和点数实际价值。")
    if "best western rewards" in title_lower and "1,000" in title_lower:
        specific_title = True
        add("文章介绍 Best Western 在 Italy 和 Malta 的每晚 1,000 bonus points 促销，时间为 May 11 至 September 7, 2026。")
        add("这类促销更适合作为已有路线的附加收益；如果为了积分改变酒店或路线，通常要重新比较现金价、位置和会员权益。")
    if specific_title:
        return points[:limit]
    item_type = life_item_type(item)
    retirement_title = any(k in title_lower for k in ["withdrawal", "safe withdrawal", "swr", "retirement", "personal finance"])
    if "housing" in lower and "retirement" in lower and (item_type == "财务自由" or "housing" in title_lower):
        add("文章把住房视为退休现金流的一部分，而不是退休规划完成后的附属选择。")
        add("对环球慢旅而言，是否保留长期基地会影响税务居民风险、医疗可达性、家庭稳定感和年度固定开支。")
    if retirement_title and ("withdrawal" in lower or "sequence risk" in lower or "flexible spending" in lower):
        add("文章讨论提款率、序列风险和弹性支出规则，适合放进退休后 10 年慢旅预算压力测试。")
        if "3.9%" in lower:
            add("文中提到 3.9% 起始安全提款率；应结合个人资产配置、现金缓冲和实际消费弹性重新测算。")
        if "perpetual" in lower:
            add("如果目标是长期或永久性提款，不能只看传统 30 年退休窗口。")
    if "transfer bonus" in lower or "conversion bonus" in lower:
        add("文章涉及转点或兑换 bonus；只有在目标酒店/航司现金价较高、奖励库存可订且点数估值合理时才有实际价值。")
        add("需要核实截止日期、可转点账户、目标酒店/航班库存和取消政策，避免为了 bonus 囤低流动性积分。")
    title_lower = item.title.lower()
    if (
        "credit card" in title_lower
        or "card guide" in title_lower
        or "card review" in title_lower
        or "admirals club" in title_lower
    ) and life_item_type(item) != "住宿" and ("credit card" in lower or "annual fee" in lower or "admirals club" in lower):
        add("文章是美国信用卡/联名卡评测，核心要看年费、积分获取、权益兑现和境外使用成本。")
        if "admirals club" in lower:
            add("Admirals Club passes 一类权益要按实际航线和使用频率估值，不能只按宣传价计算。")
        if "foreign transaction" in lower:
            add("对香港读者还要单独看境外交易费、汇率成本和是否容易持有/还款。")
    if "best rate guarantee" in title_lower:
        add("文章围绕酒店最优价格保证或会员权益兑现争议，提醒长住和高端酒店预订要保存条款证据。")
        add("实操上应保存比价截图、取消窗口、房型条款和酒店书面回复，避免入住前后维权成本过高。")
    if "portugal" in lower:
        add("Portugal 被作为长期停留候选地时，应同时核实居留路径、医疗可达性、生活成本、航班连接和税务居民风险。")
    if "spain" in lower:
        add("Spain 相关慢旅规划要关注公私营医疗、长期租赁、当地登记、语言摩擦和保险覆盖。")
    if "digital nomad visa" in lower or "minimum income" in lower or "tax exposure" in lower:
        add("数字游民签证不能只看能否入境，还要核实最低收入、医疗保险、税务暴露和停留天数触发条件。")
    if "long stay" in lower or "monthly stay" in lower or "coliving" in lower or "outsite" in lower:
        add("长住/共居住宿的关键不是单晚价格，而是网络、安静程度、厨房洗衣、取消规则、社群密度和 1-3 个月稳定性。")
    if "elite night" in lower or "hotel promotion" in lower or "blackout" in lower:
        add("酒店促销要看 elite night credits、积分倍率、不可用日期、现金价和路线匹配度。")
    if "english speaking expatfire destinations" in lower:
        add("论坛把英语环境作为 ExpatFIRE 目的地筛选条件，但这只是线索，不能替代官方签证、税务、医疗和安全核验。")
    if life_is_travel_community_item(item):
        community_nums: list[str] = []
        for num in re.findall(r"(?:[$€£]\s*)?\d[\d,]*(?:\.\d+)?(?:%|x| points?| nights?| days?| years?| months?| per night)?", text, flags=re.I):
            cleaned_num = clean_text(num, 40)
            digits = re.sub(r"\D", "", cleaned_num)
            if not digits or len(digits) > 7:
                continue
            has_unit = bool(re.search(r"[$€£,%]|points?|nights?|days?|years?|months?|per night|x", cleaned_num, flags=re.I))
            if not has_unit and len(digits) > 2:
                continue
            if cleaned_num not in community_nums:
                community_nums.append(cleaned_num)
            if len(community_nums) >= 4:
                break
        if community_nums:
            add("帖中可提取的关键数字包括：" + "、".join(community_nums) + "；这些数字应与官网现金价、积分库存和取消条款一起复核。")
        if "maldives" in lower and "bora bora" in lower:
            add("帖子把 Maldives 和 Bora Bora 放在同一个高端海岛选择题里，核心不是景点，而是接驳疲劳、房型、季节天气、取消政策和家庭适配度。")
        if any(k in lower for k in ["points per night", "award availability", "hyatt", "marriott", "hilton", "120,000", "60,000", "35,000"]):
            add("帖子提供了积分兑换维度：需要比较每晚点数、奖励房库存、现金价、点数估值和取消规则。")
        if any(k in lower for k in ["breakfast", "suite upgrade", "late checkout", "resort credit", "fhr", "virtuoso"]):
            add("帖子关注早餐、套房升级、延迟退房或酒店礼遇，这些要按确定性和实际舒适度估值，而不是只按宣传金额估值。")
        if any(k in lower for k in ["parents", "children", "kids", "elderly", "family-friendly", "family friendly"]):
            add("帖子涉及父母、孩子或多代家庭出行，慢旅筛选要额外看接驳强度、安静房型、餐饮便利和医疗可达性。")
        if any(k in lower for k in ["cash rates", "cash rate", "refundable", "cancellation", "travel insurance", "medical evacuation"]):
            add("帖子把现金价、可取消条款、旅行保险和医疗转运放进讨论，适合沉淀为高端慢旅预订前检查清单。")
        if "tokyo" in lower or "kyoto" in lower or "japan" in lower:
            add("日本高端酒店讨论要同时看交通位置、洗衣便利、安静程度、连续入住稳定性和是否适合带父母慢节奏移动。")
        if "safari" in lower:
            add("Safari lodge 讨论必须额外核实医疗转运、保险覆盖、接送安排和取消政策，不能只比较房价和礼遇。")
    if "6m nw" in lower and "zero debt" in lower:
        add("社区案例显示约 600 万美元净资产且无债并不等于规划结束，退休第一年仍要观察支出、心理安全感和生活节奏。")
    if "healthcare" in lower or "health insurance" in lower:
        add("医疗和保险是慢旅目的地评分的硬变量，需要确认公私营医疗、保险可报销范围和紧急转运安排。")
    if (
        not specific_title
        and life_source_tier(item) not in {"官方", "专业机构"}
        and not life_is_travel_community_item(item)
        and not life_title_has_decision_signal(item)
    ):
        return []
    for point in life_generic_detail_points(item, limit=limit):
        add(point)
    return points[:limit]


def life_has_enough_summary_evidence(item: Item) -> bool:
    points = life_summary_points(item)
    if len(points) >= 2:
        return True
    if len(points) >= 1 and life_source_tier(item) in {"官方", "专业机构"} and life_item_type(item) in {"税务居留", "签证入境", "医疗安全"}:
        return True
    return False


def life_action(item: Item) -> str:
    typ = life_item_type(item)
    if typ == "财务自由":
        return "把提款率、现金缓冲、资产配置和未来 10 年慢旅预算放到同一张压力测试表里。"
    if typ == "税务居留":
        return "若该国家进入候选池，手动核实税务居民天数、CRS、资本利得、遗产税和医疗保险要求。"
    if typ in {"签证入境", "医疗安全"}:
        return "在加入路线前核对官方入境、疫苗、医疗和旅行保险要求，并记录最后更新时间。"
    if typ == "目的地":
        return "按生活质量、医疗、安全、长租、签证、税务风险和飞行可达性给目的地打分。"
    if typ == "积分":
        return "只在与你持有的航司、酒店计划或可转点货币相关时进入待办，并核实截止日期和真实兑换价值。"
    if typ == "住宿":
        return "把房型、取消窗口、比价证据、会员权益和长住舒适度放入预订前检查清单。"
    return "保留为生活方式观察，不直接形成行动。"


def life_detailed_summary(item: Item, points: list[str] | None = None) -> str:
    points = points if points is not None else life_summary_points(item)
    zh_title = life_title_zh(item)
    if points:
        selected = [point.rstrip("。；; ") for point in points[:3]]
        connector = "帖子" if life_source_tier(item) == "论坛经验" else "文章"
        return f"{connector}《{zh_title}》的核心信息是：" + "；".join(selected) + "。"
    fallback = life_summary(item)
    if fallback:
        return f"《{zh_title}》的核心内容：{fallback}"
    return f"《{zh_title}》未抓取到足够正文细节，暂不应作为强结论，只保留标题和来源供人工复核。"


def pick_life_items(items: list[Item], limit: int = 8) -> list[Item]:
    relevant = [item for item in items if life_relevant(item) and life_has_enough_summary_evidence(item)]
    relevant.sort(key=lambda item: (life_score(item), parse_date(item.published) or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
    picked: list[Item] = []
    seen_titles: set[str] = set()
    type_counts: dict[str, int] = {}
    for item in relevant:
        heading = life_display_title(item)
        key = norm_title(heading)
        if key in seen_titles:
            continue
        typ = life_item_type(item)
        if type_counts.get(typ, 0) >= 3:
            continue
        seen_titles.add(key)
        type_counts[typ] = type_counts.get(typ, 0) + 1
        picked.append(item)
        if len(picked) >= limit:
            break
    return picked


def append_life_item(lines: list[str], item: Item, idx: int) -> None:
    points = life_summary_points(item)
    lines += [
        f"### {idx}. {life_display_title(item)}",
        f"- 来源：{item.source}",
        f"- 标题：{life_display_title(item)}",
        f"- 链接：{item.url}",
        f"- 类型：{life_item_type(item)}",
        f"- 来源等级：{life_source_tier(item)}",
        f"- 决策影响：{life_decision_impact(item)}",
        f"- 是否需要人工核实：{life_needs_human_check(item)}",
        f"- 是否进入候选目的地或待办清单：{life_candidate_status(item)}",
        "",
        f"**内容总结**：{life_detailed_summary(item, points)}",
        "",
    ]
    if points:
        lines.append("**内容要点**：")
        for point in points:
            lines.append(f"- {point}")
        lines.append("")
    lines += [
        f"**下一步**：{life_action(item)}",
        "",
    ]


def supplement_life_with_community(picked: list[Item], seen_headings: set[str], limit: int = 8) -> int:
    if len(picked) >= limit:
        return 0
    items: list[Item] = []
    for feed in LIFE_COMMUNITY_FALLBACK_FEEDS:
        items.extend(parse_feed(feed.source, feed.url, limit=feed.limit))
        time.sleep(0.1)
    candidates = [item for item in filter_previously_sent("life", sort_recent(items), days=1) if life_relevant(item)]
    candidates.sort(
        key=lambda item: (life_score(item), parse_date(item.published) or datetime.min.replace(tzinfo=timezone.utc)),
        reverse=True,
    )
    added = 0
    seen_urls = {canonical_url(item.url) for item in picked}
    for item in pick_life_items([enrich_article_item(item) for item in candidates[:24]], limit=limit):
        if len(picked) >= limit:
            break
        if not life_has_enough_summary_evidence(item):
            continue
        if life_is_travel_community_item(item) and len(life_summary_points(item)) < 3:
            continue
        heading_key = norm_title(life_display_title(item))
        url_key = canonical_url(item.url)
        if heading_key in seen_headings or url_key in seen_urls:
            continue
        seen_headings.add(heading_key)
        seen_urls.add(url_key)
        picked.append(item)
        added += 1
    return added


def build_life_digest(out_dir: Path) -> None:
    started = now_bj()
    items: list[Item] = []
    for feed in LIFE_DIGEST_FEEDS:
        items.extend(parse_feed(feed.source, feed.url, limit=feed.limit))
        time.sleep(0.1)
    candidates = filter_previously_sent("life", sort_recent(items))
    if len(candidates) < 8:
        candidates = filter_previously_sent("life", sort_recent(items), days=1)
    broad_candidates = [item for item in candidates if life_relevant(item)]
    broad_candidates.sort(
        key=lambda item: (life_score(item), parse_date(item.published) or datetime.min.replace(tzinfo=timezone.utc)),
        reverse=True,
    )
    enriched_candidates = [enrich_article_item(item) for item in broad_candidates[:36]]
    enriched_candidates = pick_life_items(enriched_candidates, limit=12)
    picked: list[Item] = []
    seen_headings: set[str] = set()
    for item in enriched_candidates:
        if not life_has_enough_summary_evidence(item):
            continue
        key = norm_title(life_display_title(item))
        if key in seen_headings:
            continue
        seen_headings.add(key)
        picked.append(item)
        if len(picked) >= 8:
            break
    community_added = supplement_life_with_community(picked, seen_headings, limit=8)
    date_s = report_date()
    md = out_dir / f"wealth_slow_travel_digest_{date_s}.md"

    major_changes = [
        item
        for item in picked
        if life_item_type(item) in {"税务居留", "签证入境", "医疗安全"} and life_source_tier(item) in {"官方", "专业机构"}
    ]
    retirement = [item for item in picked if life_item_type(item) == "财务自由"][:1]
    destinations = [item for item in picked if life_item_type(item) in {"目的地", "税务居留", "签证入境", "医疗安全"}][:3]
    points = [item for item in picked if life_item_type(item) in {"积分", "住宿"}][:3]
    lifestyle = [item for item in picked if life_item_type(item) == "生活方式"][:2]
    rendered_urls: set[str] = set()

    lines = [
        f"# 宽裕版财务自由 + 环球慢旅生活日报 - {date_s}",
        "",
        "> 默认读者：生活在香港地区的中国人；美国本土税务、Medicare/ACA/401(k) 等内容默认降权，除非具有跨境或通用规划价值。  ",
        "> 定位：宽裕版财务自由 + 全球生活方式配置 + 环球慢旅实操日报；本报告不是投资、税务、法律或医疗建议。",
        "",
        "## 一句话结论",
        "",
        "今天只保留会影响未来 10 年环球慢旅、财务自由消费、税务居留、签证医疗安全、长住和积分实操的内容；普通旅游新闻、景点促销和美国本土低相关税务医保内容已降权。",
        "",
        "## 今日速览",
        "",
        "| # | 标题（英文｜中文） | 类型 | 来源 | 决策影响 |",
        "|---:|---|---|---|---|",
    ]
    for i, item in enumerate(picked, 1):
        lines.append(f"| {i} | {life_display_title(item)} | {life_item_type(item)} | {item.source} | {life_decision_impact(item)} |")

    lines += ["", "---", "", "## 今日重大变化", ""]
    if not major_changes:
        lines.append("今日未发现足以改变路线、入境、医疗、安全或税务居留判断的高优先级变化。")
    for i, item in enumerate(major_changes[:3], 1):
        append_life_item(lines, item, i)
        rendered_urls.add(canonical_url(item.url))

    lines += ["", "---", "", "## 财务自由与退休收入", ""]
    if not retirement:
        lines.append("今日没有足够高质量的新退休收入内容；不从普通 FIRE 或省钱文里硬写。")
    for i, item in enumerate(retirement, 1):
        append_life_item(lines, item, i)
        rendered_urls.add(canonical_url(item.url))

    lines += ["", "---", "", "## 环球慢旅目的地观察", ""]
    if not destinations:
        lines.append("今日没有新的目的地进入候选池；候选城市仍按生活质量、医疗、安全、签证、税务、长租和飞行可达性评分。")
    for i, item in enumerate(destinations, 1):
        append_life_item(lines, item, i)
        rendered_urls.add(canonical_url(item.url))

    lines += ["", "---", "", "## 长住住宿与积分机会", ""]
    if not points:
        lines.append("今日没有与你未来长途慢旅显著相关的航司、酒店、长住或转点机会。")
    for i, item in enumerate(points, 1):
        append_life_item(lines, item, i)
        rendered_urls.add(canonical_url(item.url))

    if lifestyle:
        lines += ["", "---", "", "## 生活方式案例与反面教材", ""]
        for i, item in enumerate(lifestyle, 1):
            append_life_item(lines, item, i)
            rendered_urls.add(canonical_url(item.url))

    remaining = [item for item in picked if canonical_url(item.url) not in rendered_urls]
    if remaining:
        lines += ["", "---", "", "## 其他高价值线索", ""]
        for i, item in enumerate(remaining, 1):
            append_life_item(lines, item, i)
            rendered_urls.add(canonical_url(item.url))

    lines += [
        "",
        "---",
        "",
        "## 今日可执行事项",
        "",
        "1. 需要进一步查证的国家/城市：" + ("、".join(life_display_title(item) for item in destinations[:3]) if destinations else "无新增。"),
        "2. 需要加入候选清单的目的地：" + ("、".join(life_display_title(item) for item in destinations if life_decision_impact(item) != "低") if destinations else "无新增。"),
        "3. 需要排除的目的地：若官方安全、医疗或税务居留规则不适合 1-3 个月慢旅，先进入排除清单而不是路线表。",
        "4. 需要手动核实的签证/税务/医疗问题：" + ("、".join(life_display_title(item) for item in major_changes[:3]) if major_changes else "今日无新增高优先级核实项。"),
        "5. 对未来路线或资产配置有影响的事项：" + ("、".join(life_display_title(item) for item in retirement + points[:1]) if retirement or points else "今日无新增。"),
        "",
        "---",
        "",
        "## 源分级与筛选审计",
        "",
        f"- 第一版源数量：{len(LIFE_DIGEST_FEEDS)}，限制在 30 个以内；低产出源连续观察后再删减。",
        f"- 内容不足时补充源：Reddit r/FATTravel、r/chubbytravel、r/luxurytravel；本次补充进入正文 {community_added} 条，标题均按“英文原标题｜中文标题”展示，并按论坛经验处理。",
        "- 筛选规则：保留财务自由、税务居留、签证入境、医疗安全、目的地、住宿积分和长期生活方式内容；剔除普通景点、短期促销、网红打卡、极端省钱 FIRE 和低相关美国本土税务/医保内容。",
        f"- 本次进入正文的高价值内容：{len(picked)} 条。",
        "",
    ]
    update_digest_history("life", picked)
    lines += audit_lines("07:00 Asia/Shanghai", started)
    md.write_text("\n".join(lines), encoding="utf-8")
    body = "\n".join(
        [
            "一句话结论：今天只保留影响环球慢旅、财务自由消费、税务居留、签证医疗安全、长住和积分实操的内容。",
            f"条目数量：{len(picked)}",
            f"重大变化数量：{len(major_changes)}",
            "完整排版版见附件。",
            f"调度审计：实际启动 {started.strftime('%Y-%m-%d %H:%M:%S %Z')}；执行环境 GitHub Actions。",
        ]
    )
    write_meta(out_dir, f"宽裕版财务自由 + 环球慢旅生活日报 - {date_s}", body, md)


def audit_lines(planned: str, started: datetime) -> list[str]:
    finished = now_bj()
    return [
        "## 调度审计",
        "",
        f"- 计划时间：{planned}",
        f"- 实际启动时间：{started.strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"- 完成时间：{finished.strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"- 总耗时：{(finished - started).total_seconds():.1f} 秒",
        "- 执行环境：GitHub Actions cloud workflow",
        f"- Run URL：{os.environ.get('GITHUB_RUN_URL', '')}",
    ]


def build_fat_fire(out_dir: Path) -> None:
    build_life_digest(out_dir)


def build_travel(out_dir: Path) -> None:
    build_life_digest(out_dir)
    return
    started = now_bj()
    feeds = {
        "r/chubbytravel": "https://www.reddit.com/r/chubbytravel/hot/.rss",
        "r/FATTravel": "https://www.reddit.com/r/FATTravel/hot/.rss",
        "One Mile at a Time": "https://onemileatatime.com/feed/",
        "Frequent Miler": "https://frequentmiler.com/feed/",
        "LoyaltyLobby": "https://loyaltylobby.com/feed/",
        "View from the Wing": "https://viewfromthewing.com/feed/",
        "Travel Codex": "https://www.travelcodex.com/feed/",
    }
    items: list[Item] = []
    for source, url in feeds.items():
        items.extend(parse_feed(source, url, limit=10))
        time.sleep(0.2)
    candidates = filter_previously_sent("travel", [x for x in sort_recent(items) if travel_relevant(x)])
    if len(candidates) < 12:
        candidates = filter_previously_sent("travel", [x for x in sort_recent(items) if travel_relevant(x)], days=1)
    picked = []
    seen_travel_topics: set[str] = set()
    for item in candidates:
        topic = travel_heading(item.title, item.summary)
        if topic in seen_travel_topics:
            continue
        seen_travel_topics.add(topic)
        picked.append(item)
        if len(picked) >= 12:
            break
    picked = [x if x.source.startswith("r/") else enrich_article_item(x) for x in picked]
    date_s = report_date()
    md = out_dir / f"global_slow_travel_digest_{date_s}.md"
    lines = [
        f"# 环球高质量慢旅行日报 - {date_s}",
        "",
        "## 一句话结论",
        "",
        "今天用酒店、航司、权益、目的地体验和社区真实反馈来沉淀长期环球旅居的选择标准，而不是追求低价促销。",
        "",
        "## 今日速览",
        "",
        "| # | 中文标题 | 来源 | 日期 | 类型 |",
        "|---:|---|---|---:|---|",
    ]
    for i, it in enumerate(picked, 1):
        kind = "社区体验/讨论" if it.source.startswith("r/") else "RSS/旅行资讯"
        dt = (parse_date(it.published) or datetime.now(timezone.utc)).astimezone(BJ).date()
        lines.append(f"| {i} | {travel_heading(it.title, it.summary)} | {it.source} | {dt} | {kind} |")
    lines += ["", "---", "", "## 条目详情", ""]
    for i, it in enumerate(picked, 1):
        fact_label = "帖子事实" if it.source.startswith("r/") else "原文事实"
        lines += [
            f"### {i}. {travel_heading(it.title, it.summary)}",
            f"- 来源：{it.source}",
            f"- {meta_title_label(it)}：{it.title}",
            f"- 链接：{it.url}",
            f"- 类型：{'社区体验/讨论' if it.source.startswith('r/') else 'RSS/旅行资讯'}",
            "",
            f"**{fact_label}**：{travel_chinese_fact(it)}",
            "",
            f"**旅行规划含义**：{travel_implication(it.title, it.summary)}",
            "",
            f"**需要沉淀/核验**：{travel_standard(it.title, it.summary)}",
            "",
            "---",
            "",
        ]
    lines += [
        "## 去重与修正说明",
        "",
        "- 去重窗口：最近 7 天；按 canonical URL 和标题去重，历史记录写入 `digest_history/travel.json`。",
        "- `今日速览` 使用具体中文标题，英文原题只保留在条目元信息里。",
        "",
    ]
    update_digest_history("travel", picked)
    lines += audit_lines("07:10 Asia/Shanghai", started)
    md.write_text("\n".join(lines), encoding="utf-8")
    body = "\n".join(
        [
            "一句话结论：今天围绕酒店、航司、权益和社区真实体验沉淀长期环球旅居标准。",
            f"条目数量：{len(picked)}",
            f"社区体验数量：{sum(1 for x in picked if x.source.startswith('r/'))}",
            "完整排版版见附件。",
            f"调度审计：实际启动 {started.strftime('%Y-%m-%d %H:%M:%S %Z')}；执行环境 GitHub Actions。",
        ]
    )
    write_meta(out_dir, f"环球高质量慢旅行日报 - {date_s}", body, md)


def etf_price_change(symbol: str) -> tuple[str, float] | None:
    encoded = urllib.parse.quote(symbol.upper())
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?range=7d&interval=1d"
    try:
        payload = json.loads(fetch_bytes(url, timeout=20).decode("utf-8"))
    except Exception:
        return None
    try:
        result = payload["chart"]["result"][0]
        timestamps = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]
    except Exception:
        return None
    rows = [(ts, close) for ts, close in zip(timestamps, closes) if close is not None]
    if len(rows) < 2:
        return None
    last_ts, last_close = rows[-1]
    _prev_ts, prev_close = rows[-2]
    change = (float(last_close) / float(prev_close) - 1.0) * 100.0
    date_s = datetime.fromtimestamp(int(last_ts), timezone.utc).astimezone(ZoneInfo("America/New_York")).date().isoformat()
    return date_s, change


def build_etf_legacy(out_dir: Path) -> None:
    started = now_bj()
    tickers = ["SPY", "QQQM", "EMXC", "VEA", "GLDM", "VGLT", "PDBC", "IBIT", "UUP", "DBMF", "KMLM", "XLK", "XLE", "XLF", "XLV", "IWM", "TLT", "HYG", "LQD", "VNQ"]
    rows = []
    for t in tickers:
        val = etf_price_change(t)
        if val:
            rows.append((t, val[0], val[1]))
        time.sleep(0.1)
    rows_sorted = sorted(rows, key=lambda x: x[2], reverse=True)
    feeds = {
        "A Wealth of Common Sense": "https://awealthofcommonsense.com/feed/",
        "ETF Trends": "https://www.etftrends.com/feed/",
        "ETF Database": "https://etfdb.com/feed/",
        "Alpha Architect": "https://alphaarchitect.com/feed/",
        "Meb Faber": "https://mebfaber.com/feed/",
    }
    items: list[Item] = []
    for source, url in feeds.items():
        items.extend(parse_feed(source, url, limit=5))
    picked = dedupe_items([x for x in sort_recent(items) if etf_research_relevant(x)])[:8]
    picked = [enrich_article_item(x) for x in picked]
    date_s = report_date()
    md = out_dir / f"us_etf_allocation_digest_{date_s}.md"
    lines = [
        f"# 美股 ETF 与资产配置简报 - {date_s}",
        "",
        "## 一句话结论",
        "",
        "云端版已更新核心 ETF 池与主要参照资产的上一完整交易日价格涨跌，并附带资产配置/ETF 研究线索。",
        "",
        "## 核心 ETF 表现",
        "",
        "| Ticker | 交易日 | 涨跌幅 |",
        "|---|---:|---:|",
    ]
    for t, d, ch in rows_sorted:
        lines.append(f"| {t} | {d} | {ch:+.2f}% |")
    lines += ["", "## 涨跌观察（核心池口径，非全市场排行榜）", ""]
    lines.append("上涨靠前：" + "；".join(f"{t} {ch:+.2f}%" for t, _, ch in rows_sorted[:10]))
    lines.append("下跌靠前：" + "；".join(f"{t} {ch:+.2f}%" for t, _, ch in rows_sorted[-10:]))
    lines += ["", "## 研究/资讯线索", ""]
    for i, it in enumerate(picked, 1):
        lines += [
            f"### {i}. {etf_display_title(it)}",
            f"- 来源：{it.source}",
            f"- 标题：{etf_display_title(it)}",
            f"- 链接：{it.url}",
            "",
            f"**原文事实**：{etf_chinese_fact(it)}",
            "",
            f"**后续关注**：{etf_follow_up_point(it.title, it.summary)}",
            "",
        ]
    lines += audit_lines("06:45 Asia/Shanghai", started)
    md.write_text("\n".join(lines), encoding="utf-8")
    body = "\n".join(
        [
            "一句话结论：云端版已更新核心 ETF 池与主要资产参照的价格涨跌。",
            f"核心池数量：{len(rows)}",
            f"涨幅靠前：{'; '.join(f'{t} {ch:+.2f}%' for t, _, ch in rows_sorted[:3])}",
            "完整排版版见附件。",
            f"调度审计：实际启动 {started.strftime('%Y-%m-%d %H:%M:%S %Z')}；执行环境 GitHub Actions。",
        ]
    )
    write_meta(out_dir, f"美股 ETF 与资产配置简报 - {date_s}", body, md)


@dataclass(frozen=True)
class MarketAsset:
    code: str
    name: str
    source: str
    symbol: str
    description: str
    category: str = ""


def yahoo_daily_rows(symbol: str, range_s: str = "3mo") -> list[tuple[str, float]]:
    encoded = urllib.parse.quote(symbol.upper(), safe="")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?range={range_s}&interval=1d"
    try:
        payload = json.loads(fetch_bytes(url, timeout=20).decode("utf-8"))
        result = payload["chart"]["result"][0]
        timestamps = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]
    except Exception:
        return []
    rows: list[tuple[str, float]] = []
    for ts, close in zip(timestamps, closes):
        if close is None:
            continue
        date_s = datetime.fromtimestamp(int(ts), timezone.utc).astimezone(ZoneInfo("America/New_York")).date().isoformat()
        rows.append((date_s, float(close)))
    return rows


def secid_to_sina_symbol(secid: str) -> str:
    market, code = secid.split(".", 1)
    prefix = "sh" if market == "1" else "sz"
    return prefix + code


def sina_daily_rows(secid: str, lmt: int = 80) -> list[tuple[str, float]]:
    symbol = secid_to_sina_symbol(secid)
    url = (
        "https://quotes.sina.cn/cn/api/jsonp.php/var%20K=/CN_MarketDataService.getKLineData"
        f"?symbol={urllib.parse.quote(symbol, safe='')}&scale=240&ma=no&datalen={lmt}"
    )
    try:
        text = fetch_bytes(url, timeout=20).decode("utf-8", "ignore")
    except Exception:
        return []
    match = re.search(r"var K=\((\[.*\])\)", text, flags=re.S)
    if not match:
        return []
    try:
        payload = json.loads(match.group(1))
    except Exception:
        return []
    rows: list[tuple[str, float]] = []
    for item in payload:
        try:
            rows.append((str(item["day"]), float(item["close"])))
        except (KeyError, TypeError, ValueError):
            continue
    return rows


def eastmoney_daily_rows(secid: str, lmt: int = 80) -> list[tuple[str, float]]:
    sina_rows = sina_daily_rows(secid, lmt)
    if sina_rows:
        return sina_rows
    end_date = (now_bj() + timedelta(days=30)).strftime("%Y%m%d")
    url = (
        "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        f"?secid={urllib.parse.quote(secid, safe='.')}"
        "&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57"
        f"&klt=101&fqt=1&beg=20050101&end={end_date}&lmt={lmt}"
    )
    try:
        payload = json.loads(fetch_bytes(url, timeout=20).decode("utf-8"))
        klines = (payload.get("data") or {}).get("klines") or []
    except Exception:
        return []
    rows: list[tuple[str, float]] = []
    for line in klines:
        parts = str(line).split(",")
        if len(parts) < 3:
            continue
        try:
            rows.append((parts[0], float(parts[2])))
        except ValueError:
            continue
    return rows


def csindex_daily_rows(index_code: str, lmt: int = 80) -> list[tuple[str, float]]:
    end_dt = now_bj().date() + timedelta(days=3)
    start_dt = end_dt - timedelta(days=max(lmt * 3, 30))
    url = (
        "https://www.csindex.com.cn/csindex-home/perf/index-perf"
        f"?indexCode={urllib.parse.quote(index_code, safe='')}"
        f"&startDate={start_dt:%Y%m%d}&endDate={end_dt:%Y%m%d}"
    )
    headers = {
        "Host": "www.csindex.com.cn",
        "Referer": "https://www.csindex.com.cn/",
        "User-Agent": UA,
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
    }
    try:
        payload = json.loads(fetch_bytes(url, timeout=20, headers=headers).decode("utf-8"))
        data = payload.get("data") or []
    except Exception:
        return []
    rows: list[tuple[str, float]] = []
    for item in data:
        try:
            raw_date = str(item["tradeDate"])
            close = float(item["close"])
            date_s = f"{raw_date[0:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
        except (KeyError, TypeError, ValueError):
            continue
        rows.append((date_s, close))
    return rows[-lmt:]


def change_from_rows(rows: list[tuple[str, float]], sessions: int = 1) -> tuple[str, float] | None:
    if len(rows) <= sessions:
        return None
    last_date, last_close = rows[-1]
    _prev_date, prev_close = rows[-1 - sessions]
    if prev_close == 0:
        return None
    return last_date, (last_close / prev_close - 1.0) * 100.0


def asset_change(asset: MarketAsset, sessions: int = 1) -> tuple[str, float] | None:
    if asset.source == "yahoo":
        rows = yahoo_daily_rows(asset.symbol, "6mo" if sessions > 5 else "1mo")
    elif asset.source == "eastmoney":
        rows = eastmoney_daily_rows(asset.symbol, max(80, sessions + 10))
    elif asset.source == "csindex":
        rows = csindex_daily_rows(asset.symbol, max(80, sessions + 10))
    else:
        return None
    return change_from_rows(rows, sessions=sessions)


def fetch_asset_changes(assets: list[MarketAsset], sessions: int = 1) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for asset in assets:
        val = asset_change(asset, sessions=sessions)
        if val:
            rows.append({"asset": asset, "date": val[0], "change": val[1]})
        time.sleep(0.05)
    return rows


def fmt_change(value: object) -> str:
    return f"{float(value):+.2f}%"


def append_asset_table(lines: list[str], rows: list[dict[str, object]], include_strategy: bool = False) -> None:
    if include_strategy:
        lines += [
            "| 策略 | 代码 | 名称 | 交易日 | 涨跌幅 | 一句话说明 |",
            "|---|---|---|---:|---:|---|",
        ]
        for row in rows:
            asset = row["asset"]
            assert isinstance(asset, MarketAsset)
            lines.append(
                f"| {asset.category} | {asset.code} | {asset.name} | {row['date']} | {fmt_change(row['change'])} | {asset.description} |"
            )
    else:
        lines += ["| 代码 | 名称 | 交易日 | 涨跌幅 | 一句话说明 |", "|---|---|---:|---:|---|"]
        for row in rows:
            asset = row["asset"]
            assert isinstance(asset, MarketAsset)
            lines.append(
                f"| {asset.code} | {asset.name} | {row['date']} | {fmt_change(row['change'])} | {asset.description} |"
            )


def dedupe_by_category(rows: list[dict[str, object]], reverse: bool) -> list[dict[str, object]]:
    picked: list[dict[str, object]] = []
    seen: set[str] = set()
    for row in sorted(rows, key=lambda x: float(x["change"]), reverse=reverse):
        asset = row["asset"]
        assert isinstance(asset, MarketAsset)
        key = asset.category or asset.code
        if key in seen:
            continue
        seen.add(key)
        picked.append(row)
        if len(picked) >= 10:
            break
    return picked


def broad_mover_rows(records: list[dict[str, object]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for record in records:
        rows.append(
            {
                "asset": MarketAsset(
                    str(record["symbol"]),
                    str(record["name"]),
                    "yahoo",
                    str(record["symbol"]),
                    str(record["description"]),
                    str(record["category"]),
                ),
                "date": str(record["date"]),
                "change": float(record["change"]),
                "average_daily_volume": int(record["average_daily_volume"]),
                "average_daily_dollar_volume": float(record["average_daily_dollar_volume"]),
            }
        )
    return rows


A_STRATEGY_ASSETS = [
    MarketAsset("H20955", "中证红利低波100全收益", "csindex", "H20955", "A策略使用的红利低波权益全收益指数；日涨跌取中证指数官网 H20955。", "A策略"),
    MarketAsset("399606", "创业板指数", "eastmoney", "0.399606", "A策略权益池里的创业板宽基指数。", "A策略"),
    MarketAsset("H00016", "上证50全收益", "eastmoney", "1.000016", "A策略使用的大盘蓝筹指数；日涨跌用上证50价格指数代理。", "A策略"),
    MarketAsset("H00852", "中证1000全收益", "eastmoney", "1.000852", "A策略使用的小盘成长宽基指数；日涨跌用中证1000价格指数代理。", "A策略"),
    MarketAsset("H00905", "中证500全收益", "eastmoney", "1.000905", "A策略使用的中盘宽基指数；日涨跌用中证500价格指数代理。", "A策略"),
    MarketAsset("H11077", "10年期国债全收益", "eastmoney", "1.000012", "A策略债券防守资产；日涨跌用上证国债指数代理。", "A策略"),
]

ADK_STRATEGY_ASSETS = [
    MarketAsset("000852", "中证1000", "eastmoney", "1.000852", "ADK策略多配对池里的小盘宽基指数。", "ADK策略"),
    MarketAsset("000016", "上证50", "eastmoney", "1.000016", "ADK策略多配对池里的大盘蓝筹指数。", "ADK策略"),
    MarketAsset("000300", "沪深300", "eastmoney", "1.000300", "ADK策略多配对池里的A股核心宽基指数。", "ADK策略"),
    MarketAsset("000905", "中证500", "eastmoney", "1.000905", "ADK策略多配对池里的中盘宽基指数。", "ADK策略"),
    MarketAsset("399006", "创业板指", "eastmoney", "0.399006", "ADK策略多配对池里的创业板价格指数。", "ADK策略"),
]

B_STRATEGY_ASSETS = [
    MarketAsset("QQQM", "Invesco NASDAQ 100 ETF", "yahoo", "QQQM", "B策略美股成长敞口，跟踪纳斯达克100指数。", "B策略"),
    MarketAsset("EMXC", "iShares MSCI Emerging Markets ex China ETF", "yahoo", "EMXC", "B策略新兴市场但排除中国的股票敞口。", "B策略"),
    MarketAsset("VEA", "Vanguard FTSE Developed Markets ETF", "yahoo", "VEA", "B策略美国以外发达市场股票敞口。", "B策略"),
    MarketAsset("GLDM", "SPDR Gold MiniShares Trust", "yahoo", "GLDM", "B策略黄金敞口，代表实物黄金价格变化。", "B策略"),
    MarketAsset("VGLT", "Vanguard Long-Term Treasury ETF", "yahoo", "VGLT", "B策略美国长期国债久期敞口。", "B策略"),
    MarketAsset("PDBC", "Invesco Optimum Yield Diversified Commodity Strategy No K-1 ETF", "yahoo", "PDBC", "B策略多商品期货篮子敞口。", "B策略"),
    MarketAsset("IBIT", "iShares Bitcoin Trust ETF", "yahoo", "IBIT", "B策略现货比特币敞口。", "B策略"),
    MarketAsset("UUP", "Invesco DB US Dollar Index Bullish Fund", "yahoo", "UUP", "B策略美元指数多头敞口。", "B策略"),
    MarketAsset("DBMF", "iMGP DBi Managed Futures Strategy ETF", "yahoo", "DBMF", "B策略管理期货/趋势跟踪敞口。", "B策略"),
    MarketAsset("KMLM", "KFA Mount Lucas Managed Futures Index Strategy ETF", "yahoo", "KMLM", "B策略系统化管理期货趋势敞口。", "B策略"),
]

D_STRATEGY_ASSETS = [
    MarketAsset("159915.SZ", "创业板100 ETF", "eastmoney", "0.159915", "D策略六ETF池里的创业板100场内ETF。", "D策略"),
    MarketAsset("159941.SZ", "纳指ETF", "eastmoney", "0.159941", "D策略六ETF池里的境内纳斯达克100 ETF。", "D策略"),
    MarketAsset("513030.SH", "德国ETF", "eastmoney", "1.513030", "D策略六ETF池里的德国股票市场ETF。", "D策略"),
    MarketAsset("513520.SH", "日经ETF", "eastmoney", "1.513520", "D策略六ETF池里的日本日经指数ETF。", "D策略"),
    MarketAsset("159985.SZ", "豆粕ETF", "eastmoney", "0.159985", "D策略六ETF池里的豆粕商品ETF。", "D策略"),
    MarketAsset("518880.SH", "黄金ETF", "eastmoney", "1.518880", "D策略六ETF池里的境内黄金ETF。", "D策略"),
]

CORE_MARKET_ASSETS = [
    MarketAsset("^GSPC", "S&P 500", "yahoo", "^GSPC", "美国大盘股核心指数。"),
    MarketAsset("^NDX", "NASDAQ 100", "yahoo", "^NDX", "美国大型成长股和科技股权重较高的核心指数。"),
    MarketAsset("^DJI", "Dow Jones Industrial Average", "yahoo", "^DJI", "美国蓝筹股价格加权指数。"),
    MarketAsset("^RUT", "Russell 2000", "yahoo", "^RUT", "美国小盘股核心指数。"),
    MarketAsset("^VIX", "CBOE VIX", "yahoo", "^VIX", "美股隐含波动率指数，反映期权市场风险定价。"),
    MarketAsset("000300", "沪深300", "eastmoney", "1.000300", "A股核心大盘宽基指数。"),
    MarketAsset("000905", "中证500", "eastmoney", "1.000905", "A股中盘宽基指数。"),
    MarketAsset("000852", "中证1000", "eastmoney", "1.000852", "A股小盘宽基指数。"),
    MarketAsset("399006", "创业板指", "eastmoney", "0.399006", "A股成长风格核心指数。"),
    MarketAsset("000016", "上证50", "eastmoney", "1.000016", "A股大盘蓝筹核心指数。"),
]

MOVER_UNIVERSE = [
    MarketAsset("^GSPC", "S&P 500 指数", "yahoo", "^GSPC", "美国大盘股核心指数。", "US Large Cap Index"),
    MarketAsset("^NDX", "NASDAQ 100 指数", "yahoo", "^NDX", "美国大型成长股和科技股权重较高的指数。", "US Growth Index"),
    MarketAsset("^RUT", "Russell 2000 指数", "yahoo", "^RUT", "美国小盘股核心指数。", "US Small Cap Index"),
    MarketAsset("RSP", "Invesco S&P 500 Equal Weight ETF", "yahoo", "RSP", "等权重持有标普500成分股的美国大盘ETF。", "US Equal Weight"),
    MarketAsset("VEA", "Vanguard FTSE Developed Markets ETF", "yahoo", "VEA", "覆盖美国以外发达市场股票。", "Developed ex US"),
    MarketAsset("VWO", "Vanguard FTSE Emerging Markets ETF", "yahoo", "VWO", "覆盖全球新兴市场股票。", "Emerging Markets"),
    MarketAsset("EMXC", "iShares MSCI Emerging Markets ex China ETF", "yahoo", "EMXC", "覆盖中国以外新兴市场股票。", "Emerging ex China"),
    MarketAsset("EWJ", "iShares MSCI Japan ETF", "yahoo", "EWJ", "日本股票市场ETF。", "Japan Equity"),
    MarketAsset("EWG", "iShares MSCI Germany ETF", "yahoo", "EWG", "德国股票市场ETF。", "Germany Equity"),
    MarketAsset("EWU", "iShares MSCI United Kingdom ETF", "yahoo", "EWU", "英国股票市场ETF。", "UK Equity"),
    MarketAsset("EWZ", "iShares MSCI Brazil ETF", "yahoo", "EWZ", "巴西股票市场ETF。", "Brazil Equity"),
    MarketAsset("INDA", "iShares MSCI India ETF", "yahoo", "INDA", "印度股票市场ETF。", "India Equity"),
    MarketAsset("EWT", "iShares MSCI Taiwan ETF", "yahoo", "EWT", "台湾股票市场ETF。", "Taiwan Equity"),
    MarketAsset("EWY", "iShares MSCI South Korea ETF", "yahoo", "EWY", "韩国股票市场ETF。", "Korea Equity"),
    MarketAsset("FXI", "iShares China Large-Cap ETF", "yahoo", "FXI", "香港上市中国大盘股ETF。", "China Equity"),
    MarketAsset("ASHR", "Xtrackers Harvest CSI 300 China A-Shares ETF", "yahoo", "ASHR", "跟踪沪深300A股的美国上市ETF。", "China Equity"),
    MarketAsset("XLK", "Technology Select Sector SPDR Fund", "yahoo", "XLK", "美国科技行业ETF。", "US Technology"),
    MarketAsset("XLY", "Consumer Discretionary Select Sector SPDR Fund", "yahoo", "XLY", "美国可选消费行业ETF。", "US Discretionary"),
    MarketAsset("XLP", "Consumer Staples Select Sector SPDR Fund", "yahoo", "XLP", "美国必需消费行业ETF。", "US Staples"),
    MarketAsset("XLE", "Energy Select Sector SPDR Fund", "yahoo", "XLE", "美国能源行业ETF。", "US Energy"),
    MarketAsset("XLF", "Financial Select Sector SPDR Fund", "yahoo", "XLF", "美国金融行业ETF。", "US Financials"),
    MarketAsset("XLV", "Health Care Select Sector SPDR Fund", "yahoo", "XLV", "美国医疗保健行业ETF。", "US Health Care"),
    MarketAsset("XLI", "Industrial Select Sector SPDR Fund", "yahoo", "XLI", "美国工业行业ETF。", "US Industrials"),
    MarketAsset("XLB", "Materials Select Sector SPDR Fund", "yahoo", "XLB", "美国材料行业ETF。", "US Materials"),
    MarketAsset("XLRE", "Real Estate Select Sector SPDR Fund", "yahoo", "XLRE", "美国上市房地产行业ETF。", "US Real Estate"),
    MarketAsset("XLU", "Utilities Select Sector SPDR Fund", "yahoo", "XLU", "美国公用事业行业ETF。", "US Utilities"),
    MarketAsset("XLC", "Communication Services Select Sector SPDR Fund", "yahoo", "XLC", "美国通信服务行业ETF。", "US Communication"),
    MarketAsset("SMH", "VanEck Semiconductor ETF", "yahoo", "SMH", "美国上市半导体产业链ETF。", "Semiconductors"),
    MarketAsset("IGV", "iShares Expanded Tech-Software Sector ETF", "yahoo", "IGV", "美国软件行业ETF。", "Software"),
    MarketAsset("BUG", "Global X Cybersecurity ETF", "yahoo", "BUG", "网络安全主题ETF，持有安全软件、身份管理和云安全相关公司。", "Cybersecurity"),
    MarketAsset("XBI", "SPDR S&P Biotech ETF", "yahoo", "XBI", "等权重美国生物科技行业ETF。", "Biotech"),
    MarketAsset("KRE", "SPDR S&P Regional Banking ETF", "yahoo", "KRE", "美国区域银行行业ETF。", "Regional Banks"),
    MarketAsset("XRT", "SPDR S&P Retail ETF", "yahoo", "XRT", "美国零售行业ETF。", "Retail"),
    MarketAsset("XHB", "SPDR S&P Homebuilders ETF", "yahoo", "XHB", "美国住宅建筑与相关产业ETF。", "Homebuilders"),
    MarketAsset("JETS", "U.S. Global Jets ETF", "yahoo", "JETS", "航空公司和航空服务相关股票ETF。", "Airlines"),
    MarketAsset("IYT", "iShares U.S. Transportation ETF", "yahoo", "IYT", "美国运输行业ETF。", "Transportation"),
    MarketAsset("URA", "Global X Uranium ETF", "yahoo", "URA", "铀矿和核燃料产业链ETF。", "Uranium Equity"),
    MarketAsset("TAN", "Invesco Solar ETF", "yahoo", "TAN", "全球太阳能产业链ETF。", "Clean Energy"),
    MarketAsset("ICLN", "iShares Global Clean Energy ETF", "yahoo", "ICLN", "全球清洁能源股票ETF。", "Clean Energy"),
    MarketAsset("MTUM", "iShares MSCI USA Momentum Factor ETF", "yahoo", "MTUM", "美国动量因子ETF。", "Momentum Factor"),
    MarketAsset("VLUE", "iShares MSCI USA Value Factor ETF", "yahoo", "VLUE", "美国价值因子ETF。", "Value Factor"),
    MarketAsset("QUAL", "iShares MSCI USA Quality Factor ETF", "yahoo", "QUAL", "美国质量因子ETF。", "Quality Factor"),
    MarketAsset("USMV", "iShares MSCI USA Min Vol Factor ETF", "yahoo", "USMV", "美国低波动因子ETF。", "Low Vol Factor"),
    MarketAsset("SHY", "iShares 1-3 Year Treasury Bond ETF", "yahoo", "SHY", "美国短期国债ETF。", "Short Treasury"),
    MarketAsset("IEF", "iShares 7-10 Year Treasury Bond ETF", "yahoo", "IEF", "美国中长期国债ETF。", "Intermediate Treasury"),
    MarketAsset("VGLT", "Vanguard Long-Term Treasury ETF", "yahoo", "VGLT", "美国长期国债ETF。", "Long Treasury"),
    MarketAsset("TIP", "iShares TIPS Bond ETF", "yahoo", "TIP", "美国通胀保值债券ETF。", "TIPS"),
    MarketAsset("LQD", "iShares iBoxx Investment Grade Corporate Bond ETF", "yahoo", "LQD", "美元投资级公司债ETF。", "Investment Grade Credit"),
    MarketAsset("HYG", "iShares iBoxx High Yield Corporate Bond ETF", "yahoo", "HYG", "美元高收益公司债ETF。", "High Yield Credit"),
    MarketAsset("EMB", "iShares J.P. Morgan USD Emerging Markets Bond ETF", "yahoo", "EMB", "美元计价新兴市场主权债ETF。", "EM Bond"),
    MarketAsset("MUB", "iShares National Muni Bond ETF", "yahoo", "MUB", "美国市政债ETF。", "Municipal Bond"),
    MarketAsset("PDBC", "Invesco Diversified Commodity Strategy ETF", "yahoo", "PDBC", "多商品期货策略ETF。", "Broad Commodities"),
    MarketAsset("VNQ", "Vanguard Real Estate ETF", "yahoo", "VNQ", "美国REITs和房地产股票ETF。", "REITs"),
    MarketAsset("UUP", "Invesco DB US Dollar Index Bullish Fund", "yahoo", "UUP", "美元指数多头ETF。", "US Dollar"),
    MarketAsset("DBMF", "iMGP DBi Managed Futures Strategy ETF", "yahoo", "DBMF", "管理期货趋势跟踪ETF。", "Managed Futures"),
    MarketAsset("KMLM", "KFA Mount Lucas Managed Futures Index Strategy ETF", "yahoo", "KMLM", "系统化管理期货趋势ETF。", "Managed Futures"),
]


def build_etf(out_dir: Path) -> None:
    started = now_bj()
    strategy_assets = A_STRATEGY_ASSETS + ADK_STRATEGY_ASSETS + B_STRATEGY_ASSETS + D_STRATEGY_ASSETS
    strategy_rows = fetch_asset_changes(strategy_assets)
    broad_universe = broad_etf_movers.fetch_universe()
    daily_movers = broad_etf_movers.daily_rankings(broad_universe)
    top_rows = broad_mover_rows(daily_movers.gainers)
    bottom_rows = broad_mover_rows(daily_movers.losers)
    mover_rows = top_rows + bottom_rows

    items: list[Item] = []
    for feed in ETF_RESEARCH_FEEDS:
        items.extend(parse_feed(feed.source, feed.url, limit=feed.limit))
        time.sleep(0.1)
    scored_picked = select_etf_research_items(items, limit=9)
    picked = [x.item for x in scored_picked]
    fixed_monitor_updates = collect_etf_fixed_monitor_updates(exclude_urls={canonical_url(item.url) for item in picked})

    forum_items = collect_etf_forum_items()
    today = report_date()
    fresh_forum_items = filter_previously_sent("etf", forum_items, days=ETF_DEDUPE_DAYS, ignore_dates={today})
    forum_picked = select_etf_forum_items(fresh_forum_items, limit=ETF_FORUM_DISPLAY_LIMIT)
    forum_picked = ensure_non_reddit_forum_mix(forum_picked, fresh_forum_items, min_non_reddit=2, limit=ETF_FORUM_DISPLAY_LIMIT)
    forum_picked = supplement_forum_items_with_same_day_new_history(
        forum_picked,
        minimum=ETF_MIN_VISIBLE_FORUM_ITEMS,
        limit=ETF_FORUM_DISPLAY_LIMIT,
    )

    date_s = today
    data_dates = sorted({str(r["date"]) for r in strategy_rows + mover_rows})
    data_date_s = data_dates[-1] if data_dates else "数据不足"
    md = out_dir / f"us_etf_allocation_digest_{date_s}.md"

    lines = [
        f"# 美股 ETF 与资产配置日报 - {date_s}",
        "",
        f"> 数据日期：最新可取得的收盘数据截至 {data_date_s}；涨跌幅为收盘价相对上一交易日的价格涨跌，不含分红再投资。",
        "",
        "## 目录",
        "- [策略相关 ETF / 指数涨跌](#策略相关-etf--指数涨跌)",
        "- [ETF 涨跌幅榜](#etf-涨跌幅榜)",
        "- [市场 regime 是否变化](#市场-regime-是否变化)",
        "- [资产配置影响](#资产配置影响)",
        "- [量化策略影响](#量化策略影响)",
        "- [A 股 / 港股专项](#a-股--港股专项)",
        "- [固定关注博客/播客更新](#固定关注博客播客更新)",
        "- [待验证假设](#待验证假设)",
        "",
        "---",
        "",
        "## 一句话结论",
        "",
        "今天的文章部分按 source ranking 和 relevance scoring 过滤，只把内容写成事实层、配置/策略映射和可测试假设；论坛内容只做 idea mining，不进入结论。",
        "",
        "## 策略相关 ETF / 指数涨跌",
        "",
        f"交易日：{data_date_s}",
        "",
    ]
    append_strategy_price_table(lines, strategy_rows)

    lines += [
        "",
        "---",
        "",
        "## ETF 涨跌幅榜",
        "",
        "排行范围：美国上市 ETF 全市场，不使用精选 ETF 池。流动性门槛为近 3 个月平均成交额至少 500 万美元/日且平均成交量至少 5 万份/日。",
        "",
        "过滤口径：已排除杠杆、反向/做空、单股日内目标、期权收益增强/定义结果、ETN、单一加密资产和实物/现货信托；正常的商品、外汇、波动率和管理期货 ETF 可以纳入。每个榜单按共同经济驱动强去重；同一经济敞口优先保留近 3 个月平均成交额更高的 ETF，成交额缺失或相同时再比较基金资产规模，涨跌幅不参与同类代表选择。涨幅榜只展示正收益，跌幅榜只展示负收益。",
        "",
        "### 涨幅前 10",
        "",
    ]
    append_mover_table(lines, top_rows)
    if len(top_rows) < 10:
        lines += ["", f"> 当日符合过滤口径且正收益的候选不足 10 个，仅展示 {len(top_rows)} 个。", ""]
    lines += ["", "### 跌幅前 10", ""]
    append_mover_table(lines, bottom_rows)

    period_movers: dict[str, object] | None = None
    if now_bj().weekday() == 5:
        period_movers = broad_etf_movers.period_rankings(broad_universe)
        for label, key in [("最近一周", "one_week"), ("最近一个月", "one_month")]:
            period_block = period_movers[key]
            assert isinstance(period_block, dict)
            lines += ["", f"### {label}涨幅前 10", ""]
            append_mover_table(lines, broad_mover_rows(period_block["gainers"]))
            lines += ["", f"### {label}跌幅前 10", ""]
            append_mover_table(lines, broad_mover_rows(period_block["losers"]))

    forum_rendered_count = append_etf_research_sections(lines, scored_picked, forum_picked, strategy_rows, mover_rows, data_date_s)
    fixed_monitor_rendered_count = append_etf_fixed_monitor_section(lines, fixed_monitor_updates)
    update_digest_history("etf", [*picked, *fixed_monitor_updates, *forum_picked], days=ETF_HISTORY_DAYS)
    lines += [
        "## 去重与补充审计",
        "",
        f"- 去重窗口：最近 {ETF_DEDUPE_DAYS} 天；按 canonical URL 和标题去重，历史记录写入 `digest_history/etf.json`。",
        f"- RSS/研究文章新鲜度：优先使用最近 {ETF_ARTICLE_MAX_AGE_HOURS} 小时内发布的条目；若高证据文章不足 {ETF_MIN_RESEARCH_ITEMS} 篇，按严格证据门槛回填近 {ETF_ARTICLE_BACKFILL_MAX_AGE_HOURS // 24} 天文章。",
        f"- RSS/研究文章数量：{len(scored_picked)}",
        f"- 固定关注博客/播客更新数量：{fixed_monitor_rendered_count}",
        f"- 论坛/社区 idea mining 数量：{forum_rendered_count}",
        f"- ETF 排行宇宙：扫描 {daily_movers.universe_count} 只美国上市 ETF，流动性及产品结构过滤后合格 {daily_movers.eligible_count} 只；每个涨跌榜按共同经济驱动强去重。",
        "",
    ]
    lines.append(f"- 回填去重：文章回填排除最近 {ETF_BACKFILL_DEDUPE_DAYS} 天已推送内容；论坛回填排除最近 {ETF_FORUM_BACKFILL_DEDUPE_DAYS} 天已推送内容，并只统计真正进入正文的帖子。")
    lines += audit_lines("08:00 Asia/Shanghai", started)
    report_text = "\n".join(lines)
    md.write_text(report_text, encoding="utf-8")

    top_preview = "; ".join(
        f"{row['asset'].code} {fmt_change(row['change'])}" for row in top_rows[:3] if isinstance(row["asset"], MarketAsset)
    )
    bottom_preview = "; ".join(
        f"{row['asset'].code} {fmt_change(row['change'])}" for row in bottom_rows[:3] if isinstance(row["asset"], MarketAsset)
    )
    preheader = (
        f"数据日期 {data_date_s}；涨幅靠前 {top_preview or '无'}；"
        f"跌幅靠前 {bottom_preview or '无'}；完整报告直接在邮件正文查看。"
    )
    html_body = markdown_to_email_html(report_text, preheader)
    write_meta(
        out_dir,
        f"美股 ETF 与资产配置日报 - {date_s}",
        report_text,
        None,
        html_body=html_body,
    )


def fetch_json(url: str) -> object:
    return json.loads(fetch_bytes(url).decode("utf-8"))


def fetch_json_with_retries(
    url: str,
    attempts: int = 4,
    timeout: int = 30,
    delays: tuple[float, ...] = (5.0, 15.0, 30.0),
) -> object:
    last_error: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return json.loads(fetch_bytes(url, timeout=timeout).decode("utf-8"))
        except (TimeoutError, OSError, urllib.error.URLError) as exc:
            last_error = exc
            if attempt == attempts:
                break
            delay = delays[min(attempt - 1, len(delays) - 1)] if delays else 0
            if delay > 0:
                time.sleep(delay)
    raise RuntimeError(f"Failed to fetch JSON after {attempts} attempts: {url}") from last_error


def build_ai(out_dir: Path) -> None:
    started = now_bj()
    base = "https://aihot.virxact.com"
    daily = fetch_json_with_retries(f"{base}/api/public/daily")
    date_s = str(daily.get("date") or report_date())
    sections = daily.get("sections") or []
    lead = daily.get("lead")
    if isinstance(lead, dict):
        conclusion = clean_text(lead.get("leadParagraph") or lead.get("title"))
    else:
        conclusion = clean_text(lead)
    conclusion = conclusion or "今日 AI 热点以模型、产品和行业动态为主。"
    flat = [it for sec in sections for it in (sec.get("items") or [])]
    counts = " · ".join(f"{sec.get('label') or '其他'} {len(sec.get('items') or [])} 条" for sec in sections)
    why_it_matters = "可能影响模型选择、产品工作流、成本结构或行业竞争格局，建议结合实际使用场景判断是否跟进。"

    plain_lines = [
        f"AI HOT 日报 - {date_s}",
        "",
        "一句话结论",
        conclusion,
        "",
        "今日速览",
        f"共 {len(flat)} 条：{counts}",
        "",
    ]
    html_sections: list[str] = []
    global_index = 0
    for sec in sections:
        label = sec.get("label") or "其他"
        plain_lines += [label, ""]
        html_cards: list[str] = []
        for it in sec.get("items") or []:
            global_index += 1
            title = clean_text(it.get("title") or "")
            summary = clean_text(it.get("summary") or "")
            url = it.get("sourceUrl") or it.get("url") or ""
            source = it.get("sourceName") or it.get("source") or ""
            summary = summary or "AI HOT 未提供摘要。"
            source = source or "未标注"
            plain_lines += [
                f"{global_index}. {title}",
                f"来源：{source}",
                f"原文链接：{url or '未提供'}",
                f"要点摘要：{summary}",
                f"为什么重要：{why_it_matters}",
                "",
            ]
            safe_url = html.escape(str(url), quote=True)
            link_html = (
                f'<a href="{safe_url}" style="color:#4f46e5;font-size:14px;font-weight:700;text-decoration:none;">阅读原文 →</a>'
                if url
                else '<span style="color:#98a2b3;font-size:14px;">原文链接未提供</span>'
            )
            html_cards.append(
                '<div style="margin:0 0 16px;padding:18px 20px;background:#ffffff;border:1px solid #e6eaf0;border-radius:12px;">'
                f'<div style="font-size:18px;line-height:1.5;font-weight:700;color:#172033;margin-bottom:8px;">'
                f'<span style="display:inline-block;min-width:30px;color:#5b5bd6;">{global_index}.</span>{html.escape(title)}</div>'
                f'<div style="font-size:13px;line-height:1.6;color:#667085;margin-bottom:10px;">来源：{html.escape(source)}</div>'
                f'<div style="font-size:15px;line-height:1.8;color:#344054;margin-bottom:9px;"><strong style="color:#172033;">要点摘要：</strong>{html.escape(summary)}</div>'
                f'<div style="font-size:15px;line-height:1.8;color:#344054;margin-bottom:12px;"><strong style="color:#172033;">为什么重要：</strong>{html.escape(why_it_matters)}</div>'
                f'{link_html}</div>'
            )
        html_sections.append(
            '<section style="margin-top:30px;">'
            f'<h2 style="margin:0 0 14px;padding-bottom:9px;border-bottom:2px solid #dfe3ff;font-size:22px;line-height:1.4;color:#27306b;">{html.escape(label)}</h2>'
            f'{"".join(html_cards)}</section>'
        )

    flashes = daily.get("flashes") or []
    if flashes:
        plain_lines += ["快讯", ""]
        flash_rows: list[str] = []
        for flash in flashes:
            title = clean_text(flash.get("title") or "")
            source = clean_text(flash.get("sourceName") or flash.get("source") or "未标注")
            url = flash.get("sourceUrl") or flash.get("url") or ""
            plain_lines += [f"- {title} — {source}", f"  {url or '原文链接未提供'}"]
            link = (
                f'<a href="{html.escape(str(url), quote=True)}" style="color:#4f46e5;text-decoration:none;">{html.escape(title)}</a>'
                if url
                else html.escape(title)
            )
            flash_rows.append(f'<li style="margin:0 0 8px;line-height:1.7;">{link} — {html.escape(source)}</li>')
        html_sections.append(
            '<section style="margin-top:30px;">'
            '<h2 style="margin:0 0 14px;padding-bottom:9px;border-bottom:2px solid #dfe3ff;font-size:22px;color:#27306b;">快讯</h2>'
            f'<ul style="margin:0;padding-left:20px;color:#344054;">{"".join(flash_rows)}</ul></section>'
        )

    audit_time = started.strftime("%Y-%m-%d %H:%M:%S %Z")
    plain_lines += [
        "",
        "运行审计",
        "计划时间：每天 08:15（北京时间）",
        f"实际启动：{audit_time}",
        "执行环境：GitHub Actions",
        f"数据日期：{date_s}",
    ]
    body = "\n".join(plain_lines)
    html_body = f'''<!doctype html>
<html lang="zh-CN"><body style="margin:0;padding:0;background:#f3f5f9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',Arial,sans-serif;color:#172033;">
<div style="display:none;max-height:0;overflow:hidden;">今日 {len(flat)} 条 AI 动态，完整内容直接在邮件正文查看。</div>
<div style="max-width:760px;margin:0 auto;padding:24px 14px 40px;">
  <header style="background:#35378f;padding:30px 26px;border-radius:16px;color:#ffffff;">
    <div style="font-size:13px;letter-spacing:1.5px;opacity:.85;">AI HOT · 每日中文简报</div>
    <h1 style="margin:8px 0 6px;font-size:30px;line-height:1.3;">AI HOT 日报</h1>
    <div style="font-size:15px;opacity:.9;">{html.escape(date_s)} · 共 {len(flat)} 条</div>
  </header>
  <div style="margin-top:16px;padding:20px 22px;background:#fff8e8;border:1px solid #f3d38b;border-radius:12px;">
    <div style="font-size:13px;font-weight:700;color:#8a5a00;margin-bottom:6px;">一句话结论</div>
    <div style="font-size:16px;line-height:1.8;color:#563b00;">{html.escape(conclusion)}</div>
  </div>
  <div style="margin-top:16px;padding:18px 22px;background:#ffffff;border:1px solid #e6eaf0;border-radius:12px;">
    <div style="font-size:17px;font-weight:700;color:#172033;margin-bottom:8px;">今日速览</div>
    <div style="font-size:14px;line-height:1.8;color:#475467;">{html.escape(counts)}</div>
  </div>
  {''.join(html_sections)}
  <footer style="margin-top:28px;padding:18px 22px;background:#eef1f6;border-radius:12px;color:#667085;font-size:12px;line-height:1.8;">
    <strong style="color:#344054;">运行审计</strong><br>
    计划时间：每天 08:15（北京时间）<br>
    实际启动：{html.escape(audit_time)}<br>
    执行环境：GitHub Actions<br>
    数据日期：{html.escape(date_s)}
  </footer>
</div></body></html>'''
    write_meta(out_dir, f"AI HOT 日报 - {date_s}", body, None, html_body=html_body)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", choices=["life", "fat-fire", "travel", "etf", "ai-hot"])
    parser.add_argument("--out-dir", default="artifacts")
    args = parser.parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.report == "life":
        build_life_digest(out_dir)
    elif args.report == "fat-fire":
        build_fat_fire(out_dir)
    elif args.report == "travel":
        build_travel(out_dir)
    elif args.report == "etf":
        build_etf(out_dir)
    elif args.report == "ai-hot":
        build_ai(out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
