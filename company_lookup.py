"""会社名 → 証券コードの解決。

`/stock/<code>` は証券コードを受ける前提だが、実際には会社名で開かれる。
検索欄のプレースホルダが「銘柄コードまたは会社名」なので、名前を打つのが自然で、
サジェストを選ばずにEnterを押すと名前のままURLになる（例 `/stock/キオクシア`）。

名前のまま来たときに空のページを返すと「アプリが壊れている」に見えるため、
ここで名前をコードに寄せ、解決できないときは候補を返して選ばせる。

出典は `static/companies.json`（JPXの上場銘柄一覧・ETF/REIT除外済み）。
DBを引かないので軽く、リクエストごとの読み込みも起きない。
"""

import json
import os
from typing import Dict, List, Optional

_COMPANIES: Optional[List[dict]] = None
_BY_CODE: Optional[Dict[str, str]] = None


def _load() -> List[dict]:
    """companies.json を [{c: コード, n: 会社名}, ...] として一度だけ読み込む"""
    global _COMPANIES, _BY_CODE
    if _COMPANIES is None:
        items: List[dict] = []
        try:
            path = os.path.join(os.path.dirname(__file__), 'static', 'companies.json')
            with open(path, 'r', encoding='utf-8') as f:
                for row in json.load(f):
                    code = str(row.get('c', '')).strip()
                    name = (row.get('n') or '').strip()
                    if code and name:
                        items.append({'c': code, 'n': name})
        except Exception as e:
            print(f'companies.json 読み込みエラー: {e}')
        _COMPANIES = items
        _BY_CODE = {i['c']: i['n'] for i in items}
    return _COMPANIES


def is_listed_code(code: str) -> bool:
    """JPXの一覧に載っているコードか（＝実在はするが、まだ分析していないだけ）"""
    if not code:
        return False
    _load()
    return code.strip().upper() in (_BY_CODE or {})


def name_of(code: str) -> Optional[str]:
    """コードから会社名。一覧に無ければ None"""
    if not code:
        return None
    _load()
    return (_BY_CODE or {}).get(code.strip().upper())


def looks_like_name(text: str) -> bool:
    """コードではなく会社名として打たれたか。

    日本株コードは `7203` `285A` のように必ずASCII4文字で、米国株ティッカーも
    ASCIIしか使わない。したがって**非ASCIIが1文字でも入っていれば名前**と判断できる。
    「4文字かどうか」で判定すると `AAPL` のような外国株を巻き込むため、この境界にする。
    """
    return any(ord(ch) > 127 for ch in (text or ''))


def suggest(text: str, limit: int = 8) -> List[dict]:
    """会社名の候補を返す。並びは検索欄のサジェストと同じにする。

    画面に出ている順とサーバーが選ぶ順が違うと、同じ入力で行き先が変わって見える。
    """
    q = (text or '').strip()
    if not q:
        return []
    lower = q.lower()
    upper = q.upper()

    starts: List[dict] = []
    contains: List[dict] = []
    for item in _load():
        if item['c'].upper().startswith(upper) or item['n'].lower().startswith(lower):
            starts.append(item)
        elif lower in item['n'].lower():
            contains.append(item)
        if len(starts) >= limit:
            break
    return (starts + contains)[:limit]


def resolve(text: str) -> Optional[str]:
    """会社名から証券コードを1つに決められるときだけ、そのコードを返す。

    決められないとき（候補が複数）は None を返す。**勝手に1件目を選ばない。**
    「トヨタ」で トヨタ紡織 と トヨタ自動車 が出るような場合に片方へ飛ばすと、
    開いたページが目的の会社かどうかを読み手が確かめられないため。
    """
    q = (text or '').strip()
    if not q:
        return None

    # コードそのものが来ていればそれを使う
    if is_listed_code(q):
        return q.upper()

    lower = q.lower()
    items = _load()

    exact = [i for i in items if i['n'].lower() == lower]
    if len(exact) == 1:
        return exact[0]['c']

    starts = [i for i in items if i['n'].lower().startswith(lower)]
    if len(starts) == 1:
        return starts[0]['c']

    contains = [i for i in items if lower in i['n'].lower()]
    if len(contains) == 1:
        return contains[0]['c']

    return None
