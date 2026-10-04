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
-- 流す順:
--   1. この関数（1〜3）を SQL Editor で流す。新しいツールはこの時点でこれを使う
--   2. 前の版のツールを使っている人がいなくなってから、4（Users を閉じる）を流す。
--      閉じると、前の版は transformed_uid を取れなくなる（統計は uid なしで送られる）

-- 1. 一意（同時に登録されても二重にならない・番号が重ならない）
create unique index if not exists users_vrchat_uid_key on public."Users" ("VRChat_uid");
create unique index if not exists users_transformed_uid_key on public."Users" (transformed_uid);

-- 2. 関数。所有者の権限で Users を読む（security definer）ので、呼ぶ側に Users の権限は要らない
create or replace function public.get_transformed_uid(p_vrchat_uid text)
returns smallint
language plpgsql
security definer
set search_path = public
as $$
declare
  found smallint;
  candidate smallint;
begin
  -- 形は緩く見る（古いアカウントは usr_ で始まらない ID のことがある）
  if p_vrchat_uid is null or length(p_vrchat_uid) = 0 or length(p_vrchat_uid) > 64 then
    raise exception 'VRChat の uid ではありません';
  end if;

  select transformed_uid into found from public."Users" where "VRChat_uid" = p_vrchat_uid;
  if found is not null then
    return found;
  end if;

  -- 空いている番号を引く。重なったら引き直す（int2 の 65536 通り）
  for i in 1..200 loop
    candidate := (floor(random() * 65536) - 32768)::smallint;
    begin
      insert into public."Users" ("VRChat_uid", transformed_uid) values (p_vrchat_uid, candidate);
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
revoke all on function public.get_transformed_uid(text) from public;
grant execute on function public.get_transformed_uid(text) to anon, authenticated;

-- 4. Users を直接は読ませない・書かせない（前の版のツールが残っている間は流さない）
-- alter table public."Users" enable row level security;
-- revoke select, insert, update, delete on public."Users" from anon, authenticated;
