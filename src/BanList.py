"""指定した VRChat の UID を締め出す。該当するアカウントが見つかったら、何も出さずにプロセスを終える。
終わらせれば、マクロ・VRChat の起動・スマホ通知・OSC・声・窓の操作がすべて止まる。
一覧は配布する exe に同梱する（元のファイルは gitignore。コミットしない）。
理由・UID はどこにも出さない（ログ・デバッグログ・画面・不具合の報告のどれにも）"""
import os

# 終わらせる処理。本番は即座に・捕まえられずに終わる。テストはこれを差し替える
_TERMINATE = os._exit


def load_banned(path=None) -> frozenset:
    """一覧のファイルを読む。1 行に 1 UID。空行と # で始まる行は無視。大文字小文字は区別しない。
    ファイルが無い・読めないときは空"""
    try:
        if path is None:
            import config
            path = config.BAN_UIDS_PATH
        with open(path, encoding="utf-8-sig") as f:
            text = f.read()
    except Exception:
        return frozenset()
    ids = set()
    for line in text.splitlines():
        line = line.strip().lower()
        if not line or line.startswith("#"):
            continue
        ids.add(line)
    return frozenset(ids)


def is_banned(user_id) -> bool:
    if not user_id:
        return False
    return user_id.strip().lower() in load_banned()


def enforce(user_id) -> None:
    """一覧にある UID なら、何も出さずに終わる（ログも print も出さない）"""
    if is_banned(user_id):
        _TERMINATE(0)


def enforce_startup() -> None:
    """起動時: 直近のログの UID を調べる。読めなくても起動は続ける"""
    try:
        import config
        import VRChatLauncher
        user_id = VRChatLauncher.latest_user_id(config.VRCHAT_LOG_DIR)
    except Exception:
        return
    enforce(user_id)
