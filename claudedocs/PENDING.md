# 未完了の作業（2026-08-10 中断時点）

次のセッションはここを見れば、何が終わっていて何が残っているか分かる。
取得元・欠損対策の詳細は `DATA_ACQUISITION.md`、全体像は `../CLAUDE.md`。

---

## ブランチ

```
feature/valuation-history-and-data-gaps   （8コミット・push済み・未マージ）
```
作業ツリーはクリーン。マージ判断は未了。

---

## 運用側でやること

### 1. Render に環境変数を追加（**本番でログインするのに必須**）
```
GIA_SUPABASE_URL
GIA_SUPABASE_ANON_KEY
GIA_SUPABASE_SERVICE_ROLE_KEY
GIA_ADMIN_EMAILS=global.information.academy@gmail.com
```
認証をGIA（キャンパス）のSupabase Authへ統一したため、
**これが無いと本番で誰もログインできない**。ローカル `.env` には設定済み。

### 2. パスワード再設定の連絡（2名）
```
1008petalchime1119@gmail.com
sakkun.i.0622@gmail.com
```
ハッシュ方式が違い移行できないため。GIA側のパスワードリセットから設定してもらう。
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

テストは `py -3 -m unittest discover -s tests` で147件。
