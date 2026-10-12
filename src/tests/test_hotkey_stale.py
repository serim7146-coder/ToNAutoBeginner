"""キーを設定すると「半角/全角」が勝手に入っていた（EL。依頼者 2026-10-13: 停止のキーが ctrl+p+半角/全角）。
本物のキーボードは使わない（keyboard.hook を偽物にする。名前の並べ方だけ本物の get_hotkey_name）"""
from tests.support import *  # noqa: F401,F403
import tests.test_item_fetch as _item_fetch_tests     # 読み込みの偽の画面を借りる（ここでは回さない）

HALF = "半角/全角"


class TestCapture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.real = _real_keyboard()

    def setUp(self):
        if self.real is None:
            self.skipTest("keyboard が入っていない")
        self.calls = {"hook": [], "unhook": []}
        p = patch.object(HotKey, "keyboard", self.real)
        p.start()
        self.addCleanup(p.stop)

    def _capture(self, events, timeout=2.0):
        """events（(down/up, 名前, スキャンコード)）を、捕捉を始めた後に流す"""
        calls = self.calls

        def hook(callback, suppress=False):
            calls["hook"].append(suppress)
            for kind, name, code in events:
                callback(type("E", (), {"event_type": kind, "name": name, "scan_code": code})())
            return "the-hook"
        with patch.object(self.real, "hook", hook), \
             patch.object(self.real, "unhook", lambda h: calls["unhook"].append(h)), \
             patch.object(self.real, "read_hotkey", side_effect=AssertionError("read_hotkey は使わない")):
            return HotKey.capture(timeout)

    def test_a_key_held_since_before_the_capture_is_not_included(self):
        """ライブラリの記録に「押されたまま」で残った 半角/全角 を入れない（その記録はもう見ない）"""
        with patch.dict(self.real._pressed_events, {41: type("E", (), {"name": HALF})()}):
            got = self._capture([("down", "ctrl", 29), ("down", "p", 25), ("up", "p", 25)])
        self.assertEqual(got, "ctrl+p")

    def test_ctrl_then_p_released_is_ctrl_p(self):
        self.assertEqual(self._capture([("down", "ctrl", 29), ("down", "p", 25), ("up", "p", 25)]), "ctrl+p")

    def test_the_order_is_get_hotkey_name(self):
        self.assertEqual(self._capture([("down", "p", 25), ("down", "shift", 42), ("down", "ctrl", 29),
                                        ("up", "ctrl", 29)]), "ctrl+shift+p")

    def test_half_width_pressed_during_the_capture_is_included(self):
        self.assertEqual(self._capture([("down", HALF, 41), ("up", HALF, 41)]), HALF)

    def test_a_release_of_a_key_pressed_before_does_not_end_it(self):
        """捕捉の前から押していたキーの「離した」は数えない（次に押して離したもので決める）"""
        self.assertEqual(self._capture([("up", HALF, 41), ("down", "f9", 67), ("up", "f9", 67)]), "f9")

    def test_a_held_key_repeating_is_one_key(self):
        self.assertEqual(self._capture([("down", "ctrl", 29), ("down", "ctrl", 29), ("down", "p", 25),
                                        ("up", "ctrl", 29)]), "ctrl+p")

    def test_a_key_already_released_during_the_capture_is_not_included(self):
        """最初に離した時点で決まる（その後のキーは入らない）"""
        self.assertEqual(self._capture([("down", "a", 30), ("up", "a", 30), ("down", "b", 48), ("up", "b", 48)]),
                         "a")

    def test_a_timeout_is_none_and_unhooks(self):
        self.assertIsNone(self._capture([("down", "ctrl", 29)], timeout=0.1))
        self.assertEqual(self.calls["unhook"], ["the-hook"])
        self.assertEqual(self.calls["hook"], [False], "suppress しない")

    def test_it_always_unhooks(self):
        self._capture([("down", "p", 25), ("up", "p", 25)])
        self.assertEqual(self.calls["unhook"], ["the-hook"])

    def test_an_error_in_the_callback_is_none_and_unhooks(self):
        with patch.object(self.real, "get_hotkey_name", side_effect=RuntimeError("boom")):
            self.assertIsNone(self._capture([("down", "p", 25), ("up", "p", 25)]))
        self.assertEqual(self.calls["unhook"], ["the-hook"])


class TestWithoutStale(unittest.TestCase):
    def test_it_is_removed(self):
        W = HotKey.without_stale
        self.assertEqual(W("ctrl+p+半角/全角"), ("ctrl+p", True))
        self.assertEqual(W("半角/全角+f9"), ("f9", True))
        self.assertEqual(W(HALF), (None, True), "何も残らなければ None（既定・未設定の扱いへ）")
        self.assertEqual(W("ctrl+p"), ("ctrl+p", False))
        self.assertEqual(W(""), ("", False))
        self.assertEqual(W(None), (None, False))

    def test_only_half_width(self):
        for other in ("変換", "無変換", "カタカナ/ひらがな"):
            self.assertEqual(HotKey.without_stale(f"ctrl+{other}"), (f"ctrl+{other}", False), other)
        self.assertEqual(HotKey.STALE_KEYS, (HALF,))


class TestTheSavedKeys(unittest.TestCase):
    """設定を読むとき、5 つのキーから 半角/全角 を外す。外したら画面のログに 1 回・保存"""

    def _load(self, data):
        saves = []
        real_load = _item_fetch_tests.TestStartKeySettings._load

        def load(loader, d, valid=True):
            # FakeApp に保存の予約を足して回す
            original = mainGUI.App._report_stale_keys

            def report(app, fields):
                app._schedule_settings_save = lambda: saves.append(1)
                original(app, fields)
            with patch.object(mainGUI.App, "_report_stale_keys", report):
                return real_load(loader, d, valid)
        app = load(_item_fetch_tests.TestStartKeySettings("test_it_is_saved"), data)
        app.saves = saves
        return app

    def test_the_stop_key(self):
        app = self._load({"emergency_stop_key": "ctrl+p+半角/全角"})
        self.assertEqual(app.v_emergency_key.get(), "ctrl+p")
        self.assertEqual([m for m in app.logs if "[キー]" in m],
                         ["[キー] 停止のキーから「半角/全角」を外しました（Ctrl+P）"])
        self.assertEqual(app.saves, [1], "外した結果を保存する")

    def test_only_half_width_falls_back_like_a_broken_value(self):
        app = self._load({"emergency_stop_key": HALF, "start_key": HALF, "suicide_cancel_key": HALF,
                          "chase_cw_key": HALF, "chase_ccw_key": HALF})
        self.assertEqual(app.v_emergency_key.get(), config.EMERGENCY_STOP_KEY, "停止は既定")
        self.assertEqual(app.v_start_key.get(), "", "マクロ開始は未設定")
        self.assertEqual(app.v_suicide_cancel_key.get(), config.SUICIDE_CANCEL_KEY, "自爆キャンセルは既定")
        self.assertEqual((app.v_chase_cw_key.get(), app.v_chase_ccw_key.get()),
                         (config.CHASE_CW_KEY, config.CHASE_CCW_KEY), "チェイスは既定")
        self.assertIn("[キー] マクロ開始のキーから「半角/全角」を外しました（未設定）", app.logs)
        self.assertEqual(app.saves, [1], "まとめて 1 回")

    def test_all_five_keys(self):
        app = self._load({"emergency_stop_key": "ctrl+p+半角/全角", "start_key": "f9+半角/全角",
                          "suicide_cancel_key": "f8+半角/全角", "chase_cw_key": "f6+半角/全角",
                          "chase_ccw_key": "f7+半角/全角"})
        self.assertEqual([app.v_emergency_key.get(), app.v_start_key.get(), app.v_suicide_cancel_key.get(),
                          app.v_chase_cw_key.get(), app.v_chase_ccw_key.get()], ["ctrl+p", "f9", "f8", "f6", "f7"])
        self.assertEqual([m for m in app.logs if "[キー]" in m], [
            "[キー] 停止のキーから「半角/全角」を外しました（Ctrl+P）",
            "[キー] マクロ開始のキーから「半角/全角」を外しました（F9）",
            "[キー] チェイス（時計回り）のキーから「半角/全角」を外しました（F6）",
            "[キー] チェイス（反時計回り）のキーから「半角/全角」を外しました（F7）",
            "[キー] 自爆キャンセルのキーから「半角/全角」を外しました（F8）"])
        self.assertEqual(app.saves, [1])

    def test_clean_keys_are_not_logged_or_saved(self):
        app = self._load({"emergency_stop_key": "ctrl+p", "start_key": "f9"})
        self.assertFalse([m for m in app.logs if "[キー]" in m])
        self.assertEqual(app.saves, [])
        self.assertEqual(app.v_emergency_key.get(), "ctrl+p")
