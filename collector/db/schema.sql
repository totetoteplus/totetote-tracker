-- とてとてtracker 収集システム DBスキーマ (Phase 1)
-- Supabase (PostgreSQL) の SQL Editor で実行する。
-- 冪等に実行できるよう IF NOT EXISTS を用いる。

create extension if not exists pgcrypto;

-- updated_at を自動更新する共通トリガー関数
create or replace function set_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

-- ============================================================
-- shops: 販売元サイト
-- ============================================================
create table if not exists shops (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  domain text not null unique,
  official_url text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

drop trigger if exists trg_shops_updated_at on shops;
create trigger trg_shops_updated_at
  before update on shops
  for each row execute function set_updated_at();

-- ============================================================
-- products: 正規化済みの確定商品
-- ============================================================
create table if not exists products (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  normalized_name text not null,
  jan text,
  image_url text,
  category text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- JAN はある場合のみ一意（NULL は重複扱いしない部分ユニーク制約）
create unique index if not exists ux_products_jan
  on products (jan) where jan is not null;

create index if not exists ix_products_normalized_name
  on products (normalized_name);

drop trigger if exists trg_products_updated_at on products;
create trigger trg_products_updated_at
  before update on products
  for each row execute function set_updated_at();

-- ============================================================
-- product_match_candidates: 自動統合できない商品同一性判定の保留候補
-- (JANなし・名称類似度が閾値未満などのケース。人手/AI補助での確定待ち)
-- ============================================================
create table if not exists product_match_candidates (
  id uuid primary key default gen_random_uuid(),
  raw_product_name text not null,
  raw_data jsonb not null,
  candidate_product_id uuid references products (id) on delete set null,
  confidence numeric(4,3),
  status text not null default 'pending'
    check (status in ('pending', 'approved', 'rejected')),
  source_page_id uuid,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists ix_match_candidates_status
  on product_match_candidates (status);

drop trigger if exists trg_match_candidates_updated_at on product_match_candidates;
create trigger trg_match_candidates_updated_at
  before update on product_match_candidates
  for each row execute function set_updated_at();

-- ============================================================
-- listings: 商品 x 店舗の販売情報
-- ============================================================
create table if not exists listings (
  id uuid primary key default gen_random_uuid(),
  product_id uuid not null references products (id) on delete cascade,
  shop_id uuid not null references shops (id) on delete cascade,
  url text not null,
  price integer,
  retail_price integer,
  stock_status text
    check (stock_status in ('in_stock', 'out_of_stock', 'preorder', 'unknown')),
  last_checked_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (product_id, shop_id, url)
);

create index if not exists ix_listings_shop on listings (shop_id);
create index if not exists ix_listings_product on listings (product_id);

drop trigger if exists trg_listings_updated_at on listings;
create trigger trg_listings_updated_at
  before update on listings
  for each row execute function set_updated_at();

-- ============================================================
-- lotteries: 抽選情報
-- ============================================================
create table if not exists lotteries (
  id uuid primary key default gen_random_uuid(),
  product_id uuid not null references products (id) on delete cascade,
  shop_id uuid not null references shops (id) on delete cascade,
  title text not null,
  url text,
  application_start timestamptz,
  application_end timestamptz,
  result_date timestamptz,
  release_date timestamptz,
  conditions text,
  status text
    check (status in ('soon', 'open', 'closed', 'unknown')),
  source_url text,
  last_checked_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists ix_lotteries_shop on lotteries (shop_id);
create index if not exists ix_lotteries_product on lotteries (product_id);
create index if not exists ix_lotteries_status on lotteries (status);
create index if not exists ix_lotteries_application_end on lotteries (application_end);

drop trigger if exists trg_lotteries_updated_at on lotteries;
create trigger trg_lotteries_updated_at
  before update on lotteries
  for each row execute function set_updated_at();

-- ============================================================
-- price_history: 価格・在庫状態の変動ログ
-- ============================================================
create table if not exists price_history (
  id uuid primary key default gen_random_uuid(),
  listing_id uuid not null references listings (id) on delete cascade,
  price integer,
  stock_status text,
  recorded_at timestamptz not null default now()
);

create index if not exists ix_price_history_listing on price_history (listing_id, recorded_at desc);

-- ============================================================
-- source_pages: 取得元ページと差分検知の起点 (content_hash)
-- ============================================================
create table if not exists source_pages (
  id uuid primary key default gen_random_uuid(),
  shop_id uuid not null references shops (id) on delete cascade,
  url text not null,
  page_type text,
  last_checked_at timestamptz,
  content_hash text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (shop_id, url)
);

drop trigger if exists trg_source_pages_updated_at on source_pages;
create trigger trg_source_pages_updated_at
  before update on source_pages
  for each row execute function set_updated_at();

-- ============================================================
-- collector_runs: Collector実行ログ（監視・将来の稼働状況表示用）
-- ============================================================
create table if not exists collector_runs (
  id uuid primary key default gen_random_uuid(),
  collector_key text not null,
  started_at timestamptz not null,
  finished_at timestamptz,
  fetched_count integer not null default 0,
  new_count integer not null default 0,
  updated_count integer not null default 0,
  error_count integer not null default 0,
  error_details jsonb,
  status text not null default 'running'
    check (status in ('running', 'success', 'partial_failure', 'failed')),
  created_at timestamptz not null default now()
);

create index if not exists ix_collector_runs_key_started
  on collector_runs (collector_key, started_at desc);
