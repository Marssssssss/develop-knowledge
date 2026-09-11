/* B+ Tree index - public interface and types.
 *
 * Mirrors the Python bptree.py module; uses M = 4 (max 3 keys per node).
 *
 * Conventions:
 *   * Inner node: keys[0..nk-1] are ascending; children[0..nk] with the
 *     convention that children[i+1]'s smallest key == keys[i]. The first
 *     child (children[0]) has no preceding separator.
 *   * Leaf node: keys[0..nk-1] ascending; vals[i] paired with keys[i];
 *     `next` is the right sibling for range scans.
 *   * Splits propagate upward; the root grows when the root itself splits.
 *   * Delete rebalances via borrow-from-sibling, else merges with sibling.
 */
#ifndef BPTREE_H
#define BPTREE_H

#include <stddef.h>

#define BPT_M              4     /* M-way search tree */
#define BPT_MAX_KEYS       (BPT_M - 1)            /* 3 */
#define BPT_MIN_KEYS_LEAF  ((BPT_M + 1) / 2 - 1)  /* 1 */
#define BPT_MIN_KEYS_INNER (BPT_M / 2 - 1)        /* 1 */

typedef struct BptNode {
    int is_leaf;
    int nk;
    int keys[BPT_MAX_KEYS + 1];         /* +1 for split overflow */
    union {
        struct {
            char vals[BPT_MAX_KEYS + 1][32];
            struct BptNode *next;
        } leaf;
        struct {
            struct BptNode *children[BPT_M + 1]; /* +1 for split overflow */
        } inner;
    } u;
} BptNode;

/* Constructors (caller owns the returned node, must free with bpt_free). */
BptNode *bpt_leaf_new(void);
BptNode *bpt_inner_new(void);

/* Read: point lookup (NULL if absent). */
const char *bpt_search(BptNode *root, int key);

/* Read: range scan, returns matching pairs in sorted order. */
void bpt_range_query(BptNode *root, int lo, int hi_inclusive,
                     int *out_keys, char *out_vals[], int *out_n);

/* Write: insert (key, value). Updates value if key exists. Returns (new) root. */
BptNode *bpt_insert(BptNode *root, int key, const char *value);

/* Write: delete key. Returns (possibly collapsed) root. */
BptNode *bpt_delete(BptNode *root, int key);

/* Bulk-load: build a B+ tree from a SORTED (key[], vals[]) array of length n.
 * O(N) versus O(N log N) for repeated inserts. Returns the new root. */
BptNode *bpt_bulk_load(const int *keys, const char *const *vals, int n);

/* Free the entire tree (recursive). */
void bpt_free(BptNode *root);

/* Print sideways ASCII tree (for debugging/demo). */
void bpt_visualize(const BptNode *root, const char *prefix, int is_last);

/* Count leaves in the tree. */
int bpt_count_leaves(const BptNode *root);

#endif /* BPTREE_H */