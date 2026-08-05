import unittest

from edinet_db_client import (
    EdinetDbClient,
    apply_edinet_db_fallback,
    fetch_edinet_db_business_summary,
)


class FakeResponse:
    def __init__(self, payload, status_code=200, remaining="99"):
        self._payload = payload
        self.status_code = status_code
        self.headers = {"X-RateLimit-Remaining": remaining}

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


class StubEdinetClient:
    enabled = True

    def __init__(self, payloads, edinet_code="E00001"):
        self.payloads = payloads
        self.edinet_code = edinet_code
        self.calls = []

    def find_edinet_code(self, symbol):
        self.calls.append(("search", symbol))
        return self.edinet_code, "success" if self.edinet_code else "no_data"

    def get(self, path, params=None):
        self.calls.append((path, params))
        value = self.payloads.get(path)
        if value is None:
            return None, "no_data"
        return value, "success"

    def budget_snapshot(self):
        return {
            "daily_limit": 100,
            "daily_reserve": 10,
            "requests_used_process": len(self.calls),
            "remaining_remote": 94,
        }


class EdinetDbClientTests(unittest.TestCase):
    def test_exact_stock_code_is_selected(self):
        session = FakeSession([FakeResponse({"results": [
            {"edinet_code": "E99999", "sec_code": "72010", "listing_status": "listed"},
            {"edinet_code": "E02144", "sec_code": "72030", "listing_status": "listed"},
            {"edinet_code": "E00000", "sec_code": "72030", "listing_status": "delisted"},
        ]})])
        client = EdinetDbClient(api_key="test", session=session)

        code, status = client.find_edinet_code("7203.T")

        self.assertEqual("success", status)
        self.assertEqual("E02144", code)
        self.assertEqual("test", session.calls[0][1]["headers"]["X-API-Key"])

    def test_daily_reserve_stops_before_free_limit(self):
        session = FakeSession([
            FakeResponse({"ok": 1}, remaining="2"),
            FakeResponse({"ok": 2}, remaining="1"),
        ])
        client = EdinetDbClient(api_key="test", session=session)
        client.daily_limit = 3
        client.reserve = 1

        self.assertEqual("success", client.get("/one")[1])
        self.assertEqual("success", client.get("/two")[1])
        self.assertEqual("budget_reserved", client.get("/three")[1])
        self.assertEqual(2, len(session.calls))

    def test_missing_fields_are_filled_without_overwriting_existing_values(self):
        base = "/companies/E00001"
        client = StubEdinetClient({
            f"{base}/profile": {"data": {
                "name_ja": "補完株式会社",
                "founding_date": "2001-02-03",
                "representative_name": "代表 太郎",
                "hq_address": "東京都千代田区",
                "business_summary": "<b>公式</b>の事業概要",
            }},
            f"{base}/financials": {"data": [
                {
                    "fiscal_year": 2024, "revenue": 1000,
                    "operating_income": 100, "ordinary_income": 90,
                    "net_income": 50, "total_assets": 2000,
                    "shareholders_equity": 1000, "cf_operating": 80,
                    "cf_investing": -20, "cf_financing": -10,
                    "cash": 300, "current_assets": 600,
                    "current_liabilities": 250, "eps": 50,
                    "dividend_per_share": 10,
                },
                {
                    "fiscal_year": 2025, "revenue": 1200,
                    "operating_income": 144, "ordinary_income": 130,
                    "net_income": 72, "total_assets": 2400,
                    "shareholders_equity": 1200, "cf_operating": 100,
                    "cf_investing": -30, "cf_financing": -15,
                    "cash": 360, "current_assets": 700,
                    "current_liabilities": 280, "eps": 60,
                    "dividend_per_share": 12,
                },
            ]},
            f"{base}/major-shareholders": {"data": [
                {"fiscal_year": 2025, "holder_name": "大株主A", "shares_held": 500, "ratio_pct": 25.0},
                {"fiscal_year": 2024, "holder_name": "旧大株主", "shares_held": 400, "ratio_pct": 20.0},
            ]},
            f"{base}/directors": {"data": [
                {"fiscal_year": 2025, "officerName": "役員 花子", "officialTitle": "代表取締役", "sharesHeld": 10},
            ]},
            f"{base}/earnings": {"data": {"count": 1, "earnings": [
                {"disclosure_date": "2025-08-01", "forecast_fiscal_year_end": "2026-03-31",
                 "forecast_revenue": 150000000000, "forecast_operating_income": 18000000000,
                 "forecast_ordinary_income": 17000000000, "forecast_net_income": 10000000000},
            ], "edinet_code": "E00001"}},
        })
        result = {
            "business_summary_jp": "既存の日本語概要",
            "name_jp": "既存社名",
            "established": None,
            "headquarters_jp": None,
            "ceo_name_jp": None,
            "revenue": [], "op_income": [], "ordinary_income": [], "net_income": [],
            "eps": [], "dps": [], "operating_cf": [], "investing_cf": [],
            "financing_cf": [], "cash": [], "current_assets_list": [],
            "current_liabilities_list": [], "equity_ratio_list": [], "roe": [],
            "roa": [], "payout_ratio": [], "major_shareholders_jp": [],
            "company_officers": [], "forecast_revenue": None,
            "forecast_op_income": None, "forecast_net_income": None,
            "source_status": {},
        }

        filled = apply_edinet_db_fallback("7203.T", result, client=client)

        self.assertEqual("既存の日本語概要", result["business_summary_jp"])
        self.assertEqual("既存社名", result["name_jp"])
        self.assertEqual("2001-02-03", result["established"])
        self.assertEqual(2, len(result["revenue"]))
        self.assertAlmostEqual(12.0, result["op_margin_pct"])
        self.assertAlmostEqual(50.0, result["equity_ratio_pct"])
        self.assertAlmostEqual(20.0, result["payout_ratio"][-1]["value"])
        self.assertEqual("大株主A", result["major_shareholders_jp"][0]["name"])
        self.assertEqual("役員 花子", result["company_officers"][0]["name"])
        self.assertEqual(1500.0, result["forecast_revenue"])
        self.assertEqual("success", result["source_status"]["edinet_db"]["status"])
        self.assertIn("revenue", filled)

    def test_unlisted_code_records_no_data_without_other_requests(self):
        client = StubEdinetClient({}, edinet_code=None)
        result = {"source_status": {}}

        filled = apply_edinet_db_fallback("164A.T", result, client=client)

        self.assertEqual([], filled)
        self.assertEqual("no_data", result["source_status"]["edinet_db"]["status"])
        self.assertEqual(1, len(client.calls))

    def test_partial_forecast_is_completed_without_overwriting_existing_value(self):
        client = StubEdinetClient({
            "/companies/E00001/earnings": {"data": [{
                "disclosure_date": "2026-08-01",
                "forecast_fiscal_year_end": "2027-03-31",
                "forecast_revenue": 20000000000,
                "forecast_operating_income": 3000000000,
                "forecast_ordinary_income": 2800000000,
                "forecast_net_income": 1800000000,
            }]},
        })
        result = {
            "business_summary_jp": "概要", "established": "2000-01-01",
            "headquarters_jp": "東京都", "ceo_name_jp": "代表者",
            "revenue": [1], "op_income": [1], "net_income": [1],
            "operating_cf": [1], "major_shareholders_jp": [1],
            "company_officers": [1],
            "forecast_revenue": 123.0, "forecast_op_income": None,
            "forecast_ordinary_income": 20.0, "forecast_net_income": None,
            "forecast_year": None, "source_status": {},
        }

        filled = apply_edinet_db_fallback("7203.T", result, client=client)

        self.assertEqual(123.0, result["forecast_revenue"])
        self.assertEqual(30.0, result["forecast_op_income"])
        self.assertEqual(18.0, result["forecast_net_income"])
        self.assertEqual("2027-03-31", result["forecast_year"])
        self.assertEqual("success", result["source_status"]["forecast"]["status"])
        self.assertIn("forecast_op_income", filled)
        self.assertEqual(2, len(client.calls))
        self.assertIn("/earnings", client.calls[1][0])

    def test_summary_only_fallback_calls_profile_only(self):
        client = StubEdinetClient({
            "/companies/E00001/profile": {
                "data": {"business_summary": "EDINET DBの日本語概要"},
            },
        })

        summary, diagnostic = fetch_edinet_db_business_summary("7203.T", client)

        self.assertEqual("EDINET DBの日本語概要", summary)
        self.assertEqual("success", diagnostic["status"])
        self.assertEqual(2, len(client.calls))
        self.assertNotIn("financials", str(client.calls))


if __name__ == "__main__":
    unittest.main()
