# ToNAutoBeginner 技術仕様書

対象: `src/` 以下の実装（develop、2026-10-04 時点）。
利用者向けの説明は `README.md`。本書は開発者向けに、モジュールの責務・スレッド・状態・データの流れを書く。
関数シグネチャの網羅は目的としない。細かい理由や実測値は各モジュールの docstring・コメントにある。

## 1. 全体像

VRChat ワールド「Terrors of Nowhere (ToN)」向けのデスクトップ支援ツール（Windows 専用、Tkinter GUI）。
VRChat のログを窓ごとに tail してラウンドの進行を追い、自爆・Begin・全窓フリーズ・音声アナウンス・
統計の送信などを行う。複数の VRChat の窓（マルチアカウント／マルチクライアント）を同時に扱う。

```
main.py ──▶ mainGUI.App (Tkinter。Tk のスレッド)
              ├─ 窓ごとに LogMonitor（1窓=1スレッド。ログを読んで判定する）
              │     ├─ LogParser          : ログ1行 → LogEvent
              │     ├─ VerifiedTracker    : `Verified` が Begin の受理か定期シグナルか
              │     ├─ RoundDecision / GroupRound / RoundSequence / TerrorReplacement : 判定
              │     ├─ FogEarlyRead       : 霧の看破
              │     ├─ ActionExecutor     : 自爆・Begin・AFK 対策・速度検知・アイテム自動取得（実操作）
              │     │     ├─ WindowOperator : 前面化・キー・クリック・カーソル（Win32）
              │     │     ├─ OSCClient / OSCReceiver : OSC の送信・速度の受信
              │     │     └─ BeginDetect / ItemFetch : 画像で BEGIN・店のボタンを探す（OpenCV）
              │     ├─ PlaySound / Recorder : 音声・OBS 録画
              │     └─ ConnectDB          : 統計の送信（Supabase）
              ├─ SharedState   : 窓をまたぐ状態（操作のロック・全窓フリーズ・全窓共通の設定）
              ├─ MatchTNL      : 続行リスト（.tnl・ToN ListTool の主催リスト）の読み込み
              ├─ VRChatLauncher / VRChatDiscovery / ToNEntry : VRChat の起動・窓とログの対応・入室操作
              ├─ WindowVolume / ToolLauncher / BugReport / AutoUpdate
              └─ StatisticsGUI → RoundStore / Statistics / ConnectDB : 統計画面
```

## 2. モジュール一覧

### 画面・設定
| モジュール | 責務 |
|---|---|
| `main.py` | 入口。`mainGUI.App` を作って `mainloop()`。 |
| `mainGUI.py` | 画面。窓タブ・設定の保存（settings.json）・監視の開始/停止・VRChat の起動 UI・主催リストの追従・キー（緊急停止・開始・自爆キャンセル・チェイス）・外部ツール・OBS・音量・不具合の報告・自動アップデート。 |
| `config.py` | 定数（パス・GUI の色・待ち時間・ID・ラウンドの集合など）。`resource_path()` で開発実行と Nuitka の exe の両方からデータファイルを引く。 |
| `State.py` | `WindowConfig`（窓ごとの設定）と `WindowState`（窓ごとの実行時の状態）。ロジックは持たない。 |
| `SharedState.py` | 窓をまたぐ状態。`_GLOBAL_ACTION_LOCK`・全窓フリーズ4種（`_Freeze`）・全窓共通の設定（`_Setting`）・前面の貸し借り・掴んでいる窓の一覧。 |
| `HotKey.py` | キー名の検証・表記・捕捉。 |
| `UIFont.py` | 日本語を持つフォントを PC にあるものから選ぶ。 |
| `SecretStore.py` | OBS のパスワードを DPAPI で暗号化して保存する。 |
| `ToolLauncher.py` / `ProcessCheck.py` | 外部ツールの起動、プロセスの生死（ToolHelp スナップショット）。 |

### ログと判定
| モジュール | 責務 |
|---|---|
| `LogMonitor.py` | 1窓を1スレッドで監視する中心。ログを読み、`WindowState` を更新し、判定して `ActionExecutor` に操作を頼む。イベントの種類ごとに `_HANDLERS` からメソッドを引く。 |
| `LogParser.py` | ログ1行を `LogEvent`（種類＋付帯情報）にする。状態を持たない。 |
| `VerifiedTracker.py` | `Verified` の行が Begin の受理か、約300秒ごとの定期シグナルかを、ログの時刻で見分ける。 |
| `MatchTNL.py` | .tnl と ToN ListTool の主催リスト（`host_state.sqlite3`）の読み込み、ログのテラーID → 続行リストのIDの変換（Alternate +134・Unbound +200）。全窓で共有する `SharedLists`。 |
| `RoundDecision.py` | 続行するか・3クラ解放（DTM/Waldo）の対象かの判定。 |
| `GroupRound.py` | 干し芋／焼き芋のインスタンスのルール（と、プライベートの Sabotage）。 |
| `RoundSequence.py` | ラウンドの並び（通常・特殊）から Moon の解放状況を推定する。 |
| `TerrorReplacement.py` | 置き換えテラー（Atrached・Hungry Home Invader・Bloodthirsty・Foxy・Glorbo・Gigabytes など）の合図の行（`SIGNALS`）と差し替えの表（`TABLE`）。 |
| `FogEarlyRead.py` | 霧の看破（`[NetworkProcessing]` のオブジェクト名から公開前にテラーを特定）の許可と、名前ごとの答え合わせの記録。 |
| `ReadJson.py` / `ItemCatalog.py` | terrors.json・item.json の読み込みと引き方。 |

### 操作
| モジュール | 責務 |
|---|---|
| `ActionExecutor.py` | 実際の操作。自爆（背面へキー）、Begin（移動・押し方・押し直し・位置合わせ）、DTM/Waldo の AFK 対策、速度検知、チェイス、アイテム自動取得、アイテムロストの前面化と音声。 |
| `WindowOperator.py` | Win32 の操作。前面化・背面へのキー・クリック・カーソル・窓の矩形・前面の貸し借り（`FrontLoan`）。VRChat を前に出すたびに `config.CURSOR_LOCK_KEY`（Tab）を押して離し、カーソルを中央に固定する（`lock_cursor`。依頼者の実測。浮いているかは見ない。その窓が前面のときだけ押す）。 |
| `OSCClient.py` / `OSCReceiver.py` | VRChat への OSC 送信（移動・視点・UseRight）と、速度（VelocityMagnitude）の受信。窓ごとに別ポート。 |
| `BeginDetect.py` / `BeginMiss.py` / `ScreenCapture.py` | 画像で `[ BEGIN ]` を探す（PrintWindow で背面の窓も撮る）、見つからなかった撮影を残す。 |
| `ItemFetch.py` | アイテム自動取得。OSC で店へ移動し、SIFT＋ホモグラフィで店のボタンを見つけ、視点を回してクリックする。 |
| `ToNEntry.py` | 入室直後の選択画面を突破する（OSC の横移動＋クリック）。 |

### 起動・外部連携・統計
| モジュール | 責務 |
|---|---|
| `VRChatLauncher.py` / `VRChatDiscovery.py` | Steam から `launch.exe` を探して窓ごとに `--profile=N`・`--osc=` で起動、窓とログの対応付け（プロセスの起動時刻とログの作成時刻）。 |
| `AutoUpdate.py` | GitHub Releases の最新版と比べて exe を差し替える。古い展開先も消す。開発実行では無効。 |
| `Migration.py` | exe 単体で動いている人をインストーラー版へ移す（Setup を落として画面なしで実行）。開発実行では無効。 |
| `PlaySound.py` | MCI（winmm）で音声を鳴らす。 |
| `WindowVolume.py` | Windows のアプリごとの音量で、窓の状態ごとに VRChat の音量を変える。 |
| `Recorder.py` / `OBSClient.py` | 続行ラウンドを OBS（obs-websocket v5）で録る。通信は専用のスレッド。 |
| `ConnectDB.py` | Supabase との通信。ラウンドの送信（`rpc/register_round`）、transformed_uid の取得（`rpc/get_transformed_uid`）、統計画面の差分取得。`.env` が無ければ何もしない。 |
| `RoundStore.py` / `Statistics.py` / `StatisticsGUI.py` | 統計画面。DB の差分を手元の SQLite に取り込み、手元で集計する。二項分布の検定。 |
| `DebugLog.py` / `BugReport.py` | debug.log（秘密・ユーザー名を伏せる）と、不具合の報告（Discord の Webhook）。 |

## 3. スレッド

| スレッド | 何をするか |
|---|---|
| Tk のスレッド | 画面。ほかのスレッドからは `after(0, …)` で渡す。重い処理（プロセスの一覧・SQLite）は置かない。 |
| 主催リスト・外部ツールの確認 | 3秒ごとに裏のスレッドで読み、結果だけを Tk のスレッドで当てる（`_poll_host_save`・`_poll_tool_buttons`）。 |
| keyboard のフック | 緊急停止・マクロ開始・自爆キャンセル・チェイスのキー。押した・離した通知のたびに見る（ポーリングしない）。 |
| LogMonitor（窓ごと） | `LOG_POLL_INTERVAL`（0.3秒）ごとにログの追記を読み、行ごとに `_process`。 |
| 操作のデーモン | 自爆・Begin・AFK 対策・フリーズの遅延解除など（§5.1）。`LogMonitor._start_daemon`。 |
| OSC の受信（窓ごと） | 速度を受け続ける（監視中ずっと）。 |
| Recorder のワーカー | OBS との通信。LogMonitor はキューに入れるだけで待たない。 |

## 4. 状態

### 4.1 `WindowConfig`（窓ごとの設定）
`hwnd`・`log_path`・自動 Begin・自動自爆・DTM/Waldo 続行・OSC のポート・ラウンドごとの自爆/続行の指定（`skip_rounds`・`continue_rounds`。インスタンスが変わると外す）・音声のパス。
窓数を変えて窓タブを作り直しても、各窓の設定と割り当ては引き継ぐ（`WindowTab.snapshot`/`restore`。減らした窓の分も、ツールを閉じるまで覚えておく）。

### 4.2 `WindowState`（窓ごとの実行時の状態）
LogMonitor が1窓に1つ持つ。主なもの:
- ラウンド: `in_round`・`round_type`・`terror_ids`・`map_id`・`round_seq`（ラウンドごとに増える。遅れて動くタスクが自分のラウンドかを見る）
- インスタンス: `instance_type`・`instance_access`・`instance_id`・`players`・`players_known`
- Begin: `begin_done`・`round_end_seen`・`begin_move_done`・`last_begin_press_at`・`pending_verified_time`
- アイテム: `item_id`（ロストの判定用）・`held_item_id`（所持の表示と自動取得用）・`last_lost_item_id`・`waiting_for_equip`
- フリーズの保持: `equip_freeze_held`・`continue_freeze_held`・`speed_freeze_held`・`round_freeze_held`・`front_loan`
- 置き換えの合図: `TerrorReplacement.flags()` の属性（ラウンド開始で落とす）
- 霧の看破・3クラ・自爆の流れ（`suicide_seq`・`suicide_cancelled_round`）など

### 4.3 `SharedState`（全窓で共通）
- `_GLOBAL_ACTION_LOCK`: 前面を切り替える操作（前面化・クリック・カーソルの差し込み）は必ずこれを取る。1度に1窓だけ。
- 全窓フリーズ4種（§5）。
- 全窓共通の設定（`_Setting`）: インスタンスタイプ・自爆キー・放置モード・速度検知・フリーズ設定・続行リストの供給元（`"host"`/`"tnl"`）・アイテム自動取得とその感度・アイテム取得→Begin モード。
- 掴んでいる VRChat の窓・当ツール自身の窓。

### 4.4 続行リスト（`MatchTNL.SharedLists`）
続行リスト・参加者別の希望・参加者・タブを、画面と全窓の LogMonitor が同じ1つで持つ。
**中の dict は書き換えず、新しい dict を代入して差し替える**（`clear()`→`update()` の途中で判定した窓が空のリストを見て自爆しないように）。

## 5. 全窓フリーズ

| 種類 | 張る | 解く |
|---|---|---|
| 続行 | 続行ラウンド（アナウンスが鳴るもの）が決まったとき。DTM/Waldo の続行は張らない | 死亡・RoundOver から猶予の後（`_release_continue_freeze_after_delay`）／次のラウンド開始 |
| 装備待ち | アイテムロストの窓。張った順に並び、先頭の窓だけが前面化＋音声 | 装備＋Begin の受理から猶予の後（Begin を押さない窓は装備から猶予の後）／ラウンド開始 |
| 速度検知 | 速度で 8 Pages / Punished と分かったとき（設定で選ぶ） | 8 Pages はアイテムを取ったら猶予の後／ラウンド開始で必ず |
| 突入 | 選んだ種類のラウンドに入ったとき | 死亡から猶予の後／ラウンド開始で必ず |

- どれも `_Freeze`: 張っている窓の数で管理し、0 になったら解除。窓ごとの保持（`*_held`）で多重登録・多重解除を防ぐ（足していない窓が引くと、別の窓の本物のフリーズを解いてしまう）。
- 前面を要する操作（Begin のクリックなど）の前に `ActionExecutor._wait_other_windows()` で待つ。自分が張った分は待たない。移動（OSC・背面のキー）は前面を奪わないので待たない。自爆も背面送信なので待たない。
- フリーズのために前面にした窓は、その窓のフリーズが全部解けたら元の窓へ返す（`FrontLoan`）。
- 停止（`mainGUI.App._stop`）と開始で4種とも強制的に解除する（`SharedState.begin_run`）。停止は**先に監視を止めてから**解く。
  逆だと、ほかの窓のフリーズ明けを待っていた窓が止まる前に起きて自分のフリーズを張り、次の開始まで全窓が止まったままになる。
  さらに窓は開始した回（`WindowState.run_id`）を持ち、前の回の窓は張れない・解けない（止めた後も動き終わっていないスレッドのため）。
- 押している最中の移動（OSC の長押し・背面のキー）は、停止でその場で離す（`stop` を渡す）。

### 5.1 非同期タスクの寿命

`_start_daemon()` で起動するタスクは寿命が3種類ある。停止・中断・キャンセルの仕組みを足すときは、この表に従うこと。

| タスク | 起動元 | 寿命 | ラウンド開始で |
|---|---|---|---|
| `ActionExecutor.do_skip` | 判定・放置モード・グループ・指定ラウンド | そのラウンド中 | 起動される側。`round_seq` で自己判定 |
| `ActionExecutor.do_open_special_round_loop` | DTM/Waldo・Glorbo の続行 | そのラウンド中 | 起動される側。ラウンド終了で自己停止 |
| `LogMonitor._delayed_decision` | テラー確定（置き換えの合図待ち。最大0.3秒） | 数百ミリ秒 | 起動される側。`round_seq` で自己判定 |
| `ActionExecutor.do_after_round` / `do_begin_again` | RoundOver ／ 定期の Verified と分かったとき | ラウンド**間** | **畳んでよい** |
| `ActionExecutor.do_speed_detect` / `do_speed_strafe` | Verified Round End ／ 受理した Verified | ラウンド**間** | **畳んでよい**。`in_round`/`round_seq` で自己停止 |
| `LogMonitor._release_equip_wait_after_delay` | Begin の受理・装備 | **後始末** | **畳んではいけない** |
| `LogMonitor._release_continue_freeze_after_delay` | 死亡・RoundOver | **後始末** | **畳んではいけない** |
| `LogMonitor._release_round_freeze_after_delay` / `_release_speed_freeze_after_delay` / `_release_equip_freeze_after_equip` | 死亡・アイテム取得・装備 | 後始末 | `round_seq` が変わっていれば何もしない（ラウンド開始側が解除済み） |

**後始末を畳んではいけない理由**: Begin の受理の直後にラウンドが始まるのが正常な流れなので、「ラウンド開始でその窓のタスクを畳む」を素朴に作ると、
`_release_equip_wait_after_delay` が `equip_freeze_end()` を呼ばずに終わり、装備待ちフリーズが解けずに**全窓が止まり続ける**。
受理の時点で `waiting_for_equip` は落としてあるので、ラウンド開始側の解除は効かない。この遅延タスクが唯一の解除者。

## 6. 1行の流れ

```
VRChat のログ（追記）
   │ LogMonitor._run: 0.3秒ごとに追記を読む
   ▼
LogMonitor._process(line)
   ├ ログの時刻を st.log_now に（判定はログの時刻で行う）
   ├ LogParser.parse(line) → LogEvent
   ├ [NetworkProcessing] → 霧の看破（_on_network_object）
   └ LogMonitor._HANDLERS[event.kind] のメソッド（_on_round_start・_on_killers_set・_on_verified …）
```

主なイベント:
- **ラウンド開始**: ラウンドごとの状態を落とす。Moon の何回目か（`RoundSequence`）、突入フリーズ、ラウンド開始でのアイテムロスト。テラーを持たないラウンド（Moon・Run・Special）はここで統計を送る。
- **テラー確定**（Killers set / revealed・Enrage・Stunned・Joy・Foxy・看破）→ `_on_killers` → `_plan()` で手順を決めて `_decide()`。置き換えで結論が変わるときだけ合図を待つ。
- **置き換えの合図**（`EVENT_REPLACEMENT`）: 知らせる → `TerrorReplacement.TABLE` で差し替える。
- **RoundOver**: チェイス停止・録画の終了予約・続行フリーズの解除予約・アイテムロストの案内・`do_after_round`。
- **Verified Round End**: Begin を押せる合図。アイテムロストの判定・速度検知の開始。
- **Verified**: `VerifiedTracker` で受理か定期かを見分ける。受理なら `begin_done`。15秒以内にラウンドが始まらなければ定期だったとして押し直す。

## 7. ラウンドの判定

1. 続行リストの供給元: ToN ListTool が動いていて、参加者・待機の誰かが続行リストを持っていれば主催リスト（`host_state.sqlite3`）、それ以外は .tnl。取れない状態が `HOST_LIST_LOSS_GRACE_SEC` 続いたら .tnl へ。
   主催リストの複窓対応のタブは、その窓にいる人の名前の重なりで窓に対応づける。参加者別の希望は、その窓にいる人の分だけを使う。
2. ログのテラーIDを続行リストのIDへ（`RoundDecision.normalize_killer_ids`: Alternate 枠 +134、Unbound +200。8 Pages は terrors.json の `list_id`）。
3. インスタンスの種類で分ける:
   - private（Invite・Invite+・Friends・Friends+）: 自爆・Begin・放置モード・ラウンドごとの指定を使う。
   - 干し芋・焼き芋: `GroupRound.decide`（Classic・Bloodbath など問答無用の自爆、Moon、Fog、Sabotage）。`NORMAL` なら続行リストへ。
   - それ以外: 操作しない（判定のログだけ）。
   - private と干し芋・焼き芋で、他の人がいるのに主催リストが無い窓・その窓にいる誰も続行リストを持っていない窓は、自爆を止める（他人の周回を自分のリストで裁かない）。
4. `RoundDecision.decide_killers`: 続行リスト（Classic と Moon では Special/Moon 枠も見る）・3クラ解放（DTM/Waldo）・Self Inserts の Bloodthirsty（リストで表せないので必ず続行）。
   DTM/Waldo は3勝まで（窓の設定 `cancel_afk_after_unlock` で3勝の後も）。Waldo は Guidance Plush を持っているときだけ（番号は item.json の名前から。分からなければ前と同じく続行）。
   テラーが複数体出るラウンド（`config.OPEN_SPECIAL_ROUND_EXCLUDED_ROUNDS`: Double Trouble・Bloodbath・Midnight）では DTM/Waldo でも続行しない。Bloodbath EX・Cracked などほかの特殊ラウンドは続行する（完全放置モードも同じ）。
   Bloodbath EX はラウンド開始の行では「Bloodbath」と出る。`LogParser` が Killers 行で3つの番号がそろった Bloodbath を「Bloodbath EX」に読み替える（「EX」とだけ出た場合も同じ名前にそろえる。実ログでは未確認）。判断はすべて Killers 行の後なので、続行リスト・ラウンド指定・DTM/Waldo・統計（番号 8）は EX として扱う。干し芋/焼き芋では自爆しない（`GroupRound.ALWAYS_CONTINUE_ROUNDS`。8 Pages・Run と同じ）。

## 8. Begin

- RoundOver から `BEGIN_WAIT_SEC` 待って、前進＋左（OSC は同時押し）で Begin の前へ。
- OSC の窓は UseRight を細かく連打しておき、Verified Round End でカーソルを窓の Begin の上へ一瞬だけ差し込む（前面化しない）。だめなら前面化＋クリック。
- 受理されなければ、画像で BEGIN を探して横・前後に寄せてから押し直す（最大 `BEGIN_RETRY_MAX` 回）。見つからなければ窓を前に出してあきらめる。

## 9. そのほかの機能

- **霧の看破**: `--enable-sdk-log-levels` 付きで起動した窓だけ。画面のボタンが ON で Invite・Invite+・Friends のときだけ判定・表示に使う。それ以外は DB にだけ黙って送る。名前ごとに公開と答え合わせし、一度でも食い違った名前は使わない。看破できる起動で5秒たってもオブジェクトの名前が出なければ DTM と判断する。
- **速度検知**: OSC で VelocityMagnitude を受ける窓だけ。Verified Round End から速度を見て、張り付いた値で 8 Pages（6.5）・Punished（4.0）を先読みする。
- **アイテム**: `item_id`（ロストの判定）と `held_item_id`（所持）を別に持つ。8 Pages でページを取ったとき、持ち込めないアイテム（item.json の 0）をなくす。アイテム自動取得は Begin が通った後、最後にロストしたアイテムを店で装備する（OSC・自動 Begin の窓だけ。完全放置モードでは Guidance Plush だけ（`RoundDecision.guidance_plush_id`）。合計 `ITEM_FETCH_LIMIT_SEC` まで）。取得で動かした縦の視点は、どの終わり方でも戻す。前面を取られて戻せなかった分は、裏で見張って、この窓が前面になったら・ほかの窓が誰もフリーズしていなければ前面を借りて戻す（`_restore_view_soon`）。
- **VRChat の起動**: 窓ごとに `--profile=N`・`--osc=<受信>:127.0.0.1:<送信>`（窓 i は 9000+10i と +1）。窓が出てから `LAUNCH_STAGGER_SEC` 置いて次を起動する（VRChat API の 429 を避けるため）。入室後の選択画面は `ToNEntry` が突破する。
- **自動アップデート**: 起動時に GitHub Releases の最新タグと `config.APP_VERSION` を比べ、新しければダウンロードして実行中の exe を `.old` にして差し替える。

## 10. 統計

- 送る: ラウンドごとに1回、`rpc/register_round`（開始の時刻・ラウンドの番号・マップ・テラー3体まで・transformed_uid・インスタンスの番号）。インスタンスの番号はインスタンスIDの SHA-256 の先頭8バイトで、DB 側で同じインスタンスの同じラウンドを1行にまとめる（ソロなら送らない）。
- transformed_uid: `rpc/get_transformed_uid` で VRChat uid から int2 の番号をもらう（無ければ DB が割り当てる）。`Users` の表は直接読まない（`supabase/get_transformed_uid.sql`）。
- 統計画面: `RoundStore`（%APPDATA%\ToNAutoBeginner\rounds.sqlite）に DB の差分だけを取り込み、手元で集計する。自分が送った行は送ったときに手元にも書く。

## 11. 置き場所

| ファイル | 中身 |
|---|---|
| `%APPDATA%\ToNAutoBeginner\settings.json` | 設定（OBS のパスワードは DPAPI で暗号化） |
| `%APPDATA%\ToNAutoBeginner\debug.log`（.1〜.3） | デバッグログ。20MB で世代を回す |
| `%APPDATA%\ToNAutoBeginner\rounds.sqlite` | 統計画面の手元の保存 |
| `%APPDATA%\ToNAutoBeginner\fog_object_names.json` | 看破の名前ごとの答え合わせ |
| `%APPDATA%\ToNAutoBeginner\begin_miss\` | BEGIN が見つからなかった撮影（窓ごとに2枚） |
| `%APPDATA%\ToN ListTool\host_state.sqlite3` | ToN ListTool の主催リスト（読むだけ） |
| リポジトリの `terrors.json`・`maps.json`・`item.json`・`voice/`・`begin_templates/`・`shop_templates/` | exe に同梱するデータ |
| `maps.json` の各マップ | `id`・`name` と印 `normal`（通常ラウンドで出る）・`8pages`（8 Pages で選ばれる）・`run`（Run のマップ）。1 か 0。同じ番号は 1 だけ（Dring King's Citadel と Sewers）で、統計の名前はラウンドに合う印で選ぶ（`Statistics.map_name_for_id`） |

## 12. ビルド・テスト

- ビルド: リポジトリの直下で `python build.py`（Nuitka の onefile）。展開先は `%LOCALAPPDATA%\ToNAutoBeginner\<版>-<ビルドの印>`。
  exe ができたら続けてインストーラー `dist/ToNAutoBeginner-Setup.exe` を作る（Inno Setup 7 か 6 の ISCC。`installer/ToNAutoBeginner.iss`。見つからなければ exe だけ）。
- インストーラー: ユーザー単位（管理者権限なし）で `%LOCALAPPDATA%\Programs\ToNAutoBeginner` に入れる。自動更新は exe の横で差し替えるので、書き込める場所に入れる。
  起動中かは `config.APP_MUTEX_NAME` のミューテックス（`main.hold_running_mutex`。.iss の AppMutex と同じ名前）で見る。
  アップデート（自動更新・Setup の上書き）ではアンインストールは走らずデータは残る。アンインストールはインストール先・`%APPDATA%\ToNAutoBeginner`・`%LOCALAPPDATA%\ToNAutoBeginner` を全部消す。
  `AppId` は変えない（変えると別アプリ扱いになる）。
- 新しく入れる人はインストール先を選ぶ（`DisableDirPage=auto`。上書きでは聞かない）。Program Files など管理者権限が要る場所は選べない（.iss の `NextButtonClick`）。
- exe 単体からの移行（`Migration.py`）: exe 単体（Inno Setup のアンインストール情報 `InstallLocation` の外）で起動したら、最新リリースの `ToNAutoBeginner-Setup.exe` を落とし、元の exe の場所を settings.json（`migrated_from`）に書いて、PowerShell の係を裏で起こしてツールを終える。係は起動中の目印が消えるのを待ち（最大60秒）、Setup を `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /launch=1` で動かす。Setup は終わるとツールを起動する。Setup が成功したら（終了コード 0）係が元の exe（と `.old`）を消す。消せなかったときはインストール版が最初の起動で消す。失敗したらその回は今のまま動き、次の起動でやり直す。移行するときは更新の確認はしない。
- リリース: `ToNAutoBeginner.exe`（自動更新が探すのはこの名前）と `ToNAutoBeginner-Setup.exe`（移行が探す）の両方を、`APP_VERSION` と同じタグのリリースに置く。
- テスト: `src` で `python UnitTest.py`（全部）／`python UnitTest.py test_freezes`（1ファイル）。本体は `src/tests/test_*.py`、共通の準備は `src/tests/support.py`（Windows 専用モジュールの差し替え・本物の OSC / 設定 / DB に触らない安全装置）。
