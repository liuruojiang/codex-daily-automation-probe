"""Offline summary selection and versioned research records; never sends or fetches.

One atomic JSON transaction holds the mapping, topic history and idempotency
receipt together. OS advisory locks are released automatically after a crash.
Request provenance is recorded, not independently authenticated by this CLI.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import copy
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit

from etf_preflight import canonical

VERSION = "etf-research-workspace-v1"
STATES = {"once", "wait", "tracked"}


class AmbiguousSelection(ValueError):
    pass


class WorkspaceBusy(ValueError):
    pass


def require(ok, message):
    if not ok:raise ValueError(message)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def label(value):
    require(isinstance(value, str) and bool(value.strip()) and not any(ord(c) < 32 for c in value), "expected nonempty single-line text")
    return value.strip()


def text(value):
    require(isinstance(value, str) and bool(value.strip()), "expected nonempty text")
    return value


def source_url(value):
    value=label(value);parts=urlsplit(value)
    require(parts.scheme in ("http", "https") and parts.hostname and not parts.username and not parts.password
            and not any(c.isspace() for c in value), "invalid source URL")
    return canonical(value)


def request_record(value, intent=None):
    require(isinstance(value, dict) and set(value) == {"id", "text", "source", "intent"}, "invalid user request record")
    require(value["source"] == "user" and (intent is None or value["intent"] == intent), "explicit user intent required")
    return {"id": label(value["id"]), "text": text(value["text"]), "source": "user", "intent": label(value["intent"])}


def empty_state():
    return {"version": VERSION, "revision": 0, "mails": {}, "summaries": {}, "topics": {}, "operations": {}}


def validate_state(state):
    require(isinstance(state, dict) and set(state) == {"version", "revision", "mails", "summaries", "topics", "operations"}
            and state["version"] == VERSION and type(state["revision"]) is int and state["revision"] >= 0,
            "invalid workspace envelope")
    require(all(isinstance(state[k], dict) for k in ("mails", "summaries", "topics", "operations")), "invalid workspace records")
    for mid, mail in state["mails"].items():
        require(mid == mail["mail_id"] and mail["fingerprint"] == digest({k:v for k,v in mail.items() if k != "fingerprint"}),
                "authoritative mail mapping changed")
    for sid, row in state["summaries"].items():
        payload=row["payload"]
        require(sid == summary_id(payload) and row["fingerprint"] == digest(payload), "summary snapshot changed")
        require(row["items"] == bound_summary_items(state, payload), "summary selection mapping changed")
    for tid, topic in state["topics"].items():
        require(topic["topic_id"] == tid and topic["state"] in STATES and topic["state_events"]
                and topic["state_events"][-1]["state"] == topic["state"], "invalid topic identity/state")
        anchor=topic["anchor"]
        require(anchor == resolve(state,{"summary_id":anchor["summary_id"],"item_id":anchor["item_id"]})
                and tid == "t_"+digest({"item_id":anchor["item_id"],"question":topic["question"].strip()})[:24],
                "topic selection identity changed")
        for event in topic["state_events"]:
            require(event["state"] in STATES,"invalid topic state event")
            require(type(event["revision_at_request"]) is int and 0 <= event["revision_at_request"] <= len(topic["revisions"]),
                    "invalid authorization revision boundary")
            request_record(event["request"],{"once":"deep_read" if event is topic["state_events"][0] else "once",
                                             "tracked":"track","wait":"wait"}[event["state"]])
        previous=None
        for number, revision in enumerate(topic["revisions"], 1):
            require(revision["version"] == number and revision["parent_hash"] == previous, "research history chain broken")
            count=revision["authorization_event_count"]
            require(type(count) is int and 1 <= count <= len(topic["state_events"]),"invalid revision authorization")
            authorization=topic["state_events"][count-1]
            require(authorization["request"]["id"]==revision["authorization_request_id"] and authorization["state"]!="wait"
                    and (revision["execution_mode"]!="scheduled" or authorization["state"]=="tracked")
                    and (authorization["state"]!="once" or number==authorization["revision_at_request"]+1),
                    "research revision exceeds recorded authorization")
            own={k:v for k,v in revision.items() if k != "hash"}
            require(revision["hash"] == digest(own), "research revision changed")
            previous=revision["hash"]


def load_state(workspace):
    path=Path(workspace)/"workspace.json"
    state=json.loads(path.read_text(encoding="utf-8")) if path.exists() else empty_state()
    validate_state(state)
    return state


@contextmanager
def locked(workspace):
    root=Path(workspace);root.mkdir(parents=True, exist_ok=True)
    with (root/"workspace.lock").open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:handle.write(b"0");handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:raise WorkspaceBusy("workspace is locked by another writer") from exc
        try:yield root
        finally:
            handle.seek(0)
            if os.name == "nt":msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def atomic_save(root, state):
    validate_state(state)
    target=root/"workspace.json";temporary=None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=root, prefix=".workspace-", suffix=".tmp", delete=False) as handle:
            temporary=Path(handle.name)
            json.dump(state, handle, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
            handle.write("\n");handle.flush();os.fsync(handle.fileno())
        os.replace(temporary, target);temporary=None
        # POSIX directory sync strengthens power-loss durability. Windows does
        # not support opening a directory this way; atomic replace still applies.
        if os.name != "nt":
            fd=os.open(root, os.O_RDONLY)
            try:os.fsync(fd)
            finally:os.close(fd)
    finally:
        if temporary is not None:temporary.unlink(missing_ok=True)


def transact(workspace, operation_id, action, payload, mutation):
    operation_id=label(operation_id);fingerprint=digest({"action": action, "payload": payload})
    with locked(workspace) as root:
        state=load_state(root);prior=state["operations"].get(operation_id)
        if prior is not None:
            require(prior["fingerprint"] == fingerprint, "idempotency key reused for a different request")
            return copy.deepcopy(prior["result"])
        result=mutation(state)
        state["revision"]+=1
        state["operations"][operation_id]={"fingerprint": fingerprint, "action": action, "result": copy.deepcopy(result)}
        atomic_save(root, state)
        return result


def summary_id(payload):
    return "s_"+digest({k:payload[k] for k in ("report_date", "run_id", "version")})[:24]


def mail_mapping(metadata, manifest):
    """Build original identities from numbered body blocks, NEVER array offsets.

    Inputs must come from the upstream verified authoritative run directory.
    This validates internal consistency, not the authenticity of external files.
    """
    require(isinstance(metadata,dict) and isinstance(manifest,dict),"invalid mail artifacts")
    body=text(metadata["body"]);subject=label(metadata["subject"])
    body_hash=hashlib.sha256(body.encode("utf-8")).hexdigest()
    require(body_hash == manifest["body_sha256"],"mail body hash differs from manifest")
    dates=re.findall(r"\d{4}-\d{2}-\d{2}",subject)
    require(len(dates)==1,"mail subject needs one report date")
    report_date=dates[0];require(date.fromisoformat(report_date).isoformat()==report_date,"invalid report date")
    require(body.splitlines()[0].strip()=="# "+subject,"mail body and subject differ")
    run_id=label(manifest["run_id"]);head_sha=label(manifest["head_sha"])
    selected={}
    for item in manifest["selected_items"]:
        url=source_url(item["url"])
        require(url not in selected,"duplicate selected source URL")
        selected[url]=label(item["title"])
    require(selected,"mail has no selected items")
    # H1-H3 and horizontal rules delimit blocks, preventing an article link in
    # the following section or audit appendix from inheriting a prior number.
    boundaries=list(re.finditer(r"(?m)^(?:#{1,3} .+|---\s*)$",body))
    section=None;rows=[];seen=set();slots=set()
    for index, match in enumerate(boundaries):
        heading=match.group().strip()
        if heading.startswith("## "):section=heading[3:].strip();continue
        numbered=re.fullmatch(r"### ([1-9]\d*)\.\s*(.+)",heading)
        if not numbered:continue
        end=boundaries[index+1].start() if index+1<len(boundaries) else len(body)
        block=body[match.end():end]
        urls=[source_url(v) for v in re.findall(r"(?m)^- (?:链接|原文链接)：\s*(https?://\S+)\s*$",block)]
        matches=[u for u in urls if u in selected]
        if not matches:continue
        require(section is not None and len(urls)==1 and len(matches)==1,"ambiguous formal article block")
        url=matches[0];number=numbered[1]
        require(url not in seen and (section,number) not in slots,"duplicate formal article identity")
        seen.add(url);slots.add((section,number));global_number=len(rows)+1
        identity={"report_date":report_date,"run_id":run_id,"section":section,"number":number,"url":url}
        rows.append({"section":section,"number":number,"title":selected[url],"url":url,
                     "original_heading":numbered[2],"global_number":global_number,
                     "global_id":f"E{report_date.replace('-','')}-{run_id}-{global_number:03d}",
                     "item_id":"i_"+digest(identity)[:24]})
    require(seen==set(selected),"selected sources missing unique numbered body blocks")
    mail={"mail_id":"m_"+digest({"report_date":report_date,"run_id":run_id,"head_sha":head_sha,"body_sha256":body_hash})[:24],
          "report_date":report_date,"run_id":run_id,"head_sha":head_sha,"body_sha256":body_hash,"items":rows}
    mail["fingerprint"]=digest(mail)
    return mail


def register_mail(workspace, metadata, manifest, operation_id):
    mapping=mail_mapping(metadata,manifest)
    def mutate(state):
        for existing in state["mails"].values():
            if existing["run_id"]==mapping["run_id"]:
                require(existing==mapping,"authoritative run is immutable; conflicting body mapping")
        state["mails"][mapping["mail_id"]]=mapping
        return copy.deepcopy(mapping)
    return transact(workspace,operation_id,"register-mail",mapping,mutate)


def bound_summary_items(state,payload):
    mail=state["mails"].get(payload["mail_id"])
    require(mail is not None,"register authoritative mail before importing summary")
    require(all(payload[k]==mail[k] for k in ("report_date","run_id")),"summary date/run differs from authoritative mail")
    lookup={(row["section"],row["number"],row["url"]):row for row in mail["items"]}
    result=[]
    for row in summary_items(payload):
        original=lookup.get(tuple(row[k] for k in ("section","number","url")))
        require(original is not None and original["title"]==row["title"],"summary article does not match authoritative body section/number/URL/title")
        require(original["item_id"]==row["item_id"],"summary article identity differs")
        result.append({**row,**{k:original[k] for k in ("global_id","global_number","original_heading")}})
    return result


def normalize_summary(payload):
    require(isinstance(payload, dict) and set(payload) == {"mail_id", "report_date", "run_id", "version", "text", "items"}, "invalid summary input")
    report_date=label(payload["report_date"]);require(date.fromisoformat(report_date).isoformat() == report_date, "invalid report date")
    require(isinstance(payload["items"], list) and payload["items"], "summary needs visible items")
    items=[]
    for item in payload["items"]:
        require(isinstance(item, dict) and set(item) == {"section", "number", "title", "url"}, "invalid summary item")
        items.append({"section": label(item["section"]), "number": label(item["number"]),
                      "title": label(item["title"]), "url": source_url(item["url"])})
    result={"mail_id":label(payload["mail_id"]),"report_date": report_date, "run_id": label(payload["run_id"]), "version": label(payload["version"]),
            "text": text(payload["text"]), "items": items}
    rows=summary_items(result)
    require(len({row["item_id"] for row in rows}) == len(rows), "duplicate original article identity")
    require(len({(row["section"], row["number"]) for row in rows}) == len(rows), "ambiguous original section/number")
    return result


def summary_items(payload):
    rows=[]
    for display_number, item in enumerate(payload["items"], 1):
        identity={"report_date":payload["report_date"], "run_id":payload["run_id"], **{k:item[k] for k in ("section", "number", "url")}}
        rows.append({**copy.deepcopy(item), "display_number":display_number, "item_id":"i_"+digest(identity)[:24]})
    return rows


def import_summary(workspace, payload, operation_id):
    normalized=normalize_summary(payload);sid=summary_id(normalized)
    def mutate(state):
        record={"summary_id":sid,"payload":normalized,"fingerprint":digest(normalized),"items":bound_summary_items(state,normalized)}
        if sid in state["summaries"]:
            require(state["summaries"][sid] == record, "summary version is immutable; import a new version")
        else:state["summaries"][sid]=record
        return copy.deepcopy(record)
    return transact(workspace,operation_id,"import-summary",normalized,mutate)


def resolve(state, selector):
    fields={"summary_id", "report_date", "run_id", "version", "display_number", "item_id", "global_id", "section", "number", "url"}
    require(isinstance(selector,dict) and selector and set(selector) <= fields, "invalid selection fields")
    require(any(k in selector for k in ("display_number", "item_id", "global_id", "url", "number")), "selection needs an article selector")
    if "display_number" in selector:require(type(selector["display_number"]) is int and selector["display_number"] > 0,"invalid visible number")
    query=copy.deepcopy(selector)
    if "url" in query:query["url"]=source_url(query["url"])
    matches=[]
    for sid,summary in state["summaries"].items():
        base={"summary_id":sid,**{k:summary["payload"][k] for k in ("report_date","run_id","version")}}
        for item in summary["items"]:
            row={**base,**item}
            if all(row[k] == v for k,v in query.items()):matches.append(row)
    require(matches,"selection not found")
    if len(matches) != 1:raise AmbiguousSelection("ambiguous selection: include the report date and exact summary version/id")
    return copy.deepcopy(matches[0])


def create_topic(workspace,payload,operation_id):
    require(isinstance(payload,dict) and set(payload)=={"selection","question","request"},"invalid topic creation input")
    question=text(payload["question"]);request=request_record(payload["request"],"deep_read")
    def mutate(state):
        anchor=resolve(state,payload["selection"])
        tid="t_"+digest({"item_id":anchor["item_id"],"question":question.strip()})[:24]
        require(tid not in state["topics"],"topic already exists; use its stable ID")
        topic={"topic_id":tid,"question":question,"anchor":anchor,"state":"once",
               "state_events":[{"state":"once","request":request,"revision_at_request":0}],"revisions":[]}
        state["topics"][tid]=topic
        return copy.deepcopy(topic)
    return transact(workspace,operation_id,"create-topic",payload,mutate)


def set_status(workspace,payload,operation_id):
    require(isinstance(payload,dict) and set(payload)=={"topic_id","expected_state","expected_event_count","state","request"},"invalid state transition")
    require(payload["state"] in STATES,"invalid topic state")
    request=request_record(payload["request"],{"tracked":"track","wait":"wait","once":"once"}[payload["state"]])
    def mutate(state):
        topic=state["topics"].get(payload["topic_id"]);require(topic is not None,"topic not found")
        require(topic["state"] == payload["expected_state"],"topic state changed; reread before updating")
        require(type(payload["expected_event_count"]) is int and len(topic["state_events"])==payload["expected_event_count"],
                "authorization events changed; reread before updating")
        require(request["id"] not in {event["request"]["id"] for event in topic["state_events"]},"new authorization requires a new user request ID")
        topic["state"]=payload["state"];topic["state_events"].append({"state":payload["state"],"request":request,
                                                                   "revision_at_request":len(topic["revisions"])})
        return {"topic_id":topic["topic_id"],"state":topic["state"],"state_event_count":len(topic["state_events"])}
    return transact(workspace,operation_id,"set-status",payload,mutate)


def evidence_record(value):
    fields={"source_url","source_version","capture_sha256","capture_text","scope","family","stance","note_zh"}
    require(isinstance(value,dict) and set(value)==fields,"invalid evidence record")
    require(value["scope"] in {"abstract","excerpt","fulltext","metadata"} and value["stance"] in {"support","oppose","uncertain"},"invalid evidence scope/stance")
    capture=value["capture_sha256"]
    require(isinstance(capture,str) and len(capture)==64 and all(c in "0123456789abcdef" for c in capture),"invalid capture hash")
    require(hashlib.sha256(text(value["capture_text"]).encode("utf-8")).hexdigest()==capture,
            "capture hash differs from retained evidence text")
    record={**value,"source_url":source_url(value["source_url"]),"source_version":label(value["source_version"]),
            "family":label(value["family"]),"note_zh":text(value["note_zh"])}
    identity={k:record[k] for k in ("source_url","source_version","capture_sha256","scope","family")}
    return {"evidence_id":"e_"+digest(identity)[:24],**record}


def record_revision(workspace,payload,operation_id):
    fields={"topic_id","expected_version","expected_event_count","execution_mode","judgment_zh","change_zh","open_questions","evidence","retired_evidence"}
    require(isinstance(payload,dict) and set(payload)==fields,"invalid research revision input")
    require(type(payload["expected_version"]) is int and payload["expected_version"]>=0,"invalid expected revision")
    require(payload["execution_mode"] in {"interactive","scheduled"},"explicit execution mode required")
    text(payload["judgment_zh"]);text(payload["change_zh"])
    require(isinstance(payload["open_questions"],list) and all(isinstance(v,str) and v.strip() for v in payload["open_questions"]),"invalid open questions")
    require(isinstance(payload["evidence"],list) and payload["evidence"],"research revision needs evidence")
    evidence=[evidence_record(value) for value in payload["evidence"]]
    require(len({e["evidence_id"] for e in evidence})==len(evidence),"duplicate evidence snapshot")
    retired=payload["retired_evidence"]
    require(isinstance(retired,dict) and all(isinstance(k,str) and isinstance(v,str) and v.strip() for k,v in retired.items()),"retired evidence needs explicit reasons")
    def mutate(state):
        topic=state["topics"].get(payload["topic_id"]);require(topic is not None,"topic not found")
        require(topic["state"] != "wait","topic is waiting/paused")
        require(payload["execution_mode"] != "scheduled" or topic["state"]=="tracked","scheduled research requires explicit tracking authorization")
        revisions=topic["revisions"];require(len(revisions)==payload["expected_version"],"research version changed; reread before updating")
        require(type(payload["expected_event_count"]) is int and len(topic["state_events"])==payload["expected_event_count"],
                "authorization events changed; reread before updating")
        require(topic["state"] != "once" or len(revisions)==topic["state_events"][-1]["revision_at_request"],
                "one-off authorization already completed; a new explicit user request is required")
        previous=revisions[-1] if revisions else None
        old_ids={e["evidence_id"] for e in previous["evidence"]} if previous else set()
        new_ids={e["evidence_id"] for e in evidence}
        require(set(retired)==old_ids-new_ids,"removed evidence must have explicit disposition; no invented retirements")
        revision={"version":len(revisions)+1,"parent_hash":previous["hash"] if previous else None,
                  "recorded_at":datetime.now(timezone.utc).isoformat(),"execution_mode":payload["execution_mode"],
                  "authorization_event_count":len(topic["state_events"]),"authorization_request_id":topic["state_events"][-1]["request"]["id"],
                  "judgment_zh":payload["judgment_zh"],"change_zh":payload["change_zh"],"open_questions":payload["open_questions"],
                  "evidence":evidence,"retired_evidence":retired,"evidence_family_count":len({e["family"] for e in evidence})}
        revision["hash"]=digest(revision);revisions.append(revision)
        return copy.deepcopy(revision)
    return transact(workspace,operation_id,"record-revision",payload,mutate)


def pending(state):
    """Mail-run deduplication must not suppress these independent topic queues."""
    return {kind:[tid for tid,topic in state["topics"].items() if topic["state"]==kind and
                  (kind=="tracked" or len(topic["revisions"])==topic["state_events"][-1]["revision_at_request"])]
            for kind in ("once","tracked")}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command",choices=("map-mail","register-mail","import-summary","resolve","create-topic","set-status","record-revision","pending","show"))
    parser.add_argument("--workspace",type=Path)
    parser.add_argument("--source-dir",type=Path)
    parser.add_argument("--input",type=Path)
    parser.add_argument("--operation-id")
    args=parser.parse_args(argv)
    require(args.command=="map-mail" or args.workspace is not None,"--workspace is required")
    if args.command in {"map-mail","register-mail"}:
        require(args.source_dir is not None,"--source-dir is required")
        metadata=json.loads((args.source_dir/"metadata.json").read_text(encoding="utf-8"))
        manifest=json.loads((args.source_dir/"collection_manifest.json").read_text(encoding="utf-8"))
        if args.command=="map-mail":result=mail_mapping(metadata,manifest)
        else:
            require(args.operation_id is not None,"mutations require --operation-id")
            result=register_mail(args.workspace,metadata,manifest,args.operation_id)
    elif args.command=="show":result=load_state(args.workspace)
    elif args.command=="pending":result=pending(load_state(args.workspace))
    else:
        require(args.input is not None,"--input JSON is required")
        payload=json.loads(args.input.read_text(encoding="utf-8"))
        if args.command=="resolve":result=resolve(load_state(args.workspace),payload)
        else:
            require(args.operation_id is not None,"mutations require --operation-id")
            action={"import-summary":import_summary,"create-topic":create_topic,"set-status":set_status,"record-revision":record_revision}[args.command]
            result=action(args.workspace,payload,args.operation_id)
    print(json.dumps(result,ensure_ascii=False,sort_keys=True))
    return 0


if __name__=="__main__":raise SystemExit(main())
