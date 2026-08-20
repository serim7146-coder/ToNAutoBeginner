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
"""
import ctypes
import threading
from pathlib import Path

import config

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


def get_sound_volume() -> float:
    with _SOUND_VOLUME_LOCK:
        return sound_volume

def set_sound_volume(volume: float):
    global sound_volume
    with _SOUND_VOLUME_LOCK:
        sound_volume = max(0.0, min(1.0, volume))


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
        print(f"音声再生エラー: {_mci_error_text(code)}（{command}）")
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
    print(message)


def _mci_volume_value() -> int:
    """0.0〜1.0 の音量を MCI の 0〜1000 に直す"""
    return int(round(max(0.0, min(1.0, get_sound_volume())) * 1000))


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
    print(f"音声再生エラー: {_mci_error_text(code)}（{command}）")
    return False


def play_sound(path: str):
    if not path:
        return
    p = Path(path)
    if not p.exists():
        return

    def _play_sound():
        try:
            alias = _next_alias()
            if not _open_device(p, alias):
                return
            try:
                # waveaudio は setaudio 非対応。音量が効かないだけで再生はできるので、
                # 再生自体は続ける。毎回出すとうるさいので警告は拡張子ごとに1回。
                code = _mci(f"setaudio {alias} volume to {_mci_volume_value()}")
                if code:
                    _warn_once(
                        f"volume{p.suffix.lower()}",
                        f"音声再生エラー: 音量を設定できません（{_mci_error_text(code)}）"
                        f" → {p.suffix} はシステム音量で再生します")
                # wait を付けて再生完了までブロックする（このスレッドだけが止まる）
                _mci_run(f"play {alias} wait")
            finally:
                # 閉じ忘れるとデバイスが解放されず、いずれ再生できなくなる
                _mci_run(f"close {alias}")
        except Exception as e:
            print(f"音声再生エラー: {e}")
    threading.Thread(target=_play_sound, daemon=True).start()
