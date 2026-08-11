# 未完了の作業（2026-08-12 時点）

次のセッションはここを見れば、何が終わっていて何が残っているか分かる。
取得元・欠損対策の詳細は `DATA_ACQUISITION.md`、全体像は `../CLAUDE.md`。
会員・課金の全体は `../../../contexts/projects/gia/membership_plans_stripe.md`。

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

### 1. Supabase のメール文面を日本語にする
Authentication → Emails → Reset Password。件名・本文を差し替えるだけ。
`{{ .ConfirmationURL }}` は残すこと。文面案はセッション記録にある。

### 2. 移行で弾かれた2名へ連絡
```
1008petalchime1119@gmail.com
sakkun.i.0622@gmail.com
```
`note.gia2018.com/forgot-password` から本人が再設定できる。

### 3. Stripe の `動作確認TEST` 商品をアーカイブ
¥50 の Price はテスト用に残しておいてもよい。

### 4. ヘッダー修正の実機確認
740px / 900px あたり。想定ではナビが消えて☰になる。
（ブラウザのウィンドウ幅指定が効かず、こちらで再現確認できていない）

---

## コードで残っていること

### admin 判定のねじれ
```
ページ /admin/users   session.user_role（GIA_ADMIN_EMAILS か app_users.role）
API   /api/admin/*    app_users.role のみ
```
メールが管理者でもDBの role が admin でないと、**ページは開けてAPIだけ403**。
メール基準（`GIA_ADMIN_EMAILS`）に寄せるのが素直。gia-next 側もメール判定。

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

- `/upgrade`・`/plans` のキャッチや説明文を五島さんの言葉にする
  （いま入っている文言は正本の定義から起こしたもので、間違ってはいないが
  セミナーで話している言い回しではない）
- `invite` / `premium` の申込URLを配る運用（URLを知っていれば誰でも申し込める）

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
