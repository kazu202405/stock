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
# ⚠️ JPXの生の区分名と、こちらで短くしたラベルの**両方**を並べる。
#    sync_market_segments.py は 'REIT等' という短い名前で保存するが、
#    過去に取り込んだ行には生の区分名が入っている。片方だけにすると、
#    保存の仕方が変わった瞬間に判定がすり抜ける（実際に一度やった）。
NON_OPERATING_SEGMENTS = (
    'ETF・ETN',
    'REIT・ベンチャーファンド・カントリーファンド・インフラファンド',
    'REIT等',
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
    # 種類株式（優先株・社債型）。**会社ではない。**
    # JPXは普通株と同じ「プライム（内国株式）」に入れるので区分では分けられない。
    # 見分けは**5桁の数字コード**（普通株は4桁）。2026-08-26 時点でJPXの5桁は
    # 7件あり、7件とも優先株か社債型種類株式で、普通株は1つも無い。
    # これを外さないと「ゼンショーホールディングス第１回社債型種類株式」が
    # 業種=小売業・株価4,919円で並び、ゼンショーを調べた人が当たりうる。
    '75505',  # ゼンショーホールディングス第１回社債型種類株式
    '92025',  # ＡＮＡホールディングス第１回社債型種類株式

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

    コードの列挙（EXCLUDED_CODES）と市場区分の**両方**で外す。
    列挙は手で足すものなので必ず取りこぼす。実際 8963 インヴィンシブル投資法人と
    8987 Japan Excellent, Inc. が漏れていた（後者は英語名なので
    NON_OPERATING_KEYWORDS の '投資法人' にも掛からない）。

    ⚠️ 区分の条件を `not_.in_()` だけで書かないこと。SQLでは
       `NULL NOT IN (...)` が真にならないため、**区分がまだ入っていない行が
       まとめて消える**。実測で37件が巻き込まれた。
       「区分が空」または「非事業区分でない」の or で書く。
    """
    if EXCLUDED_CODES:
        query = query.not_.in_(column, list(EXCLUDED_CODES))
    if NON_OPERATING_SEGMENTS:
        quoted = ','.join('"%s"' % s for s in NON_OPERATING_SEGMENTS)
        query = query.or_('market_segment.is.null,'
                          'market_segment.not.in.(%s)' % quoted)
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


def is_class_share(code) -> bool:
    """種類株式（優先株・社債型）なら True。

    JPXの銘柄コードは普通株が4桁。**5桁は種類株式**で、会社そのものではない。
    2026-08-26 時点のJPX一覧にある5桁7件は、伊藤園第１種優先株式・
    ソフトバンク第１回社債型種類株式など、すべて種類株式だった。

    取り込みの入口で弾くために使う。EXCLUDED_CODES は既に入ってしまった
    ぶんの手当てで、こちらは新しく増えないようにするためのもの。
    """
    text = str(code or '').strip()
    return len(text) == 5 and text.isdigit()


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
