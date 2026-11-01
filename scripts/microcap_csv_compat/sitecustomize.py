"""Preserve the approved Windows CSV serialization in Linux delivery processes."""
import os

# Loaded before pandas in every inherited Python subprocess. No source/data
# validation is changed: the approved candidate bytes must still hash exactly.
if os.environ.get("MICROCAP_CSV_LINE_TERMINATOR") == "crlf":
    os.linesep = "\r\n"

if os.environ.get("MICROCAP_REVIEWED_NON_ST_BOUNDARY") == "20261030-exact-v1":
    import notice_boundary_adapter
    notice_boundary_adapter.install()
