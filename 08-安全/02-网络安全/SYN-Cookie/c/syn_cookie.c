// SYN Cookie 防 SYN Flood (Bernstein/Schenk 1996, RFC 4987 §3.1)
//
// 本程序演示:
//   - server 收 SYN 时生成 32-bit cookie ISN (top 5 bit 慢时戳 / mid 3 bit MSS / low 24 bit HMAC-SHA1)
//   - 客户端回 ACK,server 验证 cookie(时戳 + HMAC),通过才分配 TCB
//   - 攻击者伪造 cookie(1 bit 翻转)→ 验证失败 → 丢弃
//
// 编译:  gcc -O2 -Wall -Wextra -pedantic syn_cookie.c -lcrypto -o syn_cookie

#include <openssl/sha.h>
#include <openssl/hmac.h>
#include <openssl/rand.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <stdint.h>

#define MSS_TABLE_SIZE 8
static const uint16_t MSS_TABLE[MSS_TABLE_SIZE] = {536, 1300, 1460, 1500, 2000, 4096, 8192, 9000};
#define SLOW_TICK 64            // 1 t 单位 = 64 秒
#define TOLERANCE 4             // ±4 t 单位 ≈ 256 秒容忍

typedef struct {
    uint8_t secret[16];          // 启动生成的随机 server key
} syn_cookie_server_t;

static inline int slow_time(time_t now) {
    return ((now / SLOW_TICK) % 32) & 0x1F;
}

static int encode_mss(uint16_t mss) {
    int idx = 0; int min_diff = abs((int)MSS_TABLE[0] - (int)mss);
    for (int i = 1; i < MSS_TABLE_SIZE; i++) {
        int d = abs((int)MSS_TABLE[i] - (int)mss);
        if (d < min_diff) { min_diff = d; idx = i; }
    }
    return idx;
}

static uint32_t compute_s(const syn_cookie_server_t *srv,
                          const char *src_ip, uint16_t src_port,
                          const char *dst_ip, uint16_t dst_port,
                          int t, int m_code) {
    char msg[256];
    int n = snprintf(msg, sizeof(msg), "%s|%u|%s|%u|%d|%d",
                     src_ip, src_port, dst_ip, dst_port, t, m_code);
    if (n <= 0 || n >= (int)sizeof(msg)) return 0;
    uint8_t digest[SHA_DIGEST_LENGTH];
    unsigned int digest_len = SHA_DIGEST_LENGTH;
    HMAC(srv->secret, sizeof(srv->secret),
         (const uint8_t *)msg, (size_t)n,
         digest, &digest_len);
    uint32_t v;
    memcpy(&v, digest, 4);
    return v & 0xFFFFFF;  // low 24 bit
}

void server_init(syn_cookie_server_t *srv) {
    RAND_bytes(srv->secret, sizeof(srv->secret));
}

uint32_t syn_cookie_make(const syn_cookie_server_t *srv,
                          const char *src_ip, uint16_t src_port,
                          const char *dst_ip, uint16_t dst_port,
                          uint16_t mss) {
    time_t now; time(&now);
    int t = slow_time(now);
    int m = encode_mss(mss);
    uint32_t s = compute_s(srv, src_ip, src_port, dst_ip, dst_port, t, m);
    return ((uint32_t)t << 27) | ((uint32_t)m << 24) | s;
}

typedef struct {
    int valid;                 // 0 = 失败, 1 = 通过
    char src[64];
    char dst[64];
    uint16_t mss;
    int t_cooked, t_now;
} syn_cookie_info_t;

syn_cookie_info_t syn_cookie_verify(const syn_cookie_server_t *srv,
                                     uint32_t ack_num,
                                     const char *src_ip, uint16_t src_port,
                                     const char *dst_ip, uint16_t dst_port) {
    syn_cookie_info_t info = {0};
    uint32_t cookie = (ack_num - 1) & 0xFFFFFFFF;
    int t_cooked = (cookie >> 27) & 0x1F;
    int m_code   = (cookie >> 24) & 0x7;
    uint32_t s_recv = cookie & 0xFFFFFF;

    time_t now; time(&now);
    int t_now = slow_time(now);
    int diff = (t_now - t_cooked + 32) % 32;
    if (diff > 16) diff = 32 - diff;
    if (diff > TOLERANCE) return info;  // 时戳过期

    uint32_t s_expected = compute_s(srv, src_ip, src_port, dst_ip, dst_port, t_cooked, m_code);
    if (s_expected != s_recv) return info;  // HMAC 不匹配

    info.valid    = 1;
    info.t_cooked = t_cooked;
    info.t_now    = t_now;
    info.mss      = MSS_TABLE[m_code];
    snprintf(info.src, sizeof(info.src), "%s:%u", src_ip, src_port);
    snprintf(info.dst, sizeof(info.dst), "%s:%u", dst_ip, dst_port);
    return info;
}

// ============================================================
// Demo
// ============================================================
int main(void) {
    printf("================================================================\n");
    printf(" SYN Cookie Demo (Bernstein/Schenk 1996, RFC 4987 §3.1)\n");
    printf("================================================================\n");

    syn_cookie_server_t srv; server_init(&srv);

    // Step 1: Client A 发 SYN
    printf("\n[Step 1] Client A (192.0.2.10:54321) 发 SYN → server\n");
    uint32_t isn = syn_cookie_make(&srv, "192.0.2.10", 54321, "203.0.113.5", 80, 1460);
    printf("  Server 编码 SYN cookie ISN = 0x%08X\n", isn);
    printf("    Top 5  bit t   = %u\n", (isn >> 27) & 0x1F);
    printf("    Mid 3  bit mss = %u  → MSS = %u B\n", (isn >> 24) & 0x7, MSS_TABLE[(isn >> 24) & 0x7]);
    printf("    Low 24 bit s   = 0x%06X\n", isn & 0xFFFFFF);

    // Step 2: Client A 回 ACK (ack = ISN + 1)
    uint32_t ack_num = isn + 1;
    printf("\n[Step 2] Client A 回 ACK (ack_number = 0x%08X = ISN+1)\n", ack_num);
    syn_cookie_info_t info = syn_cookie_verify(&srv, ack_num, "192.0.2.10", 54321, "203.0.113.5", 80);
    if (info.valid) {
        printf("  ✓ 通过 → 分配 TCB\n");
        printf("    src     = %s\n", info.src);
        printf("    dst     = %s\n", info.dst);
        printf("    MSS     = %u (from 3-bit code)\n", info.mss);
        printf("    t_cooked = %d   t_now = %d\n", info.t_cooked, info.t_now);
    } else {
        printf("  ✗ 校验失败 (意料之外)\n");
        return 1;
    }

    // Step 3: 攻击者伪造 cookie
    printf("\n[Step 3] 攻击者篡改 cookie 1 bit → 验证失败\n");
    uint32_t fake_isn = isn ^ 0x1;
    uint32_t fake_ack = fake_isn + 1;
    syn_cookie_info_t info2 = syn_cookie_verify(&srv, fake_ack, "192.0.2.10", 54321, "203.0.113.5", 80);
    printf("  Fake cookie       = 0x%08X  (legal = 0x%08X)\n", fake_isn, isn);
    printf("  Server 验证       = %s\n", info2.valid ? "通过" : "失败 (丢弃)");
    if (info2.valid) return 1;

    // Step 4: 多个 SYN
    printf("\n[Step 4] 10 个客户端同一秒 SYN → hash 区分不同 src\n");
    int unique = 0;
    uint32_t seen_low[16] = {0};
    int unique_count = 0;
    int s_low;
    int seen_size = 0;
    for (int i = 0; i < 10; i++) {
        char src[16]; snprintf(src, sizeof(src), "192.0.2.%d", i + 1);
        uint32_t s = syn_cookie_make(&srv, src, 50000 + i, "203.0.113.5", 80, 1460);
        uint32_t low = s & 0xFFFFFF;
        int dup = 0;
        for (int j = 0; j < seen_size; j++)
            if (seen_low[j] == low) dup = 1;
        if (!dup) seen_low[seen_size++] = low;
    }
    printf("  %d/10 互不相同的低 24 bit (hash 区分)\n", seen_size);

    printf("\n================================================================\n");
    printf("  SYN Cookie 演示完成 ✓\n");
    printf("================================================================\n");
    return 0;
}
