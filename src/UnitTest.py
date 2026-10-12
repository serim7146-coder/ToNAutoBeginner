"""全テストを回す入口。テストの本体は tests/test_*.py、共通の準備は tests/support.py。

    python UnitTest.py                      # 全部（src で。ほかの場所からでも src へ移って回す）
    python UnitTest.py test_freezes         # tests/test_freezes.py だけ
    python -m unittest tests.test_fog       # 標準の書き方でも回せる（src で）
"""
import os
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main(argv) -> int:
    os.chdir(HERE)                  # ソースを読むテストは src からの相対パスで開く
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    # テスト名・メッセージは日本語。コンソールが cp932 のままだと化けるので UTF-8 で出す
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    loader = unittest.defaultTestLoader
    if argv:
        suite = unittest.TestSuite(loader.loadTestsFromName(f"tests.{name.removesuffix('.py')}")
                                   for name in argv)
    else:
        suite = loader.discover(str(HERE / "tests"), top_level_dir=str(HERE))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
