"""デバッグログ（公開ログとは別のファイル）。

%APPDATA%\\ToNAutoBeginner\\debug.log へ時刻つきで1行ずつ追記する。ツールの公開ログ
（GUI のログ欄・その保存）には一切出さない。我々がデバッグで使うためのもの。
行頭のタグで種類が分かる（[画面]・[事象]・[状態]・[操作]・[音声]・[例外]・[環境] など）。

守ること（依頼者の方針）:
- 霧の先読み（看破）が NG のインスタンスで、判明前のテラーの情報を書かない
  （[NetworkProcessing] の名前も書かない）
- DB への送信のログ（送った内容・結果）を書かない
- 秘密（OBS のパスワード・.env の値）を書かない。add_secret() で覚えた文字は
  どの行でも *** に置き換える
- Windows のユーザー名は伏せる（%USERPROFILE% のパスを全行で置き換える）

どのスレッドから呼んでもよい。書けなくても例外を外へ出さない（ツールの動きを止めない）。
DEBUG_LOG_MAX_BYTES を超えたら世代を回す（debug.log → .1 → .2 → .3。古い .3 は消す）。
"""
import os
import re
import sys
import threading
import time
import traceback
from datetime import datetime

import config

_lock = threading.Lock()
# 残す世代の数（debug.log.1 〜 .GENERATIONS）
GENERATIONS = 3
# 同じ (場所, 例外の型, メッセージ) を書くのはこの秒数に1回まで
EXCEPTION_THROTTLE_SEC = 60.0

_secrets: set = set()
_secrets_lock = threading.Lock()
_seen: dict = {}            # (where, 型, メッセージ) → [最後に書いた時刻, 省いた回数]
_seen_lock = threading.Lock()


def add_secret(value):
    """この文字を以後どの行にも書かない（*** に置き換える）。短すぎるものは覚えない"""
    text = str(value or "").strip()
    if len(text) < 4:
        return
    with _secrets_lock:
        _secrets.add(text)


def scrub(text: str) -> str:
    """秘密とユーザー名を伏せる"""
    text = str(text)
    with _secrets_lock:
        secrets = sorted(_secrets, key=len, reverse=True)
    for secret in secrets:
        if secret in text:
            text = text.replace(secret, "***")
    profile = os.environ.get("USERPROFILE", "")
    if profile:
        for form in {profile, profile.replace("\\", "/")}:
            text = re.sub(re.escape(form), "%USERPROFILE%", text, flags=re.IGNORECASE)
    return text


# 書いているファイルと、その大きさ（毎行 stat しないため。パスが変わったら測り直す）
_size_path = None
_size = 0


def write(message: str):
    """1行追記する（複数行のメッセージはそのまま）。行頭は [YYYY-MM-DD HH:MM:SS.mmm]。
    画面のスレッドからも呼ぶので、1行ごとの手間を小さくする（フォルダは開けなかった
    ときだけ作る・大きさは覚えておく）。ファイルは開きっぱなしにしない"""
    global _size_path, _size
    try:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        data = f"[{stamp}] {scrub(message)}\n".encode("utf-8")
        with _lock:
            path = config.DEBUG_LOG_PATH
            if path != _size_path:
                _size_path = path
                try:
                    _size = path.stat().st_size
                except OSError:
                    _size = 0
            if _rotate_if_full(path, _size):
                _size = 0
            try:
                f = open(path, "ab")
            except FileNotFoundError:
                path.parent.mkdir(parents=True, exist_ok=True)
                f = open(path, "ab")
            with f:
                f.write(data)
                _size = f.tell()
    except Exception:
        pass                        # 失敗は黙って捨てる


def _rotate_if_full(path, size: int) -> bool:
    """大きさが上限を超えていたら世代を回す（.1〜.GENERATIONS。古いものは消す）。回したら True"""
    if size < config.DEBUG_LOG_MAX_BYTES:
        return False
    # os.replace は行き先があれば置き換える（いちばん古い .GENERATIONS はここで消える）
    for n in range(GENERATIONS - 1, 0, -1):
        src = path.with_name(f"{path.name}.{n}")
        if src.exists():
            os.replace(src, path.with_name(f"{path.name}.{n + 1}"))
    if path.exists():
        os.replace(path, path.with_name(path.name + ".1"))
    return True


# ── 例外 ─────────────────────────────────────
def exception(where: str):
    """except の中で呼ぶ。今の例外をトレースバックごと書く"""
    etype, value, tb = sys.exc_info()
    if etype is not None:
        report(where, etype, value, tb)


def report(where: str, etype, value, tb):
    """例外をトレースバックごと書く。同じものは EXCEPTION_THROTTLE_SEC に1回まで"""
    try:
        key = (where, getattr(etype, "__name__", str(etype)), str(value))
        now = time.monotonic()
        with _seen_lock:
            entry = _seen.get(key)
            if entry is not None and now - entry[0] < EXCEPTION_THROTTLE_SEC:
                entry[1] += 1
                return
            skipped = entry[1] if entry is not None else 0
            if len(_seen) > 1000:
                _seen.clear()
            _seen[key] = [now, 0]
        text = "".join(traceback.format_exception(etype, value, tb)).rstrip()
        note = f"（同じものを {skipped} 回省略）" if skipped else ""
        write(f"[例外] {where}{note}\n{text}")
    except Exception:
        pass


def install_hooks(tk_root=None):
    """sys・threading・Tk の例外の口を差し替えて、ここにも書く（今の動きは残す）"""
    if getattr(sys.excepthook, "_debug_log", False) is not True:
        previous = sys.excepthook

        def sys_hook(etype, value, tb):
            report("sys.excepthook", etype, value, tb)
            previous(etype, value, tb)
        sys_hook._debug_log = True
        sys.excepthook = sys_hook
    if getattr(threading.excepthook, "_debug_log", False) is not True:
        previous_thread = threading.excepthook

        def thread_hook(args):
            name = getattr(args.thread, "name", "?")
            report(f"threading.excepthook（{name}）", args.exc_type, args.exc_value,
                   args.exc_traceback)
            previous_thread(args)
        thread_hook._debug_log = True
        threading.excepthook = thread_hook
    if (tk_root is not None
            and getattr(tk_root.report_callback_exception, "_debug_log", False) is not True):
        previous_tk = tk_root.report_callback_exception

        def tk_hook(etype, value, tb):
            report("Tk report_callback_exception", etype, value, tb)
            previous_tk(etype, value, tb)
        tk_hook._debug_log = True
        tk_root.report_callback_exception = tk_hook
