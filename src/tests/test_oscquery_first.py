"""速さの受信は「まず OSCQuery、届かなかった窓だけ固定ポート」（DS）"""
from tests.support import *  # noqa: F401,F403
import socket as real_socket

import ProcessCheck
from tests.test_oscquery import _Case


class _Ports:
    """固定ポートの偽の socket（OSCReceiver の中だけ）。bind を記録し、使用中にもできる。届く値を順に返す"""

    def __init__(self, busy=()):
        self.binds = []
        self.busy = set(busy)
        self.packets = {}               # ポート → [(data, addr)]
        ports = self

        class Sock:
            def __init__(self, *_a):
                self.port = None
                self.closed = False

            def bind(self, addr):
                if addr[1] in ports.busy:
                    raise OSError(10048, "使用中")
                ports.binds.append(addr)
                self.port = addr[1]

            def settimeout(self, _sec):
                pass

            def recvfrom(self, _size):
                queue = ports.packets.get(self.port) or []
                if queue:
                    return queue.pop(0)
                if self.closed:
                    raise OSError("closed")
                time.sleep(0.005)
                raise real_socket.timeout()

            def close(self):
                self.closed = True
        self.module = type(sys)("socket_ports")
        self.module.socket = Sock
        self.module.AF_INET, self.module.SOCK_DGRAM = real_socket.AF_INET, real_socket.SOCK_DGRAM
        self.module.timeout = real_socket.timeout


class _FirstCase(_Case):
    WAIT = 0.05

    def setUp(self):
        super().setUp()
        self.ports = _Ports()
        self.logs = []
        for p in (patch.object(OSCReceiver, "socket", self.ports.module),
                  patch.object(config, "OSCQUERY_CONNECT_WAIT_SEC", self.WAIT)):
            p.start()
            self.addCleanup(p.stop)

    def _receiver(self, hwnd, port, label):
        r = OSCReceiver.VelocityReceiver(hwnd, port, self.logs.append, window_label=label)
        self.addCleanup(r.stop)
        return r

    def _wait_mode(self, r, mode=None):
        deadline = time.time() + 2
        while (r.mode == OSCReceiver.MODE_WAITING if mode is None else r.mode != mode) and time.time() < deadline:
            time.sleep(0.005)
        return r.mode


class TestOSCQueryFirst(_FirstCase):
    def test_arriving_through_oscquery_never_opens_the_port(self):
        r = self._receiver(11, 9011, "窓1")
        r.start()
        service = OSCQuery.current()
        self.assertTrue(service.route(5001, OSCReceiver.VELOCITY_MAGNITUDE, 6.5))
        self.assertEqual(r.mode, OSCReceiver.MODE_OSCQUERY)
        time.sleep(self.WAIT * 4)                   # 待つ時間を過ぎても
        self.assertEqual(r.mode, OSCReceiver.MODE_OSCQUERY)
        self.assertEqual(self.ports.binds, [], "固定ポートは開かない（ほかのアプリが使える）")
        self.assertEqual(r.speed, 6.5)

    def test_not_arriving_opens_the_port_after_the_wait(self):
        r = self._receiver(11, 9011, "窓1")
        r.start()
        self.assertEqual(self.ports.binds, [], "待っている間は開かない")
        self.assertEqual(self._wait_mode(r), OSCReceiver.MODE_PORT)
        self.assertEqual(self.ports.binds, [("127.0.0.1", 9011)])
        packet = OSCClient.OSCClient.build_message(OSCReceiver.VELOCITY_MAGNITUDE, 4.0)
        self.ports.packets[9011] = [(packet, ("127.0.0.1", 50000))]
        deadline = time.time() + 2
        while r.speed is None and time.time() < deadline:
            time.sleep(0.005)
        self.assertEqual(r.speed, 4.0)
        self.assertTrue(r.ever_received)

    def test_the_wait_is_15_seconds(self):
        with patch.object(config, "OSCQUERY_CONNECT_WAIT_SEC", 15.0):
            self.assertEqual(config.OSCQUERY_CONNECT_WAIT_SEC, 15.0)
        src = Path(config.__file__).read_text(encoding="utf-8")
        self.assertIn("OSCQUERY_CONNECT_WAIT_SEC = 15.0", src)

    def test_a_busy_port_cannot_receive_and_names_the_app(self):
        self.ports.busy.add(9031)
        with patch.object(OSCClient, "udp_ports_by_pid", return_value={4242: {9031}, 100: {5001}}), \
             patch.object(ProcessCheck, "process_name", side_effect=lambda pid: "ToNUtils.exe" if pid == 4242 else None):
            r = self._receiver(11, 9031, "窓4")
            r.start()
            self.assertEqual(self._wait_mode(r), OSCReceiver.MODE_NONE)
        self.assertEqual(self.logs, ["窓4: 速さを受け取れません（OSCQuery で届かず、ポート 9031 は ToNUtils.exe が使っています）"])
        self.assertFalse(r.ever_received)

    def test_an_unknown_owner_is_said_too(self):
        self.ports.busy.add(9031)
        with patch.object(OSCClient, "udp_ports_by_pid", return_value={}):
            r = self._receiver(11, 9031, "窓4")
            r.start()
            self._wait_mode(r, OSCReceiver.MODE_NONE)
        self.assertEqual(self.logs, ["窓4: 速さを受け取れません（OSCQuery で届かず、ポート 9031 は ほかのアプリ が使っています）"])

    def test_two_windows_received_in_different_ways_do_not_mix(self):
        a = self._receiver(11, 9011, "窓1")
        b = self._receiver(22, 9021, "窓2")
        a.start()
        b.start()
        service = OSCQuery.current()
        self.assertTrue(service.route(5001, OSCReceiver.VELOCITY_MAGNITUDE, 6.5))   # 窓1 は OSCQuery
        self.assertEqual(self._wait_mode(b), OSCReceiver.MODE_PORT)                # 窓2 は固定ポート
        packet = OSCClient.OSCClient.build_message(OSCReceiver.VELOCITY_MAGNITUDE, 4.0)
        self.ports.packets[9021] = [(packet, ("127.0.0.1", 50000))]
        deadline = time.time() + 2
        while b.speed is None and time.time() < deadline:
            time.sleep(0.005)
        self.assertEqual((a.mode, b.mode), (OSCReceiver.MODE_OSCQUERY, OSCReceiver.MODE_PORT))
        self.assertEqual((a.speed, b.speed), (6.5, 4.0))
        self.assertEqual(self.ports.binds, [("127.0.0.1", 9021)], "窓1 の固定ポートは開かない")

    def test_the_way_does_not_change_later(self):
        """固定ポートで受けている窓に、後から OSCQuery で届いても固定ポートのまま（二重にしない）"""
        r = self._receiver(11, 9011, "窓1")
        r.start()
        self.assertEqual(self._wait_mode(r), OSCReceiver.MODE_PORT)
        service = OSCQuery.current()
        self.assertFalse(service.route(5001, OSCReceiver.VELOCITY_MAGNITUDE, 9.9))
        self.assertEqual(r.mode, OSCReceiver.MODE_PORT)
        self.assertIsNone(r.speed, "OSCQuery の分は捨てる")
        self.assertEqual(self.ports.binds, [("127.0.0.1", 9011)], "開き直さない")

    def test_an_oscquery_window_does_not_fall_back_later(self):
        r = self._receiver(11, 9011, "窓1")
        r.start()
        OSCQuery.current().route(5001, OSCReceiver.VELOCITY_MAGNITUDE, 6.5)
        r.fall_back()                               # 待ちの終わり（届いた後）
        self.assertEqual(r.mode, OSCReceiver.MODE_OSCQUERY)
        self.assertEqual(self.ports.binds, [])

    def test_stopping_while_waiting_does_not_open_the_port(self):
        with patch.object(config, "OSCQUERY_CONNECT_WAIT_SEC", 60):
            r = self._receiver(11, 9011, "窓1")
            r.start()
        r.stop()
        r._wait_thread.join(1.0)
        self.assertFalse(r._wait_thread.is_alive(), "止めたら待ちもすぐ終わる（60 秒残らない）")
        time.sleep(self.WAIT * 4)
        self.assertEqual(self.ports.binds, [])
        self.assertEqual(r.mode, OSCReceiver.MODE_WAITING)

    def test_the_debug_log_tells_the_way(self):
        with patch.object(DebugLog, "write") as write:
            a = self._receiver(11, 9011, "窓1")
            a.start()
            OSCQuery.current().route(5001, OSCReceiver.VELOCITY_MAGNITUDE, 6.5)
            b = self._receiver(22, 9021, "窓2")
            b.start()
            self._wait_mode(b)
        lines = [c.args[0] for c in write.call_args_list]
        self.assertIn("[速度受信] 窓1: OSCQuery で受信（PID 100）。固定ポート 9011 は開かない", lines)
        self.assertTrue(any(l.startswith("[速度受信] 窓2: OSCQuery で") and "固定ポート 9021 で受信" in l
                            for l in lines), lines)


class TestNotReceivingYet(_FirstCase):
    """受け取れていない窓（待っている間・受け取れない）では横移動しない・「速度検知 開始」と言わない"""

    def setUp(self):
        super().setUp()
        SharedState.set_speed_detect(True)
        self.addCleanup(SharedState.set_speed_detect, config.SPEED_DETECT_ENABLED)

    def _executor(self, receiver):
        st = WindowState(instance_type=config.INSTANCE_PRIVATE)
        ex = ActionExecutor.ActionExecutor(WindowConfig(hwnd=11, osc_port=9000), st, lambda: True, lambda _m: None)
        ex._speed_ready.set()
        ex._receiver = receiver
        return ex

    def test_waiting_and_unable_do_not_strafe(self):
        self.ports.busy.add(9001)
        with patch.object(config, "OSCQUERY_CONNECT_WAIT_SEC", 60):
            waiting = self._receiver(11, 9001, "窓1")
            waiting.start()
        unable = self._receiver(22, 9001, "窓2")
        unable.start()
        self._wait_mode(unable, OSCReceiver.MODE_NONE)
        for r in (waiting, unable):
            ex = self._executor(r)
            with patch.object(ex, "move") as move:
                ex.do_speed_strafe()
            move.assert_not_called()
            self.assertFalse(ex.speed_receiving)

    def test_received_through_either_way_strafes(self):
        r = self._receiver(11, 9011, "窓1")
        r.start()
        OSCQuery.current().route(5001, OSCReceiver.VELOCITY_MAGNITUDE, 6.5)
        ex = self._executor(r)
        with patch.object(ex, "move") as move:
            ex.do_speed_strafe()
        self.assertEqual(move.call_count, 2)


class TestTheServiceStaysUp(_FirstCase):
    def test_the_launch_puts_it_up_first(self):
        src = Path(mainGUI.__file__).read_text(encoding="utf-8")
        body = src[src.index("    def _launch_vrchat(self):"):][:3000]
        self.assertIn("OSCQuery.ensure()", body)
        self.assertLess(body.index("OSCQuery.ensure()"), body.index("def worker():"), "VRChat を起動する前に")
        self.assertTrue(OSCQuery.ensure())
        service = OSCQuery.current()
        self.assertTrue(OSCQuery.ensure())
        self.assertIs(OSCQuery.current(), service, "2回目は立て直さない")

    def test_releasing_every_window_keeps_it(self):
        r = self._receiver(11, 9011, "窓1")
        r.start()
        service = OSCQuery.current()
        r.stop()
        self.assertIs(OSCQuery.current(), service)
        self.assertEqual(self.withdrawn, [])


class TestProcessName(unittest.TestCase):
    def test_the_name_of_a_pid(self):
        with patch.object(ProcessCheck, "_processes", return_value=(p for p in [(4, "System"), (4242, "ToNUtils.exe")])):
            self.assertEqual(ProcessCheck.process_name(4242), "ToNUtils.exe")
        with patch.object(ProcessCheck, "_processes", return_value=(p for p in [(4, "System")])):
            self.assertIsNone(ProcessCheck.process_name(4242))
        self.assertIsNone(ProcessCheck.process_name(0))
        with patch.object(ProcessCheck, "_processes", side_effect=OSError):
            self.assertIsNone(ProcessCheck.process_name(4242))
