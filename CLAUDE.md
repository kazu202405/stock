# Company Note — プロジェクト概要

## アプリの概要
「Company Note」は企業理解力・思考力・経営リテラシーを育てる**教育寄りの企業分析アプリ**。
「儲かった」ではなく「賢くなった」が見える設計思想。金融煽りUI（損益・利回り・ポートフォリオ表示）は禁止。

## 技術スタック
- **言語**: Python 3.x
- **フレームワーク**: Flask 3.0.3（Flask-Login, Flask-SQLAlchemy, Flask-CORS）
- **DB**: PostgreSQL（Supabase経由）、SQLAlchemy ORM
- **フロントエンド**: Jinja2テンプレート + Tailwind CSS + Alpine.js + Chart.js
- **フォント**: Inter + Noto Sans JP
- **デプロイ**: Render（render.yaml）、Gunicorn
- **株データ**: yfinance / yahooquery（Yahoo Finance）
- **AI**: Dify API（チャットボット）、OpenAI GPT
- **外部連携**: AWS S3, LINE Messaging API, Discord Webhook, Google Calendar/Vision

## ローカル起動方法
```bash
# 1. 依存パッケージインストール
pip install -r requirements.txt

# 2. 環境変数を設定（.envファイルを作成）
#    必須: DB_SERVER, DB_USERNAME, DB_PASSWORD, APP_SECRET_KEY
#    任意: SUPABASE_URL, SUPABASE_KEY, LINE_CHANNEL_ACCESS_TOKEN,
#          DIFY_API_KEY, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY

# 3. 起動
python app.py
# → http://localhost:5000 でアクセス
```

## ディレクトリ構成
```
stock/
├── app.py                    # メインFlaskアプリ（ルート定義・API）
├── config.py                 # 設定・DB接続・APIキー・カテゴリマッピング
├── supabase_client.py        # Supabase DBクライアント
├── stock_analyzer.py         # Yahoo Finance株分析モジュール
├── gc_scraper.py             # GC/DC銘柄スクレイパー
├── models/
│   ├── model.py              # SQLAlchemyモデル（User, Settlement, LoginAttempt, Message）
│   ├── root.py               # ページルート（/, /dashboard, /search, /stock/<code>）
│   ├── common.py             # ユーティリティ（パスワードハッシュ, AWS S3, LINE API）
│   ├── login.py              # 認証（ログイン/ログアウト）※現在無効化
│   ├── user.py               # ユーザー管理CRUD
│   ├── financial_analysis.py # 財務指標計算
│   ├── chatbot.py            # Dify AIチャットボット
│   └── business_plan_preparation.py
├── templates/                # Jinja2テンプレート（layout.htmlがベース）
│   ├── layout.html           # 共通レイアウト（ナビ, モーダル, Alpine.js読込）
│   ├── lp.html               # ランディングページ /
│   ├── stock.html            # ダッシュボード /dashboard
│   ├── stock_detail.html     # 銘柄詳細 /stock/<code>
│   ├── search.html           # 銘柄検索 /search
│   ├── screener.html         # スクリーナー /screener
│   ├── mypage.html           # マイページ /mypage
│   ├── learning.html         # 学習ノート /learning
│   ├── community.html        # コミュニティ /community
│   └── chatbot.html          # AIチャット /chatbot
├── utils/                    # ユーティリティ（日英翻訳, 日本語ラベル, 株主情報）
├── static/companies.json     # 企業データキャッシュ
└── tools/基準値.xlsx          # 業界ベンチマーク基準値
```

## URLルーティングマップ

| URL | テンプレート | 認証 | 状態 | 説明 |
|-----|-------------|------|------|------|
| `/` | lp.html | 不要 | 本番 | ランディングページ |
| `/login` | login.html | 不要 | 仮実装（何でも通る） | ログイン |
| `/dashboard` | stock.html | 不要 | 本番 | 分析ダッシュボード（閲覧専用） |
| `/dashboard/admin` | stock.html | 不要 | 本番 | 管理画面（編集可能、is_admin=True） |
| `/stock/<code>` | stock_detail.html | 不要 | 本番 | 個別銘柄詳細 |
| `/search` | search.html | 不要 | 本番 | 銘柄検索 |
| `/screener` | screener.html | 不要 | 本番 | 好調企業ランキング |
| `/mypage` | mypage.html | 不要 | フロントのみ | マイページ（バックエンド未接続） |
| `/learning` | learning.html | 不要 | 静的コンテンツ | 学習ノート |
| `/community` | community.html | 不要 | フロントのみ | コミュニティ |
| `/chatbot` | chatbot.html | — | Dify API連携 | AIチャット |

## 実装ステータス

### 本番稼働中
- 株分析（単一・一括）: yfinance → screened_latestに自動保存
- ウォッチリスト: Supabase CRUD完全動作
- GC/DC銘柄スクレイピング・分析: kabutan.jp → signal_stocks
- スクリーニング: 基準値.xlsxとの合致度計算
- 銘柄検索: companies.jsonによるサジェスト

### フロントのみ（バックエンド未接続）
- **マイページ**: ノート・研究対象企業・学習記録すべてモックデータ
- **コミュニティ**: UI表示のみ
- **学習ノート**: 静的HTMLコンテンツ（DB連携なし）

### 無効化中
- **ログイン認証**: `models/login.py`のimportがコメントアウト。現在は何を入力してもログイン成功する仮実装（root.py）
- **ユーザー管理**: モデルは定義済みだがログイン機能が無効のため実質未使用

## データモデル

### SQLAlchemy（PostgreSQL）
```
User（users）
├── id: UUID（主キー）
├── company_name / email / password_hash（認証基本情報）
├── is_system_admin / is_user_admin（権限ロール）
├── contract_start / contract_end（契約期間）
└── corporate_number / employee_count / revenue 他（法人詳細情報）

Settlement（settlements）
├── id: UUID（主キー）
├── company_id: UUID（→ Userへの参照、FKなし）
├── year / month（決算期）
└── sales / operating_income / total_assets 他（BS/PL全項目）

LoginAttempt（login_attempts）
├── user_id: UUID / ip_address / user_agent
└── attempt_time / status（成功/失敗）

Message（messages）
├── user_id: UUID / user_type（user/bot）
└── message / timestamp
```
※ テーブル間にForeignKey制約は未設定。company_idは論理的な参照のみ。

### Supabase（直接API）
| テーブル | 用途 |
|---------|------|
| `watched_tickers` | ウォッチリスト（company_code） |
| `screened_latest` | 分析結果キャッシュ（財務指標・履歴JSON・合致度・GC/DC日付） |
| `signal_stocks` | GC/DC銘柄（テクニカルシグナル） |

## 主要APIエンドポイント
- `POST /api/stock/analyze` — 単一銘柄分析（60秒タイムアウト）
- `POST /api/stock/batch` — 一括分析（最大200銘柄）
- `GET /api/stock/cache/<symbol>` — キャッシュ取得
- `GET /api/stock/screened/<code>` — screened_latest取得
- `POST /api/stock/summary-jp/<code>` — 日本語事業概要再取得
- `GET/POST/DELETE /api/watchlist/*` — ウォッチリスト操作
- `POST /api/watchlist/analyze` — ウォッチリスト一括分析（バックグラウンド）
- `GET/POST /api/gc-stocks/*`, `/api/dc-stocks/*` — GC/DC銘柄
- `POST /api/gc-stocks/analyze` — GC銘柄一括分析（バックグラウンド）
- `GET /api/technical-stocks` — テクニカル銘柄統合一覧

## UIデザイン方針
- **背景**: #f7f7f5（ページ全体）、#fafaf8（ヘッダー）
- **カード**: 白背景 + border: #ebebeb + 角丸12px
- **アクセントカラー**: #1b4332（深緑）/ #2d6a4f / #22c55e
- **テキスト色**: #1a1a1a（見出し）、#525252（本文）、#737373（補助）、#a3a3a3（薄め）
- **左ボーダーアクセント**: 3px（#1b4332 or #22c55e）
- ノート・教科書・図鑑のような落ち着いたデザイン

## 禁止パターン（金融煽りUI）
以下は設計思想に反するため、絶対に実装しない：
- 損益表示（含み益/含み損、評価損益、トータルリターン）
- ポートフォリオの資産額・構成比表示
- 利回り計算・投資パフォーマンス表示
- 「買い時」「売り時」などの投資判断を促す表現
- 赤/緑の株価変動色（煽り感のある配色）
- 株価アラート・通知（価格ベースのもの）
- ランキングに「値上がり率」「出来高急増」など短期トレード向けの指標

OKなもの：
- 財務指標の客観的な数値表示（PER/PBR/ROE等）
- 業界平均との比較・合致度スコア
- 企業の事業内容・財務構造の解説
- 学習進捗・研究記録の可視化

## コーディング規約
- 日本語でのコミュニケーションを優先
- コードコメントは日本語で記述
- 変数名・関数名は英語（camelCase）
- テンプレートはlayout.htmlを継承（`{% extends "layout.html" %}`）
- フロントのインタラクションはAlpine.jsで実装（jQuery不使用）

## 既知の制約・注意点
- **yfinance レート制限**: 短時間に大量リクエストするとYahoo Financeからブロックされる。バッチ分析では0.35秒のsleepを挟んでいる
- **分析タイムアウト**: 単一銘柄分析は60秒でタイムアウト（ANALYZE_TIMEOUT）
- **バッチ上限**: 一括分析は最大200銘柄
- **ログイン無効**: `models/login.py`のimportがapp.pyでコメントアウトされている。認証が必要な機能を作る場合は先にこれを有効化する必要がある
- **ForeignKey未設定**: Settlement.company_idにFK制約がない。データ整合性はアプリ側で担保
- **Supabase接続**: 環境変数 `SUPABASE_URL` / `SUPABASE_KEY` が必要。未設定だとウォッチリスト・スクリーニング機能が動作しない
- **.env必須**: APIキー・DB接続情報等は.envに格納。絶対にコミットしない
- **セキュリティ重視**: 不要なファイル作成は避ける
