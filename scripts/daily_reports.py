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


def article_paragraphs(url: str, limit: int = 8) -> list[str]:
    try:
        page = fetch_bytes(url, timeout=20).decode("utf-8", "ignore")
    except Exception:
        return []
    raw_paras = re.findall(r"<p[^>]*>(.*?)</p>", page, flags=re.I | re.S)
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
    )
    for para in raw_paras:
        text = clean_text(para, 700)
        lower = text.lower()
        if len(text) < 55:
            continue
        if any(x in lower for x in noise):
            continue
        out.append(text)
        if len(out) >= limit:
            break
    return out


def enrich_article_item(item: Item) -> Item:
    paras = article_paragraphs(item.url)
    if not paras:
        return item
    summary = clean_text(" ".join([item.summary, *paras]), 3000)
    return Item(item.source, item.title, item.url, item.published, summary)


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
    return chinese_topic(title, summary)


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


def etf_chinese_fact(item: Item) -> str:
    title = clean_text(item.title, 220)
    text = clean_text(item.summary, 3000)
    lower = f"{title} {text}".lower()
    parts: list[str] = []

    if "world markets watchlist" in lower:
        parts.append(
            "文章跟踪全球九个主要股票指数，截至 2026 年 5 月 11 日，其中六个指数年内仍为正收益。"
            "日本 Nikkei 225 年内上涨约 24.0%，领先观察清单；美国 S&P 500 上涨约 8.3%，加拿大 TSX 上涨约 7.7%。"
            "表现较弱的是印度 BSE SENSEX，年内下跌约 10.8%；德国 DAXK 和法国 CAC 40 分别下跌约 2.6% 和 1.1%。"
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
    picked = [x if x.source.startswith("r/") else enrich_article_item(x) for x in picked]
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
        fact_label = "帖子讨论点" if it.source.startswith("r/") else "原文事实"
        lines += [
            f"### {i}. {chinese_topic(it.title, it.summary)}",
            f"- 来源：{it.source}",
            f"- 原文标题：{it.title}",
            f"- 链接：{it.url}",
            f"- 类型：{kind}",
            "",
            f"**{fact_label}**：{fat_fire_chinese_fact(it)}",
            "",
            f"**FAT FIRE 含义**：{fat_fire_implication(it.title, it.summary)}",
            "",
            f"**需要验证**：{fat_fire_validation(it.title, it.summary)}",
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
    candidates = [x for x in sort_recent(items) if travel_relevant(x)]
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
        "| # | 中文主题 | 来源 | 日期 | 类型 |",
        "|---:|---|---|---:|---|",
    ]
    for i, it in enumerate(picked, 1):
        kind = "社区体验/讨论" if it.source.startswith("r/") else "RSS/旅行资讯"
        dt = (parse_date(it.published) or datetime.now(timezone.utc)).astimezone(BJ).date()
        lines.append(f"| {i} | {chinese_topic(it.title, it.summary)} | {it.source} | {dt} | {kind} |")
    lines += ["", "---", "", "## 条目详情", ""]
    for i, it in enumerate(picked, 1):
        fact_label = "帖子体验点" if it.source.startswith("r/") else "原文事实"
        lines += [
            f"### {i}. {travel_heading(it.title, it.summary)}",
            f"- 来源：{it.source}",
            f"- 原文标题：{it.title}",
            f"- 链接：{it.url}",
            "",
            f"**{fact_label}**：{travel_chinese_fact(it)}",
            "",
            f"**对你们的意义**：{travel_implication(it.title)}",
            "",
            f"**可以沉淀的标准**：{travel_standard(it.title)}",
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


A_STRATEGY_ASSETS = [
    MarketAsset("H20955", "中证红利低波100全收益", "eastmoney", "1.000827", "A策略使用的红利低波权益指数；日涨跌用中证红利低波动100价格指数代理。", "A策略"),
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
    MarketAsset("000300", "沪深300指数", "eastmoney", "1.000300", "A股核心大盘宽基指数。", "China Equity"),
    MarketAsset("000905", "中证500指数", "eastmoney", "1.000905", "A股中盘宽基指数。", "China Equity"),
    MarketAsset("000852", "中证1000指数", "eastmoney", "1.000852", "A股小盘宽基指数。", "China Equity"),
    MarketAsset("399006", "创业板指", "eastmoney", "0.399006", "A股成长风格核心指数。", "China Equity"),
]


def build_etf(out_dir: Path) -> None:
    started = now_bj()
    strategy_assets = A_STRATEGY_ASSETS + ADK_STRATEGY_ASSETS + B_STRATEGY_ASSETS + D_STRATEGY_ASSETS
    strategy_rows = fetch_asset_changes(strategy_assets)
    core_rows = fetch_asset_changes(CORE_MARKET_ASSETS)
    mover_rows = fetch_asset_changes(MOVER_UNIVERSE)
    top_rows = dedupe_by_category(mover_rows, reverse=True)
    bottom_rows = dedupe_by_category(mover_rows, reverse=False)

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
    data_dates = sorted({str(r["date"]) for r in strategy_rows + core_rows + mover_rows})
    data_date_s = data_dates[-1] if data_dates else "数据不足"
    md = out_dir / f"us_etf_allocation_digest_{date_s}.md"

    lines = [
        f"# 美股 ETF 与资产配置日报 - {date_s}",
        "",
        f"> 数据日期：最新可取得的收盘数据截至 {data_date_s}；涨跌幅为收盘价相对上一交易日的价格涨跌，不含分红再投资。",
        "",
        "## 目录",
        "- [策略相关 ETF / 指数涨跌](#策略相关-etf--指数涨跌)",
        "- [市场核心指数涨跌](#市场核心指数涨跌)",
        "- [前一交易日 ETF / 指数涨跌幅榜](#前一交易日-etf--指数涨跌幅榜)",
        "- [研究/资讯线索](#研究资讯线索)",
        "",
        "---",
        "",
        "## 一句话结论",
        "",
        "今天的日报先看 A、ADK、B、D 四个策略直接涉及的资产，再看核心市场指数，最后看已过滤杠杆、反向、期权收益增强、单一资产和同类重复后的 ETF/指数涨跌榜。",
        "",
        "## 策略相关 ETF / 指数涨跌",
        "",
    ]
    append_asset_table(lines, strategy_rows, include_strategy=True)

    lines += ["", "---", "", "## 市场核心指数涨跌", ""]
    append_asset_table(lines, core_rows)

    lines += [
        "",
        "---",
        "",
        "## 前一交易日 ETF / 指数涨跌幅榜",
        "",
        "过滤口径：已排除杠杆、反向、期权/收益增强、单股日内目标、单一资产信托/现货商品/单一加密产品，并按主题/类别去重；每个类别只保留当日表现最极端的一只。",
        "",
        "### 涨幅前 10",
        "",
    ]
    append_asset_table(lines, top_rows)
    lines += ["", "### 跌幅前 10", ""]
    append_asset_table(lines, bottom_rows)

    if now_bj().weekday() == 5:
        for label, sessions in [("最近一周", 5), ("最近一个月", 21)]:
            period_rows = fetch_asset_changes(MOVER_UNIVERSE, sessions=sessions)
            lines += ["", f"### {label}涨幅前 10", ""]
            append_asset_table(lines, dedupe_by_category(period_rows, reverse=True))
            lines += ["", f"### {label}跌幅前 10", ""]
            append_asset_table(lines, dedupe_by_category(period_rows, reverse=False))

    lines += ["", "---", "", "## 研究/资讯线索", ""]
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
    lines += audit_lines("08:00 Asia/Shanghai", started)
    md.write_text("\n".join(lines), encoding="utf-8")

    top_preview = "; ".join(
        f"{row['asset'].code} {fmt_change(row['change'])}" for row in top_rows[:3] if isinstance(row["asset"], MarketAsset)
    )
    bottom_preview = "; ".join(
        f"{row['asset'].code} {fmt_change(row['change'])}" for row in bottom_rows[:3] if isinstance(row["asset"], MarketAsset)
    )
    body = "\n".join(
        [
            "一句话结论：ETF/资产配置日报已按策略池、核心指数、去重涨跌榜三段式生成。",
            f"数据日期：{data_date_s}",
            f"涨幅靠前：{top_preview}",
            f"跌幅靠前：{bottom_preview}",
            "完整排版版见附件。",
            f"调度审计：实际启动 {started.strftime('%Y-%m-%d %H:%M:%S %Z')}；执行环境 GitHub Actions。",
        ]
    )
    write_meta(out_dir, f"美股 ETF 与资产配置日报 - {date_s}", body, md)


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
