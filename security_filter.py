"""事業会社でない銘柄（ETF・ETN・REIT等）を除外する判定を1か所に集める。

なぜ除外するか:
  Company Note は「会社を見る目」を育てるアプリで、見るのは事業・経営・財務。
  ETFやREITには事業も経営者も競争優位もなく、12項目のスコアも1〜2項目しか
  判定できない。中身の無いページが検索やスクリーナーに混ざると、
  「調べたのに何も出てこない」という体験になる。

判定の優先順位:
  1. JPXの「市場・商品区分」（最も正確。fetch_companies.py で使う）
  2. 銘柄名のキーワード（区分が手元に無い場面のための保険）

キーワード判定は名前だけが頼りなので、事業会社を巻き込まないか必ず実データで
確認すること（2026-07-30 時点では screened_latest 3,878件に対し誤検出0件）。
"""

# JPXの「市場・商品区分」のうち、事業会社でないもの。
# PRO Market・出資証券・外国株式は事業会社なので除外しない。
NON_OPERATING_SEGMENTS = (
    'ETF・ETN',
    'REIT・ベンチャーファンド・カントリーファンド・インフラファンド',
)

# 区分が取れない場面のための保険。銘柄名に含まれていたら非事業会社とみなす。
#
# ここは意図的に「狭く」してある。短い語を入れると実在企業を巻き込むため。
# 実際に確認した誤検出（2026-07-30）:
#   'リート' → 旭コンクリート工業・日本コンクリート工業（コンク"リート"）
#   'ブル'   → ブルボン・ブルドックソース・ダブルツリー・ブルーイノベーション
#   'ベア'   → ミネベアミツミ
#   'インバース' → リファインバースグループ（リファ"インバース"）
# 判定の本命はJPXの市場・商品区分。こちらは取りこぼしより誤検出を避ける方を優先する。
NON_OPERATING_KEYWORDS = (
    'ETF', 'ＥＴＦ', 'ETN', 'ＥＴＮ',
    'REIT', 'ＲＥＩＴ', '投資法人',
    '上場投信', '上場信託', '上場インデックスファンド',
    'インデックスファンド', 'インデックス・ファンド',
    '投資信託', '連動型上場',
    'ダブル・インバース', 'レバレッジ',
    'ＮＥＸＴ　ＦＵＮＤＳ', 'NEXT FUNDS',
    'ｉＦｒｅｅ', 'iFree', 'ＳＰＤＲ', 'SPDR', 'ＭＡＸＩＳ', 'MAXIS',
    'ｉシェアーズ', 'iShares', 'Ｔｒａｃｅｒｓ', 'Tracers',
    '純金上場', '純プラチナ', '純銀上場', '純パラジウム',
)


# 表示から外す銘柄コード。**DBの行は消さない**。
# 消すと元に戻せないが、ここに書いてあるだけなら1行消せば復活する。
# 入口(companies.json)はJPXの区分で除外済みなので、ここに載るのは
# 過去に取り込んでしまった残りだけ。
EXCLUDED_CODES = (
    '1305',  # iFreeETF TOPIX（年1回決算型）
    '1306',  # NEXT FUNDS TOPIX連動型上場投信
    '1309',  # NEXT FUNDS ChinaAMC・中国株式・上証50
    '1326',  # SPDRゴールド・シェア
    '1540',  # 純金上場信託
    '1541',  # 純プラチナ上場信託
    '1542',  # 純銀上場信託
    '1543',  # 純パラジウム上場信託
    '2093',  # 上場Tracers 米国債0-2年ラダー
    '3290',  # Oneリート投資法人
    '3472',  # 日本ホテル＆レジデンシャル投資法人
)


def exclude_non_operating(query, column='company_code'):
    """Supabaseのクエリに「事業会社でないものを除く」条件を足す。

    Python側で絞ると件数とページングが狂うので、必ずDB側で外す。
    """
    if EXCLUDED_CODES:
        query = query.not_.in_(column, list(EXCLUDED_CODES))
    return query


def is_non_operating_segment(market_segment) -> bool:
    """JPXの市場・商品区分から判定する（最も正確）"""
    return str(market_segment or '').strip() in NON_OPERATING_SEGMENTS


def is_non_operating_name(name) -> bool:
    """銘柄名から判定する（区分が無いときの保険）"""
    n = str(name or '')
    return any(k in n for k in NON_OPERATING_KEYWORDS)


def is_non_operating(name=None, market_segment=None) -> bool:
    """事業会社でない銘柄なら True。区分が分かるならそちらを優先する。"""
    if market_segment is not None and str(market_segment).strip():
        return is_non_operating_segment(market_segment)
    return is_non_operating_name(name)


def exclude_delisted(query, column='delisted_at'):
    """上場廃止の銘柄を一覧から外す条件を足す。

    行は消さない。印を消せば戻る（ETFの EXCLUDED_CODES と同じ考え方）。
    列がまだ無い（migration 未適用）ときは何もしない。条件を足すと
    クエリが400で落ちて一覧が丸ごと表示されなくなるため。
    """
    import supabase_client as sc
    if sc.has_column('screened_latest', column):
        query = query.is_(column, 'null')
    return query
