# ToNAutoBeginner 技術仕様書

対象: `src/` 以下の実装（develop、2026-10-12 時点。コミット 2ef96aa ＋ 置き場所の整理）。
利用者向けの説明は `README.md`。本書は開発者向けに、モジュールの責務・スレッド・状態・データの流れを書く。
関数シグネチャの網羅は目的としない。細かい理由や実測値は各モジュールの docstring・コメントにある。
コードにもコミットにも理由が残っていない決めごとは、§13 に短く書く。

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
              │     ├─ UtilsWatch         : ToN Utils の続行・自爆の判断を読む（設定で ON のとき）
              │     ├─ ActionExecutor     : 自爆・Begin・AFK 対策・速度検知・チェイス・アイテム自動取得・Run リスポーン（実操作）
              │     │     ├─ WindowOperator : 前面化・キー・クリック・カーソル・窓の矩形（Win32）
              │     │     ├─ OSCClient / OSCReceiver / OSCQuery : OSC の送信・速度の受信
              │     │     └─ BeginDetect / ItemFetch / RespawnButton / RunFace : 撮影から絵で探す（OpenCV）
              │     ├─ PlaySound / Recorder : 音声・OBS 録画
              │     └─ ConnectDB          : 統計の送信（Supabase）
              ├─ SharedState   : 窓をまたぐ状態（操作のロック・全窓フリーズ・全窓共通の設定）
              ├─ MatchTNL      : 続行リスト（.tnl・ToN ListTool の主催リスト）の読み込み
              ├─ VRChatLauncher / VRChatDiscovery / ToNEntry / SaveLoad : VRChat の起動・窓とログの対応・入室操作・セーブコード
              ├─ WindowArrange : 窓の整列（升の計算）
              ├─ WindowVolume / ToolLauncher / BugReport / AutoUpdate / Migration
              └─ StatisticsGUI → RoundStore / Statistics / ConnectDB : 統計画面
```

## 2. モジュール一覧

`src/` の全モジュール（`tests/` を除く）。

### 画面・設定
| モジュール | 責務 |
|---|---|
| `main.py` | 入口。起動中の目印（ミューテックス）を持ち、`mainGUI.App` を作って `mainloop()`。先頭に Nuitka の参考のコマンド（build.py の `BASE_ARGS` と同じ）。 |
| `mainGUI.py` | 画面。窓タブ・設定の保存（settings.json）・監視の開始/停止（1つのボタン）・VRChat の起動 UI と入室操作・主催リストの追従・キー（停止・開始・自爆キャンセル・チェイス。キーの設定の画面）・窓の整列と配置を覚える・外部ツール・OBS・音量・不具合の報告・自動アップデート。主催リストの `user_save.json` は時刻ではなく中身（`last_active`・`accounts`）のダイジェストで変化を見る。画面のログは行をためて 1 回の差し込みでまとめて流す。 |
| `UiInbox.py` | 別のスレッドから Tk のスレッドへ関数を渡す受付箱（`call_soon`）。別のスレッドは Python の queue に入れるだけで、Tk のスレッドが 50ms ごとに取り出して実行する。Tk のスレッドでないスレッドが Tk を呼ぶと、スレッド 1 本ごとに Tcl の Event とスレッドごとのデータ（約 17KB）が残るため（FC）。閉じるときは残りを実行してから。 |
| `ProcStats.py` | このプロセスの WS・Private・ハンドル数・スレッド数（ctypes）。debug.log に 10 分ごと（`MEMORY_LOG_SEC`）と起動時に 1 行。 |
| `TaskStats.py` | CPU の使い道を仕事ごとに記録する（FI）。スレッドを立てる所は `TaskStats.thread(名前, target, …)` で、終わるときにそのスレッドの CPU を名前ごとに足す（長生きのスレッドは立っている間の CPU を `ProcStats.thread_cpu` で読んだ前回との差）。`mainGUI._log_memory` が 10 分ごとに `snapshot()` で前回からの差（プロセス全体・画面のスレッド・終わった仕事・生きているスレッド・最大 WS の増え）をまとめ、`line()` が debug.log に `[CPU]` の 1 行を書く（上位 `MEMORY_LOG_TOP` 個）。画面のスレッドの内訳（FO）は、UiInbox が取り出して実行した関数と、`@TaskStats.tk_timed` を付けた定期の関数（`_poll_host_save`・`_poll_tool_buttons`・`_log_memory`・`_refresh_cooldown`・`_draw_round_chart`）を、名前ごとに `time.thread_time()` の差で `tk_account` へ足し、`[CPU]` の行の「画面 n秒」の後ろに「（上位: 名前 ×回数 n秒 / …）」で出す（ユーザー操作のコールバックは対象外）。行の末尾に、本体の CPU から画面・終わった・生きている・ほかのスレッドを引いた「未計上 n秒」と、TaskStats が知らない Python のスレッド（zeroconf など。メインスレッド＝画面は除く）の CPU を `Thread.name` ごとに出す「ほかのスレッド: …」を足す（FS）。 |
| `config.py` | 定数（パス・GUI の色・待ち時間・ID・ラウンドの集合など）。`resource_path()` で開発実行と Nuitka の exe の両方から同梱データ（`assets/…`）を引く。 |
| `State.py` | `WindowConfig`（窓ごとの設定）と `WindowState`（窓ごとの実行時の状態）。ロジックは持たない。 |
| `SharedState.py` | 窓をまたぐ状態。`_GLOBAL_ACTION_LOCK`・全窓フリーズ4種（`_Freeze`）・全窓共通の設定（`_Setting`）・前面の貸し借り・掴んでいる窓の一覧。 |
| `HotKey.py` | キー名の検証・表記・捕捉。 |
| `UIFont.py` | 日本語を持つフォントを PC にあるものから選ぶ。 |
| `SecretStore.py` | OBS のパスワードを DPAPI で暗号化して保存する。 |
| `ToolLauncher.py` / `ProcessCheck.py` | 外部ツールの起動、プロセスの生死（ToolHelp スナップショット）。一覧は `RunningNamesCache`（App が 1 つ持ち、主催リストの元とツールのボタンで共有。FO）で取る: `names()` は前回から `PROCESS_LIST_MIN_SEC`（10 秒）以内なら前回の一覧、`watch(名前)` は前回の一覧で見つけた PID の生死だけを `is_pid_alive` （PID を開いて終了コードと exe 名を見る。数 µs）で見て、消えた・未発見のときだけ（10 秒に 1 回まで）一覧を取り直す。 |
| `WindowArrange.py` | 窓の整列の升の割り方と矩形の計算だけ（Win32 は呼ばない。動かすのは `WindowOperator`）。 |

### ログと判定
| モジュール | 責務 |
|---|---|
| `LogMonitor.py` | 1窓を1スレッドで監視する中心。ログを読み、`WindowState` を更新し、判定して `ActionExecutor` に操作を頼む。イベントの種類ごとに `_HANDLERS` からメソッドを引く。 |
| `LogParser.py` | ログ1行を `LogEvent`（種類＋付帯情報）にする。状態を持たない。 |
| `VerifiedTracker.py` | `Verified` の行が Begin の受理か、約300秒ごとの定期シグナルかを、ログの時刻で見分ける。 |
| `MatchTNL.py` | .tnl と ToN ListTool の主催リスト（`host_state.sqlite3`）の読み込み、ログのテラーID → 続行リストのIDの変換（Alternate +134・Unbound +200）。全窓で共有する `SharedLists`。ビット列 → ID の集合の変換は同じビット列なら覚えた結果を使う。 |
| `RoundDecision.py` | 続行するか・3クラ解放（DTM/Waldo）の対象かの判定。 |
| `GroupRound.py` | 干し芋／焼き芋のインスタンスのルール（と、プライベートの Sabotage）。 |
| `RoundSequence.py` | ラウンドの並び（通常・特殊）から Moon の解放状況を推定する。 |
| `TerrorReplacement.py` | 置き換えテラー（Atrached・Hungry Home Invader・Bloodthirsty・Foxy・Glorbo・Gigabytes など）の合図の行（`SIGNALS`）と差し替えの表（`TABLE`）。 |
| `FogEarlyRead.py` | 霧の看破（`[NetworkProcessing]` のオブジェクト名から公開前にテラーを特定）の許可と、名前ごとの答え合わせの記録。 |
| `UtilsWatch.py` | ToN Utils のログ（`ton-utils.log`）の press・hold・許可の変化を読み、Utils の source を窓に対応づける（`save-history.sqlite` の `logs` の source・started だけを読み取り専用で読む）。全窓で1つ（`shared()`）。 |
| `ReadJson.py` / `ItemCatalog.py` | terrors.json・item.json の読み込みと引き方。 |

### 操作
| モジュール | 責務 |
|---|---|
| `ActionExecutor.py` | 実際の操作。自爆（背面へキー）、Begin（移動・押し方・押し直し・位置合わせ）、DTM/Waldo の AFK 対策、速度検知、チェイス、アイテム自動取得、アイテムロストの前面化と音声、死んだ・生き残ったときにアイテムを落とす、Run のリスポーン。移動の実測（`MotionSampler`）。 |
| `WindowOperator.py` | Win32 の操作。前面化・背面へのキー・クリック・カーソル・窓の矩形と移動・前面の貸し借り（`FrontLoan`）。VRChat を前に出すたびに `config.CURSOR_LOCK_KEY`（Tab）を押して離し、カーソルを中央に固定する（`lock_cursor`。依頼者の実測。浮いているかは見ない。その窓が前面のときだけ押す）。 |
| `OSCClient.py` / `OSCReceiver.py` | VRChat への OSC 送信（移動・視点・UseRight・Drop）と、速度（VelocityMagnitude）の受信。受け方は窓ごとに「OSCQuery → 届かなければ固定ポート」。固定ポートの受信（`_run`）は、先頭が `/avatar/parameters/VelocityMagnitude` か `/avatar/parameters/Grounded`（NUL 終端込み）でないデータを `parse_message` せずに捨てる（FO）。 |
| `OSCQuery.py` | ツールに1つの OSCQuery のサービス（HTTP＋mDNS）。VRChat が送ってきた値を、送り元のポート → プロセス → 窓で振り分ける。 |
| `BeginAdjustShots.py` | Begin の位置合わせの各段の撮影を、見つかった四角と照準を描いて残す（1/2 の JPEG。新しい 10 組）。 |
| `BeginDetect.py` / `BeginMiss.py` / `ScreenCapture.py` | 画像で `[ BEGIN ]` を探す（PrintWindow で背面の窓も撮る）、見つからなかった撮影を残す。 |
| `ItemFetch.py` | アイテム自動取得。OSC で店へ移動し（柵は速さで当たったのを見て跳び、跳んだ後の着地 2 回で越えた。速さを受け取れない窓は決め打ち）、SIFT＋ホモグラフィで店のボタンを見つけ、視点を回してクリックする。HUD に掛かるなら店の画面を真ん中へ寄せる。 |
| `FetchMiss.py` | アイテム取得で「店の画面」「Have Plush の行」が見つからなかったとき、と Guidance Plush の装備を確かめられなかったとき（行を押す前と Equip を待った後の 2 枚）の撮影を残す（不具合の報告には撮影のチェックがあるときだけ）。 |
| `RespawnButton.py` | Esc のメニューの「リスポーン」のボタンを撮影から絵で探す（Run のリスポーン）。 |
| `RunFace.py` | Run のリスポーンの後、玉座＋階段の切り出し（`assets/templates/run/throne.png`）を特徴点（SIFT＋ホモグラフィ）で撮影から探し、玉座の軸の x（真正面からのずれ）と大きさ（倍率）を測る（FOV・解像度が違っても見つかる。閾値の根拠は `config.RUN_FACE_FEATURE_*`）。受け入れなかったときだけ、階段の上の構造の絵の照合（倍率 0.85〜1.15）を保険に使う。 |
| `RunPos.py` | Run の位置合わせのためのデータ集め（撮って残すだけ）。止まっているときの基準の撮影と、Run の後の撮影を等倍の PNG で残す。 |
| `ToNEntry.py` | 入室直後の選択画面を突破する（OSC の横移動＋クリック）。セーブコードを貼る流れ（`load_code`）。 |
| `SaveLoad.py` | Save/Load のコード欄・入力欄を絵で探す所と、クリップボード。コード欄は最初に特徴点（SIFT＋ホモグラフィ）で板を探して欄の左寄りを押し、受け入れられなければ今の照合（0.8）。見つからなければ 3 秒（0.5 秒おき）撮り直し、数字だけを debug.log に。セーブコードはどこにも書き出さない（コード欄が写る撮影も残さない）。 |

### 起動・外部連携・統計
| モジュール | 責務 |
|---|---|
| `VRChatLauncher.py` / `VRChatDiscovery.py` | Steam から `launch.exe` を探して窓ごとに `--profile=N`・`--osc=` で起動、窓とログの対応付け（プロセスの起動時刻とログの作成時刻）。 |
| `AutoUpdate.py` | GitHub Releases の最新版と比べて exe を差し替える。古い展開先も消す。開発実行では無効。 |
| `Migration.py` | exe 単体で動いている人をインストーラー版へ移す（Setup を落として画面なしで実行。入れる場所は元の exe のフォルダ）。開発実行では無効。 |
| `PlaySound.py` | MCI（winmm）で音声を鳴らす。 |
| `WindowVolume.py` | Windows のアプリごとの音量で、窓の状態ごとに VRChat の音量を変える。見張りのスレッドが Core Audio を使い、止めると元の音量に戻す。 |
| `Recorder.py` / `OBSClient.py` | 続行ラウンドを OBS（obs-websocket v5）で録る。通信は専用のスレッド。 |
| `ConnectDB.py` | Supabase との通信。ラウンドの送信（`rpc/register_round`）、transformed_uid の取得（`rpc/get_transformed_uid`）、統計画面の差分取得。`.env` が無ければ何もしない。 |
| `RoundStore.py` / `Statistics.py` / `StatisticsGUI.py` | 統計画面。DB の差分を手元の SQLite に取り込み、手元で集計する。二項分布の検定。 |
| `DebugLog.py` / `BugReport.py` | debug.log（秘密・ユーザー名を伏せる）と、不具合の報告（Discord の Webhook。窓を複数・全部選べる）。 |
| `UnitTest.py` | テストを回す入口（`tests/test_*.py`）。 |

## 3. スレッド

| スレッド | 何をするか |
|---|---|
| Tk のスレッド | 画面。ほかのスレッドからは `after(0, …)` で渡す。重い処理（プロセスの一覧・SQLite・窓の整列）は置かない。 |
| 主催リスト・外部ツールの確認 | 3秒ごとに裏のスレッドで読み、結果だけを Tk のスレッドで当てる（`_poll_host_save`・`_poll_tool_buttons`）。 |
| keyboard のフック | 停止・開始・自爆キャンセル・チェイスのキー。押した・離した通知のたびに見る（ポーリングしない）。 |
| LogMonitor（窓ごと） | `LOG_POLL_INTERVAL`（0.3秒）ごとにログの追記を読み、行ごとに `_process`。 |
| 操作のデーモン | 自爆・Begin・AFK 対策（DTM/Waldo・Glorbo のループと、ラウンド開始ごとの AFK 回避の見張り `do_afk_watch`）・フリーズの遅延解除・Utils の判断待ち（`_wait_utils`）・Run のリスポーンなど（§5.1）。`LogMonitor._start_daemon`。UtilsWatch は自分のスレッドを持たず、ここから読む。 |
| OSC の受信（窓ごと） | 速度を受け続ける（監視中ずっと）。OSCQuery で届かなければ固定ポートへ切り替える待ちのスレッドもある。 |
| OSCQuery のサービス | HTTP・UDP の受け口・mDNS の広告（ツールに1つ。最初の監視か VRChat の起動で立て、ツールを閉じるまで出したまま）。 |
| 音量の見張り | `WindowVolume`。窓の状態に合わせて音量を変え続け、止めると元に戻して COM を片付ける。 |
| 移動の実測 | `MotionSampler`。移動の間に速さと接地を読み、離してから少し後に debug.log へ1行。 |
| チェイス・UseRight の連打・視点の戻し | `ActionExecutor` が必要な間だけ立てる。 |
| 入室操作 | VRChat の起動の後、窓ごとの入室（選択画面・セーブコード・Begin）と、Begin の受理の後の Guidance Plush（窓ごと）。 |
| 窓の整列 | 「窓を整列」で裏のスレッド（全画面・最小化の窓を戻すのに待つため）。 |
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
- Utils: `utils_anchor_time`・`utils_anchor_seq`（起点の行）・`utils_wait_seq`・`utils_reason`
- 霧の看破・3クラ・自爆の流れ（`suicide_seq`・`suicide_cancelled_round`）・開始した回（`run_id`）など

### 4.3 `SharedState`（全窓で共通）
- `_GLOBAL_ACTION_LOCK`: 前面を切り替える操作（前面化・クリック・カーソルの差し込み・リスポーンのメニュー）は必ずこれを取る。1度に1窓だけ。
- 全窓フリーズ4種（§5）。
- 全窓共通の設定（`_Setting`）: インスタンスタイプ・自爆キー・放置モード・速度検知・フリーズ設定（8 Pages・Punished・突入するラウンド）・続行リストの供給元（`"host"`/`"tnl"`）・アイテム自動取得とその感度・アイテム取得→Begin モード・アイテムを落とす（`continue_drop_item`）・続行ラウンドの後に窓を戻す（`continue_restore_window`）・チェイスを使う（`chase_enabled`）・Utils の判断に従う（`follow_utils`）・Run リスポーン（`run_respawn`）・開始した回・ツールの窓を隠しているか。
- 掴んでいる VRChat の窓・当ツール自身の窓。

### 4.4 続行リスト（`MatchTNL.SharedLists`）
続行リスト・参加者別の希望・参加者・タブを、画面と全窓の LogMonitor が同じ1つで持つ。
**中の dict は書き換えず、新しい dict を代入して差し替える**（`clear()`→`update()` の途中で判定した窓が空のリストを見て自爆しないように）。

## 5. 全窓フリーズ

| 種類 | 張る | 解く |
|---|---|---|
| 続行 | 続行ラウンド（アナウンスが鳴るもの）が決まったとき。DTM/Waldo の続行は張らない | 死亡・RoundOver から猶予の後（`_release_continue_freeze_after_delay`）／次のラウンド開始 |
| 装備待ち | アイテムロストの窓。自動で取りに行かない窓は Begin が通ったとき（通らなければ張らない）、取りに行く窓は店で前面を借りてボタンを押す間だけ、アイテム取得→Begin モードは Begin の前（ロストした回はツールは移動もしないし押さない。装備も Begin も利用者がして、その Begin の受理で解く。FY・FZ）。張った順に並び、先頭の窓だけが前面化＋音声 | 装備＋Begin の受理から猶予の後（Begin を押さない窓は装備から猶予の後）・取りに行く窓は店の操作の後（取れた・取れない・時間切れとも）／ラウンド開始 |
| 速度検知 | 速度で 8 Pages / Punished と分かったとき（設定で選ぶ） | 8 Pages はアイテムを取ったら猶予の後／ラウンド開始で必ず |
| 突入 | 選んだ種類のラウンドに入ったとき（Sabotage は Sus player の行で murder／star に分ける。行は開始の行より前に来る） | 死亡から猶予の後／ラウンド開始で必ず |

- どれも `_Freeze`: 張っている窓の数で管理し、0 になったら解除。窓ごとの保持（`*_held`）で多重登録・多重解除を防ぐ（足していない窓が引くと、別の窓の本物のフリーズを解いてしまう）。
- 前面を要する操作（Begin のクリックなど）の前に `ActionExecutor._wait_other_windows()` で待つ。自分が張った分は待たない。移動（OSC・背面のキー）は前面を奪わないので待たない。自爆も背面送信なので待たない。
- フリーズのために前面にした窓は、その窓のフリーズが全部解けたら元の窓へ返す（`FrontLoan`）。
- 画面の「フリーズ」の枠に、張っている種類と窓を出す。
- 停止（`mainGUI.App._stop`）と開始で4種とも強制的に解除する（`SharedState.begin_run`）。停止の順番は:
  1. 入室操作を止める・掴んでいる窓の記録を消す
  2. **監視を全部止める**（`LogMonitor.stop`）
  3. 音量を元に戻す（見張りが「戻し終えた」まで最大 `WINDOW_VOLUME_STOP_WAIT_SEC` 1秒。COM の片付けは待たない。前はスレッドの終わりを3秒待っていて、停止が毎回3秒かかった）
  4. フリーズを解く（`begin_run`）など
  監視を先に止めるのは、ほかの窓のフリーズ明けを待っていた窓が止まる前に起きて自分のフリーズを張り、次の開始まで全窓が止まったままになるのを防ぐため。音量を待っている間に、止めたはずの窓が Begin の移動を始めることもあった。
  さらに窓は開始した回（`WindowState.run_id`）を持ち、前の回の窓は張れない・解けない（止めた後も動き終わっていないスレッドのため）。
- 押している最中の移動（OSC の長押し・背面のキー）は、停止でその場で離す（`stop` を渡す）。

### 5.1 非同期タスクの寿命

`_start_daemon()` で起動するタスクは寿命が3種類ある。停止・中断・キャンセルの仕組みを足すときは、この表に従うこと。

| タスク | 起動元 | 寿命 | ラウンド開始で |
|---|---|---|---|
| `ActionExecutor.do_skip` | 判定・放置モード・グループ・指定ラウンド・Utils の hold | そのラウンド中 | 起動される側。`round_seq` で自己判定 |
| `ActionExecutor.do_open_special_round_loop` | DTM/Waldo・Glorbo の続行 | そのラウンド中 | 起動される側。ラウンド終了で自己停止 |
| `LogMonitor._delayed_decision` | テラー確定（置き換えの合図待ち。最大0.3秒） | 数百ミリ秒 | 起動される側。`round_seq` で自己判定 |
| `LogMonitor._wait_utils` | Utils の判断に従う窓のテラー確定 | そのラウンド中（最大 `UTILS_WAIT_SEC`） | `round_seq` で自己判定 |
| `ActionExecutor.do_run_respawn` | Run のラウンドの開始 | そのラウンド中 | ラウンドが変わったら移動をやめる |
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
- **ラウンド開始**: ラウンドごとの状態を落とす。Moon の何回目か（`RoundSequence`）、突入フリーズ、ラウンド開始でのアイテムロスト、Run ならリスポーン（§9）。テラーを持たないラウンド（Moon・Run・Special）はここで統計を送る。
- **テラー確定**（Killers set / revealed・Enrage・Stunned・Joy・Foxy・看破）→ `_on_killers` → `_plan()` で手順を決めて `_decide()`。置き換えで結論が変わるときだけ合図を待つ。Utils の判断に従う窓は Utils を待つ（§9）。
- **置き換えの合図**（`EVENT_REPLACEMENT`）: 知らせる → `TerrorReplacement.TABLE` で差し替える。
- **RoundOver**: チェイス停止・録画の終了予約・続行フリーズの解除予約・アイテムロストの案内・`do_after_round`。
- **死亡（You died.）・生存（Lived in round.）**: そのラウンドで先に来た方で 1 回、アイテムを落とす（`_drop_when_round_ends`）。
- **Verified Round End**: Begin を押せる合図。アイテムロストの判定・速度検知の開始。
- **Verified**: `VerifiedTracker` で受理か定期かを見分ける（予定は「最後の定期 + 300 秒」、許容 ±2.0 秒。倍数では追わない）。受理なら `begin_done`。15秒以内にラウンドが始まらなければ定期だったとして押し直す（そのラウンドにアイテム取得で店へ行っていたら、Run の後と同じくリスポーン → Begin 前の移動から。押し直しの流れであきらめても、あきらめた窓のやり直し（§8）に入る）。

## 7. ラウンドの判定

1. 続行リストの供給元: ToN ListTool が動いていて、参加者・待機の誰かが続行リストを持っていれば主催リスト（`host_state.sqlite3`）、それ以外は .tnl。取れない状態が `HOST_LIST_LOSS_GRACE_SEC` 続いたら .tnl へ。
   主催リストの複窓対応のタブは、その窓にいる人の名前の重なりで窓に対応づける。参加者別の希望は、その窓にいる人の分だけを使う。
   主催リストは `HOST_SAVE_IGNORED_KEYS`（Fog (Alternate)・Ghost (Alternate) の枠）を読み飛ばす（ログの Fog (Alternate) は通常の Fog の枠で照合するため、畳むと判定が変わる）。tnl の読み込みでも同じ 2 つの枠を読み飛ばす（件数が主催リストと一致する。FV）。
2. ログのテラーIDを続行リストのIDへ（`RoundDecision.normalize_killer_ids`: Alternate 枠 +134、Unbound +200。8 Pages は terrors.json の `list_id`）。
3. インスタンスの種類で分ける:
   - private（Invite・Invite+・Friends・Friends+）: 自爆・Begin・放置モード・ラウンドごとの指定を使う。Run は判定を通らず、全続行で選んでいればラウンド開始の時点で全続行（リスポーンしない）として扱う。移動・クリック・カーソル・チェイスなどの操作（OSC・マウス）は private の窓だけ（`ActionExecutor.can_operate`。種類が分からない窓もしない）。前面化・音・判定・グループの自爆（キー）は見ない。
   - 干し芋・焼き芋: `GroupRound.decide`（Classic・Bloodbath など問答無用の自爆、Moon、Fog、Sabotage）。`NORMAL` なら続行リストへ。
   - それ以外: 操作しない（判定のログだけ）。
   - private と干し芋・焼き芋で、他の人がいるのに主催リストが無い窓・その窓にいる誰も続行リストを持っていない窓は、自爆を止める（他人の周回を自分のリストで裁かない）。
4. `RoundDecision.decide_killers`: 続行リスト（Classic と Moon では Special/Moon 枠も見る）・3クラ解放（DTM/Waldo）・Self Inserts の Bloodthirsty（リストで表せないので必ず続行）。
   DTM/Waldo は3勝まで（窓の設定 `cancel_afk_after_unlock` で3勝の後も）。Waldo は Guidance Plush を持っているときだけ（番号は item.json の名前から。分からなければ前と同じく続行）。
   テラーが複数体出るラウンド（`config.OPEN_SPECIAL_ROUND_EXCLUDED_ROUNDS`: Double Trouble・Bloodbath・Midnight）では DTM/Waldo でも続行しない。Bloodbath EX・Cracked などほかの特殊ラウンドは続行する（完全放置モードも同じ）。完全放置モードの private の窓では、Glorbo・Bloodbath EX の 3 体 DTM・Guidance Plush を持っているときの Bloodbath EX の 3 体 Waldo は、3 クラの状態・「DTM/Waldo続行」・続行リストに関係なく続行する（`LogMonitor._hands_free_keep_reason` → plan `hands_free_keep` → `_start_glorbo_continue`。他窓フリーズ・AFK 解除ループ `glorbo=True`。アナウンス・録画なし。FX）。
   Bloodbath EX はラウンド開始の行では「Bloodbath」と出る。`LogParser` が Killers 行で3つの番号がそろった Bloodbath を「Bloodbath EX」に読み替える（「EX」とだけ出た場合も同じ名前にそろえる。実ログでは未確認）。判断はすべて Killers 行の後なので、続行リスト・ラウンド指定・DTM/Waldo・統計（番号 8）は EX として扱う。干し芋/焼き芋では自爆しない（`GroupRound.ALWAYS_CONTINUE_ROUNDS`。8 Pages・Run と同じ）。
5. 選んだ Fog・Ghost・8 Pages のラウンド（音声・突入フリーズ）は、`(Alternate)` の付いた Killers 行でも同じラウンドとして扱う。

## 8. Begin

- RoundOver から `BEGIN_WAIT_SEC` 待って、前進＋左（OSC は同時押し）で Begin の前へ（通常 2.08 秒・Punished の後は 3.2 秒）。
- 押している時間は「動き出し」（受信した速さが `MOTION_START_SPEED` を超えた時）から数える。離すのは 押し始め ＋ 秒数 ＋ 延び（延び ＝ 動き出しまで − `BEGIN_USUAL_START_SEC` 0.08。上限 `BEGIN_EXTEND_MAX_SEC` 0.5 ＋ 押し直しで失った時間）。重い窓で動き出しが遅れ、距離が足りなかったため。
- 速さを受け取れているのに `BEGIN_START_WAIT_SEC`（0.6秒）動き出さなければ、いったん離して 0.1 秒後に押し直す（`MOVE_REPRESS_MAX` 2回まで）。それでも動かなければ押し始めから数えた時間で離し、debug.log に書く。速さを受け取れない窓は今までどおり時間で押す。
- debug.log の「前進 … 秒＋左 … 秒（OSC）」の行に、「通常」の行から押し始めるまでの遅れ（秒）を書く。
- OSC の窓は UseRight を細かく連打しておき、Verified Round End でカーソルを窓の Begin の上へ一瞬だけ差し込む（前面化しない）。だめなら前面化＋クリック。
- 受理されなければ、画像で BEGIN を探して横・前後に寄せてから押し直す（最大 `BEGIN_RETRY_MAX` 回）。前後は文字の幅（高さ比）が `BEGIN_ADJUST_DEPTH_OK`（0.11〜0.33）の外なら寄せる。横・前後が済んだ後、その窓が前面のときだけ、BEGIN の中心が照準より縦に `BEGIN_PITCH_TOL`（0.25）× 文字幅を超えて外れていれば、マウスの相対移動（`ItemFetch.split_move` の刻み・1 回 `ItemFetch.MOVE_CAP` まで）で視点の高さを合わせる（`ActionExecutor._pitch_to_begin`。最大 `BEGIN_PITCH_MAX_STEPS` 3 歩。縦の感度は保存値、無ければ 1 歩目を `BEGIN_PITCH_PROBE_UNITS` 24 動かして撮り直し、動いた px から感度と向きを測る（保存しない）。向きが逆なら符号を反転して 1 度だけやり直す。動かす直前に毎回前面かを確かめる。合わせた視点は戻さない）。横の位置合わせは 1 歩目を `BEGIN_ADJUST_PROBE_SEC` 0.1 秒押して横の速さ（文字幅/秒）を見積もり、2 歩目以降は 1 歩を `BEGIN_ADJUST_MAX_SEC` 0.10 秒（1 歩目と同じ長さ）までにする（長く押すと一定の速さの分が増えて見積もりより行き過ぎるため）。見積もった速さには下限 `BEGIN_ADJUST_RATE_MIN` 8.0 文字幅/秒を置く（遅く見積もるほど長く押して行き過ぎるため。実測は debug.log に 1 行）。BEGIN が見えなければ、後ろへ `BEGIN_SEARCH_BACK_SEC` 0.15 秒 → 戻して前へ `BEGIN_SEARCH_FORWARD_SEC` 0.3 秒 × `BEGIN_SEARCH_FORWARD_TRIES` 4 回の順に探す。前へ動いた量は速さの受信の積分（Begin 前の移動の実測と同じ。`MotionSampler.distance_so_far`）で測る。0.3 秒押して `BEGIN_SEARCH_BLOCKED_DIST` 0.3 未満しか動けなければ壁（台座など）で、前へ探すのをやめて後ろへ `BEGIN_SEARCH_BLOCKED_BACK_SEC` 0.3 秒動いて見る（無ければあきらめる。さらに戻さない）。壁でなく見つからなければ、最後に後ろへ戻すのは測った前への移動量だけ（`BEGIN_SEARCH_RETURN_MIN` 0.5 未満なら戻さない）。速さを受け取れない窓は押した秒数で戻す。実測は debug.log に 1 行。見つからなければ窓を前に出してあきらめる。あきらめた窓（BEGIN が見つからない・`BEGIN_RETRY_MAX` 回押して受理されない）は、30 秒後（`BEGIN_GIVE_UP_RETRY_SEC`）に「リスポーン → Begin 前の移動 → 押す」をやり直し（Run リスポーンの設定が OFF でもリスポーンする。3 回押して受理されなかった窓の 1 回目だけは押し直しのみ）、通らなければ 3 分おき（`BEGIN_GIVE_UP_REPEAT_SEC`）に繰り返す。回数の上限は無く、ラウンドが始まる・止める・Begin が通るまで続ける（1 窓だとほかにラウンドを始める窓が無く、止まり続けるため）。

## 9. そのほかの機能

- **霧の看破**: `--enable-sdk-log-levels` 付きで起動した窓だけ。画面のボタンが ON で Invite・Invite+・Friends のときだけ判定・表示に使う。それ以外は DB にだけ黙って送る。名前ごとに公開と答え合わせし、一度でも食い違った名前は使わない。看破できる起動で5秒たってもオブジェクトの名前が出なければ DTM と判断する。「Transferred ownership of … to 0」の行は持ち主の移動で、看破には使わない。
- **速度検知**: OSC で VelocityMagnitude を受ける窓だけ。Verified Round End から速度を見て、張り付いた値で 8 Pages（6.5）・Punished（4.0）を先読みする。入室時の Guidance Plush のやり直しが控えている窓（`_plush_retries > 0`）では、アイテムロスト後と同じく横移動をしない（やり直しの移動が横移動と重なって柵を越えられないため。速度の受信は今どおり）。
- **速さの受信**: まずツールに1つの OSCQuery のサービス（mDNS で広告）で待つ。`OSCQUERY_CONNECT_WAIT_SEC` たってもその窓から届かなければ、固定ポート（VRChat の送信ポート）を開く。固定ポートは先に開いたアプリだけが受け取れるので、ほかのアプリ（ToN Utils など）と取り合わないように OSCQuery を先にする。mDNS の広告は 127.0.0.1 だけで開く（`OSCQUERY_MDNS_LOOPBACK_ONLY`。0.0.0.0 で開くとビルドのたび＝onefile の展開先が変わるたびに Windows のファイアウォールが確認を出すため。同じ PC の VRChat が見つけられない PC があれば False に戻すと全インターフェースになる）（GB）。OSCQuery のサービスは、受け取る窓（受信器）が無いときは届いたものを数えず・対応表（netstat）も取り直さずに捨てて（0 になるたびに 1 行だけログ）、窓の分からない送り元のログは最初の 1 件と `UNKNOWN_LOG_SEC`（60 秒）に 1 行にまとめ、先頭が速さ・接地のデータだけを解析する（FS）。
- **アイテム**: `item_id`（ロストの判定）と `held_item_id`（所持）を別に持つ。8 Pages でページを取ったとき、持ち込めないアイテム（item.json の 0）をなくす。アイテム自動取得は Begin が通った後、最後にロストしたアイテムを店で装備する（OSC・自動 Begin の窓だけ。完全放置モードでは Guidance Plush だけ（`RoundDecision.guidance_plush_id`）。合計 `ITEM_FETCH_LIMIT_SEC` まで）。Guidance Plush は、鍵を取った時点で Connecting から `ITEM_FETCH_FRONT_LATEST_SEC` を超えていればその回は見送る（"late"。やり直しの回数は減らさない。FR）。Guidance Plush の一覧では、Have Plush の行の点が HUD に隠れる位置（照準から横 −0.080 より左・縦が HUD の範囲）なら、視点を横にずらして行を右へ寄せてから押す（`PLUSH_ROW_HUD_X_MAX`・`PLUSH_ROW_SAFE_X`。ふつうの取得は見ない）。緑の行は、上が赤（Paradise Lost）・下が黄（Joyous Orb）の並びのときだけ Have Plush と認める（Jailbird など別の行を押さない。`row_order_ok`）。取得で動かした縦の視点は、どの終わり方でも戻す。前面を取られて戻せなかった分は、裏で見張って、この窓が前面になったら・ほかの窓が誰もフリーズしていなければ前面を借りて戻す（`_restore_view_soon`）。
  - 押す文字（店のボタン・Equip）が照準の左下の HUD に掛かるなら、店の画面の真ん中を照準へ寄せてから押す。重なりは「横は押す点・縦は文字の箱（`GREEN_WINDOW` の縦 × 倍率）の上下」で見る（`ItemFetch.button_box`）。範囲 `HUD_X`・`HUD_Y` は照準からの位置を高さで割った割合。
- **アイテムを落とす**（「アイテムを落とす」）: 死んだ・生き残った行が来たとき、ラウンドごとに 1 回 `/input/DropRight` を押して離す（続行ラウンドも同じ。RoundOver・続行の終わりでは落とさない。死んだ・生き残った行が来ない Run のリスポーンでは落とさない。落としたことはログの出来事にならないので、アイテムロストと数えない）。
- **続行ラウンドの後に窓を戻す**（「窓位置リセット」）: 続行フリーズを張ったときに窓の矩形を覚え（最大化なら通常の矩形。全画面・最小化は覚えない）、解いたら位置を戻す（全画面は前面を借りて Alt+Enter で窓に、最大化は前面を取らずに戻す）。設定で切れる。
- **チェイス**: キー（既定 F1 時計回り・F2 反時計回り）で前面の窓が回り続ける（OSC の移動＋視点）。同じキーでもう一度押すと止まり、反対のキーで向きを変える。設定で使わないようにできる（キーを拾わない）。private の窓だけ。
- **Run のリスポーン**（「Runリスポーン」）: Run のラウンドに入ったら、ほかの窓のフリーズが無いときに前面を借りて Esc のメニューからリスポーンし（`RespawnButton`。鍵と前面を持つのは「メニューを開く → ボタンを探す → 押す」の間だけで、「Player respawned」の行は前面を返してから鍵の外で待つ。来なければ借り直して 1 回押し直す）、背面の OSC で後ろへ 2.85 秒（動き出しから数える。上限 `RUN_EXTEND_MAX_SEC` 1.0 秒・押し直しは Begin と同じ）→ 左へ 0.9 秒 → 撮影で目印（`RunFace`）を探して向きを直す（`RUN_FACE_TRIES` まで。ずれが大きくなったら戻して止める）。**前後の位置は直さない**（`RUN_DEPTH_ENABLED = False`。目印の倍率は debug.log に書くだけ。理由は §13）。全続行（ラウンド指定 `continue_rounds`）で Run を選んだ private 系の窓は、Run でリスポーンしない（`LogMonitor._on_round_start` が判定より前に決める。ログは「Round: Run 【全続行（リスポーンしない）】」・`all_continue_round = True`。音量は Run のまま「その他」（`WindowVolume._live_category` が Run を先に返す）。アナウンス・他窓フリーズ・録画は無し。Run は続行リストの対象外）。「Runリスポーン」が OFF の窓は全続行に関係なくリスポーンしない。
- **ToN Utils の判断に従う**（「Utilsの判断に従う」）: Utils が判断している窓では、このツールは自爆の判断をせず、Utils の press（自爆する）・hold（その窓では自爆しない → このツールが判定）・何も無い（続行）に従う。起点は VRChat のログの killers_set・killers_revealed の行の時刻。数えるのは Utils の行の時刻が 起点 − `UTILS_EARLY_SEC` 以後のもの（理由は §13）。待つ上限は `UTILS_WAIT_SEC`。Utils が動いていない・窓が対応づかないときは、このツールが判定する（理由を画面に1回）。
- **VRChat の起動**: 窓ごとに `--profile=N`・`--osc=<受信>:127.0.0.1:<送信>`（窓 i は 9000+10i と +1）。窓が出てから `LAUNCH_STAGGER_SEC` 置いて次を起動する（VRChat API の 429 を避けるため）。
- **入室操作**（`ToNEntry`）: 入室後の選択画面を突破する。設定で、Begin の前に Save/Load のコード欄へクリップボードのセーブコードを貼る（`SaveLoad`。貼った後 1 秒置いて Enter、入力欄が開いたままなら Enter を押し直す）。Begin の受理の後に Guidance Plush を取りに行く（窓ごとのスレッド）。「入室後の選択画面を自動突破」と「自動でBeginを押す」が両方 ON でマクロが止まっていれば、ログを割り当てた後・入室の前にマクロを自動で開始する（Guidance Plush の取得にマクロの速さの受信器が要るため。開始したばかりの監視は入室の Begin の Verified を受理と扱わない）。**セーブコードは画面のログ・debug.log・撮影・テストの出力のどこにも出さない（長さも出さない）。**
- **窓の整列**（「窓を整列」「配置を覚える」「✕」）: 掴んでいる VRChat の窓を作業領域に升で並べる（`WindowArrange`。全画面・最小化の窓も戻してから並べる）。覚えた配置（`window_layout`）があればその窓はそこへ。
- **不具合の報告**: 本文と zip（画面のログ・debug.log の末尾・選んだ窓の VRChat のログの末尾・設定）を Discord の Webhook へ送る。窓は1つ・複数・全部を選べる（VRChat のログは全部の窓で同じ長さずつ削る）。撮影（Begin・Run）はチェックがあるときだけ添付する（ほかのプレイヤーの名前が写りうるため）。
- **自動アップデート**: 起動時に GitHub Releases の最新タグと `config.APP_VERSION` を比べ、新しければダウンロードして実行中の exe を `.old` にして差し替える。

## 10. 統計

- 送る: ラウンドごとに1回、`rpc/register_round`（開始の時刻・ラウンドの番号・マップ・テラー3体まで・transformed_uid・インスタンスの番号）。インスタンスの番号はインスタンスIDの SHA-256 の先頭8バイトで、DB 側で同じインスタンスの同じラウンドを1行にまとめる（ソロなら送らない）。
- transformed_uid: `rpc/get_transformed_uid` で VRChat uid から int2 の番号をもらう（無ければ DB が割り当てる）。`Users` の表は直接読まない（`supabase/get_transformed_uid.sql`）。
- 統計画面: `RoundStore`（%APPDATA%\ToNAutoBeginner\rounds.sqlite）に DB の差分だけを取り込み、手元で集計する。自分が送った行は、DB が受け取ってから手元にも書く（送れなかった行は書かない）。

## 11. 置き場所

| ファイル | 中身 |
|---|---|
| `%APPDATA%\ToNAutoBeginner\settings.json` | 設定（OBS のパスワードは DPAPI で暗号化）。新しめの名前: `window_layout`・`follow_utils`・`ton_entry`・`ton_entry_plush`・`ton_begin`・`chase_enabled`・`chase_cw_key`・`chase_ccw_key`・`start_key`・`suicide_cancel_key`・`continue_drop_item`・`continue_restore_window`・`run_respawn` |
| `%APPDATA%\ToNAutoBeginner\debug.log`（.1〜.3） | デバッグログ。20MB で世代を回す |
| `%APPDATA%\ToNAutoBeginner\rounds.sqlite` | 統計画面の手元の保存 |
| `%APPDATA%\ToNAutoBeginner\fog_object_names.json` | 看破の名前ごとの答え合わせ |
| `%APPDATA%\ToNAutoBeginner\begin_miss\` | BEGIN が見つからなかった撮影（窓ごとに2枚） |
| `%APPDATA%\ToNAutoBeginner\begin_adjust\` | Begin の位置合わせの各段の撮影（1 回ごとに「時刻_窓N」の組。見つかった段は四角と照準を描く。1/2 の JPEG。新しい 10 組。不具合の報告には撮影のチェックがあるときだけ） |
| `%APPDATA%\ToNAutoBeginner\run_face\` | Run の向き・前後でうまくいかなかったときの撮影（1/2 の JPEG。最新 `SHOT_KEEP` 枚） |
| `%APPDATA%\ToNAutoBeginner\run_pos\` | Run の位置合わせのためのデータ（等倍の PNG。基準は窓ごとに 3 枚（暗い画面は基準にしない）・Run の後は 30 枚。不具合の報告には入れない） |
| `%APPDATA%\ToNAutoBeginner\fetch_miss\` | アイテム取得で見つからなかった・Guidance Plush の装備を確かめられなかったときの撮影（1/2 の JPEG。最新 20 枚。不具合の報告には撮影のチェックがあるときだけ） |
| `%APPDATA%\ToN ListTool\host_state.sqlite3` | ToN ListTool の主催リスト（読むだけ） |
| `%APPDATA%\ton-syncskip\ton-utils.log`・`save-history.sqlite` | ToN Utils のログと記録（読むだけ。sqlite は `file:…?mode=ro` で開き、`logs` の source・started だけを読む。写しも取らない） |
| リポジトリの `assets/` | exe に同梱するデータ（exe の中も同じ形）。`assets/data/terrors.json`・`maps.json`・`item.json`、`assets/voice/*.mp3`、`assets/templates/begin/`・`shop/`・`respawn/`・`run/`・`saveload/`（絵の見本）、`assets/ToNAutoBeginnerIcon.ico` |
| `maps.json` の各マップ | `id`・`name` と印 `normal`（通常ラウンドで出る）・`8pages`（8 Pages で選ばれる）・`run`（Run のマップ）。1 か 0。同じ番号は 1 だけ（Dring King's Citadel と Sewers）で、統計の名前はラウンドに合う印で選ぶ（`Statistics.map_name_for_id`） |
| `src/tests/data/` | テストの資料（撮影の切り出しなど。exe には入れない） |

## 12. ビルド・テスト

- ビルド: リポジトリの直下で `python build.py`（Nuitka の onefile）。引数は `build.BASE_ARGS`（`src/main.py` の先頭の参考の行と同じ。テストで確かめている）。同梱データは `assets/…` を同じ置き場所で入れる（`.env`・`assets/ToNAutoBeginnerIcon.ico`・`assets/data/*.json`・`assets/voice`・`assets/templates`）。`win32clipboard`（セーブコード）・`zeroconf`/`ifaddr`（OSCQuery）・`cv2` を含める。zeroconf は `--recompile-extension-modules` で全部ソースから組む（Cython 製の .pyd と混ざると mDNS の広告が `KeyError: '__pyx_vtable__'` で失敗する）。展開先は `%LOCALAPPDATA%\ToNAutoBeginner\<版>-<ビルドの印>`。
  exe ができたら続けてインストーラー `dist/ToNAutoBeginner-Setup.exe` を作る（Inno Setup 7 か 6 の ISCC。`installer/ToNAutoBeginner.iss`。見つからなければ exe だけ）。
- インストーラー: ユーザー単位（管理者権限なし）で `%LOCALAPPDATA%\Programs\ToNAutoBeginner` に入れる。自動更新は exe の横で差し替えるので、書き込める場所に入れる。
  起動中かは `config.APP_MUTEX_NAME` のミューテックス（`main.hold_running_mutex`。.iss の AppMutex と同じ名前）で見る。
  アップデート（自動更新・Setup の上書き）ではアンインストールは走らずデータは残る。アンインストールはインストール先・`%APPDATA%\ToNAutoBeginner`・`%LOCALAPPDATA%\ToNAutoBeginner` を全部消す。
  `AppId` は変えない（変えると別アプリ扱いになる）。
- 新しく入れる人はインストール先を選ぶ（`DisableDirPage=auto`。上書きでは聞かない）。Program Files など管理者権限が要る場所は選べない（.iss の `NextButtonClick`）。
- exe 単体からの移行（`Migration.py`）: exe 単体（Inno Setup のアンインストール情報 `InstallLocation` の外）で起動したら、最新リリースの `ToNAutoBeginner-Setup.exe` を落とし、元の exe の場所を settings.json（`migrated_from`）に書いて、PowerShell の係を裏で起こしてツールを終える。係は起動中の目印が消えるのを待ち（最大60秒）、Setup を `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /launch=1` で動かす。入れる場所は元の exe のフォルダ（`/DIR="…"`。まだインストールしていない人だけ）。Program Files などの管理者権限が要る場所（Setup の NextButtonClick と同じ判定）・書き込めない場所・ドライブの直下なら `/DIR` を付けず Setup の既定へ（`Migration.target_dir`）。Setup は終わるとツールを起動する。Setup が成功したら（終了コード 0）係が元の exe（と `.old`）を消す。同じフォルダに同じ名前（`ToNAutoBeginner.exe`）で入れたときは Setup が上書きしたので、exe は消さず `.old` だけ消す。消せなかったときはインストール版が最初の起動で消す。失敗したらその回は今のまま動き、次の起動でやり直す。移行するときは更新の確認はしない。
- リリース: `ToNAutoBeginner.exe`（自動更新が探すのはこの名前）と `ToNAutoBeginner-Setup.exe`（移行が探す）の両方を、`APP_VERSION` と同じタグのリリースに置く。
- テスト: `src` で `python UnitTest.py`（全部）／`python UnitTest.py test_freezes`（1ファイル）。本体は `src/tests/test_*.py`、共通の準備は `src/tests/support.py`（Windows 専用モジュールの差し替え・本物の OSC / 設定 / DB / クリップボードに触らない安全装置）。テストは本物の OSC・キー・マウス・カーソル・窓・ポート、Utils のログ・sqlite を使わない。
  `src/tests/input_guard.py`（support がいちばん最初に入れる）が、本物の入力に届いたら本物は呼ばずにそのテストを失敗にする: OSC（127.0.0.1:9000〜9100）・SendInput・keybd_event・mouse_event・SetCursorPos・ClipCursor・BlockInput・クリップボード・MCI／PlaySound・AttachThreadInput・ほかのプロセスの窓へのメッセージと前面化（ctypes のどの WinDLL からでも・pywin32 も・別のスレッドからも。テストの外で届いた分はモジュールの終わりで失敗）。外すのは `@allow_real_input("理由")` を付けたテストだけ（今は無い）。
  全部通る状態を保つ（落ちたままのテストを「既知」として残さない。退行に気づけなくなる）。

## 13. 理由がほかに残っていない決めごと

- **Run の前後を目印の大きさで直さない**（2026-10-12）: 目印（階段の上の構造）が遠く、数 m の違いが大きさにほとんど出ず、測りのぶれ（一致 0.8〜0.9）に埋もれる。13:53 の窓1 は正しい位置で倍率 1.12 と出て、後ろへ直して数 m ずれた。円が数 m 先に見える撮影でも 0.98 だった。コードと `RUN_DEPTH_*` はスイッチで止めて残してある。
- **Utils の press・hold を Utils の行の時刻で数える**: このツールが行を読んだ時刻で比べると、読むのが遅れたとき、起点を処理した時にはもう press がファイルにあり、それを「前の行」として捨てて自爆を続行と取り違えた。
- **HUD は 16:9 の全画面で大きい**: VRChat の画面に貼り付く UI は窓の縦横比で大きさの決まり方が変わる。小さい窓（851×663）だけで測った範囲では、全画面で上と右にはみ出した。入力欄の矢印（`SaveLoad`）が min(幅/1280, 高さ/720) で決まるのも同じ理由。
- **HUD の重なりの横は押す点で見る**: 横も文字の箱で見ると、店の画面を真ん中へ寄せた後も Equip の箱の左の端（照準から −0.037）が HUD に入り、押せなくなる。
- **押し直しの後の延びの上限に、失った時間を足す**: 上限を最初の押し始めから数えると、押し直しの後に動き出したとき、左（0.15 秒）や短い押しが動き出す前に離されてしまう。
- **OSCQuery のサービスを出しっぱなしにする**: 監視の開始・停止のたびに名乗りを出し入れすると、VRChat が見つけなくなった疑いがあった。
