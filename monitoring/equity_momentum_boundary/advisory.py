"""Evidence-linked research interpretation; never an order or allocation engine."""

from datetime import datetime
from html import escape
import math


VARIANTS = ("cape_boundary_10y", "cape_boundary_20y",
            "dividend_yield_boundary_10y", "dividend_yield_boundary_20y")


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _date(value, monthly=False):
    try:
        return (datetime.strptime(value, "%Y-%m") if monthly else datetime.fromisoformat(value)).date()
    except (TypeError, ValueError):
        return None


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _valid_core(item):
    if not isinstance(item, dict) or item.get("status") != "ok":
        return False
    if not all(number(item.get(k)) and 0 <= item[k] <= 1 for k in ("valuation_percentile", "term_spread_percentile")):
        return False
    if not all(number(item.get(k)) for k in ("valuation_ma12", "term_spread_ma12", "boundary_score_0_to_2")):
        return False
    if item["valuation_ma12"] <= 0 or not 0 <= item["boundary_score_0_to_2"] <= 2:
        return False
    count = sum(item[k] <= .1 or item[k] >= .9 for k in ("valuation_percentile", "term_spread_percentile"))
    return type(item.get("extreme_components")) is int and item["extreme_components"] == count


def build_advisory(report, previous=None):
    core = _mapping(report.get("core"))
    context = _mapping(report.get("supplemental"))
    market = _mapping(context.get("sp500_price_momentum"))
    vix = _mapping(context.get("vix"))
    m1, m3, m12 = (market.get("momentum_" + suffix) for suffix in ("1m", "3m", "12m"))
    generated = _date(report.get("generated_at_utc"))
    anchor = _date(_mapping(core.get("cape_boundary_10y")).get("as_of"), True)
    current, validity, initial_quality = {}, {}, []
    for key in VARIANTS:
        item = _mapping(core.get(key))
        date = _date(item.get("as_of"), True)
        age_months = (generated.year - date.year) * 12 + generated.month - date.month if generated and date else None
        valid = _valid_core(item)
        future = age_months is not None and age_months < 0
        background = bool(key.startswith("dividend") and date and anchor and date < anchor and not future)
        eligible = bool(valid and age_months is not None and 0 <= age_months <= 3 and date == anchor and not background)
        reason = ("未来日期，剔除" if future else "日期缺失或异常" if date is None else
                  "数值缺失、非有限、越界或组件计数不一致" if not valid else
                  "较旧股息率仅作滞后背景" if background else
                  "超过3个月或参考月不同，不能合并" if not eligible else "有效")
        validity[key] = dict(valid=valid and not future, current=eligible, background=background, reason=reason)
        if eligible:
            current[key] = item
            if age_months == 0:
                initial_quality.append(f"{key}使用报告当月数据，可能为暂估或未完整月份，不能视作月结确认。")
        else:
            initial_quality.append(f"{key}（截至{item.get('as_of')}）：{reason}；不参与当前边界判定。")
    complete = all(key in current for key in VARIANTS[:2])
    current_count = max((v["extreme_components"] for v in current.values()), default=0)
    current_state = {0: "中性区间", 1: "偏边界", 2: "边界"}[current_count] if complete else "数据不完整"
    missing_dividend = any(key not in current for key in VARIANTS[2:])
    if missing_dividend:
        initial_quality.append("当前核心仅由同月有效CAPE窗口支持，股息率交叉确认不足；历史标签不代表本期状态。")
    conflict = False
    for years in (10, 20):
        a, b = current.get(f"cape_boundary_{years}y"), current.get(f"dividend_yield_boundary_{years}y")
        if a and b:
            ap, bp = a["valuation_percentile"], b["valuation_percentile"]
            conflict |= (ap >= .9 and bp >= .9) or (ap <= .1 and bp <= .1)
    if conflict:
        initial_quality.append("同月CAPE与股息率估值方向相反，不能合并成一致高估或低估判断；边界仅表示极端程度。")
    market_fresh = False
    try:
        generated = datetime.fromisoformat(report["generated_at_utc"]).date()
        age = (generated - datetime.fromisoformat(market["as_of"]).date()).days
        market_fresh = 0 <= age <= 7
    except (KeyError, TypeError, ValueError):
        pass
    core_fresh = False
    vix_fresh = False
    try:
        cape_date = datetime.strptime(core["cape_boundary_10y"]["as_of"], "%Y-%m").date()
        months_old = (generated.year - cape_date.year) * 12 + generated.month - cape_date.month
        core_fresh = 0 <= months_old <= 3
        vix_age = (generated - datetime.fromisoformat(vix["as_of"]).date()).days
        vix_fresh = 0 <= vix_age <= 7
    except (KeyError, TypeError, ValueError, UnboundLocalError):
        pass
    usable = complete and core_fresh and market_fresh and all(number(v) and v > -1 for v in (m1, m3, m12)) and number(market.get("price")) and market["price"] > 0
    vix_aligned = (vix_fresh and vix.get("as_of") == market.get("as_of")
                   and number(vix.get("value")) and vix["value"] > 0
                   and number(vix.get("weekly_change")) and vix["value"] - vix["weekly_change"] > 0
                   and number(vix.get("one_year_percentile")) and 0 <= vix["one_year_percentile"] <= 1)
    stress = vix_aligned and vix["one_year_percentile"] >= .8
    reversal = number(m1) and number(m12) and m1 * m12 < 0
    if not usable:
        stance = "证据不足，暂缓新增风险判断"
        conclusion = "核心边界或近期价格数据不完整、过期，暂不能形成可靠的综合风险结论。"
    elif m12 <= 0:
        stance = "偏防守，核查策略退出条件"
        conclusion = "12个月价格动量已非正，趋势支持减弱；优先核查既有策略的减仓或退出规则。"
    elif m1 < 0 or m3 < 0:
        stance = "趋势受扰，放缓新增风险"
        conclusion = "12个月动量仍正，但短期或中期动量转负，需警惕趋势延续受阻及反复切换。"
    elif stress and number(vix.get("weekly_change")) and vix["weekly_change"] > 0:
        stance = "趋势仍正，风险警戒升温"
        conclusion = "价格动量仍正，但VIX已处自身近一年较高分位且本周上升，新增风险宜更谨慎。"
    elif current_state in ("偏边界", "边界"):
        stance = "趋势仍正，留意极端环境"
        conclusion = "价格动量仍正，但核心变量处历史尾部；继续跟踪趋势，同时保留风险余量。"
    else:
        stance = "趋势仍正，按既定规则跟踪"
        conclusion = "当前价格动量为正，核心指标未触及尾部；现有证据没有提示提高边界警戒等级。"

    if usable and missing_dividend:
        conclusion += "股息率缺少同期确认，本期结论仅覆盖有效同月核心。"
    if usable and conflict:
        conclusion += "估值方向存在分歧，不作一致高估或低估判断。"
    if usable and not vix_aligned:
        conclusion += "波动证据缺失、异常或日期不齐，不能确认风险已经缓和。"
    evidence = []
    for key in VARIANTS:
        item = _mapping(core.get(key))
        if not validity[key]["valid"]:
            evidence.append(f"{key}（截至{item.get('as_of')}）：数据不足或异常，不能用于确认边界。")
            continue
        label = "CAPE" if key.startswith("cape") else "股息率"
        p = item.get("valuation_percentile")
        tp = item.get("term_spread_percentile")
        direction = "方向不明"
        if number(p):
            direction = ("偏高估" if p >= .9 else "偏低估" if p <= .1 else "非估值尾部") if label == "CAPE" else (
                "偏高估（低股息率）" if p <= .1 else "偏低估（高股息率）" if p >= .9 else "非估值尾部")
        vp = f"{p:.1%}" if number(p) else "缺失"
        sp = f"{tp:.1%}" if number(tp) else "缺失"
        role = "当前判定" if validity[key]["current"] else "历史背景，未用于当前判定"
        evidence.append(f"{label} {item.get('window_years')}年窗口（截至{item.get('as_of')}，{role}）："
                        f"估值分位{vp}，{direction}；期限利差分位{sp}。"
                        "分位基于12个月均值；两个组件取平方后的总分不表示涨跌方向。")
    if all(number(v) for v in (m1, m3, m12)):
        evidence.append(f"S&P 500截至{market.get('as_of')}：1个月{m1:+.2%}、3个月{m3:+.2%}、12个月{m12:+.2%}；"
                        + ("长短期方向相反，存在反转警示。" if reversal else "长短期未出现反向组合。"))
    if vix_aligned:
        evidence.append(f"VIX截至{vix.get('as_of')}为{vix['value']:.2f}，较约一周前{vix['weekly_change']:+.2f}点；"
                        + (f"一年分位{vix['one_year_percentile']:.1%}。" if number(vix.get("one_year_percentile")) else "一年分位缺失。"))
    for key, label in (("term_spread_10y3m", "10年减3个月期限利差"),
                       ("term_spread_10y2y", "10年减2年期限利差"), ("baa_minus_10y", "Baa减10年国债信用利差")):
        item = _mapping(context.get(key))
        context_date = _date(item.get("as_of"))
        context_valid = bool(generated and context_date and 0 <= (generated - context_date).days <= 7
                             and number(item.get("value")) and number(item.get("weekly_change")))
        if context_valid:
            evidence.append(f"{label}截至{item.get('as_of')}为{item['value']:.2f}个百分点，周变化{item['weekly_change']:+.2f}个百分点；"
                            "仅作环境背景，不能单独推出股市方向。")

    changes = []
    if previous:
        changes.append(f"对比快照：{previous.get('generated_at_utc')}；只有日期及口径可比的项目用于变化判断。")
        changed_source = [(s.get("name"), s.get("url"), s.get("detail")) for s in report.get("sources", [])] != [
            (s.get("name"), s.get("url"), s.get("detail")) for s in previous.get("sources", [])]
        if changed_source:
            changes.append("数据来源或下载链接有变化，可能包含数据修订或来源切换；变化需谨慎解释。")
        for key in VARIANTS:
            now, old = core.get(key, {}), previous.get("core", {}).get(key, {})
            if now.get("status") != "ok" or old.get("status") != "ok":
                continue
            label = f"{now.get('value_label', key)} {now.get('window_years')}年"
            if now.get("as_of") != old.get("as_of"):
                changes.append(f"{label}参考月从{old.get('as_of')}变为{now.get('as_of')}，属于参考期更新，不将全部差额解释为本周市场变化。")
            elif any(now.get(k) != old.get(k) for k in ("valuation_ma12", "term_spread_ma12", "boundary_score_0_to_2", "valuation_percentile", "term_spread_percentile", "extreme_components", "state", "method")):
                changes.append(f"{label}参考月未变但读数改变，可能为历史修订或口径变化，不视为新增月份信号。")
            else:
                changes.append(f"{label}月度核心读数未变。")
        changes.append(f"核心状态标签：{previous.get('overall_state', '未知')} → {current_state}（本期仅计有效同月核心）。")
    else:
        changes.append("没有有效的上期报告快照，暂不判断核心状态是否较上周升级；本期作为后续比较基线。")
    price, delta = market.get("price"), market.get("weekly_change")
    if number(price) and number(delta) and price - delta > 0:
        changes.append(f"S&P 500较约7日前可用交易日变化{delta / (price - delta):+.2%}。")
    changes.append("日频的一年分位与核心10年/20年月度分位不同，不可替换或混用。")

    suggestions = []
    if not usable:
        suggestions.append("新增资金：先核实缺失或过期数据；本报告暂不支持新增仓位或杠杆决策。")
    elif m12 <= 0:
        suggestions.append("已有仓位：优先核查原策略的趋势、退出及风险预算条件；满足条件时按原规则执行。新增资金先等待趋势改善。")
    elif m1 < 0 or m3 < 0 or stress or current_state == "边界" or not vix_aligned or conflict:
        suggestions.append("已有仓位：复核既定调仓与风险预算，继续依原策略执行；新增资金：放缓追涨和增加杠杆，等待趋势与波动证据改善。")
    else:
        suggestions.append("已有仓位：按既定策略持有与调仓；新增资金：依原定计划分批执行，当前周报没有支持额外提高杠杆的证据。")
    suggestions.append("风险检查：查看保证金与现金余量、股票风险是否集中、波动上升后是否仍在原定风险预算内。没有个人仓位及回测依据，不给任意减仓比例。")
    suggestions.append("边界标签用于研究警戒；高估或低股息率不能单独推出立即卖出、做空或市场即将见顶，也不自动覆盖既有交易规则。")
    triggers = [
        "警戒上调：12个月动量仍正而1个月动量转负，或3个月动量转负；若同时出现VIX继续上升，优先复核风险预算。",
        "进一步防守复核：12个月动量转为非正，或估值与期限利差同时进入各自10年/20年历史尾部；是否交易仍取决于原策略。",
        "警戒缓和：1个月和3个月动量恢复为正、VIX回落；月度核心是否离开尾部需等待对应数据更新。",
        "以上是透明的研究观察条件；VIX一年80%分位为描述性警戒口径，并非论文验证的交易阈值。",
    ]
    triggers[-1] = "以上全部是研究启发式观察条件，包括动量正负、10%/90%尾部及VIX一年80%分位；并非论文验证的交易阈值，也未证明能改善收益或回撤。旧月股息率不可触发本期警戒。"
    if usable:
        triggers.insert(0, f"本期起点：1个月{m1:+.2%}、3个月{m3:+.2%}、12个月{m12:+.2%}；当前同月核心为{current_state}。短中期负动量已触发复核。" if m1 < 0 or m3 < 0 else f"本期起点：1个月{m1:+.2%}、3个月{m3:+.2%}、12个月{m12:+.2%}；下次重点观察短中期是否跌破零。")
    quality = initial_quality
    if not vix_aligned:
        quality.append("VIX缺失、非有限、分位越界或与价格日期不齐，不用于本期联合风险判断；缺失不能作为放松新增风险的依据。")
    months = sorted({str(_mapping(core.get(k)).get("as_of")) for k in VARIANTS if _mapping(core.get(k)).get("status") == "ok" and _mapping(core.get(k)).get("as_of")})
    if len(months) > 1:
        quality.append("核心参考月份不一致：" + "、".join(months) + "；较旧股息率仅作滞后背景，不能确认当前同月风险。")
    credit = _mapping(context.get("baa_minus_10y"))
    credit_date = _date(credit.get("as_of"))
    if not (generated and credit_date and 0 <= (generated - credit_date).days <= 7 and number(credit.get("value"))):
        quality.append("信用利差缺失，无法确认信用风险是否同步恶化；缺失不等于风险正常。")
    if not market_fresh:
        quality.append("价格数据日期缺失或距离报告日期超过7天，综合判断降级。")
    if not core_fresh:
        quality.append("CAPE核心参考月缺失、异常或超过3个月，综合判断降级。")
    if not vix_fresh:
        quality.append("VIX日期缺失或超过7天，未据此判定当前波动警戒状态。")
    quality.append("10Y−3M与10Y−2Y需分别解读；曲线变陡或变平本身不直接等于利好或衰退。")
    quality.append("期限利差使用公开代理，FRED与H.15口径差异会影响可比性；详细来源与错误信息见文末。")
    return {"stance": stance, "conclusion": conclusion, "current_core_state": current_state, "validity": validity, "sections": [
        ("关键依据", evidence), ("相较上期与本周变化", changes),
        ("可参考的应对建议", suggestions), ("下周观察与改变判断的条件", triggers),
        ("结论的证据限制", quality)]}


def render_advisory_text(advisory):
    lines = ["本周结论：" + advisory["stance"], advisory["conclusion"]]
    for title, items in advisory["sections"]:
        lines.extend(["", title] + ["- " + item for item in items])
    return "\n".join(lines)


def render_advisory_html(advisory):
    parts = ["<h3>本周结论：" + escape(advisory["stance"]) + "</h3><p>" + escape(advisory["conclusion"]) + "</p>"]
    for title, items in advisory["sections"]:
        parts.append("<h3>" + escape(title) + "</h3><ul>" + "".join("<li>" + escape(item) + "</li>" for item in items) + "</ul>")
    return "".join(parts)
