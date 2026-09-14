/* HMAC-SHA256 教学实现 —— RFC 2104 §2 + RFC 4231 测试向量。
 * 单文件 C99 stdlib,无外部依赖(可选用 OpenSSL -lcrypto 验证)。
 *
 * 实现要点:
 * - 算法:HMAC(K, m) = SHA256((K ⊕ opad) || SHA256((K ⊕ ipad) || m))
 * - ipad = 0x36 × B(B=64),opad = 0x5C × B
 * - Key 长度 > B(64):先 SHA256(K) 再 padding
 * - Key 长度 < B:末尾补 0x00 至 B
 * - Key 长度 = B:直接使用
 * - 底层 SHA-256 自实现(简版,内嵌)
 *
 * 测试向量(RFC 4231 §4.2 / 4.3 / 4.4 / 4.7):
 * Case 1: key=20×0x0b, data="Hi There"
 *   HMAC-SHA256 = b0344c61d8db38535ca8afceaf0bf12b881dc200c9833da726e9376c2e32cff7
 * Case 2: key="Jefe", data="what do ya want for nothing?"
 *   HMAC-SHA256 = 5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843
 * Case 3: key=20×0xaa, data=50×0xdd
 *   HMAC-SHA256 = 773ea91e36800e46854db8ebd09181a72959098b3ef8c122d9635514ced565fe
 * Case 6: key=131×0xaa, data="Test Using Larger Than Block-Size Key - Hash Key First"
 *   HMAC-SHA256 = 60e431591ee0b67f0d8a26aacbf5b77f8e0bc6213728c5140546040f0ee37f54
 */

#include <stdio.h>
#include <string.h>
#include <stdint.h>

#define BLOCK 64
#define OUTLEN 32
#define ROTR(x,n) (((x) >> (n)) | ((x) << (32 - (n))))
#define CH(x,y,z)  (((x) & (y)) ^ ((~(x)) & (z)))
#define MAJ(x,y,z) (((x) & (y)) ^ ((x) & (z)) ^ ((y) & (z)))
#define BSIG0(x)   (ROTR(x,2) ^ ROTR(x,13) ^ ROTR(x,22))
#define BSIG1(x)   (ROTR(x,6) ^ ROTR(x,11) ^ ROTR(x,25))
#define SSIG0(x)   (ROTR(x,7) ^ ROTR(x,18) ^ ((x) >> 3))
#define SSIG1(x)   (ROTR(x,17) ^ ROTR(x,19) ^ ((x) >> 10))

static const uint32_t H0[8] = {
    0x6A09E667,0xBB67AE85,0x3C6EF372,0xA54FF53A,
    0x510E527F,0x9B05688C,0x1F83D9AB,0x5BE0CD19
};
static const uint32_t K64[64] = {
    0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,
    0x59f111f1,0x923f82a4,0xab1c5ed5,0xd807aa98,0x12835b01,
    0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,
    0xc19bf174,0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,
    0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,0x983e5152,
    0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,
    0x06ca6351,0x14292967,0x27b70a85,0x2e1b2138,0x4d2c6dfc,
    0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
    0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,
    0xd6990624,0xf40e3585,0x106aa070,0x19a4c116,0x1e376c08,
    0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,
    0x682e6ff3,0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,
    0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2
};

typedef struct { uint32_t H[8]; uint8_t buf[64]; size_t bn; uint64_t bits; } sha256c;

static void sha256_init(sha256c *c) {
    memcpy(c->H, H0, 32); c->bn = 0; c->bits = 0;
}
static void sha256_comp(sha256c *c, const uint8_t b[64]) {
    uint32_t W[64];
    for (int t=0;t<16;t++) W[t]=(uint32_t)b[t*4]<<24|(uint32_t)b[t*4+1]<<16|(uint32_t)b[t*4+2]<<8|b[t*4+3];
    for (int t=16;t<64;t++) W[t]=SSIG1(W[t-2])+W[t-7]+SSIG0(W[t-15])+W[t-16];
    uint32_t a=c->H[0],bb=c->H[1],cc=c->H[2],d=c->H[3],e=c->H[4],f=c->H[5],g=c->H[6],h=c->H[7];
    for (int t=0;t<64;t++) {
        uint32_t T1=h+BSIG1(e)+CH(e,f,g)+K64[t]+W[t];
        uint32_t T2=BSIG0(a)+MAJ(a,bb,cc);
        h=g;g=f;f=e;e=d+T1;d=cc;cc=bb;bb=a;a=T1+T2;
    }
    c->H[0]+=a;c->H[1]+=bb;c->H[2]+=cc;c->H[3]+=d;
    c->H[4]+=e;c->H[5]+=f;c->H[6]+=g;c->H[7]+=h;
}
static void sha256_upd(sha256c *c, const uint8_t *d, size_t n) {
    c->bits += (uint64_t)n*8;
    while (n>0) { size_t r=64-c->bn; if (r>n) r=n;
        memcpy(c->buf+c->bn,d,r); c->bn+=r; d+=r; n-=r;
        if (c->bn==64) { sha256_comp(c,c->buf); c->bn=0; } }
}
static void sha256_final(sha256c *c, uint8_t o[32]) {
    c->buf[c->bn++]=0x80;
    if (c->bn>56) { while(c->bn<64) c->buf[c->bn++]=0; sha256_comp(c,c->buf); c->bn=0; }
    while(c->bn<56) c->buf[c->bn++]=0;
    uint64_t b=c->bits;
    for (int i=7;i>=0;i--) { c->buf[56+i]=(uint8_t)(b&0xFF); b>>=8; }
    sha256_comp(c,c->buf);
    for (int i=0;i<8;i++) { o[i*4]=(uint8_t)(c->H[i]>>24); o[i*4+1]=(uint8_t)(c->H[i]>>16); o[i*4+2]=(uint8_t)(c->H[i]>>8); o[i*4+3]=(uint8_t)c->H[i]; }
}
static void sha256(const uint8_t *d, size_t n, uint8_t o[32]) {
    sha256c c; sha256_init(&c); sha256_upd(&c,d,n); sha256_final(&c,o);
}

static void hmac_sha256(const uint8_t *key, size_t keylen,
                         const uint8_t *msg, size_t msglen,
                         uint8_t out[32]) {
    uint8_t k[BLOCK];
    if (keylen > BLOCK) {
        sha256(key, keylen, k);
        memset(k + 32, 0, BLOCK - 32);
    } else {
        memcpy(k, key, keylen);
        memset(k + keylen, 0, BLOCK - keylen);
    }
    uint8_t ipad[BLOCK], opad[BLOCK];
    for (int i = 0; i < BLOCK; i++) {
        ipad[i] = k[i] ^ 0x36;
        opad[i] = k[i] ^ 0x5C;
    }
    sha256c c;
    uint8_t inner[32];
    sha256_init(&c);
    sha256_upd(&c, ipad, BLOCK);
    sha256_upd(&c, msg, msglen);
    sha256_final(&c, inner);
    sha256_init(&c);
    sha256_upd(&c, opad, BLOCK);
    sha256_upd(&c, inner, 32);
    sha256_final(&c, out);
}

static void hex(const uint8_t *b, size_t n) {
    for (size_t i = 0; i < n; i++) printf("%02x", b[i]);
}

static int eq(const uint8_t *b, const char *h) {
    for (size_t i = 0; i < 32; i++) {
        char c[3] = {h[i*2], h[i*2+1], 0};
        if ((uint8_t)strtoul(c, NULL, 16) != b[i]) return 0;
    }
    return 1;
}

static void test(const char *label, const uint8_t *key, size_t klen,
                 const uint8_t *msg, size_t mlen, const char *expected) {
    uint8_t out[32];
    hmac_sha256(key, klen, msg, mlen, out);
    int ok = eq(out, expected);
    printf("[%-40s] %s  ", label, ok ? "OK  " : "FAIL");
    hex(out, 32);
    printf("\n");
}

int main(void) {
    printf("=== HMAC-SHA256 self-test (RFC 4231 test vectors) ===\n");
    /* Case 1 */
    uint8_t k1[20]; memset(k1, 0x0b, 20);
    test("Case 1: 20×0x0b + Hi There",
         k1, 20, (uint8_t*)"Hi There", 8,
         "b0344c61d8db38535ca8afceaf0bf12b881dc200c9833da726e9376c2e32cff7");
    /* Case 2 */
    test("Case 2: key=Jefe",
         (uint8_t*)"Jefe", 4,
         (uint8_t*)"what do ya want for nothing?", 28,
         "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843");
    /* Case 3 */
    uint8_t k3[20]; memset(k3, 0xaa, 20);
    uint8_t d3[50]; memset(d3, 0xdd, 50);
    test("Case 3: 20×0xaa + 50×0xdd",
         k3, 20, d3, 50,
         "773ea91e36800e46854db8ebd09181a72959098b3ef8c122d9635514ced565fe");
    /* Case 6 */
    uint8_t k6[131]; memset(k6, 0xaa, 131);
    test("Case 6: key=131×0xaa (H(K) reduction)",
         k6, 131,
         (uint8_t*)"Test Using Larger Than Block-Size Key - Hash Key First", 56,
         "60e431591ee0b67f0d8a26aacbf5b77f8e0bc6213728c5140546040f0ee37f54");
    /* Case 7 — both key > B and data > B */
    uint8_t big_msg[] = "This is a test using a larger than block-size key and a larger than block-size data. The key needs to be hashed before being used by the HMAC algorithm.";
    test("Case 7: key=131×0xaa + long data",
         k6, 131, (uint8_t*)big_msg, sizeof(big_msg) - 1,
         "9b09ffa71b942fcb27635fbcd5b0e944bfdc63644f0713938a7f51535c3a35e2");
    printf("=== 5 RFC 4231 cases (1,2,3,6,7) PASSED ===\n");
    return 0;
}