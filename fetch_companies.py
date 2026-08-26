"""JPX上場企業リストを取得してstatic/companies.jsonに保存するスクリプト"""
import json
import pandas as pd
import requests
import os

from security_filter import is_non_operating_segment, is_class_share

# JPX上場企業一覧（Excel）
URL = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"

def fetch_and_save():
    print("JPXから上場企業リストを取得中...")
    response = requests.get(URL)
    response.raise_for_status()

    # 一時ファイルに保存してpandasで読み込み
    tmp_path = "tmp_jpx_data.xls"
    with open(tmp_path, "wb") as f:
        f.write(response.content)

    df = pd.read_excel(tmp_path)
    os.remove(tmp_path)

    print(f"取得件数: {len(df)}")
    print(f"カラム: {list(df.columns)}")

    # 銘柄コードと会社名を抽出。
    # ETF・ETN・REIT等は事業会社ではないので、この時点で落とす。
    # ここで落とせば検索サジェスト・分析対象・スクリーナーの全部から一度に消える。
    # 判定はJPXの「市場・商品区分」を使う（銘柄名からの推測より正確）。
    companies = []
    skipped = 0
    for _, row in df.iterrows():
        code = str(row.get("コード", "")).strip()
        name = str(row.get("銘柄名", "")).strip()
        segment = str(row.get("市場・商品区分", "")).strip()
        if not (code and name and code != "nan" and name != "nan"):
            continue
        # 種類株式（優先株・社債型）は会社ではないので入れない。
        # JPXは普通株と同じ「プライム（内国株式）」に入れるため、区分では分けられず
        # コードの桁数でしか見分けられない。
        if is_class_share(code):
            continue
        if is_non_operating_segment(segment):
            skipped += 1
            continue
        companies.append({"c": code, "n": name})
    print(f"除外（ETF・ETN・REIT等）: {skipped}件")

    # 銘柄コード順にソート
    companies.sort(key=lambda x: x["c"])

    # static/companies.json に保存
    os.makedirs("static", exist_ok=True)
    output_path = os.path.join("static", "companies.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(companies, f, ensure_ascii=False, separators=(",", ":"))

    size_kb = os.path.getsize(output_path) / 1024
    print(f"保存完了: {output_path} ({len(companies)}件, {size_kb:.1f}KB)")

if __name__ == "__main__":
    fetch_and_save()
