/* B+ Tree index - implementation.
 *
 * See bptree.h for the public API and invariants. This file holds the
 * structural logic (search, insert, delete, bulk_load); main.c is the
 * demo entry point with visualization.
 */
#include "bptree.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* ---- constructors ---- */

BptNode *bpt_leaf_new(void) {
    BptNode *n = calloc(1, sizeof(BptNode));
    n->is_leaf = 1;
    return n;
}

BptNode *bpt_inner_new(void) {
    BptNode *n = calloc(1, sizeof(BptNode));
    n->is_leaf = 0;
    return n;
}

void bpt_free(BptNode *root) {
    if (!root) return;
    if (!root->is_leaf) {
        for (int i = 0; i <= root->nk; i++)
            bpt_free(root->u.inner.children[i]);
    }
    free(root);
}

/* ---- binary search helpers ---- */

/* Smallest i such that keys[i] > k; else nk. (descend left of i) */
static int child_idx(BptNode *n, int k) {
    int lo = 0, hi = n->nk;
    while (lo < hi) {
        int mid = (lo + hi) / 2;
        if (n->keys[mid] <= k) lo = mid + 1;
        else hi = mid;
    }
    return lo;
}

/* Smallest i such that keys[i] >= k; else len. */
static int key_idx(const int *keys, int len, int k) {
    int lo = 0, hi = len;
    while (lo < hi) {
        int mid = (lo + hi) / 2;
        if (keys[mid] < k) lo = mid + 1;
        else hi = mid;
    }
    return lo;
}

/* ---- search / range ---- */

const char *bpt_search(BptNode *root, int k) {
    BptNode *n = root;
    while (!n->is_leaf) n = n->u.inner.children[child_idx(n, k)];
    int i = key_idx(n->keys, n->nk, k);
    return (i < n->nk && n->keys[i] == k) ? n->u.leaf.vals[i] : NULL;
}

void bpt_range_query(BptNode *root, int lo, int hi_inclusive,
                     int *out_keys, char *out_vals[], int *out_n) {
    *out_n = 0;
    if (!root) return;
    BptNode *n = root;
    while (!n->is_leaf) n = n->u.inner.children[child_idx(n, lo)];
    while (n) {
        for (int i = 0; i < n->nk; i++) {
            if (n->keys[i] < lo) continue;
            if (n->keys[i] > hi_inclusive) return;
            out_keys[*out_n] = n->keys[i];
            out_vals[*out_n] = n->u.leaf.vals[i];
            (*out_n)++;
        }
        n = n->u.leaf.next;
    }
}

/* ---- split primitives ---- */

/* Split over-full leaf in half; return (copy-up key, new right leaf). */
static int split_leaf(BptNode *L, BptNode **out_right) {
    int mid = L->nk / 2;
    BptNode *R = bpt_leaf_new();
    R->nk = L->nk - mid;
    for (int i = 0; i < R->nk; i++) {
        R->keys[i] = L->keys[mid + i];
        strcpy(R->u.leaf.vals[i], L->u.leaf.vals[mid + i]);
    }
    L->nk = mid;
    R->u.leaf.next = L->u.leaf.next;
    L->u.leaf.next = R;
    *out_right = R;
    return R->keys[0];  /* copy up */
}

/* Split over-full inner node; return (push-up key, new right inner). */
static int split_inner(BptNode *I, BptNode **out_right) {
    int mid = I->nk / 2;
    int up = I->keys[mid];
    BptNode *R = bpt_inner_new();
    R->nk = I->nk - mid - 1;
    for (int i = 0; i < R->nk; i++) {
        R->keys[i] = I->keys[mid + 1 + i];
        R->u.inner.children[i] = I->u.inner.children[mid + 1 + i];
    }
    R->u.inner.children[R->nk] = I->u.inner.children[I->nk];
    I->nk = mid;
    *out_right = R;
    return up;
}

/* ---- insert ---- */

static int leaf_insert(BptNode *L, int k, const char *v) {
    int i = key_idx(L->keys, L->nk, k);
    if (i < L->nk && L->keys[i] == k) {
        strcpy(L->u.leaf.vals[i], v);
        return 0;
    }
    for (int j = L->nk; j > i; j--) {
        L->keys[j] = L->keys[j - 1];
        strcpy(L->u.leaf.vals[j], L->u.leaf.vals[j - 1]);
    }
    L->keys[i] = k;
    strcpy(L->u.leaf.vals[i], v);
    L->nk++;
    return 1;
}

BptNode *bpt_insert(BptNode *root, int k, const char *v) {
    BptNode *path[64];
    int idx_path[64];
    int depth = 0;

    BptNode *n = root;
    while (!n->is_leaf) {
        int i = child_idx(n, k);
        path[depth] = n;
        idx_path[depth] = i;
        depth++;
        n = n->u.inner.children[i];
    }

    if (!leaf_insert(n, k, v)) return root;

    int pushed_key = 0;
    BptNode *pushed_node = NULL;
    BptNode *child = n;
    if (child->nk > BPT_MAX_KEYS) {
        pushed_key = split_leaf(child, &pushed_node);
    }

    while (pushed_node && depth > 0) {
        depth--;
        BptNode *par = path[depth];
        int idx = idx_path[depth];
        for (int j = par->nk; j > idx; j--) par->keys[j] = par->keys[j - 1];
        par->keys[idx] = pushed_key;
        for (int j = par->nk + 1; j > idx + 1; j--)
            par->u.inner.children[j] = par->u.inner.children[j - 1];
        par->u.inner.children[idx + 1] = pushed_node;
        par->nk++;
        if (par->nk <= BPT_MAX_KEYS) {
            pushed_node = NULL;
            break;
        }
        pushed_key = split_inner(par, &pushed_node);
    }

    if (pushed_node) {
        /* Root itself split -> grow a new root. `par` (the just-split
         * inner, now the left half) is the same object as the original
         * `root` pointer because the while loop popped the only path
         * entry; OR `root` itself is the leaf we split (depth was 0 the
         * whole time). Either way, `root` now refers to the left half. */
        BptNode *nr = bpt_inner_new();
        nr->keys[0] = pushed_key;
        nr->u.inner.children[0] = root;
        nr->u.inner.children[1] = pushed_node;
        nr->nk = 1;
        return nr;
    }
    return root;
}

/* ---- delete helpers ---- */

/* Borrow one entry from a sibling so the under-full child stays >= half full.
 * Returns 1 on success, 0 if no sibling has spare. */
static int borrow_from_sibling(BptNode *parent, int idx) {
    BptNode *L = parent->u.inner.children[idx];
    if (idx + 1 < parent->nk + 1) {
        BptNode *R = parent->u.inner.children[idx + 1];
        int minK = L->is_leaf ? BPT_MIN_KEYS_LEAF : BPT_MIN_KEYS_INNER;
        if (R->nk > minK) {
            if (L->is_leaf) {
                /* Borrow first of R -> end of L; separator <- R->keys[0]. */
                L->keys[L->nk] = R->keys[0];
                strcpy(L->u.leaf.vals[L->nk], R->u.leaf.vals[0]);
                L->nk++;
                for (int i = 0; i < R->nk - 1; i++) {
                    R->keys[i] = R->keys[i + 1];
                    strcpy(R->u.leaf.vals[i], R->u.leaf.vals[i + 1]);
                }
                R->nk--;
                parent->keys[idx] = R->keys[0];
            } else {
                /* Rotate through parent key. */
                L->keys[L->nk] = parent->keys[idx];
                L->u.inner.children[L->nk + 1] = R->u.inner.children[0];
                L->nk++;
                parent->keys[idx] = R->keys[0];
                for (int i = 0; i < R->nk - 1; i++) {
                    R->keys[i] = R->keys[i + 1];
                    R->u.inner.children[i] = R->u.inner.children[i + 1];
                }
                R->u.inner.children[R->nk - 1] = R->u.inner.children[R->nk];
                R->nk--;
            }
            return 1;
        }
    }
    if (idx > 0) {
        BptNode *S = parent->u.inner.children[idx - 1];
        int minK = L->is_leaf ? BPT_MIN_KEYS_LEAF : BPT_MIN_KEYS_INNER;
        if (S->nk > minK) {
            if (L->is_leaf) {
                /* Borrow last of S -> front of L; separator <- L->keys[0]. */
                for (int i = L->nk; i > 0; i--) {
                    L->keys[i] = L->keys[i - 1];
                    strcpy(L->u.leaf.vals[i], L->u.leaf.vals[i - 1]);
                }
                L->keys[0] = S->keys[S->nk - 1];
                strcpy(L->u.leaf.vals[0], S->u.leaf.vals[S->nk - 1]);
                L->nk++;
                S->nk--;
                parent->keys[idx - 1] = L->keys[0];
            } else {
                /* Rotate through parent key (other direction). */
                for (int i = L->nk; i > 0; i--) {
                    L->keys[i] = L->keys[i - 1];
                    L->u.inner.children[i + 1] = L->u.inner.children[i];
                }
                L->keys[0] = parent->keys[idx - 1];
                L->u.inner.children[0] = S->u.inner.children[S->nk];
                L->nk++;
                parent->keys[idx - 1] = S->keys[S->nk - 1];
                S->nk--;
            }
            return 1;
        }
    }
    return 0;
}

/* Merge L with a sibling; pop parent's separator. */
static void merge_with_sibling(BptNode *parent, int idx) {
    BptNode *L = parent->u.inner.children[idx];
    if (idx > 0) {
        BptNode *lsib = parent->u.inner.children[idx - 1];
        if (L->is_leaf) {
            for (int i = 0; i < L->nk; i++) {
                lsib->keys[lsib->nk + i] = L->keys[i];
                strcpy(lsib->u.leaf.vals[lsib->nk + i], L->u.leaf.vals[i]);
            }
            lsib->nk += L->nk;
            lsib->u.leaf.next = L->u.leaf.next;
        } else {
            lsib->keys[lsib->nk] = parent->keys[idx - 1];
            for (int i = 0; i < L->nk; i++) {
                lsib->keys[lsib->nk + 1 + i] = L->keys[i];
                lsib->u.inner.children[lsib->nk + 1 + i] = L->u.inner.children[i];
            }
            lsib->u.inner.children[lsib->nk + 1 + L->nk] = L->u.inner.children[L->nk];
            lsib->nk += 1 + L->nk;
        }
        free(L);
        for (int i = idx - 1; i < parent->nk - 1; i++)
            parent->keys[i] = parent->keys[i + 1];
        for (int i = idx; i < parent->nk; i++)
            parent->u.inner.children[i] = parent->u.inner.children[i + 1];
        parent->nk--;
    } else {
        BptNode *rsib = parent->u.inner.children[idx + 1];
        if (L->is_leaf) {
            for (int i = 0; i < rsib->nk; i++) {
                L->keys[L->nk + i] = rsib->keys[i];
                strcpy(L->u.leaf.vals[L->nk + i], rsib->u.leaf.vals[i]);
            }
            L->nk += rsib->nk;
            L->u.leaf.next = rsib->u.leaf.next;
        } else {
            L->keys[L->nk] = parent->keys[idx];
            for (int i = 0; i < rsib->nk; i++) {
                L->keys[L->nk + 1 + i] = rsib->keys[i];
                L->u.inner.children[L->nk + 1 + i] = rsib->u.inner.children[i];
            }
            L->u.inner.children[L->nk + 1 + rsib->nk] = rsib->u.inner.children[rsib->nk];
            L->nk += 1 + rsib->nk;
        }
        free(rsib);
        for (int i = idx; i < parent->nk - 1; i++)
            parent->keys[i] = parent->keys[i + 1];
        for (int i = idx + 1; i < parent->nk; i++)
            parent->u.inner.children[i] = parent->u.inner.children[i + 1];
        parent->nk--;
    }
}

/* Recursive delete. Returns (possibly shrunk) subtree root. */
static BptNode *del(BptNode *n, int k) {
    if (n->is_leaf) {
        int i = key_idx(n->keys, n->nk, k);
        if (i >= n->nk || n->keys[i] != k) return n;
        for (int j = i; j < n->nk - 1; j++) {
            n->keys[j] = n->keys[j + 1];
            strcpy(n->u.leaf.vals[j], n->u.leaf.vals[j + 1]);
        }
        n->nk--;
        return n;
    }
    int i = child_idx(n, k);
    BptNode *child = n->u.inner.children[i];
    del(child, k);
    int min_keys = child->is_leaf ? BPT_MIN_KEYS_LEAF : BPT_MIN_KEYS_INNER;
    if (child->nk < min_keys) {
        if (!borrow_from_sibling(n, i)) merge_with_sibling(n, i);
    }
    return n;
}

BptNode *bpt_delete(BptNode *root, int k) {
    root = del(root, k);
    if (!root->is_leaf && root->nk == 0) {
        BptNode *c = root->u.inner.children[0];
        free(root);
        root = c;
    }
    return root;
}

/* ---- bulk-load ---- */

BptNode *bpt_bulk_load(const int *keys, const char *const *vals, int n) {
    if (n == 0) return bpt_leaf_new();
    /* Step 1: build leaves. */
    int nleaves = 0;
    int cap = n / BPT_MAX_KEYS + 2;
    BptNode **leaves = malloc(cap * sizeof(BptNode *));
    BptNode *cur = bpt_leaf_new();
    for (int i = 0; i < n; i++) {
        if (cur->nk == BPT_MAX_KEYS) {
            leaves[nleaves++] = cur;
            cur = bpt_leaf_new();
        }
        cur->keys[cur->nk] = keys[i];
        strcpy(cur->u.leaf.vals[cur->nk], vals[i]);
        cur->nk++;
    }
    if (cur->nk > 0) leaves[nleaves++] = cur;
    for (int i = 0; i + 1 < nleaves; i++) leaves[i]->u.leaf.next = leaves[i + 1];

    /* Step 2: build inner levels bottom-up. */
    BptNode **level = leaves;
    int lvl_n = nleaves;
    while (lvl_n > 1) {
        int next_cap = lvl_n / BPT_MAX_KEYS + 2;
        BptNode **next = malloc(next_cap * sizeof(BptNode *));
        int next_n = 0;
        int i = 0;
        while (i < lvl_n) {
            BptNode *par = bpt_inner_new();
            par->u.inner.children[0] = level[i];
            int j = i + 1;
            while (j < lvl_n && par->nk < BPT_MAX_KEYS) {
                par->keys[par->nk] = level[j]->keys[0];
                par->u.inner.children[par->nk + 1] = level[j];
                par->nk++;
                j++;
            }
            next[next_n++] = par;
            i = j;
        }
        free(level);
        level = next;
        lvl_n = next_n;
    }
    BptNode *r = level[0];
    free(level);
    return r;
}

/* ---- visualization & shape ---- */

void bpt_visualize(const BptNode *n, const char *prefix, int is_last) {
    printf("%s%s", prefix, is_last ? "+-- " : "|-- ");
    if (n->is_leaf) {
        printf("[leaf ");
        for (int i = 0; i < n->nk; i++)
            printf("%d:%s%s", n->keys[i], n->u.leaf.vals[i],
                   i + 1 < n->nk ? ", " : "");
        printf("]%s\n", n->u.leaf.next ? " -> next" : "");
        return;
    }
    printf("[inner keys=");
    for (int i = 0; i < n->nk; i++)
        printf("%d%s", n->keys[i], i + 1 < n->nk ? "," : "");
    printf("]\n");
    char next_prefix[256];
    snprintf(next_prefix, sizeof(next_prefix), "%s%s",
             prefix, is_last ? "    " : "|   ");
    for (int i = 0; i <= n->nk; i++)
        bpt_visualize(n->u.inner.children[i], next_prefix, i == n->nk);
}

int bpt_count_leaves(const BptNode *n) {
    if (n->is_leaf) return 1;
    int c = 0;
    for (int i = 0; i <= n->nk; i++) c += bpt_count_leaves(n->u.inner.children[i]);
    return c;
}