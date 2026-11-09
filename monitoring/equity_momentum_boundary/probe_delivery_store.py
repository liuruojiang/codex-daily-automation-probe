"""Explicit no-mail validation of the isolated persistent transaction namespace."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import uuid

from weekly_delivery import GitJournal, REPOSITORY, digest


def probe(environment=None, journal=None):
    environment = os.environ if environment is None else environment
    if (environment.get("GITHUB_REPOSITORY") != REPOSITORY
            or environment.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
            or not all(str(environment.get(k, "")).isdigit() for k in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"))):
        raise ValueError("Storage probe requires an explicit dispatch in the trusted repository")
    identity = {"namespace": "validation-only-no-mail", "repository": REPOSITORY,
                "run_id": environment["GITHUB_RUN_ID"], "run_attempt": environment["GITHUB_RUN_ATTEMPT"]}
    key = digest(identity)
    journal = journal or GitJournal()
    existing = journal.read(key)
    if existing:
        if existing[1].get("phase") == "validation_complete" and existing[1].get("identity") == identity:
            return {"status": "validation_complete", "key": key, "actual_emails_sent": 0}
        raise RuntimeError("An earlier probe is incomplete; retain its evidence")
    state = {"schema_version": 1, "key": key, "identity": identity, "phase": "validation_claimed",
             "owner": uuid.uuid4().hex, "created_at": datetime.now(timezone.utc).isoformat()}
    sha = journal.claim(key, state)
    journal.finish(key, sha, {**state, "phase": "validation_complete", "actual_emails_sent": 0})
    return {"status": "validation_complete", "key": key, "actual_emails_sent": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = probe()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
