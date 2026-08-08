# Company Note — プロジェクト概要

## アプリの概要
「Company Note」は企業理解力・思考力・経営リテラシーを育てる**教育寄りの企業分析アプリ**。
「儲かった」ではなく「賢くなった」が見える設計思想。金融煽りUI（損益・利回り・ポートフォリオ表示）は禁止。

## 次回調査の入口

- Codex/Claude Codeは最初に `AGENTS.md` を読む。
- 株価・財務・日本語概要・株主・役員・設立日等の取得元、欠損理由、164Aの補完、GPT疎通、改善計画は `claudedocs/DATA_ACQUISITION.md` を正本とする。
- 上記文書の調査日以降に実コードとの矛盾や本番障害がない限り、取得経路を毎回再調査しない。

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
├── app.py                    # メインFlaskアプリ（ルート定義・API 105パス）
├── config.py                 # 設定・DB接続・APIキー・カテゴリマッピング
├── supabase_client.py        # Supabase DBクライアント
├── stock_analyzer.py         # Yahoo Finance株分析モジュール
├── analysis_quality.py       # 品質判定・決算月の導出
├── data_gaps.py              # 欠損理由の分類（レポートと銘柄詳細で共用）
├── valuation_history.py      # PER/PBRの推移を株価履歴×EPS/BPSで算出
├── report_builder.py         # 企業分析レポートの組み立て
├── price_history.py          # 株価履歴の取得・間引き・保存
├── yfinance_guard.py         # レート制限に当たったら待って再開する
├── yahoo_jp_guard.py         # Yahoo日本版HTMLのサーキットブレーカー
├── edinet_db_client.py       # EDINET DB API（Free枠100回/日）
├── official_company_profiles.py # 人が確認した公式開示のキャッシュ
├── jpx_margin.py             # JPX週次信用残高（信用倍率のフォールバック）
├── gc_scraper.py             # GC/DC銘柄スクレイパー
├── earnings_scraper.py       # 決算発表銘柄の検知
├── backfill_*.py             # 各種バックフィル（全銘柄/決算月/PER・PBR/EPS・BPS）
├── models/
│   ├── root.py               # ページルート（Company Noteの画面はここ）
│   ├── common.py             # ユーティリティ（パスワードハッシュ, AWS S3, LINE API）
│   ├── chatbot.py            # Dify AIチャットボット
│   └── model.py, login.py, user.py, financial_analysis.py 他
│                             # ※旧BizFlo由来。Company Noteでは未使用
├── templates/                # Jinja2テンプレート（layout.htmlがベース）
│   ├── layout.html           # 共通レイアウト（ナビ, スライドメニュー, Alpine.js読込）
│   ├── lp.html               # ランディングページ /
│   ├── stock.html            # ダッシュボード /dashboard, /dashboard/admin
│   ├── stock_detail.html     # 銘柄詳細 /stock/<code>
│   ├── search.html           # 銘柄検索 /search
│   ├── screener.html         # スクリーナー /screener
│   ├── earnings.html         # 決算情報 /earnings
│   ├── market.html           # マーケット /market
│   ├── themes.html, theme_detail.html # テーマ・業種
│   ├── report_select.html, report_view.html, _report_body.html # レポート
│   ├── mypage.html           # マイページ /mypage
│   ├── learning.html         # 学習ノート /learning
│   ├── community.html        # コミュニティ /community
│   ├── admin_users.html, admin_themes.html # 管理画面
│   └── chatbot.html          # AIチャット /chatbot
├── supabase/                 # DB migration（適用は運用側が手で行う）
├── tests/                    # `py -3 -m unittest discover -s tests`
├── claudedocs/DATA_ACQUISITION.md # 取得元・欠損対策の正本
├── utils/                    # ユーティリティ（日英翻訳, 日本語ラベル, 株主情報）
├── static/companies.json     # 企業データキャッシュ
└── tools/基準値.xlsx          # 業界ベンチマーク基準値
```

## URLルーティングマップ

最終確認: 2026-08-08（`app.url_map` と `models/root.py` のガードを実コードから照合）

認証欄は `_require_login()` / `_require_admin()` の有無。公開ページはSEOのため意図的に開けている。

| URL | テンプレート | 認証 | 状態 | 説明 |
|-----|-------------|------|------|------|
| `/` | lp.html | 不要 | 本番 | ランディングページ |
| `/login` `/register` | login.html / register.html | 不要 | 本番 | 認証（`app_users`で実認証。仮実装ではない） |
| `/stock/<code>` | stock_detail.html | 不要 | 本番 | 個別銘柄詳細（公開。会員限定値はサーバー側で除去） |
| `/themes` `/theme/<name>` | themes.html / theme_detail.html | 不要 | 本番 | テーマ・業種一覧（公開） |
| `/dashboard` | stock.html | 要ログイン | 本番 | 分析ダッシュボード（閲覧専用） |
| `/search` | search.html | 要ログイン | 本番 | 銘柄検索 |
| `/screener` | screener.html | 要ログイン | 本番 | 好調企業ランキング |
| `/earnings` | earnings.html | 要ログイン | 本番 | 決算情報（決算月ごとの銘柄一覧） |
| `/market` | market.html | 要ログイン | 本番 | 指数チャート・日米PER |
| `/report` `/report/<source>/<key>` | report_select / report_view | 要ログイン | 本番 | 企業分析レポート |
| `/mypage` | mypage.html | 要ログイン | 本番 | マイページ（ノート・デモ売買・紹介） |
| `/learning` | learning.html | 要ログイン | 本番 | 学習ノート（解説は静的、セクター集計は実データ） |
| `/community` | community.html | 要ログイン | 本番 | Q&Aコミュニティ |
| `/dashboard/admin` | stock.html | **admin** | 本番 | 管理画面（is_admin=True） |
| `/admin/users` | admin_users.html | **admin** | 本番 | ユーザー管理・紹介ツリー |
| `/admin/themes` | admin_themes.html | **admin** | 本番 | テーマ運用 |
| `/chatbot` | chatbot.html | — | Dify API連携 | AIチャット |
| `/settlements` `/user` `/financial_analysis` 他 | — | — | **旧BizFlo由来の残置** | Company Noteでは未使用 |

## 実装ステータス

最終確認: 2026-08-08（本番Supabaseのテーブル実在と件数を直接確認）

### 本番稼働中
- 株分析（単一・一括）: yfinance → `screened_latest` に自動保存
- ウォッチリスト: `watched_tickers`
- お気に入り: `favorite_stocks`
- GC/DC銘柄スクレイピング・分析: kabutan.jp → `signal_stocks` / `gc_stocks` / `dc_stocks`
- スクリーニング: 基準値.xlsxとの合致度計算
- 銘柄検索: companies.jsonによるサジェスト
- 決算月ページ: `screened_latest.fiscal_month`
- PER/PBRの推移: `stock_price_history` × `financial_history` から算出（保存なし）
- **ノート**: `notes`（`/api/notes` 系6本）
- **コミュニティQ&A**: `community_questions` / `community_answers` / `community_likes`（API 8本）
- **デモ売買**: `demo_account` / `demo_portfolio` / `demo_trades`（`/api/demo/*`）
- **認証・ユーザー管理・紹介ツリー**: `app_users`（`authenticate_user` で実認証）

### 一部だけ静的
- **学習ノート**: 指標の解説文は `learning.html` にハードコード（1,300行超）。
  ユーザーごとに変わらない内容なのでDB化していない。
  セクター別集計だけ `/api/sector/summary` が `screened_latest` から実データを返す。
- **学習の進捗記録**: `learning_progress` に「誰がどの項目を理解したか」だけを持つ。
  項目IDの正本は `learning_terms.py`（18項目・8カテゴリ）。解説文とは分けている。
  進捗APIは実在するIDだけ受け付ける（検証しないと任意の文字列で件数を水増しできる）。
  `learning.html` の `terms[].id` とズレたら `tests/test_learning_progress.py` が落ちる。
  **migration `supabase/migration_learning_progress.sql` の適用が必要**。
  未適用の間は学習ノート自体は開けて、記録UIだけ隠れる。

### 注意（この節は実測に基づく。過去の記述は誤りだった）
2026-08-08以前のこのファイルには「マイページ・コミュニティはフロントのみ、
学習ノートはDB連携なし」と書かれていたが、**いずれも誤り**だった。
実際には上記のとおりテーブルもAPIも存在し、データも入っている。
同様に「ログイン認証は無効化中、何を入力しても通る仮実装」も誤りで、
`app_users` に対する実認証が動いている。記述を更新する際は実コードとDBを確認すること。

## データモデル

### Supabase（Company Noteの実体はすべてこちら）

`supabase_client.py` が実際に触るテーブル。件数は2026-08-08時点の本番実測。

| テーブル | 用途 | 実測 |
|---------|------|---|
| `screened_latest` | 銘柄の分析結果（財務指標・履歴JSON・合致度・GC/DC日付・決算月） | 3,879件 |
| `stock_price_history` | 株価履歴（日足1年・週足/月足10年） | 3,879件 |
| `watched_tickers` | ウォッチリスト | 32件 |
| `favorite_stocks` | お気に入り銘柄 | 2件 |
| `signal_stocks` / `gc_stocks` / `dc_stocks` | GC/DC銘柄（テクニカルシグナル） | — |
| `app_users` | ユーザー（認証・ロール・紹介コード） | 7件 |
| `notes` | ノート | 2件 |
| `community_questions` / `community_answers` / `community_likes` | Q&Aコミュニティ | 1 / 0 / 0件 |
| `demo_account` / `demo_portfolio` / `demo_trades` | デモ売買 | 3 / 5 / 8件 |
| `earnings_queue` | 決算発表のあった銘柄の処理待ちキュー | — |
| `stock_reports` | レポートのLLM生成文キャッシュ | — |
| `learning_progress` | 学習ノートの理解済み記録（要migration適用） | — |

DB migration は `supabase/` 配下に既存ファイルの続きとして追加する。
**適用は運用側（五島さん）が手で行う。** コードが先行する期間があるため、
新しい列に依存する保存処理は列が無くても落ちないようにする
（`app._save_screened_tolerating_new_columns` を参照）。

### SQLAlchemy（PostgreSQL）— 旧BizFlo由来。Company Noteでは未使用

`models/model.py` に `User` / `Settlement` / `LoginAttempt` / `Message` が定義され、
`/settlements` `/user` `/financial_analysis` 等のルートも残っているが、
**Company Noteの機能はこれらを一切使っていない**。認証も `app_users`（Supabase）側。
触る必要が出るまで手を入れない。

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
- `GET /api/stock/valuation-history/<code>` — PER/PBRの推移（DB内のデータだけで算出）
- `POST /api/stock/holders-officers/<code>` — 主要株主・役員を閲覧時に後追い取得
- `GET /api/earnings/month/<month>` — 決算月ごとの銘柄一覧（ページング）
- `GET/POST/PUT/DELETE /api/notes/*` — ノート
- `GET/POST /api/community/questions/*` — Q&Aコミュニティ
- `GET/POST /api/demo/*` — デモ売買（口座・売買・履歴・リセット）
- `GET /api/referrals/*` — 紹介コード・紹介ツリー
- `GET /api/sector/summary` — セクター別集計（学習ノートが使用）
- `GET /api/learning/progress`, `PUT/DELETE /api/learning/progress/<term_id>` — 学習の進捗

APIのユニークパスは105本（2026-08-08時点。同じパスに複数メソッドがあるため
ルール登録数は112）。`py -3 -c "from app import app; print(app.url_map)"` で一覧できる。

## UIデザイン方針
- **背景**: #f7f7f5（ページ全体）、#fafaf8（ヘッダー）
- **カード**: 白背景 + border: #ebebeb + 角丸12px
- **アクセントカラー**: #1b4332（深緑）/ #2d6a4f / #22c55e
- **テキスト色**: #1a1a1a（見出し）、#525252（本文）、#737373（補助）、#a3a3a3（薄め）
- **左ボーダーアクセント**: 3px（#1b4332 or #22c55e）
- ノート・教科書・図鑑のような落ち着いたデザイン

## 禁止パターン（金融煽りUI）

**大原則: 「煽り」は文言の問題であって、配色の問題ではない。**
赤/緑などの色は「見やすさ」のためなら使ってよい。禁止しているのは行動を急かす**表現**。

以下は設計思想に反するため、絶対に実装しない：
- 「買い時」「売り時」「今すぐ」など投資判断を促す・行動を急かす表現
- 株価アラート・通知（価格ベースのもの）
- ランキングに「値上がり率」「出来高急増」など短期トレード向けの指標

OKなもの：
- 財務指標の客観的な数値表示（PER/PBR/ROE等）
- 業界平均との比較・合致度スコア
- 企業の事業内容・財務構造の解説
- 学習進捗・研究記録の可視化
- **損益のプラス/マイナスを緑・赤で色分けする**（可読性のため。デモ売買の損益表示など）
- 株価チャート（ただし短期売買の道具立てより、長期の推移が分かる表現を優先する）

## コーディング規約
- 日本語でのコミュニケーションを優先
- コードコメントは日本語で記述
- 変数名・関数名は英語（camelCase）
- テンプレートはlayout.htmlを継承（`{% extends "layout.html" %}`）
- フロントのインタラクションはAlpine.jsで実装（jQuery不使用）

## 既知の制約・注意点
- **yfinanceには2種類のAPIがあり、コストが桁違い**（レート制限対策の中心）
  - バッチ系 `yf.download`: 200銘柄まとめて1リクエスト。3,879銘柄でも約20回。ほぼ当たらない
  - 個別系 `ticker.info` / `.financials` / `.balance_sheet`: 銘柄ごとに1回。**当たるのはこちら**
  - 株価cron（9:25/11:45/15:20）はバッチ系なので安全。全銘柄ループを常態化させないこと
  - 制限に当たったら `yfinance_guard.RateLimitGuard` が待って同じ銘柄から再開する
- **PER/PBRは `ticker.info` からしか取れない**。FastInfoにこの2つは無く、
  `hasattr(fast_info, 'price_to_book')` は常にFalse。infoが返さない場合は
  株価÷EPS / 株価÷BPS で算出する（`_fill_missing_multiples`）
- **`per_forward` 列は forward ではない**。中身は `trailingPE or forwardPE` で trailing 優先
- **分析タイムアウト**: 単一銘柄分析は60秒でタイムアウト（ANALYZE_TIMEOUT）
- **バッチ上限**: 一括分析は最大200銘柄
- **全銘柄バックフィルは `skip_extras=True`**。株主・役員・概要・信用倍率を取らない。
  株主・役員は閲覧時に後追い取得する設計（EDINET DB無料枠100回/日のため）
- **Supabaseは1リクエスト既定1000行まで**。全件取得は必ず `range()` でページングする。
  集計は `count='exact'` でDB側に寄せる（全件取ってJS集計はサイレント欠落を起こす）
- **欠損は「取得失敗」と決めつけない**。赤字にPERは存在せず、ETFに決算は無い。
  分類は `data_gaps.py` に集約（レポートと銘柄詳細が同じ判定を使う）
- **Supabase接続**: 環境変数 `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` が必要
- **.env必須**: APIキー・DB接続情報等は.envに格納。絶対にコミットしない
- **セキュリティ重視**: 不要なファイル作成は避ける

## スマホ表示で繰り返し出た崩れ（2026-08-08）

実機幅375pxで測って直した。同じ形の崩れが再発しやすいので先に確認する。

- **表のセルが1文字ずつ縦積みになる**: `overflow-x: auto` の親があっても、
  table が `width: 100%` / `min-w-full` だと親に収まってしまいスクロールが起きない。
  表に `min-width` を与え、`th/td` は `white-space: nowrap` にする
- **タブが潰れて縦積みになる**: flexの子は既定で縮む。`flex: 0 0 auto` + `nowrap` にし、
  親を `overflow-x: auto` にして横スクロールさせる
- **ボタン列がページごと横スクロールさせる**: `flex-wrap: wrap`、狭い幅ではグリッドで縦に積む
- **全ページで横に少しドラッグできる**: 閉じたスライドメニューが `position: fixed` のまま
  画面外にあり、文書のスクロール範囲を広げていた。`html`/`body` に `overflow-x: clip` を指定。
  **`hidden` は使わない**（スクロールコンテナを作り、sticky ヘッダーが効かなくなる）
- リグレッションは `tests/test_mobile_layout.py`
