1. プロジェクト概要
1.1 目的

東証上場の普通株全銘柄を対象に、定量条件（ALL一致）で「業績好調企業」を抽出し、一覧・詳細で閲覧できる情報サイトを提供する。
※投資助言・推奨は行わず、あくまで「条件に合致した企業情報の提示」に徹する。

1.2 利用フェーズ

当面は 個人利用・検証（将来の差し替え・拡張を前提に設計は壊れにくくする）

2. スコープ
2.1 MVPで提供する機能

スクリーニング結果一覧

PASS（条件ALL一致）の企業をデフォルト表示

REVIEW（欠損/計算不可）の企業もタブで表示

検索（会社名/コード部分一致）

フィルタ（セクター、時価総額レンジ）

ソート（ROA、時価総額、売上成長：前期→今期予、配当利回り）

PC：テーブル表示 / スマホ：カード表示（自動切替）

50件/ページのページング

企業詳細

企業概要（名称、コード、セクター、上場年月、時価総額、更新日時）

指標一覧（値＋基準＋判定）

条件チェックリスト（OK/NG/要確認の理由）

データの更新日時とステータス表示（最新/更新遅延）

条件説明ページ

条件の全文表示（そのまま）

計算不可（分母0）、欠損の扱いの説明

免責（投資助言ではない、正確性保証なし、遅延の可能性、自己責任）

2.2 MVPでやらない（非スコープ）

ユーザー登録、ウォッチリスト、通知

「買い」「おすすめ」等の助言・推奨表現

コメント、SNS機能

リアルタイム更新（バッチのみ）

TDnet連携の本格運用（将来検討枠）

3. 対象ユニバース

東証（プライム / スタンダード / グロース）の普通株全銘柄

除外：ETF/ETN、REIT、優先株等（普通株以外）

4. データソース（検証フェーズ）
4.1 取得元

yfinance（Yahoo Finance由来）：株価履歴など

yahooquery（Yahoo Finance由来）：企業概要・財務等

4.2 前提とリスク対策（要件）

非公式/取得不安定の可能性がある前提で、落ちても壊れない設計にする

取得失敗時は前回成功値を保持し、ユーザーに「更新遅延」を明示する

将来、公式/契約データへ差し替え可能なように、取得ロジックはモジュール化する

取得元をDBに記録できるようにする（data_source相当の属性）

5. スクリーニング条件（ALL一致）

※以下 全て満たす場合のみ PASS とする。
※配当利回りは 表示のみ（条件に含めない）。

5.1 条件一覧（閾値）

TK会社乖離(売上高)(%)： > 0.00

TK会社乖離(営業利益)(%)： > 0.00

時価総額(億円)： <= 700.00

自己資本比率(前期)(%)： >= 30.00

売上高増減率(2期前→前期)(%)： > 0.00

売上高増減率(前期→今期予)(%)： > 0.00

売上高増減率(今期予→来期予)(%)： > 0.00

売上高営業利益率(前期)(%)： >= 10.00

営業利益増減率(2期前→前期)(%)： > 0.00

営業利益増減率(前期→今期予)(%)： > 0.00

営業利益増減率(今期予→来期予)(%)： > 0.00

営業CF前期(億円)： > 0.00

フリーCF前期(億円)： > 0.00

上場年月： > 2012/12

ROA(前期)(%)： > 4.50

PER(来期)(倍)： < 40.00

PBR(直近Q)(倍)： < 10.00

配当利回り(今期)(%)：表示のみ（判定に含めない）

5.2 TK会社乖離の代替仕様

TK会社乖離（東洋経済・会社四季報予想との乖離）はyfinance/yahooqueryから直接取得不可。

**MVP対応：アナリスト予想乖離で代替**
- analyst_deviation_revenue：（会社予想売上 - アナリスト予想売上）/ アナリスト予想売上 × 100
- analyst_deviation_op：（会社予想営利 - アナリスト予想営利）/ アナリスト予想営利 × 100
- データソース：yahooquery の `earnings_trend` / `analyst_estimates`
- 取得不可の場合：REVIEW（理由：ANALYST_DATA_UNAVAILABLE）

**将来対応：四季報CSVインポート**
- 手動でCSVアップロード可能な設計を維持
- インポート用テーブル：`shikiho_estimates`（company_code, shikiho_revenue, shikiho_op, imported_at）
- CSVフォーマット：証券コード, 四季報予想売上, 四季報予想営利
- インポート時に`screened_latest`のtk_deviation_*を再計算

5.3 データソース優先順位

1. 四季報CSV（shikiho_estimates）が存在 → TK会社乖離を計算
2. 四季報なし → アナリスト予想乖離で代替
3. アナリスト予想も取得不可 → REVIEW

6. 判定ステータス仕様（ズレ防止の最重要）
6.1 ステータス定義

PASS：必要項目が全て取得・計算でき、条件が全てOK（ALL一致）

FAIL：必要項目が全て取得・計算できたが、1つ以上NG

REVIEW：必要項目に欠損（取得不可）または計算不可があり、機械判定を完了できない

6.2 欠損（NULL）の扱い

条件に必要な任意の項目が **欠損（NULL）**の場合は REVIEW

表示：— とし、理由「データ取得不可（要確認）」を表示する

条件に関係ない“表示専用項目”（配当利回り等）が欠損でも ステータスへ影響させない

6.3 計算不可（分母0）の扱い

増減率等の計算で分母が0の場合は 計算不可としてREVIEW

表示：計算不可（前期=0） を固定文言で表示する

6.4 丸めと比較

判定：生値（丸めない）

表示：小数2桁

閾値比較：仕様通り（例：>= 10.00 は 10.00 ちょうどをOK）

7. 更新スケジュール（JST・10分ずらし確定）
7.1 更新スケジュール

月・木 06:10：財務・指標・スクリーニング判定（メイン）

平日 12:10 / 16:10：株価・時価総額更新

平日 18:30：軽更新（将来検討枠：差分があれば反映。MVPは実装しても簡易でOK）

7.2 失敗時の挙動

取得/更新に失敗した場合：

前回成功データを表示継続

data_status = stale（更新遅延）としてUIに明示

REVIEW理由（例：FETCH_FAILED）として保存

8. 株価データの扱い（確定）

期間比較・チャート：Adjusted Close

当日の値表示：Close

時価総額：取得元の値を採用（自前計算しない）

9. キャッシュ/レート制限（壊れない運用の要件）

財務・指標：TTL 7日

PER/PBR：TTL 1日

株価・時価総額：TTL 2時間

銘柄マスタ：TTL 30日

取得時は同時実行数を制限し、リトライは指数バックオフを採用

連続失敗時はREVIEW理由に積み、サイト表示を止めない

10. 画面要件（UI/UX：最高に使いやすい＆AI感ゼロ）
10.1 デザイン原則（AI感を出さないための規定）

近未来/ネオン/グラデ強め/発光/「AIっぽい装飾」は禁止

文章は“淡々と信頼感”のあるトーン（煽り・エモい表現禁止）

余白、整列、タイポグラフィで上品に見せる（派手さで誤魔化さない）

見出し・数値・ラベルの階層を明確化（視線誘導を最優先）

バッジは控えめ（PASS/REVIEWのみ、色も彩度低め）

10.2 レイアウト

PC：テーブル

スマホ：カード（レスポンシブで自動切替）

初期表示：PASSタブ

タブ：PASS / REVIEW（FAILはMVPでは非表示）

10.3 一覧（PCテーブルの列）

コード / 会社名 / セクター / 時価総額(億)

売上増減（2期前→前期、前期→今期予、今期予→来期予）

営利増減（同上）

営利率（前期）/ ROA（前期）/ 自己資本比率（前期）

PER（来期）/ PBR（直近Q）/ 配当利回り（今期：表示のみ）

更新日時（JST）＋「更新遅延」表示（stale時のみ）

10.4 一覧（スマホカードの情報順）

会社名（コード）＋ステータスバッジ（PASS/REVIEW）

セクター、時価総額

ROA、営利率、自己資本比率

売上成長（前期→今期予）、営利成長（前期→今期予）

PER、PBR、配当利回り

更新日時（JST）＋更新遅延（stale時のみ）

10.5 詳細

上部：企業概要＋主要指標サマリ

中部：条件チェックリスト（項目 / 値 / 基準 / 判定）

欠損：—＋「データ取得不可（要確認）」

分母0：計算不可（前期=0）

下部：更新日時、データステータス、ソース情報（可能な範囲で）

10.6 文言ルール（金融系で信頼を落とさない）

禁止：「買い」「おすすめ」「狙い目」「必ず」「儲かる」

使用：「条件に合致」「スクリーニング結果」「参考情報」「要確認」

11. 技術構成（確定）

フロント：Next.js（App Router）

DB：Supabase（Postgres）

バッチ：Python（yfinance / yahooquery）

API：Next側（Route Handlers）または別途FastAPI（どちらでも可）

MVPはNext内で完結でもOK（将来分離しやすい設計にする）

12. データ設計（MVPは“表示用1テーブル”で最速）

UI先行のため、最初は表示用に集約したテーブルを採用。

12.1 screened_latest（必須）

1社1行、一覧・詳細で必要な項目を全て持つ

主キー：company_code

ステータス：PASS/REVIEW/FAIL

理由格納：

review_reasons（json）

failed_reasons（json）

更新日時：

updated_at（財務・判定更新）

price_updated_at（株価更新）

12.2 DDL（screened_latest）

```sql
CREATE TABLE screened_latest (
  -- 基本情報
  company_code      VARCHAR(10) PRIMARY KEY,  -- 証券コード（例: 7203）
  company_name      VARCHAR(200) NOT NULL,    -- 会社名
  sector            VARCHAR(100),             -- セクター
  market            VARCHAR(50),              -- 市場区分（プライム/スタンダード/グロース）
  listing_date      DATE,                     -- 上場年月日

  -- 時価総額・株価
  market_cap        DECIMAL(15,2),            -- 時価総額（億円）
  stock_price       DECIMAL(15,2),            -- 株価（Close）

  -- 売上高（生値：億円）
  revenue_2y        DECIMAL(15,2),            -- 売上高（2期前）
  revenue_1y        DECIMAL(15,2),            -- 売上高（前期）
  revenue_cy        DECIMAL(15,2),            -- 売上高（今期予）
  revenue_ny        DECIMAL(15,2),            -- 売上高（来期予）

  -- 営業利益（生値：億円）
  op_2y             DECIMAL(15,2),            -- 営業利益（2期前）
  op_1y             DECIMAL(15,2),            -- 営業利益（前期）
  op_cy             DECIMAL(15,2),            -- 営業利益（今期予）
  op_ny             DECIMAL(15,2),            -- 営業利益（来期予）

  -- 財務（生値）
  total_assets      DECIMAL(15,2),            -- 総資産（億円）
  equity            DECIMAL(15,2),            -- 自己資本（億円）
  net_income        DECIMAL(15,2),            -- 当期純利益（億円）
  operating_cf      DECIMAL(15,2),            -- 営業CF（億円）
  investing_cf      DECIMAL(15,2),            -- 投資CF（億円）
  free_cf           DECIMAL(15,2),            -- フリーCF（億円）= 営業CF + 投資CF

  -- スクリーニング指標（計算値）
  tk_deviation_revenue    DECIMAL(10,4),      -- TK会社乖離(売上高)(%)
  tk_deviation_op         DECIMAL(10,4),      -- TK会社乖離(営業利益)(%)
  equity_ratio            DECIMAL(10,4),      -- 自己資本比率(%)
  revenue_growth_2y_1y    DECIMAL(10,4),      -- 売上高増減率(2期前→前期)(%)
  revenue_growth_1y_cy    DECIMAL(10,4),      -- 売上高増減率(前期→今期予)(%)
  revenue_growth_cy_ny    DECIMAL(10,4),      -- 売上高増減率(今期予→来期予)(%)
  operating_margin        DECIMAL(10,4),      -- 売上高営業利益率(前期)(%)
  op_growth_2y_1y         DECIMAL(10,4),      -- 営業利益増減率(2期前→前期)(%)
  op_growth_1y_cy         DECIMAL(10,4),      -- 営業利益増減率(前期→今期予)(%)
  op_growth_cy_ny         DECIMAL(10,4),      -- 営業利益増減率(今期予→来期予)(%)
  roa                     DECIMAL(10,4),      -- ROA(前期)(%)
  per_forward             DECIMAL(10,4),      -- PER(来期)(倍)
  pbr                     DECIMAL(10,4),      -- PBR(直近Q)(倍)
  dividend_yield          DECIMAL(10,4),      -- 配当利回り(今期)(%) ※表示のみ

  -- 判定結果
  status            VARCHAR(10) NOT NULL DEFAULT 'REVIEW',  -- PASS/FAIL/REVIEW
  review_reasons    JSONB DEFAULT '[]'::jsonb,              -- 欠損/計算不可の理由
  failed_reasons    JSONB DEFAULT '[]'::jsonb,              -- 条件未達の理由

  -- 更新管理
  updated_at        TIMESTAMP WITH TIME ZONE DEFAULT NOW(), -- 財務・判定更新日時
  price_updated_at  TIMESTAMP WITH TIME ZONE,               -- 株価更新日時
  data_status       VARCHAR(20) DEFAULT 'fresh',            -- fresh/stale
  data_source       VARCHAR(50) DEFAULT 'yfinance',         -- データソース

  -- 制約
  CONSTRAINT chk_status CHECK (status IN ('PASS', 'FAIL', 'REVIEW')),
  CONSTRAINT chk_data_status CHECK (data_status IN ('fresh', 'stale'))
);

-- インデックス
CREATE INDEX idx_screened_status ON screened_latest(status);
CREATE INDEX idx_screened_sector ON screened_latest(sector);
CREATE INDEX idx_screened_market_cap ON screened_latest(market_cap);
CREATE INDEX idx_screened_updated ON screened_latest(updated_at);

-- Row Level Security（将来の拡張用、MVPではpublic読み取り）
ALTER TABLE screened_latest ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Public read access" ON screened_latest FOR SELECT USING (true);

-- 四季報CSVインポート用テーブル（将来対応）
CREATE TABLE shikiho_estimates (
  company_code      VARCHAR(10) PRIMARY KEY REFERENCES screened_latest(company_code),
  shikiho_revenue   DECIMAL(15,2),            -- 四季報予想売上（億円）
  shikiho_op        DECIMAL(15,2),            -- 四季報予想営利（億円）
  fiscal_period     VARCHAR(20),              -- 対象期（例: 2025/03）
  imported_at       TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  source_file       VARCHAR(200)              -- インポート元ファイル名
);
```

12.3 バッチ実行環境

GitHub Actions を採用

理由：
- Pythonバッチに最適
- cron スケジュール対応（月木06:10、平日12:10/16:10）
- secrets管理が容易
- 無料枠（2000分/月）で十分

ワークフロー構成：
- .github/workflows/update-financial.yml（月木06:10 JST = 日21:10 UTC前日）
- .github/workflows/update-price.yml（平日12:10/16:10 JST）

13. API要件（MVP）

GET /api/companies?status=PASS|REVIEW&q=&sector=&minCap=&maxCap=&sort=&page=

GET /api/companies/{code}

GET /api/conditions（条件一覧・表示用）

GET /api/health

14. 受け入れ基準（Acceptance Criteria）

PASSタブに表示される企業は、条件（ALL一致）を満たすもののみ

REVIEWタブに表示される企業は、欠損または計算不可が理由として表示される

欠損は —、分母0は 計算不可（前期=0） と明示される

更新失敗時もサイトは閲覧可能で、更新遅延が表示される

PCはテーブル、スマホはカードに自動切替される

画面文言に投資助言表現が含まれない

AIエージェント向け実装指示（そのまま貼り付け用）

Supabaseに screened_latest を作成し、モックデータを投入できる状態にする

Nextで以下を実装する

/：PASS/REVIEWタブ＋検索＋フィルタ＋ソート＋ページング

/companies/[code]：条件チェックリスト（OK/NG/要確認理由）

/conditions：条件説明＋免責

レスポンシブ：PCテーブル/スマホカードへ自動切替

UIは“金融サイトの上品さ”を基準に、AIっぽい演出を禁止（ネオン、過度なグラデ、AIワード）

バッチ更新（Python）：

月木06:10に財務・指標・判定更新

平日12:10/16:10に株価・時価総額更新

失敗時は前回値保持＋stale表示

欠損/計算不可はREVIEWに分類し、理由をjsonに格納

株価：チャートはAdjusted Close、当日表示はClose