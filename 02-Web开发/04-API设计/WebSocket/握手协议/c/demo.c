/*
 * demo.c — WebSocket Opening Handshake 演示驱动（5 用例）
 *
 * 与 python/handshake.py、go/handshake.go 中的 demo 函数一一对应。
 * 每个用例独立打印 banner 与若干 [PASS]/[FAIL] 行，最后统计。
 *
 * 编译：
 *   gcc -O2 -Wall -Wextra -pedantic sha1.c base64.c handshake.c demo.c \
 *       -o handshake && ./handshake
 */
#include "handshake.h"
#include <stdio.h>
#include <string.h>

static int demo_count = 0;
static int demo_failed = 0;

#define ASSERT(cond, fmt, ...) do {                                  \
    if (!(cond)) {                                                   \
        printf("    [FAIL] " fmt "\n", ##__VA_ARGS__);               \
        demo_failed++;                                               \
    } else {                                                         \
        printf("    [PASS] " fmt "\n", ##__VA_ARGS__);               \
    }                                                                \
} while (0)

static void print_banner(const char *title) {
    printf("\n=== Case %d: %s ===\n", ++demo_count, title);
}

/*
 * Case 1: RFC 6455 §1.3 例 —— 给定 Sec-WebSocket-Key
 *       应得到 Sec-WebSocket-Accept = "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="
 */
static void case1_rfc_example(void) {
    print_banner("RFC 6455 §1.3 计算示例");
    char accept[29];
    ws_compute_accept("dGhlIHNhbXBsZSBub25jZQ==", accept);
    printf("  Key    : dGhlIHNhbXBsZSBub25jZQ==\n");
    printf("  Accept : %s\n", accept);
    ASSERT(strcmp(accept, "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=") == 0,
           "RFC example accept computed correctly");
}

/*
 * Case 2: 客户端→服务端→客户端 完整 round-trip（in-memory 字节流）
 */
static void case2_roundtrip(void) {
    print_banner("完整 round-trip（in-memory 字节流）");
    char key[25];
    ws_gen_client_key(key);
    printf("  Generated Sec-WebSocket-Key: %s\n", key);

    char req[1024];
    int req_len = ws_build_client_request("example.com", "/chat", key,
                                          "chat, superchat", req, sizeof(req));
    ASSERT(req_len > 0, "client request built (%d bytes)", req_len);

    char srv_key[64], err[128];
    int rc = ws_parse_client_request(req, srv_key, sizeof(srv_key),
                                     err, sizeof(err));
    ASSERT(rc == 0, "server parsed request: %s",
           rc == 0 ? "key extracted" : err);
    ASSERT(strcmp(srv_key, key) == 0, "key preserved through parsing");

    char accept[29];
    ws_compute_accept(srv_key, accept);

    char resp[512];
    int resp_len = ws_build_server_response(accept, "chat",
                                            resp, sizeof(resp));
    ASSERT(resp_len > 0, "server response built (%d bytes)", resp_len);

    rc = ws_parse_server_response(resp, key, err, sizeof(err));
    ASSERT(rc == 0, "client accepted response: %s",
           rc == 0 ? "all checks passed" : err);
}

/*
 * Case 3: 子协议协商 —— 服务端只能从客户端列表里挑（RFC §4.1 第 6 点）
 */
static void case3_subprotocol(void) {
    print_banner("子协议协商（Sec-WebSocket-Protocol）");
    char req[1024], key[25];
    ws_gen_client_key(key);
    ws_build_client_request("example.com", "/chat", key,
                            "chat, superchat", req, sizeof(req));

    char resp[512], accept[29];
    ws_compute_accept(key, accept);
    ws_build_server_response(accept, "wss-v2", resp, sizeof(resp));

    int found = (strstr(req, "wss-v2") != NULL);
    ASSERT(!found, "server MUST NOT select subprotocol absent from client request");

    char resp2[512];
    ws_build_server_response(accept, "chat", resp2, sizeof(resp2));
    int ok = (strstr(req, "chat") != NULL);
    ASSERT(ok, "server MAY select subprotocol that client offered");
}

/*
 * Case 4: 服务端拒绝缺少必填头 / 错误 key 长度的请求
 */
static void case4_missing_header(void) {
    print_banner("服务端拒绝缺少必填头");
    /* 没有 Sec-WebSocket-Version */
    const char *bad =
        "GET /chat HTTP/1.1\r\n"
        "Host: example.com\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n";
    char key[64], err[128];
    int rc = ws_parse_client_request(bad, key, sizeof(key), err, sizeof(err));
    ASSERT(rc == -1, "request without Sec-WebSocket-Version rejected: %s", err);

    /* 没有 Upgrade */
    const char *bad2 =
        "GET /chat HTTP/1.1\r\n"
        "Host: example.com\r\n"
        "Connection: Upgrade\r\n"
        "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n";
    rc = ws_parse_client_request(bad2, key, sizeof(key), err, sizeof(err));
    ASSERT(rc == -1, "request without Upgrade rejected: %s", err);

    /* Sec-WebSocket-Key 不是 16 字节（这里给个 11 字节 base64） */
    const char *bad3 =
        "GET /chat HTTP/1.1\r\n"
        "Host: example.com\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        "Sec-WebSocket-Key: dGhlIHNhbXBsZQ==\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n";
    rc = ws_parse_client_request(bad3, key, sizeof(key), err, sizeof(err));
    ASSERT(rc == -1, "request with bad Sec-WebSocket-Key length rejected: %s", err);
}

/*
 * Case 5: 客户端拒绝错误 accept / 非 101 状态码
 */
static void case5_client_rejects(void) {
    print_banner("客户端拒绝错误 accept / 非 101 状态码");
    char key[25];
    ws_gen_client_key(key);

    /* accept 错误 */
    const char *wrong =
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        "Sec-WebSocket-Accept: this-is-wrong\r\n\r\n";
    char err[128];
    int rc = ws_parse_server_response(wrong, key, err, sizeof(err));
    ASSERT(rc == -1, "wrong Sec-WebSocket-Accept rejected: %s", err);

    /* 状态码 200（非 101）—— RFC 6455 §4.1 第 1 点 */
    const char *not101 =
        "HTTP/1.1 200 OK\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        "Sec-WebSocket-Accept: anything\r\n\r\n";
    rc = ws_parse_server_response(not101, key, err, sizeof(err));
    ASSERT(rc == -1, "non-101 status rejected: %s", err);
}

int main(void) {
    printf("WebSocket Opening Handshake Demo (RFC 6455 §4)\n");
    printf("================================================\n");

    case1_rfc_example();
    case2_roundtrip();
    case3_subprotocol();
    case4_missing_header();
    case5_client_rejects();

    printf("\n================================================\n");
    printf("Total: %d cases, %d failed\n", demo_count, demo_failed);
    return demo_failed == 0 ? 0 : 1;
}