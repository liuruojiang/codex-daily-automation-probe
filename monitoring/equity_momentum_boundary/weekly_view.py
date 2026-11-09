"""Reader-facing weekly changes; monthly research inputs keep their own cadence."""

from datetime import datetime

from advisory import number


def mapping(value):
    return value if isinstance(value, dict) else {}


def market_lines(report, previous=None):
    market = mapping(mapping(report.get("supplemental")).get("sp500_price_momentum"))
    prior = mapping(mapping((previous or {}).get("supplemental")).get("sp500_price_momentum"))
    lines = []
    price, delta = market.get("price"), market.get("weekly_change")
    if number(price) and price > 0 and number(delta) and price - delta > 0:
        lines.append(f"标普500截至{market.get('as_of')}：本周涨跌{delta / (price - delta):+.2%}"
                     "（相对约7日前可用收盘）；价格指数，不含股息。")
    else:
        lines.append("标普500本周涨跌数据不足。")
    comparable = False
    try:
        gap = (datetime.fromisoformat(market["as_of"]) - datetime.fromisoformat(prior["as_of"])).days
        comparable = 0 < gap <= 14
    except (KeyError, TypeError, ValueError):
        pass
    for suffix, label in (("1m", "1个月"), ("3m", "3个月"), ("6m", "6个月"), ("12m", "12个月")):
        value, old = market.get("momentum_" + suffix), prior.get("momentum_" + suffix)
        if number(value) and value > -1:
            change = f"，较上期{(value - old) * 100:+.2f}个百分点" if comparable and number(old) and old > -1 else "，上期不可比或缺失"
            lines.append(f"{label}价格动量：{value:+.2%}{change}。")
    vol, old_vol = market.get("realized_vol_20d_annualized"), prior.get("realized_vol_20d_annualized")
    if number(vol) and vol >= 0:
        change = f"，较上期{(vol - old_vol) * 100:+.2f}个百分点" if comparable and number(old_vol) and old_vol >= 0 else "，上期不可比或缺失"
        lines.append(f"20日年化实现波动率：{vol:.2%}{change}。")
    else:
        lines.append("20日年化实现波动率缺失。")
    vix = mapping(mapping(report.get("supplemental")).get("vix"))
    if number(vix.get("value")) and vix["value"] > 0:
        change = f"，较约一周前{vix['weekly_change']:+.2f}点" if number(vix.get("weekly_change")) else "，周变化缺失"
        rank = vix.get("one_year_percentile")
        percentile = f"；一年分位{rank:.1%}" if number(rank) and 0 <= rank <= 1 else "；一年分位缺失或异常"
        lines.append(f"VIX截至{vix.get('as_of')}：{vix['value']:.2f}{change}{percentile}。")
    lines.append("动量和实现波动的上期差额按两份报告各自收盘日比较；日期相同不冒充本周变化。")
    return lines


def monthly_update(item, old):
    """A source-month advance and a same-month revision are different events."""
    if not old or old.get("status") != "ok":
        return "更新状态：上期缺失或不可比，无法确认是否更新"
    month, prior_month = item.get("as_of"), old.get("as_of")
    if month != prior_month:
        return f"更新状态：参考月变化（{prior_month} → {month}）"
    fields = ("valuation_raw", "valuation_ma12", "valuation_percentile")
    if any(item.get(key) != old.get(key) for key in fields):
        return "更新状态：同月读数修订，未新增月份"
    return "更新状态：本期未更新（参考月及估值读数与上期一致）"
