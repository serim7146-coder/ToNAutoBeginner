"""VRChat からの速さ（アバターのパラメータ）を OSCQuery で受け取る（DN。依頼者 2026-10-10）。

VRChat の送信ポート（9001 など）を bind すると、先に開いたアプリだけが受け取れる（ほかのアプリが
9001 を開いていた PC では速さが届かなかった。WinError 10048）。そこで固定のポートは開かず、
ツールに1つの OSCQuery のサービスを立てて mDNS で広告する。VRChat は見つけたサービスにも送る
（ToN Utils・ToN_Overlay と同じやり方。取り合いにならない）。

- OSC の受け口: 127.0.0.1 の空いている UDP ポート
- OSCQuery の HTTP: 127.0.0.1 の空いている TCP ポート。`/?HOST_INFO` と `/`（`/avatar` を含む木）
- mDNS（zeroconf）: `_oscjson._tcp`（HTTP）と `_osc._udp`（OSC）。アドレスは 127.0.0.1
- どの窓の値か: VRChat はどの窓も同じサービスへ送ってくる。送り元の UDP ポート → そのポートを
  持つプロセス（netstat）→ その PID の VRChat の窓（登録した受信器）で振り分ける

窓ごとの受信器（OSCReceiver.VelocityReceiver）が start で登録・stop で外す。サービスはツールが動いている間
出しっぱなし（窓の監視の開始・停止のたびに名乗りを出し入れしない。出し入れを重ねると VRChat が見つけなく
なった疑いがある。DS）。立てるのは最初の窓の監視か VRChat の起動（ensure）、取り下げるのはツールを閉じるとき
（shutdown）。届いた値は、その窓が OSCQuery で受けると決めたときだけ渡す（固定ポートの窓には渡さない）。
"""
import json
import random
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import config
import DebugLog
import OSCClient
import OSCReceiver
import TaskStats

HOST = "127.0.0.1"          # 待つのも広告するのもここだけ（ほかの PC の VRChat から送られない）
NAME_PREFIX = "ToNAutoBeginner"
PORTS_REFRESH_SEC = 1.0     # 表に無い送り元が来たとき、送り元の対応表を取り直す間隔
# 表にはあるがツールの窓ではない送り元（監視していない VRChat など）は、もっと空けて取り直す
# （送り続けられても netstat を毎秒起こさない。VRChat を立ち上げ直してポートが使い回されたときのため）
PORTS_RECHECK_SEC = 30.0
UNKNOWN_LOG_EVERY = 100     # 分からない送り元は、これだけ数えるごとに debug.log へ
# `/` で返すパラメータ（今読んでいるもの）。VRChat は /avatar を持つサービスにアバターのパラメータを送る
PARAMETERS = {OSCReceiver.VELOCITY_MAGNITUDE: "f", OSCReceiver.GROUNDED: "T"}


def osc_tree() -> dict:
    """`/` の木（OSCQuery の形。ACCESS 2 = 書ける＝送ってよい）"""
    root = {"FULL_PATH": "/", "ACCESS": 0, "CONTENTS": {}}
    for address, type_tag in PARAMETERS.items():
        node = root
        parts = [p for p in address.split("/") if p]
        for i, part in enumerate(parts):
            path = "/" + "/".join(parts[:i + 1])
            node = node["CONTENTS"].setdefault(part, {"FULL_PATH": path, "ACCESS": 0, "CONTENTS": {}})
        node.pop("CONTENTS", None)
        node.update({"TYPE": type_tag, "ACCESS": 2})
    return root


def host_info(name: str, osc_port: int) -> dict:
    return {"NAME": name, "OSC_IP": HOST, "OSC_PORT": osc_port, "OSC_TRANSPORT": "UDP",
            "EXTENSIONS": {"ACCESS": True, "CLIPMODE": False, "RANGE": False, "TYPE": True, "VALUE": False}}


def respond(path: str, name: str, osc_port: int) -> tuple:
    """HTTP の答え (状態, JSON の値)。`/?HOST_INFO` → HOST_INFO、`/…` → その節（無ければ 404）"""
    parts = urlsplit(path)
    if parts.query.split("&")[0].upper() == "HOST_INFO":
        return 200, host_info(name, osc_port)
    node = osc_tree()
    for part in [p for p in parts.path.split("/") if p]:
        node = (node.get("CONTENTS") or {}).get(part)
        if node is None:
            return 404, {"ERROR": "not found"}
    return 200, node


# ── テストで差し替える入口（本物の bind・mDNS をしない。tests/support の見張り）──
def _udp_socket():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((HOST, 0))
    sock.settimeout(0.2)
    return sock


def _http_server(handler):
    return ThreadingHTTPServer((HOST, 0), handler)


def _advertise(name: str, http_port: int, osc_port: int):
    """mDNS で広告する。止めるための (zeroconf, [ServiceInfo]) を返す"""
    from zeroconf import IPVersion, ServiceInfo, Zeroconf
    zc = Zeroconf(ip_version=IPVersion.V4Only)
    addresses = [socket.inet_aton(HOST)]
    infos = [ServiceInfo("_oscjson._tcp.local.", f"{name}._oscjson._tcp.local.", addresses=addresses,
                         port=http_port, properties={"txtvers": "1"}, server=f"{name}.oscjson.local."),
             ServiceInfo("_osc._udp.local.", f"{name}._osc._udp.local.", addresses=addresses,
                         port=osc_port, properties={"txtvers": "1"}, server=f"{name}.osc.local.")]
    for info in infos:
        zc.register_service(info, allow_name_change=True)
    return zc, infos


def _withdraw(advert):
    zc, infos = advert
    try:
        for info in infos:
            zc.unregister_service(info)
    finally:
        zc.close()


def _pid_of_window(hwnd: int) -> int:
    try:
        import win32process
        return int(win32process.GetWindowThreadProcessId(hwnd)[1])
    except Exception:
        DebugLog.exception("OSCQuery._pid_of_window")
        return 0


class Service:
    """ツールに1つの OSCQuery のサービス"""

    def __init__(self):
        self.name = f"{NAME_PREFIX}-{random.randint(1000, 9999)}"
        self.osc_port = 0
        self.http_port = 0
        self._sock = None
        self._http = None
        self._advert = None
        self._advert_done = threading.Event()   # 広告の結果が出た（できた／できない。FP）
        self._running = False
        self._lock = threading.Lock()
        self._receivers = []            # 登録した窓ごとの受信器
        self._port_pid = {}             # 送り元の UDP ポート → PID（netstat）
        self._refreshed_at = 0.0
        self._routed = set()            # debug.log に書いた (送り元ポート, 窓)
        self.unknown = 0                # 窓が分からず捨てた数

    def start(self) -> bool:
        try:
            self._sock = _udp_socket()
            self.osc_port = self._sock.getsockname()[1]
            service = self

            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):
                    status, value = respond(self.path, service.name, service.osc_port)
                    body = json.dumps(value).encode("utf-8")
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                def log_message(self, *_args):
                    pass                        # 標準エラーへ書かない
            self._http = _http_server(Handler)
            self.http_port = self._http.server_address[1]
        except OSError as e:
            DebugLog.exception("OSCQuery.Service.start")
            DebugLog.write(f"[OSCQuery] 受け口を開けません: {e}")
            self._close_sockets()
            return False
        self._running = True
        TaskStats.thread("OSCQuery.serve_forever", self._http.serve_forever, daemon=True).start()
        TaskStats.thread("OSCQuery._run", self._run, daemon=True).start()
        # mDNS の登録は名前の確かめに 1〜2 秒かかるので、待たない
        TaskStats.thread("OSCQuery._advertise", self._advertise, daemon=True).start()
        DebugLog.write(f"[OSCQuery] サービス {self.name}: OSC 127.0.0.1:{self.osc_port}・"
                       f"HTTP 127.0.0.1:{self.http_port}")
        return True

    @property
    def advertised(self) -> bool:
        """mDNS で広告できているか（結果が出る前・失敗・取り下げた後は False。待つのは wait_advertised）"""
        return self._advert is not None

    def _advertise(self):
        try:
            self._do_advertise()
        finally:
            self._advert_done.set()

    def _do_advertise(self):
        try:
            advert = _advertise(self.name, self.http_port, self.osc_port)
        except Exception:
            DebugLog.exception("OSCQuery.Service._advertise")
            DebugLog.write("[OSCQuery] mDNS で広告できません（VRChat に見つけてもらえない）")
            return
        with self._lock:
            if not self._running:               # 広告の間に止められた
                stale, advert = advert, None
            else:
                stale = None
                self._advert = advert
        if stale is not None:
            _withdraw(stale)
            return
        DebugLog.write(f"[OSCQuery] mDNS で広告した: {self.name}（_oscjson._tcp {self.http_port}・"
                       f"_osc._udp {self.osc_port}・127.0.0.1）")

    def stop(self):
        with self._lock:
            self._running = False
            advert, self._advert = self._advert, None
        if advert is not None:
            try:
                _withdraw(advert)
            except Exception:
                DebugLog.exception("OSCQuery.Service.stop")
        self._close_sockets()
        DebugLog.write(f"[OSCQuery] サービス {self.name} を止めた（広告を取り下げた）"
                       f"・窓の分からない送り元 {self.unknown} 件")

    def _close_sockets(self):
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.close()
            except Exception:
                DebugLog.exception("OSCQuery.Service._close_sockets")
        http, self._http = self._http, None
        if http is not None:
            try:
                http.shutdown()
                http.server_close()
            except Exception:
                DebugLog.exception("OSCQuery.Service._close_sockets")

    # ── 窓ごとの受信器 ──
    def register(self, receiver):
        with self._lock:
            if receiver not in self._receivers:
                self._receivers.append(receiver)

    def unregister(self, receiver) -> int:
        """外して、残りの数を返す"""
        with self._lock:
            self._receivers = [r for r in self._receivers if r is not receiver]
            return len(self._receivers)

    # ── 受信と振り分け ──
    def _run(self):
        while self._running and self._sock is not None:
            try:
                data, addr = self._sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            parsed = OSCReceiver.parse_message(data)
            if parsed is None:
                continue
            self.route(addr[1], *parsed)

    def route(self, src_port: int, address: str, value):
        """送り元のポートの窓へ渡す。分からなければ対応表を取り直して（間隔を空けて）もう一度、
        それでも分からなければ捨てる。渡したら True"""
        receiver = self._receiver_for(src_port)
        wait = PORTS_RECHECK_SEC if src_port in self._port_pid else PORTS_REFRESH_SEC
        if receiver is None and time.time() - self._refreshed_at >= wait:
            self._refresh_ports()
            receiver = self._receiver_for(src_port)
        if receiver is None:
            self.unknown += 1
            if self.unknown == 1 or self.unknown % UNKNOWN_LOG_EVERY == 0:
                DebugLog.write(f"[OSCQuery] 窓の分からない送り元 {src_port}（PID "
                               f"{self._port_pid.get(src_port, '?')}）→ 捨てた（{self.unknown} 件目）")
            return False
        if not receiver.accept_oscquery():
            return False                    # その窓は固定ポートで受けている（二重にしない）
        key = (src_port, receiver.window_label)
        if key not in self._routed:
            self._routed.add(key)
            DebugLog.write(f"[OSCQuery] 送り元 {src_port} → PID {self._port_pid.get(src_port)} → "
                           f"{receiver.window_label}")
        receiver._handle(address, value)
        return True

    def _receiver_for(self, src_port: int):
        pid = self._port_pid.get(src_port)
        if not pid:
            return None
        with self._lock:
            receivers = list(self._receivers)
        for receiver in receivers:
            if receiver.pid == pid:
                return receiver
        return None

    def _refresh_ports(self):
        self._refreshed_at = time.time()
        ports_by_pid = OSCClient.udp_ports_by_pid()
        if ports_by_pid is None:
            return
        self._port_pid = {port: pid for pid, ports in ports_by_pid.items() for port in ports}


# ── ツールに1つ ──
_service = None
_service_lock = threading.Lock()


def ensure() -> bool:
    """サービスが無ければ立てる（VRChat を起動する前など）。立っていれば True"""
    global _service
    with _service_lock:
        if _service is None:
            service = Service()
            if not service.start():
                return False
            _service = service
        return True


def acquire(receiver) -> bool:
    """窓の受信器を登録する（サービスが無ければ立てる）。立てられなければ False"""
    if not ensure():
        return False
    with _service_lock:
        if _service is None:
            return False
        _service.register(receiver)
        return True


def release(receiver):
    """窓の受信器を外す。サービスは止めない（ツールを閉じるまで出しっぱなし）"""
    with _service_lock:
        if _service is not None:
            _service.unregister(receiver)


def shutdown():
    """ツールを閉じるとき。残っていても止める"""
    global _service
    with _service_lock:
        service, _service = _service, None
    if service is not None:
        service.stop()


def current():
    return _service


def wait_advertised(timeout: float):
    """mDNS の広告の結果が出るまで最大 timeout 秒待ち、広告できていれば True・できていなければ False。
    サービスが無ければ None（FP。広告できていなければ VRChat は見つけられないので、15 秒待たずに固定ポートへ）"""
    service = current()
    if service is None:
        return None
    service._advert_done.wait(timeout)
    return service.advertised
