"""入室の行（Joining）のインスタンス名: 数字だけでなくても読む（LogParser.RE_JOINING）

`:\\d+` だと、インスタンスの名前が数字だけでないとき（`wrld_…:myroom~private(…)`）に行が読めず、
入室を見失っていた。`:[^~\\s]+` にした。捕まえるグループ（ワールド ID・suffix）の数・順番・意味は変えない。
ワールド・UID は作り物
"""
from tests.support import *  # noqa: F401,F403


PREFIX = "2026.10.31 12:40:19 Debug      -  "
OLD_RE_JOINING = re.compile(r"\[Behaviour\] Joining (wrld_[^:]+):\d+(.*?)(?:~region\(|$)")
USER = "usr_00000000-0000-0000-0000-0000000000a1"


class TestJoiningInstanceName(unittest.TestCase):
    def _parse(self, text):
        return LogParser.parse(PREFIX + "[Behaviour] Joining " + text)

    def test_a_letter_name_is_read_with_the_same_suffix_shape(self):
        event = self._parse(f"wrld_xxx:myroom~private({USER})~region(jp)")
        self.assertEqual(event.kind, LogParser.EVENT_JOINING)
        self.assertEqual(event.suffix, f"~private({USER})")
        self.assertEqual(event.instance, f"wrld_xxx:myroom~private({USER})~region(jp)")
        self.assertEqual(LogParser.instance_access(event.suffix), LogParser.ACCESS_INVITE)

    def test_numeric_names_are_read_as_before(self):
        for text in (f"wrld_xxx:12345~private({USER})~region(jp)",
                     "wrld_xxx:12345~friends(usr_a)~region(us)",
                     "wrld_xxx:2~group(grp_x)~groupAccessType(plus)~region(jp)",
                     f"wrld_xxx:99999~hidden({USER})~region(eu)",
                     f"wrld_xxx:7~private({USER})~canRequestInvite~region(jp)",
                     "wrld_xxx:12345~region(jp)"):
            line = "[Behaviour] Joining " + text
            old = OLD_RE_JOINING.search(line)
            new = LogParser.RE_JOINING.search(line)
            self.assertEqual((new.group(1), new.group(2)), (old.group(1), old.group(2)), text)
            event = LogParser.parse(PREFIX + line)
            self.assertEqual((event.suffix, event.instance),
                             (old.group(2), line[old.start(1):].strip()), text)

    def test_public_lines_without_a_tilde_are_read(self):
        for text, instance in (("wrld_xxx:12345", "wrld_xxx:12345"),
                               ("wrld_xxx:myroom", "wrld_xxx:myroom")):
            event = self._parse(text)
            self.assertIsNotNone(event, text)
            self.assertEqual(event.kind, LogParser.EVENT_JOINING)
            self.assertEqual(event.suffix, "", text)
            self.assertEqual(event.instance, instance)
            self.assertEqual(LogParser.instance_access(event.suffix), LogParser.ACCESS_PUBLIC)

    def test_names_with_digits_letters_and_symbols_are_read(self):
        for name in ("abc123", "123abc", "my_room-2", "Room.Name", "あいう"):
            event = self._parse(f"wrld_xxx:{name}~friends(usr_a)~region(jp)")
            self.assertEqual((event.kind, event.suffix), (LogParser.EVENT_JOINING, "~friends(usr_a)"), name)

    def test_the_world_id_group_is_still_the_first(self):
        m = LogParser.RE_JOINING.search(f"[Behaviour] Joining wrld_xxx:myroom~private({USER})~region(jp)")
        self.assertEqual(m.group(1), "wrld_xxx")
        self.assertEqual(LogParser.RE_JOINING.groups, 2)

    def test_lines_that_are_not_an_entry_are_not_read(self):
        for text in ("[Behaviour] Joining or Creating Room: Terrors of Nowhere",
                     "[Behaviour] Joining wrld_xxx",
                     "[Behaviour] Joining wrld_xxx:",
                     "[Behaviour] Joining lbp_xxx:1"):
            event = LogParser.parse(PREFIX + text)
            self.assertFalse(event is not None and event.kind == LogParser.EVENT_JOINING, text)

    def test_the_launcher_reads_the_world_of_a_letter_named_instance(self):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
        tmp.write(PREFIX + f"[Behaviour] Joining wrld_xxx:myroom~private({USER})~region(jp)\n")
        tmp.close()
        self.addCleanup(os.unlink, tmp.name)
        self.assertEqual(VRChatLauncher.joined_world_id(tmp.name), "wrld_xxx")

    def test_the_monitor_detects_the_instance_of_a_letter_named_room(self):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
        tmp.write(PREFIX + f"[Behaviour] Joining wrld_xxx:myroom~private({USER})~region(jp)\n")
        tmp.close()
        self.addCleanup(os.unlink, tmp.name)
        monitor = LogMonitor.LogMonitor(WindowConfig(log_path=Path(tmp.name)), {},
                                        lambda _m: None, window_idx=1)
        monitor.logs = []
        monitor.logger = monitor.logs.append
        monitor._detect_instance_from_log()
        self.assertEqual(monitor.st.instance_id, f"wrld_xxx:myroom~private({USER})~region(jp)")
        self.assertEqual(monitor.st.instance_type, config.INSTANCE_PRIVATE)
        self.assertEqual(monitor.st.instance_access, LogParser.ACCESS_INVITE)


if __name__ == "__main__":
    unittest.main()
