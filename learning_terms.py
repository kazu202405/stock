"""学習ノートの項目一覧（IDの正本）。

解説文は `templates/learning.html` が持つ。ユーザーごとに変わらない教科書の
本文をDBやサーバーに持たせても運用が重くなるだけなので、そこは分けている。

ここに置くのはIDとカテゴリだけで、用途は2つ。

  1. 進捗APIに渡された term_id が実在する項目か検証する
     （検証しないと、任意の文字列を送って理解済み件数を水増しできてしまう）
  2. 「何項目中いくつ理解したか」の分母を出す

learning.html 側の terms[].id とここは必ず一致していなければならない。
ズレたら `tests/test_learning_progress.py` が落ちる。
"""

# (term_id, category_id)
LEARNING_TERMS = (
    ('market_cap', 'scale'),
    ('equity_ratio', 'safety'),
    ('payout_ratio', 'safety'),
    ('revenue_growth', 'growth'),
    ('revenue_growth_forecast', 'growth'),
    ('op_growth', 'growth'),
    ('op_growth_forecast', 'growth'),
    ('shareholder_structure', 'growth'),
    ('operating_margin', 'profitability'),
    ('roa', 'profitability'),
    ('operating_cf', 'cashflow'),
    ('free_cf', 'cashflow'),
    ('per', 'valuation'),
    ('pbr', 'valuation'),
    ('moving_average', 'technical'),
    ('golden_cross', 'technical'),
    ('dead_cross', 'technical'),
    ('dca', 'strategy'),
    # 用語解説②（指標を分解して読む）。既存の roa は「ROAとは何か」、
    # roa_breakdown は「利益率 × 回転率に分けて読む」で役割を分けている。
    ('roa_breakdown', 'decompose'),
    ('roe', 'decompose'),
)

TERM_IDS = frozenset(term_id for term_id, _ in LEARNING_TERMS)

CATEGORY_IDS = (
    'scale', 'safety', 'growth', 'profitability',
    'cashflow', 'valuation', 'technical', 'strategy',
    'decompose',
)


def is_valid_term(term_id) -> bool:
    return term_id in TERM_IDS


def total_terms() -> int:
    return len(LEARNING_TERMS)
