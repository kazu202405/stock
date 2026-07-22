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


def build_prompt(english_text, company_name=None, sector=None):
    """英文の逐語訳ではなく「何で稼ぐ会社か」が掴める説明を書かせる。

    プロンプト設計の経緯:
      - 単に翻訳させると事業分野の羅列になり、読んでも会社像が結ばなかった
      - 時価総額や成長率を渡して補強を試みたが、業績推移の欄と内容が重複し
        文章が冗長になるだけで逆効果だった
      - 情報を足すより「会社の類型を最初に言い切る」「誰に売るかを書く」と
        型を絞るほうが効いた
        （例: 「厨房機器の製造・販売」→「商業用厨房機器のメーカー。
              政府・医療・ホテル向けに設計や施工管理も」）
    """
    hint = f'（参考／出力には含めない）業種: {sector or "不明"}\n\n' if sector else ''
    return (
        'あなたは企業分析の解説者です。次の英語の事業説明から、日本語の事業概要を書いてください。\n\n'
        '狙い: 一読して「何を売って、誰から、どう稼ぐ会社か」が掴めること。\n\n'
        '書き方:\n'
        '- 全体で60〜90文字。体言止め中心\n'
        '- 1文目: 会社の類型を最初に言い切る\n'
        '    （例: 〜のメーカー / 〜の専門商社 / 受託開発 / 店舗運営 / サブスク型 など）\n'
        '- 2文目: 主な顧客や用途、事業の広がりを一言\n'
        '- 事業分野を羅列しない。重要な2〜3個に絞る\n'
        '- 成長率や財務数値は書かない（別欄に表示されるため）\n'
        '- 社名は書かない。英語の固有名詞は音訳しない\n'
        '- シェア・業界順位・「最大手」など、英語説明に無い評価は書かない\n'
        '- 投資判断を促す表現は書かない\n'
        '- 要約文のみ出力\n\n'
        + hint +
        f'英語説明:\n{english_text[:2500]}'
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


def generate(code, company_name=None, sector=None, english_text=None):
    """1銘柄の日本語事業概要を生成する。失敗時は None。"""
    import llm
    if not llm.is_available():
        return None

    english = english_text or fetch_english_summary(code)
    if not english or len(english) < 40:
        return None

    text = llm.chat(build_prompt(english, company_name, sector),
                    model=MODEL, temperature=0.3, timeout=45)
    if not text:
        return None

    text = text.strip().strip('"').strip('「').strip('」')

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

    return text or None
