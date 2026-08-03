"""公式開示で確認した企業プロフィールの軽量フォールバック。

外部サイトがレート制限・未収録・HTML変更で取得できない場合でも、確認済みの
公開情報を再分析で失わないためのキャッシュ。値は欠損項目だけに適用し、Yahoo等で
取得できた新しい値を上書きしない。
"""

from copy import deepcopy


OFFICIAL_COMPANY_PROFILES = {
    "164A": {
        "business_summary_jp": (
            "駐車場・駐輪場の総合プロデュース事業を展開。主に時間貸し駐車場・"
            "駐輪場を運営管理し、土地オーナーと利用者をつないでいます。運営モデルは、"
            "土地を借りて設備を設置・運営する「一括借上」と、オーナーから管理委託料を"
            "受けて運営実務を担う「管理委託」です。"
        ),
        "established": "1991-08-07",
        "listing_date": "2024-03-25",
        "market_jp": "TOKYO PRO Market",
        "industry_jp": "不動産業",
        "headquarters_jp": "東京都北区赤羽一丁目52番10号",
        "ceo_name_jp": "山中 直樹",
        "major_shareholders_jp": [
            {"name": "株式会社HARSU", "shares": 802000, "ratio": 60.12,
             "as_of": "2026-03-31"},
            {"name": "山中 直樹", "shares": 531900, "ratio": 39.87,
             "as_of": "2026-03-31"},
            {"name": "株式会社テレビ埼玉クリエイティブ", "shares": 100,
             "ratio": 0.01, "as_of": "2026-03-31"},
        ],
        "company_officers": [
            {"name": "山中 直樹", "name_jp": "山中 直樹",
             "title": "代表取締役", "title_jp": "代表取締役"},
            {"name": "上野 篤資", "name_jp": "上野 篤資",
             "title": "取締役", "title_jp": "取締役"},
            {"name": "佐竹 誠", "name_jp": "佐竹 誠",
             "title": "取締役 パーキング事業部長", "title_jp": "取締役 パーキング事業部長"},
            {"name": "松森 貴志", "name_jp": "松森 貴志",
             "title": "取締役 経営企画本部長", "title_jp": "取締役 経営企画本部長"},
            {"name": "小俣 亜紀", "name_jp": "小俣 亜紀",
             "title": "社外監査役", "title_jp": "社外監査役"},
        ],
        "sources": [
            "https://www.jpx.co.jp/equities/products/tpm/issues/"
            "mklp770000000ysr-att/mklp770000000yur.pdf",
            "https://assets.minkabu.jp/news/article_media_content/"
            "urn%3Anewsml%3Atdnet.info%3A20260601558043/140120260601558043.pdf",
        ],
    },
}


def get_official_company_profile(stock_code: str) -> dict:
    """銘柄コードに対応する確認済みプロフィールのコピーを返す。"""
    code = (stock_code or "").upper().replace(".T", "").strip()
    return deepcopy(OFFICIAL_COMPANY_PROFILES.get(code, {}))


def apply_official_profile_fallback(stock_code: str, result: dict) -> list:
    """result の欠損だけを公式プロフィールで補い、補完したキーを返す。"""
    profile = get_official_company_profile(stock_code)
    if not profile:
        return []

    filled = []
    for key, value in profile.items():
        if key == "sources" or value in (None, "", [], {}):
            continue
        if result.get(key) in (None, "", [], {}):
            result[key] = value
            filled.append(key)

    if filled:
        statuses = result.setdefault("source_status", {})
        statuses["official_profile"] = {
            "status": "success",
            "source": "JPX/会社公式開示（確認済みキャッシュ）",
            "filled": filled,
            "references": profile.get("sources", []),
        }
    return filled
