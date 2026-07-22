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

JPX_URL = ('https://www.jpx.co.jp/markets/statistics-equities/misc/'
           'tvdivq0000001vg2-att/data_j.xls')

# 内国株式のみ。ETF・REIT・PRO Marketは分析対象外
DOMESTIC_SEGMENTS = {
    'プライム（内国株式）': 'プライム',
    'スタンダード（内国株式）': 'スタンダード',
    'グロース（内国株式）': 'グロース',
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
    import requests
    import pandas as pd

    res = requests.get(JPX_URL, timeout=timeout,
                       headers={'User-Agent': 'Mozilla/5.0'})
    res.raise_for_status()
    df = pd.read_excel(io.BytesIO(res.content))

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
