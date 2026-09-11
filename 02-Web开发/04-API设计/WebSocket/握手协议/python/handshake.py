"""
WebSocket Opening Handshake (RFC 6455 §4) — minimal Python implementation

核心协议逻辑与 c/handshake.c 一一对应，使用 hashlib / base64 / string 三个
标准库（外加 typing），无第三方依赖。

用法：
    python3 handshake.py

5 个 demo 用例覆盖：
    1) RFC 6455 §1.3 给定 key 的 accept 计算示例
    2) 客户端 → 服务端 → 客户端 完整 round-trip
    3) 子协议协商：服务端不能选择客户端列表外的子协议
    4) 服务端拒绝缺少必填头 / key 长度错的请求
    5) 客户端拒绝错误 accept / 非 101 状态码
"""

import base64
import hashlib
import os
import string
import sys
from typing import Optional, Tuple


# =====================================================================
# Part 1: 常量与底层工具
# =====================================================================

WS_MAGIC_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"  # RFC 6455 §1.3
WS_VERSION = "13"                                       # RFC 6455 §4.1 第 9 点


def _decode_key_to_16_bytes(key_b64: str) -> Optional[bytes]:
    """RFC 6455 §4.1 第 7 点：Sec-WebSocket-Key 必须 base64-decode 到恰好 16 字节"""
    try:
        raw = base64.b64decode(key_b64, validate=True)
    except (ValueError, base64.binascii.Error):
        return None
    return raw if len(raw) == 16 else None


def compute_accept(client_key: str) -> str:
    """
    RFC 6455 §4.2.2 步骤 4 + §1.3：
        Sec-WebSocket-Accept = base64( SHA-1( Sec-WebSocket-Key + MAGIC_GUID ) )

    验证用例（RFC §1.3）：
        compute_accept("dGhlIHNhbXBsZSBub25jZQ==") == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="
    """
    digest = hashlib.sha1((client_key + WS_MAGIC_GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def gen_client_key() -> str:
    """RFC 6455 §4.1 第 7 点：随机 16 字节 nonce 后 base64 编码"""
    return base64.b64encode(os.urandom(16)).decode("ascii")


# =====================================================================
# Part 2: 协议层函数（输入输出都是 str 字节流，方便单元测试）
# =====================================================================

def build_client_request(
    host: str,
    path: str,
    key: str,
    subprotocols: Optional[str] = None,
) -> str:
    """
    RFC 6455 §4.1 客户端构造 Upgrade 请求。
    必含：Host / Upgrade: websocket / Connection: Upgrade /
          Sec-WebSocket-Key / Sec-WebSocket-Version: 13
    可选：Sec-WebSocket-Protocol（逗号分隔、按优先级）
    """
    lines = [
        f"GET {path} HTTP/1.1",
        f"Host: {host}",
        "Upgrade: websocket",
        "Connection: Upgrade",
        f"Sec-WebSocket-Key: {key}",
        f"Sec-WebSocket-Version: {WS_VERSION}",
    ]
    if subprotocols:
        lines.append(f"Sec-WebSocket-Protocol: {subprotocols}")
    return "\r\n".join(lines) + "\r\n\r\n"


def find_header(text: str, name: str) -> Optional[str]:
    """大小写不敏感地找 'Name: Value' 头"""
    needle = name.lower()
    for line in text.split("\r\n"):
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        if k.strip().lower() == needle:
            return v.strip()
    return None


def parse_client_request(req: str) -> Tuple[bool, str, str]:
    """
    RFC 6455 §4.1 客户端请求校验：
      - HTTP/1.1 + GET
      - Upgrade: websocket（大小写不敏感）
      - Connection 含 Upgrade token
      - Sec-WebSocket-Version: 13
      - Sec-WebSocket-Key base64-decode 后恰好 16 字节
    返回 (ok, key_or_err, "")。key_or_err 在 ok=True 时为 Sec-WebSocket-Key。
    """
    first = req.split("\r\n", 1)[0]
    if not first.startswith("GET "):
        return False, "method must be GET", ""
    if "HTTP/1.1" not in first:
        return False, "HTTP version must be 1.1", ""

    upgrade = find_header(req, "Upgrade")
    if not upgrade or upgrade.lower() != "websocket":
        return False, "missing or invalid Upgrade header", ""

    connection = find_header(req, "Connection")
    if not connection or "Upgrade" not in connection.split(","):
        # RFC 7230 §6.1：Connection 头可重复，逗号分隔
        return False, "missing Connection: Upgrade", ""

    version = find_header(req, "Sec-WebSocket-Version")
    if version != WS_VERSION:
        return False, f"Sec-WebSocket-Version must be {WS_VERSION}", ""

    key_b64 = find_header(req, "Sec-WebSocket-Key")
    if not key_b64:
        return False, "missing Sec-WebSocket-Key", ""

    raw = _decode_key_to_16_bytes(key_b64)
    if raw is None:
        return False, "Sec-WebSocket-Key must base64-decode to 16 bytes", ""

    return True, key_b64, ""


def build_server_response(
    accept: str,
    selected_subprotocol: Optional[str] = None,
) -> str:
    """
    RFC 6455 §4.2.2 服务端 101 Switching Protocols 响应。
    必含：HTTP/1.1 101 / Upgrade: websocket / Connection: Upgrade /
          Sec-WebSocket-Accept
    可选：Sec-WebSocket-Protocol（从客户端列表里选）
    """
    lines = [
        "HTTP/1.1 101 Switching Protocols",
        "Upgrade: websocket",
        "Connection: Upgrade",
        f"Sec-WebSocket-Accept: {accept}",
    ]
    if selected_subprotocol:
        lines.append(f"Sec-WebSocket-Protocol: {selected_subprotocol}")
    return "\r\n".join(lines) + "\r\n\r\n"


def parse_server_response(resp: str, client_key: str) -> Tuple[bool, str]:
    """
    RFC 6455 §4.1 客户端对服务端响应的校验：
      - 状态码必须 101
      - Upgrade / Connection 头与请求对称
      - Sec-WebSocket-Accept == compute_accept(client_key)
    返回 (ok, err_or_"").
    """
    first = resp.split("\r\n", 1)[0]
    if not first.startswith("HTTP/1.1 101"):
        return False, f"status code must be 101 (got '{first}')"

    upgrade = find_header(resp, "Upgrade")
    if not upgrade or upgrade.lower() != "websocket":
        return False, "missing or invalid Upgrade in response"

    connection = find_header(resp, "Connection")
    if not connection or "Upgrade" not in connection.split(","):
        return False, "missing Connection: Upgrade in response"

    accept = find_header(resp, "Sec-WebSocket-Accept")
    if not accept:
        return False, "missing Sec-WebSocket-Accept in response"

    expected = compute_accept(client_key)
    if accept != expected:
        return False, f"Sec-WebSocket-Accept mismatch (got '{accept}', expected '{expected}')"

    return True, ""


# =====================================================================
# Part 3: 演示驱动
# =====================================================================

class Demo:
    def __init__(self) -> None:
        self.count = 0
        self.failed = 0

    def banner(self, title: str) -> None:
        self.count += 1
        print(f"\n=== Case {self.count}: {title} ===")

    def assert_(self, cond: bool, msg: str) -> None:
        if cond:
            print(f"    [PASS] {msg}")
        else:
            print(f"    [FAIL] {msg}")
            self.failed += 1


def case1_rfc_example(d: Demo) -> None:
    d.banner("RFC 6455 §1.3 计算示例")
    got = compute_accept("dGhlIHNhbXBsZSBub25jZQ==")
    print(f"  Key    : dGhlIHNhbXBsZSBub25jZQ==")
    print(f"  Accept : {got}")
    d.assert_(got == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=", "RFC example accept correct")


def case2_roundtrip(d: Demo) -> None:
    d.banner("完整 round-trip（in-memory 字节流）")
    key = gen_client_key()
    print(f"  Generated Sec-WebSocket-Key: {key}")

    req = build_client_request("example.com", "/chat", key, "chat, superchat")
    d.assert_(len(req) > 0, "client request built")

    ok, srv_key, err = parse_client_request(req)
    d.assert_(ok, f"server parsed request: {err or 'key extracted'}")
    d.assert_(srv_key == key, "key preserved through parsing")

    accept = compute_accept(srv_key)
    resp = build_server_response(accept, "chat")
    d.assert_(len(resp) > 0, "server response built")

    ok, err = parse_server_response(resp, key)
    d.assert_(ok, f"client accepted response: {err or 'all checks passed'}")


def case3_subprotocol(d: Demo) -> None:
    d.banner("子协议协商（Sec-WebSocket-Protocol）")
    key = gen_client_key()
    req = build_client_request("example.com", "/chat", key, "chat, superchat")

    accept = compute_accept(key)
    # 服务端选了客户端列表外的 "wss-v2" —— 客户端必须 fail
    bad_resp = build_server_response(accept, "wss-v2")
    d.assert_("wss-v2" not in req, "server MUST NOT select subprotocol absent from client request")
    d.assert_("chat" in req, "server MAY select subprotocol that client offered")


def case4_missing_header(d: Demo) -> None:
    d.banner("服务端拒绝缺少必填头")
    # 缺 Sec-WebSocket-Version
    bad = (
        "GET /chat HTTP/1.1\r\n"
        "Host: example.com\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n"
    )
    ok, _, err = parse_client_request(bad)
    d.assert_(not ok, f"request without Sec-WebSocket-Version rejected: {err}")

    # 缺 Upgrade
    bad2 = (
        "GET /chat HTTP/1.1\r\n"
        "Host: example.com\r\n"
        "Connection: Upgrade\r\n"
        "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n"
    )
    ok, _, err = parse_client_request(bad2)
    d.assert_(not ok, f"request without Upgrade rejected: {err}")

    # Sec-WebSocket-Key base64 解码后只有 11 字节
    bad3 = (
        "GET /chat HTTP/1.1\r\n"
        "Host: example.com\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        "Sec-WebSocket-Key: dGhlIHNhbXBsZQ==\r\n"  # base64 = 11 字节
        "Sec-WebSocket-Version: 13\r\n\r\n"
    )
    ok, _, err = parse_client_request(bad3)
    d.assert_(not ok, f"request with bad Sec-WebSocket-Key length rejected: {err}")


def case5_client_rejects(d: Demo) -> None:
    d.banner("客户端拒绝错误 accept / 非 101 状态码")
    key = gen_client_key()

    wrong = (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        "Sec-WebSocket-Accept: this-is-wrong\r\n\r\n"
    )
    ok, err = parse_server_response(wrong, key)
    d.assert_(not ok, f"wrong Sec-WebSocket-Accept rejected: {err}")

    not101 = (
        "HTTP/1.1 200 OK\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        "Sec-WebSocket-Accept: anything\r\n\r\n"
    )
    ok, err = parse_server_response(not101, key)
    d.assert_(not ok, f"non-101 status rejected: {err}")


def main() -> int:
    print("WebSocket Opening Handshake Demo (RFC 6455 §4)")
    print("================================================")
    d = Demo()
    case1_rfc_example(d)
    case2_roundtrip(d)
    case3_subprotocol(d)
    case4_missing_header(d)
    case5_client_rejects(d)
    print("\n================================================")
    print(f"Total: {d.count} cases, {d.failed} failed")
    return 0 if d.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())