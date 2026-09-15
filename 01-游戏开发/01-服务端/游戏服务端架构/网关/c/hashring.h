/* hashring.h — uid 一致性哈希环(供 gateway.c 使用)
 *
 * 编译: gcc -O2 -Wall -Wextra -pedantic gateway.c hashring.c -o gateway
 */
#ifndef HASHRING_H
#define HASHRING_H

#define VNODES   512                 /* 每物理节点的虚拟节点数 */
#define MAXRING  (VNODES * 16)
#define MAX_UIDS 20000

typedef struct { unsigned hash; int owner; } RingEnt;

typedef struct {
    RingEnt e[MAXRING];
    int     n;
} Ring;

typedef struct { double modulo, ring, ideal; } Remap;
typedef struct { double ring_max_dev, modulo_max_dev; } LoadStat;

unsigned fnv1a32(const char *s);
void     ring_build(Ring *r, int n_nodes);
int      ring_pick(const Ring *r, int uid);
Remap    remap_rate(int before, int after);
LoadStat ring_load(int nodes);

#endif /* HASHRING_H */
