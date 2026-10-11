"""
VRChatからのOSC受信（アバターの移動速度）

ToNはラウンド種別に応じてプレイヤーの移動速度を変える。Beginを押した直後から
新しい速度が効くため、ROUND_STARTを待たずに種別を先読みできる。

受け方は窓ごと（DS。依頼者 2026-10-11「OSCQuery で接続して、接続できなかったら固定ポート」）:
1. まずツールに1つの OSCQuery のサービス（OSCQuery.py）で待つ。サービスが送り元のポート → PID → 窓で
   振り分けて渡す。VRChat はサービスを見つけた直後にアバターの今の値をまとめて送るので、届いた＝つながった
2. OSCQUERY_CONNECT_WAIT_SEC たってもその窓から届かなければ、固定ポート（VRChat の送信ポート 9001 など）を
   開く（先に開いたアプリだけが受け取れる。OSCQuery で届く窓は開かないので、ほかのアプリが使える）
3. 固定ポートもほかのアプリが使っていれば、受け取れない（公開ログにそのアプリの名前）
一度決めた受け方は、その監視の間は変えない（固定ポートの窓に後から OSCQuery で届いても捨てる）。

注意: VRChatはアバターが持っているパラメータしか送らない。VelocityMagnitudeが
アバターに定義されていないと1件も飛んでこないため、呼び出し側は ever_received を
見て機能を止めること（無言で壊れないように）。
"""
import socket
import struct
import threading
import time

import config
import DebugLog

# 受け方
MODE_WAITING = "waiting"            # OSCQuery で届くのを待っている
MODE_OSCQUERY = "oscquery"          # OSCQuery で受ける
MODE_PORT = "port"                  # 固定ポートで受ける
MODE_NONE = "none"                  # 受け取れない（固定ポートもほかのアプリが使っている）

# VelocityX/Z は細かく刻んで送られるが加速中の値ばかりで最高速に届かない。
# VelocityMagnitude は間引かれる代わりに、きっちり定数値（6.6 等）へ到達する。
VELOCITY_MAGNITUDE = "/avatar/parameters/VelocityMagnitude"
GROUNDED = "/avatar/parameters/Grounded"


def port_owner(port: int) -> str:
    """その UDP ポートを使っているアプリの名前（例 ToNUtils.exe）。分からなければ「ほかのアプリ」"""
    try:
        import OSCClient
        import ProcessCheck
        for pid, ports in (OSCClient.udp_ports_by_pid() or {}).items():
            if port in ports:
                return ProcessCheck.process_name(pid) or f"PID {pid}"
    except Exception:
        DebugLog.exception("OSCReceiver.port_owner")
    return "ほかのアプリ"


def parse_message(data: bytes):
    """OSCメッセージを (address, value) にする。解釈できなければ None。

    float/int/真偽だけ扱えれば足りる。
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
        DebugLog.exception("OSCReceiver.parse_message")
        return None
    return None


class VelocityReceiver:
    """1つの窓の VelocityMagnitude / Grounded を受け取る（まず OSCQuery、届かなければ固定ポート）"""

    def __init__(self, hwnd: int, port: int = 0, log=None, window_label: str = "", host: str = "127.0.0.1"):
        self._hwnd = hwnd
        self._port = port                  # 固定ポート（VRChat の送信ポート）。OSCQuery で届かなかったときだけ開く
        self._host = host
        self._log = log or (lambda _m: None)
        self.window_label = window_label or f"hwnd={int(hwnd or 0):#x}"
        self._pid = 0
        self._registered = False
        self.mode = MODE_WAITING
        self._sock = None
        self._running = False
        self._stop_event = threading.Event()
        self._wait_thread = None            # OSCQuery で届くのを待つスレッド（止めたらすぐ終わる）
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

    @property
    def pid(self) -> int:
        """この窓の VRChat のプロセス（振り分けに使う。取れるまで取り直す）"""
        if not self._pid:
            import OSCQuery
            self._pid = OSCQuery._pid_of_window(self._hwnd)
        return self._pid

    def start(self) -> bool:
        """まず OSCQuery で待つ（サービスが無ければ立てる）。OSCQUERY_CONNECT_WAIT_SEC で届かなければ
        固定ポートへ。サービスを立てられなければ、すぐ固定ポートへ。受け方は後で決まるので常に True"""
        import OSCQuery
        self._started = time.time()
        self._running = True
        self._stop_event.clear()
        if OSCQuery.acquire(self):
            self._registered = True
            self._wait_thread = threading.Thread(target=self._wait_then_fall_back, daemon=True)
            self._wait_thread.start()
        else:
            DebugLog.write(f"[速度受信] {self.window_label}: OSCQuery のサービスを立てられない → 固定ポートへ")
            self.fall_back()
        return True

    def stop(self):
        self._running = False
        self._stop_event.set()
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.close()
            except Exception:
                DebugLog.exception("OSCReceiver.stop")
        if self._registered:
            self._registered = False
            import OSCQuery
            OSCQuery.release(self)

    def _wait_then_fall_back(self):
        if self._stop_event.wait(config.OSCQUERY_CONNECT_WAIT_SEC):
            return                          # 止めた
        self.fall_back()

    def accept_oscquery(self) -> bool:
        """OSCQuery で届いた値を受けるか。待っている間に届いたら OSCQuery に決める（以後は変えない）"""
        with self._lock:
            if self.mode == MODE_WAITING and self._running:
                self.mode = MODE_OSCQUERY
                first = True
            else:
                first = False
            accept = self.mode == MODE_OSCQUERY
        if first:
            DebugLog.write(f"[速度受信] {self.window_label}: OSCQuery で受信（PID {self.pid}）。固定ポート "
                           f"{self._port} は開かない")
        return accept

    def fall_back(self):
        """OSCQuery で届かなかった → 固定ポートを開く。開けなければ受け取れない（使っているアプリの名前を出す）"""
        with self._lock:
            if self.mode != MODE_WAITING or not self._running:
                return
            self.mode = MODE_PORT
        if not self._port:
            self._give_up("固定ポートが分からない")
            return
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.bind((self._host, self._port))
            sock.settimeout(0.2)
        except OSError as e:
            owner = port_owner(self._port)
            DebugLog.write(f"[速度受信] {self.window_label}: 固定ポート {self._port} は {owner} が使用中"
                           f"（{e}）→ 受け取れない")
            self._give_up(f"ポート {self._port} は {owner} が使っています")
            return
        self._sock = sock
        DebugLog.write(f"[速度受信] {self.window_label}: OSCQuery で "
                       f"{config.OSCQUERY_CONNECT_WAIT_SEC:.0f}秒届かない → 固定ポート {self._port} で受信")
        threading.Thread(target=self._run, daemon=True).start()

    def _give_up(self, why: str):
        with self._lock:
            self.mode = MODE_NONE
        self._log(f"{self.window_label}: 速さを受け取れません（OSCQuery で届かず、{why}）")

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
    def grounded_or_none(self):
        """接地しているか。未受信なら None（アイテム自動取得は、分からないと着地を数えない）"""
        with self._lock:
            return self._grounded

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
