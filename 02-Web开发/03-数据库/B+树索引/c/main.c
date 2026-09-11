/* B+ Tree demo entry point.
 *
 * Build: gcc -O2 -Wall -Wextra -pedantic main.c bptree.c -o bptree
 * Run:   ./bptree
 */
#include "bptree.h"

#include <stdio.h>
#include <stdlib.h>

int main(void) {
    printf("=== demo 1: insert 1..20, watch splits propagate ===\n");
    BptNode *root = bpt_leaf_new();
    char buf[32];
    for (int k = 1; k <= 20; k++) {
        snprintf(buf, sizeof(buf), "v%d", k);
        root = bpt_insert(root, k, buf);
    }
    bpt_visualize(root, "", 1);
    printf("\n");

    printf("=== demo 2: point search ===\n");
    for (int k = 3; k <= 99; k += 8) {
        const char *r = bpt_search(root, k);
        printf("  search(%d) -> %s\n", k, r ? r : "NULL");
    }
    printf("\n");

    printf("=== demo 3: range query (lo=7, hi=15) via sibling chain ===\n");
    int out_keys[64];
    char *out_vals[64];
    int out_n;
    bpt_range_query(root, 7, 15, out_keys, out_vals, &out_n);
    printf("  [");
    for (int i = 0; i < out_n; i++)
        printf("%d:%s%s", out_keys[i], out_vals[i], i + 1 < out_n ? ", " : "");
    printf("]\n\n");

    printf("=== demo 4: delete with borrow from sibling ===\n");
    int bkeys[] = {5, 8, 1, 7, 3, 12, 9, 14, 6, 11};
    char *bvals[] = {"v5", "v8", "v1", "v7", "v3", "v12", "v9", "v14", "v6", "v11"};
    BptNode *root2 = bpt_bulk_load(bkeys, (const char *const *)bvals, 10);
    printf("before delete:\n");
    bpt_visualize(root2, "", 1);
    for (int k = 1; k <= 5; k += 2) root2 = bpt_delete(root2, k);
    printf("after deleting 1, 3, 5:\n");
    bpt_visualize(root2, "", 1);
    printf("\n");

    printf("=== demo 5: bulk-load 1..30 (sorted input, O(N)) ===\n");
    int k30[30];
    char v30[30][32];
    for (int i = 0; i < 30; i++) {
        k30[i] = i + 1;
        snprintf(v30[i], sizeof(v30[i]), "val%d", i + 1);
    }
    const char *vp[30];
    for (int i = 0; i < 30; i++) vp[i] = v30[i];
    BptNode *root3 = bpt_bulk_load(k30, vp, 30);
    printf("  root keys:");
    for (int i = 0; i < root3->nk; i++) printf(" %d", root3->keys[i]);
    printf("\n");
    bpt_range_query(root3, 10, 25, out_keys, out_vals, &out_n);
    printf("  range[10..25] -> [");
    for (int i = 0; i < out_n; i++)
        printf("%d:%s%s", out_keys[i], out_vals[i], i + 1 < out_n ? ", " : "");
    printf("]\n");

    bpt_free(root);
    bpt_free(root2);
    bpt_free(root3);
    return 0;
}