import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from daily_reports import markdown_to_email_html
from etf_html_integrity import validate_html_alternative

BODY = "# 日报\n\nQQQ 收盘 500.12，涨幅 1.25%。\n\n- 链接：https://example.com/paper\n\n来源仅为摘要，未核实全文。\n\n发送前缺漏检查：FAILED\n"


def test_real_render_and_cosmetic_attributes_pass():
    page = markdown_to_email_html(BODY, "arbitrary preview")
    assert validate_html_alternative(BODY, page) == []
    assert validate_html_alternative(BODY, page.replace('class="report-p"', 'class="report-p cosmetic"')) == []


def test_all_links_and_warning_preserved_but_prose_replaced_fails():
    poison = '<a href="https://example.com/paper">https://example.com/paper</a><p>发送前缺漏检查：FAILED</p><p>保证盈利。</p>'
    assert validate_html_alternative(BODY, poison)


def test_market_value_changed_without_any_url_change_fails():
    page = markdown_to_email_html(BODY, "")
    assert validate_html_alternative(BODY, page.replace("500.12", "999.99"))


def test_report_content_hidden_or_extra_claim_injected_fails():
    page = markdown_to_email_html(BODY, "")
    assert validate_html_alternative(BODY, page.replace('<p class="report-p">', '<p style="display:none">', 1))
    assert validate_html_alternative(BODY, page.replace("</main>", "</main><p>收益已保证。</p>"))


def test_active_content_fails_even_with_matching_prose():
    page = markdown_to_email_html(BODY, "")
    assert validate_html_alternative(BODY, page.replace("</body>", "<script>alert(1)</script></body>"))


def test_css_generated_claim_and_overlay_and_changed_link_targets_fail():
    page = markdown_to_email_html(BODY, "")
    overlay = '<style>main::before{content:"GUARANTEED 999% RETURNS";position:fixed;inset:0;background:white;z-index:99999}</style>'
    assert validate_html_alternative(BODY, page.replace("</head>", overlay + "</head>"))
    assert validate_html_alternative(BODY, page.replace('<main class="email-main">', '<main class="email-main" style="position:fixed;inset:0">'))
    assert validate_html_alternative(BODY, page.replace('href="https://example.com/paper"', 'href="https://evil.example/"'))


def test_two_real_frozen_emails_pass_but_all_links_preserving_poison_fails():
    import html
    from etf_archived_source import verified_archive
    from validate_etf_delivery import validate, LinkParser
    for name in ("baseline", "fresh"):
        archive, _ = verified_archive(name)
        metadata, manifest, history = [json.loads(archive.read(n)) for n in
            ("metadata.json", "collection_manifest.json", "history_before.json")]
        assert validate(metadata, manifest, history, require_candidate_audit=True) == []
        parser = LinkParser()
        parser.feed(metadata["html_body"])
        metadata["html_body"] = '<p>保证盈利。发送前缺漏检查：FAILED</p>' + "".join(
            '<a href="' + html.escape(url, quote=True) + '">来源</a>' for url in sorted(parser.links))
        assert any("visible report content" in e for e in validate(metadata, manifest, history, require_candidate_audit=True))
