/* Memcached slab 分配器的 C 实现。
 *
 * 依据：memcached master 分支 slabs.c / memcached.c / memcached.h。
 * 构建： gcc -std=c11 -O2 slab_alloc.c -o sa && ./sa
 */
#include <stdio.h>

static int g_fails = 0;

static void check(const char *label, int cond, const char *detail) {
    if (cond) { printf("  ok   %s\n", label); }
    else { g_fails++; printf("  FAIL %s %s\n", label, detail); }
}

/* ---------------------------------------------------------- 常量 */

#define PAGE_SIZE        (1024 * 1024)
#define CHUNK_SIZE_MAX   (PAGE_SIZE / 2)
#define FACTOR           1.25
#define CHUNK_SIZE       48
#define CHUNK_ALIGN      8
#define MAX_CLASSES      (63 + 1)
#define POWER_SMALLEST   1
#define POWER_LARGEST    256
#define SIZEOF_ITEM      48      /* LP64：字段 42 字节 + data[] 对齐补齐到 48 */

static int align_up(int size) {
    if (size % CHUNK_ALIGN) size += CHUNK_ALIGN - (size % CHUNK_ALIGN);
    return size;
}

/* 复现 slabs_init 的建表循环。返回 class 个数，写入 sizes[]/perslabs[]。 */
static int slab_classes(int *sizes, int *perslabs) {
    int i = POWER_SMALLEST - 1, n = 0;
    double size = SIZEOF_ITEM + CHUNK_SIZE;
    while (1) {
        i++;
        if (i >= MAX_CLASSES - 1) break;
        if (size >= (double)CHUNK_SIZE_MAX / FACTOR) break;
        size = align_up((int)size);
        sizes[n] = (int)size;
        perslabs[n] = PAGE_SIZE / (int)size;
        n++;
        size = (double)((int)size) * FACTOR;
    }
    sizes[n] = CHUNK_SIZE_MAX;                  /* power_largest 特殊处理 */
    perslabs[n] = PAGE_SIZE / CHUNK_SIZE_MAX;
    return n + 1;
}

static int class_for(int ntotal, const int *sizes, int n) {
    for (int i = 0; i < n; i++) if (ntotal <= sizes[i]) return i + POWER_SMALLEST;
    return 0;
}

/* ---------------------------------------------------------- 自检 */

int main(void) {
    int sizes[MAX_CLASSES], perslabs[MAX_CLASSES];
    int n = slab_classes(sizes, perslabs);

    printf("[1] item 与默认值\n");
    check("sizeof(item) = 48", SIZEOF_ITEM == 48, "");
    check("初始 chunk = 96", SIZEOF_ITEM + CHUNK_SIZE == 96, "");
    check("chunk_size 默认 48", CHUNK_SIZE == 48, "");
    check("slab_chunk_size_max = 512KB", CHUNK_SIZE_MAX == 512 * 1024, "");
    check("factor 1.25", FACTOR == 1.25, "");
    check("MAX_CLASSES = 64", MAX_CLASSES == 64, "");

    printf("[2] slab class 表\n");
    check("power_largest = 39（class 数 = 39）", n == 39, "");
    check("class 1 = 96 / 10922", sizes[0] == 96 && perslabs[0] == 10922, "");
    check("class 2 = 120 / 8738", sizes[1] == 120 && perslabs[1] == 8738, "");
    check("class 3 = 152 / 6898", sizes[2] == 152 && perslabs[2] == 6898, "");
    check("最后 class = 512KB / perslab 2",
          sizes[n - 1] == CHUNK_SIZE_MAX && perslabs[n - 1] == 2, "");
    int ok_align = 1, ok_perslab = 1, ok_tail = 1, ok_mono = 1;
    for (int i = 0; i < n; i++) {
        if (sizes[i] % CHUNK_ALIGN) ok_align = 0;
        if (perslabs[i] != PAGE_SIZE / sizes[i]) ok_perslab = 0;
        if (PAGE_SIZE - perslabs[i] * sizes[i] >= sizes[i]) ok_tail = 0;
        if (i && sizes[i] < sizes[i - 1]) ok_mono = 0;
    }
    check("全部 8 字节对齐", ok_align, "");
    check("perslab = page / size", ok_perslab, "");
    check("尾部碎片 < 一个 chunk", ok_tail, "");
    check("尺寸单调不减", ok_mono, "");

    printf("[3] 内部碎片\n");
    check("100 字节 -> class 2（120）", class_for(100, sizes, n) == 2, "");
    check("500 字节 -> class 9（600）", class_for(500, sizes, n) == 9, "");
    check("97 字节 -> class 2（class 1 只到 96）", class_for(97, sizes, n) == 2, "");
    double worst = 0.0;
    for (int i = 0; i < n - 1; i++) {
        int cid = class_for(sizes[i] + 1, sizes, n) - 1;
        double f = (double)(sizes[cid] - (sizes[i] + 1)) / sizes[cid];
        if (f > worst) worst = f;
    }
    check("最坏碎片率接近 factor-1 = 25%", worst > 0.20 && worst < 0.26, "");

    printf("[4] 1MB 限制与 chunked item\n");
    check("恰好 512KB 可入最后一 class",
          class_for(CHUNK_SIZE_MAX, sizes, n) == n, "");
    check("超过 512KB 放不下（需 ITEM_CHUNKED）",
          class_for(CHUNK_SIZE_MAX + 1, sizes, n) == 0, "");

    printf("\n");
    if (g_fails) { printf("FAILED %d\n", g_fails); return 1; }
    printf("ALL PASS\n");
    return 0;
}
