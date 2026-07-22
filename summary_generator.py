"""
事業概要（日本語）を自前で生成する。

背景:
  従来は Yahoo!ファイナンス日本版の「特色」をスクレイピングしていたが、
  2つの問題があった。

  1. アクセス制限
     3,800銘柄を取得しようとすると1回30〜70リクエストで遮断される。
     待機を5秒(18回/分)まで落としても改善せず、実質的に完走できない。

  2. 権利面
     Yahoo・kabutanが載せている「特色」はいずれも四季報（東洋経済）の
     ライセンス提供データで、同じ編集著作物。有料サービスで再配信するのは
     リスクが高い。

  そこで yfinance の英語事業説明（longBusinessSummary）を取得し、
  LLMで日本語の概要に書き直す。yfinanceは全銘柄で問題なく通っており、
  生成物は自社の独自コンテンツになる。

プロンプト設計の要点:
  社名を書かせると誤る（多木化学→「タキケミカル」、日本調理機→「Nitcho
  Corporation」と音訳・英語のままになった）。社名は画面に別途表示されるため、
  概要には含めない指示にしている。
"""

MODEL = 'gpt-4o-mini'

# 1銘柄に付けるテーマの上限。多すぎると「何の会社か」がぼやける
MAX_TAGS = 5

_tag_cache = {'names': None}


def load_taggable_themes():
    """LLMに選ばせるテーマ名の一覧。属性(中国関連・指数など)は含めない。

    属性は事業説明文からは正しく判定できず、推測で付けると誤りが混ざるため
    tagging_enabled=false にしてある。
    """
    if _tag_cache['names'] is not None:
        return _tag_cache['names']
    try:
        from supabase_client import get_supabase_client
        res = (get_supabase_client().table('stock_tags')
               .select('name')
               .eq('kind', 'theme')
               .eq('tagging_enabled', True)
               .order('sort_order')
               .execute())
        _tag_cache['names'] = [r['name'] for r in (res.data or [])]
    except Exception as e:
        print(f'テーマ一覧の取得エラー: {e}')
        _tag_cache['names'] = []
    return _tag_cache['names']


def build_prompt(english_text, company_name=None, sector=None, themes=None):
    """事業概要とテーマ判定を1回の呼び出しでまとめて行わせる。

    別々に呼ぶと費用も時間も倍になるうえ、同じ英語説明を2度読ませることになる。

    プロンプト設計の経緯:
      - 単に翻訳させると事業分野の羅列になり、読んでも会社像が結ばなかった
      - 時価総額や成長率を渡して補強を試みたが、業績推移の欄と内容が重複し
        文章が冗長になるだけで逆効果だった
      - 情報を足すより「会社の類型を最初に言い切る」「誰に売るかを書く」と
        型を絞るほうが効いた
        （例: 「厨房機器の製造・販売」→「商業用厨房機器のメーカー。
              政府・医療・ホテル向けに設計や施工管理も」）
    """
    hint = f'（参考）業種: {sector or "不明"}\n' if sector else ''

    theme_block = ''
    if themes:
        theme_block = (
            '\n【テーマ候補】\n'
            'この一覧の中から、事業内容に実際に該当するものだけを選んでください。\n'
            f'- 最大{MAX_TAGS}個。該当が無ければ空の配列にする\n'
            '- 一覧に無い名前を作らない（表記を1文字も変えない）\n'
            '- 少しでも関係がありそう、という理由で広く付けない。'
            '主要な事業として説明文から読み取れるものだけに絞る\n\n'
            + '、'.join(themes) + '\n'
        )

    return (
        'あなたは企業分析の解説者です。次の英語の事業説明を読み、'
        '日本語の事業概要と、該当する事業テーマを判定してください。\n\n'
        '【事業概要の書き方】\n'
        '狙い: 一読して「何を売って、誰から、どう稼ぐ会社か」が掴めること。\n'
        '- 全体で60〜90文字。体言止め中心\n'
        '- 1文目: 会社の類型を最初に言い切る\n'
        '    （例: 〜のメーカー / 〜の専門商社 / 受託開発 / 店舗運営 / サブスク型 など）\n'
        '- 2文目: 主な顧客や用途、事業の広がりを一言\n'
        '- 事業分野を羅列しない。重要な2〜3個に絞る\n'
        '- 成長率や財務数値は書かない（別欄に表示されるため）\n'
        '- 社名は書かない\n'
        '- 出力にアルファベットを使わない。英語表記の商品名は日本語の一般名詞に'
        '置き換える（例:「Hobonichi Techo」→「手帳」）。カタカナへの音訳もしない。'
        'ただしIT・AI・EC・DXなど定着した略語は可\n'
        '- シェア・業界順位・「最大手」など、英語説明に無い評価は書かない\n'
        '- 投資判断を促す表現は書かない\n'
        + theme_block +
        '\n次のJSONのみを出力してください（前後に説明を付けない）:\n'
        '{"summary": "事業概要", "themes": ["テーマ名", ...]}\n\n'
        + hint +
        f'\n英語説明:\n{english_text[:2500]}'
    )


# 日本語として定着しており、そのまま使ってよい略語
ALLOWED_WORDS = {'IT', 'AI', 'EC', 'DX', 'SAAS', 'IOT', 'CD', 'DVD', 'PC', 'TV'}


def _has_foreign_words(text):
    """日本語文にアルファベット表記の固有名詞が残っているか"""
    import re
    return any(w.upper() not in ALLOWED_WORDS for w in re.findall(r'[A-Za-z]{2,}', text or ''))


def fetch_english_summary(code):
    """yfinanceから英語の事業説明を取得する"""
    import warnings
    warnings.filterwarnings('ignore')
    import yfinance as yf

    symbol = code if code.endswith('.T') else f'{code}.T'
    try:
        info = yf.Ticker(symbol).info or {}
        return info.get('longBusinessSummary')
    except Exception as e:
        print(f'英語概要の取得エラー {code}: {e}')
        return None


def generate(code, company_name=None, sector=None, english_text=None, themes=None):
    """1銘柄の事業概要とテーマを生成する。

    Returns:
        {'summary': str|None, 'themes': [str]} 生成できなければ summary が None
    """
    import llm
    empty = {'summary': None, 'themes': []}
    if not llm.is_available():
        return empty

    english = english_text or fetch_english_summary(code)
    if not english or len(english) < 40:
        return empty

    if themes is None:
        themes = load_taggable_themes()

    data = llm.chat_json(build_prompt(english, company_name, sector, themes),
                         model=MODEL, temperature=0.3, timeout=60)
    if not isinstance(data, dict):
        return empty

    text = (data.get('summary') or '').strip().strip('"').strip('「').strip('」')

    # 英語の商品名がそのまま残ることがある（例:「Hobonichi Techo」）。
    # 指示だけでは防ぎきれないため、残っていたら1度だけ書き直させる。
    if text and _has_foreign_words(text):
        retry = llm.chat(
            '次の日本語文から、アルファベット表記の商品名・ブランド名を取り除いてください。\n'
            '日本語の一般名詞に置き換えるか、省いて自然な文にしてください。\n'
            'IT・AI・EC・DXなど日本語として定着した略語はそのままで構いません。\n'
            '文字数と文体は変えず、書き直した文だけを出力してください。\n\n'
            f'{text}',
            model=MODEL, temperature=0.2, timeout=30)
        if retry and not _has_foreign_words(retry):
            text = retry.strip().strip('"').strip('「').strip('」')

    # 一覧に無い名前を作ることがあるため、必ずマスタと突き合わせて捨てる
    allowed = set(themes)
    picked = [t for t in (data.get('themes') or []) if isinstance(t, str) and t in allowed]

    return {'summary': text or None, 'themes': picked[:MAX_TAGS]}
