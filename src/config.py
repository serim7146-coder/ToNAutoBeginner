import os
from pathlib import Path

import ItemCatalog
import ReadJson


VRCHAT_LOG_DIR = (
    Path(os.environ.get("APPDATA", "~")) / ".." / "LocalLow" / "VRChat" / "VRChat"
).resolve()
VRCHAT_WINDOW_CLASS = "UnityWndClass"

# ── アプリ情報・自動アップデート ──
# APP_VERSION はリリースごとに上げ、GitHubのリリースタグと一致させること
APP_VERSION       = "v1.3.2"
GITHUB_REPO       = "serim7146-coder/ToNAutoBeginner"
UPDATE_ASSET_NAME = "ToNAutoBeginner.exe"
# インストーラー（exe 単体の人はこれで入れ直す。Migration）
SETUP_ASSET_NAME  = "ToNAutoBeginner-Setup.exe"
# 起動中の目印（名前付きミューテックス）。インストーラー・アンインストーラーがこれを見て、
# 起動中なら閉じるよう案内する。installer/ToNAutoBeginner.iss の AppMutex と同じ名前にする
APP_MUTEX_NAME    = "ToNAutoBeginnerRunning"

# ── 設定ファイル（前回のtnlパスなどを保存） ──
SETTINGS_PATH = Path(os.environ.get("APPDATA", ".")) / "ToNAutoBeginner" / "settings.json"
# デバッグログ（公開ログとは別。DebugLog）。上限を超えたら .1 へ回す
DEBUG_LOG_PATH = SETTINGS_PATH.parent / "debug.log"
DEBUG_LOG_MAX_BYTES = 20 * 1024 * 1024       # これを超えたら世代を回す（.1〜.3 を残す）
# 設定を debug.log・不具合の報告へ出すときに抜く項目（OBS のパスワード。暗号化したものも）
REPORT_SETTINGS_EXCLUDE_KEYS = ("obs_password_dpapi", "obs_password")

# ── 不具合の報告（Discord の Webhook。URL は .env の DISCORD_REPORT_WEBHOOK_URL）──
REPORT_COOLDOWN_SEC = 60                         # 送れたら、この秒数は続けて送れない
REPORT_MAX_BYTES = int(9.5 * 1024 * 1024)        # zip の上限（Webhook の添付は 10MB まで）
REPORT_VRCHAT_LOG_START_BYTES = 8 * 1024 * 1024  # VRChat のログの末尾。超えたら半分ずつ減らす
REPORT_VRCHAT_LOG_MIN_BYTES = 256 * 1024         # これより減らすなら VRChat のログは外す
REPORT_GUI_LOG_MAX_BYTES = 2 * 1024 * 1024       # 画面のログ出力の末尾
REPORT_DEBUG_LOG_MAX_BYTES = 5 * 1024 * 1024     # debug.log の末尾（足りなければ .1 から足す）
REPORT_TIMEOUT_SEC = 30
# 統計画面 v1 の手元の保存（RoundStore。DB から取った分と自分が送った分）
ROUND_STORE_PATH = SETTINGS_PATH.parent / "rounds.sqlite"

# ── ToN ListTool の主催リスト追従 ──
# ToN ListTool が %APPDATA% に書いている統合リストを読んで続行判定に使う。
# 参加者の続行希望が変わると数秒で追従する。
# ToN ListTool 2.13 以降の保存先（SQLite）。これだけを読む。
# 2.13 より前の host_save.json.gz には対応しない——ディスクに古いものが残って
# いることがあり、SQLite が一瞬無いだけで何日も前のリストで判定してしまうため
_LISTTOOL_DIR = os.path.join(os.environ.get("APPDATA", ""), "ToN ListTool")
# ToN ListTool 2.17 以降は state\・lists\ の下（2026-10-21 の更新で移った）。前の直下は残っていれば読む（GF）
HOST_STATE_PATH = os.path.join(_LISTTOOL_DIR, "state", "host_state.sqlite3")
HOST_STATE_LEGACY_PATH = os.path.join(_LISTTOOL_DIR, "host_state.sqlite3")
# 主催者自身の続行リスト。host_save の participants には入らないため別に読む
USER_SAVE_PATH = os.path.join(_LISTTOOL_DIR, "lists", "user_save.json")
USER_SAVE_LEGACY_PATH = os.path.join(_LISTTOOL_DIR, "user_save.json")
HOST_SAVE_POLL_SEC = 3.0
# 外部ツール起動ボタンの「起動中」表示を見直す間隔。
# 続行リストの供給元判定とは別のループで回す（片方の失敗を巻き込まないため）
TOOL_LAUNCH_POLL_SEC = 3.0
# プロセスの一覧（ToolHelp のスナップショット）を取り直す最短の間隔（FO）。この PC（479 プロセス）で 1 回 11ms の CPU。
# 3 秒ごとの 2 つの見張り（上の 2 つ）が別々に取ると 10 分で 391 回・約 10 秒（1 窓の CPU の 3〜5 割）になっていた。
# 10 秒に 1 回なら 10 分で 60 回に収まる。主催リストの元（ToN ListTool）は一覧から PID を覚えて生死だけを 3 秒ごとに見るので
# 落ちた検知は今までどおり 3 秒。遅れるのは「起動した」の検知（ボタンの「起動中」・ListTool が後から立ち上がったとき）だけで、
# この秒数を超える最初の確認で気づく（確認が 3 秒ごとなので 12 秒おき）
PROCESS_LIST_MIN_SEC = 10.0
# 外部ツールのパスは手打ちもできるので、1文字ごとに書かずまとめて保存する
SETTINGS_SAVE_DEBOUNCE_MS = 500
# ToN ListTool のプロセス名。動いていなければ host_save は古いものとして捨てる
# （ツールを閉じてもファイルはディスクに残るため）
TON_LISTTOOL_PROCESS = "ton_listtool.exe"

# 既定値。GUIで変更でき、settings.json に保存される。
# 実行時にここを書き換えないこと（プロセス全体に効いてしまう）
EMERGENCY_STOP_KEY = "p"
EMERGENCY_KEY_CAPTURE_SEC = 5.0   # キーバインドの画面の「変更」で待つ時間
# 開始・停止のボタン（1つ）を押した後、押せないままにする時間。開始・停止の途中に
# 積まれたクリックで、開始・停止が続けて走らないようにする（DV）
START_STOP_REARM_MS = 500
# 「▶ マクロ開始」を押すのと同じことをするキー。既定は未設定＝無効。
# 停止は効かないと危ないので、不正なキーなら既定値へ倒す。開始は逆で、
# 勝手に動き出す方が危ない。だから既定は未設定、不正なら無効にするだけで、
# 別のキーへは倒さない。捕捉の秒数は停止キーと共用する。
# どちらも押した瞬間の通知で拾う（長押しは要らない）
START_KEY = ""
# 自爆キャンセルのキー。押すと全部の窓の自爆を止め、そのラウンドはもう自爆しない。
# 押した瞬間を取りこぼさないよう、チェイスのキーと同じく押した通知（hook_key）で受ける
SUICIDE_CANCEL_KEY = "^"

# アイテム自動取得。Begin が通った後に店へ取りに行く。合計でこれを超えたらやめる
ITEM_FETCH_LIMIT_SEC = 10.0
# ハイパーパラメーター: Guidance Plush の店の操作（前面を借りて押す）は、Connecting からこの秒数以内に鍵が取れたときだけ。
# 超えていればその回は見送る（順番待ちで持ち時間 12 秒を使い切った回は、途中で打ち切られるだけなので行かない。FR）。
# 見送った回はやり直しの回数に数えない。移動（店の前まで）は毎回する。店の操作 3.8 秒＋戻し 1 秒＋余裕 2 秒 = 12 − 7
ITEM_FETCH_FRONT_LATEST_SEC = 5.0
ITEM_FETCH_FOCUS_SEC = 0.25     # 窓を前に出してから撮り始めるまで

# 録画中だけ、当ツールの窓を画面キャプチャから外す。物理モニタには見えたままで、
# 操作もできる。消えるのは録画・スクリーンショット・画面共有の中だけ。
# SetWindowDisplayAffinity が要るので Windows 10 2004（build 19041）以降。
# 使えない環境では1度だけログに出して、録画はそのまま続ける
HIDE_OWN_WINDOWS_WHILE_RECORDING = True

GUI_BG  = "#1e1e2e"
GUI_FG  = "#cdd6f4"
GUI_ACC = "#89b4fa"
GUI_RED = "#f38ba8"
GUI_GRN = "#a6e3a1"
GUI_SUB = "#313244"
GUI_YLW = "#f9e2af"
GUI_ORG = "#fab387"
GUI_LOG_MAX_LINES = 1000
GUI_OVERLAY_LOG_MAX_LINES = 20


def resource_path(filename: str) -> Path:
    here = Path(__file__).resolve().parent
    candidates = [
        here / filename,
        here.parent / filename,
        here.parent / "ToNAutoBeginner" / filename,
    ]
    compiled = globals().get("__compiled__")
    if compiled is not None:
        candidates.append(Path(compiled.containing_dir) / filename)

    for path in candidates:
        if path.exists():
            return path
    return candidates[0]

# ── テラーIDとテラー名の対応表.json ──
TERRORS = ReadJson.load_terrors(resource_path("assets/data/terrors.json"))

# ── アイテムIDと名前・8 Pages に持ち込めるか（item.json）──
# 無い・読めないときは空の表（所持アイテムは id だけで覚え、ページ取得でなくさない）
ITEMS = ItemCatalog.load_items(resource_path("assets/data/item.json"))

# Enrage に出る個体名は terrors.json の "terrors" に入っている
# （以前の terror_aliases.json は廃止した）

# ── DB v1: ラウンドの種類の番号（ToN Save Manager の ToNRoundType と同じ）──
# ログの「round type is X」の X → 番号。観測した 1/2/5/6/9/50/51/102 は一致。
# Sabotage star / Sabotage murder は1つのラウンドなので 4 にまとめる。
# 表に無い名前（空・日本語の名前など）は ROUND_TYPE_UNKNOWN_ID（日本語は対象外）。
# Special（GIGABYTE・Rift Monsters など。ログは「round type is Special」）は 106
ROUND_TYPE_IDS: dict[str, int] = {
    "Classic": 1, "Fog": 2, "Punished": 3, "Sabotage": 4, "Sabotage star": 4,
    "Sabotage murder": 4, "Cracked": 5, "Bloodbath": 6, "Double Trouble": 7,
    "Bloodbath EX": 8, "Ghost": 9, "Unbound": 10, "Randomizer": 11, "Classic.exe": 12,
    "Midnight": 50, "Alternate": 51, "Fog (Alternate)": 52, "Ghost (Alternate)": 53,
    "Mystic Moon": 100, "Blood Moon": 101, "Twilight": 102, "Solstice": 103, "Run": 104,
    "8 Pages": 105, "Special": 106, "GIGABYTE": 106, "Cold Night": 107,
}
ROUND_TYPE_UNKNOWN_ID = 999
# 逆引き（番号 → 名前）。まとめた名前（Sabotage star/murder）は代表の名前にする
ROUND_TYPE_NAMES: dict[int, str] = {}
for _name, _id in ROUND_TYPE_IDS.items():
    ROUND_TYPE_NAMES.setdefault(_id, _name)
# テラーを持たないラウンド（ムーン系4つ・Run・Special）。ラウンド開始の行でテラーを
# 空（NULL）にして1回だけ送る。ムーン系はログが毎回 Killers 0 0 0 を出し（0 は Classic の
# Huggy と同じ番号）、Run は Killers の行を出さない。Special はテラーを集計に入れない（依頼者）
NULL_TERROR_ROUND_IDS = {100, 101, 102, 103, 104, 106}
# DB v1 の時刻は 2026-01-01 00:00:00 UTC からの秒（4バイト）
DB_TIME_EPOCH = 1767225600

SPECIAL_ROUND = {
    "Classic.exe",
    "Randomizer",
    "8 Pages",
    "Fog",
    "Ghost",
    "Punished",
    "Sabotage",
    "Bloodbath",
    "Double Trouble",
    "Bloodbath EX",
    "Cracked",
    "Alternate",
    "Midnight",
    "Unbound",
    "Mystic Moon",
    "Blood Moon",
    "Twilight",
    "Solstice",
    "Fog (Alternate)",
    "Ghost (Alternate)",
    "Sabotage star",
    "Sabotage murder",
    "Special",
}

# ラウンド開始でアイテムをロストと扱うラウンド（ロストの判定 st.item_id）。
# 所持の記録（st.held_item_id）は別で、8 Pages はページを取ったときに表（item.json）の 0 だけなくす
ROUND_START_ITEM_LOSS_ROUNDS = {
    "Punished",
    "8 Pages",
}

LATE_ROUND = {
    "Punished",
}

# ── 自動Begin ──
BEGIN_WAIT_SEC        = 9.0   # RoundOver から Begin移動を始めるまでの待機。
                               # 実測: RoundOver→Verified Round End が約13秒。
                               # OSC の窓は移動した後に Verified Round End を
                               # 待ってからクリックするので、早く動き始めても
                               # 押すのは Round End の後になる。キー操作の窓は
                               # Round End を待ってから移動するので、この値はほぼ効かない。

# 続行ラウンドのフリーズ解除を死亡から遅らせる。
# 解除前に猶予を作り、手動での視点調整などを挟めるようにするため。
# 霧ラウンドから続行になった場合は短め、ふつうの続行ラウンドは長めにする。
# （霧そのもので全窓を止めるかは、突入フリーズ側で選ぶ別の仕組み）
CONTINUE_FREEZE_RELEASE_DELAY_SEC = 5.0
FOG_FREEZE_RELEASE_DELAY_SEC      = 2.0
# Begin前の移動。前進を通しで1回押し、その【頭から】左を重ねる。
# つまり 前半 = 斜め移動、後半 = 直進 になる（逐次に「前進→左」ではない）。
# 加速・減速が各1回で済むので、逐次より到達点が安定する。
# 同時押しの区間はVRChatが斜め速度を正規化するため、各軸の速度が約0.707倍に
# なる。逐次だった頃の値をそのまま使うと距離が変わるので要調整。
BEGIN_FORWARD_SEC     = 2.08   # 前進を押している総時間
BEGIN_LEFT_SEC        = 0.15   # 冒頭で左を重ねる時間（斜めになる区間）
BEGIN_FORWARD_SEC_LATER = 3.2  # パニッシュ後
BEGIN_LEFT_SEC_LATER  = 0.16   # パニッシュ後
# Begin 前の移動は、押している時間を「動き出し」（受信の速さが MOTION_START_SPEED を超えた時）から数える（EF）。
# 離すのは 押し始め ＋ 秒数 ＋ 延び。延び ＝ 動き出しまで − BEGIN_USUAL_START_SEC（0〜BEGIN_EXTEND_MAX_SEC）。
# BEGIN_START_WAIT_SEC たっても動き出さなければ延ばさない（受信が無い窓は今どおり）
BEGIN_USUAL_START_SEC = 0.08
# 停止で、音量の見張りが元の音量に戻し終えるのを待つ上限（EG）。その後の COM の片付けは待たない
WINDOW_VOLUME_STOP_WAIT_SEC = 1.0
BEGIN_EXTEND_MAX_SEC  = 0.5
BEGIN_START_WAIT_SEC  = 0.6
BEGIN_START_POLL_SEC  = 0.01
# 速さを受け取れているのに BEGIN_START_WAIT_SEC 動き出さなければ、いったん離して MOVE_REPRESS_GAP_SEC 後に
# 押し直す（MOVE_REPRESS_MAX 回まで）。Begin 前の移動・Run の後ろへ・Run の前後の直しの 3 つ（EH）
MOVE_REPRESS_MAX      = 2
MOVE_REPRESS_GAP_SEC  = 0.1

# `Verified` は Begin の受理のほかに、定期シグナルでも出る（VerifiedTracker）。
# 手元のログ全部で調べた規則（2026-09-30）: 定期は前回の定期から300秒後。その時点が
# ラウンド中なら、そのラウンドの RoundOver の0〜1秒後まで遅れて出て、次はそこから
# 300秒後（定期 4,294件のうち 300秒ちょうど 1,402件・RoundOver 直後 2,254件）。
# 本物の受理はほぼ必ずラウンド開始の12〜13秒前。今までの見分け方との比較で、
# 受理したのに始まらない取り違えが 206件 → 18件
# debug.log にメモリ・ハンドル数・スレッド数を書く間隔（FC: Tcl の Event の漏れを見張る。起動時にも 1 行）
MEMORY_LOG_SEC             = 600.0
# 同じ時刻の [CPU] の行に出す仕事の名前の数（終わった仕事・生きているスレッドのそれぞれ。CPU の多い順。FI）
MEMORY_LOG_TOP             = 8
VERIFIED_PERIODIC_SEC      = 300.0
# 予定（最後の定期 + 300 秒）からの許容ズレ（FA: 1.0 → 2.0。依頼者「±2.0 で拡張するだけでいい」）。
# 2026-10-15 09:13 窓3 は予定から +1.45 秒で「受理（予定の外）」と取り違え、店へ行って戻れなくなった。
# 同じ日の debug.log で、予定どおりに出た定期（RoundOver 直後の遅れたものを除く）のずれは 中央 0.14・90% 0.37・
# 最大 1.99 秒（EY の実測）。予定は 300 秒の倍数では追わない（遅れて出た定期の次は、遅れた時刻から 300 秒で来る）
VERIFIED_PERIODIC_TOL_SEC  = 2.0
VERIFIED_AFTER_OVER_SEC    = 2.0     # RoundOver からこの秒数以内の Verified は遅れて出た定期の候補
BEGIN_PRESS_RECENT_SEC     = 3.0     # 「直前に押した」の長さ（壁時計）。予定の外の Verified で「ツールがまだ押していない → 定期」を見分ける（予定と重なった1回は押していても定期。EP）
VERIFIED_LEARN_BACK_SEC    = 1800.0  # 起動時に位相を取り戻すため遡るログの長さ
# 採用した Verified の後、ラウンド開始が来るまでの実測は12〜13秒
VERIFIED_ROUND_START_WAIT_SEC = 15.0

# ラウンド突入フリーズで選べる種別（ログ上の表記そのまま）
# Sabotage murder（自分がマーダー）・Sabotage star（マーダーでない）はログのラウンド名ではない（ログは「Sabotage」）。
# どちらかは Sus player の行で分かる（ラウンド開始と同じ秒、開始の行より前に出る。EZ: 手元のログの Sabotage 8 回すべて）。
# Bloodbath EX は選べない（FB: 依頼者「EX はいらない」。古い設定に残っていても無視する）
ROUND_FREEZE_SELECTABLE = [
    "Alternate", "Midnight", "Unbound", "Fog", "Ghost", "8 Pages", "Punished",
    "Sabotage murder", "Sabotage star", "Cracked", "Double Trouble",
]
# 霧に入ったときの音声。既定では鳴らさない（仕組みと voice_fog の設定は残す）
ANNOUNCE_FOG_ON_ENTRY = False

# ── ラウンドごとの自動自爆 ──
# 選んだラウンドは続行リストを見ずに自爆する。効くのは private だけ——
# ただし4種moonは干し芋/焼き芋でも効く（指定すれば1回目でも自爆する）。
# 並び順はこのリストのまま表示する。ソートも独自順序も作らない
# （このリストを編集して表示順と項目を変えられるようにするため）。
SKIP_ROUND_SELECTABLE = [
    "Classic", "Classic.exe", "Randomizer",
    "Bloodbath", "Bloodbath EX", "Double Trouble",
    "Fog", "Ghost", "Punished", "Sabotage", "Cracked",
    "Alternate", "Midnight", "Unbound", "8 Pages", "Run",
    "Mystic Moon", "Blood Moon", "Twilight", "Solstice",
]
SKIP_ROUND_COLUMNS = 5   # チェックボックスの折り返し列数

# ── 速度によるラウンド種別の先読み ──
# ToNはラウンド種別で移動速度を変える。Beginを押した直後から効くので、
# ROUND_STARTを待たずに種別が分かる（8 Pagesならスキャナー、Punishならナッツを持てる）。
# 判定は「一定値に張り付いたか」で行う。閾値の帯で判定すると、平常時でも壁擦りや
# 加速途中で 6.5 付近の値が出るため誤検知する。
SPEED_DETECT_ENABLED   = True
# 判定値はすべて VelocityMagnitude（アバターパラメータ）の値。
# VelocityX/Z は加速中の値ばかりが飛んできて最高速に届かないので使えない。
SPEED_NORMAL           = 6.6    # 平常時の横移動速度
SPEED_8PAGES           = 6.5    # 8 Pages（横移動だけ遅い）
SPEED_PUNISH           = 4.0    # Punish（前後左右すべて遅い）
# 値がこの秒数だけ変化しなければ「張り付いた」とみなす。
# VRChatは値が変わったときしか送らないので、プラトーに乗ると送信が止まる。
SPEED_STABLE_SEC       = 0.25
SPEED_STICK_TOL        = 0.01   # これを超えて動いたら「変化した」とみなす
SPEED_MATCH_TOL        = 0.01   # 定数との一致許容差
SPEED_PROBE_LEFT_SEC   = 0.6    # 後に左へ動かす時間
SPEED_PROBE_RIGHT_SEC  = 0.4    # 先に右へ動かす時間（繰り返しはしない）
# 判定はラウンド突入まで見続ける。これは暴走防止の上限だけ
# （ラウンドが来ないまま回り続けないため）。実測で起点(ラウンドデータ取得)から
# ROUND_START まで最大119秒。20秒で打ち切ると約1.5%で横移動を取りこぼした
SPEED_PROBE_MAX_SEC    = 180.0
SPEED_READY_TIMEOUT_SEC = 2.0   # 速度受信のbind完了を待つ上限（通常はミリ秒で終わる）
SPEED_RECV_TIMEOUT_SEC = 5.0    # 速度パケットが来ないと判断するまで
                                # （横移動が始まるまでの猶予を含める）

# ── フォーカス取得 ──
FOCUS_RETRY_MAX      = 3     # 前面化を試みる回数
FOCUS_RETRY_WAIT_SEC = 0.12  # 前面化要求後に反映を待つ時間
# VRChat を前面にしても、カーソルが中央に固定されず浮いたままのことがある。前に出すたびに
# このキーを押して離すと固定される（依頼者の実測。固定済みでも押してよい）。空文字にすると何もしない
CURSOR_LOCK_KEY       = "tab"
CURSOR_LOCK_PRESS_SEC = 0.05
# ツールのクリックは、このキーを押したまま押す（押す → 待つ → クリック → 離す）。押すとカーソルが
# 照準（真ん中）へ戻る（依頼者 2026-10-10）。待ちは Tab とクリックが同じフレームで読まれないため
# （60fps で約2フレーム。0 にもできる。依頼者が実機で詰める）
CLICK_TAB_KEY      = "tab"
CLICK_TAB_LEAD_SEC = 0.03
# アイテム取得の店の操作は、Tab を1回だけ押したまま（押す → 待つ → 店のボタン → Equip → 離す。依頼者）。
# VRChat は Tab を読んだときにカーソルを真ん中へ戻すので、読み終わるのを待ってから置く。重い窓では
# Tab を読むのが遅れる（2026-10-10 窓6）ので、置いたら読み直し、ずれていれば置き直す
FETCH_TAB_SETTLE_SEC    = 0.1    # Tab を押してから最初にカーソルを置くまで
FETCH_CURSOR_SETTLE_SEC = 0.05   # カーソルを置いてから読み直すまで
FETCH_CURSOR_TRIES      = 3      # 置く回数（1回目＋置き直し）
FETCH_CURSOR_TOL_PX     = 2      # 読み直した位置が置いた点からこれ以内なら置けている
# 続行ラウンドの後（依頼者 2026-10-10）: アイテムを落とす（OSC の右手の「落とす」を押して離す）・
# 窓を続行ラウンドの始まりの位置へ戻す（フルスクリーンなら Alt+Enter で窓に戻してから）
CONTINUE_DROP_PRESS_SEC   = 0.1   # 落とす操作を押している長さ
CONTINUE_WINDOWED_WAIT_SEC = 2.0  # Alt+Enter の後、窓に戻るのを待つ上限
CONTINUE_WINDOWED_POLL_SEC = 0.1
# 速さの受信（DS）: まず OSCQuery で待ち、この秒数でその窓から届かなければ固定ポート（VRChat の送信ポート）を開く
OSCQUERY_CONNECT_WAIT_SEC = 15.0
# mDNS（OSCQuery の名乗り）を 127.0.0.1 だけで開く（GB）。0.0.0.0 で開くと Windows のファイアウォールが
# ビルドのたび（onefile の展開先が変わるたび）に確認を出す。同じ PC の VRChat はループバックのマルチキャストで
# 見つけられる（手元の実験）。見つけられない PC があれば False に戻す（今までどおり全インターフェース）
OSCQUERY_MDNS_LOOPBACK_ONLY = True

# Run のラウンドに入ったら、リスポーン → 後ろへ → 正面を向く（依頼者 2026-10-10。窓2 で詰めた値）
RESPAWN_MENU_WAIT_SEC = 1.0       # Esc を押してからメニューを撮るまで
RESPAWN_FIND_TRIES    = 3         # ボタンを探す撮影の回数（見つからなければ撮り直す）
RESPAWN_FIND_RETRY_SEC = 0.3
RESPAWN_CLICK_SEC     = 0.15      # リスポーンのボタンを押している長さ（0.08 は FPS 17〜18 の窓で取りこぼした）
RESPAWN_LOG_WAIT_SEC  = 2.0       # 押してから「Player respawned」の行を待つ上限
# 押した後、鍵・前面・カーソル（ボタンの上）をそのままにして行を待つ上限（EN: ceed392 で押した直後に返したら
# 押せなくなった。前の成功 51 回は押してから 0.26〜0.58 秒で行が来ていた）。来なければ返して残りは鍵の外で
RESPAWN_HOLD_FRONT_SEC = 1.0
RUN_RESPAWN_BACK_SEC  = 2.85      # リスポーンの後、後ろへ下がる長さ（赤い膜を後ろ向きに通る）
RUN_RESPAWN_TURN_SEC  = 0.9       # その後、左へ回る長さ（真後ろ → 正面。0.8 は足りない・1.0 は回しすぎ）
RUN_RESPAWN_POLL_SEC  = 0.1       # ほかの窓のフリーズが解けるのを待つ・行を待つ間隔
# 左へ回した後、撮影で階段の上の構造の絵（RunFace）を探して真正面に直す（DO・DQ。依頼者 2026-10-11）
RUN_FACE_TOL         = 0.01       # 目印が真ん中から撮影の幅のこの割合以内なら終わり（GG: 3%→1%。Run の後の最初の Begin は
                                   # 1% 以内なら 36 通って 8 外れ、1% を超えると大半が外れた＝10/17〜10/21 の 85 回）
RUN_FACE_RATE        = 1.22       # 回す速さ（撮影の幅 / 秒）。0.15 秒までは比例（窓1 の実測）
RUN_FACE_MIN_SEC     = 0.02       # 押す長さの下限
RUN_FACE_MAX_SEC     = 0.10       # 押す長さの上限（0.15 秒を超えると急に回りすぎる。0.2 秒で 310 px）
RUN_FACE_SETTLE_SEC  = 0.35       # 回してから撮るまで
RUN_FACE_TRIES       = 6          # 直す回数の上限
RUN_FACE_FINE_MAX    = 0.03       # ずれが幅のこの割合以下なら軸で細かく回す（これより大きいはボタン。GG）
RUN_FACE_FINE_AXIS   = 0.55       # 細かく回すときの軸 /input/LookHorizontal の値（0.5 以下は回らない。2026-10-22 の実機の測り）
RUN_FACE_FINE_RATE   = 0.09       # 軸 0.55 で回る速さ（撮影の幅 / 秒。2026-10-22 の実測で 0.05 秒 0.47%・0.10 秒 0.90%）
RUN_FACE_FINE_MIN_SEC = 0.02      # 軸で回す長さの下限（0.02 秒で幅の 0.16%）
RUN_FACE_FINE_MAX_SEC = 0.35      # 軸で回す長さの上限（ずれ 3% でも 0.35 秒に収まる）
RUN_FACE_MATCH_MIN   = 0.8        # 目印の絵の一致がこれ未満なら見つからない
# 目印を特徴点（SIFT＋ホモグラフィ）で探す（FK。依頼者 2026-10-16）。絵の照合は倍率 0.85〜1.15 でしか探せず、
# FOV が違う人（FOV 75 で 1.55 倍・60 で 2.06 倍）は見つからないので、玉座の切り出し（throne.png）を倍率を決めずに
# 当てる。受け入れなかったときだけ、上の絵の照合（RUN_FACE_MATCH_MIN）を保険に使う
# ■ 閾値の根拠（技術選定が 2026-10-16 に手元で測った。参照・前処理・ratio 0.75・MAGSAC 4.0 は RunFace と同じ）
#   見つかるべき撮影のインライア数: 依頼者の Run 後の撮影（852×664）11 枚 292〜475／絵の照合が見つけられなかった
#   撮影（434×352 の半分の大きさ）3 枚 48〜69／FOV 75 のスクショ 163／報告者の 2 枚（960×540）131・135
#   見つかってはいけない撮影（Begin の台の前 begin_miss 3・begin_adjust 4・店の画面 fetch_miss 3・報告者の台の上
#   begin_adjust 3・begin_miss 3）のインライア数: 8〜24
#   最小の正例 48 と最大の負例 24 のあいだで 40（負例から +16・正例から −8。正例の最小は小さい撮影なので、
#   本番の 852×664 以上ではもっと多い）。tests/test_run_face_features.py が tests/data の資料でこの表を再現する
RUN_FACE_FEATURE_MIN_INLIERS = 40   # ホモグラフィのインライアがこれ未満なら受け入れない
RUN_FACE_FEATURE_MIN_MATCHES = 8    # ratio test を通った対応がこれ未満ならホモグラフィを求めない
RUN_FACE_FEATURE_RATIO       = 0.75 # ratio test（最良の距離 < 次点の距離 × これ）
RUN_FACE_FEATURE_RANSAC_PX   = 4.0  # MAGSAC の許容誤差（px。拡大する前の撮影の座標）
# 枠の形の条件: インライア数だけでは足りない。2026-10-16 19:38 窓1（2560×1440。後ろへ下がる途中で引っかかって壁を
# 向いていた）は玉座が写っていないのに「インライア 72」が出た。写した四隅が 1 点につぶれていた（幅 0）ので、
# 四隅が凸・写した枠の幅が撮影の幅のこの範囲、で弾く
RUN_FACE_FEATURE_WIDTH       = (0.10, 0.95)
# 向きを直した後、前後の位置を目印の大きさ（倍率 f。いつもの位置で 1.0）で直す（EE）
# EJ: 止めた（False）。目印が遠く、数 m の違いが大きさにほとんど出ない（13:53 窓1: 正しい位置で 1.12 → 直して
# 後ろへずれた。円が数 m 先の撮影でも 0.98）。倍率は debug.log に書くだけ。True で前の動き（RUN_DEPTH_* は残す）
RUN_DEPTH_ENABLED    = False
RUN_DEPTH_TOL        = 0.05       # f が 1.0 からこれ以内なら直さない（0.95〜1.05）
RUN_DEPTH_FIRST_SEC  = 0.2        # 最初に押す長さ（その後は秒あたりの f の変化から決める）
RUN_DEPTH_MIN_SEC    = 0.05
RUN_DEPTH_MAX_SEC    = 0.6
RUN_DEPTH_TRIES      = 4          # 前後に動かす回数の上限
# Run の後ろへ・前後の直しも「動き出し」から数える（EH）。延びの上限（12:09 窓3 の動き出し 0.75 秒が入るように）
RUN_EXTEND_MAX_SEC   = 1.0
# 「Player respawned」を受けてから後ろへを押すまで（EP: リスポーン直後の速さ 1.2 が残っていて、動き出しから
# 数える（EH）が「動き出し 0.00 秒」と取り違えていた。2026-10-13 窓2・5）
RUN_BACK_DELAY_SEC   = 0.5
# Run の位置合わせのためのデータ集め（EQ。撮って残すだけ。RunPos）
RUN_POS_AFTER_ROUNDS = 3          # マクロの開始から普通に終わったラウンドがこの回数たったら基準を撮る
RUN_POS_STILL_SPEED  = 0.1        # 速さがこれ未満なら止まっている
RUN_POS_STILL_SEC    = 1.0        # 止まったままこの秒数続いたら撮る
RUN_POS_GAP_SEC      = 0.5        # 2 枚をこの秒数あけて撮り、向きを変えていないか比べる
RUN_POS_DIFF_MAX     = 3.0        # 2 枚の差（グレーの画素の差の絶対値の平均。0〜255）がこれ以下なら撮る
# 2 枚目の明るさ（グレーの平均）がこれ未満なら基準にしない（EW: 死んだ画面「Your skin burns away…」が基準に
# なっていた。2026-10-13 の基準 18 枚は 13.0〜13.5 で全部が死んだ画面、Run の後の撮影は 19.9〜32.8）
RUN_POS_DARK_MAX     = 15.0
RUN_POS_POLL_SEC     = 0.05
RUN_POS_REF_KEEP     = 3          # 基準は窓ごとに新しいこの枚数まで
RUN_POS_RUN_KEEP     = 30         # Run の後は全部でこの枚数まで
# 入室時に Guidance Plush を取れなかった窓は、次の普通のラウンドの後（Begin が通った後）にふだんの自動取得の
# 道で取りに行く（ER）。成功するまでこの回数まで
PLUSH_RETRY_MAX      = 3
# Begin を BEGIN_RETRY_MAX 回押して受理されなかった窓は、この秒数おいて押し直しの流れを 1 回だけ（EV）。
# BEGIN が見つからずあきらめた窓も、この秒数おいて 1 回目のやり直し（リスポーン → Begin 前の移動 → 押す。FL）
BEGIN_GIVE_UP_RETRY_SEC = 30.0
# それでも通らなかった窓の 2 回目以降のやり直しの間隔（FL）。ラウンドが始まる・止める・Begin が通るまで、上限なし
# （1 窓だと誰もラウンドを始めず、あきらめたまま止まり続けるため）
BEGIN_GIVE_UP_REPEAT_SEC = 180.0
BEGIN_GIVE_UP_POLL_SEC  = 0.5
RUN_DEPTH_WORSE      = 0.02       # 実際に動いて f が 1.0 からこれ以上遠のいたら戻す（0.01 ほどは測りのぶれ）
# 入室時のクリックで借りた前面を、離してから元の窓へ返すまでの間。入室パネルは
# 離した瞬間に判定されるので、離した後に待つ。Begin は押した瞬間に判定され、
# クリックの「押す→0.1秒→離す」の間で足りているので待たない（依頼者が後で調整する）
FOCUS_RETURN_AFTER_RELEASE_SEC = 0.3

# ── 自動自爆 ──
SELF_SUICIDE_KEY    = "^"  # デフォルト値（GUIで変更可能）
# 自爆キーはいつも背面へ送る（前面化しない）。送れない窓（最小化中など）では自爆せずに知らせる
SUICIDE_HOLD_SEC    = 3.0     # 自爆ボタンを押す時間
# 自爆が効かなかったらやり直す。背面送信で長押ししている間に他の窓が
# 前面を奪うと、VRChat はキーを離したことにする（実例: 窓4が80秒後に死亡）
SUICIDE_RETRY_MAX     = 3     # 1回目を含めた回数
SUICIDE_CONFIRM_SEC   = 1.5   # 長押しが終わってから死亡を待つ時間
# 押した Begin が受理（本物の Verified）されなければ押し直す
BEGIN_RETRY_MAX       = 3     # 1回目を含めた回数
# Begin の押し方。OSCが使える窓は「カーソルをその窓の Begin のボタンの上へ
# 置いて /input/UseRight を送る」で、前面化せずに押せる（実測。矩形の中なら
# どこでもよいわけではない。WindowOperator.click() のコメント参照）。
# False にすると従来の前面化＋クリックへ戻る
BEGIN_BY_CURSOR        = True
# カーソルを置くのは Begin のボタンの上＝照準の位置＝クライアント領域の中央。
# これは中央からのずれで、照準の位置を微調整したいときだけ動かす（通常は0,0）
BEGIN_CURSOR_OFFSET    = (0, 0)
# UseRight の連打は RoundOver から数えて始める。実測（521ラウンド）で
# RoundOver → Verified Round End は13〜14秒、Verified Round End → Begin が
# 押せるまでは 0 秒だった。カーソルは連打中は動かさない
BEGIN_USE_SPAM_START_SEC = 12.9
# UseRight を押している時間。離している時間も同じなので、押し下がる瞬間は
# この2倍ごと（0.05秒ごと）に来る。差し込みの滞在（BEGIN_CURSOR_DWELL_SEC）が
# 0.05秒なので、ひと差しの間に押し下がる瞬間が必ず1回入る。
# 0.1（1周0.2秒）だったときは、0.05秒の差し込みに当たるのが4回に1回程度で、
# 空振りして前面化へ落ちていた。ToN_Multi_Supporter も 0.05 秒間隔
BEGIN_USE_PULSE_SEC    = 0.025
# 連打で Begin が押せる状態か（前面か、カーソルがその窓の上）を見直す間隔
BEGIN_USE_REACH_CHECK_SEC = 0.5
# OSC で押している間、1 を送り直す間隔（途中で VRChat 側で入力が消えて止まることがあった）
OSC_HOLD_RESEND_SEC = 0.1
# カーソルを窓へ置くのは「ひと差し」だけ。利用者からマウスを奪う時間を最小にする
BEGIN_CURSOR_DWELL_SEC = 0.05       # 窓の上に置いている時間
BEGIN_CURSOR_GAP_SEC   = 0.3        # 置けなかったときに、次を試すまでの間
# 「差し込み → 受理を待つ」を試す回数（1回ごとに BEGIN_RETRY_WAIT_SEC まで受理を待つ）。
# 2 は1回目＋やり直し1回。連打は0.05秒周期（dcb2fab）なので1回置けば押し下がる瞬間は
# 必ず入るが、受理が来ない・置いた瞬間に利用者の手がマウスを動かした、などの一時的な
# 失敗を1度だけ拾い直す。押せなかった回の流れは「差し込み → 待つ → 差し込み → 待つ →
# 前面化＋クリック」になる（依頼者了承済み）。押せた回は実測0.2秒ほどで抜ける。
# 試す回数で上限が決まるので、ループ全体の締め切りは持たない（以前の
# BEGIN_CURSOR_LIMIT_SEC = 4.0 は、1回目の受理待ちが5秒あると2回目を切り捨てていた）
BEGIN_CURSOR_DIPS      = 2
# ── Begin が押せなかったときの位置合わせ（BeginDetect で BEGIN を探して横移動で寄せる）──
# 照準（クライアント領域の中央）と BEGIN の文字の中心の、横のずれを見る。縦は OSC で
# 視点を上下に振れないので直さない（実測では文字が照準の少し上に来る。照準は黒い天板に乗る）
BEGIN_ADJUST_TOL          = 0.3    # 許容: 文字幅に対する割合（これ以内なら動かない）
BEGIN_ADJUST_PROBE_SEC    = 0.1    # 1回目の横移動（px/秒 がまだ分からないとき）
BEGIN_ADJUST_MIN_SEC      = 0.05   # 1回の横移動の下限・上限
# 上限は 1 歩目（BEGIN_ADJUST_PROBE_SEC）と同じ 0.10。同じ長さなら同じ加速の形で動くので 1 歩目の実測が
# そのまま当てはまる。長く押すと一定の速さの分が増えて見積もりより大きく動く（FU）。300 px のずれは 2 歩で収まる。
# 1 歩目より短い押しは加速の分だけ少なく動くが、行き過ぎない側なので次の歩で寄る
BEGIN_ADJUST_MAX_SEC      = 0.10
BEGIN_ADJUST_MAX_STEPS    = 4      # 横移動の回数の上限
BEGIN_ADJUST_MAX_TOTAL_SEC = 1.2   # 1回の位置合わせで横移動してよい合計
BEGIN_ADJUST_SETTLE_SEC   = 0.4    # 動いてから撮り直すまでの待ち（減速を待つ）
BEGIN_ADJUST_MIN_MOVE_PX  = 5      # 横移動でこれ以下しかずれが変わらなければ打ち切る（OSC が届いていない等）
# 1 歩目から見積もる横の速さ（文字幅/秒）の下限。ハイパーパラメーター。実測 9.8〜10.9（成功）・5.0（失敗）。
# 遅く見積もるほど長く押して行き過ぎるので、遅い側だけ切る（FU）
BEGIN_ADJUST_RATE_MIN     = 8.0
# 前後。文字の幅÷窓の高さ（w/H）で近さが分かる（実測 2560x1440: Begin の位置 0.101〜0.105、
# 後ろへ 0.2秒 0.069・0.4秒 0.044、前へ 0.15秒 0.146。高さはほとんど変わらない）
# 押せる範囲（実測 2026-10-03 1920x1080: 押せる一番近い 0.404・一番遠い 0.066）の内側に余裕を
# 持たせた範囲なら前後に動かない。外なら、合わせる先へ向けて動く（合わせる先は秒数の計算にだけ使う）
# 下限は 0.11（EF: 幅 0.086 で「済み」にして押せなかった。位置合わせ済み 24 回のうち 22 回は 0.12 以上）
BEGIN_ADJUST_DEPTH_OK        = (0.11, 0.33)
BEGIN_ADJUST_FORWARD_TARGET_W = 0.12  # 遠い（0.08 未満）→ 前へ。ここへ向けて
BEGIN_ADJUST_BACK_TARGET_W    = 0.25  # 近い（0.33 超）→ 後ろへ。ここへ向けて
BEGIN_ADJUST_DEPTH_PROBE_SEC = 0.1    # 1回目の前後移動（w/H 毎秒がまだ分からないとき）
BEGIN_ADJUST_DEPTH_MIN_SEC   = 0.05   # 1回の前後移動の下限・上限
BEGIN_ADJUST_DEPTH_MAX_SEC   = 0.4
BEGIN_ADJUST_DEPTH_MAX_STEPS = 4      # 前後移動の回数の上限
BEGIN_ADJUST_DEPTH_MAX_TOTAL_SEC = 1.2  # 1回の位置合わせで前後に動いてよい合計
BEGIN_ADJUST_DEPTH_MIN_CHANGE = 0.003 # 前後に動いてこれ以下しか w/H が変わらなければ打ち切る
# 高さ（視点の上下）。OSC では上下に振れないので、この窓が前面のときだけマウスの相対移動で合わせる（戻さない）。
# 前面でない窓には相対移動が届かないので、今までどおり縦は見ない
BEGIN_PITCH_TOL         = 0.25   # 許容: 文字幅に対する縦のずれの割合
BEGIN_PITCH_PROBE_UNITS = 24     # 縦の感度が保存されていないときの 1 歩目（マウスの単位。アイテム取得の CALIB_UNITS と同じ）
BEGIN_PITCH_MAX_STEPS   = 3      # 高さ合わせの回数の上限
# BEGIN が見つからないときの探し方（実機 2026-10-02）: 近すぎる（文字が視界の下に外れる）→
# 後ろへ 0.15秒。遠すぎる（文字が小さすぎる）→ 前へ 0.3秒ずつ最大4回
BEGIN_SEARCH_BACK_SEC       = 0.15
BEGIN_SEARCH_FORWARD_SEC    = 0.3
BEGIN_SEARCH_FORWARD_TRIES  = 4
# 探す動きの「前へ」は、速さの受信から積分した実際の移動量で見る（FM。報告者 2026-10-16 05:41 は前へ 0.3秒×4 が
# 台座に当たって動けていないのに、押した秒数ぶん 1.2 秒戻して台から降りてしまった）。ハイパーパラメーター。
# 根拠: Begin 前の移動の実測は 2.08 秒で 12.6、0.3 秒で約 1.5〜1.8 動く。壁なら 0。0.3 はその 1/5 ほどで、
# 動けた押しと壁の押しを分けるのに十分な間
BEGIN_SEARCH_BLOCKED_DIST = 0.3     # 前へ 0.3 秒押してこれ未満しか動かなければ壁（前へ探すのをやめる）
BEGIN_SEARCH_RETURN_MIN   = 0.5     # 前へ動けた合計がこれ未満なら、最後に後ろへ戻さない（戻す量が測りのぶれに埋もれる）
# 壁なら、代わりに後ろへこれだけ動いて探す（依頼者の判断 2026-10-16:「壁に位置しているなら、そこから固定量動けば
# Begin が見えると期待できる」）。0.7 秒にしたところ、実機の撮影で下がりすぎて BEGIN が見えない所まで行っていた
# ので 0.3 秒に短くした（依頼者 2026-10-17）。実機で確かめて直す（後ろへ動いた実測の距離を debug.log に書く）。
# 見つからなければそこであきらめる（さらに戻さない）
BEGIN_SEARCH_BLOCKED_BACK_SEC = 0.3
# 押してから受理（Verified）を待つ上限。差し込み後の待ち（_wait_begin_accepted）と、
# 前面化＋クリック後の押し直し前の待ち（_begin_accepted）で共用する（依頼者の判断）。
# 押せた回は実測 0.2 秒ほどで受理が来るので、待たずに抜ける（2026-09-27 22:35 は
# 05.139 に押して 05.297 に Connecting）。押せなかった回はこの秒数だけ待ってから次へ
# 進む（差し込み→フォールバック→押し直しで最大2回ぶん）。測るのは「こちらが押して
# から」で、Verified Round End 起点ではない。以前「Verified Round End → Verified が
# 3〜4秒」としていたのは誤りで、フォールバックが毎ラウンド起きていた時期のログを測り、
# その遅れを Verified の遅れと読んでいた
BEGIN_RETRY_WAIT_SEC  = 5.0
# 主催リストが取れない状態がこれだけ続いたら tnl へ切り替える。プロセスの
# 見え方・host_save の差し替え・参加者の入れ替えは一瞬だけ起きうる
HOST_LIST_LOSS_GRACE_SEC = 10.0

# ── 3クラ続行設定 ──────────────────────────────
DTM_ID   = ReadJson.terror_id("Don't Touch Me", TERRORS)
WALDO_ID = ReadJson.terror_id("Waldo", TERRORS)
OPEN_SPECIAL_ROUND_TERROR_IDS: set[int] = {DTM_ID, WALDO_ID}
OPEN_SPECIAL_ROUND_TARGET_WINS  = 3      # 何勝したらAFK回避を終わるか（窓ごと）。窓の設定で、3勝の後も続けられる
# Waldo を続行するのは、このアイテムを持っているときだけ（依頼者）。番号は item.json から名前で引く。
# item.json に無い間は GUIDANCE_PLUSH_ITEM_ID（分かっていれば番号を書く）。どちらも無ければ、
# 持っているか分からないので前と同じ（アイテムに関係なく続行）
GUIDANCE_PLUSH_NAME = "Guidance Plush"
GUIDANCE_PLUSH_ITEM_ID: int | None = None
# DTM/Waldo でも続行しないラウンド。テラーが複数体出るもの（依頼者 2026-10-04）。
# Bloodbath EX は続行する。Cracked などほかの特殊ラウンドは続行する
OPEN_SPECIAL_ROUND_EXCLUDED_ROUNDS = {"Double Trouble", "Bloodbath", "Midnight"}
# 「特殊ラウンドを経験したら3勝扱い」の例外。特殊ラウンドだが、例外的に3クラ前にも
# 出現するもの（依頼者 2026-09-30: Twilight だけ。ほかの Moon は出現条件の都合で
# 3クラ前には起こりえない）。特殊ラウンドであることは変わらない（SPECIAL_ROUND には残す）
OPEN_SPECIAL_ROUND_NOT_PROOF = {"Twilight"}
OPEN_SPECIAL_ROUND_INTERVAL_SEC = 60.0   # AFK回避の移動の間隔（秒）

# ── フリーズ解除待機 ──
EQUIP_RELEASE_DELAY_SEC = 2.0            # アイテム装備確認後のフリーズ解除までの待機時間

# ── インスタンスタイプ ──
INSTANCE_PUBLIC        = "public"
INSTANCE_PRIVATE       = "private"
INSTANCE_HOSHIIMO      = "干し芋"
INSTANCE_YAKIIMO       = "焼き芋"
INSTANCE_OTHER_GROUP   = "その他のグループ"
INSTANCE_EMERALD_CITY  = "Emerald City"
INSTANCE_SEER          = "Seer's Apostle"
INSTANCE_ACHIEVEMENT   = "実績・アイテム集会"

# ── インスタンスid ──
HOSHIIMO_GROUP_ID = "grp_0821983a-f7ab-4252-9895-0fe2712026a9"
YAKIIMO_GROUP_ID  = "grp_005eab93-0bee-4493-9973-252f9ed51461"
# 識別のみ。自爆・グループ判定の対象にはしない
# （GroupRound.GROUP_INSTANCES に入れると自爆が走る）
EMERALD_CITY_GROUP_ID = "grp_8f8ace13-018b-47e6-a0f3-885831fd9bc8"
SEER_GROUP_ID = "grp_71fee430-5156-41e0-9623-e36860058af8"
# 実績・アイテム集会（GT）。Group+・グループオンリーだけ INSTANCE_ACHIEVEMENT（判定・音声・統計＋自動自爆だけ）
ACHIEVEMENT_GROUP_ID = "grp_4066ddbc-28e6-4e02-9148-2d5d912f9a4a"
# private とすべて同じに扱うインスタンス（FD: Seer's Apostle の「グループ（メンバーのみ）」。依頼者 2026-10-15）。
# 操作（can_operate）・ラウンドの判断・霧の看破。Seer's Apostle でもグループ+・公開・分からないは
# INSTANCE_OTHER_GROUP（マクロ・看破とも全面禁止）。自爆のグループ判定（GroupRound.GROUP_INSTANCES）には入れない
PRIVATE_LIKE_INSTANCES = frozenset({INSTANCE_PRIVATE, INSTANCE_SEER})
# 判定（続行リスト・続行表・ラウンド指定・Glorbo・Sabotage・自動自爆）を private と同じに通すインスタンス（GT）。
# PRIVATE_LIKE_INSTANCES に実績・アイテム集会を足したもの。実操作（Begin・入室・アイテム取得・Run リスポーン・
# アイテムを落とす・チェイス・放置モード・AFK 回避の移動・窓を戻す）と霧の看破は PRIVATE_LIKE_INSTANCES のまま
JUDGE_LIKE_INSTANCES = PRIVATE_LIKE_INSTANCES | frozenset({INSTANCE_ACHIEVEMENT})


# ── Variant判定待ち ──
# Variantになりうるテラーがいる間は、出現ログを待ってから判断する。
# ラウンド種別では分けない（この長さなら、待ちが結論を変えないラウンドでも
# 実害が出ないため）。
# 実測(手元ログ): Variantの出現ログは Killers行と同じ秒に出る
# ——Gigabytes 12件・Atrached 2件とも差0秒。Variantが確定した時点で打ち切る。
TERROR_VARIANT_WAIT_SEC = 0.3
TERROR_VARIANT_POLL_SEC = 0.2

# 起動オプション
LAUNCH_OPTION = ["--enable-debug-gui", "--enable-sdk-log-levels", "--enable-udon-debug-logging"]

# ── ToNワールド ──
TON_WORLD_ID = "wrld_a61cdabe-1218-4287-9ffc-2a4d1414e5bd"  # Terrors of Nowhere
TON_DEFAULT_REGION = "jp"
# 起動時に作るインスタンスの公開範囲（全窓で共通）。リンクの形:
#   インバイト   ~private(usr)                    自分が呼んだ人だけ
#   インバイト+  ~private(usr)~canRequestInvite   招待を持つ人が友達を呼べる
#   フレンド     ~friends(usr)                    自分のフレンドが入れる
#   フレンド+    ~hidden(usr)                     中にいる人のフレンドも入れる
# 霧の即時判定（看破）が使えるのは Invite・Invite+・Friends だけ（フレンド+ では
# 使えない。FogEarlyRead.EARLY_READ_ACCESS）。放置モード・自爆はどれでも private 扱い
TON_INSTANCE_ACCESS_INVITE       = "invite"
TON_INSTANCE_ACCESS_INVITE_PLUS  = "invite_plus"
TON_INSTANCE_ACCESS_FRIENDS      = "friends"
TON_INSTANCE_ACCESS_FRIENDS_PLUS = "friends_plus"
TON_INSTANCE_ACCESS_CHOICES      = (TON_INSTANCE_ACCESS_INVITE, TON_INSTANCE_ACCESS_INVITE_PLUS,
                                    TON_INSTANCE_ACCESS_FRIENDS, TON_INSTANCE_ACCESS_FRIENDS_PLUS)
TON_INSTANCE_ACCESS_DEFAULT      = TON_INSTANCE_ACCESS_INVITE_PLUS

# ── ToN入室時の自動操作 ──
# スポーン地点と向きが固定なので、決まった秒数の横移動で的が照準（画面中央）に来る。
# 画面の座標は使わない（ToNEntry）
TON_ENTRY_ENABLED = True
TON_ENTRY_BEGIN   = True   # 選択画面突破後にBeginまで押すか

# 入室時の操作手順（実測で決めた移動量）
# アバターを横移動させると的が画面中央（クロスヘア）に来る。
# 画面座標は使わない（パネルの見かけの大きさが距離で変わるため）。
# 最後の2つは的がほぼ同じ位置なので移動せず続けて押す。
TON_ENTRY_STEPS = [
    {"move": "right", "sec": 0.35, "label": "警告同意"},
    {"move": "left",  "sec": 0.65, "label": "難易度(Casual)"},
    {"move": "right", "sec": 0.55, "label": "BGM"},
    {"move": None,    "sec": 0.0,  "label": "LET ME PLAY"},
]

# 選択画面を抜けた後、ロビーのBeginまで移動する量（実測）
# 入室直後は位置が違うため、ラウンド終了後の BEGIN_FORWARD_SEC とは別の値。
TON_ENTRY_BEGIN_FORWARD_SEC = 5.30
TON_ENTRY_BEGIN_LEFT_SEC    = 0.12
# 入室の選択画面の後・Begin の前に、Save/Load のコード欄へクリップボードのセーブコードを貼る（DY）
SAVELOAD_CLICK_SEC       = 0.15   # コード欄を押している長さ
# コード欄が見つからないとき、すぐあきらめずに撮り直す長さと間（EX: 入室直後は描画が遅れることがある）
SAVELOAD_FIND_SEC        = 3.0
SAVELOAD_FIND_POLL_SEC   = 0.5
SAVELOAD_BOX_WAIT_SEC    = 3.0    # 入力欄（送信の矢印）が出るのを待つ上限
SAVELOAD_BOX_POLL_SEC    = 0.2
# Ctrl+V から Enter まで。コードは約 1 万 5 千文字で、0.3 秒では貼り終わる前に Enter が届いて効かない窓があった（EC）
SAVELOAD_PASTE_ENTER_SEC = 1.0
# 入力欄（送信の矢印）が出たと分かってから貼るまで（ES: 12:59 窓3 は出た直後に Ctrl+V を送って空振りし、
# 空のまま Enter が効いた。入力欄は閉じたが「Loaded Data Successfully」が出なかった）
SAVELOAD_SETTLE_SEC      = 0.5
# 読み込めず入力欄が閉じていたら、コード欄を探すところから最初からやり直す回数（ES）
SAVELOAD_TRIES           = 2
SAVELOAD_LOG_WAIT_SEC    = 8.0    # Enter の後、ログに「Loaded Data Successfully」が出るのを待つ上限
SAVELOAD_RETRY_WAIT_SEC  = 3.0    # これで出なければ、入力欄が開いたままか見て Enter を押し直す（EC）
SAVELOAD_ENTER_RETRIES   = 2      # 押し直しの上限
SAVELOAD_LOG_POLL_SEC    = 0.25
# 入室の Begin の後に Guidance Plush を取りに行く（DZ）。押した後の最初の Verified を待つ上限
PLUSH_VERIFIED_WAIT_SEC  = 60.0
PLUSH_VERIFIED_POLL_SEC  = 0.25
# ToN Utils の続行の判断に従う（EB）。起点（killers_set／killers_revealed）から Utils の press・hold を待つ上限。
# 実測の press は判定から 0.2〜1.58 秒（110 回）。何も来なければ続行
UTILS_WAIT_SEC           = 3.0
# Utils の press・hold は、Utils のログの時刻が「起点の行の時刻 − この秒数」以後のものを数える（EB2）。
# VRChat のログの時刻は秒で切り捨て。前のラウンドの press は 30 秒以上前なので混ざらない
UTILS_EARLY_SEC          = 2.0
UTILS_POLL_SEC           = 0.1

TON_ENTRY_PANEL_TIMEOUT  = 180.0  # パネル出現を待つ上限（読み込みが重い）
TON_ENTRY_POLL_SEC       = 2.0    # パネル確認の間隔
TON_ENTRY_START_DELAY_SEC = 3.0   # パネル検出後、操作を始めるまでの待ち
TON_ENTRY_SETTLE_SEC     = 1.0    # 移動後、クリックするまでの待ち
TON_ENTRY_STEP_WAIT_SEC  = 3.0    # クリック後、次の画面が出るまでの待ち
TON_ENTRY_MIN_RED_PIXELS = 300    # パネルが出ているとみなす赤画素の下限
TON_PANEL_STABLE_COUNT   = 2      # 連続で検出できたら確定

# ── OSC（窓ごとに別ポートを割り当てる） ──
# VRChatは既定でUDP 9000を掴む。多重起動では --osc= で分けないと競合する。
OSC_BASE_IN_PORT = 9000   # 窓1の受信ポート（送信は+1）
OSC_PORT_STRIDE  = 10     # 窓ごとのポート間隔
OSC_ENABLED      = True   # 起動時に --osc= を付けるか

# ── OBS 自動録画（obs-websocket v5） ──
# 続行アナウンスが鳴る種類の続行ラウンドを録る。シーンは切り替えない
OBS_DEFAULT_HOST    = "127.0.0.1"
OBS_DEFAULT_PORT    = 4455     # OBS 28 以降の既定
OBS_RECORD_TAIL_SEC = 3.0      # RoundOver から録画を止めるまで
OBS_RECORD_MAX_SEC  = 900      # 1本の上限。RoundOver が来ないときの録りっぱなし防止
OBS_TIMEOUT_SEC     = 2.0      # 接続・送受信のタイムアウト

# ── 霧の看破（--enable-sdk-log-levels 付きのログの [NetworkProcessing]） ──
# 公開前にオブジェクト名からテラーを特定する。判定・表示に使うのは、画面の「霧を即時判定する」が
# ON で、Invite / Invite+ / Friends のインスタンスのときだけ（FogEarlyRead.EARLY_READ_ACCESS）。
# それ以外は DB にだけ送る
FOG_EARLY_READ_ENABLED     = True
FOG_EARLY_READ_LAUNCH_FLAG = "--enable-sdk-log-levels"
# 名前ごとの答え合わせ（一致 / 食い違い）の記録
FOG_OBJECT_NAMES_PATH = SETTINGS_PATH.parent / "fog_object_names.json"
# 看破できる起動の霧で、Killers is unknown から FOG_NO_OBJECT_DTM_SEC たっても objects の
# 名前が1つも当たらなければ DTM（50）と判断する（看破と同じ扱い: 許可なしなら DB にだけ送る）。
# 手元の全ログ: DTM 以外の霧32回はすべて 0〜2秒後に名前が出た。DTM 1回は1つも出なかった
# （確信度: 中。DTM の例が1回）。看破できない起動では DTM 以外でも出ないので使わない。
# classic で objects が空なのは DTM だけ。alternate の Convict Squad・Joy・Foxy は別の行で分かる。
# ⚠ Winterfest の期間は Neo Pilot（オブジェクト名が分からない）が霧に出る → DTM と取り違えうる。
#   その期間は False にして止める
FOG_NO_OBJECT_DTM_ENABLED = True
FOG_NO_OBJECT_DTM_SEC     = 5.0

# ── VRChat起動 ──
MAX_WINDOWS             = 8      # 同時に扱える窓数の上限
# 窓が現れてから次を起動するまでの間隔。
# 詰めると一部の窓が ToN に入室できない。原因は世界の入室ではなく VRChat API の
# レート制限（HTTP 429）。ログイン直後に各クライアントが投げる inventory /
# favorites / auth / notifications が 429 になり、その巻き添えで入室に失敗する
# （ログの文面は "whatever it is that you are doing, you are doing it too
# quickly. slow down!"）。
# 実測 2026-09-23 20:34:56〜20:35:54 の6窓（起動間隔 11〜13秒）: 429 の数が窓ごとに
# 0, 0, 0, 6, 34, 68 と増え、5窓目が入室しなかった。直後 20:38 の同じ6窓
# （11〜14秒間隔）は全窓入室したが 429 は各窓 750〜1062 件。単発起動では 429 ゼロ。
# これは「窓が現れてから」の間隔なので、PC が速いほど実際の間隔は詰まる
# （速い機体では窓が数秒で出て、結果 11 秒前後になっていた）。10.0 で 15 秒前後。
# 「前の窓が入室したのを見てから次へ」は採らない（低スペック機で待ちが長く
# なりすぎるため。依頼者の判断）。起動の速さを取って 15.0 から 10.0 へ下げた
# （依頼者の判断）。6窓以上で入室できない窓が出るなら、まずこの値を上げる
LAUNCH_STAGGER_SEC      = 10.0
# 6窓を一気に投げると取りこぼす窓が出るため、1窓ずつ「出現を待ってから次」に
# する。1窓あたりの出現待ち上限。
LAUNCH_EACH_WINDOW_TIMEOUT = 120.0
LAUNCH_WINDOW_TIMEOUT   = 180.0  # ウィンドウ出現を待つ上限
LAUNCH_LOG_TIMEOUT      = 60.0   # 起動後にログ生成を待つ上限
LAUNCH_LOG_POLL_SEC     = 1.0    # ログ生成の確認間隔
# ログ待ちの判定の中身（窓の数・候補のログ・窓ごとの最も近いログ）を debug.log に出す間隔。
# 最初の判定と最後の判定（揃った／タイムアウト）にも出す。1秒ごとには出さない
LAUNCH_LOG_DIAG_SEC     = 10.0
LAUNCH_DESKTOP_MODE     = True   # 既定はデスクトップモード(--no-vr)

# ── ウィンドウ↔ログの対応付け ──
LOG_MATCH_TOLERANCE_SEC   = 120.0  # プロセス起動時刻とログ作成時刻の許容差
LOG_MATCH_CANDIDATE_COUNT = 20     # 突き合わせ対象にするログの本数
# これより長く更新が止まったログは、VRChatが終了したものとして候補から外す
# （生きている窓のログは絶えず伸びる）
LOG_LIVE_GRACE_SEC        = 120.0

# ── ログの更新頻度 ──
LOG_POLL_INTERVAL    = 0.3
LOG_START_SCAN_CHUNK_BYTES = 256 * 1024

# Fog はテラー不明のまま進むが、Enrage のログに名前が出る。名前が
# terrors.json に一意に一致したときだけ、判定を前倒しする
ENRAGE_IDENTIFY_ENABLED = True
STUNNED_IDENTIFY_ENABLED = True

# ── テラーの番号（ツールの独自の番号。assets/data/terror_ids.json。表と合っていることはテストで確かめる）──
# Classic はログの番号そのまま、Alternate は 1000＋ログの番号、Unbound は 2000＋ログの番号、
# 8 Pages は 3000＋ページの番号、4000〜は固定の表。入口（LogMonitor._own_killer_ids）でログの番号を
# 独自の番号に直したあとは、すべてこの番号で扱う
# alternate。霧で「JOY WILL SOON AWAKEN...」が出たらテラーは Joy（置き換えではない）
JOY_ID = 1030

# ── 置き換えテラーのID ──
# 置き換えの表（元 → 置き換え後・起きるラウンド）と合図のログ行は TerrorReplacement に
# まとめてある。ここは番号だけ
SONIC_ID = 40
ATRACHED_ID = 4001                  # Classic の Sonic → Atrached
SLENDER_ID = 47
HUNGRY_HOME_INVADER_ID = 4000       # Classic の Slender → Hungry Home Invader
CURIOUS_CREATURE_ID = 106
BLOODTHIRSTY_CREATURE_ID = 4002     # Curious → Wild Yet Bloodthirsty Creature（全ラウンド）
# Unbound の Self Inserts。中の Curious が Bloodthirsty 化しても
# ToN ListTool では「Bloodthirsty 入りの Self Inserts」を指定できないので、
# 出たらリストを見ずに必ず続行する（Pack of Wild Yet Curious は対象外）
SELF_INSERTS_ID = 2083
# 「Bloodthirsty 入りの Self Inserts」の正式な番号。ListTool に枠は無い（.tnl には書かない）ので、続行表の画面だけ
# Unbound のバリアントの段に置く。続行無効に入れると、Self Inserts の Bloodthirsty でも必ず続行はせず、
# 普通のリストの判定（Self Inserts があるか）に任せる
SELF_INSERTS_BLOODTHIRSTY_ID = 4013
# alternate。Sanic は Foxy に置き換わることがある（表示名は Evil Purple Foxy）
SANIC_ID = 1002
FOXY_ID = 4011
# alternate。Fusion Pilot は Neo Pilot に置き換わる。合図のログはまだ分からない
FUSION_PILOT_ID = 1029
NEO_PILOT_ID = 4010
# Punished の Sewers。Arkus が低確率で Glorbo に置き換わる（ListTool の special317）
ARKUS_ID = 61
GLORBO_ID = 4012
# 元IDが毎回違う（実測12件すべて別ID）ので、IDからは予測できない。
# Classicの1体構成は常に候補として出現ログを待つ
GIGABYTES_ID = 4009

# Variantテラー（Classicでもこれなら通常判定に回す）
VARIANT_TERROR_IDS = frozenset({
    HUNGRY_HOME_INVADER_ID, ATRACHED_ID, BLOODTHIRSTY_CREATURE_ID, GIGABYTES_ID,
})

# ── チェイス（前面の VRChat の窓を、ラウンド終了までぐるぐる回す）──
# F1 = 時計回り（左へ歩きながら右を向く）、F2 = 反時計回り。suppress しない
CHASE_CW_KEY     = "f1"
CHASE_CCW_KEY    = "f2"
# 押している間、この間隔で「押す」を送り直す（離しはしない）。ツールのほかの
# 移動の stop_all() や UDP の取りこぼしで離されても、すぐ戻るように
CHASE_RESEND_SEC = 0.2

# ── 移動のキー（押している間、監視中の全窓を動かす。ジャンプは 1 回押しで 1 回）──
MOVE_FORWARD_KEY = ""     # デフォルト未設定（使う人が決める）
MOVE_LEFT_KEY    = ""
MOVE_RIGHT_KEY   = ""
JUMP_KEY         = ""
JUMP_PRESS_SEC   = 0.1    # ジャンプを押している時間

# ── 音量 ──
DEFAULT_SOUND_VOLUME = 1.0
# VRChat の窓ごとの音量（WindowVolume）。窓の状態で Windows の音量ミキサーの音量を
# 切り替える。既定は OFF。値は %（0〜100）
WINDOW_VOLUME_POLL_SEC          = 0.5
DEFAULT_WINDOW_VOLUME_ENABLED   = False
DEFAULT_WINDOW_VOLUME_CONTINUE  = 100
DEFAULT_WINDOW_VOLUME_ALL_CONTINUE = 100     # 全続行・ラウンド突入フリーズの窓（EZ。既定は続行と同じ）
DEFAULT_WINDOW_VOLUME_FREEZE    = 100
DEFAULT_WINDOW_VOLUME_OTHER     = 0

# ── 音声アナウンスファイルパス ──
VOICE_CONTINUE     = str(resource_path("assets/voice/Continue.mp3"))
# 主催リストが取れなくなったときのアナウンス。欄を空にすれば鳴らない
VOICE_LIST_LOST    = str(resource_path("assets/voice/StopAutoSuicide.mp3"))
VOICE_FOG          = str(resource_path("assets/voice/Fog.mp3"))
VOICE_ITEM_LOST    = str(resource_path("assets/voice/ItemLost.mp3"))
VOICE_INTERMISSION = str(resource_path("assets/voice/intermission.mp3"))
VOICE_FOXY         = str(resource_path("assets/voice/SpawnFoxy.mp3"))
# 速度からの先読み用
VOICE_8PAGES       = str(resource_path("assets/voice/8Pages.mp3"))
VOICE_PUNISH       = str(resource_path("assets/voice/Punished.mp3"))
# ラウンド突入でフリーズを選んだラウンドに入ったときの音声（霧は VOICE_FOG を使う）
VOICE_UNBOUND      = str(resource_path("assets/voice/Unbound.mp3"))
VOICE_MIDNIGHT     = str(resource_path("assets/voice/Midnight.mp3"))
VOICE_ALTERNATE    = str(resource_path("assets/voice/Alternate.mp3"))
VOICE_GHOST        = str(resource_path("assets/voice/Ghost.mp3"))
VOICE_MURDER       = str(resource_path("assets/voice/Murder.mp3"))         # Sabotage murder（EZ）
VOICE_SABOTAGE     = str(resource_path("assets/voice/Sabotage.mp3"))       # Sabotage star（EZ）
VOICE_CRACKED      = str(resource_path("assets/voice/Cracked.mp3"))
VOICE_DOUBLE_TROUBLE = str(resource_path("assets/voice/DoubleTrouble.mp3"))
# 生き残るラウンドで AFK_STILL_SEC 動かなかったとき、private でない窓で鳴らす警告音（EZ）
VOICE_AFK          = str(resource_path("assets/voice/AFK.mp3"))
# ツールが自爆しないラウンドの AFK 回避（EZ。依頼者 2026-10-15）。速さ（OSC の受信）が AFK_MOVING_SPEED 未満の
# 状態が AFK_STILL_SEC 続いたら、private の窓は微移動（DTM/Waldo と同じ）、それ以外は警告音。受信が無い窓は止まっていると見る
AFK_STILL_SEC      = 70.0
AFK_POLL_SEC       = 1.0
AFK_MOVING_SPEED   = 0.1

# 自動操作後の待ち時間
OPERATOR_WAIT_SEC = 0.05
