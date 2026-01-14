# 日本企業情報スクレイパー
# j-lic.com（役員）、strainer.jp（大株主）、Yahoo Japan（事業概要）から取得

import requests
from bs4 import BeautifulSoup
import re
import time

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'ja,en-US;q=0.7,en;q=0.3',
}


def get_officers_from_jlic(stock_code: str) -> list:
    """
    j-lic.comから役員情報を取得

    Args:
        stock_code: 銘柄コード（4桁）

    Returns:
        list: 役員情報リスト
    """
    code = stock_code.replace('.T', '').strip()
    officers = []

    try:
        url = f'https://j-lic.com/companies/{code}/directors'
        print(f'役員データ取得中: {url}')

        response = requests.get(url, headers=HEADERS, timeout=15)
        response.encoding = 'utf-8'

        if response.status_code != 200:
            print(f'役員データ取得失敗: HTTP {response.status_code}')
            return officers

        soup = BeautifulSoup(response.text, 'html.parser')

        # テーブルから役員情報を抽出
        for table in soup.find_all('table'):
            rows = table.find_all('tr')

            # ヘッダー行をスキップ
            for row in rows[1:]:
                cells = row.find_all(['th', 'td'])
                if len(cells) >= 3:
                    # #, 氏名, 役職名, 生年月日, 略歴, 任期, 所有株式数
                    try:
                        idx = cells[0].get_text(strip=True)
                        if not idx.isdigit():
                            continue

                        name = cells[1].get_text(strip=True)
                        title = cells[2].get_text(strip=True)
                        birth_date = cells[3].get_text(strip=True) if len(cells) > 3 else None
                        bio = cells[4].get_text(strip=True)[:200] if len(cells) > 4 else None
                        shares = cells[6].get_text(strip=True) if len(cells) > 6 else None

                        # 株式数をパース
                        shares_num = None
                        if shares:
                            match = re.search(r'普通株式([\d,]+)', shares)
                            if match:
                                shares_num = int(match.group(1).replace(',', ''))

                        officers.append({
                            'name': name,
                            'title': title,
                            'birth_date': birth_date,
                            'bio': bio,
                            'shares': shares_num
                        })
                    except Exception:
                        continue

        print(f'役員データ取得成功: {len(officers)}名')

    except requests.exceptions.Timeout:
        print('役員データ取得タイムアウト')
    except Exception as e:
        print(f'役員データ取得エラー: {str(e)}')

    return officers


def get_shareholders_from_strainer(stock_code: str) -> list:
    """
    strainer.jpから大株主情報を取得

    Args:
        stock_code: 銘柄コード（4桁）

    Returns:
        list: 大株主情報リスト（最新データ）
    """
    code = stock_code.replace('.T', '').strip()
    shareholders = []

    try:
        url = f'https://strainer.jp/companies/JP-{code}/ownership'
        print(f'大株主データ取得中: {url}')

        response = requests.get(url, headers=HEADERS, timeout=15)
        response.encoding = 'utf-8'

        if response.status_code != 200:
            print(f'大株主データ取得失敗: HTTP {response.status_code}')
            return shareholders

        soup = BeautifulSoup(response.text, 'html.parser')

        # テーブルから大株主情報を抽出
        for table in soup.find_all('table'):
            rows = table.find_all('tr')

            if len(rows) < 2:
                continue

            # ヘッダー行から最新の列を特定
            header_cells = rows[0].find_all(['th', 'td'])
            if len(header_cells) < 2:
                continue

            # 最新の年度列を特定（右端が最新）
            latest_col_idx = len(header_cells) - 1

            # データ行を処理
            for row in rows[1:]:
                cells = row.find_all(['th', 'td'])
                if len(cells) < 2:
                    continue

                try:
                    name = cells[0].get_text(strip=True)

                    # 単位行はスキップ
                    if '単位' in name:
                        continue

                    # 最新列の値を取得
                    latest_value = cells[latest_col_idx].get_text(strip=True) if latest_col_idx < len(cells) else ''

                    if not latest_value or latest_value == '-':
                        continue

                    # 「計」行はスキップ
                    if name == '計':
                        continue

                    # 株数と比率をパース（例: "108,84716.79%", "21,3783.3%"）
                    # フォーマット: 株数(千株、カンマ区切り)+比率(小数)%
                    # カンマ後は [3桁の株数下位] + [比率] の構造

                    val = latest_value.replace(' ', '').rstrip('%')

                    # 最後のカンマ位置を特定（株数の千の位区切り）
                    last_comma = val.rfind(',')
                    if last_comma == -1:
                        continue

                    # カンマの前後を分離
                    shares_upper = val[:last_comma].replace(',', '')  # 上位桁
                    after_comma = val[last_comma+1:]  # "84716.79"

                    # カンマ後の構造: 最初の3桁が株数下位、残りが比率
                    if len(after_comma) < 4:  # 最低 "XXX.X" の形式
                        continue

                    shares_lower = after_comma[:3]  # 株数の下位3桁
                    ratio_str = after_comma[3:]     # 比率部分 "16.79"

                    try:
                        shares = int(shares_upper + shares_lower) * 1000  # 千株単位
                        ratio = float(ratio_str)

                        if ratio <= 0 or ratio > 100:
                            continue
                    except ValueError:
                        continue

                    if ratio is not None:
                        shareholders.append({
                            'name': name,
                            'shares': shares,
                            'ratio': ratio
                        })
                except Exception:
                    continue

        print(f'大株主データ取得成功: {len(shareholders)}名')

    except requests.exceptions.Timeout:
        print('大株主データ取得タイムアウト')
    except Exception as e:
        print(f'大株主データ取得エラー: {str(e)}')

    return shareholders


def get_yahoo_japan_profile(stock_code: str) -> dict:
    """
    Yahoo Finance Japanから企業情報を取得
    """
    code = stock_code.replace('.T', '').strip()
    symbol = f'{code}.T'

    result = {
        'business_summary_jp': None,
        'business_segments': None,
        'headquarters': None,
        'ceo_name': None,
        'established': None,
        'industry': None,
        'market': None,
        'employees': None,
        'average_age': None,
        'average_salary': None,
        'error': None
    }

    try:
        url = f'https://finance.yahoo.co.jp/quote/{symbol}/profile'
        print(f'Yahoo Japan データ取得中: {url}')

        response = requests.get(url, headers=HEADERS, timeout=15)
        response.encoding = 'utf-8'

        if response.status_code != 200:
            result['error'] = f'HTTP {response.status_code}'
            return result

        soup = BeautifulSoup(response.text, 'html.parser')

        for table in soup.find_all('table'):
            for tr in table.find_all('tr'):
                cells = tr.find_all(['th', 'td'])
                if len(cells) >= 2:
                    label = cells[0].get_text(strip=True)
                    value = cells[1].get_text(strip=True)

                    if '特色' in label:
                        result['business_summary_jp'] = re.sub(r'^【特色】', '', value)
                    elif '連結事業' in label:
                        result['business_segments'] = re.sub(r'^【連結事業】', '', value)
                    elif '本社所在地' in label:
                        result['headquarters'] = re.sub(r'Yahoo!.*$', '', value).strip()
                    elif '代表者名' in label:
                        result['ceo_name'] = value
                    elif '設立年月日' in label:
                        result['established'] = value
                    elif '業種分類' in label:
                        result['industry'] = value
                    elif '市場名' in label:
                        result['market'] = value
                    elif '従業員数' in label:
                        result['employees'] = value
                    elif '平均年齢' in label:
                        result['average_age'] = value
                    elif '平均年収' in label:
                        result['average_salary'] = value

        print(f'Yahoo Japan データ取得成功')

    except Exception as e:
        result['error'] = str(e)
        print(f'Yahoo Japan データ取得エラー: {str(e)}')

    return result


def get_all_jp_company_data(stock_code: str) -> dict:
    """
    日本企業の全情報を取得（統合関数）

    Args:
        stock_code: 銘柄コード

    Returns:
        dict: 統合された企業情報
    """
    code = stock_code.replace('.T', '').strip()

    result = {
        'company_code': f'{code}.T',
        'business_summary_jp': None,
        'business_segments': None,
        'headquarters_jp': None,
        'ceo_name_jp': None,
        'established': None,
        'industry_jp': None,
        'employees_jp': None,
        'average_salary_jp': None,
        'officers_jp': [],
        'major_shareholders_jp': [],
        'error': None
    }

    try:
        # 1. Yahoo Japanから基本情報を取得
        yahoo_data = get_yahoo_japan_profile(code)
        if not yahoo_data.get('error'):
            result['business_summary_jp'] = yahoo_data.get('business_summary_jp')
            result['business_segments'] = yahoo_data.get('business_segments')
            result['headquarters_jp'] = yahoo_data.get('headquarters')
            result['ceo_name_jp'] = yahoo_data.get('ceo_name')
            result['established'] = yahoo_data.get('established')
            result['industry_jp'] = yahoo_data.get('industry')
            result['employees_jp'] = yahoo_data.get('employees')
            result['average_salary_jp'] = yahoo_data.get('average_salary')

        time.sleep(0.5)  # サーバー負荷軽減

        # 2. j-lic.comから役員情報を取得
        officers = get_officers_from_jlic(code)
        if officers:
            result['officers_jp'] = officers

        time.sleep(0.5)

        # 3. strainer.jpから大株主情報を取得
        shareholders = get_shareholders_from_strainer(code)
        if shareholders:
            result['major_shareholders_jp'] = shareholders

    except Exception as e:
        result['error'] = str(e)

    return result


# テスト用
if __name__ == '__main__':
    import json

    test_code = '1928'
    print(f'\n=== {test_code} ===')
    data = get_all_jp_company_data(test_code)

    print('\n--- 結果 ---')
    print(f'事業概要: {data["business_summary_jp"][:50] if data["business_summary_jp"] else "なし"}...')
    print(f'役員数: {len(data["officers_jp"])}名')
    if data['officers_jp']:
        for off in data['officers_jp'][:3]:
            print(f'  - {off["title"]}: {off["name"]}')
    print(f'大株主数: {len(data["major_shareholders_jp"])}社')
    if data['major_shareholders_jp']:
        for sh in data['major_shareholders_jp'][:5]:
            print(f'  - {sh["name"]}: {sh["ratio"]}%')
