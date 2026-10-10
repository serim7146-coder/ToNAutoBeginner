from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class WindowConfig:
    hwnd: int = 0
    log_path: Optional[Path] = None
    active: bool = True
    auto_begin: bool = True
    do_skip: bool = True
    cancel_afk: bool = True
    # DTM/Waldo を3クラ解放（3勝）の後も続行する
    cancel_afk_after_unlock: bool = False
    osc_port: int = 0       # 0 = OSC不可（従来のキーボード操作にフォールバック）
    # VRChatが値を送ってくるポート。0なら従来どおり osc_port+1 を使う
    # （--osc= の送信ポートは受信+1とは限らない）
    osc_out_port: int = 0
    voice_intermission: str = ""
    announce_intermission: bool = False
    # 問答無用で自爆するラウンド（privateのみ）。続行リストより優先する
    skip_rounds: set = field(default_factory=set)
    # 続行リストを見ずに自爆しない（privateのみ）。skip_rounds より優先する
    continue_rounds: set = field(default_factory=set)
    voice_continue: str = ""
    voice_fog: str = ""
    voice_item_lost: str = ""
    voice_foxy: str = ""
    voice_8pages: str = ""
    voice_punish: str = ""
    voice_list_lost: str = ""
    voice_unbound: str = ""
    voice_midnight: str = ""
    voice_alternate: str = ""
    voice_ghost: str = ""


@dataclass
class WindowState:
    instance_type: str = "public"
    # Joining 行の公開範囲（LogParser.ACCESS_*）。空＝判定できない（看破は NG 扱い）
    instance_access: str = ""
    # ── 霧の看破（ラウンドごと） ──
    fog_reading: bool = False             # Killers is unknown から公開/RoundOver まで
    early_read_hits: dict = field(default_factory=dict)   # 正規化した名前 → ID
    early_read_tid: Optional[int] = None  # 看破で使ったID
    early_read_void: bool = False         # 2種類以上のIDが出た → このラウンドは使わない
    # 看破の期間に objects の名前が1つでも当たったか（信用・void に関係なく）。DTM の否定
    fog_object_seen: bool = False
    # オブジェクトなし → DTM を確かめるログの時刻（0 = 確かめない）。1回だけ
    fog_no_object_deadline: float = 0.0
    fog_no_object_dtm: bool = False       # このラウンドでオブジェクトなし → DTM と判断した
    eight_pages_unknown_logged: bool = False   # 未登録の 8 Pages 番号の案内は1回だけ
    statistics_quiet: bool = False        # 看破の結果で送る統計は黙って送る
    log_pos: int = 0
    in_round: bool = False
    round_type: str = ""
    terror_ids: list[int] = field(default_factory=list)
    map_id: int = 0
    round_seq: int = 0
    round_over_time: float = 0.0   # RoundOverを受けた時刻（Begin移動の起点）
    # マクロの回（SharedState.begin_run）。前の回の窓がフリーズを張ったり解いたりしないため。
    # None は回を問わない（監視だけで使うとき・テスト）
    run_id: Optional[int] = field(default=None, compare=False)
    # 窓の番号（debug.log の行に付けるため。判定には使わない）
    window_idx: int = field(default=0, compare=False)
    # このラウンドで Begin 前の移動を最後までやったか（押し直しで2回動かさないため）。
    # ラウンド開始・RoundOver で False
    begin_move_done: bool = False
    round_end_seen: bool = False   # Verified Round End を受けたか（クリック可の合図）
    # 定期シグナル（約300秒周期のVerified）の追跡。ラウンドをまたぐのでROUND_STARTでは消さない
    # 時刻はどれもログの時刻（壁時計ではない。負荷で処理が遅れてもずれないため）
    log_now: float = 0.0                # 最後に読んだ行の時刻
    last_begin_press_at: float = 0.0    # ツールが Begin を実際に押した時刻（壁時計）
    pending_verified_time: float = 0.0  # 本物として採用したVerifiedの時刻（ラウンド開始待ち）
    statistics_sent: bool = False
    transformed_uid: int | None = None
    local_player_name: str = ""
    local_user_id: str = ""
    # インスタンス内のプレイヤー（usr_ID。表示名は変わりうる）。自分も入る
    players: set = field(default_factory=set)
    # players を信用できるか。起動時に復元できなければ False のまま——
    # 「他の人がいる」側に倒す（他人の周回を自分の tnl で裁かないため）
    players_known: bool = False
    # 入室者の表示名（usr_ID → 名前）。主催リストの希望は表示名で持っている
    player_names: dict = field(default_factory=dict)
    # 希望が見つからなかった入室者（ログを1回だけ出すため）
    unmatched_logged: set = field(default_factory=set)
    fog: bool = False
    is_continue_round: bool = False
    # DTM/Waldo による続行か（is_continue_round と一緒に落とす）。音量では通常扱い
    open_special_continue: bool = False
    instance_id: str = ""                  # 今のインスタンスの ID（DB v1 でまとめる目印の元）
    round_start_time: Optional[float] = None   # そのラウンドの開始の行の時刻（送る時刻ではない）
    # この窓が「他窓フリーズ」を張っているか。is_continue_round とは別物で、
    # DTM/Waldo の窓は is_continue_round=True でもこちらは False（他窓を止めない）
    continue_freeze_held: bool = False
    # 続行フリーズを張った・外した瞬間に呼ぶ（引数 True / False。LogMonitor が入れる）
    continue_hook: object = field(default=None, compare=False, repr=False)
    _skip_time: float = 0.0
    begin_done: bool = False
    speed_round_kind: str = ""   # 速度から先読みしたラウンド種別（通知済みのもの）
    speed_probe_done: bool = False  # このラウンドで速度検知を起動したか
    speed_strafe_done: bool = False  # このラウンドで速度検知の横移動をしたか
    # 自爆（リトライ込み）が走っているラウンドの round_seq。流れを1本にするため
    suicide_seq: int = -1
    # 自爆キャンセルのキーを押したラウンドの round_seq。そのラウンドはもう自爆しない
    suicide_cancelled_round: int = -1
    # 入室のたびに進める。インスタンスをまたいで動き続けるものを止めるため
    instance_seq: int = 0
    speed_freeze_held: bool = False  # この窓が速度検知フリーズを張っているか
    round_freeze_held: bool = False  # この窓がラウンド突入フリーズを張っているか
    speed_freeze_kind: str = ""      # "8pages" / "punish"。解除条件を覚えるため
    is_open_special_round_round: bool = False
    open_special_round_wins: int = 0
    # 今のインスタンスで経験した Twilight（OPEN_SPECIAL_ROUND_NOT_PROOF）の回数。
    # 1回目は3クラ前にも起こりうるので数えない。2回目で3勝扱い
    twilight_count: int = 0
    twilight_round_seq: int = -1          # 数えたラウンド（KILLERS_SET が2回来ても1回）
    item_id: int = 1
    item_id_at_round_start: int = 1
    # 所持アイテム（0 = 持っていない）。item_id はロスト判定の都合（8 Pages の開始で
    # 0 になるなど）を含むので、別に持つ。ログの表示と今後の判定用
    held_item_id: int = 0
    # 最後にロストしたアイテム（アイテム自動取得が取りに行くもの）。装備したとき・インスタンスが
    # 変わったときに 0 にする。それまでの「アイテム未回収」のラウンドでも取りに行く
    last_lost_item_id: int = 0
    # Equipping <id> を受けた回数と最後の id（アイテム自動取得が Equip の結果を待つ）
    equip_seen_seq: int = 0
    equip_seen_id: int = 0
    waiting_for_equip: bool = False
    equip_freeze_held: bool = False
    # フリーズの理由で前面を借りたときの札（WindowOperator.FrontLoan）。最初の1枚だけ
    # 持ち、この窓のフリーズが全部解けたら返す（SharedState.keep_front_loan）
    front_loan: object = field(default=None, compare=False, repr=False)
    # アイテム取得→Begin モードの装備待ちなら、この窓の hwnd（0 = 前面を引き継がない）。
    # 列の先頭にいるあいだ、ほかの窓が返そうとした札を引き継ぐ（SharedState）
    equip_front_hwnd: int = 0
    item_lost_announced: bool = False
    item_lost_this_round: bool = False
    randomizer_item_changed: bool = False
    died_this_round: bool = False
    lived_this_round: bool = False
    item_equipped_after_death: bool = False
    pending_sabotage_murder: bool = False
    sabotage_murder_this_round: bool = False
    bloodthirsty_creature_variant: bool = False
    hungry_home_invader_variant: bool = False
    atrached_variant: bool = False
    gigabytes: bool = False
    glorbo: bool = False           # Punished の Arkus が Glorbo に置き換わった
    glorbo_afk: bool = False       # Glorbo の続行で AFK 対策のループを回している（ラウンド開始で戻す）
    # 置き換えの合図（TerrorReplacement.TABLE の flag）
    foxy: bool = False
    neo_pilot: bool = False
    # Sabotageで選出されたマーダーの表示名（Sus player / Sus player 2）。
    # Verified Round End でクリアする——ROUND_START と同じ秒に積まれるため
    sus_players: list[str] = field(default_factory=list)
    moon_repeat: bool = False   # このmoonが2回目以降か（ROUND_STARTで確定）
    # Enrage のログから前倒しで判明させたテラーID。ラウンドごとに落とす
    enrage_identified: int | None = None
    # 主催リスト喪失を知らせたか。ラウンドごとに鳴らさないための抑制
    list_lost_notified: bool = False
    list_lost_reason: str = ""      # "host"（主催リスト無し）/ "wishes"（希望無し）
