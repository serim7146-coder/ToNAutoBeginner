"""
音声再生（Windows標準のMCI / winmm.dll をctypesで叩く）

PowerShell + MediaPlayer をプロセス起動する方式から置き換えた。旧方式は
`$mp.Play(); Start-Sleep -Seconds 2` のため2秒を超える音声が途中で切れ、
再生のたびに数十MBのPowerShellが立っていた。

同時再生を保つため、再生ごとに一意のエイリアスを割り当てる。MCIは
1エイリアス=1デバイスなので、固定エイリアスだと2回目の再生が1回目を
止めてしまう（複数窓が同じ音声を同時に鳴らすのは日常的に起きる）。

MCIは例外を投げず、戻り値(MCIERROR、非ゼロが失敗)でエラーを返す。
戻り値を見ないと無音の失敗が完全に不可視になるため、必ず確認する。

止める（EM）: 重いときに1回の再生が終わらず、声の切れ端が鳴り続けた（停止でも止まらず、閉じるまで
鳴っていた）。`stop_all()` で鳴っているものを全部止める（マクロの停止で呼ぶ。閉じるときも停止を通る）。
MCI のエイリアスは開いたスレッドに縛られる（2026-10-13 の実測: ほかのスレッドから stop・close を送ると
MCIERROR 263（開いていない）で、`play … wait` は最後まで止まらなかった）。なので `play` は wait なしで送り、
開いたスレッドが `status … mode` を見て待つ。`stop_all` はそのスレッドへ止める合図を出すだけで、stop・close は
そのスレッドが送る。
"""
import ctypes
import threading
from pathlib import Path

import config
import DebugLog

# Volume（0.0~1.0）
sound_volume: float = config.DEFAULT_SOUND_VOLUME
_SOUND_VOLUME_LOCK = threading.Lock()

# 再生ごとのエイリアス通し番号
_ALIAS_LOCK = threading.Lock()
_alias_seq = 0

# 拡張子 → MCIのデバイス種別。未知の拡張子は type 句を省いて自動判別に任せる
_MCI_TYPES = {
    ".mp3": "mpegvideo",
    ".wav": "waveaudio",
}

_MCI_BUF_CHARS = 256

# 鳴っているもの: エイリアス → 止める合図。開いたら足し、閉じたら外す（EM）
_ACTIVE_LOCK = threading.Lock()
_active: dict = {}
POLL_SEC = 0.05             # 鳴り終わったか・止める合図が来たかを見る間隔
_PLAYING_MODES = ("playing", "seeking", "not ready")


def get_sound_volume() -> float:
    with _SOUND_VOLUME_LOCK:
        return sound_volume

def set_sound_volume(volume: float):
    global sound_volume
    with _SOUND_VOLUME_LOCK:
        sound_volume = max(0.0, min(1.0, volume))


# 声の種類ごとの音量（EO）。鳴らす音量 ＝ 全体（sound_volume）× その声の音量。種類の名前は窓の設定の
# voice_<種類> と同じ。ファイルのパスでは引かない（同じファイルを 2 種類に使っても別々に効くように）
VOICE_KINDS = ("continue", "alternate", "midnight", "unbound", "fog", "ghost", "8pages", "punish",
               "item_lost", "intermission", "foxy", "list_lost",
               "murder", "sabotage", "cracked", "double_trouble", "afk")     # 後ろの 5 つは EZ（FB で EX を外した）
_voice_volumes: dict = {}
_voice_enabled: dict = {}       # 声ごとのオン・オフ（FG。覚えていなければオン）


def get_voice_volume(kind) -> float:
    """その声の音量（0.0〜1.0）。覚えていない・種類なしは 1.0"""
    with _SOUND_VOLUME_LOCK:
        return _voice_volumes.get(kind, 1.0)


def set_voice_volume(kind: str, volume: float):
    with _SOUND_VOLUME_LOCK:
        _voice_volumes[kind] = max(0.0, min(1.0, float(volume)))


def get_voice_enabled(kind) -> bool:
    """その声を鳴らすか（FG）。覚えていない・種類なしはオン"""
    with _SOUND_VOLUME_LOCK:
        return _voice_enabled.get(kind, True)


def set_voice_enabled(kind: str, enabled: bool):
    with _SOUND_VOLUME_LOCK:
        _voice_enabled[kind] = bool(enabled)


def voice_enabled_setting() -> dict:
    """settings.json に書く形（VOICE_KINDS すべて）"""
    return {kind: get_voice_enabled(kind) for kind in VOICE_KINDS}


def load_voice_enabled(value) -> dict:
    """settings.json の値を読む。無い・壊れた値（真偽でない）はオン。読んだ値を返す"""
    value = value if isinstance(value, dict) else {}
    got = {}
    for kind in VOICE_KINDS:
        v = value.get(kind)
        got[kind] = v if isinstance(v, bool) else True
        set_voice_enabled(kind, got[kind])
    return got


def voice_volumes_setting() -> dict:
    """settings.json に書く形（VOICE_KINDS すべて。小数 2 桁）"""
    return {kind: round(get_voice_volume(kind), 2) for kind in VOICE_KINDS}


def load_voice_volumes(value) -> dict:
    """settings.json の値を読む。無い・壊れた値・範囲の外は 1.0（100%）。読んだ値を返す"""
    value = value if isinstance(value, dict) else {}
    got = {}
    for kind in VOICE_KINDS:
        v = value.get(kind)
        ok = isinstance(v, (int, float)) and not isinstance(v, bool) and 0.0 <= v <= 1.0
        got[kind] = float(v) if ok else 1.0
        set_voice_volume(kind, got[kind])
    return got


def _next_alias() -> str:
    """再生ごとに別のエイリアスを返す（同時再生を潰さないため）"""
    global _alias_seq
    with _ALIAS_LOCK:
        _alias_seq += 1
        return f"ton_snd_{_alias_seq}"


def _mci(command: str) -> int:
    """MCIコマンドを送り、MCIERROR（0なら成功）を返す。

    日本語を含むパスが化けないよう必ずW（ワイド文字）版を使う。
    """
    buf = ctypes.create_unicode_buffer(_MCI_BUF_CHARS)
    return ctypes.windll.winmm.mciSendStringW(command, buf, _MCI_BUF_CHARS - 1, 0)


def _mci_mode(alias: str):
    """`status <alias> mode`（"playing"・"stopped" など。小文字）。聞けなければ None"""
    buf = ctypes.create_unicode_buffer(_MCI_BUF_CHARS)
    code = ctypes.windll.winmm.mciSendStringW(f"status {alias} mode", buf, _MCI_BUF_CHARS - 1, 0)
    return None if code else buf.value.strip().lower()


def stop_all() -> int:
    """鳴っているものを全部止める（それぞれの再生のスレッドへ合図を出す。stop・close はそのスレッドが送る）。
    止めた数を返し、1 つ以上なら debug.log に 1 行"""
    with _ACTIVE_LOCK:
        stops = list(_active.values())
    for stop in stops:
        stop.set()
    if stops:
        DebugLog.write(f"[音声] 鳴っている {len(stops)} 個を止めました")
    return len(stops)


def _mci_error_text(code: int) -> str:
    """MCIERROR を人が読めるメッセージにする"""
    try:
        buf = ctypes.create_unicode_buffer(_MCI_BUF_CHARS)
        if ctypes.windll.winmm.mciGetErrorStringW(code, buf, _MCI_BUF_CHARS):
            return buf.value
    except Exception:
        pass
    return f"MCIERROR {code}"


def _mci_run(command: str) -> bool:
    """MCIコマンドを実行し、失敗したらメッセージを表示してFalseを返す"""
    code = _mci(command)
    if code:
        _say(f"音声再生エラー: {_mci_error_text(code)}（{command}）")
        return False
    return True


# 一度出したら黙る警告（毎回の再生で出すとログが埋まるため）
_WARNED_LOCK = threading.Lock()
_warned: set = set()


def _warn_once(key: str, message: str):
    with _WARNED_LOCK:
        if key in _warned:
            return
        _warned.add(key)
    _say(message)


def _mci_volume_value(kind=None) -> int:
    """全体 × その声（種類なしは全体だけ）の 0.0〜1.0 を MCI の 0〜1000 に直す"""
    volume = get_sound_volume() * (get_voice_volume(kind) if kind else 1.0)
    return int(round(max(0.0, min(1.0, volume)) * 1000))


def _open_command(path: Path, alias: str, path_text: str = "") -> str:
    """open コマンド。パスは空白区切り対策で必ずダブルクォートで囲む"""
    device_type = _MCI_TYPES.get(path.suffix.lower())
    type_part = f" type {device_type}" if device_type else ""
    return f'open "{path_text or path.absolute()}"{type_part} alias {alias}'


def _short_path(path: Path) -> str:
    """8.3形式の短いパスを返す（取れなければ空文字）。

    MCIは概ね128文字以上のパスを開けず MCIERR_FILENAME(304) を返すため、
    深い場所にexeを置いた場合の逃げ道として使う。
    """
    try:
        buf = ctypes.create_unicode_buffer(1024)
        if ctypes.windll.kernel32.GetShortPathNameW(str(path.absolute()), buf, 1024):
            return buf.value
    except Exception:
        pass
    return ""


def _open_device(path: Path, alias: str) -> bool:
    """音声デバイスを開く。長いパスで失敗したら短縮パスで開き直す。"""
    command = _open_command(path, alias)
    code = _mci(command)
    if not code:
        return True
    short = _short_path(path)
    if short and short != str(path.absolute()):
        retry = _open_command(path, alias, path_text=short)
        if not _mci(retry):
            return True
        command = retry
    _say(f"音声再生エラー: {_mci_error_text(code)}（{command}）")
    return False


def _say(message: str):
    """コンソールへ出し、debug.log にも書く"""
    print(message)
    DebugLog.write(f"[音声] {message}")


def play_sound(path: str, kind: str | None = None, force: bool = False):
    """鳴らす。kind（VOICE_KINDS の 1 つ）を渡すと、その声の音量も掛ける（EO）。その声をオフにしていれば
    鳴らさない（FG）。force は画面の ▶（試し再生）: オフでも鳴らす"""
    if not path:
        return
    if kind and not force and not get_voice_enabled(kind):
        DebugLog.write(f"[音声] {kind} はオフ → 鳴らさない")
        return
    p = Path(path)
    if not p.exists():
        DebugLog.write(f"[音声] ファイルがありません: {p}")
        return
    DebugLog.write(f"[音声] 再生 {p.name}")

    def _play_sound():
        try:
            alias = _next_alias()
            if not _open_device(p, alias):
                return
            stop = threading.Event()
            with _ACTIVE_LOCK:
                _active[alias] = stop
            try:
                # waveaudio は setaudio 非対応。音量が効かないだけで再生はできるので、
                # 再生自体は続ける。毎回出すとうるさいので警告は拡張子ごとに1回。
                code = _mci(f"setaudio {alias} volume to {_mci_volume_value(kind)}")
                if code:
                    _warn_once(
                        f"volume{p.suffix.lower()}",
                        f"音声再生エラー: 音量を設定できません（{_mci_error_text(code)}）"
                        f" → {p.suffix} はシステム音量で再生します")
                # wait は付けない（付けるとほかのスレッドから止められない）。このスレッドが鳴り終わるか
                # 止める合図が来るまで待つ（このスレッドだけが止まる）
                if _mci_run(f"play {alias}"):
                    while True:
                        if stop.wait(POLL_SEC):
                            _mci(f"stop {alias}")
                            break
                        if _mci_mode(alias) not in _PLAYING_MODES:
                            break
            finally:
                with _ACTIVE_LOCK:
                    _active.pop(alias, None)
                # 閉じ忘れるとデバイスが解放されず、いずれ再生できなくなる
                _mci_run(f"close {alias}")
        except Exception as e:
            DebugLog.exception("PlaySound._play_sound")
            _say(f"音声再生エラー: {e}")
    threading.Thread(target=_play_sound, daemon=True).start()
