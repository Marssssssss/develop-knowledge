/* Neo4j 存储层定长记录 + 双向链表 —— C 版。
 *
 * 权威来源：
 *   - Neo4j KB "Understanding Neo4j's data on disk"
 *     https://neo4j.com/developer/kb/understanding-data-on-disk/
 *     "neostore.nodestore.db  15 B  Nodes"
 *     "neostore.relationshipstore.db  34 B  Relationships"
 *   - neo4j-contrib Glossary
 *     https://github.com/neo4j-contrib/neo4j-org/wiki/Glossary
 *     "Each RelationshipRecord is a fixed length consisting of 33 bytes"
 *   - Angles & Gutierrez "Demystifying Graph Databases" (arXiv 1910.09017v6)
 *     https://arxiv.org/pdf/1910.09017v6
 *     "An edge is stored once, but is part of two such linked lists"
 *
 * 本 demo 用 mmap-like 数组模拟"定长记录文件" + 双向链表。
 * NodeRecord: 15 B  RelRecord: 34 B（与 KB 文章保持一致）
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define NODE_SIZE 15
#define REL_SIZE  34
#define MAXN      64
#define MAXR      256

/* ── NodeRecord（15 B）：in_use(1) labels(1) first_rel(4) first_prop(4) extra(5) ── */
typedef struct {
    unsigned char in_use;
    unsigned char labels_bits;
    unsigned int  first_rel;
    unsigned int  first_prop;
    unsigned char extra[5];
} NodeRecord;

/* ── RelRecord（34 B）：in_use(1) type(1) src(4) dst(4) first_prop(4)
 *                       prev_out(4) next_out(4) prev_in(4) next_in(4) extra(2) ── */
typedef struct {
    unsigned char in_use;
    unsigned char type_id;
    unsigned int  src, dst;
    unsigned int  first_prop;
    unsigned int  prev_out, next_out;
    unsigned int  prev_in,  next_in;
    unsigned char extra[2];
} RelRecord;

static NodeRecord nodes[MAXN];
static RelRecord  rels[MAXR];
static int ncount = 0, rcount = 0;

static void init_node(NodeRecord *n, unsigned char labels) {
    memset(n, 0, sizeof(*n));
    n->in_use = 1; n->labels_bits = labels;
}

static int new_node(unsigned char labels) {
    init_node(&nodes[ncount], labels);
    nodes[ncount].first_rel = REL_NULL;
    return ncount++;
}

static int new_rel(unsigned char type_id, int src, int dst) {
    RelRecord *r = &rels[rcount];
    memset(r, 0, sizeof(*r));
    r->in_use = 1;
    r->type_id = type_id;
    r->src = src; r->dst = dst;
    /* 挂到 src.out 双向链表头 */
    unsigned int old_out = nodes[src].first_rel;
    unsigned int old_in  = nodes[dst].first_rel;
    r->next_out = old_out;
    r->next_in  = old_in;
    r->prev_out = REL_NULL;
    r->prev_in  = REL_NULL;
    nodes[src].first_rel = rcount;
    nodes[dst].first_rel = rcount;
    if (old_out != REL_NULL) rels[old_out].prev_out = rcount;
    if (old_in  != REL_NULL) rels[old_in].prev_in   = rcount;
    return rcount++;
}

/* 邻接遍历：node.first_rel → rel.next_out → ... */
static void print_neighbors(int nid) {
    printf("node[%d] out neighbors:", nid);
    unsigned int cur = nodes[nid].first_rel;
    while (cur != REL_NULL) {
        printf(" (dst=%u, rid=%u)", rels[cur].dst, cur);
        cur = rels[cur].next_out;
    }
    printf("\n");
}

int main(void) {
    memset(nodes, 0, sizeof(nodes));
    memset(rels,  0, sizeof(rels));

    int n0 = new_node(0x01);   /* Person */
    int n1 = new_node(0x02);   /* Movie  */
    int n2 = new_node(0x01);
    int n3 = new_node(0x02);

    new_rel(1, n0, n1);
    new_rel(1, n2, n1);
    new_rel(1, n0, n3);

    printf("node[%d] first_rel = %u (链头 rid)\n", n0, nodes[n0].first_rel);
    print_neighbors(n0);
    print_neighbors(n1);
    print_neighbors(n2);

    printf("\n--- RelRecord 自检 ---\n");
    for (int i = 0; i < rcount; i++) {
        unsigned int po = rels[i].prev_out;
        unsigned int no = rels[i].next_out;
        printf("rid=%d type=%u %u->%u prev_out=%s next_out=%s\n",
               i, rels[i].type_id, rels[i].src, rels[i].dst,
               po == REL_NULL ? "NULL" : "(see rid)",
               no == REL_NULL ? "NULL" : "(see rid)");
    }
    printf("\nNeo4j 定长记录大小：NodeRecord = %d B, RelRecord = %d B\n",
           NODE_SIZE, REL_SIZE);
    printf("无索引邻接：通过 node.first_rel + rel.next_out O(d) 遍历 d 度\n");
    return 0;
}