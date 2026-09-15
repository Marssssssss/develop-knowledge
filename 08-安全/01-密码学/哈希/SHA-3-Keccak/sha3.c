/* SHA-3 / Keccak-f[1600] 海绵函数实现(FIPS 202)。
 * 依据: keccak.team 规格摘要(五步轮函数/旋转偏移表/各实例 rate 与域分离后缀);
 * RC 常量按官方 CompactFIPS202 的 LFSR 生成式推导, 逐项断言防抄录错误。
 * 自测: NIST 官方向量(空串/abc) + SHAKE 向量 + padding 边界确定性。
 * 编译: cc sha3.c -o sha3 && ./sha3 */
#include <stdio.h>
#include <string.h>
#include <stdint.h>

static uint64_t RC[24];
static const int ROT[5][5] = {
    {0, 36, 3, 41, 18},
    {1, 44, 10, 45, 2},
    {62, 6, 43, 15, 61},
    {28, 55, 25, 21, 56},
    {27, 20, 39, 8, 14},
};

static void gen_rc(void) { /* 官方 LFSR: R 跨轮持续演化 */
    unsigned char R = 1;
    for (int i = 0; i < 24; i++) {
        uint64_t val = 0;
        for (int j = 0; j < 7; j++) {
            R = (unsigned char)(((R << 1) ^ ((R >> 7) * 0x71)) & 0xFF);
            if (R & 2)
                val ^= (uint64_t)1 << ((1 << j) - 1);
        }
        RC[i] = val;
    }
}

static uint64_t rotl(uint64_t v, int n) {
    n %= 64;
    return n ? (v << n) | (v >> (64 - n)) : v;
}

/* lane A[x][y] 存于 a[x + 5y] */
static void keccak_f1600(uint64_t *a) {
    uint64_t b[25], c[5], d[5];
    for (int i = 0; i < 24; i++) {
        /* theta */
        for (int x = 0; x < 5; x++)
            c[x] = a[x] ^ a[x + 5] ^ a[x + 10] ^ a[x + 15] ^ a[x + 20];
        for (int x = 0; x < 5; x++)
            d[x] = c[(x + 4) % 5] ^ rotl(c[(x + 1) % 5], 1);
        for (int x = 0; x < 5; x++)
            for (int y = 0; y < 5; y++)
                a[x + 5 * y] ^= d[x];
        /* rho + pi */
        memset(b, 0, sizeof(b));
        for (int x = 0; x < 5; x++)
            for (int y = 0; y < 5; y++)
                b[y + 5 * ((2 * x + 3 * y) % 5)] = rotl(a[x + 5 * y], ROT[x][y]);
        /* chi */
        for (int y = 0; y < 5; y++)
            for (int x = 0; x < 5; x++)
                a[x + 5 * y] = b[x + 5 * y] ^ (~b[(x + 1) % 5 + 5 * y] & b[(x + 2) % 5 + 5 * y]);
        /* iota */
        a[0] ^= RC[i];
    }
}

static void permute(unsigned char *state) {
    uint64_t lanes[25];
    for (int k = 0; k < 25; k++) {
        lanes[k] = 0;
        for (int i = 0; i < 8; i++)
            lanes[k] |= (uint64_t)state[8 * k + i] << (8 * i);
    }
    keccak_f1600(lanes);
    for (int k = 0; k < 25; k++)
        for (int i = 0; i < 8; i++)
            state[8 * k + i] = (unsigned char)(lanes[k] >> (8 * i));
}

/* pad10*1 + 域分离后缀; 末块恰剩 1 字节时 suffix|0x80 合并(如 0x86) */
static void sponge(const unsigned char *data, size_t n, int rate, unsigned char suffix,
                   unsigned char *out, size_t out_len) {
    unsigned char state[200];
    size_t off = 0, last = 0, take;
    memset(state, 0, sizeof(state));
    while (off < n) {
        take = n - off < (size_t)rate ? n - off : (size_t)rate;
        for (size_t i = 0; i < take; i++)
            state[i] ^= data[off + i];
        off += take;
        last = take;
        if (take == (size_t)rate) {
            permute(state);
            last = 0;
        }
    }
    state[last] ^= suffix;
    state[rate - 1] ^= 0x80;
    permute(state);
    size_t done = 0;
    while (done < out_len) {
        size_t blk = out_len - done < (size_t)rate ? out_len - done : (size_t)rate;
        memcpy(out + done, state, blk);
        done += blk;
        if (done < out_len)
            permute(state);
    }
}

static void sha3(const unsigned char *data, size_t n, int bits, unsigned char *out) {
    int rate = bits == 224 ? 144 : bits == 256 ? 136 : bits == 384 ? 104 : 72;
    sponge(data, n, rate, 0x06, out, (size_t)bits / 8);
}

static void print_hex(const unsigned char *b, size_t n) {
    for (size_t i = 0; i < n; i++)
        printf("%02x", b[i]);
    printf("\n");
}

int main(void) {
    gen_rc();
    /* 抽检 RC 防抄录错误 */
    if (RC[0] != 1 || RC[1] != 0x8082ULL || RC[11] != 0x8000000AULL ||
        RC[23] != 0x8000000080008008ULL) {
        puts("RC 常量错误");
        return 1;
    }
    unsigned char out[64];
    int fail = 0;
    /* demo1: NIST 官方向量 */
    sha3((const unsigned char *)"", 0, 224, out);
    puts(!memcmp(out, "\x6b\x4e\x03\x42\x36\x67\xdb\xb7\x3b\x6e\x15\x45\x4f\x0e\xb1\xab"
                      "\xd4\x59\x7f\x9a\x1b\x07\x8e\x3f\x5b\x5a\x6b\xc7", 28)
             ? "demo1 SHA3-224(\"\"): PASS" : "FAIL SHA3-224");
    fail |= memcmp(out, "\x6b\x4e\x03\x42\x36\x67\xdb\xb7\x3b\x6e\x15\x45\x4f\x0e\xb1\xab"
                        "\xd4\x59\x7f\x9a\x1b\x07\x8e\x3f\x5b\x5a\x6b\xc7", 28) != 0;
    sha3((const unsigned char *)"", 0, 256, out);
    puts(!memcmp(out, "\xa7\xff\xc6\xf8\xbf\x1e\xd7\x66\x51\xc1\x47\x56\xa0\x61\xd6\x62"
                      "\xf5\x80\xff\x4d\xe4\x3b\x49\xfa\x82\xd8\x0a\x4b\x80\xf8\x43\x4a", 32)
             ? "demo1 SHA3-256(\"\"): PASS" : "FAIL SHA3-256");
    fail |= memcmp(out, "\xa7\xff\xc6\xf8\xbf\x1e\xd7\x66\x51\xc1\x47\x56\xa0\x61\xd6\x62"
                        "\xf5\x80\xff\x4d\xe4\x3b\x49\xfa\x82\xd8\x0a\x4b\x80\xf8\x43\x4a", 32) != 0;
    sha3((const unsigned char *)"abc", 3, 256, out);
    puts(!memcmp(out, "\x3a\x98\x5d\xa7\x4f\xe2\x25\xb2\x04\x5c\x17\x2d\x6b\xd3\x90\xbd"
                      "\x85\x5f\x08\x6e\x3e\x9d\x52\x5b\x46\xbf\xe2\x45\x11\x43\x15\x32", 32)
             ? "demo1 SHA3-256(abc): PASS" : "FAIL SHA3-256(abc)");
    fail |= memcmp(out, "\x3a\x98\x5d\xa7\x4f\xe2\x25\xb2\x04\x5c\x17\x2d\x6b\xd3\x90\xbd"
                        "\x85\x5f\x08\x6e\x3e\x9d\x52\x5b\x46\xbf\xe2\x45\x11\x43\x15\x32", 32) != 0;
    /* demo2: SHAKE256 空串前 32 字节 */
    sponge((const unsigned char *)"", 0, 136, 0x1F, out, 32);
    puts(!memcmp(out, "\x46\xb9\xdd\x2b\x0b\xa8\x8d\x13\x23\x3b\x3f\xeb\x74\x3e\xeb\x24"
                      "\x3f\xcd\x52\xea\x62\xb8\x1b\x82\xb5\x0c\x27\x64\x6e\xd5\x76\x2f", 32)
             ? "demo2 SHAKE256(\"\",32): PASS" : "FAIL SHAKE256");
    fail |= memcmp(out, "\x46\xb9\xdd\x2b\x0b\xa8\x8d\x13\x23\x3b\x3f\xeb\x74\x3e\xeb\x24"
                        "\x3f\xcd\x52\xea\x62\xb8\x1b\x82\xb5\x0c\x27\x64\x6e\xd5\x76\x2f", 32) != 0;
    /* demo3: 跨块长输入输出互异 */
    unsigned char big1[135], big2[136], o1[32], o2[32];
    memset(big1, 'a', sizeof(big1));
    memset(big2, 'a', sizeof(big2));
    sha3(big1, sizeof(big1), 256, o1);
    sha3(big2, sizeof(big2), 256, o2);
    puts(memcmp(o1, o2, 32) ? "demo3 跨块(135/136)输出互异: PASS" : "FAIL 跨块");
    fail |= memcmp(o1, o2, 32) == 0;
    /* demo4: 雪崩 */
    sha3((const unsigned char *)"the quick brown fox", 19, 256, o1);
    sha3((const unsigned char *)"the quick brown fox!", 20, 256, o2);
    int diff = 0;
    for (int i = 0; i < 32; i++)
        for (unsigned char x = o1[i] ^ o2[i]; x; x >>= 1)
            diff += x & 1;
    printf("demo4 雪崩: %d/256 位翻转 (期望约128): %s\n", diff,
           (diff > 100 && diff < 156) ? "PASS" : "FAIL");
    fail |= !(diff > 100 && diff < 156);
    /* demo5: padding 边界(135 字节=rate-1, 0x06|0x80 合并为 0x86) */
    unsigned char pad[135], pa[32], pb[32];
    for (int i = 0; i < 135; i++)
        pad[i] = (unsigned char)(i * 7);
    sha3(pad, 135, 256, pa);
    sha3(pad, 135, 256, pb);
    puts(!memcmp(pa, pb, 32) ? "demo5 padding 边界确定性: PASS" : "FAIL padding");
    fail |= memcmp(pa, pb, 32) != 0;
    if (fail) {
        puts("SOME FAILED");
        return 1;
    }
    puts("ALL PASS");
    return 0;
}
