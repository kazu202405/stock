# 未完了の作業（2026-08-10 時点）

次のセッションはここを見れば、何が終わっていて何が残っているか分かる。
取得元・欠損対策の詳細は `DATA_ACQUISITION.md`、全体像は `../CLAUDE.md`。

---

## ブランチ

`feature/valuation-history-and-data-gaps` は **2026-08-10 に main へマージ・
本番反映済み**（`/earnings` の応答が404から変わったことで確認）。
Render の `GIA_SUPABASE_*` 投入も完了しているため、本番のログインは動いている。

---

## 運用側でやること

### 1. Supabase（GIAプロジェクト）の設定 — パスワード再設定を動かすのに必須

`/forgot-password` → `/reset-password` を実装したが、**メールを送るのは
Supabase Auth**。次の2つがダッシュボード側で揃っていないと動かない。

**(a) Redirect URLs に追加**（Authentication → URL Configuration）— ✅ **2026-08-10 完了**
```
https://note.gia2018.com/reset-password
```
未登録だとこのURLは無視され、Site URL（gia2018.com）に飛ばされて
再設定できない。ローカル確認もするなら `http://127.0.0.1:5000/reset-password`。

**(b) SMTP の確認**（Project Settings → Authentication → SMTP Settings）— ⚠️ **未確認**
Supabase内蔵のメール送信は検証用で送信数の上限が低い。会員が増えてから
「再設定メールが届かない」を踏むと原因が見えにくいので、独自SMTP
（Resend / SendGrid 等）を入れておく方が安全。
会員登録は `email_confirm=True` で作っているためメール送信の実績が無く、
コードからは判断できない。

**確かめ方** ＝ `note.gia2018.com/forgot-password` に自分のアドレスを入れて
1通送ってみる。届けば内蔵送信は生きている（ただし上限は低いまま）。
届かない・エラーになるなら独自SMTPが要る。

### 2. パスワード再設定の連絡（2名）
```
1008petalchime1119@gmail.com
sakkun.i.0622@gmail.com
```
ハッシュ方式が違い移行できないため。上記1が済めば
`/forgot-password` から本人が自分で再設定できる。
`azmagic.17@` と `cepure7777@` は既存アカウントに紐付いたので再設定不要。

### 3. migration（任意）
```
supabase/migration_app_users_no_password.sql   未適用・適用しなくても動く
```
適用すると `app_users.password_hash` の暫定値（`'moved-to-gia-auth'`）が不要になる。

**適用済み**: `migration_fiscal_month.sql` / `migration_learning_progress.sql`
（learning_progress は RLS 有効・ポリシー無しで作成。サービスロール運用なので正しい）

---

## コードで残っていること

### kabutan 依存の差し替え（持ち越し中・外部アクセス不要）
`claudedocs/DATA_ACQUISITION.md` は「株探は機械取得禁止なので取得元に使わない」と
明記し、2026-08-05 に株探のPBR取得を撤去した。しかし以下がまだ残っている。

- `gc_scraper.py:117` … kabutan の表から PER/PBR を読んでいる
- `app.py` の技術銘柄一覧 … `sc.get('pbr') or sig.get('pbr')` で**画面に出ている**
- `earnings_scraper.py` … 決算発表銘柄の検知そのものが kabutan

単純に消すと `_filter_stocks()` の ETF・REIT 除外が壊れる（PER/PBRで判定しているため）。
**「消す」ではなく「`security_filter.py` か `screened_latest` の値に差し替える」作業。**
いま `screened_latest.pbr` は99%、`per_forward` は93%埋まっているので差し替え先としては十分。

### 学習の進捗記録
実装・適用とも完了。ただし**実データでの通し確認はまだ**（テーブルが0件）。
学習ノートを開いて「理解したらチェック」を押すと動くはず。

### パスワード再設定（本番反映済み・実メールでの通し確認だけ未了）
`/forgot-password`（送信）と `/reset-password`（設定）を追加し、
2026-08-10 に本番へ出した（`784a7a2`）。ログイン画面から辿れる。
Redirect URLs も登録済み。**残るは実際に1通送って届くかの確認だけ**（上の 1-(b)）。

`/reset-password` に渡しているのは **anonキーだけ**。サービスロールキーを
ページに出すと、開いた誰もが全ユーザーを操作できるため
`tests/test_password_reset.py` で固定してある。

### 未実装のまま残しているもの（意図的）
- **課金による機能差（有料ゲート）**。
  「今の時点では株のアプリに差はない」という判断のため作っていない。
  会員情報は `gia_identity.get_membership()` で読める状態にはしてある。
  将来リアル会限定の機能を出すときに判定を足す

---

## 直近で完了したこと（参考）

- 財務健全性を `cf_history` 参照に修正 → 25件 → 3,797件（97%）表示
- EPS最新期の欠落を純利益÷株数で補完 → 1,434件 → 3件
- PER/PBRの**推移**を自前計算（外部取得ゼロ）→ PER 95% / PBR 97% で表示可能
- 決算月ページ `/earnings` → 3,806銘柄（98%）判定
- 欠損理由の分類（赤字・ETF・制度上対象外などを区別）→ `data_gaps.py`
- スマホの表示崩れ修正（縦積み・横はみ出し）→ `tests/test_mobile_layout.py`
- 認証をGIAへ統一（`gia_identity.py` / `migrate_users_to_gia.py`）

テストは `py -3 -m unittest discover -s tests` で162件。
