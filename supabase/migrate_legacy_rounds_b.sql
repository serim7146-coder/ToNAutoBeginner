-- ①〜④ は 2026-11-01 に流し済み。⑤（残した表と対応表を消す）は数日後。
-- 無印 ToNRounds の全行を v2（ToNRounds_v2）へ写し、無印をしまう（B）。2026-11-01
--
-- 確かめた前提（2026-11-01）:
--   - 無印への最後の書き込みは 2026-10-25 04:26 JST（318,204 行）。v2 は 2026-10-23 10:20 JST から（89,842 行）。
--   - ToNRounds / ToNRounds_v2 に一意の制約・索引は無い。同じラウンドのまとめは register_round_v2 が
--     ToNRoundsRecent_v2（instance_key・time・row_ctid。10 分で消える）で行う。インスタンスは行に残らない。
--   - 無印の列: time, round, map_id, terror1..3, transformed_uid, other_uids。v2 はこれに players。
--   - 無印の番号は ListTool の番号。v2 はツール独自の番号。対応表 terror_id_map（330 行）で直す。
--     8 Pages のラウンド（round = 105）は 8 Pages の行（eight_pages）を先に引き、無ければ通常の行。どちらにも無ければ NULL。
--   - 重なりの期間（10/23 10:20〜10/25 04:26）の無印の行は 8,576 行。同じラウンドが v2 にもある可能性があるので、
--     「同じ round・map・テラー 3 つ・開始が 15 秒以内」の v2 の行があれば写さず、その行の other_uids に無印の
--     transformed_uid を足す（register_round_v2 と同じ考え。上限 20）。
--
-- 流し方（Supabase の SQL Editor。1 つの枠が 1 つのトランザクション）:
--   ① 対応表を作る（何度流しても同じ）
--   ② 確認（読むだけ）。rows_with_unknown_number が多ければ止めて相談
--   ③ 写す（1 回だけ流す。2 回流すと二重になる。最後の 1 行で件数を確かめる）
--   ④ 無印をしまう（③ の件数が合ってから。関数を消し、表は名前を変えて残す）
--   ⑤ 数日して統計画面に問題が無ければ、残した表と対応表を消す
-- ツール側: ④ の後、古い版は無印へ送れなくなる（404）。今の版は無印の取り込みが 404 でも v2 だけで動く。
-- 次のリリースで LegacyRoundsTable（無印の取り込み）を消す。

-- ═══ ① 対応表 ═══════════════════════════════════════════════
create table if not exists public.terror_id_map (
  legacy      smallint not null,
  eight_pages boolean  not null,
  own         smallint not null,
  primary key (legacy, eight_pages)
);
alter table public.terror_id_map enable row level security;   -- ポリシーを作らない = anon からは読めも書けもしない

-- BEGIN terror_id_map rows（assets/data/terror_ids.json から生成。330 行: 通常 268・8 Pages 62）
insert into public.terror_id_map (legacy, eight_pages, own) values
  (0, false, 0),
  (1, false, 1),
  (2, false, 2),
  (3, false, 3),
  (4, false, 4),
  (5, false, 5),
  (6, false, 6),
  (7, false, 7),
  (8, false, 8),
  (9, false, 9),
  (10, false, 10),
  (11, false, 11),
  (12, false, 12),
  (13, false, 13),
  (14, false, 14),
  (15, false, 15),
  (16, false, 16),
  (17, false, 17),
  (18, false, 18),
  (19, false, 19),
  (20, false, 20),
  (21, false, 21),
  (22, false, 22),
  (23, false, 23),
  (24, false, 24),
  (25, false, 25),
  (26, false, 26),
  (27, false, 27),
  (28, false, 28),
  (29, false, 29),
  (30, false, 30),
  (31, false, 31),
  (32, false, 32),
  (33, false, 33),
  (34, false, 34),
  (35, false, 35),
  (36, false, 36),
  (37, false, 37),
  (38, false, 38),
  (39, false, 39),
  (40, false, 40),
  (41, false, 41),
  (42, false, 42),
  (43, false, 43),
  (44, false, 44),
  (45, false, 45),
  (46, false, 46),
  (47, false, 47),
  (48, false, 48),
  (49, false, 49),
  (50, false, 50),
  (51, false, 51),
  (52, false, 52),
  (53, false, 53),
  (54, false, 54),
  (55, false, 55),
  (56, false, 56),
  (57, false, 57),
  (58, false, 58),
  (59, false, 59),
  (60, false, 60),
  (61, false, 61),
  (62, false, 62),
  (63, false, 63),
  (64, false, 64),
  (65, false, 65),
  (66, false, 66),
  (67, false, 67),
  (68, false, 68),
  (69, false, 69),
  (70, false, 70),
  (71, false, 71),
  (72, false, 72),
  (73, false, 73),
  (74, false, 74),
  (75, false, 75),
  (76, false, 76),
  (77, false, 77),
  (78, false, 78),
  (79, false, 79),
  (80, false, 80),
  (81, false, 81),
  (82, false, 82),
  (83, false, 83),
  (84, false, 84),
  (85, false, 85),
  (86, false, 86),
  (87, false, 87),
  (88, false, 88),
  (89, false, 89),
  (90, false, 90),
  (91, false, 91),
  (92, false, 92),
  (93, false, 93),
  (94, false, 94),
  (95, false, 95),
  (96, false, 96),
  (97, false, 97),
  (98, false, 98),
  (99, false, 99),
  (100, false, 100),
  (101, false, 101),
  (102, false, 102),
  (103, false, 103),
  (104, false, 104),
  (105, false, 105),
  (106, false, 106),
  (107, false, 107),
  (108, false, 108),
  (109, false, 109),
  (110, false, 110),
  (111, false, 111),
  (112, false, 112),
  (113, false, 113),
  (114, false, 114),
  (115, false, 115),
  (116, false, 116),
  (117, false, 117),
  (118, false, 118),
  (119, false, 119),
  (120, false, 120),
  (121, false, 121),
  (122, false, 122),
  (123, false, 123),
  (124, false, 124),
  (125, false, 125),
  (126, false, 126),
  (127, false, 127),
  (128, false, 128),
  (129, false, 129),
  (130, false, 130),
  (131, false, 131),
  (132, false, 132),
  (133, false, 133),
  (134, false, 1000),
  (135, false, 1001),
  (136, false, 1002),
  (137, false, 1003),
  (138, false, 1004),
  (139, false, 1005),
  (140, false, 1006),
  (141, false, 1007),
  (142, false, 1008),
  (143, false, 1009),
  (144, false, 1010),
  (145, false, 1011),
  (146, false, 1012),
  (147, false, 1013),
  (148, false, 1014),
  (149, false, 1015),
  (150, false, 1016),
  (151, false, 1017),
  (152, false, 1018),
  (153, false, 1019),
  (154, false, 1020),
  (155, false, 1021),
  (156, false, 1022),
  (157, false, 1023),
  (158, false, 1024),
  (159, false, 1025),
  (160, false, 1026),
  (161, false, 1027),
  (162, false, 1028),
  (163, false, 1029),
  (164, false, 1030),
  (165, false, 1031),
  (166, false, 1032),
  (167, false, 1033),
  (168, false, 1034),
  (169, false, 1035),
  (190, false, 4000),
  (191, false, 4001),
  (192, false, 4002),
  (196, false, 4003),
  (197, false, 4004),
  (198, false, 4005),
  (199, false, 4006),
  (200, false, 2000),
  (201, false, 2001),
  (202, false, 2002),
  (203, false, 2003),
  (204, false, 2004),
  (205, false, 2005),
  (206, false, 2006),
  (207, false, 2007),
  (208, false, 2008),
  (209, false, 2009),
  (210, false, 2010),
  (211, false, 2011),
  (212, false, 2012),
  (213, false, 2013),
  (214, false, 2014),
  (215, false, 2015),
  (216, false, 2016),
  (217, false, 2017),
  (218, false, 2018),
  (219, false, 2019),
  (220, false, 2020),
  (221, false, 2021),
  (222, false, 2022),
  (223, false, 2023),
  (224, false, 2024),
  (225, false, 2025),
  (226, false, 2026),
  (227, false, 2027),
  (228, false, 2028),
  (229, false, 2029),
  (230, false, 2030),
  (231, false, 2031),
  (232, false, 2032),
  (233, false, 2033),
  (234, false, 2034),
  (235, false, 2035),
  (236, false, 2036),
  (237, false, 2037),
  (238, false, 2038),
  (239, false, 2039),
  (240, false, 2040),
  (241, false, 2041),
  (242, false, 2042),
  (243, false, 2043),
  (244, false, 2044),
  (245, false, 2045),
  (246, false, 2046),
  (247, false, 2047),
  (248, false, 2048),
  (249, false, 2049),
  (250, false, 2050),
  (251, false, 2051),
  (252, false, 2052),
  (253, false, 2053),
  (254, false, 2054),
  (255, false, 2055),
  (256, false, 2056),
  (257, false, 2057),
  (258, false, 2058),
  (259, false, 2059),
  (260, false, 2060),
  (261, false, 2061),
  (262, false, 2062),
  (263, false, 2063),
  (264, false, 2064),
  (265, false, 2065),
  (266, false, 2066),
  (267, false, 2067),
  (268, false, 2068),
  (269, false, 2069),
  (270, false, 2070),
  (271, false, 2071),
  (272, false, 2072),
  (273, false, 2073),
  (274, false, 2074),
  (275, false, 2075),
  (276, false, 2076),
  (277, false, 2077),
  (278, false, 2078),
  (279, false, 2079),
  (280, false, 2080),
  (281, false, 2081),
  (282, false, 2082),
  (283, false, 2083),
  (312, false, 4007),
  (313, false, 4008),
  (314, false, 4009),
  (315, false, 4010),
  (316, false, 4011),
  (317, false, 4012),
  (1283, false, 4013),
  (0, true, 3014),
  (1, true, 3015),
  (2, true, 3016),
  (4, true, 3017),
  (8, true, 3018),
  (9, true, 3013),
  (14, true, 3012),
  (19, true, 3019),
  (20, true, 3020),
  (24, true, 3022),
  (25, true, 3021),
  (30, true, 3010),
  (31, true, 3023),
  (33, true, 3011),
  (35, true, 3009),
  (37, true, 3033),
  (39, true, 3059),
  (44, true, 3028),
  (46, true, 3030),
  (51, true, 3008),
  (52, true, 3007),
  (56, true, 3006),
  (60, true, 3027),
  (61, true, 3004),
  (62, true, 3005),
  (69, true, 3024),
  (70, true, 3057),
  (71, true, 3035),
  (73, true, 3025),
  (75, true, 3003),
  (76, true, 3001),
  (81, true, 3002),
  (82, true, 3031),
  (85, true, 3055),
  (86, true, 3026),
  (89, true, 3040),
  (101, true, 3038),
  (103, true, 3039),
  (105, true, 3041),
  (109, true, 3042),
  (112, true, 3044),
  (113, true, 3043),
  (116, true, 3034),
  (122, true, 3029),
  (128, true, 3047),
  (132, true, 3048),
  (135, true, 3050),
  (140, true, 3052),
  (141, true, 3051),
  (144, true, 3049),
  (147, true, 3036),
  (152, true, 3054),
  (155, true, 3053),
  (156, true, 3060),
  (170, true, 3032),
  (171, true, 3045),
  (172, true, 3046),
  (173, true, 3037),
  (174, true, 3061),
  (177, true, 3000),
  (193, true, 3056),
  (194, true, 3058)
on conflict (legacy, eight_pages) do nothing;
-- END terror_id_map rows

-- ═══ ② 確認（読むだけ）═════════════════════════════════════════
-- unknown_numbers: 対応表に無い番号（その欄は NULL で写る）。rows_with_unknown_number: その番号を含む行数
select
  (select count(*) from public."ToNRounds")    as legacy_rows,
  (select count(*) from public."ToNRounds_v2") as v2_rows,
  (select count(*) from public."ToNRounds" r
    where (r.terror1 is not null and not exists (select 1 from public.terror_id_map m where m.legacy = r.terror1))
       or (r.terror2 is not null and not exists (select 1 from public.terror_id_map m where m.legacy = r.terror2))
       or (r.terror3 is not null and not exists (select 1 from public.terror_id_map m where m.legacy = r.terror3)))
                                               as rows_with_unknown_number,
  (select string_agg(t::text, ', ' order by t) from (
     select distinct t from (
       select terror1 t from public."ToNRounds" union all
       select terror2   from public."ToNRounds" union all
       select terror3   from public."ToNRounds") x
     where t is not null and not exists (select 1 from public.terror_id_map m where m.legacy = x.t)) u)
                                               as unknown_numbers;

-- ═══ ③ 写す（1 回だけ）═════════════════════════════════════════
-- 番号を直した無印の行（src は無印の行の位置。重なりの判定に使う）
create temp table legacy_conv as
select r.ctid as src, r."time", r.round, r.map_id,
       case when r.round = 105 then coalesce(e1.own, n1.own) else n1.own end as terror1,
       case when r.round = 105 then coalesce(e2.own, n2.own) else n2.own end as terror2,
       case when r.round = 105 then coalesce(e3.own, n3.own) else n3.own end as terror3,
       r.transformed_uid, r.other_uids
  from public."ToNRounds" r
  left join public.terror_id_map n1 on n1.legacy = r.terror1 and not n1.eight_pages
  left join public.terror_id_map e1 on e1.legacy = r.terror1 and     e1.eight_pages
  left join public.terror_id_map n2 on n2.legacy = r.terror2 and not n2.eight_pages
  left join public.terror_id_map e2 on e2.legacy = r.terror2 and     e2.eight_pages
  left join public.terror_id_map n3 on n3.legacy = r.terror3 and not n3.eight_pages
  left join public.terror_id_map e3 on e3.legacy = r.terror3 and     e3.eight_pages;

-- 重なりの期間で、v2 に同じラウンドがある無印の行（1 行につき最も近い v2 の行 1 つ）
-- coalesce(…, -1) は NULL 同士も同じと見るため（ハッシュ結合が効く形）
create temp table legacy_dup as
select distinct on (c.src) c.src, v.ctid as dst, c.transformed_uid
  from legacy_conv c
  join public."ToNRounds_v2" v
    on v.round = c.round
   and coalesce(v.map_id,  -1) = coalesce(c.map_id,  -1)
   and coalesce(v.terror1, -1) = coalesce(c.terror1, -1)
   and coalesce(v.terror2, -1) = coalesce(c.terror2, -1)
   and coalesce(v.terror3, -1) = coalesce(c.terror3, -1)
   and abs(v."time" - c."time") <= 15
 where c."time" >= (select min("time") from public."ToNRounds_v2") - 15
 order by c.src, abs(v."time" - c."time");

-- 同じラウンドだった v2 の行に、無印側の transformed_uid を other_uids として足す（重複なし・自分は除く・20 まで）
update public."ToNRounds_v2" v
   set other_uids = (
     select array_agg(u) from (
       select distinct u from unnest(coalesce(v.other_uids, '{}'::smallint[]) || d.uids) as u
        where u is distinct from v.transformed_uid
        limit 20) s)
  from (select dst, array_agg(distinct transformed_uid) as uids
          from legacy_dup where transformed_uid is not null group by dst) d
 where v.ctid = d.dst;

-- 残り（重なりでない行）を写す。人数は NULL
insert into public."ToNRounds_v2" ("time", round, map_id, terror1, terror2, terror3, transformed_uid, other_uids, players)
select c."time", c.round, c.map_id, c.terror1, c.terror2, c.terror3, c.transformed_uid, c.other_uids, null
  from legacy_conv c
 where not exists (select 1 from legacy_dup d where d.src = c.src);

-- 件数の確認: v2_rows = 89,842 + 318,204 - merged_as_duplicate になっているはず
select (select count(*) from legacy_conv)                 as legacy_rows,
       (select count(*) from legacy_dup)                  as merged_as_duplicate,
       (select count(*) from public."ToNRounds_v2")       as v2_rows;
-- （一時の表は接続が切れれば消える。同じ枠でもう一度流すときのために消しておく）
-- drop table legacy_conv, legacy_dup;

-- ═══ ④ 無印をしまう（③ の件数が合ってから）═══════════════════════
-- 無印の関数（引数の形が何であれ消す）。古い版のツールはこれで 404 になり、無印へ送れなくなる
do $$
declare f record;
begin
  for f in select oid::regprocedure as sig from pg_proc
            where proname = 'register_round' and pronamespace = 'public'::regnamespace loop
    execute 'drop function ' || f.sig;
  end loop;
end $$;
alter table public."ToNRounds"       rename to "ToNRounds_legacy_bak";
alter table public."ToNRoundsRecent" rename to "ToNRoundsRecent_legacy_bak";   -- 無印の関数だけが使っていた補助の表

-- ═══ ⑤ 数日後（統計画面に問題が無ければ）═══════════════════════
-- drop table public."ToNRounds_legacy_bak";
-- drop table public."ToNRoundsRecent_legacy_bak";
-- drop table public.terror_id_map;
