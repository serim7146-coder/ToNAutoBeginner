"""デバッグログ（公開ログとは別のファイル）。

%APPDATA%\\ToNAutoBeginner\\debug.log へ時刻つきで1行ずつ追記する。ツールの公開ログ
（GUI のログ欄・その保存）には一切出さない。我々がデバッグで使うためのもの。

守ること（依頼者の方針）:
- 霧の先読み（看破）が NG のインスタンスで、判明前のテラーの情報を書かない
- DB への送信のログ（送った内容）を書かない

どのスレッドから呼んでもよい。書けなくても例外を外へ出さない（ツールの動きを止めない）。
DEBUG_LOG_MAX_BYTES を超えたら debug.log を debug.log.1 に置き換えて書き始める
（前の .1 は消す）。
"""
import os
import threading
from datetime import datetime

import config

_lock = threading.Lock()


def write(message: str):
    """1行追記する。行頭は [YYYY-MM-DD HH:MM:SS.mmm]（ローカル時刻）"""
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    line = f"[{stamp}] {message}\n"
    try:
        with _lock:
            path = config.DEBUG_LOG_PATH
            path.parent.mkdir(parents=True, exist_ok=True)
            _rotate_if_full(path)
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
    except Exception:
        pass                        # 失敗は黙って捨てる


def _rotate_if_full(path):
    try:
        size = path.stat().st_size
    except OSError:
        return
    if size < config.DEBUG_LOG_MAX_BYTES:
        return
    old = path.with_name(path.name + ".1")
    os.replace(path, old)           # 前の .1 は置き換わって消える
