"""文言の決まり: 操作の主体をぼかす「手で」と、同じ意味の語を使わない・画面に出る文言と README に「利用者」と書かない"""
from tests.support import *  # noqa: F401,F403
import ast
from pathlib import Path

SRC = Path(mainGUI.__file__).parent
README = SRC.parent / "README.md"

_SCREEN_CALLS = {"_log", "_note", "showinfo", "showwarning", "showerror",
                 "askyesno", "askokcancel", "askretrycancel", "askquestion"}


def _sources():
    """走査の対象: src/*.py・src/tests/*.py・README.md・リポジトリ直下の build.py。この中に探す語そのものが書いてあるので、このファイル自身は除く"""
    me = Path(__file__).resolve()
    tests = [p for p in sorted((SRC / "tests").glob("*.py")) if p.resolve() != me]
    return sorted(SRC.glob("*.py")) + tests + [README, SRC.parent / "build.py"]


def _screen_strings(path):
    """self._log / self._note / messagebox に渡す文字列リテラル（f 文字列の固定部分も）。docstring・コメントは含まない"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr in _SCREEN_CALLS):
            continue
        for arg in list(node.args) + [k.value for k in node.keywords]:
            for sub in ast.walk(arg):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                    found.append((node.lineno, sub.value))
    return found


class TestWording(unittest.TestCase):
    def test_no_hand_metaphor_in_the_sources(self):
        """「手で」は、誰が・どの画面の・どの操作かが決まらない。具体語で書く。
        対象は操作の主体をぼかす「手で」だけ。「手に持った」はドメイン用語（ToN の Pickup）なので対象外"""
        hits = [f"{p.name}:{n}" for p in _sources()
                for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
                if "手で" in line]
        self.assertEqual(hits, [])

    def test_no_manual_in_the_sources(self):
        """操作の主体をぼかす語「手」＋「動」は 1 件も使わない。誰が操作するのかを具体語で書く"""
        word = "手" + "動"   # このファイル自身にその語を書かない
        hits = [f"{p.name}:{n}" for p in _sources()
                for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
                if word in line]
        self.assertEqual(hits, [])

    def test_the_screen_text_has_no_third_person(self):
        """画面を読むのは本人。「利用者」と書くと三人称になるので、ログ・ダイアログ・README には書かない"""
        hits = [f"{p.name}:{n}" for p in sorted(SRC.glob("*.py"))
                for n, text in _screen_strings(p) if "利用者" in text]
        hits += [f"README.md:{n}" for n, line in
                 enumerate(README.read_text(encoding="utf-8").splitlines(), 1) if "利用者" in line]
        self.assertEqual(hits, [])
