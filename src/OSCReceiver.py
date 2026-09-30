"""
VRChatからのOSC受信（アバターの移動速度）

ToNはラウンド種別に応じてプレイヤーの移動速度を変える。Beginを押した直後から
新しい速度が効くため、ROUND_STARTを待たずに種別を先読みできる。

窓ごとに VRChat の送信ポート（OSCClient.ports_for_window() の2つ目）をbindする。

注意: VRChatはアバターが持っているパラメータしか送らない。VelocityMagnitudeが
アバターに定義されていないと1件も飛んでこないため、呼び出し側は ever_received を
見て機能を止めること（無言で壊れないように）。
"""
import socket
import struct
import threading
import time

import config

# VelocityX/Z は細かく刻んで送られるが加速中の値ばかりで最高速に届かない。
# VelocityMagnitude は間引かれる代わりに、きっちり定数値（6.6 等）へ到達する。
VELOCITY_MAGNITUDE = "/avatar/parameters/VelocityMagnitude"
GROUNDED = "/avatar/parameters/Grounded"


def parse_message(data: bytes):
    """OSCメッセージを (address, value) にする。解釈できなければ None。

    OSCControl.monitor() と同じ手順。float/int/真偽だけ扱えれば足りる。
    """
    try:
        end = data.index(b"\x00")
        address = data[:end].decode("utf-8", "replace")
        pos = (end + 4) & ~3
        tend = data.index(b"\x00", pos)
        tags = data[pos + 1:tend].decode("ascii", "replace")
        pos = (tend + 4) & ~3
        if tags[:1] == "f":
            return address, struct.unpack_from(">f", data, pos)[0]
        if tags[:1] == "i":
            return address, float(struct.unpack_from(">i", data, pos)[0])
        if tags[:1] in ("T", "F"):
            return address, 1.0 if tags[0] == "T" else 0.0
    except Exception:
        return None
    return None


class VelocityReceiver:
    """1つの窓の VelocityMagnitude / Grounded を受け取る"""

    def __init__(self, port: int, log=None, host: str = "127.0.0.1"):
        self._port = port
        self._host = host
        self._log = log or (lambda _m: None)
        self._sock = None
        self._thread = None
        self._running = False
        self._lock = threading.Lock()
        self._speed = None
        self._grounded = None      # 未受信。Groundedを持たないアバターもある
        self._last_recv = 0.0
        self._started = 0.0
        self._ever_received = False
        # 「変化しなかった時間」はパケットが届いた瞬間に分かる。
        # 受信側で追跡し、ポーリング回数に依存しない判定にする。
        self._stable_value = None   # 直近で「変化していない」とみなしている値
        self._stable_since = 0.0    # その値になった時刻

    def start(self) -> bool:
        """受信を開始する。ポートを開けなければ False。"""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.bind((self._host, self._port))
            sock.settimeout(0.2)
        except OSError as e:
            self._log(f"速度受信ポート{self._port}を開けません: {e}")
            return False
        self._sock = sock
        self._started = time.time()
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self._running = False
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass

    def _run(self):
        while self._running and self._sock is not None:
            try:
                data, _addr = self._sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            parsed = parse_message(data)
            if parsed is None:
                continue
            self._handle(*parsed)

    def _handle(self, address, value):
        """受信した1件を反映する"""
        if address == VELOCITY_MAGNITUDE:
            now = time.time()
            with self._lock:
                self._speed = value
                self._last_recv = now
                self._ever_received = True
                # 更新するのは許容を超えて変化したときだけ。許容内の揺らぎで
                # 更新すると _stable_since が延々リセットされ、張り付かなくなる。
                if (self._stable_value is None
                        or abs(value - self._stable_value) > config.SPEED_STICK_TOL):
                    self._stable_value = value
                    self._stable_since = now
        elif address == GROUNDED:
            with self._lock:
                self._grounded = bool(value)

    @property
    def speed(self):
        """最新の VelocityMagnitude。未受信なら None"""
        with self._lock:
            return self._speed

    @property
    def grounded(self) -> bool:
        """接地しているか。未受信のときは True 扱い。

        Grounded を持たないアバターでも機能を止めないため。
        """
        with self._lock:
            return True if self._grounded is None else self._grounded

    @property
    def stable_value(self):
        """張り付いている値。未受信なら None"""
        with self._lock:
            return self._stable_value

    @property
    def stable_for(self) -> float:
        """その値が変化せずに続いている秒数。未受信なら 0.0"""
        with self._lock:
            since = self._stable_since
        return (time.time() - since) if since else 0.0

    @property
    def ever_received(self) -> bool:
        """速度を1件でも受け取ったか"""
        with self._lock:
            return self._ever_received

    @property
    def alive(self) -> bool:
        """速度が届いているか。「未受信」と「途絶」を区別する。

        起動直後はまだ動き出していないので受信0が正常。猶予内はTrueを返す。
        """
        with self._lock:
            last = self._last_recv
            started = self._started
        if not last:
            return (time.time() - started) <= config.SPEED_RECV_TIMEOUT_SEC
        return (time.time() - last) <= config.SPEED_RECV_TIMEOUT_SEC
