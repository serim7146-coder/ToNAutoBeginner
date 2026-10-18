"""mDNS（OSCQuery の名乗り）も 127.0.0.1 だけで開く（GB。ファイアウォールの確認を出さない）。
本物のソケットは開かない: zeroconf.Zeroconf は偽、zeroconf._utils.net.new_socket も記録用の偽に差し替える"""
from tests.support import *  # noqa: F401,F403

import config
import zeroconf
import zeroconf._utils.net as znet


class _Fixture(unittest.TestCase):
    def setUp(self):
        self.new_socket_calls = []       # 元の new_socket（記録用の偽）に届いた bind_addr
        self.zeroconf_calls = []         # (kwargs, 作っている間の znet.new_socket)
        self.inside = []                 # 偽の Zeroconf の中から new_socket(("",)) / (("10.0.0.1",)) を呼んだ結果
        self.raise_in_zeroconf = None
        test = self

        def fake_new_socket(bind_addr, *args, **kwargs):
            test.new_socket_calls.append(bind_addr)
            return "sock"

        class FakeZeroconf:
            def __init__(self, *args, **kwargs):
                test.zeroconf_calls.append((args, kwargs, znet.new_socket))
                test.inside.append(znet.new_socket(("",)))
                test.inside.append(znet.new_socket(("10.0.0.1",)))
                if test.raise_in_zeroconf is not None:
                    raise test.raise_in_zeroconf

            def register_service(self, _info, **_kwargs):
                pass

        self.fake_new_socket = fake_new_socket
        for p in (patch.object(znet, "new_socket", fake_new_socket),
                  patch.object(zeroconf, "Zeroconf", FakeZeroconf)):
            p.start()
            self.addCleanup(p.stop)

    def advertise(self):
        return _OscSocketTrap.real["advertise"]("ToNAutoBeginner-1234", 50124, 50123)


class TestLoopbackZeroconf(_Fixture):
    def test_zeroconf_is_made_on_the_loopback_interface_only(self):
        with patch.object(config, "OSCQUERY_MDNS_LOOPBACK_ONLY", True):
            self.advertise()
        self.assertEqual(len(self.zeroconf_calls), 1)
        _args, kwargs, _sock = self.zeroconf_calls[0]
        self.assertEqual(kwargs, {"ip_version": zeroconf.IPVersion.V4Only, "interfaces": ["127.0.0.1"]})

    def test_the_wildcard_bind_is_swapped_to_the_loopback_while_it_is_made(self):
        with patch.object(config, "OSCQUERY_MDNS_LOOPBACK_ONLY", True):
            self.advertise()
        self.assertIsNot(self.zeroconf_calls[0][2], self.fake_new_socket)       # 作っている間は差し替わっている
        self.assertEqual(self.new_socket_calls, [("127.0.0.1",), ("10.0.0.1",)])  # ("",) だけが 127.0.0.1 へ。他はそのまま
        self.assertEqual(self.inside, ["sock", "sock"])

    def test_the_wildcard_swap_keeps_the_rest_of_the_bind_address(self):
        """("", 5353) のように後ろがあれば残す。引数も元の関数へそのまま渡す"""
        def zc_with_extra(*_a, **_k):
            znet.new_socket(("", 5353), "ip", apple_p2p=True)
            return MagicMock()
        with patch.object(zeroconf, "Zeroconf", zc_with_extra), patch.object(config, "OSCQUERY_MDNS_LOOPBACK_ONLY", True):
            self.advertise()
        self.assertEqual(self.new_socket_calls, [("127.0.0.1", 5353)])

    def test_new_socket_is_put_back_after_it_is_made(self):
        with patch.object(config, "OSCQUERY_MDNS_LOOPBACK_ONLY", True):
            self.advertise()
        self.assertIs(znet.new_socket, self.fake_new_socket)

    def test_new_socket_is_put_back_even_when_zeroconf_raises(self):
        self.raise_in_zeroconf = OSError("bind failed")
        with patch.object(config, "OSCQUERY_MDNS_LOOPBACK_ONLY", True):
            with self.assertRaises(OSError):
                self.advertise()
        self.assertIs(znet.new_socket, self.fake_new_socket)

    def test_the_lock_is_released_so_the_next_one_can_be_made(self):
        self.raise_in_zeroconf = OSError("bind failed")
        with patch.object(config, "OSCQUERY_MDNS_LOOPBACK_ONLY", True):
            with self.assertRaises(OSError):
                self.advertise()
            self.raise_in_zeroconf = None
            self.advertise()
        self.assertFalse(OSCQuery._ADVERTISE_LOCK.locked())

    def test_the_flag_is_on_by_default(self):
        self.assertIs(config.OSCQUERY_MDNS_LOOPBACK_ONLY, True)

    def test_flag_off_neither_swaps_nor_passes_interfaces(self):
        with patch.object(config, "OSCQUERY_MDNS_LOOPBACK_ONLY", False):
            self.advertise()
        _args, kwargs, sock = self.zeroconf_calls[0]
        self.assertEqual(kwargs, {"ip_version": zeroconf.IPVersion.V4Only})
        self.assertIs(sock, self.fake_new_socket)                               # 差し替えていない
        self.assertEqual(self.new_socket_calls, [("",), ("10.0.0.1",)])

    def test_the_services_are_still_registered_with_the_loopback_address(self):
        with patch.object(config, "OSCQUERY_MDNS_LOOPBACK_ONLY", True):
            _zc, infos = self.advertise()
        self.assertEqual(sorted((i.type, i.port) for i in infos),
                         [("_osc._udp.local.", 50123), ("_oscjson._tcp.local.", 50124)])
        for info in infos:
            self.assertEqual(info.addresses, [socket.inet_aton("127.0.0.1")])


class TestAdvertisedLog(unittest.TestCase):
    def _log(self, flag):
        service = OSCQuery.Service()
        service._running = True
        with patch.object(OSCQuery, "_advertise", return_value=("zc", [])), \
                patch.object(config, "OSCQUERY_MDNS_LOOPBACK_ONLY", flag), \
                patch.object(DebugLog, "write") as write:
            service._do_advertise()
        lines = [c.args[0] for c in write.call_args_list if "mDNS で広告した" in c.args[0]]
        self.assertEqual(len(lines), 1)
        return lines[0]

    def test_the_log_says_it_waits_on_127_0_0_1_only(self):
        line = self._log(True)
        self.assertTrue(line.endswith("・127.0.0.1）（127.0.0.1 だけで待つ）"), line)

    def test_the_log_is_the_old_sentence_when_the_flag_is_off(self):
        line = self._log(False)
        self.assertTrue(line.endswith("・127.0.0.1）"), line)
        self.assertNotIn("だけで待つ", line)
