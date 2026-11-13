"""OSCQuery の値が、監視を始める前・固定ポートへ落ちた後に届く場合の受け方
S1 監視の前に届いた PID の窓は、start でそのまま OSCQuery に決まる（15 秒待たない・固定ポートを開かない）
S2 覚えは受信器 0 個の間に作られ、netstat の取り直しは間引き（PORTS_REFRESH_SEC）を超えない
S3 固定ポートで 1 通も受けていない窓（固定ポートで待つ・受け取れない）は、あとから OSCQuery で届いたら切り替える
S4 固定ポートで 1 通でも受けた窓・止めた窓は変えない
S5 HTTP の初回のログ・「届かない」の行の添え書き
本物の UDP・mDNS・netstat は使わない（tests/test_oscquery.py の _Case・tests/test_oscquery_first.py の _FirstCase）"""
from tests.support import *  # noqa: F401,F403
from tests.test_oscquery import _Case  # noqa: F401
from tests.test_oscquery_first import _FirstCase
import OSCClient
import OSCQuery
import OSCReceiver
import ProcessCheck

VEL = OSCReceiver.VELOCITY_MAGNITUDE


class _LateCase(_FirstCase):
    def setUp(self):
        super().setUp()
        self.exceptions = patch.object(DebugLog, "exception")
        self.exceptions_mock = self.exceptions.start()
        self.addCleanup(self.exceptions.stop)
        self.assertTrue(OSCQuery.ensure())
        self.service = OSCQuery.current()

    def lines(self, needle):
        return [c.args[0] for c in DebugLog.write.call_args_list if needle in c.args[0]]

    def _to_port(self, hwnd=11, port=9011, label="窓1"):
        r = self._receiver(hwnd, port, label)
        r.start()
        self.assertEqual(self._wait_mode(r), OSCReceiver.MODE_PORT)
        return r

    def _wait_speed(self, r):
        deadline = time.time() + 2
        while r.speed is None and time.time() < deadline:
            time.sleep(0.005)
        return r.speed


class TestSentBeforeTheWatch(_LateCase):
    """S1: 監視の前（受信器が 0 個）に届いていた窓は、待たずに OSCQuery に決まる"""

    def test_the_window_that_sent_earlier_is_decided_at_start(self):
        self.assertFalse(self.service.route(5001, VEL, 6.5), "受信器が無いので捨てる")
        self.assertTrue(self.service.has_sent(100))
        r = self._receiver(11, 9011, "窓1")
        with patch.object(config, "OSCQUERY_CONNECT_WAIT_SEC", 60):
            self.assertTrue(r.start())
        self.assertEqual(r.mode, OSCReceiver.MODE_OSCQUERY)
        self.assertIsNone(r._wait_thread, "15 秒待つスレッドを立てない")
        self.assertEqual(self.ports.binds, [], "固定ポートは開かない")
        self.assertEqual(self.lines("窓1: OSCQuery で受信（監視の前に届いていた・PID 100）。固定ポート 9011 は開きません"),
                         ["[速度の受信] 窓1: OSCQuery で受信（監視の前に届いていた・PID 100）。固定ポート 9011 は開きません"])
        self.assertTrue(self.service.route(5001, VEL, 6.5), "そのまま受ける")
        self.assertEqual(r.speed, 6.5)
        self.assertEqual(self.lines("固定ポート"), self.lines("監視の前に届いていた"), "ほかの切り替えのログは出ない")

    def test_another_window_that_did_not_send_still_waits(self):
        self.service.route(5001, VEL, 6.5)           # 窓1（PID 100）だけ届いていた
        b = self._receiver(22, 9021, "窓2")
        b.start()
        self.assertEqual(b.mode, OSCReceiver.MODE_WAITING)
        self.assertIsNotNone(b._wait_thread)
        self.assertEqual(self._wait_mode(b), OSCReceiver.MODE_PORT, "覚えに無ければ今までどおり 15 秒後に固定ポート")
        self.assertEqual(self.ports.binds, [("127.0.0.1", 9021)])

    def test_an_unknown_pid_is_not_taken_as_sent(self):
        self.service.route(5001, VEL, 6.5)
        self.assertFalse(self.service.has_sent(0))
        r = self._receiver(99, 9099, "窓9")          # PID が分からない窓（0）
        r.start()
        self.assertEqual(r.mode, OSCReceiver.MODE_WAITING)

    def test_a_stopped_window_is_not_decided(self):
        self.service.route(5001, VEL, 6.5)
        r = self._receiver(11, 9011, "窓1")
        r._running = True
        r.stop()
        self.assertFalse(r._decide_from_earlier_packets())
        self.assertEqual(r.mode, OSCReceiver.MODE_WAITING)


class TestTheMemoryIsMadeWhileNobodyWatches(_LateCase):
    """S2: 受信器が 0 個でも覚え、netstat は間引きを超えて起こさない"""

    def setUp(self):
        super().setUp()
        self.now = [1000.0]
        p = patch.object(OSCQuery.time, "time", side_effect=lambda: self.now[0])
        p.start()
        self.addCleanup(p.stop)
        self.netstat[300] = {5009}

    def test_one_lookup_for_a_flood_from_a_known_port(self):
        for _ in range(500):
            self.assertFalse(self.service.route(5001, VEL, 6.5))
        self.assertEqual(self.netstat_calls, 1, "表に載った送り元では取り直さない")
        self.assertTrue(self.service.has_sent(100))
        self.assertEqual(self.service.unknown, 0, "数えない")
        self.assertEqual(len(self.lines("受け取る窓がありません")), 1, "ログは 1 回")

    def test_an_unlisted_sender_waits_for_the_interval(self):
        self.service.route(5001, VEL, 6.5)
        self.assertEqual(self.netstat_calls, 1)
        self.netstat[200] = {5002, 6002}             # 窓2 の VRChat が別のポートから送り始めた
        for _ in range(50):
            self.service.route(6002, VEL, 6.5)
        self.assertEqual(self.netstat_calls, 1, "間引きの間は取り直さない")
        self.assertFalse(self.service.has_sent(200))
        self.now[0] += OSCQuery.PORTS_REFRESH_SEC + 0.1
        for _ in range(50):
            self.service.route(6002, VEL, 6.5)
        self.assertEqual(self.netstat_calls, 2, "間引きの後は 1 回だけ")
        self.assertTrue(self.service.has_sent(200))

    def test_a_port_that_never_appears_does_not_look_up_every_packet(self):
        for _ in range(100):
            self.service.route(7777, VEL, 6.5)       # どのプロセスも持っていない
        self.assertEqual(self.netstat_calls, 1)
        self.now[0] += 0.5
        for _ in range(100):
            self.service.route(7777, VEL, 6.5)
        self.assertEqual(self.netstat_calls, 1)
        self.now[0] += 1.0
        self.service.route(7777, VEL, 6.5)
        self.assertEqual(self.netstat_calls, 2)

    def test_a_sender_of_an_unwatched_vrchat_is_remembered_while_others_watch(self):
        """別の窓を監視している間に届いた、まだ監視していない VRChat も覚える"""
        a = self._receiver(11, 9011, "窓1")
        a.start()
        self.assertFalse(self.service.route(5009, VEL, 6.5))     # PID 300（窓の分からない送り元）
        self.assertEqual(self.service.unknown, 1)
        self.assertTrue(self.service.has_sent(300))
        self.pids[33] = 300
        c = self._receiver(33, 9031, "窓3")
        c.start()
        self.assertEqual(c.mode, OSCReceiver.MODE_OSCQUERY)

    def test_the_old_idle_rules_still_hold(self):
        """受信器が 0 個: 数えない・ログ 1 回（unknown を増やさない）"""
        for _ in range(20):
            self.service.route(5009, VEL, 6.5)
        self.assertEqual(self.service.unknown, 0)
        self.assertEqual(self.lines("窓の分からない送り元"), [])


class TestSwitchFromThePort(_LateCase):
    """S3: 固定ポートで 1 通も受けていない窓に OSCQuery で届いたら、固定ポートを閉じて切り替える"""

    def test_it_switches_closes_the_port_and_takes_the_value(self):
        r = self._to_port()
        sock = r._sock
        self.assertIsNotNone(sock)
        self.assertFalse(sock.closed)
        self.assertTrue(self.service.route(5001, VEL, 6.5))
        self.assertEqual(r.mode, OSCReceiver.MODE_OSCQUERY)
        self.assertTrue(sock.closed, "固定ポートのソケットを閉じる")
        self.assertIsNone(r._sock)
        self.assertEqual(r.speed, 6.5, "その値から受ける")
        self.assertTrue(r.ever_received)
        self.assertTrue(self.service.route(5001, VEL, 7.0))
        self.assertEqual(r.speed, 7.0)
        self.assertEqual(len(self.lines("OSCQuery で届いた")), 1, "切り替えのログは 1 回")
        self.assertIn("[速度の受信] 窓1: 固定ポート 9011 では 1 通も受けていないうちに OSCQuery で届いた（PID 100）"
                      "→ 固定ポートを閉じて OSCQuery で受信", self.lines("OSCQuery で届いた"))
        self.assertEqual(self.logs, [], "公開ログには出さない（受け取れていた窓の話ではない）")

    def test_the_port_thread_ends_quietly(self):
        r = self._to_port()
        self.service.route(5001, VEL, 6.5)
        time.sleep(0.3)                              # 固定ポートの _run が OSError で抜ける
        self.exceptions_mock.assert_not_called()
        self.assertEqual(self.ports.binds, [("127.0.0.1", 9011)], "開き直さない")
        r.stop()
        time.sleep(0.05)
        self.exceptions_mock.assert_not_called()

    def test_a_non_speed_packet_on_the_port_does_not_count_as_received(self):
        r = self._to_port()
        voice = OSCClient.OSCClient.build_message("/avatar/parameters/Voice", 0.5)
        self.ports.packets[9011] = [(voice, ("127.0.0.1", 50000))]
        time.sleep(0.1)
        self.assertEqual(self.ports.packets[9011], [], "読んだ")
        self.assertTrue(self.service.route(5001, VEL, 6.5), "速さ・接地として受けていないので切り替わる")
        self.assertEqual(r.mode, OSCReceiver.MODE_OSCQUERY)

    def test_grounded_on_the_port_counts_as_received(self):
        r = self._to_port()
        packet = OSCClient.OSCClient.build_message(OSCReceiver.GROUNDED, True)
        self.ports.packets[9011] = [(packet, ("127.0.0.1", 50000))]
        deadline = time.time() + 2
        while r.grounded_or_none is None and time.time() < deadline:
            time.sleep(0.005)
        self.assertFalse(self.service.route(5001, VEL, 6.5))
        self.assertEqual(r.mode, OSCReceiver.MODE_PORT)

    def test_a_window_received_on_the_port_is_not_switched(self):
        """S4: 固定ポートで 1 通でも受けた窓は変えない（OSCQuery で届いても捨てる）"""
        r = self._to_port()
        packet = OSCClient.OSCClient.build_message(VEL, 4.0)
        self.ports.packets[9011] = [(packet, ("127.0.0.1", 50000))]
        self.assertEqual(self._wait_speed(r), 4.0)
        sock = r._sock
        self.assertFalse(self.service.route(5001, VEL, 9.9))
        self.assertEqual(r.mode, OSCReceiver.MODE_PORT)
        self.assertEqual(r.speed, 4.0, "OSCQuery の値は反映しない")
        self.assertFalse(sock.closed)
        self.assertEqual(self.lines("OSCQuery で届いた"), [])

    def test_the_port_opened_while_switching_is_closed(self):
        """固定ポートを開く間に OSCQuery で届いて切り替わったら、開いたソケットは使わず閉じる"""
        r = self._receiver(11, 9011, "窓1")
        r._running = True
        opened = []
        original = self.ports.module.socket

        def opening(*a):
            sock = original(*a)
            opened.append(sock)
            real_bind = sock.bind

            def bind(addr):
                real_bind(addr)
                r.accept_oscquery()                 # bind の直後に OSCQuery で届いた（mode は PORT）
            sock.bind = bind
            return sock
        with patch.object(self.ports.module, "socket", opening):
            r.fall_back()
        self.assertEqual(r.mode, OSCReceiver.MODE_OSCQUERY)
        self.assertTrue(opened[0].closed)
        self.assertIsNone(r._sock)


class TestSwitchFromNone(_LateCase):
    """S3: 固定ポートをほかのアプリが使っていて受け取れない窓も、あとから OSCQuery で届いたら受ける"""

    def test_an_unable_window_receives_later(self):
        self.ports.busy.add(9031)
        with patch.object(OSCClient, "udp_ports_by_pid", return_value={4242: {9031}}), \
             patch.object(ProcessCheck, "process_name", return_value="ToNUtils.exe"):
            r = self._receiver(11, 9031, "窓4")
            r.start()
            self.assertEqual(self._wait_mode(r), OSCReceiver.MODE_NONE)
        self.assertEqual(self.logs, ["窓4: 速度を受け取れません（OSCQuery で届かず、ポート 9031 は ToNUtils.exe が使っています）"])
        self.assertTrue(self.service.route(5001, VEL, 6.5))
        self.assertEqual(r.mode, OSCReceiver.MODE_OSCQUERY)
        self.assertEqual(r.speed, 6.5)
        self.assertEqual(self.logs[1:], ["窓4: OSCQuery で受け取れるようになりました"])
        self.assertEqual(len(self.lines("受け取れなかったが OSCQuery で届いた")), 1)
        self.service.route(5001, VEL, 7.0)
        self.assertEqual(r.speed, 7.0)
        self.assertEqual(len(self.logs), 2, "1 回だけ")
        self.assertEqual(len(self.lines("受け取れなかったが OSCQuery で届いた")), 1)
        self.assertEqual(self.ports.binds, [])


class TestAStoppedReceiverDoesNothing(_LateCase):
    """S4: 止めた受信器は何も受けない・切り替えない"""

    def test_stopped_while_waiting(self):
        with patch.object(config, "OSCQUERY_CONNECT_WAIT_SEC", 60):
            r = self._receiver(11, 9011, "窓1")
            r.start()
        r.stop()
        self.assertFalse(r.accept_oscquery())
        self.assertFalse(self.service.route(5001, VEL, 6.5))
        self.assertEqual(r.mode, OSCReceiver.MODE_WAITING)
        self.assertIsNone(r.speed)

    def test_stopped_on_the_port(self):
        r = self._to_port()
        r.stop()
        self.assertFalse(r.accept_oscquery())
        self.assertEqual(r.mode, OSCReceiver.MODE_PORT)
        self.assertIsNone(r.speed)

    def test_stopped_while_unable(self):
        self.ports.busy.add(9031)
        r = self._receiver(11, 9031, "窓4")
        r.start()
        self._wait_mode(r, OSCReceiver.MODE_NONE)
        r.stop()
        self.assertFalse(r.accept_oscquery())
        self.assertEqual(r.mode, OSCReceiver.MODE_NONE)
        self.assertEqual(len(self.logs), 1)

    def test_a_oscquery_window_stopped_takes_nothing(self):
        r = self._receiver(11, 9011, "窓1")
        r.start()
        self.service.route(5001, VEL, 6.5)
        r.stop()
        self.assertFalse(r.accept_oscquery())


def _hit(service_handler, path):
    h = service_handler.__new__(service_handler)
    h.path, h.wfile, h.request_version, h.requestline = path, io.BytesIO(), "HTTP/1.1", ""
    h.command, h._headers_buffer, h.close_connection = "GET", [], True
    h.do_GET()
    return h


class TestTheHttpLog(_LateCase):
    """S5: HTTP の初回だけ 1 行・「届かない」の行に添え書き（判定には使わない）"""

    def test_the_first_access_is_logged_once(self):
        handler = self.https[0].handler
        self.assertFalse(self.service.http_seen)
        for path in ("/?HOST_INFO", "/", "/avatar", "/?HOST_INFO"):
            _hit(handler, path)
        self.assertTrue(self.service.http_seen)
        self.assertEqual(self.lines("HTTP を読みに来ました"),
                         ["[OSCQuery] VRChat が HTTP を読みに来ました（見つけてもらえた）"])

    def test_the_answer_is_not_changed(self):
        handler = self.https[0].handler
        h = _hit(handler, "/?HOST_INFO")
        body = json.loads(h.wfile.getvalue().split(b"\r\n\r\n", 1)[1])
        self.assertEqual(body["OSC_PORT"], self.service.osc_port)

    def test_the_fall_back_line_says_http_was_not_read(self):
        r = self._receiver(11, 9011, "窓1")
        r.start()
        self.assertEqual(self._wait_mode(r), OSCReceiver.MODE_PORT)
        self.assertEqual(self.lines("固定ポート 9011 で受信"),
                         ["[速度の受信] 窓1: OSCQuery で 0秒届かない（HTTP も読みに来ていない） → 固定ポート 9011 で受信"])

    def test_the_fall_back_line_says_http_was_read(self):
        _hit(self.https[0].handler, "/?HOST_INFO")
        r = self._receiver(11, 9011, "窓1")
        r.start()
        self.assertEqual(self._wait_mode(r), OSCReceiver.MODE_PORT)
        self.assertEqual(self.lines("固定ポート 9011 で受信"),
                         ["[速度の受信] 窓1: OSCQuery で 0秒届かない（HTTP は読みに来ていた） → 固定ポート 9011 で受信"])

    def test_http_does_not_decide_anything(self):
        """HTTP を読みに来ていても、届かなければ固定ポートへ（判定はしない）"""
        _hit(self.https[0].handler, "/")
        r = self._receiver(11, 9011, "窓1")
        r.start()
        self.assertEqual(self._wait_mode(r), OSCReceiver.MODE_PORT)

    def test_without_a_service_nothing_is_added(self):
        r = self._receiver(11, 9011, "窓1")
        r._running = True
        with patch.object(OSCQuery, "current", return_value=None):
            r.fall_back()
        self.assertEqual(self.lines("固定ポート 9011 で受信"),
                         ["[速度の受信] 窓1: OSCQuery で 0秒届かない → 固定ポート 9011 で受信"])

    def test_the_other_reason_has_no_note(self):
        r = self._receiver(11, 9011, "窓1")
        r._running = True
        r.fall_back(why="mDNS で公開されていない（OSCQuery では届かない）")
        self.assertEqual(self.lines("固定ポート 9011 で受信"),
                         ["[速度の受信] 窓1: mDNS で公開されていない（OSCQuery では届かない）→ 固定ポート 9011 で受信"])
