"""
VRChatへのOSC送信（依存追加なし・OSCメッセージは自前で生成）

窓ごとに別ポートを割り当てることで、多重起動したVRChatを個別に操作できる。
VRChatは既定でUDP 9000を掴むため、2窓目以降は --osc=<in>:<ip>:<out> を
付けて起動しないとポートが競合して受信できない。

送れるのは移動と視点のみ。クリック相当（/input/UseRight等）は
「手に持ったアイテムを使う」入力で、ワールドUIのクリックには使えない。
（VRChatのOSCQueryで全39項目を確認済み。該当する入力は存在しない）
"""
import socket
import struct
import subprocess
import time

import config


class OSCClient:
    """1つのVRChatウィンドウに対応するOSC送信クライアント"""

    def __init__(self, port: int, host: str = "127.0.0.1"):
        self.port = port
        self.host = host
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    # ── OSCメッセージ生成 ──────────────────────

    @staticmethod
    def _pad(data: bytes) -> bytes:
        """4バイト境界へ揃える。既に揃っていれば何も足さない。

        以前は常に (4 - len % 4) バイト足していたため、長さが4の倍数の
        アドレス（/input/Vertical, /input/Jump, /input/UseRight など）で
        4バイト余計に入り、型タグの位置がずれてVRChatが解釈できなかった。
        """
        return data + b"\x00" * ((4 - len(data) % 4) % 4)

    @classmethod
    def build_message(cls, address: str, value) -> bytes:
        out = cls._pad(address.encode("utf-8") + b"\x00")
        if isinstance(value, bool):
            # ブール型タグ(,T / ,F)は引数を持たない。これをVRChatへ送ると
            # 数値を読もうとしてメモリ上のゴミを拾い、巨大な値が保持されて
            # 入力が振り切れたまま固定される事故が実際に起きた。intで送る。
            return out + cls._pad(b",i\x00") + struct.pack(">i", 1 if value else 0)
        if isinstance(value, float):
            return out + cls._pad(b",f\x00") + struct.pack(">f", value)
        return out + cls._pad(b",i\x00") + struct.pack(">i", int(value))

    def send(self, address: str, value) -> bool:
        try:
            self._sock.sendto(self.build_message(address, value), (self.host, self.port))
            return True
        except Exception:
            return False

    # ── 入力 ──────────────────────────────────

    def press(self, address: str, hold_sec: float) -> bool:
        """ボタン系入力を押して離す。0→1の変化で反応する入力があるため
        押す前に0を送ってから1にする。"""
        ok = self.send(address, 0)
        ok = self.send(address, 1) and ok
        time.sleep(max(0.0, hold_sec))
        return self.send(address, 0) and ok

    def press_multi(self, holds) -> bool:
        """複数の入力を同時に押し、それぞれの秒数で個別に離す。

        holds は [(アドレス, 押す秒数), ...]。全部を同時に押し始め、
        秒数の短いものから離していく。

        逐次に press() を並べると移動ごとに加速と減速が入り、
        前の移動の残留速度が次の移動に混ざる。同時押しなら加速・減速が
        各1回で済み、その混入が起きない。
        """
        holds = [(a, s) for a, s in holds if s > 0]
        if not holds:
            return True
        # 0→1の変化で反応する入力があるため、押す前に0を送る
        ok = True
        for address, _ in holds:
            ok = self.send(address, 0) and ok
        for address, _ in holds:
            ok = self.send(address, 1) and ok
        start = time.time()
        for address, sec in sorted(holds, key=lambda h: h[1]):
            remain = sec - (time.time() - start)
            if remain > 0:
                time.sleep(remain)
            ok = self.send(address, 0) and ok
        return ok

    def move_forward(self, sec: float) -> bool:
        return self.press("/input/MoveForward", sec)

    def move_left(self, sec: float) -> bool:
        return self.press("/input/MoveLeft", sec)

    def move_right(self, sec: float) -> bool:
        return self.press("/input/MoveRight", sec)

    def look(self, horizontal: float, sec: float) -> bool:
        """視点を回す。horizontalは-1.0〜1.0。"""
        ok = self.send("/input/LookHorizontal", float(horizontal))
        time.sleep(max(0.0, sec))
        return self.send("/input/LookHorizontal", 0.0) and ok

    def look_vertical(self, vertical: float, sec: float) -> bool:
        ok = self.send("/input/LookVertical", float(vertical))
        time.sleep(max(0.0, sec))
        return self.send("/input/LookVertical", 0.0) and ok

    # ボタン系（int 0/1）と軸系（float -1.0〜1.0）の全入力。
    # 軸(Vertical/Horizontal)を漏らすと移動が残り続けるので必ず含める。
    BUTTON_INPUTS = (
        "/input/MoveForward", "/input/MoveBackward",
        "/input/MoveLeft", "/input/MoveRight",
        "/input/LookLeft", "/input/LookRight",
        "/input/Run", "/input/Jump",
        "/input/ComfortLeft", "/input/ComfortRight",
    )
    AXIS_INPUTS = (
        "/input/Vertical", "/input/Horizontal",
        "/input/LookHorizontal", "/input/LookVertical",
    )

    def stop_all(self, repeat: int = 2):
        """全入力を0に戻す。

        UDPは到達保証が無いため複数回送る。軸(Vertical/Horizontal)を
        含めないと移動が残り続ける。
        """
        # 型は厳密に分ける。ボタンにfloat、軸にintを送ると解釈が不定になり
        # 意図しない入力が入る（軸へintを送って視点が上を向く事象が発生した）。
        for _ in range(max(1, repeat)):
            for addr in self.BUTTON_INPUTS:
                self.send(addr, 0)      # ボタンは int のみ
            for addr in self.AXIS_INPUTS:
                self.send(addr, 0.0)    # 軸は float のみ

    def close(self):
        try:
            self._sock.close()
        except Exception:
            pass


# ── 窓ごとのポート割り当て ────────────────────

def ports_for_window(index: int) -> tuple[int, int]:
    """窓番号(0始まり)に対する (受信ポート, 送信ポート) を返す。
    VRChatの --osc=<in>:<ip>:<out> にそのまま使う。"""
    base = config.OSC_BASE_IN_PORT + index * config.OSC_PORT_STRIDE
    return base, base + 1


def udp_ports_of_process(pid: int) -> set:
    """指定プロセスが待ち受けているUDPポートの集合を返す"""
    ports = set()
    try:
        out = subprocess.run(
            ["netstat", "-ano", "-p", "UDP"],
            capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
    except Exception:
        return ports
    for line in out.splitlines():
        parts = line.split()
        # 例: UDP  0.0.0.0:9000  *:*  40320
        if len(parts) >= 4 and parts[0].upper() == "UDP" and parts[-1].isdigit():
            if int(parts[-1]) != pid:
                continue
            local = parts[1]
            if ":" in local:
                try:
                    ports.add(int(local.rsplit(":", 1)[1]))
                except ValueError:
                    pass
    return ports


def osc_available_for(hwnd: int, window_index: int) -> bool:
    """この窓がOSCを受信できるか（起動時に1回だけ判定する想定）。

    このツールが --osc= を付けて起動した窓なら、割り当てたポートを
    掴んでいる。手動起動の窓は既定の9000しか使えず、2窓目以降は
    ポート競合でOSC自体が無効になる。
    """
    try:
        import win32process
        _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
    except Exception:
        return False
    if not pid:
        return False
    expected, _out = ports_for_window(window_index)
    return expected in udp_ports_of_process(pid)


def osc_launch_arg(index: int, host: str = "127.0.0.1") -> str:
    in_port, out_port = ports_for_window(index)
    return f"--osc={in_port}:{host}:{out_port}"
