from datetime import datetime as dt
import requests
import json
import boto3
from botocore.exceptions import NoCredentialsError
from config import *
from models.model import *
from passlib.hash import pbkdf2_sha256 as sha256
import time
import re


def generate_password_hash(password):
    return sha256.hash(password)


def check_password_hash(hashed_password, password):
    return sha256.verify(password, hashed_password)


def format_date(date_obj, format='%Y/%m/%d'):
    """指定されたフォーマットで日付を文字列に変換します。"""
    if date_obj is None:
        return ""
    return date_obj.strftime(format)

def format_datetime(date_obj, format='%Y/%m/%d %H:%M:%S'):
    """指定されたフォーマットで日時を文字列に変換します。"""
    if date_obj is None:
        return ""
    return date_obj.strftime(format)


def create_presigned_url(bucket_name, object_name, expiration=3600):
    """Generate a presigned URL to share an S3 object"""
    s3 = boto3.client('s3', aws_access_key_id=AWS_ACCESS_KEY,
                    aws_secret_access_key=AWS_SECRET_KEY)
    try:
        response = s3.generate_presigned_url('put_object',
                                            Params={'Bucket': bucket_name,
                                                    'Key': object_name},
                                            ExpiresIn=expiration)
    except NoCredentialsError:
        print("Credentials not available")
        return None

    return response


def upload_to_aws(local_file, bucket_name, s3_file):
    s3 = boto3.client('s3', aws_access_key_id=AWS_ACCESS_KEY,
                    aws_secret_access_key=AWS_SECRET_KEY)

    try:
        s3.upload_file(local_file, bucket_name, s3_file)
        print("Upload Successful")
        return True
    except FileNotFoundError:
        print("The file was not found")
        return False
    except NoCredentialsError:
        print("Credentials not available")
        return False


def upload_file_to_s3(file, bucket_name, s3_file_path, acl="public-read"):
    """
    Upload a file to an S3 bucket
    :param file: File to upload
    :param bucket_name: Bucket to upload to
    :param acl: ACL for the file
    """
    s3 = boto3.client(
        "s3",
        aws_access_key_id=AWS_ACCESS_KEY,
        aws_secret_access_key=AWS_SECRET_KEY
    )

    try:
        s3.upload_file(
            file,
            bucket_name,
            s3_file_path,
            ExtraArgs={
                # "ACL": acl,
                "ContentType": 'image/png'
            }
        )
    except Exception as e:
        # Handle the exception
        print(e)
        return None

    file_url = f"https://{bucket_name}.s3.amazonaws.com/{s3_file_path}"
    return file_url


# LINE Messaging APIのアクセストークン
ACCESS_TOKEN = '42yKm8vS7gpwm5mb6+5R9dDT1paiNYlF5ziiI0yY767WVFowhAlTR2TiSiPaiYsd+hAWJYhZZIQHAIQxAm+26/4NVURpRa0zvLdwmlOtrKfV6LMeKO9x15cU7fIdtATB3cVQlFfERg9q4EUkS2DicAdB04t89/1O/w1cDnyilFU='

def send_line_notification(message):
    url = 'https://api.line.me/v2/bot/message/broadcast'
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {ACCESS_TOKEN}'
    }
    data = {
        "messages": [
            {
                "type": "text",
                "text": message
            }
        ]
    }
    response = requests.post(url, headers=headers, data=json.dumps(data))
    
    if response.status_code == 200:
        print('通知が送信されました')
    else:
        print(f'通知の送信に失敗しました: {response.status_code}')
        print(response.text)


def generate_text(prompt):
    # OpenAI APIにリクエストを送る
    completion = openai.ChatCompletion.create(
        model="gpt-4o-mini",  # 使用するモデルを指定
        messages=[
            {"role": "user", "content": prompt}  # プロンプトを設定
        ]
    )

    # レスポンスからテキストを取得
    text = completion['choices'][0]['message']['content']

    return text


def generate_with_structure_data(field_names, src_text_all):
    prompt = f"次の情報を使用して、各情報を解析してください。{src_text_all}"
        
    # JSONスキーマのひな型を作成
    json_schema = {
        "type": "object",
        "properties": {},
        "required": field_names
    }
    
    # フィールド名リストからプロパティを生成
    for field in field_names:
        json_schema["properties"][field] = {"type": "string"}
    
    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "以下のスキーマに基づいて、JSON形式で応答してください。"},
            {"role": "user", "content": f"{prompt}\n\nスキーマ: {json.dumps(json_schema)}"}
        ]
    )

    # レスポンスから内容を取得
    try:
        response_text = response.choices[0].message["content"]
    except (KeyError, IndexError):
        return {"error": "GPTからの応答が不正です。"}

    # 応答が空かどうかチェック
    if not response_text.strip():
        return {"error": "GPTからの応答がありません。"}

    # JSON形式で応答を返す
    try:
        return json.loads(response_text)
    except json.JSONDecodeError:
        # エラーハンドリング: すべてのキーを空文字にしたデフォルトの JSON を作成
        empty_response = {}
        for field in json_schema["properties"]:
            empty_response[field] = ""
        return empty_response


def parse_gpt4_output(gpt4_output, fields):
    output_dict = {}
    for field in fields:
        search_term = f"{field}:"
        start_index = gpt4_output.find(search_term)
        if start_index != -1:
            start_index += len(search_term)
            end_index = gpt4_output.find("\n", start_index)
            if end_index == -1:
                end_index = len(gpt4_output)
            output_dict[field] = gpt4_output[start_index:end_index].strip()
        else:
            output_dict[field] = ''
    return output_dict


def categorize_company(industry, employees):
    """
    業種と従業員数に基づいて企業を中規模か小規模か判断する関数。

    :param industry: 業種 (例: '製造業', '卸売業', '小売業', 'サービス業')
    :param employees: 従業員数
    :return: '小規模企業' または '中規模企業'
    
    # 使用例
    print(categorize_company('製造業', 15))  # 小規模企業
    print(categorize_company('卸売業', 10))  # 中規模企業
    print(categorize_company('小売業', 4))   # 小規模企業

    
    """
    if industry in ['製造業', '農業', '建設業', '不動産業', '運輸業', 'エネルギー', 'その他']:
        if employees <= 20:
            return '小規模企業'
        else:
            return '中規模企業'
    elif industry == '卸売業':
        if employees <= 5:
            return '小規模企業'
        else:
            return '中規模企業'
    elif industry in ['小売業', '飲食業']:
        if employees <= 5:
            return '小規模企業'
        else:
            return '中規模企業'
    elif industry in ['サービス業', '医療業', '保健衛生', '廃棄物処理業', '観光業']:
        if employees <= 5:
            return '小規模企業'
        else:
            return '中規模企業'
    else:
        return '不明な業種'


# チャットメッセージを送信する関数
def send_chat_with_dify(query, conversation_id=None, user="abc-123", add_info=[], retries=3, delay=2):
    BASE_URL = "https://api.dify.ai/v1"
    API_KEY = "app-vHn7lwO0opF7BgbXJDOwItWZ"  # 実際のAPIキーに置き換えてください
    HEADERS = {
        'Authorization': f'Bearer {API_KEY}',
        'Content-Type': 'application/json'
    }
    
    endpoint = f"{BASE_URL}/chat-messages"
    
    payload = {
        "inputs": {},
        "query": query,
        "response_mode": "blocking",  # または "blocking"
        "conversation_id": conversation_id,
        "user": user,
        "files": [
            {
                "type": "image",
                "transfer_method": "remote_url",
                "url": "https://cloud.dify.ai/logo/logo-site.png"
            }
        ]
    }
    
    # オプションの追加情報を設定
    for info in add_info:
        payload["inputs"][info["key"]] = info["value"]
    
    # 再試行を制御するループ
    for attempt in range(retries):
        response = requests.post(endpoint, headers=HEADERS, json=payload)
        
        if response.status_code == 200:
            return response.json()  # 成功した場合は結果を返す
        else:
            print(f"Attempt {attempt + 1} failed with status code: {response.status_code}")
            print(response.text)
        
        # 次の試行まで待機 (失敗した場合)
        if attempt < retries - 1:
            time.sleep(delay)  # 指定された秒数の遅延を入れる
    
    # 最大試行回数を超えたら None を返す
    print("All retries failed.")
    return None


def format_message(text):
    # 改行を <br> に変換
    formatted_text = text.replace('\n', '<br>')

    # ** で囲まれた部分を <strong> に変換
    def replace_bold(match):
        return f"<strong>{match.group(1)}</strong>"

    formatted_text = re.sub(r'\*\*(.*?)\*\*', replace_bold, formatted_text)

    # リスト項目（1., 2., 3.）を <br> で区切って表示
    formatted_text = formatted_text.replace('1. ', '<br>1. ').replace('2. ', '<br>2. ').replace('3. ', '<br>3. ')

    # URLを検索する正規表現
    url_pattern = re.compile(r'(https?://[^\s<]+)')

    # URLをリンクに変換
    formatted_text = url_pattern.sub(r'<a href="\1" target="_blank" rel="noopener noreferrer">\1</a>', formatted_text)

    return formatted_text