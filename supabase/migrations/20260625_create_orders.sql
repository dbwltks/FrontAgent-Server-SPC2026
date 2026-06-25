create table if not exists public.orders (
  id uuid primary key default gen_random_uuid(),

  organization_id uuid not null references public.organizations(id) on delete cascade,

  order_code text,
  -- 고객에게 보여줄 주문번호
  -- 예: ORD-20260625-0001
  -- MVP에서는 백엔드에서 생성해서 넣는 방식 추천

  customer_name text not null,
  customer_phone text not null,
  customer_email text,

  order_status text not null default 'requested',
  -- requested, confirmed, preparing, shipped, delivered, cancelled

  payment_status text not null default 'unpaid',
  -- unpaid, paid, failed, refunded

  delivery_status text not null default 'pending',
  -- pending, preparing, shipped, delivered, failed

  recipient_name text,
  recipient_phone text,

  postal_code text,
  address_line1 text,
  address_line2 text,
  delivery_memo text,

  courier_name text,
  tracking_number text,

  total_amount integer not null default 0,
  currency text not null default 'KRW',

  source_channel text not null default 'web_chat',
  -- web_chat, phone, kakao, admin

  memo text,

  shipped_at timestamptz,
  delivered_at timestamptz,
  cancelled_at timestamptz,

  created_at timestamptz default now(),
  updated_at timestamptz default now(),

  constraint orders_order_status_check
    check (order_status in ('requested', 'confirmed', 'preparing', 'shipped', 'delivered', 'cancelled')),

  constraint orders_payment_status_check
    check (payment_status in ('unpaid', 'paid', 'failed', 'refunded')),

  constraint orders_delivery_status_check
    check (delivery_status in ('pending', 'preparing', 'shipped', 'delivered', 'failed'))
);

create unique index if not exists idx_orders_org_order_code_unique
on public.orders (organization_id, order_code)
where order_code is not null;

create index if not exists idx_orders_org
on public.orders (organization_id);

create index if not exists idx_orders_customer_phone
on public.orders (organization_id, customer_phone);

create index if not exists idx_orders_status
on public.orders (organization_id, order_status);

create index if not exists idx_orders_delivery_status
on public.orders (organization_id, delivery_status);

create index if not exists idx_orders_created_at
on public.orders (organization_id, created_at desc);


create table if not exists public.order_items (
  id uuid primary key default gen_random_uuid(),

  organization_id uuid not null references public.organizations(id) on delete cascade,

  order_id uuid not null references public.orders(id) on delete cascade,

  product_id uuid references public.products(id) on delete set null,

  product_name text not null,
  product_sku text,

  quantity int not null default 1,

  unit_price integer not null default 0,
  total_price integer not null default 0,

  selected_options jsonb default '{}'::jsonb,
  -- 예: {"color":"black","size":"L"}

  created_at timestamptz default now(),

  constraint order_items_quantity_check
    check (quantity > 0),

  constraint order_items_unit_price_check
    check (unit_price >= 0),

  constraint order_items_total_price_check
    check (total_price >= 0)
);

create index if not exists idx_order_items_org
on public.order_items (organization_id);

create index if not exists idx_order_items_order
on public.order_items (order_id);

create index if not exists idx_order_items_product
on public.order_items (product_id);