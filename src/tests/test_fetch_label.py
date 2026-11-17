"""アイテム取得の画面のログは、番号ではなく名前で出す（依頼者 2026-11-17:
「#29を取りに行きましたという表記があり、これがあまり良くないです。アイテム名でお願いします」）。

テストの中では config.ITEMS が空なので、表は差し込んで確かめる"""
from tests.support import *  # noqa: F401,F403


ITEMS = {
    29: ItemCatalog.Item("Emerald Coil", "Survival", False),
    70: ItemCatalog.Item("Guidance Plush", "Event", True),
    18: ItemCatalog.Item("Bloody Kat Charm", "Enkephalin", False),
}


class TestFetchLabel(unittest.TestCase):
    def setUp(self):
        p = patch.object(config, "ITEMS", ITEMS)
        p.start()
        self.addCleanup(p.stop)

    def label(self, shop, item_id):
        return ActionExecutor.ActionExecutor._fetch_label(shop, item_id)

    def test_it_uses_the_name_from_the_table(self):
        self.assertEqual(self.label("Survival", 29), "Survival Shop の Emerald Coil")
        self.assertEqual(self.label("Event", 70), "Event Shop の Guidance Plush")
        self.assertEqual(self.label("Enkephalin", 18), "Enkephalin Shop の Bloody Kat Charm")

    def test_the_number_never_shows_when_the_name_is_known(self):
        for item_id in ITEMS:
            self.assertNotIn("#", self.label("Enkephalin", item_id), item_id)

    def test_an_unknown_number_falls_back_to_the_number(self):
        self.assertEqual(self.label("Event", 9999), "Event Shop の #9999")

    def test_an_empty_table_still_says_something(self):
        """item.json を読めなかったときも落ちない（番号で出る）"""
        with patch.object(config, "ITEMS", {}):
            self.assertEqual(self.label("Survival", 29), "Survival Shop の #29")

    def test_the_fetch_start_log_uses_the_label(self):
        """取りに行くときのログが _fetch_label を通っていること（直接 f文字列に戻さない）"""
        src = Path(ActionExecutor.__file__).read_text(encoding="utf-8")
        self.assertIn("name = self._fetch_label(shop, item_id)", src)
        self.assertNotIn('f"{shop} Shop の #{item_id}"', src)
