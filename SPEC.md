# ToNAutoBeginner 技術仕様書

対象: `src/` 以下の実装（開発版、2026-08-19時点）。
機能面のユーザー向け説明は `README.md` を参照。本書は開発者向けに、アーキテクチャ・モジュール責務・データフロー・状態管理を記述する。関数シグネチャの網羅は目的としない。

## 1. 全体像

VRChatワールド「Terrors of Nowhere (ToN)」向けのデスクトップ支援ツール（Windows専用、Tkinter GUI）。
VRChatのプレイヤーログファイルをリアルタイムで tail し、ラウンド開始・テラー判定・ラウンド終了などのイベントを検出して、ウィンドウ操作（自爆キー押下・Begin自動操作・AFK防止）や音声アナウンスを自動実行する。複数のVRChatウィンドウ（マルチアカウント/マルチクライアント運用）を同時監視できる。

```
main.py ──▶ mainGUI.App (Tkinter)
              ├─ 窓ごとに LogMonitor を起動（1窓=1スレッド）
              │     ├─ LogParser        : ログ1行 → LogEvent
              │     ├─ RoundDecision    : テラーID正規化・続行/AFK解除判定
              │     ├─ MatchTNL         : tnlデータとの突合・Alternate/Unboundオフセット
              │     ├─ ActionExecutor   : 自爆・Begin自動操作・AFK防止（実操作）
              │     │     └─ WindowOperator : キー/クリックのOS操作
              │     ├─ PlaySound        : 音声アナウンス再生
              │     └─ ConnectDB        : Supabaseへのラウンド統計送信
              ├─ SharedState : 窓横断の排他制御（グローバルロック・フリーズイベント）
              ├─ VRChatDiscovery / VRChatLauncher : VRChatプロセス検出・起動
              ├─ AutoUpdate  : GitHub Releasesからの自己アップデート
              └─ StatisticsGUI → Statistics : 収集した統計の表示・検定
```

## 2. モジュール一覧と責務

| モジュール | 責務 |
|---|---|
| `main.py` | エントリポイント。`mainGUI.App` を生成し `mainloop()` を回すだけ。 |
| `mainGUI.py` | Tkinter GUIアプリ本体。窓タブ管理、設定の永続化、監視の開始/停止、VRChat起動UI、自動アップデートUI、緊急停止(Pキー)ポーリング。 |
| `config.py` | 全定数（パス、GUI配色、待機秒数、インスタンス種別、特殊ラウンド集合、テラーID等）を集約。`resource_path()` で開発実行/Nuitkaコンパイル後の両方に対応したリソース解決を行う。 |
| `State.py` | `WindowConfig`（窓ごとのユーザー設定）と `WindowState`（窓ごとの実行時状態）の2つの`dataclass`を定義。ロジックは持たない。 |
| `SharedState.py` | 複数窓スレッド間で共有するグローバル状態：操作の排他ロック(`_GLOBAL_ACTION_LOCK`)、現在のインスタンスタイプ、自爆キー、放置モード/アイテム取得→Beginモードのフラグ、装備待ち・続行ラウンド中の「全窓フリーズ」を表す`threading.Event`とカウンタ。 |
| `LogMonitor.py` | 窓1つを1スレッドで監視するコア。ログファイルをポーリング（`LOG_POLL_INTERVAL`秒間隔）で読み進め、`LogParser`でイベント化し、`WindowState`を更新しながら自爆/Begin/統計送信/音声再生をトリガーする。ロジック（判定）を担当し、実操作は`ActionExecutor`に委譲する。 |
| `LogParser.py` | VRChatログの1行を正規表現で`LogEvent`（種別+付帯情報）に変換するステートレスな関数群。 |
| `ActionExecutor.py` | 実際のウィンドウ操作：自爆キー長押し(`do_skip`)、Begin前移動+クリック+リトライ(`do_after_round`)、DTM/Waldo中のAFK防止ループ(`do_open_special_round_loop`)。`SharedState`のロック/イベントを使って他窓と協調する。 |
| `WindowOperator.py` | 最下層のOS操作（`win32gui`でフォーカス、`keyboard`でキー押下、`pydirectinput`でクリック）。 |
| `MatchTNL.py` | `.tnl`（続行リスト）ファイルの読み込みと、ログ上のテラーID→tnlスロットIDへの変換（Alternate枠+134オフセット、Unbound+200オフセット）。 |
| `RoundDecision.py` | テラーID正規化（`MatchTNL`呼び出し）と、「続行すべきか」「DTM/Waldo3クラ解放対象か」の判定ロジック。 |
| `ReadJson.py` | `terrors.json`のロードとテラーID↔名前の相互変換。 |
| `VRChatDiscovery.py` | `win32gui`でVRChatウィンドウを列挙。プロセス起動時刻とログファイル名の時刻を突き合わせて、Zオーダーに依存しない窓↔ログの対応付けを行う。 |
| `VRChatLauncher.py` | Steamライブラリ検出によるVRChat起動exe(`launch.exe`)の自動解決、`--profile=N`指定でのプロセス起動、参加リンク(`vrchat://launch?...`)の正規化とログからの取得、起動後のウィンドウ出現待ち。 |
| `AutoUpdate.py` | GitHub Releases APIから最新版を取得しバージョン比較。新しければEXEをダウンロードし、実行中EXEを`.old`にリネーム→新EXEを配置→再起動。開発実行（未コンパイル）時は無効化される。 |
| `PlaySound.py` | PowerShell経由でMediaPlayerを起動し音声ファイルを再生（音量調整可）。 |
| `ConnectDB.py` | Supabase REST APIとの通信。VRChat UIDを匿名の`transformed_uid`(int2)に変換して登録、ラウンド統計(`ToNRoundStatistics`)の送信・取得。`.env`の`SUPABASE_URL`/`SUPABASE_KEY`が無ければ全機能が無効化される（オフライン動作可）。 |
| `Statistics.py` | 収集済み統計データの集計・二項分布による出現率の仮説検定（p値算出、有意水準ラベル付け）、マップ名解決。GUI非依存の純粋ロジック。 |
| `StatisticsGUI.py` | 統計データの可視化用Tkinterウィンドウ（円グラフ的な内訳、ラウンド別集計、フィルタUI）。`Statistics.py`と`ConnectDB.py`を呼び出す。 |
| `UnitTest.py` | 上記モジュール群の単体テスト（`unittest`、2000行超）。 |

## 3. 状態管理

### 3.1 `WindowConfig`（`State.py`, 窓ごとのユーザー設定・不変に近い）
GUIの窓タブで設定される値：`hwnd`, `log_path`, 各種ON/OFFスイッチ（`auto_begin`, `do_skip`, `cancel_afk`, `hoshiimo_skip`, `announce_intermission`）、音声ファイルパス群。`LogMonitor`/`ActionExecutor`に読み取り専用に近い形で渡される。

### 3.2 `WindowState`（`State.py`, 窓ごとの実行時状態）
`LogMonitor`が1窓につき1つ生成し、ログイベントに応じて更新する可変状態。主なフィールド：
- ラウンド進行: `in_round`, `round_type`, `terror_ids`, `map_id`, `round_seq`
- インスタンス: `instance_type`
- アイテム: `item_id`, `item_id_at_round_start`, `waiting_for_equip`, `item_lost_this_round`, `randomizer_item_changed`
- 続行/フリーズ: `is_continue_round`, `equip_freeze_held`
- DTM/Waldo(3クラ解放): `is_open_special_round_round`, `open_special_round_wins`
- テラーバリアント: `bloodthirsty_creature_variant`, `hungry_home_invader_variant`
- その他: `begin_done`, `died_this_round`, `lived_this_round`, `sabotage_murder_this_round`, `pending_sabotage_murder`

### 3.3 `SharedState`（全窓共有・プロセスグローバル）
- `_GLOBAL_ACTION_LOCK`: キー入力・マウス操作前に必ず取得するロック。1度に1窓しかOS操作しない。
- `EQUIP_WAIT_EVENT` / `_EQUIP_FREEZE_COUNT`: いずれかの窓がアイテムロスト装備待ちの間、全窓の自爆/Beginをフリーズする。カウンタ方式（複数窓が同時にロストしても全解除まで維持）。
- `CONTINUE_ROUND_EVENT` / `_CONTINUE_ROUND_COUNT`: いずれかの窓が続行/霧ラウンド中の間、全窓をフリーズする（同上のカウンタ方式）。
- 放置モード(`_HANDS_FREE`)、アイテム取得→Beginモード(`_ITEM_BEGIN_MODE`)、自爆キー、現在のインスタンスタイプもここに保持（全窓共通のトグル）。

## 4. データフロー（1ラウンドのライフサイクル）

```
VRChatログファイル（追記）
   │  LogMonitor._run(): ポーリングで新規行を取得
   ▼
LogParser.parse(line) → LogEvent（種別: round_start / killers_set / killers_revealed /
                                   round_over / verified_end / you_died / item_equip 等）
   │
   ▼
LogMonitor._process(event): WindowState を更新し、種別ごとに分岐
   │
   ├─ EVENT_ROUND_START      : in_round=True, terror_ids初期化, ラウンド開始時アイテムロスト判定
   ├─ EVENT_KILLERS_SET/REVEALED → _on_killers():
   │     RoundDecision.normalize_killer_ids() でAlternate/Unboundオフセット適用
   │     → RoundDecision.decide_killers() で続行可否・DTM/Waldo3クラ対象かを判定
   │     → 続行なら PlaySound + SharedState.continue_round_start()（他窓フリーズ）
   │     → 続行しないかつ private/干し芋対象なら ActionExecutor.do_skip() を別スレッドで起動
   ├─ EVENT_ROUND_OVER       : in_round=False。アイテム未回収なら waiting_for_equip セット
   ├─ EVENT_VERIFIED_END     : 続行ラウンド終了処理、アイテムロスト通知、
   │                            auto_begin=True なら ActionExecutor.do_after_round() を起動
   └─ EVENT_ITEM_EQUIP       : item_id更新。装備完了+Begin完了が揃えば全窓フリーズを遅延解除
```

`ActionExecutor.do_skip()` / `do_after_round()` は実行前に `SharedState.EQUIP_WAIT_EVENT` と `CONTINUE_ROUND_EVENT` を待ち合わせ、他窓が続行中/装備待ち中であれば自窓の操作をブロックする。実際のOS操作（フォーカス・キー押下・クリック）は `_GLOBAL_ACTION_LOCK` を取得した区間でのみ行い、複数窓が同時に操作することはない。

統計送信（`ConnectDB.send_ToNRoundStatistics`）は `LogMonitor._send_round_statistics_once()` から、テラー確定（バリアント判定が必要な場合はそれを待って）1ラウンドにつき1回だけ非同期送信される。

## 5. マルチウィンドウ協調制御

- 各窓＝1 `LogMonitor`インスタンス＝1スレッド（ログ監視ループ）。アクション（自爆/Begin/AFK防止）はさらに`_start_daemon`でデーモンスレッドとして起動される。
- 排他制御は3種類：
  1. `_GLOBAL_ACTION_LOCK`（即時排他）: OS操作そのものを1窓ずつに直列化。
  2. `CONTINUE_ROUND_EVENT`（フリーズ）: いずれかの窓が続行/霧ラウンドをプレイ中は、他窓の自爆・Beginを待たせる。
  3. `EQUIP_WAIT_EVENT`（フリーズ）: いずれかの窓がアイテムロストで装備待ち中は、他窓の自爆・Beginを待たせる。
- 2と3はどちらも「保持カウンタが0になったら解除」方式で、複数窓が同時にフリーズ要因を持っても正しく解除される（`equip_freeze_start/end`, `continue_round_start/end`）。
- 停止時（`mainGUI.App._stop`）は`SharedState.equip_freeze_reset()`/`continue_round_reset()`で強制的に全解除する。

### 5.1 非同期タスクの寿命とキャンセル可否

`_start_daemon()` で起動されるタスクは、寿命が3種類に分かれる。
**この分類はコード上のどこにも明示されておらず、「キャンセル機構が存在しない」ことによって暗黙に成立している。**
停止・中断・キャンセルの仕組みを追加・変更する際は、必ず本表に従うこと。

| タスク | 起動元イベント | 寿命 | ラウンド開始時 |
|---|---|---|---|
| `ActionExecutor.do_skip` | ROUND_START ほか（`LogMonitor.py:733` 他計6箇所） | そのラウンド中 | 起動される側 |
| `ActionExecutor.do_open_special_round_loop` | ROUND_START（`LogMonitor.py:726`） | そのラウンド中 | 起動される側。ラウンド終了で自己停止 |
| `LogMonitor._delayed_group_skip` | ROUND_START（`LogMonitor.py:658`） | バリアント判定待ち（数秒） | 起動される側。`round_seq` で自己判定 |
| `ActionExecutor.do_after_round` | ROUND_OVER（`LogMonitor.py:529`） | ラウンド**間** | **畳んでよい** |
| `LogMonitor._release_equip_wait_after_delay` | BEGIN_DONE（`LogMonitor.py:415`） | **後始末** | **畳んではいけない** |
| `LogMonitor._release_continue_freeze_after_delay` | 死亡（`LogMonitor.py:493`） | **後始末** | **畳んではいけない** |

#### 後始末タスクを畳んではいけない理由

`_release_equip_wait_after_delay` は BEGIN_DONE で起動され、`EQUIP_RELEASE_DELAY_SEC` 待ってから `SharedState.equip_freeze_end()` を呼ぶ。
**BEGIN_DONE の直後にラウンドが開始するのが正常な流れ**（Begin成功 → Connecting → ラウンド開始）なので、「ラウンド開始で当該窓のタスクを畳む」を素朴に実装すると次が起きる：

- `_EQUIP_FREEZE_COUNT` が減らない → `EQUIP_WAIT_EVENT` が clear のまま → **全窓が永久にフリーズする**

ROUND_START 側にも解除処理があるが（`LogMonitor.py:449`）、これは `st.waiting_for_equip` が真の場合のみ動作する。
BEGIN_DONE の時点で既に `st.waiting_for_equip = False` にされている（`LogMonitor.py:414`）ため、**この経路では解除されない。この遅延タスクが唯一の解除者である。**

`_release_continue_freeze_after_delay` は `round_seq` を見てラウンドが変わっていれば自分から降りるが、**降りるときに `continue_round_end()` を呼ばない**。その場合の解除は ROUND_START 側（`LogMonitor.py:422`）が行う。解除責任がタイミングによって2箇所に分岐している点に注意。

#### 現状のキャンセル表現

上記の「畳む／畳まない」は、`ActionExecutor` 内の手書きチェックとして表現されている（401行中、計46箇所）：

- `st.in_round` … 19箇所（＝「ラウンドが始まったので Begin 作業を捨てる」）
- `self._is_running()` … 21箇所（＝「停止された」）
- `st.begin_done` … 6箇所（＝「Begin 成功済みなのでリトライ不要」）

後始末タスク2つには、この種のガードが**一切ない**。停止操作についてのみ、`mainGUI.App._stop`（`mainGUI.py:781`）が `equip_freeze_reset()` / `continue_round_reset()` を呼んで別途つじつまを合わせている。

## 6. ラウンド判定ロジック（`.tnl`連携）

1. GUIで`.tnl`ファイル（ToN ListToolが出力する続行リスト、JSON）を読み込み、`MatchTNL.load_tnl()`が `{ラウンドキー: {スキップ対象テラーIDの集合}}` の`keepOn_set`を作る。
2. ログから得たテラーIDは`RoundDecision.normalize_killer_ids()`でtnlのスロットID体系に正規化される：
   - Alternate枠（ログID 0〜35）は `MatchTNL.apply_alternate_offset()` で `+134`
   - Unboundラウンドは `+200`
3. `RoundDecision.decide_killers()` が `MatchTNL.should_continue()`（tnlに登録済みテラーか）と `is_open_special_round_target()`（DTM/Waldoで3勝未達か）を合成し、続行すべきか・自爆すべきかを決める。
4. インスタンスタイプ（`INSTANCE_PRIVATE`/`INSTANCE_HOSHIIMO`等、`LogMonitor._parse_instance_type()`がログのJoining行から判定）によって、自動操作を行うかどうかが変わる：
   - private系のみ通常の自爆/Begin/AFK防止を実行
   - 干し芋/焼き芋グループは`hoshiimo_skip`設定時のみ、`HOSHIIMO_SKIP_ROUNDS`に該当する場合だけ自爆（テラーバリアント確定を待ってから判断）
   - それ以外のグループは操作しない（判定ログのみ）

## 7. VRChat起動・自己アップデート

- **起動**: `VRChatLauncher.resolve_vrchat_exe()` がSteamの`libraryfolders.vdf`からライブラリを探索し`launch.exe`を検出（`VRChat.exe`直起動はオフラインモードになるため避ける）。窓ごとに`--profile=N`を付けて`subprocess.Popen`で起動し、`VRChatDiscovery`で新規ウィンドウの出現を待つ。参加リンクは`vrchat://launch?...`形式に正規化して渡す。
- **アップデート**: 起動時に`AutoUpdate.fetch_latest_release()`でGitHub Releasesの最新タグと`config.APP_VERSION`を比較。新しければユーザーに確認の上ダウンロードし、実行中EXEを`.old`にリネームしてから新EXEに置き換えて再起動する。開発実行時（Nuitkaコンパイル前）は`current_exe_path()`が`None`を返しこの機能全体が無効。

## 8. 統計機能

- `LogMonitor`がラウンドごとに`ConnectDB.send_ToNRoundStatistics()`でSupabaseへ`{round, terror_ids, map_id, transformed_uid}`を送信（`.env`未設定ならスキップ）。
- `StatisticsGUI.StatisticsWindow`（`mainGUI`の「統計」ボタンから開く）が`ConnectDB.get_ToNRoundStatistics()`で取得し、`Statistics.py`の`analyze_terrors()`で二項分布に基づくp値を算出、`SIGNIFICANCE_LEVELS`で「出やすい/かなり出やすい/テーブル！」ラベルを付与して表示する。
- VRChat UIDは`ConnectDB.send_Users()`で任意のint2 (`transformed_uid`) に変換されてから送信される（プライバシー・DB軽量化のため）。

## 9. ビルド・配布

`main.py`冒頭のコメントにNuitkaでのワンファイルexeビルドコマンドが記載されている。`--include-data-files`で`.env`/アイコン/`maps.json`/`terrors.json`/`voice/`を同梱する。`config.resource_path()`が開発実行・コンパイル後(`__compiled__`)の両方でこれらのファイルを解決する。

## 10. 未コミット差分について

このリポジトリのブランチ`fix`には、本書作成時点で以下の未コミット変更がある（本書はこの状態を含めて記述している）：
- 新規追加: `AutoUpdate.py`, `VRChatLauncher.py`（本書のセクション7で説明した機能）
- 変更: `ActionExecutor.py`, `LogMonitor.py`, `SharedState.py`, `State.py`, `UnitTest.py`, `VRChatDiscovery.py`, `config.py`, `mainGUI.py`
