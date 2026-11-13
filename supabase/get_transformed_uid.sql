-- VRChat の uid → transformed_uid（int2）を返す関数。無ければ割り当てる。
-- ツール（ConnectDB.send_Users）は Users の表を直接読まず、この関数だけを呼ぶ。
--
-- なぜ: anon キーは exe から取り出せる。Users を anon で読めると、誰でも
--   GET /rest/v1/Users?select=*
-- で全員の VRChat uid と transformed_uid の対応を引ける（統計の行が誰のものか分かる）。
--
-- 前提（実際の表と違えば直してから流すこと。未確認）:
--   - 表は public."Users"、列は "VRChat_uid"（text）と transformed_uid（int2 / smallint）
--   - 下の一意制約を足す。既に同じ uid・同じ番号の重複行があると作れないので、先に確かめる:
--       select "VRChat_uid", count(*) from public."Users" group by 1 having count(*) > 1;
--       select transformed_uid, count(*) from public."Users" group by 1 having count(*) > 1;
--
-- 表示名（p_name）: ツールはログインの行で読んだ自分の表示名を添えて呼ぶ。DB 側が Users.name に入れる
--   （最新だけ。変わっていれば name_updated_at も更新する）。ツールは表示名をログに出さない。
--
-- 最後に見た時刻（last_seen_at）: この関数が呼ばれるたびに更新する。古い版（v1.4.1 以前）は
--   この関数だけを呼び check_access は呼ばないので、last_seen_at が新しいのに last_checked_at
--   （check_access.sql の列）が無い（古い）人は古い版。
--
-- 流す順: SQL → リリース。
--   先にツールだけ新しくすると、引数が合わず 404 → transformed_uid なしで動く（統計の番号が抜ける）。
--   古い版のツールは p_name なしで呼ぶ → default null なので今まで通り動く。
--
-- 流し方:
--   1. この関数（1〜3）を SQL Editor で流す。新しいツールはこの時点でこれを使う
--   2. 4（Users を閉じる）は流し済み。
--
-- 注意: drop と create は同じ実行で流すこと。古い関数 (text) と新しい関数 (text, text default null) が
--   両方あると、PostgREST が候補を選べず 300 で止まる。

-- 1. 一意（同時に登録されても二重にならない・番号が重ならない）
create unique index if not exists users_vrchat_uid_key on public."Users" ("VRChat_uid");
create unique index if not exists users_transformed_uid_key on public."Users" (transformed_uid);

-- 1b. 表示名の列（ツールが読んだ最新の名前と、その更新時刻）
alter table public."Users" add column if not exists name text;
alter table public."Users" add column if not exists name_updated_at timestamptz;
alter table public."Users" add column if not exists last_seen_at timestamptz;     -- check_access.sql と同じ列

-- 2. 関数。所有者の権限で Users を読む（security definer）ので、呼ぶ側に Users の権限は要らない
--    古い (text) を消してから新しい (text, text default null) を作る（同じ実行で流す）
drop function if exists public.get_transformed_uid(text);

create function public.get_transformed_uid(p_vrchat_uid text, p_name text default null)
returns smallint
language plpgsql
security definer
set search_path = public
as $$
declare
  found smallint;
  candidate smallint;
  found_name text;
  v_name text;
begin
  -- 形は緩く見る（古いアカウントは usr_ で始まらない ID のことがある）
  if p_vrchat_uid is null or length(p_vrchat_uid) = 0 or length(p_vrchat_uid) > 64 then
    raise exception 'VRChat の uid ではありません';
  end if;

  -- 表示名は前後の空白を除いて 64 文字まで。空なら null（名前なしの呼び出しと同じ）
  v_name := left(btrim(p_name), 64);
  if v_name = '' then
    v_name := null;
  end if;

  select transformed_uid, name into found, found_name from public."Users" where "VRChat_uid" = p_vrchat_uid;
  if found is not null then
    -- 見た時刻は毎回更新する（名前の更新とは別）
    update public."Users" set last_seen_at = now() where "VRChat_uid" = p_vrchat_uid;
    -- 名前が来ていて、今のと違うときだけ更新する
    if v_name is not null and v_name is distinct from found_name then
      update public."Users" set name = v_name, name_updated_at = now() where "VRChat_uid" = p_vrchat_uid;
    end if;
    return found;
  end if;

  -- 空いている番号を引く。重なったら引き直す（int2 の 65536 通り）
  for i in 1..200 loop
    candidate := (floor(random() * 65536) - 32768)::smallint;
    begin
      insert into public."Users" ("VRChat_uid", transformed_uid, name, name_updated_at, last_seen_at)
        values (p_vrchat_uid, candidate, v_name, case when v_name is null then null else now() end, now());
      return candidate;
    exception when unique_violation then
      -- 同じ uid が同時に登録された → そちらの番号を返す。番号が重なった → 引き直す
      select transformed_uid into found from public."Users" where "VRChat_uid" = p_vrchat_uid;
      if found is not null then
        return found;
      end if;
    end;
  end loop;
  return null;      -- 割り当てられない（ほぼ埋まっている）
end
$$;

-- 3. anon（ツール）からはこの関数だけを呼べる
revoke all on function public.get_transformed_uid(text, text) from public;
grant execute on function public.get_transformed_uid(text, text) to anon, authenticated;

-- 4. Users を直接は読ませない・書かせない（流し済み）
-- alter table public."Users" enable row level security;
-- revoke select, insert, update, delete on public."Users" from anon, authenticated;
