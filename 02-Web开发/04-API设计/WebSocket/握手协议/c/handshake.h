/*
 * handshake.h — WebSocket Opening Handshake 协议层（RFC 6455 §4）
 *
 * 暴露给 demo.c 的协议层 API。所有函数返回 0 表示成功、-1 表示失败。
 */
#ifndef WS_HANDSHAKE_H
#define WS_HANDSHAKE_H

#include <stddef.h>

/* 生成 16 字节随机 nonce base64 → out[25]（Sec-WebSocket-Key） */
void ws_gen_client_key(char out[25]);

/*
 * 计算 Sec-WebSocket-Accept → out[29]（28 字节 + \0）。
 * 算法：base64( SHA-1( client_key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11" ) )
 */
void ws_compute_accept(const char *client_key, char out[29]);

/* 构造客户端 Upgrade 请求；返回写入字节数（不含 \0），-1 = 失败 */
int ws_build_client_request(const char *host, const char *path,
                            const char *key,
                            const char *subprotocols,
                            char *buf, size_t buf_cap);

/* 解析客户端请求；成功 0，key 写入 key_out；失败 -1，err 写错误原因 */
int ws_parse_client_request(const char *req, char *key_out,
                            size_t key_cap, char *err, size_t err_cap);

/* 构造服务端 101 响应；返回写入字节数（不含 \0），-1 = 失败 */
int ws_build_server_response(const char *accept,
                             const char *selected_subprotocol,
                             char *buf, size_t buf_cap);

/* 解析服务端响应；成功 0；失败 -1 */
int ws_parse_server_response(const char *resp, const char *client_key,
                             char *err, size_t err_cap);

#endif