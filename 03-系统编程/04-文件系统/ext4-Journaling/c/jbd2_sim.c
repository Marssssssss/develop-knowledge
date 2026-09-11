/*
 * jbd2_sim.c — JBD2 journal 块格式 in-memory 模拟器
 *
 * 编译: gcc -O2 -Wall -Wextra -pedantic jbd2_sim.c -o jbd2_sim
 * 运行: ./jbd2_sim
 *
 * 演示:
 *   1. 写入一笔事务:descriptor + data + commit,展示字节布局
 *   2. Recovery:顺序扫描 journal,重放已 commit 事务,丢弃未 commit
 *   3. JBD2 大端 vs ext4 小端:同一份数据两种序列化
 *   4. 数据块前 4 字节 == magic 时 JBD2_FLAG_ESCAPE 处理
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <assert.h>

#define BLCKSIZE    4096
#define JBD2_MAGIC  0xC03B3998U
#define DESC_BLOCK  1
#define COMMIT_BLOCK 2
#define REVOKE_BLOCK 5
#define SAME_UUID   0x2
#define ESCAPE      0x1
#define LAST_TAG    0x8

/* helpers: big-endian 32-bit */
static void be32_pack(uint8_t *p, uint32_t v) {
    p[0] = (v >> 24) & 0xff;
    p[1] = (v >> 16) & 0xff;
    p[2] = (v >>  8) & 0xff;
    p[3] = (v >>  0) & 0xff;
}
static uint32_t be32_unpack(const uint8_t *p) {
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) |
           ((uint32_t)p[2] <<  8) | ((uint32_t)p[3] <<  0);
}

/* ---------- demo 1: write a transaction ---------- */
typedef struct { uint32_t blocknr; uint32_t flags; uint8_t uuid[16]; } tag_t;

static void make_descriptor(uint8_t *block, uint32_t seq, const tag_t *tags, int n_tags) {
    be32_pack(block + 0,  JBD2_MAGIC);
    be32_pack(block + 4,  DESC_BLOCK);
    be32_pack(block + 8,  seq);
    /* tags start at offset 12; each tag = 4 (blocknr) + 4 (flags) [+16 uuid if !SAME_UUID] */
    int off = 12;
    for (int i = 0; i < n_tags; i++) {
        uint32_t f = tags[i].flags | (i == n_tags - 1 ? LAST_TAG : 0);
        be32_pack(block + off, tags[i].blocknr); off += 4;
        be32_pack(block + off, f);               off += 4;
        if (!(f & SAME_UUID)) {
            memcpy(block + off, tags[i].uuid, 16);
            off += 16;
        }
    }
}

static void make_commit(uint8_t *block, uint32_t seq) {
    be32_pack(block + 0, JBD2_MAGIC);
    be32_pack(block + 4, COMMIT_BLOCK);
    be32_pack(block + 8, seq);
    /* commit_block header is 32 bytes; rest zero */
}

static void demo_write_transaction(void) {
    printf("\n=== demo 1: write a transaction (descriptor + data + commit) ===\n");
    uint8_t journal[BLCKSIZE * 4] = {0};

    /* seq=42, 两个 data block: 块 256 (inode) 和 块 257 (dir entry) */
    uint8_t uuid[16] = {0xAA,0xBB,0xCC,0xDD,0,0,0,0,0,0,0,0,0,0,0,0x01};
    tag_t tags[] = {
        { .blocknr = 256, .flags = SAME_UUID, .uuid = {0} },
        { .blocknr = 257, .flags = SAME_UUID, .uuid = {0} },
    };
    memcpy(tags[0].uuid, uuid, 16);
    memcpy(tags[1].uuid, uuid, 16);

    /* descriptor block */
    make_descriptor(journal, 42, tags, 2);

    /* data block #0: inode(模仿) */
    memset(journal + BLCKSIZE, 0, BLCKSIZE);
    memcpy(journal + BLCKSIZE, "INODE_BLOCK_FOR_FILE_42", 23);

    /* data block #1: dir entry */
    memset(journal + BLCKSIZE * 2, 0, BLCKSIZE);
    memcpy(journal + BLCKSIZE * 2, "DIRENT: hello.txt -> inode 42", 30);

    /* commit block */
    make_commit(journal + BLCKSIZE * 3, 42);

    printf("descriptor header bytes (12): ");
    for (int i = 0; i < 12; i++) printf("%02x ", journal[i]);
    printf("\n  expect: c0 3b 39 98  (magic)  00 00 00 01  (descriptor)  00 00 00 2a  (seq=42)\n");
    printf("descriptor tags (blocknr + flags):\n");
    printf("  tag 0: blocknr=256, flags=0x%x\n", be32_unpack(journal + 16));
    printf("  tag 1: blocknr=257, flags=0x%x  (LAST_TAG | SAME_UUID = 0x%x)\n",
           be32_unpack(journal + 24), LAST_TAG | SAME_UUID);
    printf("commit header bytes (12): ");
    for (int i = 0; i < 12; i++) printf("%02x ", journal[BLCKSIZE * 3 + i]);
    printf("\n  expect: c0 3b 39 98  00 00 00 02  00 00 00 2a\n");
}

/* ---------- demo 2: replay ---------- */
static void demo_replay(void) {
    printf("\n=== demo 2: replay committed, drop uncommitted ===\n");
    /* 构造两份事务:T1 完整(有 commit),T2 不完整(无 commit) */
    uint8_t log[2 * 4 * BLCKSIZE] = {0};      /* 最多 2 笔事务,每笔 ≤ 4 块 */
    int off = 0;

    /* T1 = descriptor + data + commit */
    tag_t t1[] = {{ 100, SAME_UUID, {0} }};
    make_descriptor(log + off, 10, t1, 1); off += BLCKSIZE;
    memset(log + off, 0, BLCKSIZE);
    memcpy(log + off, "T1_data_at_block_100", 21); off += BLCKSIZE;
    make_commit(log + off, 10); off += BLCKSIZE;

    /* T2 = descriptor + data,但 NO commit(模拟崩溃中途) */
    tag_t t2[] = {{ 200, SAME_UUID, {0} }};
    make_descriptor(log + off, 11, t2, 1); off += BLCKSIZE;
    memset(log + off, 0, BLCKSIZE);
    memcpy(log + off, "T2_data_at_block_200_orphan", 27); off += BLCKSIZE;

    int n_blocks = off / BLCKSIZE;
    printf("log contains %d blocks (3 from T1 + 2 from T2)\n", n_blocks);

    int replayed_txn = 0;
    int i = 0;
    while (i < n_blocks) {
        uint32_t magic = be32_unpack(log + i * BLCKSIZE);
        if (magic != JBD2_MAGIC) { i++; continue; }
        uint32_t btype = be32_unpack(log + i * BLCKSIZE + 4);
        uint32_t seq   = be32_unpack(log + i * BLCKSIZE + 8);

        if (btype == DESC_BLOCK) {
            /* 收集直到 commit block 或下一个 descriptor */
            int data_start = i + 1;
            int j = data_start;
            while (j < n_blocks) {
                uint32_t m2 = be32_unpack(log + j * BLCKSIZE);
                if (m2 != JBD2_MAGIC) { j++; continue; }
                uint32_t t2 = be32_unpack(log + j * BLCKSIZE + 4);
                if (t2 == DESC_BLOCK || t2 == REVOKE_BLOCK) break;
                if (t2 == COMMIT_BLOCK &&
                    be32_unpack(log + j * BLCKSIZE + 8) == seq) {
                    /* 找到匹配的 commit → replay */
                    printf("  ✓ replay T#%u (data blocks %d..%d)\n",
                           seq, data_start, j - 1);
                    replayed_txn++;
                    i = j + 1;
                    goto next;
                }
                j++;
            }
            /* 没有 commit → 丢弃 */
            printf("  ✗ drop T#%u (no commit block found)\n", seq);
            i = j;
        }
        next: ;
        i++;
    }
    printf("total replayed transactions: %d (expect 1)\n", replayed_txn);
    assert(replayed_txn == 1);
}

/* ---------- demo 3: big-endian vs little-endian ---------- */
static void demo_endian(void) {
    printf("\n=== demo 3: JBD2 big-endian vs ext4 little-endian ===\n");
    /* 同一 uint32 = 42 (0x2A),展示两种序列化 */
    uint32_t v = 42;
    uint8_t be[4], le[4];
    be32_pack(be, v);                  /* JBD2 风格 */
    le[0] = v & 0xff; le[1] = (v >> 8) & 0xff; le[2] = (v >> 16) & 0xff; le[3] = (v >> 24) & 0xff;
    printf("value 42 → JBD2 big-endian:    %02x %02x %02x %02x\n", be[0], be[1], be[2], be[3]);
    printf("value 42 → ext4 little-endian: %02x %02x %02x %02x\n", le[0], le[1], le[2], le[3]);
    printf("  → 解析 real ext4 journal 必须用 be32_to_cpu()/cpu_to_be32()\n");
}

/* ---------- demo 4: ESCAPE flag ---------- */
static void demo_escape(void) {
    printf("\n=== demo 4: JBD2_FLAG_ESCAPE — 当 data 前 4 字节 == magic ===\n");
    /* 模拟 data block 起始 4 字节是 0xC03B3998(罕见但可能,例如压缩磁盘块或特定 mkfs 工具数据) */
    uint8_t block[BLCKSIZE] = {0};
    be32_pack(block, JBD2_MAGIC);              /* 起始 = magic */
    memcpy(block + 4, "rest of payload...", 18);

    /* JBD2 写入时:检测到前 4B == magic → 清零 + set ESCAPE flag */
    uint8_t on_disk[BLCKSIZE];
    memcpy(on_disk, block, BLCKSIZE);
    uint8_t needs_escape = (be32_unpack(on_disk) == JBD2_MAGIC);
    if (needs_escape) {
        memset(on_disk, 0, 4);                 /* 前 4 字节清零 */
    }

    /* replay 时:descriptor tag.flags 含 ESCAPE → 把前 4 字节还原为 magic */
    uint8_t recovered[BLCKSIZE];
    memcpy(recovered, on_disk, BLCKSIZE);
    if (needs_escape) {
        be32_pack(recovered, JBD2_MAGIC);
    }
    memcpy(recovered + 4, block + 4, BLCKSIZE - 4);

    printf("original block[0..7] : ");
    for (int i = 0; i < 8; i++) printf("%02x ", block[i]);
    printf("\non-disk[0..7]        : ");
    for (int i = 0; i < 8; i++) printf("%02x ", on_disk[i]);
    printf("  (前 4B 清零 + set ESCAPE)\n");
    printf("recovered[0..7]      : ");
    for (int i = 0; i < 8; i++) printf("%02x ", recovered[i]);
    printf("  (前 4B 还原回 magic)\n");
}

int main(void) {
    demo_write_transaction();
    demo_replay();
    demo_endian();
    demo_escape();
    printf("\nall 4 demos done\n");
    return 0;
}