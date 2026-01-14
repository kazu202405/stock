from config import *
from flask import render_template, request, session, jsonify
from flask_login import login_required
from models.common import *
from models.model import *

import requests
import json
import time  # 再試行間の待機時間を設定するために使用
from concurrent.futures import ThreadPoolExecutor


# リクエスト用のヘッダーを設定
headers = {
    "Authorization": f"Bearer {DIFY_API_KEY}",
    "Content-Type": "application/json"
}

# チャットメッセージを送信する関数
def run_workflows(query, conversation_id=None, user="abc-123", add_info=[], retries=3, delay=2):
    endpoint = f"{BASE_URL}/workflows/run"
    
    payload = {
        "inputs": {},
        "query": query,
        "response_mode": "blocking",  # または "streaming"
        "conversation_id": conversation_id,
        "user": user
    }
    
    for info in add_info:
        payload["inputs"][info["key"]] = info["value"]
    
    response = requests.post(endpoint, headers=headers, json=payload)
    
    # 再試行を制御するループ
    for attempt in range(retries):
        response = requests.post(endpoint, headers=headers, json=payload)
        
        if response.status_code == 200:
            return response.json()  # 成功した場合は結果を返す
        else:
            print(f"Attempt {attempt + 1} failed with status code: {response.status_code}")
            print(response.text)
        
        # 次の試行まで待機 (失敗した場合)
        if attempt < retries - 1:
            time.sleep(delay)  # 2秒の遅延を入れる
    
    # 最大試行回数を超えたら None を返す
    print("All retries failed.")
    return None


def translate_key(key):
    translations = {
        'business_description': '事業の説明',
        'current_sales_profit_status': '現在の売上状況',
        'main_customer_segments': '主な顧客層',
        'promotion_methods_advertising_sales': '販促方法',
        'customer_needs': '顧客ニーズ',
        'market_trends': '市場動向',
        'future_forecast': '将来予測',
        'company_strengths': '会社の強み',
        'management_policy_goals': '経営方針・目標',
        'future_plan': '将来の計画',
        'features_of_the_introduced_items': '導入された項目の特徴',
        'specific_measures': '具体的な措置',
        'target_customer_segments_of_subsidy_project': '補助金プロジェクトのターゲット顧客層',
        "subsidy_project_overview": "補助金プロジェクト概要",
        'customer_problems': '顧客の課題',
        'expected_effects': '期待される効果',
        'implementation_and_project_schedule': '実施およびプロジェクトスケジュール',
        'effect_estimation': '効果見積もり'
    }
    return translations.get(key, key)


# フィルタを登録
app.jinja_env.filters['translate_key'] = translate_key

def expand_text(key, text):
    expansion_prompt = f"""
        入力されたテキストをより充実した内容にし文字数を2倍に拡張してしてください
        出力形式：2倍にした文章部分のみを出力。前書きや後書きは全く出力しない
        入力されたテキスト: {text}
    """
    return key, generate_text(expansion_prompt)

def process_infos(infos):
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(expand_text, key, text) 
                  for key, text in infos.items()]
        return dict(f.result() for f in futures)

@login_required
@app.route('/business_plan_preparation', methods=['GET', 'POST'])
def business_plan_preparation():
    """事業計画書作成ページ"""
    if request.method == 'POST':
        # フォームから入力された情報を取得
        company_name = request.form.get('company_name', '株式会社AzCreate')
        ceo_name = request.form.get('ceo_name', '杉野 一貴')
        establishment_date = request.form.get('establishment_date', '2024年2月7日')
        address = request.form.get('address', '大阪市中央区南船場3')
        servise = request.form.get('servise', 'システム開発 100%')
        target = request.form.get('target', '士業')
        pr = request.form.get('pr' , '口コミ')
        product = request.form.get('product', 'https://drive.google.com/file/d/1s2BGIyoFj0EP8BFO26JXZgnNAIkeNaey/view?usp=sharing')
        project = request.form.get('project', '業務効率化')
        project_target = request.form.get('project_target', '士業')
        market_info = request.form.get('market_info', 'https://www.nice2meet.us/life-saving-tools-in-work')
        company_supplementary_info = request.form.get('company_supplementary_info', 'https://cnavi.g-search.or.jp/detail/8120001262231.html')
        additional_info = request.form.get('additional_info', '')
        
        session['company_name'] = company_name
        session['ceo_name'] = ceo_name
        session['establishment_date'] = establishment_date
        session['address'] = address
        session['servise'] = servise
        session['target'] = target
        session['pr'] = pr
        session['product'] = product
        session['project'] = project
        session['project_target'] = project_target
        session['market_info'] = market_info
        session['company_supplementary_info'] = company_supplementary_info
        session['additional_info'] = additional_info
        
        # チャットボットに情報を送信
        add_info = [
            {"key": "company_name", "value": company_name},
            {"key": "ceo_name", "value": ceo_name},
            {"key": "establishment_date", "value": establishment_date},
            {"key": "address", "value": address},
            {"key": "servise", "value": servise},
            {"key": "target", "value": target},
            {"key": "pr", "value": pr},
            {"key": "product", "value": product},
            {"key": "project", "value": project},
            {"key": "project_target", "value": project_target},
            {"key": "market_info", "value": market_info},
            {"key": "company_supplementary_info", "value": company_supplementary_info},
            {"key": "additional_info", "value": additional_info}
        ]
        
        query = ""
        result = run_workflows(query, add_info=add_info)
        
        # 動的なタイトルやヘッダーを定義
        page_title = "事業計画書作成"
        header_title = "AI事業計画書作成"
        result_header = "生成された事業計画書の内容"
        
        response_text = result["data"]["outputs"]["text"]
        
        if "},\n{" in response_text:
            json_objects_all = response_text.split('},\n{')
            
        elif "},\n  {" in response_text:
            json_objects_all = response_text.split("},\n  {")
        
        infos = {}

        # 各オブジェクトにカッコをつけて完全なJSONオブジェクトにする
        for i, obj in enumerate(json_objects_all):
            obj = obj.replace('[', '')
            obj = obj.replace(']', '')
            # 先頭のオブジェクトには '{' を追加
            if i == 0:
                obj = obj + '}'
            # 最後のオブジェクトには '}' を追加
            elif i == len(json_objects_all) - 1:
                obj = '{' + obj
            # 中間のオブジェクトには '{' と '}' の両方を追加
            else:
                obj = '{' + obj + '}'
            
            # JSONに変換を試みる
            try:
                data = json.loads(obj)
                # print("変換後のデータ:", data)
                if len(data) == 2:
                    keys = list(data.keys())
                    infos[data[keys[0]]] = data[keys[1]]
                else:
                    print("エントリー数が2以外です")
            
            except json.JSONDecodeError as e:
                print(f"JSONの解析中にエラーが発生しました: {e}")   
        
        # テキストを拡張
        infos = process_infos(infos)
        
        return render_template(
            'business_plan_preparation.html',
            page_title=page_title,
            header_title=header_title,
            result_header=result_header,
            result=infos,
            company_name=company_name,
            ceo_name=ceo_name,
            establishment_date=establishment_date,
            address=address,
            servise=servise,
            target=target,
            pr=pr,
            product=product,
            project=project,
            project_target=project_target,
            market_info=market_info,
            company_supplementary_info=company_supplementary_info,
            additional_info=additional_info
        )

    if "company_name" in session:
        company_name = session.get('company_name')
        ceo_name = session.get('ceo_name')
        establishment_date = session.get('establishment_date')
        address = session.get('address')
        servise = session.get('servise')
        target = session.get('target')
        pr = session.get('pr')
        product = session.get('product')
        project = session.get('project')
        project_target = session.get('project_target')
        market_info = session.get('market_info')
        company_supplementary_info = session.get('company_supplementary_info')
        additional_info = session.get('additional_info')
        
        return render_template(
            'business_plan_preparation.html',
            page_title="事業計画書作成",
            header_title="AI事業計画書作成",
            company_name=company_name,
            ceo_name=ceo_name,
            establishment_date=establishment_date,
            address=address,
            servise=servise,
            target=target,
            pr=pr,
            product=product,
            project=project,
            project_target=project_target,
            market_info=market_info,
            company_supplementary_info=company_supplementary_info,
            additional_info=additional_info
        )
        
    return render_template(
        'business_plan_preparation.html',
        page_title="事業計画書作成",
        header_title="AI事業計画書作成"
    )


def format_chat_history(chat_history):
    """
    メッセージ履歴を適切なプロンプト形式に変換する
    """
    conversation = ""
    for msg in chat_history:
        role = "User" if msg['role'] == 'user' else "Bot"
        conversation += f"{role}: {msg['message']}\n"
    return conversation


@app.route('/send_message', methods=['POST'])
def send_message():
    # セッション内のメッセージ履歴を取得（初期値は空のリスト）
    chat_history = session.get('chat_history', [])
    if type(chat_history) is not list:
        chat_history = []
    
    # フロントエンドから受け取ったデータを取得
    data = request.get_json()
    user_message = data.get('message')
    
    # ユーザーのメッセージを履歴に追加
    chat_history.append({'role': 'user', 'message': user_message})
    
    # 過去の会話履歴を整形して、プロンプトに含める
    conversation_history = format_chat_history(chat_history)
    
    # GPTに履歴を渡して応答を生成
    bot_reply = generate_text(conversation_history)
    
    # Botの応答を履歴に追加
    chat_history.append({'role': 'bot', 'message': bot_reply})
    
    # セッションにチャット履歴を保存
    session['chat_history'] = chat_history
    
    # クライアントに応答を返す
    return jsonify({'reply': format_message(bot_reply)})