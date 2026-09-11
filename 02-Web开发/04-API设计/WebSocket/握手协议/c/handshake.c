/*
 * handshake.c — WebSocket Opening Handshake 协议层 (RFC 6455 §4)
 *
 * 本文件只放协议逻辑：
 *   - 常量：WS_MAGIC_GUID、WS_VERSION
 *   - 工具：gen_client_key（生成 16B 随机 nonce base64）、
 *           compute_accept（计算 Sec-WebSocket-Accept）、
 *           find_header / istarts_with（HTTP 头解析辅助）
 *   - 协议层：build_client_request / parse_client_request /
 *             build_server_response / parse_server_response
 *
 * 演示驱动见 demo.c；base64 编解码见 base64.c；SHA-1 见 sha1.c。
 */

#include "handshake.h"
#include "sha1.h"
#include "base64.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>

/* ====================================================================
 * 常量与底层工具
 * ==================================================================== */

/* RFC 6455 §1.3 写死的 Magic GUID（与协议绑定） */
static const char WS_MAGIC_GUID[] = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11";
#define WS_MAGIC_GUID_LEN 36

/* RFC 6455 §4.1 第 9 点强制 Sec-WebSocket-Version: 13 */
#define WS_PROTOCOL_VERSION "13"

/* 不区分大小写比较前缀 */
static int istarts_with(const char *s, const char *prefix) {
    while (*prefix) {
        if (tolower((uint8_t)*s) != tolower((uint8_t)*prefix)) return 0;
        s++; prefix++;
    }
    return 1;
}

/* 从 "Header: value\r\n..." 中找指定头并返回 value；返回是否找到 */
static int find_header(const char *buf, const char *name,
                       char *out, size_t out_cap) {
    size_t name_len = strlen(name);
    const char *p = buf;
    while (*p) {
        if (istarts_with(p, name) && p[name_len] == ':') {
            p += name_len + 1;
            while (*p == ' ' || *p == '\t') p++;
            size_t i = 0;
            while (*p && *p != '\r' && *p != '\n' && i + 1 < out_cap) {
                out[i++] = *p++;
            }
            out[i] = '\0';
            return 1;
        }
        while (*p && *p != '\n') p++;
        if (*p == '\n') p++;
    }
    return 0;
}

/*
 * 生成 16 字节 base64 nonce（Sec-WebSocket-Key），写入 out[25]。
 * 用确定性伪随机：RFC 6455 §4.1 第 7 点仅要求"随机 16B"，
 * 真实生产请用 OpenSSL RAND_bytes / arc4random_buf / getrandom()。
 */
void ws_gen_client_key(char out[25]) {
    uint8_t nonce[16];
    for (int i = 0; i < 16; i++) {
        nonce[i] = (uint8_t)((i * 1103515245u + 12345u) >> 16);
    }
    base64_encode(nonce, 16, out, 25);
}

/*
 * compute_accept —— RFC 6455 §4.2.2 步骤 4 + §1.3 例：
 *   Sec-WebSocket-Accept = base64( SHA-1( Sec-WebSocket-Key + MAGIC_GUID ) )
 *
 * 例：ws_compute_accept("dGhlIHNhbXBsZSBub25jZQ==") → "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="
 */
void ws_compute_accept(const char *client_key, char out[29]) {
    char buf[64];
    size_t klen = strlen(client_key);
    memcpy(buf, client_key, klen);
    memcpy(buf + klen, WS_MAGIC_GUID, WS_MAGIC_GUID_LEN);
    uint8_t hash[20];
    sha1(buf, klen + WS_MAGIC_GUID_LEN, hash);
    base64_encode(hash, 20, out, 29);
}

/* ====================================================================
 * 协议层函数
 * ==================================================================== */

/*
 * build_client_request —— RFC 6455 §4.1：构造客户端 Upgrade 请求。
 * 必填：Host / Upgrade: websocket / Connection: Upgrade /
 *       Sec-WebSocket-Key / Sec-WebSocket-Version: 13
 * 可选：Sec-WebSocket-Protocol（按优先级逗号分隔）
 * 返回写入 buf 的字节数（不含末尾 \0）；失败返回 -1。
 */
int ws_build_client_request(const char *host, const char *path,
                            const char *key,
                            const char *subprotocols,
                            char *buf, size_t buf_cap) {
    int n = snprintf(buf, buf_cap,
        "GET %s HTTP/1.1\r\n"
        "Host: %s\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        "Sec-WebSocket-Key: %s\r\n"
        "Sec-WebSocket-Version: " WS_PROTOCOL_VERSION "\r\n",
        path, host, key);
    if (n < 0 || (size_t)n >= buf_cap) return -1;
    if (subprotocols && *subprotocols) {
        int m = snprintf(buf + n, buf_cap - n,
            "Sec-WebSocket-Protocol: %s\r\n", subprotocols);
        if (m < 0 || (size_t)m >= buf_cap - n) return -1;
        n += m;
    }
    if (n + 2 >= (int)buf_cap) return -1;
    buf[n++] = '\r'; buf[n++] = '\n';
    buf[n] = '\0';
    return n;
}

/*
 * parse_client_request —— RFC 6455 §4.1 客户端请求校验。
 * 必查 4 项：HTTP/1.1+GET、Upgrade/Connection、Sec-WebSocket-Version:13、
 *              Sec-WebSocket-Key base64 解码后 = 16 字节。
 * 返回 0 成功；-1 失败，错误原因写入 err。
 */
int ws_parse_client_request(const char *req, char *key_out,
                            size_t key_cap, char *err, size_t err_cap) {
    if (!istarts_with(req, "GET ")) {
        snprintf(err, err_cap, "method must be GET");
        return -1;
    }
    if (!strstr(req, "HTTP/1.1")) {
        snprintf(err, err_cap, "HTTP version must be 1.1");
        return -1;
    }
    char upgrade[32] = {0}, connection[64] = {0}, version[8] = {0};
    if (!find_header(req, "Upgrade", upgrade, sizeof(upgrade)) ||
        strcasecmp(upgrade, "websocket") != 0) {
        snprintf(err, err_cap, "missing or invalid Upgrade header");
        return -1;
    }
    if (!find_header(req, "Connection", connection, sizeof(connection)) ||
        !strstr(connection, "Upgrade")) {
        snprintf(err, err_cap, "missing Connection: Upgrade");
        return -1;
    }
    if (!find_header(req, "Sec-WebSocket-Version", version, sizeof(version)) ||
        strcmp(version, WS_PROTOCOL_VERSION) != 0) {
        snprintf(err, err_cap, "Sec-WebSocket-Version must be 13");
        return -1;
    }
    char key_b64[64] = {0};
    if (!find_header(req, "Sec-WebSocket-Key", key_b64, sizeof(key_b64))) {
        snprintf(err, err_cap, "missing Sec-WebSocket-Key");
        return -1;
    }
    uint8_t nonce[16];
    size_t n = base64_decode(key_b64, strlen(key_b64), nonce, 16);
    if (n != 16) {
        snprintf(err, err_cap,
            "Sec-WebSocket-Key must base64-decode to 16 bytes (got %zu)", n);
        return -1;
    }
    if (strlen(key_b64) + 1 > key_cap) {
        snprintf(err, err_cap, "key buffer too small");
        return -1;
    }
    strcpy(key_out, key_b64);
    return 0;
}

/*
 * build_server_response —— RFC 6455 §4.2.2：构造 101 Switching Protocols 响应。
 * selected_subprotocol 为 NULL/空则不写 Sec-WebSocket-Protocol。
 */
int ws_build_server_response(const char *accept,
                             const char *selected_subprotocol,
                             char *buf, size_t buf_cap) {
    int n = snprintf(buf, buf_cap,
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        "Sec-WebSocket-Accept: %s\r\n", accept);
    if (n < 0 || (size_t)n >= buf_cap) return -1;
    if (selected_subprotocol && *selected_subprotocol) {
        int m = snprintf(buf + n, buf_cap - n,
            "Sec-WebSocket-Protocol: %s\r\n", selected_subprotocol);
        if (m < 0 || (size_t)m >= buf_cap - n) return -1;
        n += m;
    }
    if (n + 2 >= (int)buf_cap) return -1;
    buf[n++] = '\r'; buf[n++] = '\n';
    buf[n] = '\0';
    return n;
}

/*
 * parse_server_response —— RFC 6455 §4.1 客户端对响应的校验：
 *   - 状态码必须 101
 *   - Upgrade: websocket、Connection 含 Upgrade token
 *   - Sec-WebSocket-Accept == base64(SHA-1(key + GUID))
 * 成功返回 0；失败返回 -1。
 */
int ws_parse_server_response(const char *resp, const char *client_key,
                             char *err, size_t err_cap) {
    if (!istarts_with(resp, "HTTP/1.1 101")) {
        snprintf(err, err_cap, "status code must be 101");
        return -1;
    }
    char upgrade[32] = {0}, connection[64] = {0}, accept[64] = {0};
    if (!find_header(resp, "Upgrade", upgrade, sizeof(upgrade)) ||
        strcasecmp(upgrade, "websocket") != 0) {
        snprintf(err, err_cap, "missing or invalid Upgrade in response");
        return -1;
    }
    if (!find_header(resp, "Connection", connection, sizeof(connection)) ||
        !strstr(connection, "Upgrade")) {
        snprintf(err, err_cap, "missing Connection: Upgrade in response");
        return -1;
    }
    if (!find_header(resp, "Sec-WebSocket-Accept", accept, sizeof(accept))) {
        snprintf(err, err_cap, "missing Sec-WebSocket-Accept in response");
        return -1;
    }
    char expected[29];
    ws_compute_accept(client_key, expected);
    if (strcmp(accept, expected) != 0) {
        snprintf(err, err_cap,
            "Sec-WebSocket-Accept mismatch (got '%s', expected '%s')",
            accept, expected);
        return -1;
    }
    return 0;
}