"""
決算発表・業績修正のあった銘柄を取得する。

背景:
  財務データの更新は1銘柄あたり10回のAPI呼び出しが必要で、全3,875銘柄を
  やり直すと約4.5時間かかる。株価のようなバッチ取得も効かない。

  一方で財務データが変わるのは決算を発表したときだけ。
  「今日決算を出した銘柄」だけを更新すれば、平常日は数件〜数十件で済む。
  実測では通常日で12銘柄（全体の0.3%）だった。

取得元:
  kabutan.jp/warning/ 配下。GC/DCスクレイパーと同じ場所で、
  こちらはブロックされた実績がない（Yahoo!JPとは別サイト）。
    mode=4_2 取引時間中に決算発表・業績予想を修正した銘柄
    mode=4_3 取引終了後に決算発表・業績予想を修正した銘柄
    mode=5_1 翌営業日の決算発表予定銘柄
"""

import re
import time
import requests
from bs4 import BeautifulSoup

HEADERS = {
    'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                   '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'ja,en-US;q=0.7,en;q=0.3',
}

BASE = 'https://kabutan.jp/warning/?mode='

MODES = {
    'intraday': '4_2',    # 取引時間中に発表
    'afterhours': '4_3',  # 取引終了後に発表
    'upcoming': '5_1',    # 翌営業日の発表予定
}


def _scrape_mode(mode):
    """1ページ分の銘柄コードと社名を取り出す"""
    url = BASE + mode
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        response.encoding = response.apparent_encoding
        if response.status_code != 200:
            print(f'決算ページ取得エラー HTTP {response.status_code}: {url}')
            return []
    except Exception as e:
        print(f'決算ページ取得エラー {url}: {e}')
        return []

    soup = BeautifulSoup(response.text, 'html.parser')
    table = soup.find('table', class_='stock_table')
    if not table:
        tables = soup.find_all('table')
        table = tables[0] if tables else None
    if not table:
        return []

    stocks = []
    for row in table.find_all('tr')[1:]:
        cells = row.find_all(['th', 'td'])
        if len(cells) < 2:
            continue
        code = cells[0].get_text(strip=True)
        # 4桁数字、または新形式（例: 142A）
        if not re.match(r'^\d{3,4}[A-Z]?$', code):
            continue
        stocks.append({
            'company_code': code,
            'company_name': cells[1].get_text(strip=True),
        })
    return stocks


def fetch_announced_stocks(include_upcoming=False):
    """決算発表・業績修正のあった銘柄を返す。

    Args:
        include_upcoming: 翌営業日の発表予定も含めるか
    Returns:
        {'stocks': [{company_code, company_name, source}], 'by_source': {...}}
    """
    wanted = ['intraday', 'afterhours']
    if include_upcoming:
        wanted.append('upcoming')

    seen = {}
    by_source = {}
    for i, key in enumerate(wanted):
        if i:
            time.sleep(1)   # 連続アクセスを避ける
        rows = _scrape_mode(MODES[key])
        by_source[key] = len(rows)
        for r in rows:
            code = r['company_code']
            if code not in seen:
                seen[code] = {**r, 'source': key}

    return {'stocks': list(seen.values()), 'by_source': by_source}
