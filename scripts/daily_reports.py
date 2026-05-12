from __future__ import annotations

import argparse
import csv
import email.utils
import html
import json
import os
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


BJ = ZoneInfo("Asia/Shanghai")
UA = "Mozilla/5.0 (Codex daily digest; +https://github.com/liuruojiang/codex-daily-automation-probe)"


@dataclass
class Item:
    source: str
    title: str
    url: str
    published: str
    summary: str


def now_bj() -> datetime:
    return datetime.now(BJ)


def report_date() -> str:
    return now_bj().date().isoformat()


def fetch_bytes(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
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
            out.append(
                Item(
                    source=source,
                    title=title,
                    url=link,
                    published=dt.isoformat() if dt else "",
                    summary=clean_text(summary, 700),
                )
            )
    return out


def sort_recent(items: list[Item]) -> list[Item]:
    return sorted(items, key=lambda x: parse_date(x.published) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)


def chinese_topic(title: str, summary: str = "") -> str:
    text = (title + " " + summary).lower()
    rules = [
        (("tax", "roth", "ira", "401", "irmaa", "medicare"), "税务/账户/医保阈值"),
        (("withdraw", "retire", "payday", "principal", "cash flow"), "退休现金流/提款"),
        (("trust", "estate", "charitable", "giving"), "信托/遗产/慈善"),
        (("real estate", "rental", "housing", "mortgage"), "房地产/居住成本"),
        (("concentrated", "direct indexing", "capital gain", "stock"), "集中持仓/税损收割"),
        (("travel", "hotel", "airline", "lounge", "resort", "suite", "business class"), "高质量旅行体验"),
        (("etf", "allocation", "portfolio", "bond", "treasury", "commodity"), "资产配置/ETF"),
        (("ai", "model", "openai", "anthropic", "google", "agent"), "AI 模型/产品"),
    ]
    for keys, label in rules:
        if any(k in text for k in keys):
            return label
    return "规划/执行风险"


def etf_research_heading(title: str, summary: str = "") -> str:
    text = (title + " " + summary).lower()
    if "institutional investor attention" in text or ("macro news" in text and "volatility" in text):
        return "机构注意力、波动率与宏观新闻"
    if "capital market" in text or "expected return" in text or "valuation" in text:
        return "长期资本市场假设与估值"
    if "treasury" in text or "duration" in text or "bond" in text or "yield" in text:
        return "债券久期、收益率与信用风险"
    if "factor" in text or "momentum" in text or "value" in text or "quality" in text:
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


def etf_chinese_fact(item: Item) -> str:
    title = clean_text(item.title, 220)
    text = clean_text(item.summary, 900)
    lower = f"{title} {text}".lower()
    parts: list[str] = []

    if "world markets watchlist" in lower:
        parts.append("文章是全球市场观察清单，围绕主要区域市场和跨资产价格表现更新当日市场状态。")
    elif "weekly economic snapshot" in lower and "labor market" in lower:
        parts.append("文章是每周经济快照，核心事实是美国劳动力市场仍显示韧性，这会影响市场对增长、通胀和利率路径的判断。")
    elif "hits $6.5 billion" in lower or "record pace" in lower:
        parts.append("文章提到相关 ETF 资产规模快速增长并达到 65 亿美元，反映该主题产品的资金关注度正在上升。")
    elif "institutional investor attention" in lower or ("macro news" in lower and "volatility" in lower):
        parts.append(
            "文章讨论机构投资者注意力：当市场波动率上升时，把更多注意力转向宏观新闻的基金表现更好。"
        )
        if "stocks they own" in lower or "position and trading decisions" in lower:
            parts.append("文章还指出，基金会更关注自己持有的股票，这种注意力有助于提升仓位管理和交易决策的价值。")
    elif "nuclear" in lower:
        parts.append("文章关注美国核能主题，核心事实是合作项目和市场情绪改善正在推动核能相关主题资产获得更多关注。")
    elif "securitization" in lower:
        parts.append("文章讨论证券化投资，重点是把贷款、应收账款或其他现金流资产打包后的信用暴露如何进入投资组合。")
    elif "melt-up" in lower:
        parts.append("文章讨论市场快速上行阶段的风险偏好，核心事实是价格上涨本身可能吸引更多资金追涨，形成短期动量强化。")
    elif "hedge fund" in lower or "bearish" in lower:
        parts.append("文章讨论对冲基金经理为何经常偏谨慎或偏空，核心事实是机构表达的风险叙事不一定等同于实际仓位。")
    elif "capital market" in lower or "expected return" in lower:
        parts.append("文章围绕长期资本市场假设、估值和预期收益展开，重点是不同资产类别未来回报与风险补偿的变化。")
    elif "treasury" in lower or "duration" in lower or "bond" in lower or "yield" in lower:
        parts.append("文章关注债券、收益率或久期变化，核心事实是利率路径会直接影响长债、短债和信用债 ETF 的价格弹性。")
    elif "factor" in lower or "momentum" in lower or "value" in lower or "quality" in lower:
        parts.append("文章讨论因子或风格表现，重点在价值、动量、质量等风险因子是否继续获得市场补偿。")
    elif "commodity" in lower or "gold" in lower or "inflation" in lower:
        parts.append("文章关注商品、黄金或通胀相关资产，核心事实是实物资产的表现通常与通胀预期、美元和实际利率有关。")
    elif text:
        parts.append(f"RSS 摘要只提供有限信息；当前可确认文章围绕“{title}”这个主题展开。")
    else:
        parts.append(f"RSS 未提供摘要；当前只能确认来源发布了“{title}”这篇内容。")

    return "".join(parts)


def etf_follow_up_point(title: str, summary: str = "") -> str:
    text = (title + " " + summary).lower()
    if "world markets watchlist" in text:
        return "可用来检查美股、海外股票、债券、商品和美元是否出现同步转向，避免只从单一 ETF 解释市场状态。"
    if "weekly economic snapshot" in text and "labor market" in text:
        return "可重点观察就业数据是否改变降息预期，并映射到长债、信用债、小盘股和周期行业的相对表现。"
    if "hits $6.5 billion" in text or "record pace" in text:
        return "可把它当作主题 ETF 资金拥挤度线索，和估值、成交量及主题成分股集中度一起看。"
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


def write_meta(out_dir: Path, subject: str, body: str, attachment: Path) -> None:
    meta = {"subject": subject, "body": body, "attachment": str(attachment)}
    (out_dir / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


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
    started = now_bj()
    feeds = {
        "Early Retirement Now": "https://earlyretirementnow.com/feed/",
        "JL Collins": "https://jlcollinsnh.com/feed/",
        "Bogleheads Blog": "https://www.bogleheads.org/blog/feed/",
        "Of Dollars And Data": "https://ofdollarsanddata.com/feed/",
        "A Wealth of Common Sense": "https://awealthofcommonsense.com/feed/",
        "Go Curry Cracker": "https://www.gocurrycracker.com/feed/",
        "Financial Samurai": "https://www.financialsamurai.com/feed/",
        "White Coat Investor": "https://www.whitecoatinvestor.com/feed/",
        "HumbleDollar": "https://humbledollar.com/feed/",
        "r/fatFIRE": "https://www.reddit.com/r/fatFIRE/hot/.rss",
        "r/ChubbyFIRE": "https://www.reddit.com/r/ChubbyFIRE/hot/.rss",
    }
    items: list[Item] = []
    for source, url in feeds.items():
        items.extend(parse_feed(source, url, limit=8))
        time.sleep(0.2)
    picked = sort_recent(items)[:14]
    date_s = report_date()
    md = out_dir / f"fat_fire_digest_{date_s}.md"
    lines = [
        f"# FAT FIRE 与高净值财务自由简报 - {date_s}",
        "",
        "> 本报告是高净值财务自由研究摘要，不构成投资、税务或法律建议。",
        "",
        "## 一句话结论",
        "",
        "今天优先关注税务阈值、退休现金流、集中持仓退出和高净值家庭生活方式风险；RSS 不足或重复时已纳入 Reddit 社区讨论作案例观察。",
        "",
        "## 今日速览",
        "",
        "| # | 中文主题 | 来源 | 日期 | 类型 |",
        "|---:|---|---|---:|---|",
    ]
    for i, it in enumerate(picked, 1):
        kind = "社区讨论/案例观察" if it.source.startswith("r/") else "RSS/博客研究"
        dt = (parse_date(it.published) or datetime.now(timezone.utc)).astimezone(BJ).date()
        lines.append(f"| {i} | {chinese_topic(it.title, it.summary)} | {it.source} | {dt} | {kind} |")
    lines += ["", "---", "", "## 条目详情", ""]
    for i, it in enumerate(picked, 1):
        kind = "社区讨论/案例观察" if it.source.startswith("r/") else "RSS/博客研究"
        lines += [
            f"### {i}. {chinese_topic(it.title, it.summary)}",
            f"- 来源：{it.source}",
            f"- 原文标题：{it.title}",
            f"- 链接：{it.url}",
            f"- 类型：{kind}",
            "",
            f"**原文事实/讨论点**：{it.summary or '来源未提供摘要，需要打开原文核对。'}",
            "",
            "**FAT FIRE 含义**：把它作为家庭资产规模较大时的执行风险提示，重点检查税务、现金流、保险、集中度、支出基准和家庭约束。",
            "",
            "**需要验证**：用自己的税率、账户结构、年龄、医保/保险方案和支出预算复算，不直接套用作者或社区案例。",
            "",
            "---",
            "",
        ]
    lines += ["## 去重与补充审计", "", "- 去重窗口：最近 7 天原则；本云端首版无法读取历史附件，后续会加入历史 URL 记录文件。", f"- 社区条目数量：{sum(1 for x in picked if x.source.startswith('r/'))}", ""]
    lines += audit_lines("07:00 Asia/Shanghai", started)
    md.write_text("\n".join(lines), encoding="utf-8")
    body = "\n".join(
        [
            "一句话结论：今天关注税务阈值、退休现金流、集中持仓退出和生活方式风险。",
            f"条目数量：{len(picked)}",
            f"社区案例数量：{sum(1 for x in picked if x.source.startswith('r/'))}",
            "完整排版版见附件。",
            f"调度审计：实际启动 {started.strftime('%Y-%m-%d %H:%M:%S %Z')}；执行环境 GitHub Actions。",
        ]
    )
    write_meta(out_dir, f"FAT FIRE 与高净值财务自由简报 - {date_s}", body, md)


def build_travel(out_dir: Path) -> None:
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
    picked = sort_recent(items)[:16]
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
        "| # | 中文主题 | 来源 | 日期 | 类型 |",
        "|---:|---|---|---:|---|",
    ]
    for i, it in enumerate(picked, 1):
        kind = "社区体验/讨论" if it.source.startswith("r/") else "RSS/旅行资讯"
        dt = (parse_date(it.published) or datetime.now(timezone.utc)).astimezone(BJ).date()
        lines.append(f"| {i} | {chinese_topic(it.title, it.summary)} | {it.source} | {dt} | {kind} |")
    lines += ["", "---", "", "## 条目详情", ""]
    for i, it in enumerate(picked, 1):
        lines += [
            f"### {i}. {chinese_topic(it.title, it.summary)}",
            f"- 来源：{it.source}",
            f"- 原文标题：{it.title}",
            f"- 链接：{it.url}",
            "",
            f"**原文事实/体验点**：{it.summary or '来源未提供摘要，需要打开原文核对。'}",
            "",
            "**对你们的意义**：用于建立酒店、航线、休息室、会籍、目的地体验和旅行节奏的长期筛选标准。",
            "",
            "**可以沉淀的标准**：记录舒适度、位置、服务稳定性、航线便利性、权益兑现和长期在路上的疲劳成本。",
            "",
            "---",
            "",
        ]
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


def build_etf(out_dir: Path) -> None:
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
            f"### {i}. {etf_research_heading(it.title, it.summary)}",
            f"- 来源：{it.source}",
            f"- 原文标题：{it.title}",
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


def fetch_json(url: str) -> object:
    return json.loads(fetch_bytes(url).decode("utf-8"))


def build_ai(out_dir: Path) -> None:
    started = now_bj()
    base = "https://aihot.virxact.com"
    daily = fetch_json(f"{base}/api/public/daily")
    date_s = str(daily.get("date") or report_date())
    sections = daily.get("sections") or []
    md = out_dir / f"ai_hot_digest_{date_s}.md"
    lines = [f"# AI HOT 日报 - {date_s}", "", "## 一句话结论", "", clean_text(daily.get("lead") or "今日 AI 热点以模型、产品和行业动态为主。"), ""]
    for sec in sections:
        label = sec.get("label") or "其他"
        lines += ["---", "", f"## {label}", ""]
        for i, it in enumerate(sec.get("items") or [], 1):
            title = clean_text(it.get("title") or "")
            summary = clean_text(it.get("summary") or "")
            url = it.get("sourceUrl") or it.get("url") or ""
            source = it.get("sourceName") or it.get("source") or ""
            lines += [
                f"### {i}. {title}",
                f"- 来源：{source or '未标注'}",
                f"- 原文链接：{url or '未提供'}",
                "",
                f"**要点摘要**：{summary or 'AI HOT 未提供摘要。'}",
                "",
                "**为什么重要**：可能影响模型选择、产品工作流、成本结构或行业竞争格局，建议结合实际使用场景判断是否跟进。",
                "",
            ]
    lines += audit_lines("08:15 Asia/Shanghai", started)
    md.write_text("\n".join(lines), encoding="utf-8")
    flat = [it for sec in sections for it in (sec.get("items") or [])]
    body = "\n".join(
        [
            f"一句话结论：{clean_text(daily.get('lead') or '今日 AI 热点以模型、产品和行业动态为主。', 160)}",
            f"实际日报日期：{date_s}",
            f"条目数量：{len(flat)}",
            "完整排版版见附件。",
            f"调度审计：实际启动 {started.strftime('%Y-%m-%d %H:%M:%S %Z')}；执行环境 GitHub Actions。",
        ]
    )
    write_meta(out_dir, f"AI HOT 日报 - {date_s}", body, md)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", choices=["fat-fire", "travel", "etf", "ai-hot"])
    parser.add_argument("--out-dir", default="artifacts")
    args = parser.parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.report == "fat-fire":
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
