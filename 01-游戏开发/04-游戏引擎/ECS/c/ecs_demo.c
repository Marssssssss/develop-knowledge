/*
 * ecs_demo.c — 最小 sparse set ECS（Entity Component System）演示
 *
 * 存储：每类组件一个 sparse set
 *   sparse[] : entity id -> dense 下标（无效用 SIZE_MAX 表示）
 *   dense[]  : dense 下标 -> entity id
 *   comps[]  : dense 下标 -> 组件数据（与 dense 一一对应，连续内存）
 *
 * 演示：8 实体（偶数号 P+V，奇数号仅 P）-> 3 帧 movement -> 销毁实体 2
 *       （swap-remove）-> 打印内部布局
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define NUM_ENTITIES 8
#define FRAMES 3
#define DT 0.016f /* 帧间隔（秒），教学用固定值 */

typedef int Entity;

typedef struct { float x, y; } Position;
typedef struct { float x, y; } Velocity;

/* 无效 sparse 槽位标记 */
#define NO_INDEX ((size_t)-1)

/* 组件集合：一套 void* + memcpy 的"泛型" sparse set */
typedef struct {
    size_t *sparse;    /* entity -> dense 下标        */
    Entity *dense;     /* dense 下标 -> entity        */
    void    *comps;    /* dense 下标 -> 组件字节      */
    size_t   elem_size;
    size_t   count;    /* dense 实际长度              */
    size_t   capacity; /* dense/comps 容量            */
    size_t   sparse_cap;
} ComponentSet;

static void die(const char *msg) { perror(msg); exit(1); }

static void *xrealloc(void *p, size_t n) {
    void *q = realloc(p, n);
    if (!q) die("realloc");
    return q;
}

static void set_init(ComponentSet *s, size_t elem_size, size_t max_entities) {
    s->sparse = xrealloc(NULL, max_entities * sizeof(size_t));
    for (size_t i = 0; i < max_entities; i++) s->sparse[i] = NO_INDEX;
    s->dense  = NULL;
    s->comps  = NULL;
    s->elem_size = elem_size;
    s->count = s->capacity = 0;
    s->sparse_cap = max_entities;
}

static int set_has(const ComponentSet *s, Entity e) {
    return e >= 0 && (size_t)e < s->sparse_cap && s->sparse[e] != NO_INDEX;
}

/* O(1) swap-remove：尾元素填补空洞并回写其 sparse 索引 */
static void set_remove(ComponentSet *s, Entity e) {
    size_t idx  = s->sparse[e];
    size_t last = s->count - 1;
    Entity moved = s->dense[last];
    s->dense[idx] = moved;
    memcpy((char *)s->comps + idx * s->elem_size,
           (char *)s->comps + last * s->elem_size, s->elem_size);
    s->sparse[moved] = idx;
    s->sparse[e] = NO_INDEX;
    s->count--;
}

/* 追加组件；若已存在则覆盖（简化：不支持重复添加） */
static void set_add(ComponentSet *s, Entity e, const void *comp) {
    if (set_has(s, e)) {
        memcpy((char *)s->comps + s->sparse[e] * s->elem_size, comp, s->elem_size);
        return;
    }
    if (s->count == s->capacity) {
        s->capacity = s->capacity ? s->capacity * 2 : 8;
        s->dense = xrealloc(s->dense, s->capacity * sizeof(Entity));
        s->comps = xrealloc(s->comps, s->capacity * s->elem_size);
    }
    s->dense[s->count] = e;
    memcpy((char *)s->comps + s->count * s->elem_size, comp, s->elem_size);
    s->sparse[e] = s->count;
    s->count++;
}

/* 取组件指针（调用方保证 set_has 为真） */
static void *set_get(const ComponentSet *s, Entity e) {
    return (char *)s->comps + s->sparse[e] * s->elem_size;
}

/* World：实体分配 + 两组件集合（教学版，无 generation） */
typedef struct {
    Entity  next;
    int     alive[NUM_ENTITIES * 2];
    ComponentSet positions;
    ComponentSet velocities;
} World;

static Entity world_create(World *w) {
    Entity e = w->next++;
    w->alive[e] = 1;
    return e;
}

static void world_destroy(World *w, Entity e) {
    if (set_has(&w->positions,  e)) set_remove(&w->positions,  e);
    if (set_has(&w->velocities, e)) set_remove(&w->velocities, e);
    w->alive[e] = 0;
}

/* System：查询 P+V 组合，遍历较短的集合做成员测试 */
static void movement_system(World *w) {
    ComponentSet *p = &w->positions, *v = &w->velocities;
    ComponentSet *base = (v->count < p->count) ? v : p;
    for (size_t i = 0; i < base->count; i++) {
        Entity e = base->dense[i];
        if (!set_has(p, e) || !set_has(v, e)) continue;
        Position *pos = set_get(p, e);
        Velocity *vel = set_get(v, e);
        pos->x += vel->x * DT;
        pos->y += vel->y * DT;
    }
}

static void print_layout(const char *name, const ComponentSet *s) {
    printf("  %s: dense=[", name);
    for (size_t i = 0; i < s->count; i++)
        printf("%s%d", i ? "," : "", s->dense[i]);
    printf("] sparse=[");
    for (size_t e = 0; e < 8; e++) {
        if (s->sparse[e] == NO_INDEX) printf("%sX", e ? "," : "");
        else printf("%s%zu", e ? "," : "", s->sparse[e]);
    }
    printf("]\n");
}

static void print_positions(World *w, const char *tag) {
    printf("%s\n", tag);
    for (Entity e = 0; e < NUM_ENTITIES; e++) {
        if (!w->alive[e]) { printf("  e%d: <destroyed>\n", e); continue; }
        if (!set_has(&w->positions, e)) { printf("  e%d: (no position)\n", e); continue; }
        Position *p = set_get(&w->positions, e);
        printf("  e%d: pos=(%.2f, %.2f)%s\n", e, p->x, p->y,
               set_has(&w->velocities, e) ? "  [P+V]" : "  [P]");
    }
}

int main(void) {
    World w = {0};
    set_init(&w.positions,  sizeof(Position), NUM_ENTITIES * 2);
    set_init(&w.velocities, sizeof(Velocity), NUM_ENTITIES * 2);

    /* 偶数号实体挂 P+V，奇数号只挂 P */
    for (Entity e = 0; e < NUM_ENTITIES; e++) {
        world_create(&w);
        Position p = { (float)e, 0.0f };
        set_add(&w.positions, e, &p);
        if (e % 2 == 0) {
            Velocity v = { 1.0f, 2.0f };
            set_add(&w.velocities, e, &v);
        }
    }

    print_positions(&w, "--- frame 0 (initial) ---");
    for (int f = 1; f <= FRAMES; f++) {
        movement_system(&w);
        printf("--- after frame %d ---\n", f);
        for (Entity e = 0; e < NUM_ENTITIES; e += 2) { /* 只看带速度的 */
            Position *p = set_get(&w.positions, e);
            printf("  e%d: pos=(%.2f, %.2f)\n", e, p->x, p->y);
        }
    }

    /* 销毁实体 2：触发 swap-remove，观察 dense 紧凑性 */
    printf("--- destroy e2 (swap-remove) ---\n");
    world_destroy(&w, 2);
    print_positions(&w, "positions after destroy:");
    printf("sparse set internals:\n");
    print_layout("Position ", &w.positions);
    print_layout("Velocity", &w.velocities);

    free(w.positions.sparse);  free(w.positions.dense);  free(w.positions.comps);
    free(w.velocities.sparse); free(w.velocities.dense); free(w.velocities.comps);
    return 0;
}
