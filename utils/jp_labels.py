# pip install beautifulsoup4 requests
import re
import requests
from bs4 import BeautifulSoup

UA = {"User-Agent": "Mozilla/5.0 (compatible; stock-report-bot/1.0)"}

def fetch_jp_labels(symbol: str):
    """
    日本株(.T)のときに Yahoo!ファイナンス日本版から
    会社名（日本語）と業種（日本語）を取得。
    戻り値: (name_jp, industry_jp) どちらか取れなければ None
    """
    if not symbol.endswith(".T"):
        return None, None

    try:
        url = f"https://finance.yahoo.co.jp/quote/{symbol}"
        response = requests.get(url, headers=UA, timeout=15)
        response.raise_for_status()
        html = response.text
        soup = BeautifulSoup(html, "html.parser")

        # 会社名（ページ上部の見出しなど）
        name_jp = None
        h1 = soup.find("h1")
        if h1:
            name_jp = h1.get_text(" ", strip=True)
            # 「の株価・株式情報」「株価・株式情報」を除去
            name_jp = re.sub(r'の?株価・株式情報$', '', name_jp).strip()

        # 業種（「業種：建設業」などのテキストを横断抽出）
        industry_jp = None
        text = soup.get_text(" ", strip=True)
        m = re.search(r"業種[:：]\s*([^\s/|・]+)", text)
        if m:
            industry_jp = m.group(1)

        return name_jp, industry_jp
    
    except Exception as e:
        print(f"日本語ラベル取得エラー ({symbol}): {str(e)}")
        return None, None