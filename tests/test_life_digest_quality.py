from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import daily_reports as dr  # noqa: E402


class WealthSlowTravelDigestTests(unittest.TestCase):
    def item(self, source: str, title: str, summary: str, url: str = "https://example.com/x") -> dr.Item:
        return dr.Item(source=source, title=title, url=url, published="2026-05-15T12:00:00+00:00", summary=summary)

    def test_combined_life_digest_structure_filters_us_only_noise(self) -> None:
        feed_items = {
            "Morningstar Retirement": [
                self.item(
                    "Morningstar Retirement",
                    "Flexible Withdrawal Rates and Retirement Income",
                    "Morningstar estimates a 3.9% starting safe withdrawal rate and says flexible spending can improve lifetime retirement spending.",
                    "https://www.morningstar.com/retirement",
                )
            ],
            "OECD Tax Residency": [
                self.item(
                    "OECD Tax Residency",
                    "Tax residency rules for CRS jurisdictions",
                    "Tax residence is determined by local domestic law and matters for cross-border reporting and long stays.",
                    "https://www.oecd.org/tax/automatic-exchange/crs-implementation-and-assistance/tax-residency/",
                )
            ],
            "UK FCDO": [
                self.item(
                    "UK FCDO",
                    "Portugal travel advice updated",
                    "Entry requirements, safety and health guidance for travellers were updated.",
                    "https://www.gov.uk/foreign-travel-advice/portugal",
                )
            ],
            "Nomad Capitalist": [
                self.item(
                    "Nomad Capitalist",
                    "Digital nomad visa and residence planning",
                    "Residence planning, second base and tax exposure ideas for globally mobile investors.",
                    "https://nomadcapitalist.com/",
                )
            ],
            "Frequent Miler": [
                self.item(
                    "Frequent Miler",
                    "Transfer bonus for business class awards",
                    "A hotel and airline points transfer bonus may improve premium cabin award value before a deadline.",
                    "https://frequentmiler.com/",
                )
            ],
            "White Coat Investor": [
                self.item(
                    "White Coat Investor",
                    "Medicare and ACA details for US retirees",
                    "A US-only Medicare, ACA and state tax planning article with little cross-border relevance.",
                    "https://www.whitecoatinvestor.com/us-only-medicare/",
                )
            ],
        }
        original_parse_feed = dr.parse_feed
        original_update_history = dr.update_digest_history
        original_report_date = dr.report_date
        original_enrich = dr.enrich_article_item
        original_sleep = dr.time.sleep

        def fake_parse_feed(source: str, url: str, limit: int = 12) -> list[dr.Item]:
            return feed_items.get(source, [])

        try:
            dr.parse_feed = fake_parse_feed
            dr.update_digest_history = lambda *args, **kwargs: None
            dr.report_date = lambda: "2026-05-17"
            dr.enrich_article_item = lambda item: item
            dr.time.sleep = lambda *_args, **_kwargs: None
            with tempfile.TemporaryDirectory() as tmp:
                out_dir = Path(tmp)
                dr.build_life_digest(out_dir)
                report = (out_dir / "wealth_slow_travel_digest_2026-05-17.md").read_text(encoding="utf-8")
        finally:
            dr.parse_feed = original_parse_feed
            dr.update_digest_history = original_update_history
            dr.report_date = original_report_date
            dr.enrich_article_item = original_enrich
            dr.time.sleep = original_sleep

        self.assertIn("宽裕版财务自由 + 环球慢旅生活日报", report)
        self.assertIn("默认读者：生活在香港地区的中国人", report)
        for heading in [
            "今日重大变化",
            "财务自由与退休收入",
            "环球慢旅目的地观察",
            "长住住宿与积分机会",
            "今日可执行事项",
        ]:
            self.assertIn(heading, report)
        for label in ["类型：", "来源等级：", "决策影响：", "是否需要人工核实：", "是否进入候选目的地或待办清单："]:
            self.assertIn(label, report)
        self.assertIn("3.9%", report)
        self.assertIn("税务居民", report)
        self.assertIn("Portugal", report)
        self.assertIn("商务舱", report)
        self.assertNotIn("Medicare and ACA details", report)
        self.assertNotIn("US-only Medicare", report)
        self.assertLessEqual(report.count("### "), 8)

    def test_life_digest_excludes_generic_tourism_incidents_and_english_blocks(self) -> None:
        noisy_items = [
            self.item(
                "Nomadic Matt",
                "Madrid 5-Day Itinerary: A Guide to Culture, Gastronomy, and Local Life",
                "A standard sightseeing itinerary with museums, restaurants and local culture.",
            ),
            self.item(
                "One Mile at a Time",
                "Croatia Airlines Airbus A220 Veers Off Runway At High Speed, Smashes Signs",
                "A dramatic aviation incident with footage and passenger discussion.",
            ),
            self.item(
                "Collaborative Fund",
                "WHOOP",
                "We are in the early innings of a fundamental shift in how we understand the human body and wearable data.",
            ),
            self.item(
                "Frequent Miler",
                "Watch out for easy-to-forget debts!",
                "I have been gradually re-entering the miles and points game and have an embarrassing debt reminder.",
            ),
            self.item(
                "HumbleDollar",
                "The Nerf Gun Incident: Sunk Costs, Suppressing Fire and the Glassblower",
                "A personal forum anecdote with family and hobby discussion but no travel, retirement-income or planning signal.",
            ),
            self.item(
                "HumbleDollar",
                "Take a Look In the Mirror",
                "A broad personal reflection with no actionable slow-travel, tax, visa, healthcare, housing or points content.",
            ),
            self.item(
                "Reddit r/luxurytravel",
                "Must to do in Hong Kong, Tokyo, Malaysia and Singapore",
                "Generic must-do sightseeing request with no hotel, points, visa, healthcare or long-stay detail.",
            ),
            self.item(
                "International Living",
                "Portugal long-stay residence and cost of living update",
                "Portugal remains a candidate for long stays because of residence options, healthcare access, cost of living and flight connectivity.",
            ),
        ]

        picked = dr.pick_life_items(noisy_items, limit=8)
        rendered = "\n".join(dr.life_heading(item) + "\n" + dr.life_summary(item) for item in picked)

        self.assertEqual([item.source for item in picked], ["International Living"])
        self.assertIn("Portugal", rendered)
        self.assertNotIn("Madrid 5-Day Itinerary", rendered)
        self.assertNotIn("Croatia Airlines Airbus", rendered)
        self.assertNotIn("WHOOP", rendered)
        self.assertNotIn("Nerf Gun Incident", rendered)
        self.assertNotIn("Take a Look In the Mirror", rendered)
        self.assertNotIn("Must to do", rendered)
        self.assertNotRegex(rendered, r"[A-Za-z][A-Za-z ,'-]{100,}")

    def test_us_credit_card_reviews_are_kept_as_points_content(self) -> None:
        item = self.item(
            "The Points Guy",
            "Royal ONE Plus Visa Signature credit card review: A premium path to cruise rewards",
            "This credit card review discusses cruise rewards, annual fee, points earning and travel benefits.",
        )

        self.assertTrue(dr.life_relevant(item))
        self.assertEqual(dr.life_item_type(item), "积分")
        self.assertIn("信用卡", dr.life_heading(item))
        self.assertIn("积分", dr.life_summary(item))

    def test_hotel_best_rate_guarantee_is_not_mislabeled_as_credit_card(self) -> None:
        item = self.item(
            "LoyaltyLobby",
            "Reader Email: Kempinski Hotel San Lawrenz Gozo Malta Best Rate Guarantee Issue",
            "The hotel best rate guarantee issue includes page text with unrelated credit card references.",
        )

        self.assertTrue(dr.life_relevant(item))
        self.assertEqual(dr.life_item_type(item), "住宿")
        self.assertIn("酒店最优价格保证", dr.life_heading(item))
        self.assertNotIn("信用卡评测", dr.life_heading(item))

    def test_life_digest_enriches_links_and_outputs_detailed_points_for_up_to_eight_items(self) -> None:
        thin_items = [
            self.item("Retirement Researcher", "Housing Is Not an Afterthought in Retirement", "Retirement housing planning.", "https://example.com/housing"),
            self.item("Portfolio Charts", "Withdrawal Rates and Global Retirement Portfolios", "Withdrawal rate update.", "https://example.com/withdrawal"),
            self.item("Frequent Miler", "Chase Ultimate Rewards offering 55% transfer bonus to Marriott", "Transfer bonus.", "https://example.com/bonus"),
            self.item("One Mile at a Time", "Citi AAdvantage Globe Card American Admirals Club Passes Explained", "Credit card guide.", "https://example.com/card"),
            self.item("LoyaltyLobby", "Kempinski Hotel Best Rate Guarantee Issue", "Hotel guarantee issue.", "https://example.com/brg"),
            self.item("International Living", "Portugal long-stay residence and cost of living update", "Portugal long stay.", "https://example.com/portugal"),
            self.item("Expatica", "Healthcare and long-term housing for expats in Spain", "Spain expat healthcare.", "https://example.com/spain"),
            self.item("Nomad Capitalist", "Digital nomad visa and residence planning", "Visa planning.", "https://example.com/visa"),
            self.item("The Points Guy", "World of Hyatt promotion for long stays", "Hotel points promotion.", "https://example.com/hyatt"),
        ]
        body_by_url = {
            "https://example.com/housing": "Housing choices shape retirement cash flow, tax residency, healthcare access, family stability and whether a household should keep a long-term base while traveling.",
            "https://example.com/withdrawal": "The article compares safe withdrawal rates, perpetual withdrawal rates, sequence risk and flexible spending rules for long retirement horizons.",
            "https://example.com/bonus": "The transfer bonus is 55% to Marriott, has a stated deadline, and only makes sense if award nights beat cash prices after considering Marriott point value.",
            "https://example.com/card": "The credit card guide explains annual fee, Admirals Club passes, earning rates, airline perks, foreign transaction costs and whether the benefits justify keeping the card.",
            "https://example.com/brg": "The hotel best rate guarantee dispute shows why travelers should keep screenshots, cancellation terms, rate rules and written hotel responses before relying on a claim.",
            "https://example.com/portugal": "Portugal is discussed as a long-stay candidate because of residence options, healthcare access, cost of living, flight connectivity and tax residency questions.",
            "https://example.com/spain": "Spain expat planning requires checking public and private healthcare, long-term rentals, local registration, safety, language friction and insurance coverage.",
            "https://example.com/visa": "The residence planning article discusses digital nomad visa requirements, minimum income, tax exposure, health insurance and how long stays can create residency risk.",
            "https://example.com/hyatt": "The hotel promotion affects long stays through elite night credits, points earning, cash rates, blackout dates and whether the property footprint matches the route.",
        }
        original_parse_feed = dr.parse_feed
        original_update_history = dr.update_digest_history
        original_report_date = dr.report_date
        original_enrich = dr.enrich_article_item
        original_sleep = dr.time.sleep

        def fake_parse_feed(source: str, url: str, limit: int = 12) -> list[dr.Item]:
            if source == "Retirement Researcher":
                return thin_items[:2]
            if source == "Frequent Miler":
                return [thin_items[2]]
            if source == "One Mile at a Time":
                return [thin_items[3]]
            if source == "LoyaltyLobby":
                return [thin_items[4]]
            if source == "International Living":
                return [thin_items[5]]
            if source == "Expatica":
                return [thin_items[6]]
            if source == "Nomad Capitalist":
                return [thin_items[7]]
            if source == "The Points Guy":
                return [thin_items[8]]
            return []

        def fake_enrich(item: dr.Item) -> dr.Item:
            return dr.Item(item.source, item.title, item.url, item.published, item.summary + " " + body_by_url[item.url])

        try:
            dr.parse_feed = fake_parse_feed
            dr.update_digest_history = lambda *args, **kwargs: None
            dr.report_date = lambda: "2026-05-17"
            dr.enrich_article_item = fake_enrich
            dr.time.sleep = lambda *_args, **_kwargs: None
            with tempfile.TemporaryDirectory() as tmp:
                out_dir = Path(tmp)
                dr.build_life_digest(out_dir)
                report = (out_dir / "wealth_slow_travel_digest_2026-05-17.md").read_text(encoding="utf-8")
        finally:
            dr.parse_feed = original_parse_feed
            dr.update_digest_history = original_update_history
            dr.report_date = original_report_date
            dr.enrich_article_item = original_enrich
            dr.time.sleep = original_sleep

        self.assertGreaterEqual(report.count("### "), 8)
        self.assertIn("**内容要点**：", report)
        self.assertGreaterEqual(report.count("- "), 24)
        self.assertIn("55%", report)
        self.assertIn("转点或兑换 bonus", report)
        self.assertIn("年费", report)
        self.assertIn("医疗可达性", report)
        self.assertNotIn("当前自动摘要没有提取到足够结构化细节", report)

    def test_life_digest_supplements_thin_days_with_reddit_luxury_travel_posts(self) -> None:
        primary = self.item(
            "HumbleDollar",
            "Retirement spending and lifestyle after financial independence",
            "Retirement spending planning and lifestyle tradeoffs after financial independence.",
            "https://example.com/retirement-spending",
        )
        fallback_posts = {
            "Reddit r/FATTravel": [
                self.item(
                    "Reddit r/FATTravel",
                    "Maldives vs Bora Bora for a family trip using points",
                    "A short RSS teaser about a luxury family resort decision.",
                    "https://www.reddit.com/r/FATTravel/comments/abc/maldives_vs_bora_bora/",
                )
            ],
            "Reddit r/chubbytravel": [
                self.item(
                    "Reddit r/chubbytravel",
                    "Tokyo and Kyoto hotels with breakfast, suite upgrade and late checkout",
                    "A short RSS teaser about Japan hotel choices.",
                    "https://www.reddit.com/r/chubbytravel/comments/def/tokyo_kyoto_hotels/",
                )
            ],
            "Reddit r/luxurytravel": [
                self.item(
                    "Reddit r/luxurytravel",
                    "Is Amex FHR or Virtuoso better for a safari lodge booking?",
                    "A short RSS teaser about booking-channel benefits.",
                    "https://www.reddit.com/r/luxurytravel/comments/ghi/fhr_virtuoso_safari/",
                )
            ],
        }
        body_by_url = {
            primary.url: "The article discusses retirement spending, lifestyle tradeoffs, portfolio buffers and how a high-net-worth household can spend more intentionally after financial independence.",
            "https://www.reddit.com/r/FATTravel/comments/abc/maldives_vs_bora_bora/": "The post compares Maldives and Bora Bora for parents traveling with children. Commenters discuss overwater villas, family-friendly resorts, seaplane transfer fatigue, breakfast inclusion, cancellation rules, weather season, cash rates above $1,500 per night, and whether points at 120,000 per night are still worth using.",
            "https://www.reddit.com/r/chubbytravel/comments/def/tokyo_kyoto_hotels/": "The thread compares Tokyo and Kyoto hotels for a slow trip. Details include Hyatt and Marriott award availability, 35,000 to 60,000 points per night, breakfast, suite upgrade odds, late checkout, cash rates, train access, laundry, quiet rooms and whether elderly parents can handle frequent hotel changes.",
            "https://www.reddit.com/r/luxurytravel/comments/ghi/fhr_virtuoso_safari/": "The discussion asks whether Amex FHR or Virtuoso is better for a safari lodge booking. Replies compare resort credits, breakfast, airport transfer, cancellation policy, refundable rates, travel insurance, medical evacuation coverage, and whether the higher cash rate is justified by benefits.",
        }
        original_parse_feed = dr.parse_feed
        original_update_history = dr.update_digest_history
        original_report_date = dr.report_date
        original_enrich = dr.enrich_article_item
        original_sleep = dr.time.sleep

        def fake_parse_feed(source: str, url: str, limit: int = 12) -> list[dr.Item]:
            if source == "HumbleDollar":
                return [primary]
            return fallback_posts.get(source, [])

        def fake_enrich(item: dr.Item) -> dr.Item:
            return dr.Item(item.source, item.title, item.url, item.published, item.summary + " " + body_by_url[item.url])

        try:
            dr.parse_feed = fake_parse_feed
            dr.update_digest_history = lambda *args, **kwargs: None
            dr.report_date = lambda: "2026-05-17"
            dr.enrich_article_item = fake_enrich
            dr.time.sleep = lambda *_args, **_kwargs: None
            with tempfile.TemporaryDirectory() as tmp:
                out_dir = Path(tmp)
                dr.build_life_digest(out_dir)
                report = (out_dir / "wealth_slow_travel_digest_2026-05-17.md").read_text(encoding="utf-8")
        finally:
            dr.parse_feed = original_parse_feed
            dr.update_digest_history = original_update_history
            dr.report_date = original_report_date
            dr.enrich_article_item = original_enrich
            dr.time.sleep = original_sleep

        self.assertIn("Reddit r/FATTravel", report)
        self.assertIn("Reddit r/chubbytravel", report)
        self.assertIn("Reddit r/luxurytravel", report)
        self.assertIn("Maldives vs Bora Bora for a family trip using points｜Maldives 与 Bora Bora 奢华海岛家庭积分旅行比较", report)
        self.assertIn("Tokyo and Kyoto hotels with breakfast, suite upgrade and late checkout｜东京和京都酒店选择：早餐、套房升级与延迟退房", report)
        self.assertIn("Is Amex FHR or Virtuoso better for a safari lodge booking?｜Safari Lodge 预订：Amex FHR 还是 Virtuoso 更合适？", report)
        self.assertIn("标题：Maldives vs Bora Bora for a family trip using points｜Maldives 与 Bora Bora 奢华海岛家庭积分旅行比较", report)
        self.assertIn("120,000", report)
        self.assertIn("早餐、套房升级、延迟退房或酒店礼遇", report)
        self.assertIn("父母、孩子或多代家庭", report)
        self.assertIn("医疗转运", report)
        self.assertNotIn("正文已抓取到可用信息", report)
        self.assertNotIn("这是 Reddit 高端旅行社区的经验帖", report)

    def test_life_title_display_uses_original_then_chinese_translation_and_specific_summary(self) -> None:
        item = self.item(
            "LoyaltyLobby",
            "Chase Ultimate Rewards To Southwest Airlines Rapid Rewards 30% Conversion Bonus Through June 5, 2026",
            "Chase Ultimate Rewards has a 30% transfer bonus to Southwest Airlines Rapid Rewards through June 5, 2026. The article says travelers should compare cash fares, award availability, cancellation policy and the value of Southwest points before transferring.",
            "https://example.com/chase-southwest",
        )
        lines: list[str] = []
        dr.append_life_item(lines, item, 1)
        rendered = "\n".join(lines)

        self.assertIn("### 1. Chase Ultimate Rewards To Southwest Airlines Rapid Rewards 30% Conversion Bonus Through June 5, 2026｜Chase Ultimate Rewards 转 Southwest Rapid Rewards 30% 奖励，截止 2026-06-05", rendered)
        self.assertIn("标题：Chase Ultimate Rewards To Southwest Airlines Rapid Rewards 30% Conversion Bonus Through June 5, 2026｜Chase Ultimate Rewards 转 Southwest Rapid Rewards 30% 奖励，截止 2026-06-05", rendered)
        self.assertIn("30%", rendered)
        self.assertIn("June 5, 2026", rendered)
        self.assertIn("现金价", rendered)
        self.assertNotIn("内容涉及转点 bonus", rendered)

    def test_life_title_translation_and_summary_are_not_polluted_by_related_links(self) -> None:
        item = self.item(
            "LoyaltyLobby",
            "GHA Discovery Double D$ For Stays At Almanac Hotels May 15 – December 31, 2026 (Book May 15 – August 15)",
            "GHA Discovery offers Double D$ for stays at Almanac Hotels from May 15 to December 31, 2026, with booking required from May 15 to August 15. Related sidebar text mentions Kempinski Best Rate Guarantee and retirement withdrawal, but those are unrelated.",
            "https://example.com/gha-double-d",
        )
        lines: list[str] = []
        dr.append_life_item(lines, item, 1)
        rendered = "\n".join(lines)

        self.assertIn("GHA Discovery Double D$ For Stays At Almanac Hotels May 15 – December 31, 2026 (Book May 15 – August 15)｜GHA Discovery Almanac 酒店双倍 D$ 促销：入住至 2026-12-31，预订至 2026-08-15", rendered)
        self.assertIn("双倍 D$", rendered)
        self.assertIn("May 15", rendered)
        self.assertIn("August 15", rendered)
        self.assertNotIn("Kempinski 酒店最优价格保证", rendered)
        self.assertNotIn("提款率", rendered)

    def test_reddit_named_sources_are_enriched_from_thread_body(self) -> None:
        item = self.item(
            "r/ExpatFIRE",
            "CoastFIRE plans in Asia, am I making a mistake?",
            "RSS teaser only.",
            "https://www.reddit.com/r/ExpatFIRE/comments/1tfkvom/coastfire_plans_in_asia_am_i_making_a_mistake/",
        )
        original_reddit = dr.reddit_thread_paragraphs
        original_article = dr.article_paragraphs

        try:
            dr.reddit_thread_paragraphs = lambda url, limit=8: [
                "The full thread discusses visa runs, long-stay health insurance, tax residency, income durability, renting in Asia and whether CoastFIRE still works if markets underperform."
            ]
            dr.article_paragraphs = lambda url, limit=8: ["SHOULD NOT USE ARTICLE SCRAPER"]
            enriched = dr.enrich_article_item(item)
        finally:
            dr.reddit_thread_paragraphs = original_reddit
            dr.article_paragraphs = original_article

        self.assertIn("visa runs", enriched.summary)
        self.assertIn("tax residency", enriched.summary)
        self.assertNotIn("SHOULD NOT USE", enriched.summary)

    def test_life_source_registry_stays_under_first_version_limit(self) -> None:
        self.assertLessEqual(len(dr.LIFE_DIGEST_FEEDS), 30)
        sources = {feed.source for feed in dr.LIFE_DIGEST_FEEDS}
        self.assertIn("Morningstar Retirement", sources)
        self.assertIn("OECD Tax Residency", sources)
        self.assertIn("Frequent Miler", sources)


if __name__ == "__main__":
    unittest.main()
