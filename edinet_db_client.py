"""EDINET DB API を使った日本株データの欠損補完。

EDINET DB は金融庁の公式EDINET APIとは別の第三者サービス。
Freeプラン（100 requests/day）を前提に、Yahoo等で取れなかった項目だけを埋める。
既に値がある項目は絶対に上書きしない。
"""

from __future__ import annotations

import html
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests
from dotenv import load_dotenv


load_dotenv()

BASE_URL = "https://edinetdb.jp/v1"
JST = timezone(timedelta(hours=9))
FREE_PLAN_LIMIT = 100


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        for key in ("value", "amount", "raw"):
            if key in value:
                return _number(value[key])
        return None
    try:
        text = str(value).strip().replace(",", "").replace("％", "").replace("%", "")
        if not text or text in {"-", "--", "---"}:
            return None
        return float(text)
    except (TypeError, ValueError):
        return None


def _first(mapping: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if not _empty(value):
            return value
    return None


def _clean_text(value: Any, max_length: int = 1200) -> Optional[str]:
    if _empty(value):
        return None
    text = html.unescape(str(value))
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_length] if text else None


def _rows(payload: Any) -> List[Dict[str, Any]]:
    """エンドポイント間の薄いレスポンス差を吸収して辞書行を返す。"""
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []

    for key in ("data", "results", "items", "companies", "financials",
                "earnings", "shareholders", "directors", "officers"):
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
        if isinstance(value, dict):
            for nested_key in ("data", "results", "items", "companies",
                               "financials", "earnings", "shareholders",
                               "directors", "officers"):
                nested = value.get(nested_key)
                if isinstance(nested, list):
                    return [row for row in nested if isinstance(row, dict)]
            return [value]
    return [payload]


def _object(payload: Any) -> Dict[str, Any]:
    rows = _rows(payload)
    return rows[0] if rows else {}


def _date_for_row(row: Dict[str, Any]) -> str:
    value = _first(row, "fiscal_year_end", "period_end", "year_end_date",
                   "fiscalYearEnd", "date", "fiscal_year", "fiscalYear")
    if value is None:
        return ""
    text = str(value).strip()
    if re.fullmatch(r"\d{4}", text):
        # APIに決算月が無い行でも年度順を壊さないための表示用日付。
        return f"{text}-12-31"
    return text[:10]


def _series(rows: Iterable[Dict[str, Any]], key: str) -> List[Dict[str, Any]]:
    result = []
    for row in rows:
        value = _number(row.get(key))
        date = _date_for_row(row)
        if value is not None and date:
            result.append({"date": date, "value": value})
    return sorted(result, key=lambda item: item["date"])[-5:]


class EdinetDbClient:
    """日次上限・安全余力・短期キャッシュを内蔵したAPIクライアント。"""

    def __init__(self, api_key: Optional[str] = None,
                 session: Optional[requests.Session] = None):
        # EDINETDB_API_KEYを正本とし、旧名EDINET_API_KEYも移行用に受け付ける。
        self.api_key = (api_key or os.getenv("EDINETDB_API_KEY")
                        or os.getenv("EDINET_API_KEY") or "").strip()
        configured_limit = _env_int("EDINETDB_DAILY_LIMIT", FREE_PLAN_LIMIT)
        self.daily_limit = max(1, min(configured_limit, FREE_PLAN_LIMIT))
        self.reserve = max(0, min(_env_int("EDINETDB_DAILY_RESERVE", 10),
                                  self.daily_limit - 1))
        self.cache_seconds = max(60, _env_int("EDINETDB_CACHE_HOURS", 24) * 3600)
        self.timeout = max(3, _env_int("EDINETDB_TIMEOUT", 20))
        self.session = session or requests.Session()
        self._lock = threading.RLock()
        self._day = datetime.now(JST).date()
        self._used = 0
        self._remaining: Optional[int] = None
        self._cache: Dict[Tuple[str, Tuple[Tuple[str, str], ...]], Tuple[float, Any, str]] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def budget_snapshot(self) -> Dict[str, Any]:
        with self._lock:
            self._roll_day()
            return {
                "daily_limit": self.daily_limit,
                "daily_reserve": self.reserve,
                "requests_used_process": self._used,
                "remaining_remote": self._remaining,
            }

    def _roll_day(self) -> None:
        today = datetime.now(JST).date()
        if today != self._day:
            self._day = today
            self._used = 0
            self._remaining = None
            self._cache.clear()

    def _can_request(self) -> bool:
        self._roll_day()
        usable = self.daily_limit - self.reserve
        if self._used >= usable:
            return False
        if self._remaining is not None and self._remaining <= self.reserve:
            return False
        return True

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Tuple[Any, str]:
        if not self.enabled:
            return None, "disabled"

        clean_params = {str(k): str(v) for k, v in (params or {}).items() if v is not None}
        cache_key = (path, tuple(sorted(clean_params.items())))

        with self._lock:
            self._roll_day()
            cached = self._cache.get(cache_key)
            if cached and time.time() - cached[0] < self.cache_seconds:
                return cached[1], cached[2]
            if not self._can_request():
                return None, "budget_reserved"

            # 同時アクセスでも上限を超えないよう、判定からレスポンス処理まで直列化する。
            self._used += 1
            try:
                response = self.session.get(
                    f"{BASE_URL}{path}",
                    params=clean_params,
                    headers={
                        "X-API-Key": self.api_key,
                        "Accept": "application/json",
                        "User-Agent": "CompanyNote/1.0 (EDINET DB fallback)",
                    },
                    timeout=self.timeout,
                )
            except requests.Timeout:
                return None, "timeout"
            except requests.RequestException:
                return None, "network_error"

            remaining = response.headers.get("X-RateLimit-Remaining")
            if remaining is not None:
                try:
                    self._remaining = int(remaining)
                except (TypeError, ValueError):
                    pass

            if response.status_code == 429:
                self._remaining = 0
                return None, "rate_limited"
            if response.status_code == 404:
                self._cache[cache_key] = (time.time(), None, "no_data")
                return None, "no_data"
            if response.status_code in (401, 403):
                return None, "auth_error"
            if response.status_code >= 500:
                return None, "source_error"
            if response.status_code >= 400:
                return None, "error"

            try:
                payload = response.json()
            except ValueError:
                return None, "parse_error"
            self._cache[cache_key] = (time.time(), payload, "success")
            return payload, "success"

    def find_edinet_code(self, stock_code: str) -> Tuple[Optional[str], str]:
        code = re.sub(r"\.T$", "", (stock_code or "").strip().upper())
        if not re.fullmatch(r"[0-9A-Z]{4}", code):
            return None, "invalid_code"

        payload, status = self.get("/search", {"q": code})
        if status != "success":
            return None, status

        candidates = []
        for row in _rows(payload):
            sec_code = str(_first(row, "sec_code", "securities_code", "security_code", "ticker") or "").upper()
            listed = str(row.get("listing_status") or "listed").lower() != "delisted"
            if listed and (sec_code == code or sec_code[:4] == code):
                candidates.append(row)
        if not candidates:
            return None, "no_data"
        edinet_code = _first(candidates[0], "edinet_code", "edinetCode", "code")
        return (str(edinet_code) if edinet_code else None,
                "success" if edinet_code else "no_data")


_default_client: Optional[EdinetDbClient] = None
_default_lock = threading.Lock()


def get_edinet_db_client() -> EdinetDbClient:
    global _default_client
    with _default_lock:
        if _default_client is None:
            _default_client = EdinetDbClient()
        return _default_client


def fetch_edinet_db_business_summary(
        symbol: str, client: Optional[EdinetDbClient] = None) -> Tuple[Optional[str], Dict[str, Any]]:
    """事業概要の再取得ボタン用に、EDINET DBプロフィールだけを取得する。"""
    client = client or get_edinet_db_client()
    fetched_at = datetime.now(timezone.utc).isoformat()
    diagnostic: Dict[str, Any] = {
        "status": "disabled" if not client.enabled else "no_data",
        "source": "EDINET DB API",
        "fetched_at": fetched_at,
    }
    if not client.enabled:
        diagnostic["reason"] = "EDINETDB_API_KEY未設定"
        return None, diagnostic

    edinet_code, search_status = client.find_edinet_code(symbol)
    diagnostic["endpoints"] = {"search": search_status}
    if not edinet_code:
        diagnostic["status"] = search_status
        diagnostic["reason"] = "証券コードに対応するEDINETコードが未収録"
        diagnostic.update(client.budget_snapshot())
        return None, diagnostic

    payload, profile_status = client.get(f"/companies/{edinet_code}/profile")
    diagnostic.update({
        "status": profile_status,
        "edinet_code": edinet_code,
        "endpoints": {"search": search_status, "profile": profile_status},
        **client.budget_snapshot(),
    })
    if profile_status != "success":
        return None, diagnostic

    temporary: Dict[str, Any] = {}
    _apply_profile(temporary, payload)
    summary = temporary.get("business_summary_jp")
    diagnostic["status"] = "success" if summary else "no_data"
    diagnostic["filled"] = ["business_summary_jp"] if summary else []
    diagnostic["attribution"] = "Powered by EDINET DB"
    return summary, diagnostic


def _apply_profile(result: Dict[str, Any], payload: Any) -> List[str]:
    profile = _object(payload)
    filled = []
    mapping = {
        "name_jp": ("name_ja", "name_jp", "company_name_ja", "name"),
        "established": ("founding_date", "established", "establishment_date"),
        "listing_date": ("listing_date", "listed_date"),
        "headquarters_jp": ("hq_address", "headquarters", "address"),
        "ceo_name_jp": ("representative_name", "representative", "ceo_name"),
        "industry_jp": ("industry", "industry_name", "business_category"),
        "market_jp": ("market", "market_segment", "listing_market"),
        "business_summary_jp": ("business_summary", "business_items", "business_overview"),
    }
    for target, aliases in mapping.items():
        if not _empty(result.get(target)):
            continue
        value = _first(profile, *aliases)
        if target == "business_summary_jp":
            if isinstance(value, list):
                value = "、".join(str(item) for item in value if item)
            value = _clean_text(value)
        elif isinstance(value, (dict, list)):
            value = _clean_text(value)
        if not _empty(value):
            result[target] = value
            filled.append(target)
    return filled


def _apply_financials(result: Dict[str, Any], payload: Any) -> List[str]:
    rows = _rows(payload)
    filled = []
    direct = {
        "revenue": "revenue",
        "op_income": "operating_income",
        "ordinary_income": "ordinary_income",
        "net_income": "net_income",
        "eps": "eps",
        "dps": "dividend_per_share",
        "operating_cf": "cf_operating",
        "investing_cf": "cf_investing",
        "financing_cf": "cf_financing",
        "cash": "cash",
        "current_assets_list": "current_assets",
        "current_liabilities_list": "current_liabilities",
    }
    for target, source in direct.items():
        if _empty(result.get(target)):
            values = _series(rows, source)
            if values:
                result[target] = values
                filled.append(target)

    computed = {"equity_ratio_list": [], "roe": [], "roa": [], "payout_ratio": []}
    margins = []
    for row in rows:
        date = _date_for_row(row)
        if not date:
            continue
        revenue = _number(row.get("revenue"))
        op_income = _number(row.get("operating_income"))
        net_income = _number(row.get("net_income"))
        assets = _number(row.get("total_assets"))
        equity = (_number(row.get("shareholders_equity"))
                  or _number(row.get("net_assets")))
        eps = _number(row.get("eps"))
        dps = _number(row.get("dividend_per_share"))

        if revenue not in (None, 0) and op_income is not None:
            margins.append({"date": date, "value": op_income / revenue * 100})
        if assets not in (None, 0) and equity is not None:
            computed["equity_ratio_list"].append({"date": date, "value": equity / assets * 100})
        if equity not in (None, 0) and net_income is not None:
            computed["roe"].append({"date": date, "value": net_income / equity * 100})
        if assets not in (None, 0) and net_income is not None:
            computed["roa"].append({"date": date, "value": net_income / assets * 100})
        if eps not in (None, 0) and dps is not None and eps > 0:
            ratio = dps / eps * 100
            if 0 <= ratio <= 200:
                computed["payout_ratio"].append({"date": date, "value": ratio})

    for target, values in computed.items():
        if _empty(result.get(target)) and values:
            result[target] = sorted(values, key=lambda item: item["date"])[-5:]
            filled.append(target)

    if _empty(result.get("op_margin_pct")) and margins:
        result["op_margin_pct"] = sorted(margins, key=lambda item: item["date"])[-1]["value"]
        filled.append("op_margin_pct")
    if _empty(result.get("equity_ratio_pct")) and computed["equity_ratio_list"]:
        result["equity_ratio_pct"] = sorted(
            computed["equity_ratio_list"], key=lambda item: item["date"])[-1]["value"]
        filled.append("equity_ratio_pct")

    for scalar, series_key in (("operating_cash_flow", "operating_cf"),
                               ("current_liabilities", "current_liabilities_list"),
                               ("cash_and_equivalents", "cash")):
        values = result.get(series_key) or []
        if _empty(result.get(scalar)) and values:
            result[scalar] = sorted(values, key=lambda item: item.get("date", ""))[-1].get("value")
            filled.append(scalar)
    return filled


def _apply_shareholders(result: Dict[str, Any], payload: Any) -> List[str]:
    if not _empty(result.get("major_shareholders_jp")):
        return []
    rows = _rows(payload)
    if not rows:
        return []
    latest_year = max((str(_first(row, "fiscal_year", "fiscalYear") or "") for row in rows), default="")
    holders = []
    for row in rows:
        year = str(_first(row, "fiscal_year", "fiscalYear") or "")
        if latest_year and year and year != latest_year:
            continue
        name = _first(row, "holder_name", "holderName", "name")
        if not name:
            continue
        holders.append({
            "name": str(name),
            "shares": _number(_first(row, "shares_held", "sharesHeld", "shares")),
            "ratio": _number(_first(row, "ratio_pct", "ratio", "ownership_ratio")),
            "as_of": _first(row, "as_of", "period_end", "fiscal_year", "fiscalYear"),
            "source": "EDINET DB API（有価証券報告書）",
        })
    if holders:
        result["major_shareholders_jp"] = holders[:10]
        return ["major_shareholders_jp"]
    return []


def _apply_directors(result: Dict[str, Any], payload: Any) -> List[str]:
    if not _empty(result.get("company_officers")):
        return []
    rows = _rows(payload)
    if not rows:
        return []
    latest_year = max((str(_first(row, "fiscal_year", "fiscalYear") or "") for row in rows), default="")
    officers = []
    for row in rows:
        year = str(_first(row, "fiscal_year", "fiscalYear") or "")
        if latest_year and year and year != latest_year:
            continue
        name = _first(row, "officerName", "officer_name", "name")
        if not name:
            continue
        title = _first(row, "officialTitle", "official_title", "title")
        officers.append({
            "name": str(name),
            "name_jp": str(name),
            "title": str(title or "役員"),
            "title_jp": str(title or "役員"),
            "shares": _number(_first(row, "sharesHeld", "shares_held", "shares")),
            "source": "EDINET DB API（有価証券報告書）",
        })
    if officers:
        result["company_officers"] = officers
        return ["company_officers"]
    return []


def _apply_earnings(result: Dict[str, Any], payload: Any) -> List[str]:
    rows = _rows(payload)
    if not rows:
        return []
    row = max(rows, key=lambda item: str(_first(item, "disclosure_date", "date") or ""))
    filled = []
    mapping = {
        "forecast_revenue": "forecast_revenue",
        "forecast_op_income": "forecast_operating_income",
        "forecast_ordinary_income": "forecast_ordinary_income",
        "forecast_net_income": "forecast_net_income",
    }
    for target, source in mapping.items():
        if _empty(result.get(target)):
            value = _number(row.get(source))
            if value is not None:
                result[target] = value / 1e8
                filled.append(target)
    # 予想値が1つも無い行の決算期を「予想年度」として保存しない。
    if filled and _empty(result.get("forecast_year")):
        year = _first(row, "forecast_fiscal_year_end", "forecast_year_end",
                      "fiscal_year_end", "forecast_fiscal_year", "fiscal_year")
        if year:
            result["forecast_year"] = str(year)
            filled.append("forecast_year")
    return filled


def apply_edinet_db_fallback(symbol: str, result: Dict[str, Any],
                             client: Optional[EdinetDbClient] = None) -> List[str]:
    """日本株の欠損項目だけをEDINET DBで補完し、埋めたキー一覧を返す。"""
    if not symbol.endswith(".T"):
        return []
    client = client or get_edinet_db_client()
    status_root = result.setdefault("source_status", {})
    fetched_at = datetime.now(timezone.utc).isoformat()
    if not client.enabled:
        status_root["edinet_db"] = {
            "status": "disabled", "source": "EDINET DB API",
            "reason": "EDINETDB_API_KEY未設定", "fetched_at": fetched_at,
        }
        return []

    need_profile = any(_empty(result.get(key)) for key in (
        "business_summary_jp", "established", "headquarters_jp", "ceo_name_jp"))
    need_financials = any(_empty(result.get(key)) for key in (
        "revenue", "op_income", "net_income", "operating_cf"))
    need_shareholders = _empty(result.get("major_shareholders_jp"))
    need_directors = _empty(result.get("company_officers"))
    need_earnings = all(_empty(result.get(key)) for key in (
        "forecast_revenue", "forecast_op_income", "forecast_net_income"))
    if not any((need_profile, need_financials, need_shareholders,
                need_directors, need_earnings)):
        return []

    edinet_code, search_status = client.find_edinet_code(symbol)
    endpoint_status = {"search": search_status}
    if not edinet_code:
        status_root["edinet_db"] = {
            "status": search_status, "source": "EDINET DB API",
            "reason": "証券コードに対応するEDINETコードが未収録",
            "fetched_at": fetched_at, **client.budget_snapshot(),
        }
        return []

    filled: List[str] = []
    requests_to_make = []
    if need_profile:
        requests_to_make.append(("profile", f"/companies/{edinet_code}/profile", None, _apply_profile))
    if need_financials:
        requests_to_make.append(("financials", f"/companies/{edinet_code}/financials",
                                 {"years": 5, "period": "annual"}, _apply_financials))
    if need_shareholders:
        requests_to_make.append(("major_shareholders", f"/companies/{edinet_code}/major-shareholders",
                                 {"period": "annual"}, _apply_shareholders))
    if need_directors:
        requests_to_make.append(("directors", f"/companies/{edinet_code}/directors", None, _apply_directors))
    if need_earnings:
        requests_to_make.append(("earnings", f"/companies/{edinet_code}/earnings", None, _apply_earnings))

    for label, path, params, apply_func in requests_to_make:
        payload, status = client.get(path, params)
        endpoint_status[label] = status
        if status == "success":
            filled.extend(apply_func(result, payload))
        if status in {"budget_reserved", "rate_limited", "auth_error"}:
            break

    unique_filled = list(dict.fromkeys(filled))
    failures = [s for s in endpoint_status.values() if s != "success"]
    final_status = "success" if unique_filled else (
        "budget_reserved" if "budget_reserved" in failures else
        "rate_limited" if "rate_limited" in failures else
        "auth_error" if "auth_error" in failures else
        "no_data" if failures and all(s == "no_data" for s in failures) else
        "no_data")
    status_root["edinet_db"] = {
        "status": final_status,
        "source": "EDINET DB API",
        "edinet_code": edinet_code,
        "filled": unique_filled,
        "endpoints": endpoint_status,
        "fetched_at": fetched_at,
        "attribution": "Powered by EDINET DB",
        **client.budget_snapshot(),
    }

    if any(key in unique_filled for key in (
            "revenue", "op_income", "ordinary_income", "net_income", "operating_cf")):
        status_root["financials"] = {
            "status": "success", "source": "Yahoo Finance / EDINET DB API",
            "fallback": "EDINET DB API", "fetched_at": fetched_at,
        }
    if "business_summary_jp" in unique_filled:
        status_root["business_summary"] = {
            "status": "success", "source": "EDINET DB API (gBizINFO/EDINET)",
            "language": "ja", "fetched_at": fetched_at,
        }
    if "major_shareholders_jp" in unique_filled or "company_officers" in unique_filled:
        status_root["holders_officers"] = {
            "status": "success", "source": "EDINET DB API (有価証券報告書)",
            "fetched_at": fetched_at,
        }
    return unique_filled
