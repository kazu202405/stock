"""
JPX（日本取引所）が公開している上場銘柄一覧を取得する。

背景:
  業種の判定をLLMに任せると誤りが残った。実測では、家具小売のニトリに
  「専門商社」、厨房機器メーカーに「建設」が付いた。名前だけでは境界が
  伝わらないため定義を足したが、それでも揺れる。

  一方でJPXは全上場銘柄の業種区分を公式に無料公開している。事実データなので
  推論が要らず、間違いようがない。業種はここから取り、LLMには
  より細かいテーマ判定だけを任せる。

取得できるもの:
  33業種区分  … 東証の標準的な業種分類（水産・農林業／電気機器／銀行業 など）
  17業種区分  … 33業種をまとめた粗い分類
  市場・商品区分 … プライム／スタンダード／グロース／ETF・ETN／REIT など
  規模区分    … TOPIX Core30／Large70／Mid400／Small

  市場区分があるとETF・REIT・PRO Marketを確実に除外できる。
  従来は銘柄コードからの推測に頼っていた。
"""

import io
import re
from urllib.parse import urljoin

# ⚠️ **JPXはファイル名を変える。** 2026-09、`data_j.xls` が `data_j.xlsx` に
#    差し替わって固定URLが404になった。上場廃止の判定はこの一覧が前提なので、
#    取れないと判定そのものが効かなくなる（実際に10日ほど気づけなかった）。
#    ∴ 固定URLでだめなら、配布ページからリンクを見つけ直す。
JPX_URL = ('https://www.jpx.co.jp/markets/statistics-equities/misc/'
           'tvdivq0000001vg2-att/data_j.xlsx')
JPX_INDEX_URL = 'https://www.jpx.co.jp/markets/statistics-equities/misc/01.html'


def _download(timeout=60):
    """銘柄一覧ファイルの中身を返す。取れなければ例外。

    ⚠️ ここで None やからの中身を返さないこと。呼び出し側が「一覧に載って
       いない＝上場廃止」と読むので、取得失敗を空の一覧として渡すと
       全銘柄を廃止扱いにする。
    """
    import requests

    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        res = requests.get(JPX_URL, timeout=timeout, headers=headers)
        res.raise_for_status()
        return res.content
    except Exception as e:
        print('JPXの固定URLで取得できませんでした（配布ページから探します）: %s' % e)

    page = requests.get(JPX_INDEX_URL, timeout=timeout, headers=headers)
    page.raise_for_status()
    m = re.search(r'href="([^"]*data_j\.xlsx?)"', page.text)
    if not m:
        raise RuntimeError('JPXの配布ページに data_j のリンクが見つかりません')
    url = urljoin(JPX_INDEX_URL, m.group(1))
    print('JPXの一覧を配布ページのリンクから取得します: %s' % url)
    res = requests.get(url, timeout=timeout, headers=headers)
    res.raise_for_status()
    return res.content

# 内国株式のみ。ETF・REIT・PRO Marketは分析対象外
DOMESTIC_SEGMENTS = {
    'プライム（内国株式）': 'プライム',
    'スタンダード（内国株式）': 'スタンダード',
    'グロース（内国株式）': 'グロース',
}

# 内国普通株**以外**。分析の対象外だが、**区分は必ず記録する**。
#
# ⚠️ ここを空のままにしたせいで 2026-08-26 に誤診をやった。
#    PRO Market（プロ投資家向け市場）は売買が成立しない日が続くのが正常で、
#    Yahoo・kabutanも扱っていない。区分を持っていなかったため、
#    「出来高ゼロが1年続く103銘柄＝上場廃止」と読み違えた。
#    区分さえ入っていれば起きなかった。
OTHER_SEGMENTS = {
    'PRO Market': 'PRO Market',
    'ETF・ETN': 'ETF・ETN',
    'REIT・ベンチャーファンド・カントリーファンド・インフラファンド': 'REIT等',
    'プライム（外国株式）': '外国株',
    'スタンダード（外国株式）': '外国株',
    'グロース（外国株式）': '外国株',
    '出資証券': '出資証券',
}

# JPXは値が無い欄をハイフンで埋める
_BLANK = {'-', '', 'nan', 'None'}


def _clean(value):
    text = str(value).strip()
    return None if text in _BLANK else text


def fetch(timeout=60):
    """JPXの銘柄一覧を取り込み、内国株式だけを返す。

    Returns:
        [{'code','name','industry','industry17','market','size'}, ...]
    """
    import pandas as pd

    df = pd.read_excel(io.BytesIO(_download(timeout)))

    rows = []
    for _, r in df.iterrows():
        segment = _clean(r.get('市場・商品区分'))
        if segment not in DOMESTIC_SEGMENTS:
            continue

        # 銘柄コードは4桁ゼロ埋め。新形式（156A等）は文字を含むため文字列で扱う
        code = str(r.get('コード')).strip()
        if code.endswith('.0'):
            code = code[:-2]
        code = code.zfill(4)

        rows.append({
            'code': code,
            'name': _clean(r.get('銘柄名')),
            'industry': _clean(r.get('33業種区分')),
            'industry17': _clean(r.get('17業種区分')),
            'market': DOMESTIC_SEGMENTS[segment],
            'size': _clean(r.get('規模区分')),
        })
    return rows


def fetch_all(timeout=60):
    """内国普通株**以外も含めて**全銘柄を返す。

    `fetch()` は分析対象（内国普通株）だけに絞る。こちらは市場区分を
    記録するためのもので、PRO Market・ETF・REIT・外国株も落とさない。

    Returns:
        [{'code','name','industry','industry17','market','size','domestic'}, ...]
        market … 'プライム'/'スタンダード'/'グロース'/'PRO Market'/'ETF・ETN'/
                 'REIT等'/'外国株'/'出資証券'/'その他'
    """
    import pandas as pd

    df = pd.read_excel(io.BytesIO(_download(timeout)))

    rows = []
    for _, r in df.iterrows():
        segment = _clean(r.get('市場・商品区分'))
        domestic = segment in DOMESTIC_SEGMENTS
        # ⚠️ 知らない区分を 'その他' にまとめない。JPXが区分名を変えたときに
        #    黙って混ざり、また区分を見失う。素の区分名をそのまま入れる。
        market = (DOMESTIC_SEGMENTS.get(segment)
                  or OTHER_SEGMENTS.get(segment) or segment or 'その他')
        code = str(r.get('コード')).strip()
        if code.endswith('.0'):
            code = code[:-2]
        code = code.zfill(4)
        rows.append({
            'code': code,
            'name': _clean(r.get('銘柄名')),
            'industry': _clean(r.get('33業種区分')),
            'industry17': _clean(r.get('17業種区分')),
            'market': market,
            'size': _clean(r.get('規模区分')),
            'domestic': domestic,
        })
    return rows


def industry_names(rows):
    """出現した33業種を、JPXの並び順のまま重複なく返す"""
    seen = []
    for r in rows:
        if r['industry'] and r['industry'] not in seen:
            seen.append(r['industry'])
    return seen


def as_map(rows):
    """銘柄コードで引ける辞書にする"""
    return {r['code']: r for r in rows}
