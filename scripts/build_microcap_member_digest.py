"""Render the independent member-daily result without promoting missing fields."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import csv
import io
import math
import zipfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from microcap_member_report_contract import (
    checkpoint_proof, final_signal_files, member_action, publication_context, saved_close_identity,
    validate_publication_clock, validate_signal_identity, versions_for_date,
)

SCHEMA = "member_daily_report_v1"
REVISIONS = {
    "v2.0": "plain_mom16_fixed1_20260904",
    "v2.3": "plain_lb25_hl2p5_r2off_no_overheat_20261029",
    "v2.5": "plain_lb20_hl3_entry0_exit0_20260905",
}


def now_bj():
    return datetime.now(ZoneInfo('Asia/Shanghai'))


def number(value, percent=False):
    try:
        value=float(value)
        if not math.isfinite(value): return 'N/A（字段缺失）'
        return f'{value:.2%}' if percent else f'{value:.4f}'
    except (TypeError,ValueError): return 'N/A（字段缺失）'


def holding(value):
    return {'cash':'空仓','long_microcap_short_zz1000':'多微盘、空中证1000',
            'long_microcap_top100':'多微盘'}.get(value,str(value or 'N/A'))


def cell(value):
    return str(value).replace('|','／').replace('\n',' ')


def details(result, by_version, context, member_actions=None):
    """Presentation only: read saved evidence, never fetch or run a model."""
    cp=context.get('checkpoint',{}); period=context.get('period',{})
    next_day=cp.get('next_trade_date') if cp.get('as_of_date')==result['market_date'] else None
    lines=['','## 信号与执行日期','',
           f"信号日：{result['market_date']}；下一实际执行日：{next_day or 'N/A（本次未绑定下一交易日）'}。",
           '当前持仓是模型已确认状态；下一目标以本次信号为准，不代表账户已经成交。',
           '', '## 三版状态与原因','',
           '|版本|当前→目标|当前尺度→目标尺度|本次状态／动作|', '|---|---|---|---|']
    for version,item in by_version.items():
        s=item.get('signal') or {}; verified=item['status']=='verified'
        action='未确认：'+str(item.get('reason') or '新一期名单待交接')
        if verified:
            current,target=s.get('current_holding'),s.get('next_holding')
            action=('维持空仓，无需操作' if current==target=='cash' else
                    '继续持有' if current==target else '下一执行日模型入场' if current=='cash' else
                    '下一执行日模型退出' if target=='cash' else '持仓调整')
            if current==target and current!='cash' and s.get('scale_trade_required') is True: action='持仓相同，尺度调整'
            try:
                scales=[float(s[k]) for k in ('current_execution_scale','next_session_target_scale')]
                if not all(math.isfinite(v) and v>=0 for v in scales): raise ValueError()
                if (current=='cash' and scales[0]!=0) or (target=='cash' and scales[1]!=0): raise ValueError()
            except (KeyError,ValueError,TypeError): action='尺度字段缺失或冲突，动作未确认；持仓字段照列'
            rotated=(member_actions or {}).get(version)
            if rotated:
                action=rotated['text'] if action=='继续持有' else action+'；'+rotated['text']
        lines.append('|'+ '|'.join(map(cell,[version,holding(s.get('current_holding'))+'→'+holding(s.get('next_holding')),
            number(s.get('current_execution_scale'))+'→'+number(s.get('next_session_target_scale')),action]))+'|')
    lines+=['','|版本|动量指标|本次读数|入场／退出条件|判断说明|','|---|---|---|---|---|']
    for version,item in by_version.items():
        s=item.get('signal') or {}
        if version=='v2.0':
            score=s.get('momentum_gap');label='16日微盘－中证1000动量差';rule='空仓时动量差>0入场；持有时<0退出；等于0保持'
            explain='微盘动量 '+number(s.get('microcap_mom'),True)+'；中证1000动量 '+number(s.get('hedge_mom'),True)
        elif version=='v2.3':
            score=s.get('annualized_log_wls_score');label='价差净值年化对数WLS得分（25日／半衰期2.5）'
            rule='空仓时得分>'+number(s.get('momentum_gap_entry_threshold'),True)+'入场；持有时得分<−'+number(s.get('momentum_gap_exit_buffer'),True)+'退出'
            explain='R²过滤关闭；过热约束关闭；信号对冲1、执行对冲0.8'
        else:
            score=s.get('annualized_log_wls_score');label='微盘年化对数WLS得分（20日／半衰期3）'
            rule='得分>'+number(s.get('entry_threshold'),True)+'入场；≤'+number(s.get('exit_threshold'),True)+'退出'
            explain='仅微盘，无指数对冲；R²、过热和目标波动率约束关闭'
        if item['status']!='verified': explain+='；本行仅展示已保存字段，完整目标未确认'
        lines.append('|'+ '|'.join(map(cell,[version,label,number(score,True),rule,explain]))+'|')
    lines+=['','v2.3、v2.5的年化对数WLS得分是信号指标，不是实际收益率；负值可以低于−100%。',
            '', '## 行情与资料','', '|项目|本次证据|','|---|---|']
    quotes=context.get('quotes',{});hedge=context.get('hedge',{})
    sources=sorted({str(row.get('source')) for row in quotes.get('rows',[]) if row.get('source')})
    evidence=[('成员行情覆盖',result.get('quote_coverage','N/A')),
              ('历史计算锚点',result.get('anchor_date','N/A')),
              ('成员行情来源','；'.join(sources) or 'N/A（本次未附逐成员来源）'),
              ('成员行情采集完成',quotes.get('collected_at',result.get('collected_at','N/A'))),
              ('中证1000收盘／快照',number(hedge.get('close'))),
              ('指数来源与采集完成',str(hedge.get('source','N/A'))+'；'+str(hedge.get('collected_at','N/A'))),
              ('信号结果生成',result.get('generated_at','N/A'))]
    history=context.get('history',[])
    if len(history)>=2 and str(history[-1].get('date',''))[:10]==result['market_date']:
        for column,label in [('microcap','微盘篮子当日涨跌'),('hedge','中证1000当日涨跌')]:
            try: value=float(history[-1][column])/float(history[-2][column])-1
            except (ValueError,TypeError,KeyError,ZeroDivisionError): value=None
            evidence.append((label,number(value,True)))
    for key,value in evidence: lines.append('|'+cell(key)+'|'+cell(value)+'|')
    lines+=['','## 本期名单与双周交接','',
            f"本期选股日：{period.get('anchor_day','N/A')}；实际名单收益起算日：{period.get('effective_from','N/A')}；下次计划选股日：{period.get('next_rebalance_date','N/A')}。",
            f"名单状态：{result.get('period_status','未绑定')}；目标与实际生效名单分开保存，受限换仓不视为完成。"]
    for event in period.get('rebalance_events',[]):
        lines.append(f"历史名单事件 {event.get('rebalance_date','N/A')}：换入{event.get('entry_count','N/A')}、换出{event.get('exit_count','N/A')}；受阻换入{event.get('blocked_entry_count','N/A')}、受阻换出{event.get('blocked_exit_count','N/A')}。这是名单背景，不自动成为今天的买卖动作。")
    if any((x.get('signal') or {}).get('current_holding')=='cash' and (x.get('signal') or {}).get('next_holding')=='cash' for x in by_version.values()):
        lines.append('当前和下一目标均为空仓的版本，即使名单有更新，也不据此产生股票买卖。')
    lines+=['','历史绩效、长期NAV及全市场资料更新由独立维护提供；本次没有重算或冒用旧绩效作为今日数值。']
    return '\n'.join(lines)+'\n'


def html_document(body):
    lines=body.splitlines();out=[];i=0
    while i<len(lines):
        line=lines[i]
        if line.startswith('|') and i+1<len(lines) and lines[i+1].startswith('|---'):
            out.append('<table style="border-collapse:collapse" border="1" cellpadding="6">')
            for j in range(i,len(lines)):
                if not lines[j].startswith('|'): break
                if j==i+1: continue
                tag='th' if j==i else 'td'
                out.append('<tr>'+''.join('<'+tag+'>'+html.escape(v.strip())+'</'+tag+'>' for v in lines[j].strip('|').split('|'))+'</tr>')
            else: j=len(lines)
            out.append('</table>');i=j;continue
        if line.startswith('## '): out.append('<h3>'+html.escape(line[3:])+'</h3>')
        elif line: out.append('<p>'+html.escape(line)+'</p>')
        i+=1
    return '<html><body>'+''.join(out)+'</body></html>'


def saved_context(root, checkpoint_path=None, expected_date=None, source_result=None):
    context={};warnings=[]
    for key in ('quotes','hedge'):
        path=root/(key+'.json')
        if path.exists():
            try:
                value=json.loads(path.read_bytes())
                if expected_date and (value.get('market_date')!=expected_date or
                        (key=='quotes' and any(row.get('trade_date')!=expected_date for row in value.get('rows',[])))):
                    warnings.append(key+'展示证据错日，仅该来源项显示N/A')
                else: context[key]=value
            except (ValueError,OSError): warnings.append(key+'保存证据无法读取，仅该项显示N/A')
    path=root/'checkpoint.json'
    if not path.exists() and checkpoint_path is not None: path=checkpoint_path
    if path.exists():
        try:
            cp=json.loads(path.read_bytes());loaded={}
            if expected_date and cp.get('as_of_date','')>expected_date: raise ValueError('future context')
            for key in ('period','history'):
                spec=cp[key];source=(path.parent/spec['path']).resolve()
                if not source.is_relative_to(path.parent.resolve()): raise ValueError('context path escape')
                raw=source.read_bytes()
                if hashlib.sha256(raw).hexdigest()!=spec['sha256']:raise ValueError('context hash differs')
                loaded[key]=json.loads(raw) if key=='period' else list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
            context.update(checkpoint=cp,**loaded)
            # Turnover is optional presentation context. Its independent bound
            # bytes are mandatory only when deriving a current member action.
            try:
                spec=cp['turnover'];source=(path.parent/spec['path']).resolve()
                if not source.is_relative_to(path.parent.resolve()):raise ValueError('turnover path escape')
                raw=source.read_bytes()
                if hashlib.sha256(raw).hexdigest()!=spec['sha256']:raise ValueError('turnover hash differs')
                context['turnover']=list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
                context['member_contract_bound']=True
            except (ValueError,OSError,KeyError,TypeError):
                warnings.append('名单成本事件证据缺失或哈希不符，不据此生成本次成员调仓动作')
        except (ValueError,OSError,KeyError,TypeError): warnings.append('名单／历史展示证据缺失或哈希不符，仅相应背景字段显示N/A')
    if (source_result or {}).get('reused_close_checkpoint') is True:
        try:
            context['saved_close_proof']=checkpoint_proof(checkpoint_path or root/'checkpoint.json')
        except (ValueError,OSError,KeyError,TypeError):
            warnings.append('原始收盘检查点完整证明缺失，不将保存行补写为今日行情')
    context['presentation_warnings']=warnings
    return context


def render(result: dict, *, expected_date: str, context=None) -> dict:
    by_version=versions_for_date(result,expected_date,REVISIONS)
    validate_publication_clock(result,expected_date,now_bj())
    context=publication_context(result,context,expected_date,REVISIONS)
    saved=saved_close_identity(result,context,expected_date,REVISIONS)
    rows, verified = [], 0
    for version, revision in REVISIONS.items():
        item = by_version[version]
        if item.get("revision") != revision or item.get("status") not in {"verified", "partial", "reference"}:
            raise ValueError("Member daily revision/status mismatch")
        signal = item.get("signal")
        if item['status'] in {'verified','reference'}:
            validate_signal_identity(signal,version,revision,expected_date,result['publication_mode'],saved_close=saved)
        if item["status"] == "verified":
            if not isinstance(signal, dict):
                raise ValueError("Verified member daily row has no signal")
            required = ("current_holding", "next_holding")
            if any(signal.get(key) in (None, "") for key in required):
                raise ValueError("Verified signal has missing holding identity")
            verified += 1
            scale = signal.get("next_session_target_scale", "N/A")
            text = f"{holding(signal['current_holding'])} → {holding(signal['next_holding'])}；目标尺度 {scale}"
        elif item["status"] == "reference":
            text = "旧篮子观察，新一期名单未交接；下一正式目标未确认。"
            if isinstance(signal, dict):
                text += f" 观察结果：{signal.get('current_holding', 'N/A')} → {signal.get('next_holding', 'N/A')}"
        else:
            if not isinstance(item.get("reason"), str) or not item["reason"].strip():
                raise ValueError("Partial member row must explain its missing information")
            text = "未确认：" + item["reason"]
        rows.append({"version": version, "revision": revision, "status": item["status"], "text": text})
    status = "OK" if verified == 3 else "PARTIAL"
    timing = "收盘确认" if result["publication_mode"] == "close_confirmed" else "盘中预估，待收盘确认"
    body = f"微盘股每日研究报告｜{expected_date}｜{timing}\n\n"
    body += "\n".join(f"{row['version']} [{row['revision']}] {row['text']}" for row in rows)
    body += ("\n\n成员行情覆盖：" + str(result.get('quote_coverage', '未提供'))
             + "；历史计算锚点：" + str(result.get('anchor_date', '未提供'))
             + "；本次结果生成：" + str(result.get('generated_at', '未提供')) + "。")
    missing = result.get('missing_member_symbols')
    if isinstance(missing, list) and missing:
        body += "\n行情缺项：" + "、".join(map(str, missing)) + "。"
    warnings = result.get("warnings", [])
    if not isinstance(warnings, list) or not all(isinstance(x, str) for x in warnings):
        raise ValueError("Warnings must be explicit strings")
    if warnings:
        body += "\n\n资料与数据提示：\n" + "\n".join(warnings)
    maintenance = result.get("maintenance")
    maintenance_date = maintenance.get("last_updated") if isinstance(maintenance, dict) else None
    body += "\n\n资料维护最后更新：" + str(maintenance_date or "未核验，未绑定本次维护回执")
    body += "。本期名单的历史核验不代表今日公告/ST资料已经完成核验。\n"
    body += "研究目标与模型记录，不代表账户成交。\n"
    member_actions={}
    for version,item in by_version.items():
        if item['status']=='verified':
            action=member_action(result,version,item['signal'],context or {})
            if action:member_actions[version]=action
    body += details(result,by_version,context or {},member_actions)
    label = "微盘股资料报告（未形成当日完整信号）" if verified == 0 else "微盘股日报"
    return {"schema": SCHEMA, "status": status, "signal_date": expected_date,
            "publication_mode": "realtime" if result["publication_mode"] == "intraday" else "close_confirmed",
            "subject": f"[{timing}]{'[部分信息]' if status == 'PARTIAL' else ''}{label} {expected_date}",
            "body": body, "html_body": html_document(body),
            "versions": rows, "warnings": warnings, "member_actions": member_actions}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--expected-date", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument('--context-root',type=Path)
    parser.add_argument('--context-checkpoint',type=Path)
    args = parser.parse_args()
    raw = args.result.read_bytes()
    result=json.loads(raw)
    context=saved_context(args.context_root or args.result.parent,args.context_checkpoint,args.expected_date,result)
    context=publication_context(result,context,args.expected_date,REVISIONS)
    signal_files=final_signal_files(result,args.context_root or args.result.parent,args.expected_date,REVISIONS,context=context)
    if context['presentation_warnings']:
        result=dict(result,warnings=list(result.get('warnings',[]))+context['presentation_warnings'])
    metadata = render(result, expected_date=args.expected_date,context=context)
    metadata["result_sha256"] = hashlib.sha256(raw).hexdigest()
    metadata['rendered_at']=now_bj().isoformat()
    metadata['final_signal_sha256']={version:hashlib.sha256(value).hexdigest() for version,value in signal_files.items()}
    if result.get('reused_close_checkpoint') is True:
        metadata['saved_checkpoint_sha256']=context['saved_close_proof']['sha256']
    args.out.mkdir(parents=True, exist_ok=True)
    if result.get('reused_close_checkpoint') is True:
        # 477 reserializes its exported checkpoint. Retain the exact original
        # hash-bound source for a later reviewed send-only restore, separately
        # from the untouched saved result/CSV and the email attachment.
        source_root=(args.out/'source-checkpoint').resolve()
        for relative,value in context['saved_close_proof']['files'].items():
            target=(source_root/relative).resolve()
            if not target.is_relative_to(source_root):raise ValueError('Saved source output path escape')
            target.parent.mkdir(parents=True,exist_ok=True)
            if target.exists() and target.read_bytes()!=value:raise ValueError('Existing saved source checkpoint differs')
            target.write_bytes(value)
        metadata['saved_checkpoint_source']='source-checkpoint/checkpoint.json'
    # Small saved-input attachment; no source fetching or model calculation.
    attachment=args.out/'member-daily-details.zip'
    with zipfile.ZipFile(attachment,'w',zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('result.json',raw)
        for field in ('effective_members','target_members'):
            members=context.get('period',{}).get(field,[])
            if members:
                names=sorted({k for row in members for k in row});buf=io.StringIO(newline='')
                writer=csv.DictWriter(buf,fieldnames=names);writer.writeheader();writer.writerows(members)
                archive.writestr(field+'.csv',buf.getvalue().encode('utf-8-sig'))
        for version,value in signal_files.items():
            archive.writestr(version.replace('.','_')+'_signal.csv',value)
        for key in ('quotes','hedge'):
            if key in context:
                path=(args.context_root or args.result.parent)/(key+'.json')
                if path.is_file():archive.write(path,path.name)
        prices=context.get('quotes',{}).get('rows',[])
        if prices:
            buf=io.StringIO(newline='');writer=csv.DictWriter(buf,fieldnames=sorted({k for row in prices for k in row}))
            writer.writeheader();writer.writerows(prices)
            archive.writestr('member_quotes.csv',buf.getvalue().encode('utf-8-sig'))
    metadata['attachment']=str(attachment.resolve())
    metadata['body']+='\n附件：当次结果、已保存的三版CSV、本期名单及成员行情明细。\n'
    metadata['html_body']=html_document(metadata['body'])
    (args.out / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.out / "report.md").write_text(metadata["body"], encoding="utf-8")
    (args.out / "report.html").write_text(metadata["html_body"], encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
