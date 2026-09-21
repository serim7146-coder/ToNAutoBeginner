"""obs-websocket v5 の最小クライアント（標準ライブラリだけで書く）。

使うのは GetRecordStatus / StartRecord / StopRecord の3つだけ。
WebSocket（RFC 6455）も自前で書く。新しい依存を足さないため。

失敗は例外にせず、戻り値で返す。呼び出し側（Recorder のワーカースレッド）が
OBS の状態でゲーム側の処理を止めないように。パスワードはログに出さない。
"""
import base64
import hashlib
import json
import os
import socket
import struct
import time

import config


WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"   # RFC 6455 の固定値
SUBPROTOCOL = "obswebsocket.json"

OP_TEXT = 0x1
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA
OP_CONTINUATION = 0x0

# obs-websocket の OpCode
OBS_HELLO = 0
OBS_IDENTIFY = 1
OBS_IDENTIFIED = 2
OBS_REQUEST = 6
OBS_REQUEST_RESPONSE = 7

MAX_MESSAGE_BYTES = 16 * 1024 * 1024     # 変なサーバーに巨大な長さを名乗られても抱え込まない


CLOSE_AUTHENTICATION_FAILED = 4009     # WebSocketCloseCode::AuthenticationFailed


class OBSError(Exception):
    """内部でだけ使う。外へは (False, 理由) で返す"""

    def __init__(self, message: str, close_code: int = 0):
        super().__init__(message)
        self.close_code = close_code


def auth_string(password: str, salt: str, challenge: str) -> str:
    """Identify に入れる authentication。

    base64(sha256(base64(sha256(password + salt)) + challenge))
    """
    secret = base64.b64encode(
        hashlib.sha256((password + salt).encode("utf-8")).digest()).decode("ascii")
    return base64.b64encode(
        hashlib.sha256((secret + challenge).encode("utf-8")).digest()).decode("ascii")


def encode_frame(payload: bytes, opcode: int = OP_TEXT, mask_key: bytes = None) -> bytes:
    """クライアントから送るフレーム。RFC 6455 の必須要件どおり必ずマスクする"""
    if mask_key is None:
        mask_key = os.urandom(4)
    head = bytes([0x80 | opcode])                   # FIN=1
    n = len(payload)
    if n <= 125:
        head += bytes([0x80 | n])
    elif n <= 0xFFFF:
        head += bytes([0x80 | 126]) + struct.pack("!H", n)
    else:
        head += bytes([0x80 | 127]) + struct.pack("!Q", n)
    masked = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
    return head + mask_key + masked


def read_frame(recv_exact) -> tuple[bool, int, bytes]:
    """フレームを1つ読む。(FIN, opcode, payload)。

    recv_exact(n) はちょうど n バイトを返す関数。サーバーのフレームは
    マスクされない決まりだが、されていても読めるようにしておく。
    """
    b0, b1 = recv_exact(2)
    fin = bool(b0 & 0x80)
    opcode = b0 & 0x0F
    masked = bool(b1 & 0x80)
    n = b1 & 0x7F
    if n == 126:
        (n,) = struct.unpack("!H", recv_exact(2))
    elif n == 127:
        (n,) = struct.unpack("!Q", recv_exact(8))
    if n > MAX_MESSAGE_BYTES:
        raise OBSError(f"フレームが大きすぎます（{n}バイト）")
    key = recv_exact(4) if masked else None
    payload = recv_exact(n) if n else b""
    if key:
        payload = bytes(b ^ key[i % 4] for i, b in enumerate(payload))
    return fin, opcode, payload


class OBSClient:
    """1回の接続。connect() → request() … → close()"""

    def __init__(self, host: str = config.OBS_DEFAULT_HOST,
                 port: int = config.OBS_DEFAULT_PORT, password: str = "",
                 timeout: float = config.OBS_TIMEOUT_SEC):
        self.host = host
        self.port = port
        self._password = password
        self.timeout = timeout
        self._sock = None
        self._buf = b""
        self._request_seq = 0
        self._deadline = 0.0

    def __repr__(self):
        # パスワードを repr に出さない（例外の表示やログに紛れ込まないように）
        return f"OBSClient({self.host}:{self.port})"

    # ── 公開 ───────────────────────────────
    def connect(self) -> tuple[bool, str]:
        """接続して Identified まで進める。(成功, 失敗理由)"""
        self._deadline = time.monotonic() + self.timeout * 2
        try:
            self._sock = socket.create_connection((self.host, self.port),
                                                  timeout=self.timeout)
            self._sock.settimeout(self.timeout)
            self._handshake()
            self._identify()
            return True, ""
        except Exception as e:
            self.close()
            return False, self._describe(e)

    def request(self, request_type: str, data: dict = None) -> tuple[bool, dict, str]:
        """リクエストを1つ送って応答を待つ。(成功, responseData, 失敗理由)"""
        if self._sock is None:
            return False, {}, "未接続です"
        self._deadline = time.monotonic() + self.timeout
        try:
            self._request_seq += 1
            request_id = f"tab-{self._request_seq}"
            d = {"requestType": request_type, "requestId": request_id}
            if data:
                d["requestData"] = data
            self._send_json({"op": OBS_REQUEST, "d": d})
            while True:
                msg = self._recv_json()
                if msg.get("op") != OBS_REQUEST_RESPONSE:
                    continue            # イベントなど。購読はしていないが念のため読み飛ばす
                rd = msg.get("d") or {}
                if rd.get("requestId") != request_id:
                    continue
                status = rd.get("requestStatus") or {}
                if status.get("result"):
                    return True, rd.get("responseData") or {}, ""
                comment = status.get("comment") or ""
                return False, {}, f"{request_type} が失敗（code {status.get('code')}）{comment}"
        except Exception as e:
            self.close()
            return False, {}, self._describe(e)

    def close(self):
        sock, self._sock = self._sock, None
        if sock is None:
            return
        try:
            sock.sendall(encode_frame(struct.pack("!H", 1000), OP_CLOSE))
        except Exception:
            pass
        try:
            sock.close()
        except Exception:
            pass

    # ── WebSocket ──────────────────────────────
    def _handshake(self):
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        req = (f"GET / HTTP/1.1\r\n"
               f"Host: {self.host}:{self.port}\r\n"
               "Upgrade: websocket\r\n"
               "Connection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\n"
               "Sec-WebSocket-Version: 13\r\n"
               f"Sec-WebSocket-Protocol: {SUBPROTOCOL}\r\n"
               "\r\n")
        self._sock.sendall(req.encode("ascii"))
        while b"\r\n\r\n" not in self._buf:
            self._fill()
            if len(self._buf) > 65536:
                raise OBSError("ハンドシェイクの応答が長すぎます")
        head, self._buf = self._buf.split(b"\r\n\r\n", 1)
        lines = head.decode("latin-1").split("\r\n")
        if len(lines[0].split()) < 2 or lines[0].split()[1] != "101":
            raise OBSError(f"WebSocketに切り替わりません（{lines[0]}）")
        headers = {}
        for line in lines[1:]:
            name, _sep, value = line.partition(":")
            headers[name.strip().lower()] = value.strip()
        expected = base64.b64encode(
            hashlib.sha1((key + WS_GUID).encode("ascii")).digest()).decode("ascii")
        if headers.get("sec-websocket-accept") != expected:
            raise OBSError("WebSocketの応答が不正です（Sec-WebSocket-Accept）")

    def _fill(self):
        # 1回の recv にはタイムアウトが付くが、少しずつ送り続けるサーバーだと
        # いつまでも戻らない。操作ごとの締め切りでも切る
        if time.monotonic() > self._deadline:
            raise socket.timeout()
        chunk = self._sock.recv(65536)
        if not chunk:
            raise OBSError("OBSが接続を閉じました")
        self._buf += chunk

    def _recv_exact(self, n: int) -> bytes:
        while len(self._buf) < n:
            self._fill()
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def _send_json(self, obj: dict):
        self._sock.sendall(encode_frame(json.dumps(obj).encode("utf-8")))

    def _recv_json(self) -> dict:
        """テキストメッセージを1つ受け取る。ping には pong、close なら閉じる"""
        parts = []
        while True:
            fin, opcode, payload = read_frame(self._recv_exact)
            if opcode == OP_PING:
                self._sock.sendall(encode_frame(payload, OP_PONG))
                continue
            if opcode == OP_PONG:
                continue
            if opcode == OP_CLOSE:
                code = struct.unpack("!H", payload[:2])[0] if len(payload) >= 2 else 0
                reason = payload[2:].decode("utf-8", "replace")
                raise OBSError(f"OBSが接続を閉じました（{code} {reason}".rstrip() + "）",
                               close_code=code)
            if opcode not in (OP_TEXT, OP_CONTINUATION):
                raise OBSError(f"想定外のフレームです（opcode {opcode}）")
            parts.append(payload)
            if sum(len(p) for p in parts) > MAX_MESSAGE_BYTES:
                raise OBSError("メッセージが大きすぎます")
            if fin:
                return json.loads(b"".join(parts).decode("utf-8"))

    # ── obs-websocket ────────────────────────────
    def _identify(self):
        hello = self._recv_json()
        if hello.get("op") != OBS_HELLO:
            raise OBSError("OBSからHelloが届きません")
        d = hello.get("d") or {}
        identify = {"rpcVersion": 1, "eventSubscriptions": 0}   # イベントは要らない
        auth = d.get("authentication")
        if auth:
            if not self._password:
                raise OBSError("OBSにパスワードが設定されています（こちらのパスワードが空です）")
            identify["authentication"] = auth_string(
                self._password, auth.get("salt", ""), auth.get("challenge", ""))
        self._send_json({"op": OBS_IDENTIFY, "d": identify})
        msg = self._recv_json()
        if msg.get("op") != OBS_IDENTIFIED:
            raise OBSError("Identified が返りません")

    @staticmethod
    def _describe(e: Exception) -> str:
        if isinstance(e, socket.timeout):
            return "OBSが応答しません（タイムアウト）"
        if isinstance(e, ConnectionRefusedError):
            return "OBSに接続できません（OBSが起動していないか、WebSocketサーバーが無効です）"
        if isinstance(e, OBSError):
            if e.close_code == CLOSE_AUTHENTICATION_FAILED:
                return "OBSの認証に失敗しました（パスワードが違います）"
            return str(e)
        return f"OBSとの通信に失敗しました（{type(e).__name__}: {e}）"
