-- ツールが「いま動かしてよいか」をサーバーに聞くための関数と表。
-- ツール（AccessCheck.py）は起動時・マクロの開始時・動作中に、この関数 check_access だけを呼ぶ。
--
-- なぜ: 締め出す UID の一覧と「使える最低の版」を exe に入れると、ビルドし直さないと変えられず、
--   一覧（生の UID）が全利用者の PC に平文で出る。サーバーに置けば、ここで 1 行足すだけで締め出せる。
--
-- 前提（実際の表と違えば直してから流すこと。未確認）:
--   - 表は public."Users"、列は "VRChat_uid"（text）。get_transformed_uid.sql と同じ
--   - Users に列 last_seen_at・last_checked_at・last_version を足す（下の 3）
--
-- ツールが送るのは、自分の VRChat の uid（読めなければ null）と、ツールの版（例 v1.5.0）だけ。
-- 返るのは {"allowed": bool, "min_version": text, "require_uid": bool}。締め出しの理由は返さない。
--   allowed      : 締め出しでなければ true
--   min_version  : これより古い版は使えない（AppSettings の min_version。形は v1.2.3）
--   require_uid  : false なら、uid が読めなくても動かす（AppSettings の require_uid が 'false' のときだけ false）
--
-- 流す順: SQL → 新しい exe で確かめる → リリース。
--   先にツールだけ新しくすると、関数が無く 404 になり「つながらない」扱いになる
--   （最後の確認から 3 日以内ならそのまま動くが、初めての人・3 日を過ぎた人は動かない）。
--   v1.4.1 以前のツールはこの関数を呼ばない（古い版への対応は何もしない）。
--
-- 古い版の見分け方: 古い版は get_transformed_uid だけを呼ぶので、last_seen_at が新しいのに
--   last_checked_at が無い（古い）人は古い版。
--
-- 締め出すとき（UID は伏せた見本。本物は書かないこと）:
--   insert into public."Bans" ("VRChat_uid", note) values ('usr_xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx', 'メモ');
-- 解くとき:
--   delete from public."Bans" where "VRChat_uid" = 'usr_xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx';
-- 最低の版を上げるとき（これより古い版は「この版は使えません」と出て始められない）:
--   update public."AppSettings" set value = 'v1.5.0' where key = 'min_version';
-- uid が読めなくても動かすとき（普段は true のまま）:
--   update public."AppSettings" set value = 'false' where key = 'require_uid';

-- 1. 締め出しの一覧。anon・authenticated からは何も読めない・書けない（関数だけが読む）
create table if not exists public."Bans" (
  "VRChat_uid" text primary key,
  note text,
  created_at timestamptz not null default now()
);
alter table public."Bans" enable row level security;
revoke all on public."Bans" from anon, authenticated;

-- 2. サーバーの設定（key / value）。これも閉じる
create table if not exists public."AppSettings" (
  key text primary key,
  value text
);
alter table public."AppSettings" enable row level security;
revoke all on public."AppSettings" from anon, authenticated;

insert into public."AppSettings" (key, value) values ('min_version', 'v0.0.0') on conflict do nothing;
insert into public."AppSettings" (key, value) values ('require_uid', 'true') on conflict do nothing;

-- 3. Users に、最後に見た時刻・最後に確認した時刻・そのときの版を持たせる
alter table public."Users" add column if not exists last_seen_at timestamptz;
alter table public."Users" add column if not exists last_checked_at timestamptz;
alter table public."Users" add column if not exists last_version text;

-- 4. 関数。所有者の権限で Bans・AppSettings・Users を読む（security definer）
create or replace function public.check_access(p_vrchat_uid text, p_version text)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_banned boolean := false;
  v_min text;
  v_require text;
begin
  -- uid が来たときだけ、締め出しかを見て、確認の時刻と版を残す（Users に行が無ければ何もしない）
  if p_vrchat_uid is not null and length(p_vrchat_uid) between 1 and 64 then
    select exists (select 1 from public."Bans" where "VRChat_uid" = p_vrchat_uid) into v_banned;
    update public."Users"
       set last_checked_at = now(), last_version = left(p_version, 32)
     where "VRChat_uid" = p_vrchat_uid;
  end if;

  select value into v_min from public."AppSettings" where key = 'min_version';
  select value into v_require from public."AppSettings" where key = 'require_uid';

  return jsonb_build_object(
    'allowed', not v_banned,
    'min_version', coalesce(v_min, 'v0.0.0'),
    'require_uid', v_require is distinct from 'false'
  );
end
$$;

-- 5. anon（ツール）からはこの関数だけを呼べる
revoke all on function public.check_access(text, text) from public;
grant execute on function public.check_access(text, text) to anon, authenticated;
