"""JPX公式の銘柄別信用取引週末残高を取得する。

Yahoo!ファイナンス日本版から信用残高を取得できない場合の無料フォールバック。
JPXが毎週公開するPDFを1プロセスにつき一定時間キャッシュし、売残・買残から
信用倍率を計算する。週次データなので、取得元と基準日を必ず併記する。
"""

from __future__ import annotations

import io
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urljoin

import requests
from pypdf import PdfReader


PAGE_URL = "https://www.jpx.co.jp/markets/statistics-equities/margin/05.html"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en-US;q=0.7,en;q=0.3",
}
CACHE_SECONDS = 12 * 60 * 60
ERROR_CACHE_SECONDS = 5 * 60

_lock = threading.Lock()
_cache: Dict[str, Any] = {
    "loaded_at": 0.0,
    "rows": {},
    "as_of": None,
    "url": None,
    "status": "not_loaded",
    "error": None,
}


def _clean_number(value: str) -> int:
    return int(re.sub(r"[^0-9]", "", value or "") or 0)


def parse_margin_text(text: str) -> Tuple[Dict[str, Dict[str, Any]], Optional[str]]:
    """pypdfの抽出テキストから銘柄別の売残・買残を取り出す。"""
    rows: Dict[str, Dict[str, Any]] = {}
    as_of_match = re.search(r"(\d{4})/(\d{1,2})/(\d{1,2})\s*申込み現在", text or "")
    as_of = None
    if as_of_match:
        as_of = f"{int(as_of_match.group(1)):04d}-{int(as_of_match.group(2)):02d}-{int(as_of_match.group(3)):02d}"

    for raw_line in (text or "").splitlines():
        # PDFでは通常の4桁コードにも末尾0が付き、7203は72030と表記される。
        match = re.search(
            r"\s(?P<code>[0-9A-Z]{5})\s+JP[0-9A-Z]{10}\s+(?P<values>.+)$",
            raw_line,
        )
        if not match:
            continue

        # PDF抽出時に「2,748, 400」のような不要な空白が入る場合がある。
        values_text = re.sub(r",\s+(?=\d{3}(?:\D|$))", ",", match.group("values"))
        values = re.findall(r"(?:▲\s*)?[\d,]+", values_text)
        if len(values) < 3:
            continue

        sell = _clean_number(values[0])
        buy = _clean_number(values[2])
        code = match.group("code")[:-1]
        rows[code] = {
            "margin_trading_sell": sell,
            "margin_trading_buy": buy,
            "margin_trading_ratio": round(buy / sell, 2) if sell > 0 else None,
            "as_of": as_of,
        }
    return rows, as_of


def _latest_pdf(session: requests.Session, timeout: int) -> Tuple[str, str]:
    response = session.get(PAGE_URL, headers=HEADERS, timeout=timeout)
    response.raise_for_status()
    links = re.findall(
        r'href=["\']([^"\']*syumatsu(\d{8})00\.pdf)["\']',
        response.text,
        flags=re.I,
    )
    if not links:
        raise ValueError("JPX信用残高PDFのリンクが見つかりません")
    href, date_text = max(links, key=lambda item: item[1])
    return urljoin(PAGE_URL, href), date_text


def _load(session: Optional[requests.Session] = None, timeout: int = 20) -> Dict[str, Any]:
    session = session or requests.Session()
    pdf_url, link_date = _latest_pdf(session, timeout)
    response = session.get(pdf_url, headers=HEADERS, timeout=timeout)
    response.raise_for_status()

    reader = PdfReader(io.BytesIO(response.content))
    all_text = "\n".join((page.extract_text() or "") for page in reader.pages)
    rows, as_of = parse_margin_text(all_text)
    if not rows:
        raise ValueError("JPX信用残高PDFから銘柄データを抽出できません")

    return {
        "loaded_at": time.time(),
        "rows": rows,
        "as_of": as_of or f"{link_date[:4]}-{link_date[4:6]}-{link_date[6:8]}",
        "url": pdf_url,
        "status": "success",
        "error": None,
    }


def get_margin_balance(stock_code: str, session: Optional[requests.Session] = None,
                       timeout: int = 20) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """銘柄の週次信用残高と取得診断を返す。"""
    code = re.sub(r"\.T$", "", (stock_code or "").strip().upper())
    fetched_at = datetime.now(timezone.utc).isoformat()

    with _lock:
        cache_ttl = (CACHE_SECONDS if _cache.get("status") == "success"
                     else ERROR_CACHE_SECONDS)
        if time.time() - float(_cache.get("loaded_at") or 0) >= cache_ttl:
            try:
                _cache.update(_load(session=session, timeout=timeout))
            except requests.Timeout as exc:
                _cache.update({
                    "loaded_at": time.time(), "rows": {}, "status": "timeout",
                    "error": str(exc), "url": PAGE_URL, "as_of": None,
                })
            except requests.RequestException as exc:
                _cache.update({
                    "loaded_at": time.time(), "rows": {}, "status": "network_error",
                    "error": str(exc), "url": PAGE_URL, "as_of": None,
                })
            except Exception as exc:
                _cache.update({
                    "loaded_at": time.time(), "rows": {}, "status": "parse_error",
                    "error": str(exc), "url": PAGE_URL, "as_of": None,
                })

        row = (_cache.get("rows") or {}).get(code)
        status = "success" if row else (
            "no_data" if _cache.get("status") == "success" else _cache.get("status")
        )
        diagnostic = {
            "status": status,
            "source": "JPX 銘柄別信用取引週末残高",
            "as_of": _cache.get("as_of"),
            "fetched_at": fetched_at,
            "url": _cache.get("url") or PAGE_URL,
            "frequency": "weekly",
        }
        if _cache.get("error"):
            diagnostic["error"] = _cache["error"]
        return (dict(row) if row else None), diagnostic
