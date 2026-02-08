# ゴールデンクロス / デッドクロス銘柄スクレイパー
# kabutan.jpからGC/DC達成銘柄を取得

import requests
from bs4 import BeautifulSoup
import time
import re

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'ja,en-US;q=0.7,en;q=0.3',
}

GC_BASE_URL = 'https://kabutan.jp/tansaku/?mode=2_0870'
DC_BASE_URL = 'https://kabutan.jp/tansaku/?mode=2_0871'


def _scrape_all_pages(base_url):
    """指定URLから全ページの銘柄を取得する共通処理"""
    all_stocks = []
    page = 1

    while True:
        if page == 1:
            url = base_url
        else:
            url = f'{base_url}&market=0&capitalization=-1&dispmode=normal&stc=&stm=0&page={page}'

        stocks, has_next = _scrape_page(url)
        all_stocks.extend(stocks)

        if not has_next:
            break

        page += 1
        time.sleep(1)

    return all_stocks


def scrape_gc_stocks():
    """
    kabutan.jpからゴールデンクロス達成銘柄を全ページ取得。
    全件返す（フィルタはAPI表示層で実施）。
    """
    return _scrape_all_pages(GC_BASE_URL)


def scrape_dc_stocks():
    """
    kabutan.jpからデッドクロス銘柄を全ページ取得（フィルタなし）。
    """
    return _scrape_all_pages(DC_BASE_URL)


def _scrape_page(url):
    """
    1ページ分のGC銘柄テーブルをパースする。

    Returns:
        tuple: (銘柄リスト, 次ページ有無)
    """
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        response.encoding = response.apparent_encoding
    except requests.exceptions.RequestException as e:
        print(f"リクエストエラー: {e}")
        return [], False

    soup = BeautifulSoup(response.text, 'html.parser')

    stocks = []

    # データテーブルを探す
    table = soup.find('table', class_='stock_table')
    if not table:
        tables = soup.find_all('table')
        for t in tables:
            rows = t.find_all('tr')
            if len(rows) > 1:
                cells = rows[0].find_all(['th', 'td'])
                if len(cells) >= 8:
                    table = t
                    break

    if not table:
        return [], False

    rows = table.find_all('tr')

    for row in rows[1:]:  # ヘッダー行をスキップ
        # 銘柄名が<th>に入っているため、th/td両方取得
        cells = row.find_all(['th', 'td'])
        if len(cells) < 13:
            continue

        # コード取得
        code_cell = cells[0]
        code_link = code_cell.find('a')
        code = code_link.get_text(strip=True) if code_link else code_cell.get_text(strip=True)

        # 数字3〜4桁+英字の銘柄コード（142Aなども含む）
        if not re.match(r'^\d{3,4}[A-Z]?$', code):
            continue

        # 列構成（13セル th/td混在）:
        #   コード<td>(0), 銘柄名<th>(1), 市場<td>(2), 空(3), 空(4),
        #   株価(5), 空(6), 前日比(7), 前日比%(8), 5MA(9), 25MA(10), PER(11), PBR(12)
        stock = {
            'company_code': code,
            'company_name': cells[1].get_text(strip=True),
            'market': cells[2].get_text(strip=True),
            'stock_price': _parse_number(cells[5].get_text(strip=True)),
            'per': _parse_number(cells[11].get_text(strip=True)),
            'pbr': _parse_number(cells[12].get_text(strip=True)),
        }
        stocks.append(stock)

    # 次ページ判定: "次へ" を含むリンクがあるか
    has_next = False
    for a in soup.find_all('a'):
        if '次へ' in a.get_text():
            has_next = True
            break

    return stocks, has_next


def _parse_number(text):
    """数値文字列をfloatに変換（カンマ除去、'---'等はNone）"""
    if not text:
        return None
    text = text.strip().replace(',', '').replace('倍', '').replace('%', '')
    if text in ('---', '－', '-', ''):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _filter_stocks(stocks):
    """PER/PBR両方なし（ETF・リート等）、PER >= 40、PBR >= 10 を除外"""
    result = []
    for s in stocks:
        per = s.get('per')
        pbr = s.get('pbr')
        # PER/PBR両方なしはETF・リート等なので除外
        if per is None and pbr is None:
            continue
        if per is not None and per >= 40:
            continue
        if pbr is not None and pbr >= 10:
            continue
        result.append(s)
    return result


if __name__ == '__main__':
    print("GC銘柄取得中...")
    stocks = scrape_gc_stocks()
    print(f"フィルタ後: {len(stocks)}件")
    for s in stocks[:10]:
        print(f"  {s['company_code']} {s['company_name']} PER:{s['per']} PBR:{s['pbr']}")
