"""
OSCでVRChatを操作するコマンドラインツール

窓ごとに割り当てたOSCポートへ送信するので、フォーカスを奪わずに
複数の窓を同時に動かせる。ただし送れるのは移動と視点だけで、
クリックはできない（VRChatにデスクトップ用のインタラクト入力が無いため）。

使い方:
    python OSCControl.py --list
        窓番号とポートの対応を表示

    python OSCControl.py -w 1 forward 2.1 left 0.11
        窓1で 前進2.1秒 → 左移動0.11秒

    python OSCControl.py -w 2 --script begin.txt
        窓2でスクリプトファイルを実行

    python OSCControl.py -w 1 --monitor 20
        窓1からの送信を20秒間監視（速度が見える）

スクリプトファイルの書き方（1行1コマンド、# 以降はコメント）:
    forward 2.1      # 前進
    left 0.11        # 左
    wait 0.5         # 待つ
    look 0.6 0.4     # 視点を右へ（値 0.6 を 0.4秒）
    stop             # 入力を全部戻す

Pythonから使う場合:
    import OSCControl
    c = OSCControl.controller(window=1)
    c.forward(2.1); c.left(0.11)
"""
import argparse
import socket
import struct
import sys
import time

import config
import OSCClient


# ── コマンド定義 ────────────────────────────────
# 名前: (OSCアドレス, 種別)  種別 'button'=押して離す / 'axis'=値と秒
COMMANDS = {
    "forward": ("/input/MoveForward", "button"),
    "back":    ("/input/MoveBackward", "button"),
    "left":    ("/input/MoveLeft", "button"),
    "right":   ("/input/MoveRight", "button"),
    "run":     ("/input/Run", "button"),
    "jump":    ("/input/Jump", "button"),
    "look":    ("/input/LookHorizontal", "axis"),
    "lookv":   ("/input/LookVertical", "axis"),
    "turnl":   ("/input/LookLeft", "button"),
    "turnr":   ("/input/LookRight", "button"),
}


class Controller:
    """1つの窓を操作する"""

    def __init__(self, port: int, log=print):
        self.client = OSCClient.OSCClient(port)
        self.port = port
        self._log = log or (lambda _m: None)

    # 個別コマンド
    def forward(self, sec): return self.run_command("forward", sec)
    def back(self, sec):    return self.run_command("back", sec)
    def left(self, sec):    return self.run_command("left", sec)
    def right(self, sec):   return self.run_command("right", sec)
    def jump(self, sec=0.1): return self.run_command("jump", sec)
    def look(self, value, sec): return self.run_command("look", sec, value)
    def lookv(self, value, sec): return self.run_command("lookv", sec, value)

    def wait(self, sec):
        self._log("  wait  %.2f秒" % sec)
        time.sleep(max(0.0, sec))
        return True

    def stop(self):
        self.client.stop_all()
        self._log("  stop  全入力をリセット")
        return True

    def run_command(self, name: str, sec: float, value: float = 1.0) -> bool:
        if name == "wait":
            return self.wait(sec)
        if name == "stop":
            return self.stop()
        if name not in COMMANDS:
            self._log("  不明なコマンド: %s" % name)
            return False
        address, kind = COMMANDS[name]
        if kind == "axis":
            self._log("  %-7s 値%.2f を %.2f秒 (%s)" % (name, value, sec, address))
            return self.client.look(value, sec) if name == "look" else \
                self.client.look_vertical(value, sec)
        self._log("  %-7s %.2f秒 (%s)" % (name, sec, address))
        return self.client.press(address, sec)

    def run_script(self, lines) -> bool:
        ok = True
        for raw in lines:
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            parts = line.split()
            name = parts[0].lower()
            try:
                if name == "stop":
                    ok = self.stop() and ok
                elif name == "wait":
                    ok = self.wait(float(parts[1])) and ok
                elif name in ("look", "lookv"):
                    ok = self.run_command(name, float(parts[2]), float(parts[1])) and ok
                else:
                    sec = float(parts[1]) if len(parts) > 1 else 0.1
                    ok = self.run_command(name, sec) and ok
            except (IndexError, ValueError):
                self._log("  書式エラー: %s" % raw.strip())
                ok = False
        return ok

    def close(self):
        self.client.close()


def controller(window: int = 1, port: int = None, log=print) -> Controller:
    """窓番号(1始まり)またはポート指定でControllerを作る"""
    if port is None:
        port, _out = OSCClient.ports_for_window(max(0, window - 1))
    return Controller(port, log=log)


# ── 受信監視 ────────────────────────────────────

def monitor(port: int, seconds: float):
    """VRChatからの送信を監視する（移動しているかの確認に使う）"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("127.0.0.1", port))
    except OSError as e:
        print("ポート%dを開けません: %s" % (port, e))
        return
    sock.settimeout(0.5)
    print("ポート%d を %.0f秒間監視します" % (port, seconds))
    t0 = time.time()
    seen = {}
    while time.time() - t0 < seconds:
        try:
            data, _ = sock.recvfrom(4096)
        except socket.timeout:
            continue
        try:
            end = data.index(b"\x00")
            address = data[:end].decode("utf-8", "replace")
            pos = (end + 4) & ~3
            tend = data.index(b"\x00", pos)
            tags = data[pos + 1:tend].decode("ascii", "replace")
            pos = (tend + 4) & ~3
            value = None
            if tags[:1] == "f":
                value = round(struct.unpack_from(">f", data, pos)[0], 3)
            elif tags[:1] == "i":
                value = struct.unpack_from(">i", data, pos)[0]
            elif tags[:1] in ("T", "F"):
                value = tags[0] == "T"
        except Exception:
            continue
        if seen.get(address) != value:
            print("  +%5.1fs %-46s %s" % (time.time() - t0, address, value))
            seen[address] = value
    sock.close()
    print("監視終了（%d種類のアドレスを受信）" % len(seen))


# ── 緊急解除 ────────────────────────────────────

# OSCClient側の定義を使う（二重管理を避ける）
ALL_INPUTS = list(OSCClient.OSCClient.BUTTON_INPUTS) + [
    "/input/UseRight", "/input/UseLeft", "/input/GrabRight", "/input/GrabLeft",
]
ALL_AXES = list(OSCClient.OSCClient.AXIS_INPUTS)


def panic_release(windows: int = 8, repeat: int = 3):
    """全窓・全入力へ解除を送る。

    UDPは到達保証が無く解除のパケットだけ落ちると入力が入りっぱなしに
    なるため、複数回まとめて送る。押しっぱなしのキーも解放する。
    """
    print("全入力の強制解除を実行します")
    for i in range(windows):
        in_port, _out = OSCClient.ports_for_window(i)
        client = OSCClient.OSCClient(in_port)
        for _ in range(repeat):
            # ブール型(,T/,F)は引数を持たないため送ってはいけない。
            # VRChatが数値を読もうとしてメモリ上のゴミを拾い、巨大な値が
            # 保持されて入力が振り切れたまま固定される（実際に発生した）。
            # 型は厳密に分ける（混ぜると解釈が不定になり入力が入ってしまう）
            for address in ALL_INPUTS:
                client.send(address, 0)      # ボタンは int のみ
            for address in ALL_AXES:
                client.send(address, 0.0)    # 軸は float のみ
            time.sleep(0.02)
        client.close()
        print("  窓%d (ポート%d) へ解除を送信（int/bool/float の全型）" % (i + 1, in_port))

    try:
        import keyboard
        for key in ("w", "a", "s", "d", "shift", "space", "ctrl", "alt",
                    config.SELF_SUICIDE_KEY):
            try:
                keyboard.release(key)
            except Exception:
                pass
        print("  押しっぱなしのキーを解放しました")
    except Exception:
        print("  キー解放はスキップ（keyboardモジュール無し）")
    print("完了")


# ── CLI ─────────────────────────────────────────

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="OSCでVRChatを操作する（移動と視点のみ。クリックは不可）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="コマンド: " + " / ".join(sorted(COMMANDS)) + " / wait / stop")
    parser.add_argument("-w", "--window", type=int, default=1, help="窓番号（1始まり）")
    parser.add_argument("-p", "--port", type=int, help="送信ポートを直接指定")
    parser.add_argument("--script", help="コマンドを並べたファイルを実行")
    parser.add_argument("--monitor", type=float, metavar="秒",
                        help="VRChatからの送信を監視する")
    parser.add_argument("--list", action="store_true", help="窓とポートの対応を表示")
    parser.add_argument("--panic", action="store_true",
                        help="全窓の全入力を強制解除する（操作が取られた時の脱出用）")
    parser.add_argument("commands", nargs="*",
                        help="コマンドと秒数の並び 例: forward 2.1 left 0.11")
    args = parser.parse_args(argv)

    if args.panic:
        panic_release()
        return 0

    if args.list:
        print("窓番号とOSCポートの対応（起動時に --osc= で割り当て）")
        for i in range(8):
            in_port, out_port = OSCClient.ports_for_window(i)
            print("  窓%d: 送信先(VRChatが受信)=%d  監視用(VRChatが送信)=%d"
                  % (i + 1, in_port, out_port))
        return 0

    if args.monitor is not None:
        _in, out_port = OSCClient.ports_for_window(max(0, args.window - 1))
        monitor(args.port or out_port, args.monitor)
        return 0

    ctrl = controller(args.window, args.port)
    print("窓%d へ送信します（ポート%d）" % (args.window, ctrl.port))
    try:
        if args.script:
            with open(args.script, encoding="utf-8") as fh:
                ok = ctrl.run_script(fh.readlines())
        elif args.commands:
            ok = ctrl.run_script([" ".join(args.commands).replace(" ", " ")]) \
                if False else _run_inline(ctrl, args.commands)
        else:
            parser.print_help()
            return 1
        ctrl.stop()
        return 0 if ok else 1
    finally:
        ctrl.close()


def _run_inline(ctrl: Controller, tokens) -> bool:
    """`forward 2.1 left 0.11` のような並びを順に実行する"""
    ok = True
    i = 0
    while i < len(tokens):
        name = tokens[i].lower()
        if name in ("stop",):
            ok = ctrl.stop() and ok
            i += 1
        elif name in ("look", "lookv"):
            value = float(tokens[i + 1]); sec = float(tokens[i + 2])
            ok = ctrl.run_command(name, sec, value) and ok
            i += 3
        elif name == "wait":
            ok = ctrl.wait(float(tokens[i + 1])) and ok
            i += 2
        elif name in COMMANDS:
            sec = float(tokens[i + 1]) if i + 1 < len(tokens) else 0.1
            ok = ctrl.run_command(name, sec) and ok
            i += 2
        else:
            print("  不明なコマンド: %s" % tokens[i])
            ok = False
            i += 1
    return ok


if __name__ == "__main__":
    sys.exit(main())
