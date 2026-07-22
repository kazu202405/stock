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
               .select('name, category, description')
               .eq('kind', 'theme')
               .eq('tagging_enabled', True)
               .order('sort_order')
               .execute())
        rows = res.data or []
        _tag_cache['names'] = [r['name'] for r in rows]
        _tag_cache['by_category'] = rows
    except Exception as e:
        print(f'テーマ一覧の取得エラー: {e}')
        _tag_cache['names'] = []
        _tag_cache['by_category'] = []
    return _tag_cache['names']


def format_theme_list(themes):
    """候補をカテゴリ別に整形し、定義のあるものには説明を添える。

    200件を一列に並べるとモデルが見落とし、本業のテーマすら拾わなくなる
    （日立・ニトリでタグ0件になった）。カテゴリで区切ると走査しやすい。

    また、意味が広い語には判定が引き寄せられる（実測で「専門商社」が7銘柄中
    4件に付き、メーカーにも誤付与された）。誤りやすいものは定義を並記して
    境界を示す。
    """
    rows = _tag_cache.get('by_category') or []
    allowed = set(themes)
    grouped = {}
    notes = []
    for r in rows:
        if r['name'] not in allowed:
            continue
        grouped.setdefault(r.get('category') or 'その他', []).append(r['name'])
        if r.get('description'):
            notes.append(f"  ・{r['name']}: {r['description']}")

    if not grouped:
        return '、'.join(themes)

    body = '\n'.join(f'[{cat}] ' + '、'.join(names) for cat, names in grouped.items())
    if notes:
        body += '\n\n【間違えやすいテーマの定義】\n' + '\n'.join(notes)
    return body


def build_prompt(english_text, company_name=None, sector=None, themes=None,
                 code_hint=None, industry=None):
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
    # 社名と銘柄コードは先頭に置く。
    # 「英語説明に無いことは書くな」という制約が、モデルが持っている企業知識まで
    # 封じてしまい、ニトリ・日立でタグが1つも付かなかった。
    # 文章は英語説明の範囲に限り、テーマ判定には知識を使わせる、と役割を分ける。
    ident = f'【対象企業】{company_name or "不明"}（銘柄コード {code_hint or "不明"}）\n'
    if industry:
        # JPXが公表している事実データ。英語説明より信頼できるので、
        # 概要の1文目とテーマの両方をこれと矛盾させない
        ident += (f'東証の業種分類: {industry}\n'
                  '　※これは取引所が公表している確定情報です。'
                  '事業概要の類型もテーマも、これと矛盾しないようにしてください\n')
    elif sector:
        ident += f'（参考）業種分類: {sector}\n'

    theme_block = ''
    if themes:
        theme_block = (
            '\n【テーマ候補】カテゴリごとに並べています。\n'
            'この一覧から、その企業の事業として実際に該当するものを選んでください。\n'
            f'- 1〜{MAX_TAGS}個\n'
            '- **まず本業を必ず入れる。'
            '上で書いた事業概要の1文目の類型に対応するテーマを、一覧から必ず探して選ぶ**\n'
            '- **東証の業種分類と同じ意味のテーマが一覧にあれば、それも必ず選ぶ**。'
            '業種を伝えているのは判断材料にするためで、重複を避けさせるためではない\n'
            '    例) 業種が「電気機器」なら一覧の「電気機器」を選ぶ\n'
            '    例) 業種が「鉄鋼」なら一覧の「鉄鋼」を選ぶ\n'
            '    例) 概要を「衣料品のメーカー」と書いたなら「アパレル」を入れる\n'
            '    例) 概要を「厨房機器のメーカー」と書いたなら「機械」を入れる\n'
            '    例) 概要を「金融持株会社」と書いたなら「銀行」など該当する金融のテーマを入れる\n'
            '- 次に、副次的な事業として説明文に明記があるものを加える\n'
            '- **テーマ判定に限り、あなたがこの企業について知っている一般的な知識を'
            '使ってよい**。英語説明が短くても、社名と銘柄コードから業態が分かるなら'
            'それに基づいて選ぶ（ただし憶測での多用は避け、確信のあるものだけ）\n'
            '- 一覧に無い名前を作らない（表記を1文字も変えない）\n'
            '- **「作っている物」と「売り先・納入先」を混同しない**\n'
            '    誤りの例: 調味料メーカーに「食品スーパー」「外食」を付ける\n'
            '      → その企業がスーパーや飲食店を運営しているわけではない。正しくは「食品」\n'
            '    誤りの例: 産業機械メーカーに、納入先の業界のテーマを付ける\n'
            '- 関連しそう・将来性がありそう、という理由では広げない\n'
            '- どうしても該当が無い場合のみ空の配列にしてよい\n\n'
            + format_theme_list(themes) + '\n'
        )

    return (
        'あなたは企業分析の解説者です。次の企業について、'
        '日本語の事業概要と、該当する事業テーマを判定してください。\n\n'
        + ident +
        '\n【事業概要の書き方】\n'
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
        '{"summary": "事業概要", "themes": ["テーマ名", ...]}\n'
        f'\n英語の事業説明:\n{english_text[:2500]}'
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


def generate(code, company_name=None, sector=None, english_text=None, themes=None,
             industry=None):
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

    data = llm.chat_json(build_prompt(english, company_name, sector, themes,
                                      code_hint=code, industry=industry),
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

    # 0件はほぼ誤り。日立・KDDI・日本製鉄という誰が見ても分類できる企業で
    # 0件になった実績があるため、本業だけに絞ってもう一度だけ聞き直す。
    # 全体の一部でしか発火しないので費用への影響は小さい。
    if not picked and text:
        picked = _retry_themes(llm, text, company_name, industry, themes, allowed)

    return {'summary': text or None, 'themes': picked[:MAX_TAGS]}


def generate_themes_only(summary_text, company_name=None, industry=None, themes=None):
    """既にある日本語概要から、テーマだけを付け直す。

    マスタにテーマを足すたびに全銘柄を作り直すと、英語説明の再取得と
    長いプロンプトで2.5時間かかる。概要が既にあるならそれを使えば、
    取得が不要になり入力も短くなる。
    """
    import llm
    if not llm.is_available() or not summary_text:
        return []
    if themes is None:
        themes = load_taggable_themes()
    return _retry_themes(llm, summary_text, company_name, industry,
                         themes, set(themes), limit=MAX_TAGS)


def _retry_themes(llm, summary_text, company_name, industry, themes, allowed,
                  limit=3):
    """テーマが1つも選ばれなかったときに、本業だけを聞き直す"""
    data = llm.chat_json(
        f'次の企業に当てはまるテーマを、一覧から1〜{limit}個だけ選んでください。\n'
        '本業を最優先し、次に説明文に明記のある副次的な事業を加えてください。\n'
        '該当が無いということはまずありません。最も近いものを必ず選んでください。\n'
        '一覧に無い名前を作らず、表記も1文字も変えないでください。\n\n'
        f'企業: {company_name or "不明"}\n'
        f'東証の業種分類: {industry or "不明"}\n'
        f'事業概要: {summary_text}\n\n'
        '【一覧】\n' + format_theme_list(themes) + '\n\n'
        '次のJSONのみを出力してください:\n{"themes": ["テーマ名", ...]}',
        model=MODEL, temperature=0.1, timeout=45)
    if not isinstance(data, dict):
        return []
    return [t for t in (data.get('themes') or [])
            if isinstance(t, str) and t in allowed]
