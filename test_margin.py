import requests
import re

# Yahoo!ファイナンス日本版から信用倍率を取得
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}

# トヨタ自動車のページ
url = 'https://finance.yahoo.co.jp/quote/7203'
response = requests.get(url, headers=headers)

if response.status_code == 200:
    text = response.text
    
    # 信用関連のキーワードを探す
    keywords = ['信用買残', '信用売残', '信用倍率', '買残', '売残']
    
    for keyword in keywords:
        if keyword in text:
            idx = text.find(keyword)
            print(f"\n'{keyword}' found at position {idx}")
            # 前後200文字を表示
            start = max(0, idx - 100)
            end = min(len(text), idx + 200)
            context = text[start:end]
            # HTMLタグを簡易的に表示
            context = context.replace('\n', ' ').replace('\t', ' ')
            print(f"Context: ...{context}...")
            
    # より具体的なパターンマッチング
    patterns = [
        # パターン1: dt/dd形式（柔軟版）
        (r'信用買残.*?</dt>\s*<dd[^>]*>([^<]+)</dd>', '信用買残'),
        (r'信用売残.*?</dt>\s*<dd[^>]*>([^<]+)</dd>', '信用売残'),
        (r'信用倍率.*?</dt>\s*<dd[^>]*>([^<]+)</dd>', '信用倍率'),
    ]
    
    print("\n\nパターンマッチング結果:")
    for pattern, name in patterns:
        match = re.search(pattern, text, re.DOTALL)
        if match:
            print(f"{name}: {match.group(1)}")
else:
    print(f"Error: {response.status_code}")