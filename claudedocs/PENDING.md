# 未完了の作業（2026-08-19 更新）

> **⚠️ 2026-08-19 のセッションはここから読む。** 下の「2026-08-19 の作業」を先に見ること。

---

## 2026-08-19 の作業（すべて**未コミット・未デプロイ**）

### いま走っているもの（2026-08-20 夜）

**本番バックフィル**（`screened_latest` に書き込む）:

```
python backfill_yahoo_fields.py --max-per-run 400 --sleep 5.0
```

- ログ: `claudedocs/backfill_real_400.log`
- **未取得だけを拾うので、いつ止めても同じコマンドで続きから走る**
- 1銘柄18.3秒。400件で約2時間。**全3,800件だと約19時間**なので10回ほどに分けて流す
- 進捗: `grep -c "OK (" claudedocs/backfill_real_400.log`
- 件数確認: 下の「実行前後の件数」参照

**実行前の件数（2026-08-20）**

| | |
|---|---|
| forecast_revenue | 382 |
| forecast_op_income | 384 |
| business_summary_jp | 3,784 |

（50件テスト前は 348 / 350。50件で +34＝68%が埋まった）

⚠️ ブレーカーが開いたときの「待って復帰」はまだ本番で発動していない。
このログに「遮断されました。◯分待って再開します」が出たら、そこが初検証。

---

### 計測スクリプト（DBに書かない方）

```
python backfill_impact_dryrun.py --missing-only --spread 100 --sleep 1.5 --resume
```

- ログ: `claudedocs/dryrun_run3.log`
- 結果: `claudedocs/backfill_impact_dryrun.jsonl`（**1銘柄ずつ追記**。途中で落ちても残る）
- 中断したら**同じコマンドをもう一度**打てば続きから走る（`--resume` 付き）
- 途中経過だけ見たい: `python backfill_impact_dryrun.py --report`

**何を測っているか** ＝ いま今期予想が入っていない銘柄を、コード帯を散らして100件。
①本当に埋まるのか ②スコアがどう動くのか（PER/PBRが判定不能に転ばないか）。
これが出れば**全3,879件のバックフィルをやるかどうか**を決められる。

判断の目安：埋まる率が3割を切るなら全件（約4.5時間・1銘柄10リクエスト）は割に合わない。
EDINET DB（業績予想あり・Free枠100回/日）を時価総額順に日々消化する方に倒す。

### 触ったファイル（`git status` で確認できる）

| ファイル | 内容 |
|---|---|
| `yahoo_jp_guard.py` | **ブレーカーに半開放**（10分→倍々・上限60分）。以前は `reset()` を手で呼ぶまで戻らず、予想が全銘柄で取れない真因だった |
| `app.py` | `_enqueue_announced()`。**決算更新の二度押し／21時cronで全件再取得**になっていたのを修正。日付をUTC→JSTに |
| `models/root.py` | `/stock/<code>` に会社名が来たときコードへ寄せる。`/welcome` `/seminar` 追加 |
| `company_lookup.py` | 会社名→証券コードの解決（新規） |
| `templates/stock_not_found.html` | 空ページの代わりに理由と候補を出す（新規） |
| `templates/stock_detail.html` | **適合度に12項目メーター**。色＝点数／枠＝充足度に分離 |
| `templates/search.html` | Enterで候補未選択のとき入力文字列のまま飛んでいたのを修正 |
| `templates/welcome.html` `templates/seminar.html` | 知人向け・勉強会LP（新規） |
| `.gitignore` | `!static/images/seminar/*.png`（`*.png` に巻き込まれて本番だけ404になる） |
| `tests/test_company_lookup.py` `tests/test_yahoo_jp_guard.py` | 新規。全272件通過 |

詳細な経緯と数字は `../../../contexts/projects/gia/company_note.md` の「20」〜「25」。

### 分かっていること（結論を間違えないための要点）

- **キオクシア(285A)に予想が無いのは会社が出していないから**（`not_disclosed`）。バグではない
- 予想の充足率9%の主因は、**当初考えた「在庫が古い」ではない**。
  ブレーカー（修正済み）と、**Yahooにそもそも業績ページが無い（`no_data`）**の合わせ技
- スコア副作用は50件計測で **PER/PBR判定不能 0件**＝全件やっても点数は崩れない
- ⚠️ **`order('company_code')` の先頭から取ると偏る。** 先頭50件は56%が予想持ち（母集団9%）

---

次のセッションはここを見れば、何が終わっていて何が残っているか分かる。
取得元・欠損対策の詳細は `DATA_ACQUISITION.md`、全体像は `../CLAUDE.md`。
会員・課金の全体は `../../../contexts/projects/gia/membership_plans_stripe.md`。

## 2026-08-12 までの状態

---

## 状態

`main` = 本番。未pushゼロ。テスト185件。

2026-08-10〜12 で入れたもの（すべて本番反映済み）:

- 認証をGIA（キャンパス）の Supabase Auth へ統一
- パスワード再設定（`/forgot-password` → `/reset-password`）。実メール到達確認済み
- モバイル導線（ボトムタブにマイノート／☰をヘッダー右端へ）
- **会員ゲート**（ページ・API・銘柄ページ・コミュニティ）
- アカウント設定に会員種別を表示
- ヘッダーの折り返し修正（lg 1024px で切替）

---

## 運用側でやること

### 1. Supabase のメール文面を日本語にする — ✅ 2026-08-12 設定完了
GIAプロジェクトの Authentication → Emails → Reset Password。件名・本文を差し替える。
`{{ .ConfirmationURL }}` は必ず残す（これがリンクの実体）。

⚠️ **このテンプレートは Company Note 専用ではない。**
GIAプロジェクトの `auth.users` は Company Note とキャンパスで共通なので、
将来キャンパス側に再設定を付けたら同じ文面が飛ぶ。
→ 「Company Note のパスワード」と書かず、**GIAアカウント**と書く。

**件名**
```
【GIA】パスワード再設定のご案内
```

**本文（HTML）**
```html
<p>GIA アカウントのパスワード再設定のご依頼を受け付けました。</p>

<p>下のリンクを開いて、新しいパスワードを設定してください。</p>

<p><a href="{{ .ConfirmationURL }}">パスワードを再設定する</a></p>

<p>このリンクの有効期限は1時間です。期限が切れた場合は、
お手数ですが再度お手続きをお願いいたします。</p>

<p>お心当たりのない場合は、このメールを破棄してください。
パスワードは変更されません。</p>

<hr>
<p style="font-size:12px;color:#666;">
GIA<br>
Company Note　https://note.gia2018.com<br>
HIROGARU キャンパス　https://gia2018.com
</p>
```

### 完了したもの（2026-08-12 確認）

- ✅ **`STRIPE_PRICE_PREMIUM_LIVE` を ¥33,000 に戻した**（実地テストで ¥50 に
  一時差し替えていたもの。招待URLを配る前に戻せた）
- ✅ Stripe の `動作確認TEST` 商品をアーカイブ
- ✅ 移行で弾かれた2名へ連絡（`1008petalchime1119@` / `sakkun.i.0622@`）
- ✅ ヘッダーのタブレット幅の折り返し = **☰に集約する現状の挙動でよい**と判断
  （五島さん「変に折り返すくらいならハンバーガーメニューもいい感じ」）

---

## コードで残っていること

### ✅ PBR/PER の食い違いと配当利回りの桁違い（2026-08-12 コード修正済み・DB反映待ち）

**コードは修正済み。本番DBの既存値はまだ直っていない。**
反映手順は下の「DBに流す手順」。

#### 分かったこと（当初の見立てを1つ訂正している）

最初は「Yahooが誤り、割り算が正しい」と見て割り算側を正にしようとしたが、
**割り算側も壊れる**ことが分かったので方針を変えた。

| 銘柄 | 壊れている側 | 内容 |
|---|---|---|
| 3939 | Yahoo | `bookValue` 10.319（貸借対照表からは 97.97）→ PBR 48.65倍（正: 5.26倍） |
| 1773 | 割り算 | EPS・BPS系列が同じ倍率で小さい → PBR 48.7倍（Yahooの 1.20倍 がROEと整合） |

**ROEによる検算は使えない。** EPSとBPSが同じ倍率で狂うと ROE（＝EPS÷BPS）は
変わらないので、スケール誤りを検出できない。株数を経由しない
「時価総額 ÷ 純資産」なら検出できるが、**`equity` 列は全3,879件で空**。

→ どちらが正しいか機械的に決められない。**決められないものを決めたふりで出さない。**
2つが1.5倍以上食い違ったら値を持たせず「判定不能」にする。
スコアは判定できた項目数を分母にするので（§10）、判定不能は減点にならない。

#### 配当利回り（こちらは原因が確定していて一意に直る）

スクリーナーを配当利回り順に並べると40%超が並んでいた。原因は2つ。
- `dividendYield` は**常に%**（0.4 は 0.4%）。それを「0.5未満なら小数」と推測して
  100倍する分岐があり、**利回り0.5%未満の銘柄を軒並み100倍**していた
  （9720: 0.4% → 40% / 153A: 0.43% → 43%）
- `trailingAnnualDividendRate` は**分割調整されないことがある**
  （4918: 実際15円のところ150円 → 47.5%）

→ `ticker.dividends`（分割調整済みの支払い実績）の直近12か月合計÷株価を正とした。
無配は 0.0 でなく None（「出していない」と「0%」は違う）。

#### DBに流す手順（未実施）

```
py -3 backfill_derived_multiples.py --dry-run   # PER 272件 / PBR 214件が変わる
py -3 backfill_derived_multiples.py
py -3 backfill_dividend_yield.py --dry-run      # 27件中 18件を再計算・9件を不明に
py -3 backfill_dividend_yield.py
py -3 recalc_match_rates.py                     # PER/PBRは12項目に入るので必須
```

`backfill_derived_multiples.py` は外部アクセスなし。
`backfill_dividend_yield.py` は対象銘柄だけ（27件）Yahooを引く。

---

### （記録）当初の調査メモ

`/stock/3939` で発覚。**同じ銘柄ページの中で PBR が食い違う。**

| 表示場所 | 出所 | 3939 の値 |
|---|---|---|
| PBR推移グラフ | `financial_history.bps`（貸借対照表由来） | **5.3倍**（正しい） |
| 基準適合度カード | `screened_latest.pbr`（Yahoo `priceToBook`） | **48.65倍**（誤り） |

**原因＝Yahooの `bookValue` が日本株で当てにならない。**
3939: Yahoo `bookValue`=10.319 に対し、貸借対照表は
純資産46.49億 ÷ 発行済47,457,294株 = **BPS 97.97円** → PBR 5.26倍。
`Stockholders Equity` と `Ordinary Shares Number` から計算した値は
DBの `financial_history.bps`（97.97）とも一致する。**倍率は銘柄ごとにバラバラ**
（3939=9.5倍 / 3399=6.7倍 / 4970=4.7倍 / 4393=4.0倍）なので単位バグではない。

**なぜ補正が効かなかったか** ＝ `stock_analyzer._fill_missing_multiples()` は
**「Yahooが値を返さなかったとき」しか株価÷BPSで補完しない**（`if result.get(key) is not None: continue`）。
Yahooが"間違った値"を返した場合は素通りする。欠損だけを見ていて、異常値を見ていない。

**影響範囲**（恒等式 `PBR = PER × ROE` で全3,879件を照合）:
- **13銘柄**が「PBR≥10で不合格」だが、実際は10未満＝**合格のはずなのに減点されている**
  （3939 / 3399 / 4970 / 3628 / 6072 / 4397 / 4393 / 2164 / 4971 / 6016 / 4443 / 436A / 4712）
- PBR≥10で不合格になっている銘柄は全体で83件。うち13件が誤判定

**修正方針（未着手）**＝`financial_history.bps` から作れるなら**そちらを正とする**。
理由: ①開示資料由来で誰でも再現できる ②同じページのグラフが既にそれを使っており、
2つの出所を持つこと自体が矛盾の原因 ③Yahooの `priceToBook` は検証手段が無い。
Yahooの値は BPS が作れない銘柄のフォールバックに下げる。
直したら**再計算とスコアの再保存が要る**（§10 のスコア定義修正と同じ手順）。

⚠️ **PERも同じ構造**（`per_forward` は Yahoo の `trailingPE or forwardPE`）。
3939 は EPS からの計算と近いので今回は表面化しなかったが、同じ点検をすること。

### admin 判定のねじれ
```
ページ /admin/users   models/root.py _require_admin()
                      → session['user_role']（GIA_ADMIN_EMAILS か app_users.role）
API   /api/admin/*    app.py @role_required('admin')
                      → get_current_user()['role'] = app_users.role のみ
```
メールが管理者でもDBの role が admin でないと、**ページは開けてAPIだけ403**。
＝管理画面は開くのに中身が空／エラーになる。権限不足に見えないぶん原因が分かりにくい。

**実害があるかの確認**: `note.gia2018.com/admin/users` でユーザー一覧が出るか。
出れば現運用では問題なし（DBの role も admin）。将来ほかの人を管理者にするとき顕在化する。

修正方針＝`role_required` もメール基準（`GIA_ADMIN_EMAILS`）を見る。gia-next 側もメール判定。

### kabutan 依存の差し替え（持ち越し中・外部アクセス不要）
`DATA_ACQUISITION.md` は「株探は機械取得禁止」と明記しているが、まだ残っている。
- `gc_scraper.py:117` … kabutan の表から PER/PBR
- `app.py` の技術銘柄一覧 … `sc.get('pbr') or sig.get('pbr')` で画面に出ている
- `earnings_scraper.py` … 決算発表銘柄の検知そのもの

単純に消すと `_filter_stocks()` の ETF・REIT 除外が壊れる（PER/PBRで判定）。
**「消す」ではなく「`security_filter.py` か `screened_latest` の値に差し替える」**。
`screened_latest.pbr` は99%、`per_forward` は93%埋まっているので差し替え先は十分。

### 学習の進捗記録
実装・migration適用とも完了。実データでの通し確認だけ未了。

---

## 判断待ち（GIA側）

対象URL（すべて **gia2018.com** 側。Company Note ではない）:

| URL | ログイン | 用途 |
|---|---|---|
| `gia2018.com/plans` | 不要 | 料金比較の1枚。無料/¥4,980/¥7,980。配布用 |
| `gia2018.com/upgrade` | **必須** | 申し込み画面。未ログインは `/login` 経由で戻る |
| `gia2018.com/upgrade/invite` | 必須 | ¥11,000（非公開・URL直渡し） |
| `gia2018.com/upgrade/premium` | 必須 | ¥33,000（非公開・URL直渡し） |

- `/upgrade`・`/plans` のキャッチや説明文を五島さんの言葉にする
  （いま入っている文言は正本の定義から起こしたもので、間違ってはいないが
  セミナーで話している言い回しではない）。**課金ページなので売上への効きが一番大きい残件**
- ~~`invite` / `premium` の申込URLを配る運用~~ → **✅ 2026-08-12 決着＝現状のままでよい**。
  **承認ステップは無い**（URLを開くと即Stripe決済へ直行し、webhookが自動で段を付与。
  `noindex` だが転送されれば誰でも買える）が、**五島さん確認＝premium の会に定員なし**。
  定員が無いなら誤爆は返金で戻せるので、招待コードは作らない。
  ⚠️ **定員を設ける日が来たら、その時点で招待コードが必要になる**
  （席が埋まる損失は返金では戻せない）。

---

## この期間の教訓

- **段の名前を変えたら「その名前を読んでいる場所」を全部洗う。**
  webhook だけ直しても、画面側の判定が古い名前を見ていると
  「買ったのに使えない」になる（実際に3箇所で起きた）
- **ぼかしは装飾。保護はサーバーが値を送らないこと。**
- **会員/非会員で分岐する画面は、両方レンダリングして確認する。**
  目視は会員側しか見ないので、非会員だけ崩れていても気づけない
- 決済まわりの壊れ方は「お金を払った人が使えない」に出る

テストは `py -3 -m unittest discover -s tests` で185件。
