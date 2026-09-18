/* bptree_delete_impl.h - delete path: borrow / merge rebalancing (implementation header).
 *
 * 由 bptree.c 在原地 #include:文本级包含,同一个翻译单元,零链接风险。
 * 拆出的唯一原因是 bptree.c 触及了 300 行硬约束(_docs/OPTIMIZATION.md 1.1),
 * 被搬走的每一行都与原 bptree.c 的第 195..349 行逐字节相同 —— 只换了个家。
 *
 * 为什么是 .h 而不是第二个 .c:本机没有 C 工具链(which gcc/cc/clang 全空),
 * 拆成两个翻译单元就必须同步改 static 与原型声明,改错无法在本地发现。
 * 用文本包含时,下面这些 static 函数依然在同一个 TU 里,可见性与原来完全一致。
 *
 * 删除在 M 路 B+ 树里必须保证每个结点不低于最小占用。两条修复路径,按序尝试:
 *   * borrow(redistribute)—— 有兄弟结点持有 > 最小值的条目,借一个过来
 *     (叶子借条目、内结点借子树),并原地改写父结点的分隔键;
 *   * merge —— 没人能借,于是把两个结点合并、删掉父结点的分隔键。
 * 后者会让父结点自己也少一个,所以 bpt_delete 的调用者要沿路径向上继续修,
 * 直到某一层稳定,或根塌缩成它唯一的孩子。
 */
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
