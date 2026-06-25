create table if not exists public.products (
  id uuid primary key default gen_random_uuid(),

  organization_id uuid not null references public.organizations(id) on delete cascade,

  name text not null,
  short_description text,
  description text,

  category text,
  sku text,

  price integer,
  currency text default 'KRW',

  stock_quantity int default 0,

  main_image_url text,
  image_urls jsonb default '[]'::jsonb,

  options jsonb default '{}'::jsonb,
  -- 예: {"colors":["black","white"],"sizes":["M","L","XL"]}

  detail_info jsonb default '{}'::jsonb,
  -- 예: {"material":"cotton","origin":"Korea","warranty":"1년"}

  is_active boolean default true,

  source_type text default 'manual',
  -- manual, file_upload, external_db

  source_ref text,

  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create index if not exists idx_products_org
on public.products (organization_id);

create index if not exists idx_products_category
on public.products (category);

create index if not exists idx_products_active
on public.products (organization_id, is_active);

create index if not exists idx_products_sku
on public.products (organization_id, sku);