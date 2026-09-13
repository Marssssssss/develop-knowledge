#!/usr/bin/env python3
"""HTTP/1.1 请求解析器 — RFC 7230 状态机。

支持:
  - Request-Line: method SP request-target SP HTTP-version CRLF
  - Header fields: field-name ":" OWS field-value OWS CRLF(OWS = optional whitespace)
  - Empty line (CRLF) 终止 header section
  - Content-Length 决定的 message body 字节切片
  - chunked transfer-coding (chunk-size CRLF chunk-data CRLF ... 0 CRLF CRLF)
  - 容错:行尾接受裸 LF(部分老 HTTP/1.0 客户端);空行首部前可被忽略

不实现:HTTP/1.1 keep-alive / chunk extension / trailer / 多 part / 连接升级。
"""
import socket
import sys
from typing import Iterator, Optional, Tuple

CRLF = b"\r\n"
CR = 13
LF = 10
MAX_HEADER_BYTES = 16 * 1024  # 防御性上限


class HTTPParseError(Exception):
    pass


def _read_line(buf: memoryview, pos: int) -> Tuple[bytes, int]:
    """从 buf[start_of_line:] 读到下一个 CRLF 或裸 LF;返回 (line_bytes_inclusive_of_line_terminator, new_pos_after_terminator)。

    line_bytes 包括从原 pos 起到终止符为止的全部内容(用于判断空行 / 部分行匹配)。
    """
    start = pos
    end = len(buf)
    while pos < end:
        b = buf[pos]
        if b == LF:
            # 容忍裸 LF:把前一个 CR(若有)也包含进来,让 rstrip 能完整去掉终止符
            include_cr = 1 if (pos > start and buf[pos - 1] == CR) else 0
            return bytes(buf[start:pos + 1]), pos + 1
        if b == CR and pos + 1 < end and buf[pos + 1] == LF:
            return bytes(buf[start:pos + 2]), pos + 2
        pos += 1
    raise HTTPParseError("line exceeds available buffer")


def parse_request_head(buf: bytes) -> dict:
    """解析 buffer 中的 HTTP 头;返回 {method, target, version, headers, body_start, body_len}.

    body 不在返回里(调用者按 body_start + body_len 自己 slice)。
    """
    pos = 0
    n = len(buf)
    # 1) Request-Line(可跳过前导空行,见 RFC 7230 §3.5)
    while pos < n and buf[pos:pos + 2] == CRLF:
        pos += 2  # ignore leading empty lines

    try:
        line, pos = _read_line(memoryview(buf), pos)
    except HTTPParseError:
        raise
    line = line.rstrip(b"\r\n")
    parts = line.split(b" ")
    if len(parts) != 3:
        raise HTTPParseError(f"bad request-line: {line!r}")
    method, target, version = parts[0], parts[1], parts[2]
    if not version.startswith(b"HTTP/"):
        raise HTTPParseError(f"bad HTTP-version: {version!r}")

    # 2) Header fields
    headers = {}
    while pos < n:
        line, nxt = _read_line(memoryview(buf), pos)
        if line in (b"\r\n", b"\n"):
            pos = nxt
            break
        line = line.rstrip(b"\r\n")
        if not line:
            pos = nxt
            break
        if b":" not in line:
            raise HTTPParseError(f"bad header line: {line!r}")
        name, _, value = line.partition(b":")
        name = name.strip(b" \t").lower()  # RFC 7230 §3.2: case-insensitive
        value = value.strip(b" \t")
        # 多同名 header 合并为逗号分隔列表(§3.2.2)
        if name in headers:
            headers[name] = headers[name] + b", " + value
        else:
            headers[name] = value
        pos = nxt  # 推进到下一行(否则死循环)

    # 3) 决定 body 长度
    if b"content-length" in headers:
        body_len = int(headers[b"content-length"])
    elif headers.get(b"transfer-encoding") == b"chunked":
        body_len = -1  # chunked → 后续由 caller 流式解码
    else:
        body_len = 0
    return {
        "method": method, "target": target, "version": version,
        "headers": headers, "body_start": pos, "body_len": body_len,
    }


def decode_chunked(buf: bytes, pos: int) -> Iterator[bytes]:
    """极简 chunked 解码:chunk-size CRLF chunk-data CRLF ... 0 CRLF CRLF"""
    n = len(buf)
    while pos < n:
        line, pos = _read_line(memoryview(buf), pos)
        line = line.rstrip(b"\r\n")
        semi = line.find(b";")
        size_hex = line if semi < 0 else line[:semi]
        size = int(size_hex, 16)
        if size == 0:
            # 末块;吃掉 trailing CRLF(可有 trailer,但本 demo 忽略)
            while pos < n and buf[pos:pos + 2] != CRLF:
                # 跳过 trailer 行(若有)
                line2, pos2 = _read_line(memoryview(buf), pos)
                if line2.rstrip(b"\r\n") == b"":
                    pos = pos2
                    break
            break
        yield bytes(buf[pos:pos + size])
        pos += size
        if buf[pos:pos + 2] != CRLF:
            raise HTTPParseError("missing CRLF after chunk data")
        pos += 2


# ========== 自测 + mini server ==========
SAMPLE_REQ = (
    b"GET /index.html HTTP/1.1\r\n"
    b"Host: example.com\r\n"
    b"User-Agent: demo/1.0\r\n"
    b"Accept: text/html, application/xhtml+xml\r\n"
    b"X-Multi: a\r\n"
    b"X-Multi: b\r\n"
    b"Content-Length: 0\r\n"
    b"\r\n"
)
SAMPLE_CHUNKED = (
    b"POST /upload HTTP/1.1\r\n"
    b"Host: example.com\r\n"
    b"Transfer-Encoding: chunked\r\n"
    b"\r\n"
    b"5\r\nhello\r\n"
    b"6\r\n world\r\n"
    b"0\r\n\r\n"
)


def self_test() -> None:
    print("=== HTTP/1.1 parser self-test ===")

    # Case 1: 简单 GET, 无 body
    r = parse_request_head(SAMPLE_REQ)
    assert r["method"] == b"GET", r["method"]
    assert r["target"] == b"/index.html", r["target"]
    assert r["version"] == b"HTTP/1.1", r["version"]
    assert r["headers"][b"host"] == b"example.com"
    assert r["headers"][b"accept"] == b"text/html, application/xhtml+xml"
    assert r["headers"][b"x-multi"] == b"a, b", "X-Multi 合并应为逗号分隔"
    assert r["body_len"] == 0
    print(f"  [1] GET no body OK  method={r['method']!r} target={r['target']!r} "
          f"version={r['version']!r} headers={len(r['headers'])}")

    # Case 2: chunked POST
    r = parse_request_head(SAMPLE_CHUNKED)
    assert r["body_len"] == -1, f"chunked body_len 应为 -1, 实际 {r['body_len']}"
    body = b"".join(decode_chunked(SAMPLE_CHUNKED, r["body_start"]))
    assert body == b"hello world", body
    print(f"  [2] chunked OK  body={body!r}")

    # Case 3: 容错 — 裸 LF
    bare_lf = b"GET / HTTP/1.1\nHost: x\nAccept: */*\n\n"
    r = parse_request_head(bare_lf)
    assert r["method"] == b"GET" and r["headers"][b"host"] == b"x"
    print(f"  [3] bare LF tolerated  method={r['method']!r}")

    # Case 4: leading empty line(应被忽略)
    leading_crlf = b"\r\nGET / HTTP/1.1\r\nHost: x\r\n\r\n"
    r = parse_request_head(leading_crlf)
    assert r["method"] == b"GET"
    print(f"  [4] leading CRLF ignored OK")

    # Case 5: 解析失败 — 非法 Request-Line(只有 2 部分,缺少 HTTP 版本)
    try:
        parse_request_head(b"GET /only-two\r\n\r\n")
        assert False, "should raise"
    except HTTPParseError as e:
        print(f"  [5] bad request-line detected: {e}")

    print("all self-tests passed.\n")


def mini_server(port: int) -> None:
    """极简 server:解析单条 HTTP 请求,回 200 OK + 'hello from python http parser'。"""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", port))
    srv.listen(8)
    print(f"[mini server] listening on :{port}", flush=True)
    while True:
        try:
            conn, peer = srv.accept()
        except OSError:
            return
        print(f"[mini server] accept {peer}", flush=True)
        conn.settimeout(5.0)
        try:
            # 读 header 至 \r\n\r\n
            buf = b""
            while b"\r\n\r\n" not in buf:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf += chunk
                if len(buf) > MAX_HEADER_BYTES:
                    conn.sendall(b"HTTP/1.1 413 Payload Too Large\r\n\r\n")
                    break
            else:
                r = parse_request_head(buf)
                print(f"  method={r['method']!r} target={r['target']!r} "
                      f"version={r['version']!r}", flush=True)
                for k, v in r["headers"].items():
                    print(f"  {k.decode():>15}: {v.decode(errors='replace')}", flush=True)
                body = b"hello from python http parser\n"
                resp = (b"HTTP/1.1 200 OK\r\n"
                        b"Content-Type: text/plain\r\n"
                        b"Content-Length: " + str(len(body)).encode() + b"\r\n"
                        b"Connection: close\r\n\r\n" + body)
                conn.sendall(resp)
        except Exception as e:
            print(f"  parse/serve error: {e}", flush=True)
            try: conn.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
            except OSError: pass
        finally:
            conn.close()


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "test":
        self_test()
        sys.exit(0)
    if len(sys.argv) >= 3 and sys.argv[1] == "serve":
        mini_server(int(sys.argv[2]))
        sys.exit(0)
    print("usage:\n  python3 main.py test\n  python3 main.py serve <port>", file=sys.stderr)
    sys.exit(1)