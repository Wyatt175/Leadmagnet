-- =====================================================================
-- Concrete Bids: Supabase foundation
-- Paste this whole file into Supabase > SQL Editor > New query > Run.
-- Safe to run more than once.
-- =====================================================================


-- ---------------------------------------------------------------------
-- 1. BIDS TABLE
-- Every bid your collectors find lands here.
-- ---------------------------------------------------------------------
create table if not exists public.bids (
  id             uuid primary key default gen_random_uuid(),
  source         text not null,              -- where it came from: 'u3p', 'udot', ...
  source_bid_id  text not null,              -- the bid's ID on that source (stops duplicates)
  title          text not null,
  agency         text,                       -- who issued it
  category       text,                       -- e.g. 'concrete'
  description    text,
  close_date     timestamptz,                -- when bidding ends
  url            text,                       -- link back to the original posting
  status         text not null default 'open',
  created_at     timestamptz not null default now(),  -- when WE found it (powers freshness + head-start timer)

  -- The duplicate shield: the same bid from the same source can only exist once.
  constraint bids_source_unique unique (source, source_bid_id)
);

-- Speed up the two sorts the app will use most.
create index if not exists bids_created_at_idx on public.bids (created_at desc);
create index if not exists bids_close_date_idx on public.bids (close_date);

-- Security: logged-in contractors can READ bids. Nobody can write from the app.
-- (Your collector writes using the secret service key, which bypasses these rules.)
alter table public.bids enable row level security;

drop policy if exists "Logged-in users can read bids" on public.bids;
create policy "Logged-in users can read bids"
  on public.bids for select
  to authenticated
  using (true);


-- ---------------------------------------------------------------------
-- 2. PROFILES TABLE
-- Extends Supabase's built-in login users with contractor details.
-- These fields feed the matching score and notifications later.
-- ---------------------------------------------------------------------
create table if not exists public.profiles (
  id             uuid primary key references auth.users(id) on delete cascade,
  company_name   text,
  home_zip       text,
  radius_miles   int default 50,
  min_job_size   numeric,                    -- dollars
  max_job_size   numeric,                    -- dollars
  project_types  text[] default '{}',        -- e.g. {'flatwork','structural','paving'}
  notify_by      text default 'email'
                 check (notify_by in ('email', 'sms', 'both', 'none')),
  phone          text,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now()
);

-- Security: each contractor can only see and edit their own profile.
alter table public.profiles enable row level security;

drop policy if exists "Users read own profile" on public.profiles;
create policy "Users read own profile"
  on public.profiles for select
  to authenticated
  using (auth.uid() = id);

drop policy if exists "Users update own profile" on public.profiles;
create policy "Users update own profile"
  on public.profiles for update
  to authenticated
  using (auth.uid() = id)
  with check (auth.uid() = id);


-- ---------------------------------------------------------------------
-- 3. AUTO-CREATE A PROFILE WHEN SOMEONE SIGNS UP
-- So every new contractor automatically gets a blank profile row.
-- ---------------------------------------------------------------------
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.profiles (id) values (new.id)
  on conflict (id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();
