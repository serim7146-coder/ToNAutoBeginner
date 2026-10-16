"""FS: OSCQuery のサービスの debug.log を抑える（実機 6 窓で毎秒 2,250 件届き、マクロを止めた後も続けて 1 分 867 行）。
C1 受け取る窓（受信器）が無いときは数えず・書かず・netstat も取り直さない
C2 窓の分からない送り元のログは最初の 1 件と 60 秒に 1 行
C3 サービスも速さ・接地だけ解析する（受信器側と同じ前方一致）
本物の UDP・mDNS は開かない（受け口・広告・netstat は偽物。tests/test_oscquery.py の _Case）"""
from tests.support import *  # noqa: F401,F403
from tests.test_oscquery import _Case
import OSCClient
import OSCQuery
import OSCReceiver

_UNKNOWN = "窓の分からない送り元"
_IDLE = "受け取る窓が無い"


def _pad(raw: bytes) -> bytes:
    return raw + b"\0" * (4 - len(raw) % 4)


def _osc(address: str, value) -> bytes:
    if isinstance(value, bool):
        return _pad(address.encode()) + _pad(b",T" if value else b",F")
    if isinstance(value, int):
        return _pad(address.encode()) + _pad(b",i") + struct.pack(">i", value)
    return _pad(address.encode()) + _pad(b",f") + struct.pack(">f", value)


class _Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


class _ServiceCase(_Case):
    def setUp(self):
        super().setUp()
        self.clock = _Clock()
        p = patch.object(OSCQuery.time, "time", side_effect=self.clock)
        p.start()
        self.addCleanup(p.stop)
        self.assertTrue(OSCQuery.ensure())
        self.service = OSCQuery.current()
        self.netstat[300] = {5009, 5010, 5011}      # 監視していない VRChat（窓の分からない送り元）

    def lines(self, needle):
        return [c.args[0] for c in DebugLog.write.call_args_list if needle in c.args[0]]

    def watch(self, pid=100):
        receiver = MagicMock(pid=pid)
        self.service.register(receiver)
        return receiver


class TestNobodyToReceive(_ServiceCase):
    """C1: 受信器が 0 のとき、届いたものは捨てるだけ"""

    def test_nothing_is_counted_or_looked_up(self):
        for _ in range(300):
            self.assertFalse(self.service.route(5009, OSCReceiver.VELOCITY_MAGNITUDE, 6.5))
        self.assertEqual(self.service.unknown, 0)
        self.assertEqual(self.netstat_calls, 0, "対応表（netstat）も取り直さない")
        self.assertEqual(self.lines(_UNKNOWN), [])

    def test_one_line_only_for_the_first(self):
        for _ in range(300):
            self.service.route(5009, OSCReceiver.VELOCITY_MAGNITUDE, 6.5)
        self.assertEqual(self.lines(_IDLE),
                         ["[OSCQuery] 受け取る窓が無い（監視していない）→ 届いたものは数えずに捨てる"])

    def test_it_is_said_again_each_time_it_becomes_zero(self):
        self.service.route(5009, OSCReceiver.VELOCITY_MAGNITUDE, 6.5)
        receiver = self.watch()
        self.service.route(5001, OSCReceiver.VELOCITY_MAGNITUDE, 6.5)      # 受信器あり（言わない）
        self.assertEqual(len(self.lines(_IDLE)), 1)
        self.assertEqual(self.service.unregister(receiver), 0)
        for _ in range(50):
            self.service.route(5009, OSCReceiver.VELOCITY_MAGNITUDE, 6.5)
        self.assertEqual(len(self.lines(_IDLE)), 2, "0 になるたびに 1 行")
        self.watch()
        self.assertFalse(self.service._idle_logged, "登録で印を消す")

    def test_a_registered_receiver_counts_again(self):
        self.service.route(5009, OSCReceiver.VELOCITY_MAGNITUDE, 6.5)
        self.assertEqual(self.service.unknown, 0)
        self.watch()
        self.service.route(5009, OSCReceiver.VELOCITY_MAGNITUDE, 6.5)
        self.assertEqual(self.service.unknown, 1)
        self.assertGreaterEqual(self.netstat_calls, 1)


class TestUnknownSendersAreLoggedEverySixtySeconds(_ServiceCase):
    """C2: 受信器はあるが窓の分からない送り元は、数えて捨てる。ログは最初の 1 件と 60 秒に 1 行"""

    def setUp(self):
        super().setUp()
        self.watch(100)                 # PID 100 の窓だけ監視（300 の VRChat は分からない送り元）

    def _send(self, count, start=0):
        for i in range(count):
            port = (5009, 5010, 5011)[(start + i) % 3]
            self.assertFalse(self.service.route(port, OSCReceiver.VELOCITY_MAGNITUDE, 6.5))

    def test_the_first_and_one_after_sixty_seconds_only(self):
        self._send(1)
        self.clock.now += 10
        self._send(149, start=0)
        self.assertEqual(len(self.lines(_UNKNOWN)), 1, "60 秒たつまで書かない")
        self.clock.now += 51            # 最初の行から 61 秒
        self._send(100)
        lines = self.lines(_UNKNOWN)
        self.assertEqual(lines, [
            "[OSCQuery] 窓の分からない送り元: この 60 秒で 1 件（5009（PID 300））→ 捨てた（累計 1 件）",
            "[OSCQuery] 窓の分からない送り元: この 60 秒で 150 件（5009（PID 300）など 3 つ）→ 捨てた（累計 151 件）"])
        self.assertEqual(self.service.unknown, 250)

    def test_the_count_starts_over_after_each_line(self):
        self._send(1)
        self.clock.now += 61
        self._send(1)
        self.clock.now += 30
        self._send(7)
        self.clock.now += 31
        self._send(1)
        self.assertEqual(self.lines(_UNKNOWN)[-1],
                         "[OSCQuery] 窓の分からない送り元: この 60 秒で 8 件（5009（PID 300）など 3 つ）"
                         "→ 捨てた（累計 10 件）")
        self.assertEqual(len(self.lines(_UNKNOWN)), 3)

    def test_thousands_have_commas(self):
        self._send(1)
        self.clock.now += 61
        self._send(1234)
        self.clock.now += 61
        self._send(1)
        self.assertIn("この 60 秒で 1,234 件", self.lines(_UNKNOWN)[-1])
        self.assertIn("累計 1,236 件", self.lines(_UNKNOWN)[-1])

    def test_a_watched_sender_is_not_unknown_and_time_alone_writes_nothing(self):
        self.assertTrue(self.service.route(5001, OSCReceiver.VELOCITY_MAGNITUDE, 6.5))      # 表を取り直す
        self.clock.now += 600
        self.assertTrue(self.service.route(5001, OSCReceiver.VELOCITY_MAGNITUDE, 6.5))
        self.assertEqual(self.service.unknown, 0)
        self.assertEqual(self.lines(_UNKNOWN), [], "分からない送り元が無ければ書かない（時間では書かない）")

    def test_the_stop_summary_is_the_total(self):
        self._send(1)
        self.clock.now += 61
        self._send(40)
        total = self.service.unknown
        DebugLog.write.reset_mock()
        OSCQuery.shutdown()
        self.assertEqual(len(self.lines(f"窓の分からない送り元 {total} 件")), 1)
        self.assertEqual(total, 41)

    def test_the_every_100_constant_is_gone(self):
        source = (SRC_DIR / "OSCQuery.py").read_text(encoding="utf-8")
        self.assertNotIn("UNKNOWN_LOG_EVERY", source)
        self.assertFalse(hasattr(OSCQuery, "UNKNOWN_LOG_EVERY"))
        self.assertEqual(OSCQuery.UNKNOWN_LOG_SEC, 60.0)


class _Packets:
    """recvfrom が決めた順にデータを返し、尽きたら OSError（受信ループが終わる）"""

    def __init__(self, datagrams, port=5001):
        self.datagrams = list(datagrams)
        self.port = port

    def recvfrom(self, _size):
        if not self.datagrams:
            raise OSError("closed")
        return self.datagrams.pop(0), ("127.0.0.1", self.port)


class TestOnlySpeedAndGroundedAreParsed(unittest.TestCase):
    """C3: サービスの受信ループも、先頭が速さ・接地のものだけ parse_message へ渡す"""

    def _run(self, datagrams):
        service = OSCQuery.Service()
        service._running = True
        service._sock = _Packets(datagrams)
        service.route = MagicMock(return_value=True)
        with patch.object(OSCReceiver, "parse_message", wraps=OSCReceiver.parse_message) as parse:
            service._run()
        return service, parse

    def test_other_addresses_are_dropped_before_parsing(self):
        others = [_osc("/avatar/parameters/Voice", 0.5), _osc("/avatar/parameters/VelocityX", 1.0),
                  _osc("/avatar/parameters/Upright", 1.0), _osc("/avatar/change", 0),
                  _osc("/avatar/parameters/IsLocal", True), b"#bundle\0" + b"\0" * 8, b"", b"\0\0\0\0"]
        service, parse = self._run(others)
        parse.assert_not_called()
        service.route.assert_not_called()

    def test_a_longer_name_that_starts_the_same_is_dropped(self):
        lookalikes = [_osc("/avatar/parameters/VelocityMagnitudeX", 9.0),
                      _osc("/avatar/parameters/VelocityMagnitude2", 9.0),
                      _osc("/avatar/parameters/GroundedX", 0.0), _osc("/avatar/parameters/Grounded_", True),
                      _osc("/avatar/parameters/VelocityMagnitud", 9.0)]
        service, parse = self._run(lookalikes)
        parse.assert_not_called()
        service.route.assert_not_called()

    def test_speed_and_grounded_still_go_to_route(self):
        service, parse = self._run([_osc("/avatar/parameters/Voice", 0.2),
                                    _osc("/avatar/parameters/VelocityMagnitude", 6.5),
                                    _osc("/avatar/parameters/Grounded", True),
                                    _osc("/avatar/parameters/Voice", 0.3),
                                    _osc("/avatar/parameters/Grounded", False)])
        self.assertEqual(parse.call_count, 3, "速さと接地の 3 件だけ")
        self.assertEqual(service.route.call_args_list,
                         [call(5001, "/avatar/parameters/VelocityMagnitude", 6.5),
                          call(5001, "/avatar/parameters/Grounded", 1.0),
                          call(5001, "/avatar/parameters/Grounded", 0.0)])

    def test_a_broken_speed_message_is_still_dropped_by_parse(self):
        service, parse = self._run([_pad(b"/avatar/parameters/VelocityMagnitude")])
        parse.assert_called_once()
        service.route.assert_not_called()

    def test_the_same_prefixes_as_the_receiver(self):
        source = (SRC_DIR / "OSCQuery.py").read_text(encoding="utf-8")
        self.assertIn("data.startswith(OSCReceiver._VEL_PREFIX) or data.startswith(OSCReceiver._GND_PREFIX)", source)
