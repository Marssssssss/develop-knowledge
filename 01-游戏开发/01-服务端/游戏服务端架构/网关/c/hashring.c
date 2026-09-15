/* hashring.c — uid 一致性哈希环实现(与 python/gateway.py 的 RingPicker 等价)
 *
 * 一致性哈希: 节点与虚拟节点按哈希排成环, 取 uid 的顺时针后继。
 * 虚拟节点把每个物理节点的环上区间打散 -> 负载更均匀;
 * 增删节点时只影响相邻区间的 key -> 重映射率约 1/N(而非取模的几乎全员)。
 */
#include "hashring.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

unsigned fnv1a32(const char *s)
{
    unsigned h = 0x811C9DC5u;
    for (; *s; s++) {
        h ^= (unsigned char)*s;
        h *= 0x01000193u;
    }
    return h;
}

static int cmp_ring(const void *a, const void *b)
{
    const RingEnt *x = a, *y = b;
    if (x->hash < y->hash) return -1;
    if (x->hash > y->hash) return 1;
    return x->owner - y->owner;      /* 哈希冲突时按 owner 定序, 保证确定性 */
}

void ring_build(Ring *r, int n_nodes)
{
    char key[64];
    int node, v;
    r->n = 0;
    for (node = 0; node < n_nodes; node++)
        for (v = 0; v < VNODES; v++) {
            snprintf(key, sizeof(key), "inst%d#%d", node, v);
            r->e[r->n].hash = fnv1a32(key);
            r->e[r->n].owner = node;
            r->n++;
        }
    qsort(r->e, (size_t)r->n, sizeof(RingEnt), cmp_ring);
}

/* 二分找到第一个 hash >= h 的位置, 越界则回绕到 0 */
int ring_pick(const Ring *r, int uid)
{
    char key[32];
    unsigned h;
    int lo = 0, hi = r->n;
    snprintf(key, sizeof(key), "uid:%d", uid);
    h = fnv1a32(key);
    while (lo < hi) {
        int mid = (lo + hi) / 2;
        if (r->e[mid].hash < h) lo = mid + 1;
        else hi = mid;
    }
    if (lo == r->n) lo = 0;
    return r->e[lo].owner;
}

Remap remap_rate(int before, int after)
{
    static Ring rb, ra;              /* 每个 Ring 约 64 KB, 放静态区避免爆栈 */
    int moved_m = 0, moved_r = 0, u;
    Remap res;
    ring_build(&rb, before);
    ring_build(&ra, after);
    for (u = 0; u < MAX_UIDS; u++) {
        if (u % before != u % after) moved_m++;
        if (ring_pick(&rb, u) != ring_pick(&ra, u)) moved_r++;
    }
    res.modulo = (double)moved_m / MAX_UIDS;
    res.ring = (double)moved_r / MAX_UIDS;
    res.ideal = 1.0 / (before > after ? before : after);
    return res;
}

LoadStat ring_load(int nodes)
{
    static Ring r;
    int cnt[16], i, u, mx_r = 0, mx_m = 0;
    double mean;
    LoadStat st;
    memset(cnt, 0, sizeof(cnt));
    ring_build(&r, nodes);
    for (u = 0; u < MAX_UIDS; u++) cnt[ring_pick(&r, u)]++;
    mean = (double)MAX_UIDS / nodes;
    for (i = 0; i < nodes; i++) {
        int d = cnt[i] > mean ? (int)(cnt[i] - mean) : (int)(mean - cnt[i]);
        if (d > mx_r) mx_r = d;
    }
    st.ring_max_dev = mx_r / mean;
    for (i = 0; i < nodes; i++) {              /* 取模: 余数先到先得, 完全均匀 */
        int c = MAX_UIDS / nodes + (i < MAX_UIDS % nodes ? 1 : 0);
        int d = c > mean ? (int)(c - mean) : (int)(mean - c);
        if (d > mx_m) mx_m = d;
    }
    st.modulo_max_dev = mx_m / mean;
    return st;
}
