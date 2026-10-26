import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import etf_archived_source as archived


@pytest.mark.parametrize("name", ["baseline", "fresh", "checkpoint"])
def test_pinned_archive_has_historical_proof_without_network(name):
    with patch.dict(os.environ, {"GITHUB_RUN_ID": "99999999999"}), patch("urllib.request.urlopen", side_effect=AssertionError("network forbidden")):
        z, meta = archived.verified_archive(name)
        assert z.namelist()
        assert meta["artifact"]["digest"] == "sha256:" + archived.PINS[name][1]


def test_changed_archive_refused_before_extraction(tmp_path):
    (tmp_path / "fresh.zip.b64").write_text("e30=", "ascii")
    with pytest.raises(ValueError, match="bytes changed"):
        archived.verified_archive("fresh", tmp_path)


def test_historical_replay_has_no_delivery_authority_and_never_overwrites(tmp_path):
    out = tmp_path / "frozen"
    result = archived.materialize("fresh", out)
    assert result["current_live_status"] == "not_checked"
    assert result["delivery_eligible"] is result["sent"] is False
    assert (out / "metadata.json").exists()
    with pytest.raises(ValueError, match="must be new"):
        archived.materialize("fresh", out)
