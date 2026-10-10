"""速さの受信を OSCQuery にする（DN）: 固定のポートは開かず、ツールに1つのサービスを立てて
mDNS で広告し、送り元のポート → PID → 窓で振り分ける"""
from tests.support import *  # noqa: F401,F403
import socket as real_socket


class _Sock:
    """偽の UDP の受け口（届く値を順に返す）"""

    def __init__(self, port=50123, packets=()):
        self.port = port
        self.packets = list(packets)
        self.closed = False

    def getsockname(self):
        return ("127.0.0.1", self.port)

    def recvfrom(self, _size):
        if self.packets:
            return self.packets.pop(0)
        if self.closed:
            raise OSError("closed")
        time.sleep(0.01)
        raise real_socket.timeout()

    def close(self):
        self.closed = True


class _Http:
    server_address = ("127.0.0.1", 50124)

    def __init__(self, handler=None):
        self.handler = handler
        self.stopped = False

    def serve_forever(self):
        pass

    def shutdown(self):
        self.stopped = True

    def server_close(self):
        pass


class _Case(unittest.TestCase):
    def setUp(self):
        self.adverts, self.withdrawn, self.https = [], [], []
        self.sock = _Sock()

        def http(handler):
            server = _Http(handler)
            self.https.append(server)
            return server

        def advertise(name, http_port, osc_port):
            self.adverts.append((name, http_port, osc_port))
            return ("zc", name)
        self.pids = {11: 100, 22: 200}              # hwnd → PID
        self.netstat = {100: {5001}, 200: {5002}}   # PID → 持っている UDP ポート
        self.netstat_calls = 0

        def netstat():
            self.netstat_calls += 1
            return {pid: set(ports) for pid, ports in self.netstat.items()}
        for p in (patch.object(OSCQuery, "_udp_socket", side_effect=lambda: self.sock),
                  patch.object(OSCQuery, "_http_server", side_effect=http),
                  patch.object(OSCQuery, "_advertise", side_effect=advertise),
                  patch.object(OSCQuery, "_withdraw", side_effect=self.withdrawn.append),
                  patch.object(OSCQuery, "_pid_of_window", side_effect=lambda h: self.pids.get(h, 0)),
                  patch.object(OSCQuery.OSCClient, "udp_ports_by_pid", side_effect=netstat),
                  patch.object(DebugLog, "write")):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(OSCQuery.shutdown)

    def _wait_advertised(self):
        deadline = time.time() + 2
        service = OSCQuery.current()
        while service is not None and service._advert is None and time.time() < deadline:
            time.sleep(0.005)


class TestTheAnswers(unittest.TestCase):
    def test_host_info(self):
        status, info = OSCQuery.respond("/?HOST_INFO", "ToNAutoBeginner-1234", 50123)
        self.assertEqual(status, 200)
        self.assertEqual((info["NAME"], info["OSC_IP"], info["OSC_PORT"], info["OSC_TRANSPORT"]),
                         ("ToNAutoBeginner-1234", "127.0.0.1", 50123, "UDP"))

    def test_the_tree_has_avatar(self):
        status, root = OSCQuery.respond("/", "x", 1)
        self.assertEqual(status, 200)
        self.assertEqual(root["FULL_PATH"], "/")
        avatar = root["CONTENTS"]["avatar"]
        self.assertEqual(avatar["FULL_PATH"], "/avatar")
        params = avatar["CONTENTS"]["parameters"]["CONTENTS"]
        self.assertEqual(params["VelocityMagnitude"]["FULL_PATH"], OSCReceiver.VELOCITY_MAGNITUDE)
        self.assertEqual((params["VelocityMagnitude"]["TYPE"], params["VelocityMagnitude"]["ACCESS"]), ("f", 2))
        self.assertEqual(params["Grounded"]["FULL_PATH"], OSCReceiver.GROUNDED)

    def test_a_node_and_a_missing_one(self):
        self.assertEqual(OSCQuery.respond("/avatar", "x", 1)[1]["FULL_PATH"], "/avatar")
        self.assertEqual(OSCQuery.respond("/avatar/parameters/VelocityMagnitude", "x", 1)[1]["TYPE"], "f")
        self.assertEqual(OSCQuery.respond("/nothing", "x", 1)[0], 404)


class TestTheService(_Case):
    def test_it_listens_on_free_ports_and_answers_with_them(self):
        receiver = OSCReceiver.VelocityReceiver(11)
        self.assertTrue(receiver.start())
        self._wait_advertised()
        service = OSCQuery.current()
        self.assertEqual((service.osc_port, service.http_port), (50123, 50124))
        self.assertTrue(service.name.startswith("ToNAutoBeginner-"))
        self.assertEqual(self.adverts, [(service.name, 50124, 50123)])
        # HTTP の答え（偽の要求で本物のハンドラーを動かす）
        handler = self.https[0].handler
        for path, key, value in (("/?HOST_INFO", "OSC_PORT", 50123), ("/", "FULL_PATH", "/")):
            h = handler.__new__(handler)
            h.path, h.wfile, h.request_version, h.requestline = path, io.BytesIO(), "HTTP/1.1", ""
            h.command, h._headers_buffer, h.close_connection = "GET", [], True
            h.do_GET()
            body = json.loads(h.wfile.getvalue().split(b"\r\n\r\n", 1)[1])
            self.assertEqual(body[key], value, path)
            if path == "/":
                self.assertIn("avatar", body["CONTENTS"])

    def test_one_service_for_all_windows_and_it_stops_with_the_last(self):
        first, second = OSCReceiver.VelocityReceiver(11), OSCReceiver.VelocityReceiver(22)
        self.assertTrue(first.start())
        self.assertTrue(second.start())
        self._wait_advertised()
        self.assertEqual(len(self.https), 1, "ツールに1つ")
        service = OSCQuery.current()
        first.stop()
        self.assertIs(OSCQuery.current(), service, "残りの窓がいる間は止めない")
        self.assertEqual(self.withdrawn, [])
        second.stop()
        self.assertIsNone(OSCQuery.current())
        self.assertEqual(self.withdrawn, [("zc", service.name)], "広告を取り下げた")
        self.assertTrue(self.sock.closed)
        self.assertTrue(self.https[0].stopped)

    def test_closing_the_tool_stops_it(self):
        OSCReceiver.VelocityReceiver(11).start()
        self._wait_advertised()
        OSCQuery.shutdown()
        self.assertIsNone(OSCQuery.current())
        self.assertEqual(len(self.withdrawn), 1)
        src = Path(mainGUI.__file__).read_text(encoding="utf-8")
        body = src[src.index("    def _on_close(self):"):][:1500]
        self.assertIn("OSCQuery.shutdown()", body)

    def test_a_failed_start_is_reported(self):
        with patch.object(OSCQuery, "_udp_socket", side_effect=OSError("busy")):
            receiver = OSCReceiver.VelocityReceiver(11)
            self.assertFalse(receiver.start())
        self.assertIsNone(OSCQuery.current())
        self.assertEqual(self.adverts, [])

    def test_the_monitor_start_and_stop(self):
        monitor = LogMonitor.LogMonitor(WindowConfig(hwnd=11, osc_port=9010), {}, lambda _m: None,
                                        window_idx=1)
        monitor._start_daemon = lambda *a: None         # 監視のスレッドは動かさない
        monitor.start()
        self.assertIsNotNone(OSCQuery.current())
        self._wait_advertised()
        monitor.stop()
        self.assertIsNone(OSCQuery.current(), "監視を止めたら止める")
        self.assertEqual(len(self.withdrawn), 1)


class TestFixedPortsAreNotOpened(unittest.TestCase):
    """本物の入口が開くのは 127.0.0.1 の空いているポート（0）だけ。広告するアドレスも 127.0.0.1"""

    def test_udp(self):
        binds = []
        fake = type(sys)("socket_fake")
        fake.AF_INET, fake.SOCK_DGRAM = real_socket.AF_INET, real_socket.SOCK_DGRAM
        fake.socket = lambda *a: MagicMock(bind=binds.append)
        with patch.object(OSCQuery, "socket", fake):
            _OscSocketTrap.real["udp"]()
        self.assertEqual(binds, [("127.0.0.1", 0)])

    def test_http(self):
        with patch.object(OSCQuery, "ThreadingHTTPServer") as server:
            _OscSocketTrap.real["http"]("handler")
        server.assert_called_once_with(("127.0.0.1", 0), "handler")

    def test_mdns(self):
        import zeroconf
        with patch.object(zeroconf, "Zeroconf") as zc:
            _zc, infos = _OscSocketTrap.real["advertise"]("ToNAutoBeginner-1234", 50124, 50123)
        types = sorted((i.type, i.port) for i in infos)
        self.assertEqual(types, [("_osc._udp.local.", 50123), ("_oscjson._tcp.local.", 50124)])
        for info in infos:
            self.assertEqual(info.addresses, [real_socket.inet_aton("127.0.0.1")])
            self.assertTrue(info.name.startswith("ToNAutoBeginner-1234."))
        self.assertEqual(zc.return_value.register_service.call_count, 2)

    def test_the_old_fixed_bind_is_gone(self):
        src = Path(OSCReceiver.__file__).read_text(encoding="utf-8")
        self.assertNotIn(".bind(", src)
        ae = Path(ActionExecutor.__file__).read_text(encoding="utf-8")
        self.assertNotIn("osc_out_port or self._cfg.osc_port + 1", ae)


class TestRouting(_Case):
    """送り元のポート → PID → 窓。2 窓の値が混ざらない"""

    def _service(self):
        a, b = OSCReceiver.VelocityReceiver(11), OSCReceiver.VelocityReceiver(22)
        a.start()
        b.start()
        self._wait_advertised()
        return OSCQuery.current(), a, b

    def test_two_windows_do_not_mix(self):
        service, a, b = self._service()
        self.assertTrue(service.route(5001, OSCReceiver.VELOCITY_MAGNITUDE, 6.5))
        self.assertTrue(service.route(5002, OSCReceiver.VELOCITY_MAGNITUDE, 4.0))
        self.assertTrue(service.route(5002, OSCReceiver.GROUNDED, 0.0))
        self.assertEqual((a.speed, b.speed), (6.5, 4.0))
        self.assertEqual((a.grounded, b.grounded), (True, False))
        self.assertTrue(a.ever_received and b.ever_received)

    def test_the_receive_loop_routes(self):
        packet = OSCClient.OSCClient.build_message(OSCReceiver.VELOCITY_MAGNITUDE, 6.5)
        service, a, b = self._service()
        self.sock.packets.append((packet, ("127.0.0.1", 5002)))     # 2 窓とも登録した後に届く
        deadline = time.time() + 2
        while b.speed is None and time.time() < deadline:
            time.sleep(0.005)
        self.assertEqual((a.speed, b.speed), (None, 6.5))

    def test_an_unknown_sender_is_looked_up_again_then_dropped(self):
        service, a, b = self._service()
        service._refreshed_at = 0.0
        calls = self.netstat_calls
        self.netstat[300] = {5009}                  # 監視していない窓（ほかの VRChat）
        self.assertFalse(service.route(5009, OSCReceiver.VELOCITY_MAGNITUDE, 6.6))
        self.assertEqual(self.netstat_calls, calls + 1, "取り直した")
        self.assertFalse(service.route(5009, OSCReceiver.VELOCITY_MAGNITUDE, 6.6))
        self.assertEqual(self.netstat_calls, calls + 1, "続けては取り直さない（間隔を空ける）")
        self.assertEqual(service.unknown, 2)
        self.assertEqual((a.speed, b.speed), (None, None))

    def test_a_sender_of_another_vrchat_does_not_look_up_every_second(self):
        """監視していない VRChat が送り続けても、毎秒は取り直さない（30 秒空ける）"""
        service, a, b = self._service()
        self.netstat[300] = {5009}
        service._refreshed_at = 0.0
        self.assertFalse(service.route(5009, OSCReceiver.VELOCITY_MAGNITUDE, 6.5))
        calls = self.netstat_calls
        later = time.time() + OSCQuery.PORTS_REFRESH_SEC + 0.1
        with patch.object(OSCQuery.time, "time", return_value=later):
            self.assertFalse(service.route(5009, OSCReceiver.VELOCITY_MAGNITUDE, 6.5))
        self.assertEqual(self.netstat_calls, calls, "表にある送り元は 1 秒では取り直さない")
        self.pids[33] = 300                          # その VRChat の窓も監視し始めた
        c = OSCReceiver.VelocityReceiver(33)
        c.start()
        self.assertTrue(service.route(5009, OSCReceiver.VELOCITY_MAGNITUDE, 6.5), "表のまま届く")
        self.netstat[300] = {7009}                  # 立ち上げ直してポートが変わった
        self.netstat[400] = {5009}                  # 古いポートを別のプロセスが使う
        with patch.object(OSCQuery.time, "time", return_value=time.time() + OSCQuery.PORTS_RECHECK_SEC + 1):
            self.assertTrue(service.route(7009, OSCReceiver.VELOCITY_MAGNITUDE, 6.5))
        self.assertEqual(c.speed, 6.5)
        c.stop()

    def test_a_known_port_taken_over_by_a_watched_window_is_found_after_the_recheck(self):
        """表では監視していない VRChat のポートだったが、立ち上げ直して窓2 の VRChat が使い始めた"""
        service, a, b = self._service()
        self.netstat[300] = {5009}
        service._refreshed_at = 0.0
        self.assertFalse(service.route(5009, OSCReceiver.VELOCITY_MAGNITUDE, 6.5))
        del self.netstat[300]
        self.netstat[200] = {5002, 5009}
        with patch.object(OSCQuery.time, "time", return_value=time.time() + OSCQuery.PORTS_RECHECK_SEC + 1):
            self.assertTrue(service.route(5009, OSCReceiver.VELOCITY_MAGNITUDE, 6.5))
        self.assertEqual(b.speed, 6.5)

    def test_a_new_sender_is_found_after_looking_up_again(self):
        service, a, b = self._service()
        self.netstat[200] = {5002, 6002}            # 窓2 の VRChat が別のポートから送り始めた
        service._refreshed_at = 0.0
        self.assertTrue(service.route(6002, OSCReceiver.VELOCITY_MAGNITUDE, 6.5))
        self.assertEqual(b.speed, 6.5)

    def test_the_lookup_waits_for_the_interval(self):
        service, a, b = self._service()
        self.netstat[200] = {5002, 6002}
        service._refreshed_at = time.time()
        self.assertFalse(service.route(6002, OSCReceiver.VELOCITY_MAGNITUDE, 6.5))
        with patch.object(OSCQuery.time, "time", return_value=time.time() + OSCQuery.PORTS_REFRESH_SEC + 0.1):
            self.assertTrue(service.route(6002, OSCReceiver.VELOCITY_MAGNITUDE, 6.5))


class TestOnlyMoveWhenSpeedsArrive(unittest.TestCase):
    """受け取れていない窓では横移動しない・「速度検知 開始」と言わない（受け取れたら今どおり）"""

    def setUp(self):
        SharedState.set_speed_detect(True)
        self.addCleanup(SharedState.set_speed_detect, config.SPEED_DETECT_ENABLED)

    def _executor(self, received):
        st = WindowState(instance_type=config.INSTANCE_PRIVATE)
        ex = ActionExecutor.ActionExecutor(WindowConfig(hwnd=11, osc_port=9000), st, lambda: True,
                                           lambda _m: None)
        ex._speed_ready.set()
        ex._receiver = None if received is None else MagicMock(ever_received=received)
        return ex

    def test_the_strafe(self):
        for received, moves in ((None, []), (False, []), (True, ["right", "left"])):
            ex = self._executor(received)
            got = []
            with patch.object(ex, "move", side_effect=lambda d, s: got.append(d)):
                ex.do_speed_strafe()
            self.assertEqual(got, moves, received)

    def test_the_announcement(self):
        for received, said in ((None, False), (False, False), (True, True)):
            monitor = LogMonitor.LogMonitor(WindowConfig(hwnd=11, osc_port=9000), {}, lambda _m: None,
                                            window_idx=1)
            monitor.st.instance_type = config.INSTANCE_PRIVATE
            monitor._action._receiver = None if received is None else MagicMock(ever_received=received)
            logs = []
            monitor.logger = logs.append
            started = []
            monitor._start_daemon = lambda target, *a: started.append(target.__name__)
            monitor._start_speed_probe()
            self.assertEqual(any("速度検知 開始" in m for m in logs), said, received)
            self.assertEqual(started, ["do_speed_detect"], "判定は今どおり動かす（届かなければ今の警告）")


class TestTheDependency(unittest.TestCase):
    def test_zeroconf_is_listed_and_built_in(self):
        root = Path(mainGUI.__file__).parent.parent
        self.assertIn("zeroconf", (root / "requirements.txt").read_text(encoding="utf-8"))
        build = _load_build_script()
        self.assertIn("--include-package=zeroconf", build.BASE_ARGS)
        self.assertIn("--include-package=ifaddr", build.BASE_ARGS)
