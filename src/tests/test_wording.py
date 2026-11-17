"""文言の決まり: 「手で」「手動」の比喩を使わない・画面に出る文言と README に「利用者」と書かない"""
from tests.support import *  # noqa: F401,F403
import ast
import re
from pathlib import Path

SRC = Path(mainGUI.__file__).parent
README = SRC.parent / "README.md"

_SCREEN_CALLS = {"_log", "_note", "showinfo", "showwarning", "showerror",
                 "askyesno", "askokcancel", "askretrycancel", "askquestion"}


def _sources():
    return sorted(SRC.glob("*.py")) + [README]


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
        """「手で」は、誰が・どの画面の・どの操作かが決まらない。具体語で書く"""
        hits = [f"{p.name}:{n}" for p in _sources()
                for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
                if "手で" in line]
        self.assertEqual(hits, [])

    def test_manual_is_only_the_launch_term(self):
        """「手動」は「手動起動」（利用者が立ち上げた VRChat の窓の呼び名）の形でだけ残る"""
        hits = [f"{p.name}:{n}" for p in _sources()
                for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
                if re.search(r"手動(?!起動)", line)]
        self.assertEqual(hits, [])

    def test_the_screen_text_has_no_third_person(self):
        """画面を読むのは本人。「利用者」と書くと三人称になるので、ログ・ダイアログ・README には書かない"""
        hits = [f"{p.name}:{n}" for p in sorted(SRC.glob("*.py"))
                for n, text in _screen_strings(p) if "利用者" in text]
        hits += [f"README.md:{n}" for n, line in
                 enumerate(README.read_text(encoding="utf-8").splitlines(), 1) if "利用者" in line]
        self.assertEqual(hits, [])
