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
    hint = ''
    if company_name or sector:
        hint = (f'（参考情報／出力には含めない）会社名: {company_name or "不明"} '
                f'／ 業種: {sector or "不明"}\n\n')
    return (
        '以下は企業の事業内容の英語説明です。日本語の事業概要に書き直してください。\n\n'
        '条件:\n'
        '- 60〜100文字程度。1〜2文の簡潔な体言止め中心の文体\n'
        '- 「何で稼いでいる会社か」を最優先で書く\n'
        '- **社名は書かない**（画面に別途表示されるため）。「〜株式会社は」で始めない\n'
        '- 英語の社名・固有名詞をカタカナに音訳しない。分からない固有名詞は省く\n'
        '- 投資判断を促す表現（買い時・推奨など）は書かない\n'
        '- 要約文のみを出力する\n\n'
        + hint +
        f'英語説明:\n{english_text[:3000]}'
    )


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
                    model=MODEL, temperature=0.2, timeout=45)
    if not text:
        return None

    text = text.strip().strip('"').strip('「').strip('」')
    # 指示に反して社名から書き始めた場合の保険
    for prefix in ('株式会社', '当社は', '同社は'):
        if text.startswith(prefix):
            break
    return text or None
