1. プロジェクト概要
1.1 目的

本システムは、
マスターが登録した銘柄を
メイン画面で「ざっと状況確認できる」ことを目的とした情報閲覧システムである。
検索機能としてスクリーニング結果を算出する機能も備える。

投資助言・推奨は一切行わない

「条件に合致しているかどうか」という事実情報のみを提示する

データ取得失敗や欠損があっても、システム全体は停止しない設計とする

1.2 利用フェーズ

当面は 個人利用・検証用途

将来的な拡張（ユーザー管理・課金・代理店構造）を見据え、
設計は壊れにくく・差し替えやすい構成とする

2. システムの主目的（最重要）

マスターが登録した銘柄だけを

メイン画面（/）で一覧表示し

PASS / REVIEW を中心に、全体をざっと把握できる

※ 東証全銘柄は「データ取得の母集団」であり、
　**表示の主役は「登録銘柄セット」**である。

3. スコープ
3.1 MVPで提供する機能
(A) メイン画面：登録銘柄一覧（/）

表示対象：マスターが登録した銘柄のみ

初期表示タブ：PASS

タブ構成：PASS / REVIEW
※ FAILはMVP UIでは非表示（DBには保持）

機能

検索（会社名・証券コード 部分一致）

フィルタ（セクター、時価総額レンジ）

ソート

ROA

時価総額

売上成長（前期→今期予）

配当利回り（表示のみ）

ページング（50件/ページ）

表示

PC：テーブル

スマホ：カード（レスポンシブ自動切替）

(B) 銘柄詳細（/companies/[code]）

一覧から画面遷移

URL共有可能な通常ページ（モーダル不可）

表示内容：

企業概要

指標一覧（値 / 基準 / 判定）

条件チェックリスト（OK / NG / 要確認理由）

更新日時（JST）

データステータス（fresh / stale）

取得元（可能な範囲で）

(C) マスター銘柄登録（管理用途）

マスターは銘柄コードを 複数まとめて登録できる

改行 / カンマ区切り対応

登録結果を以下で返す

成功

失敗（理由付き）

重複

登録された銘柄は即メイン画面に反映される

※ MVPでは認証なしでも可（後述）

(D) 条件説明ページ（/conditions）

スクリーニング条件の全文表示

欠損・計算不可（分母0）の扱い説明

免責文

投資助言ではない

正確性保証なし

更新遅延の可能性

自己責任

4. 非スコープ（MVPではやらない）

ウォッチリスト

通知

コメント / SNS

リアルタイム更新

投資助言表現

本格的なログイン・権限管理（設計のみ保持）

5. 対象ユニバース
5.1 データ取得ユニバース

東証（プライム / スタンダード / グロース）の普通株全銘柄

除外：ETF / ETN / REIT / 優先株

5.2 表示ユニバース（MVPの主役）

マスターが登録した銘柄のみ

登録銘柄が0件の場合は案内メッセージを表示

6. データソース

yfinance（株価・履歴）

yahooquery（企業概要・財務・アナリスト予想）

前提：

非公式データのため、取得失敗前提で設計

失敗時は前回成功データを保持

取得元は data_source としてDBに記録

7. テーブルの設計
prompt.mdを参照すること。

8. TK会社乖離の扱い
MVP仕様（代替）

アナリスト予想乖離で代替

データ取得元：yahooquery

取得不可の場合：REVIEW

将来仕様

四季報CSVアップロード

shikiho_estimates テーブルを使用

存在時は最優先で計算

9. 判定ステータス仕様（ズレ防止・最重要）
9.1 ステータス定義

PASS
必要項目が全て取得・計算でき、条件ALL一致

FAIL
必要項目は揃っているが、1つ以上条件未達
※ MVP UIでは非表示だがDBには必ず保持

REVIEW
欠損または計算不可があり、機械判定不可

9.2 欠損（NULL）

条件項目が欠損 → REVIEW

表示：—

理由：データ取得不可（要確認）

表示専用項目の欠損はステータスに影響させない

9.3 計算不可（分母0）

分母0 → REVIEW

表示文言（固定）
計算不可（前期=0）

9.4 丸め・比較ルール

判定：生値（丸めない）

表示：小数2桁

閾値比較：仕様通り（>= は等号含む）

10. 理由コード体系（固定・必須）
10.1 REVIEW理由コード（例）

FETCH_FAILED

ANALYST_DATA_UNAVAILABLE

DIV_BY_ZERO_REVENUE_GROWTH_1Y_CY

DIV_BY_ZERO_OP_GROWTH_1Y_CY

MISSING_EQUITY_RATIO

MISSING_ROA

10.2 FAIL理由コード（例）

BELOW_MARKET_CAP_LIMIT

ROA_THRESHOLD_NOT_MET

EQUITY_RATIO_BELOW_THRESHOLD

※ UI表示文言はコード→文言マップで制御
※ DBにはコードのみを保存

11. 更新スケジュール（JST・確定）

月・木 06:10：財務・指標・判定更新

平日 12:10 / 16:10：株価・時価総額

失敗時：

前回値保持

data_status = stale

REVIEW理由を追加

12. 技術構成

フロント：Next.js（App Router）

DB：Supabase（Postgres）

API：Next Route Handlers

バッチ：Python（yfinance / yahooquery）

実行：GitHub Actions（cron）

13. データ設計
13.1 登録銘柄セット（表示制御用）
CREATE TABLE watched_tickers (
  company_code VARCHAR(10) PRIMARY KEY,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

13.2 スクリーニング結果（表示用集約）

※ あなたが提示した screened_latest DDL をそのまま採用
※ FAILは保持、UI非表示

14. API要件（MVP）

GET /api/companies
→ watched_tickers に存在する銘柄のみ返却

GET /api/companies/{code}

GET /api/conditions

GET /api/health

15. 認証・ロール（最後に実装）
ロール設計（将来）

master

agency_parent

agency_child

user

MVP方針

public read

銘柄登録は仮で master 固定

後から Supabase Auth + RLS を追加可能な設計

16. 受け入れ基準（Acceptance Criteria）

メイン画面は「登録銘柄のみ」

PASSは条件ALL一致のみ

REVIEWは欠損/計算不可理由が必ず表示される

FAILはDBに保持される

更新失敗でも閲覧可能

AI・投資助言的文言がUIに存在しない