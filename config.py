import os
import openai
from urllib.parse import quote_plus
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, logout_user, login_user, login_required, current_user# from werkzeug import secure_filename
from werkzeug.datastructures import  FileStorage
from jinja2 import Environment
from datetime import date, timedelta as td
from dotenv import load_dotenv

# .envファイルを読み込み
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('APP_SECRET_KEY', 'your_secret_key')  # セッション管理とフラッシュメッセージに必要


# サーバー設定（Supabase）- 環境変数から取得
SERVER = os.getenv('DB_SERVER', '')
DATABASE = os.getenv('DB_NAME', 'postgres')
USERNAME = os.getenv('DB_USERNAME', 'postgres')
PASSWORD = os.getenv('DB_PASSWORD', '')

# メール関連設定
MAIL_SERVER = 'smtp.googlemail.com'
MAIL_PORT = 465
MAIL_USE_SSL = True
MAIL_USERNAME = os.getenv('MAIL_USERNAME', '')
MAIL_PASSWORD = os.getenv('MAIL_PASSWORD', '')

# 秘密鍵
SECRET_KEY = os.getenv('SECRET_KEY', 'secret-key')

UPLOAD_FOLDER = 'uploads'

# app関連設定
# DB
app.config['SQLALCHEMY_DATABASE_URI'] = f'postgresql+pg8000://{USERNAME}:{quote_plus(PASSWORD)}@{SERVER}/{DATABASE}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# メール
app.config['MAIL_SERVER'] = MAIL_SERVER
app.config['MAIL_PORT'] = MAIL_PORT
app.config['MAIL_USE_SSL'] = MAIL_USE_SSL
app.config['MAIL_USERNAME'] = MAIL_USERNAME
app.config['MAIL_PASSWORD'] = MAIL_PASSWORD

# LINE
CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN', '')

# ログイン関連
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = None  # ログインページ無効

# ダミーuser_loader（DB不使用時）
@login_manager.user_loader
def load_user(user_id):
    return None

# 秘密鍵設定
app.config['SECRET_KEY'] = SECRET_KEY

# 宣言された変数を格納する辞書
app.config['VARIABLES'] = {}

# セッションの有効期限
app.config['PERMANENT_SESSION_LIFETIME'] = td(days=30)  # 例として30日に設定

# セッションの暗号化
app.config['SESSION_COOKIE_SECURE'] = True
# セッションのHTTPOnly属性
app.config['SESSION_COOKIE_HTTPONLY'] = True


# アップロード関連処理
app.config['UPLOADED_DOCUMENTS_DEST'] = 'uploads/documents'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# アップロードされるファイルの拡張子を制限（ここでは画像ファイルのみ許可）
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
# documents = UploadSet('documents', DOCUMENTS)
# configure_uploads(app, documents)

# 年齢計算
def calculate_age(birthday):
    today = date.today()
    return (today.year - birthday.year) - ((today.month, today.day) < (birthday.month, birthday.day))

env = Environment()
env.filters['calculate_age'] = calculate_age

db = SQLAlchemy(app)

# テンプレート内でenumerateを使えるようにする
app.jinja_env.globals.update(enumerate=enumerate)



# google calender
CALENDAR_ID = os.getenv('GOOGLE_CALENDAR_ID', '')
GAPI_CREDS = os.getenv('GAPI_CREDS', '')

# timetree
ACCESS_TOKEN = os.getenv('TIMETREE_ACCESS_TOKEN', '')
TT_CALENDAR_ID = os.getenv('TT_CALENDAR_ID', '')

# google vision
GOOGLE_API = os.getenv('GOOGLE_API_KEY', '')

# GPT API
GPT_API = os.getenv('OPENAI_API_KEY', '')
openai.api_key = GPT_API

# LINE
LINE_SEND_URL = 'https://api.line.me/v2/bot/message/push'
LINE_API_URL = 'https://api.line.me/v2/bot/message/reply'
LINE_PROFILE_URL = 'https://api.line.me/v2/bot/profile/'

# 通知
NOTIFICATION_CHECK_INTERVAL = 60  # seconds

# AWS
AWS_ACCESS_KEY = os.getenv('AWS_ACCESS_KEY', '')
AWS_SECRET_KEY = os.getenv('AWS_SECRET_KEY', '')
AWS_BUCKET_NAME = os.getenv('AWS_BUCKET_NAME', 'suggest-management')


# サジェスト実行時間を変更する
SUGGEST_TIME = '12:00'

# discord
WEBHOOK_URL = os.getenv('DISCORD_WEBHOOK_URL', '')

# カスタムユーザー名とアイコンURL
custom_username = "サジェスト管理システム"
# あずのユーザーID
user_id  = 373838205482434560

hide_logo_ids = ['4fd1df58-e529-4679-9fcf-8e0b7d057de6', '82fb57ce-2a50-40ae-8b72-e25bfee282d0', "fa476c60-92ec-4bc0-b567-4100934fe76c"]


# DIFY
BASE_URL = "https://api.dify.ai/v1"
DIFY_API_KEY = os.getenv('DIFY_API_KEY', '')


# 大分類/小分類の辞書
CATEGORY_SETS = {
    "01_農業": "0101_農業",
    "02_建設業": "0201_建設業",
    "03_製造業_その他の製造業": "0306_その他の製造業",
    "03_製造業_食料品・飼料・飲料製造業": "0301_食料品・飼料・飲料製造業",
    "03_製造業_化学工業・関連製品製造業": "0302_化学工業・関連製品製造業",
    "03_製造業_鉄鋼業、非鉄金属製造業": "0303_鉄鋼業、非鉄金属製造業",
    "03_製造業_金属製品製造業": "0304_金属製品製造業",
    "03_製造業_一般・電気機械器具製造業": "0305_一般・電気機械器具製造業",
    "04_卸売業_その他の卸売業": "0404_その他の卸売業",
    "04_卸売業_化学製品卸売業": "0401_化学製品卸売業",
    "04_卸売業_繊維関連製品卸売業": "0402_繊維関連製品卸売業",
    "04_卸売業_食料品卸売業": "0403_食料品卸売業",
    "05_小売業": "0501_小売業",
    "06_飲食業": "0601_飲食業",
    "07_不動産業": "0701_不動産業",
    "08_運輸業": "0801_運輸業",
    "09_エネルギー": "0901_エネルギー",
    "10_サービス業_その他のサービス業": "1006_その他のサービス業",
    "10_サービス業_物品賃貸業": "1001_物品賃貸業",
    "10_サービス業_娯楽業": "1002_娯楽業",
    "10_サービス業_広告・調査・情報サービス業": "1003_広告・調査・情報サービス業",
    "10_サービス業_事業サービス業": "1004_事業サービス業",
    "10_サービス業_専門サービス業": "1005_専門サービス業",
    "11_医療業": "1101_医療業",
    "12_保険衛生、廃棄物処理業": "1201_保険衛生、廃棄物処理業",
    "13_観光業": "1301_観光業",
    "14_その他": "1400_その他業種"
}
