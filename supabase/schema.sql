-- ============================================================================
-- Hemmy / ADF Agent — schema Supabase (Postgres)
-- ============================================================================
-- Decisioni prese con l'utente (2026-09-24):
--   1. Autenticazione app -> Supabase Auth (auth.users). Niente più pbkdf2/sessioni
--      custom: profiles.id COINCIDE con auth.users.id.
--   2. Tool generati a runtime (plugins) -> salvati anche a DB (sorgente Python +
--      manifest), perché su Cloud Run il filesystem è effimero. Il backend li
--      rimaterializza su disco all'avvio prima di importarli.
--   3. Audit log delle scritture -> tabella DB invece di file JSONL locale
--      (Cloud Run = istanze multiple, filesystem non condiviso).
--
-- NON persistito (scelta di sicurezza invariata): i token OAuth verso Azure/GitHub
-- (auth/oauth_providers.py) restano SOLO in memoria di processo, mai su disco né su
-- DB — coerente col principio "nessuna traccia di credenziali in giro". Idem per i
-- segreti cloud: qui sotto c'è SOLO lo storage cifrato dei segreti applicativi
-- dell'utente (es. la propria API key LLM), esattamente come faceva già
-- auth/users.py con SQLite.
--
-- Esecuzione: incolla nell'SQL editor di Supabase (o `supabase db push` se usi la CLI),
-- su un progetto nuovo o comunque prima di collegare il backend.
-- ============================================================================

create extension if not exists pgcrypto;

-- ----------------------------------------------------------------------------
-- profiles: dati applicativi legati a un utente Supabase Auth (1:1 con auth.users)
-- ----------------------------------------------------------------------------
create table if not exists public.profiles (
    id            uuid primary key references auth.users(id) on delete cascade,
    username      text not null unique,
    role          text not null default 'engineer'
                    check (role in ('architect', 'engineer', 'analyst')),
    settings      jsonb not null default '{}'::jsonb,  -- config NON segreta (tenant, RG, factory, repo, modello LLM...)
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now()
);

comment on table public.profiles is
  'Profilo applicativo per utente (ruolo, settings non-segrete). id = auth.users.id.';
comment on column public.profiles.settings is
  'JSON libero; il backend rifiuta chiavi che sembrano segreti (secret/password/token/api_key/connection_string) — vanno in user_secrets.';

-- Crea automaticamente il profilo alla registrazione (Supabase Auth trigger standard).
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer set search_path = public
as $$
begin
  insert into public.profiles (id, username)
  values (
    new.id,
    coalesce(new.raw_user_meta_data->>'username', split_part(new.email, '@', 1))
  )
  on conflict (id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- ----------------------------------------------------------------------------
-- user_secrets: segreti applicativi per-utente, cifrati lato backend (SecretBox/
-- pynacl con master key locale al backend, ESATTAMENTE come oggi). Il DB vede solo
-- ciphertext base64: qui non passa mai nulla in chiaro.
-- ----------------------------------------------------------------------------
create table if not exists public.user_secrets (
    user_id       uuid not null references public.profiles(id) on delete cascade,
    name          text not null,                -- es. 'deepseek_api_key'
    value_enc     text not null,                -- ciphertext (nacl.secret.SecretBox, base64)
    updated_at    timestamptz not null default now(),
    primary key (user_id, name)
);

comment on table public.user_secrets is
  'Segreti applicativi per-utente cifrati dal backend prima dell''INSERT. Mai testo in chiaro qui.';

-- ----------------------------------------------------------------------------
-- conversations: cronologia chat isolata per utente (una riga = l'intera history)
-- ----------------------------------------------------------------------------
create table if not exists public.conversations (
    user_id       uuid primary key references public.profiles(id) on delete cascade,
    messages      jsonb not null default '[]'::jsonb,
    pending       text,          -- domanda in corso non ancora completata (resume dopo crash/deploy)
    updated_at    timestamptz not null default now()
);

comment on table public.conversations is
  'Cronologia chat per utente. "pending" tiene traccia di un turno interrotto per permettere il resume esplicito (mai auto-eseguito).';

-- ----------------------------------------------------------------------------
-- plugins: tool generati a runtime (meta.* tools), sorgente + manifest persistiti
-- perché il filesystem di Cloud Run è effimero. scope='user' = isolato per
-- proprietario; scope='global' = condiviso con tutti gli utenti (promosso).
-- ----------------------------------------------------------------------------
create table if not exists public.plugins (
    id              bigint generated always as identity primary key,
    scope           text not null default 'user' check (scope in ('user', 'global')),
    owner_user_id   uuid references public.profiles(id) on delete cascade,  -- NULL se scope='global'
    module_name     text not null,          -- nome file/modulo, es. 'check_blob_tier'
    source_code     text not null,          -- sorgente Python completo del plugin
    manifest        jsonb not null,         -- MANIFEST dichiarato nel file (tools: nome/doc/write/entrypoint)
    status          text not null default 'active' check (status in ('active', 'disabled')),
    created_by      uuid references public.profiles(id) on delete set null,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now(),
    constraint plugins_scope_owner_chk check (
        (scope = 'global' and owner_user_id is null) or
        (scope = 'user' and owner_user_id is not null)
    )
);

-- Un modulo con lo stesso nome non può ripetersi nello stesso ambito (utente o globale).
create unique index if not exists plugins_global_name_uk
    on public.plugins (module_name) where scope = 'global';
create unique index if not exists plugins_user_name_uk
    on public.plugins (owner_user_id, module_name) where scope = 'user';

comment on table public.plugins is
  'Tool auto-generati (meta.*): sorgente Python + manifest. Il backend li riscrive su disco effimero all''avvio prima di importarli.';

-- ----------------------------------------------------------------------------
-- audit_log: ogni azione [WRITE] (approvata/negata/fallita), con redaction già
-- applicata lato backend prima dell'INSERT (qui non deve mai arrivare un segreto).
-- ----------------------------------------------------------------------------
create table if not exists public.audit_log (
    id            bigint generated always as identity primary key,
    user_id       uuid references public.profiles(id) on delete set null,
    action        text not null,             -- es. 'adf.start_pipeline_run'
    args          jsonb not null default '{}'::jsonb,   -- già passato da redact_secrets()
    outcome       text not null check (outcome in ('success', 'error', 'denied')),
    error         text,
    created_at    timestamptz not null default now()
);

create index if not exists audit_log_user_created_idx
    on public.audit_log (user_id, created_at desc);
create index if not exists audit_log_action_idx
    on public.audit_log (action);

comment on table public.audit_log is
  'Log di audit delle scritture. Sostituisce il file JSONL locale (multi-istanza Cloud Run, filesystem non condiviso).';

-- ============================================================================
-- Row Level Security
-- ============================================================================
-- Il backend parlerà a Supabase con la service_role key (bypassa sempre RLS): le
-- policy sotto sono difesa-in-profondità nel caso in futuro il frontend chiami
-- Supabase direttamente con la chiave anon + JWT dell'utente (es. per letture
-- veloci di chat/settings senza passare dal backend).

alter table public.profiles     enable row level security;
alter table public.user_secrets enable row level security;
alter table public.conversations enable row level security;
alter table public.plugins      enable row level security;
alter table public.audit_log    enable row level security;

-- profiles: ognuno vede/aggiorna solo il proprio profilo.
create policy profiles_select_own on public.profiles
    for select using (auth.uid() = id);
create policy profiles_update_own on public.profiles
    for update using (auth.uid() = id);

-- user_secrets: MAI leggibile/scrivibile dal client (anon/authenticated) — solo
-- il backend con service_role key. Nessuna policy = accesso negato di default.

-- conversations: ognuno vede/scrive solo la propria cronologia.
create policy conversations_select_own on public.conversations
    for select using (auth.uid() = user_id);
create policy conversations_upsert_own on public.conversations
    for insert with check (auth.uid() = user_id);
create policy conversations_update_own on public.conversations
    for update using (auth.uid() = user_id);

-- plugins: un utente vede i propri tool + quelli globali; scrittura solo backend.
create policy plugins_select_own_or_global on public.plugins
    for select using (scope = 'global' or auth.uid() = owner_user_id);

-- audit_log: un utente vede solo le proprie righe (utile per un futuro pannello
-- "le mie azioni"); scrittura solo backend (service_role).
create policy audit_log_select_own on public.audit_log
    for select using (auth.uid() = user_id);
