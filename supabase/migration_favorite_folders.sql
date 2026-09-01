-- お気に入りのフォルダ分け（2026-09-01）
--
-- 「ウォッチ銘柄」「自分で見つけた高配当銘柄」のように、お気に入りを自分の
-- 分類で束ねられるようにする。
--
-- ⚠️ **1銘柄が複数のフォルダに入れる。** 好調企業でありながら高配当、という
--    銘柄が実際にあるため、単一所属にするとどちらか一方を選ばせることになる。
--    ∴ favorite_stocks に folder_id 列を足すのではなく、別表で結ぶ。

create table if not exists favorite_folders (
    id          uuid primary key default gen_random_uuid(),
    user_id     uuid        not null,
    name        varchar(60) not null,
    sort_order  int         not null default 0,
    created_at  timestamptz not null default now(),
    -- 同じ名前のフォルダを2つ作らせない（どちらに入れたか分からなくなる）
    unique (user_id, name)
);

create index if not exists idx_fav_folders_user on favorite_folders(user_id);
alter table favorite_folders enable row level security;

-- お気に入り × フォルダ の対応。
--
-- ⚠️ **ここの cascade は「札を外す」意味であって、銘柄は消えない。**
--    favorite_id 側 … お気に入りから外したら、その銘柄に付いていた札も消える
--    folder_id 側 … フォルダを消したら札だけ消え、銘柄はお気に入りに残る
--    どちらも消えるのは中間表の行だけで、favorite_stocks の行には触らない。
create table if not exists favorite_folder_items (
    favorite_id uuid        not null references favorite_stocks(id) on delete cascade,
    folder_id   uuid        not null references favorite_folders(id) on delete cascade,
    created_at  timestamptz not null default now(),
    primary key (favorite_id, folder_id)
);

create index if not exists idx_fav_items_folder on favorite_folder_items(folder_id);

alter table favorite_folder_items enable row level security;
-- アプリは service_role で接続する（RLSはバイパスされる）。
-- ∴ **user_id の絞り込みはアプリ側の責任**。folder_id も favorite_id も
--    クライアント由来なので、本人のものかを確かめてから使うこと。

-- 既存のお気に入りは札が1枚も付いていない＝「未分類」。移行で失うものは無い。
