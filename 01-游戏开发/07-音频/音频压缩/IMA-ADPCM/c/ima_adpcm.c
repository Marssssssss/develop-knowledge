/*
 * ima_adpcm.c — IMA ADPCM(自适应差分脉冲编码)最小实现
 *
 * 演示内容:
 *   1. 16-bit PCM -> 4-bit IMA ADPCM 流式编解码(压缩比 4:1)
 *   2. 单 nibble 误码的传播与自恢复特性
 *   3. 块结构(4 字节头部 + nibble 数据)支持的随机访问解码
 *
 * 算法依据:
 *   - Davis Yen Pan, "Digital Audio Compression",
 *     Digital Technical Journal Vol.5 No.2, Spring 1993(第 3 节 IMA ADPCM)
 *   - RFC 3551 Section 4.5.1(DVI4 = IMA ADPCM 的 RTP 封装)
 */

#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

#define PI 3.14159265358979323846
#define SAMPLE_RATE 8000
#define NUM_SAMPLES 2500 /* 1000 静音段 + 500 突变段 + 1000 回落段 */
#define BLOCK_SAMPLES 256
#define BLOCK_HEADER 4 /* int16 predictor + uint8 index + uint8 reserved */

/* 步长表:89 级,近似指数增长(每级约为前级 1.1 倍),7 -> 32767。
 * 注:论文 Table 2 索引 84 处印作 22358,标准实现(ffmpeg/IMA 规范)为 22385,
 * 此处以标准实现为准(见 README"注意事项")。 */
static const int16_t step_table[89] = {
    7,     8,     9,    10,    11,    12,    13,    14,    16,    17,
    19,    21,    23,    25,    28,    31,    34,    37,    41,    45,
    50,    55,    60,    66,    73,    80,    88,    97,   107,   118,
   130,   143,   157,   173,   190,   209,   230,   253,   279,   307,
   337,   371,   408,   449,   494,   544,   598,   658,   724,   796,
   876,   963,  1060,  1166,  1282,  1411,  1552,  1707,  1878,  2066,
  2272,  2499,  2749,  3024,  3327,  3660,  4026,  4428,  4871,  5358,
  5894,  6484,  7132,  7845,  8630,  9493, 10442, 11487, 12635, 13899,
 15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767
};

/* 索引调整表:幅值小的码字(0-3 / 8-11)把索引 -1(步长收缩),
 * 幅值大的码字(4-7 / 12-15)把索引 +2/+4/+6/+8(步长扩张)。 */
static const int8_t index_table[16] = {
    -1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8
};

typedef struct {
    int32_t predictor; /* 上一个重建样本(即"预测器") */
    int index;         /* 步长表索引,0..88 */
} AdpcmState;

static void state_reset(AdpcmState *st) {
    st->predictor = 0;
    st->index = 0;
}

/* 解码单个 4-bit 码字,同时推进状态(对应论文图 2b 解码器) */
static int16_t adpcm_decode_sample(uint8_t code, AdpcmState *st) {
    int32_t step = step_table[st->index];
    /* step>>3 为半步长偏置:使重建值落在量化区间中部,消除系统性偏差 */
    int32_t diff = step >> 3;
    if (code & 4) diff += step;
    if (code & 2) diff += step >> 1;
    if (code & 1) diff += step >> 2;
    if (code & 8) diff = -diff;

    st->predictor += diff; /* 预测器 = 上一重建样本(一阶延迟) */
    if (st->predictor > 32767) st->predictor = 32767;
    if (st->predictor < -32768) st->predictor = -32768;

    st->index += index_table[code];
    if (st->index < 0) st->index = 0;
    if (st->index > 88) st->index = 88;
    return (int16_t)st->predictor;
}

/* 编码单个样本:符号位 + 三级阈值比较(对应论文图 3 量化流程图)。
 * 编码器内嵌同一解码器推进状态 —— 论文图 2a:编码器复用解码器组件,
 * 保证收发两端状态逐样本同步。 */
static uint8_t adpcm_encode_sample(int16_t sample, AdpcmState *st) {
    int32_t diff = (int32_t)sample - st->predictor;
    uint8_t code = 0;
    if (diff < 0) { code = 8; diff = -diff; }
    int32_t step = step_table[st->index];
    if (diff >= step)        { code |= 4; diff -= step; }
    if (diff >= step >> 1)   { code |= 2; diff -= step >> 1; }
    if (diff >= step >> 2)   { code |= 1; }
    adpcm_decode_sample(code, st);
    return code;
}

/* ---------- 流式编解码:连续 nibble 流,IMA WAV 打包惯例 ---------- */

/* 偶数样本放低 4 位(LSB-first);与 RTP DVI4 的高 4 位优先相反 */
static size_t encode_stream(const int16_t *pcm, size_t n, uint8_t *out) {
    AdpcmState st;
    state_reset(&st);
    for (size_t i = 0; i < n; i++) {
        uint8_t code = adpcm_encode_sample(pcm[i], &st);
        if (i % 2 == 0) out[i / 2] = (uint8_t)code;
        else            out[i / 2] |= (uint8_t)(code << 4);
    }
    return (n + 1) / 2;
}

static void decode_stream(const uint8_t *data, size_t n, int16_t *out) {
    AdpcmState st;
    state_reset(&st);
    for (size_t i = 0; i < n; i++) {
        uint8_t code = (i % 2 == 0) ? (data[i / 2] & 0x0F)
                                    : (uint8_t)(data[i / 2] >> 4);
        out[i] = adpcm_decode_sample(code, &st);
    }
}

/* ---------- 块编解码:每块 4 字节头部 + nibble 数据,支持随机访问 ---------- */

/* 头部布局(参照 RFC 3551 DVI4 块头):
 *   [0..1] int16 predictor(小端,块首预测值)
 *   [2]    uint8 index(块首步长表索引)
 *   [3]    uint8 reserved(发送方置 0) */
static size_t encode_blocks(const int16_t *pcm, size_t n, uint8_t *out) {
    AdpcmState st;
    state_reset(&st);
    size_t pos = 0;
    for (size_t off = 0; off < n; off += BLOCK_SAMPLES) {
        size_t cnt = (n - off < BLOCK_SAMPLES) ? n - off : BLOCK_SAMPLES;
        out[pos++] = (uint8_t)(st.predictor & 0xFF);
        out[pos++] = (uint8_t)((st.predictor >> 8) & 0xFF);
        out[pos++] = (uint8_t)st.index;
        out[pos++] = 0;
        for (size_t i = 0; i < cnt; i++) {
            uint8_t code = adpcm_encode_sample(pcm[off + i], &st);
            if (i % 2 == 0) out[pos] = code;
            else            out[pos++] |= (uint8_t)(code << 4);
        }
        if (cnt % 2 == 1) pos++; /* 末块奇数个样本,高 4 位留 0 */
    }
    return pos;
}

/* 从指定块号开始解码(随机访问:仅依赖该块头部状态)。
 * 除末块外每块固定 128 字节数据(256 样本);末块可能不足。 */
static size_t decode_blocks_from(const uint8_t *blocks, size_t total,
                                 size_t start_block, int16_t *out) {
    size_t pos = 0, block = 0, out_n = 0;
    size_t full_data = BLOCK_SAMPLES / 2;
    while (pos + BLOCK_HEADER <= total) {
        size_t body = total - pos - BLOCK_HEADER;
        size_t data_bytes = (body >= full_data) ? full_data : body;
        size_t cnt = data_bytes * 2;
        if (block >= start_block) {
            AdpcmState st;
            st.predictor = (int16_t)(blocks[pos] | (blocks[pos + 1] << 8));
            st.index = blocks[pos + 2];
            for (size_t i = 0; i < cnt; i++) {
                uint8_t code = (i % 2 == 0) ? (blocks[pos + BLOCK_HEADER + i / 2] & 0x0F)
                                            : (uint8_t)(blocks[pos + BLOCK_HEADER + i / 2] >> 4);
                out[out_n++] = adpcm_decode_sample(code, &st);
            }
        }
        pos += BLOCK_HEADER + data_bytes;
        block++;
    }
    return out_n;
}

/* ---------- 统计与演示 ---------- */

static void error_stats(const int16_t *orig, const int16_t *dec,
                        size_t lo, size_t hi, int32_t *max_abs, double *mean_abs) {
    int32_t m = 0;
    double sum = 0;
    for (size_t i = lo; i < hi; i++) {
        int32_t e = orig[i] - dec[i];
        if (e < 0) e = -e;
        if (e > m) m = e;
        sum += (double)e;
    }
    *max_abs = m;
    *mean_abs = sum / (double)(hi - lo);
}

static void gen_signal(int16_t *pcm) {
    for (int i = 0; i < NUM_SAMPLES; i++) {
        double t = (double)i / (double)SAMPLE_RATE;
        /* 幅度 600 -> 12000 -> 600:制造 20 倍瞬时跳变,逼出步长自适应 */
        double amp = (i < 1000) ? 600.0 : (i < 1500) ? 12000.0 : 600.0;
        double v = amp * sin(2.0 * PI * 440.0 * t);
        if (v > 32767.0) v = 32767.0;
        if (v < -32768.0) v = -32768.0;
        pcm[i] = (int16_t)v;
    }
}

int main(void) {
    static int16_t pcm[NUM_SAMPLES], dec[NUM_SAMPLES], dec2[NUM_SAMPLES];
    static uint8_t stream[(NUM_SAMPLES + 1) / 2];
    static uint8_t blocks[NUM_SAMPLES / 2 + 16 * BLOCK_HEADER];

    gen_signal(pcm);
    printf("=== IMA ADPCM demo ===\n");
    printf("采样率 %d Hz, 样本数 %d (PCM %d 字节)\n",
           SAMPLE_RATE, NUM_SAMPLES, NUM_SAMPLES * 2);

    /* [1] 流式编解码 */
    size_t nbytes = encode_stream(pcm, NUM_SAMPLES, stream);
    decode_stream(stream, NUM_SAMPLES, dec);
    int32_t maxe; double meane;
    error_stats(pcm, dec, 0, NUM_SAMPLES, &maxe, &meane);
    printf("[1] 流式编解码\n");
    printf("    编码后 %zu 字节, 压缩比 %.2f:1 (理论 4:1)\n",
           nbytes, (double)(NUM_SAMPLES * 2) / (double)nbytes);
    printf("    全段误差: 最大 %d, 平均 %.1f\n", maxe, meane);
    int32_t m1, m2, m3; double d1, d2, d3;
    error_stats(pcm, dec, 0, 1000, &m1, &d1);
    error_stats(pcm, dec, 1000, 1500, &m2, &d2);
    error_stats(pcm, dec, 1500, NUM_SAMPLES, &m3, &d3);
    printf("    分段最大误差: 静音段 %d / 突变段 %d / 回落段 %d\n", m1, m2, m3);

    /* [2] 抗误码:篡改中间 1 个 nibble */
    stream[NUM_SAMPLES / 4] ^= 0x0F;
    decode_stream(stream, NUM_SAMPLES, dec2);
    printf("[2] 抗误码: 篡改位置 %d 处 1 个 nibble\n", NUM_SAMPLES / 2);
    printf("    损坏点误差: %d\n", abs(pcm[NUM_SAMPLES / 2] - dec2[NUM_SAMPLES / 2]));
    printf("    之后 100 样本: %d / 之后 500 样本: %d",
           abs(pcm[NUM_SAMPLES / 2 + 100] - dec2[NUM_SAMPLES / 2 + 100]),
           abs(pcm[NUM_SAMPLES / 2 + 500] - dec2[NUM_SAMPLES / 2 + 500]));
    printf("  (步长已收敛, 残差为恒定直流偏置)\n");
    stream[NUM_SAMPLES / 4] ^= 0x0F; /* 还原 */

    /* [3] 块随机访问 */
    size_t btotal = encode_blocks(pcm, NUM_SAMPLES, blocks);
    decode_stream(stream, NUM_SAMPLES, dec); /* 无损坏基准 */
    size_t got = decode_blocks_from(blocks, btotal, 5, dec2);
    int same = 1;
    for (size_t i = 0; i < got; i++)
        if (dec2[i] != dec[5 * BLOCK_SAMPLES + i]) { same = 0; break; }
    size_t nblocks = (NUM_SAMPLES + BLOCK_SAMPLES - 1) / BLOCK_SAMPLES;
    printf("[3] 块结构: 共 %zu 字节 (含 %zu 个块头), 压缩比 %.2f:1\n",
           btotal, nblocks, (double)(NUM_SAMPLES * 2) / (double)btotal);
    printf("    从第 5 块头部随机访问解码 %zu 样本, 与全量解码一致: %s\n",
           got, same ? "是" : "否");
    return 0;
}
