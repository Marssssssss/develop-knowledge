/* memorder_demo.c —— 六种 memory_order 的最小对照（C11 <stdatomic.h>）
 *
 * 依据 cppreference《std::memory_order》与 C11 n1570 §5.1.2.4 / §7.17：
 *   - memory_order_relaxed：*"Atomic operations tagged memory_order_relaxed are not
 *     synchronization operations; they do not impose an order among concurrent memory
 *     accesses. They only guarantee atomicity and modification order consistency."*
 *   - release/acquire：*"All memory writes ... that happened-before the atomic store from the
 *     point of view of thread A, become visible side-effects in thread B. That is, once the
 *     atomic load is completed, thread B is guaranteed to see everything thread A wrote to
 *     memory."* —— **前提是 B 真的读到了 A 写的那个值**（或 release sequence 中更靠后的值）
 *   - release sequence：*"If some atomic is store-released and several other threads perform
 *     read-modify-write operations on that atomic, a release sequence is formed ... even if
 *     they have no memory_order_release semantics."*
 *   - seq_cst：*"a single total modification order of all atomic operations that are so tagged."*
 *
 * 编译：cc -std=c11 -Wall -pthread memorder_demo.c -o memorder_demo
 */
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>

static atomic_int data, flag, counter;

/* 1. message passing：写者先写 data 再置 flag；读者先看 flag 再读 data */
static void *writer_relaxed(void *arg)
{
    (void)arg;
    atomic_store_explicit(&data, 42, memory_order_relaxed);
    atomic_store_explicit(&flag, 1, memory_order_relaxed);   /* 全 relaxed：不建立同步 */
    return NULL;
}

static void *writer_release(void *arg)
{
    (void)arg;
    atomic_store_explicit(&data, 42, memory_order_relaxed);
    atomic_store_explicit(&flag, 1, memory_order_release);   /* 打包：之前的写一起发布 */
    return NULL;
}

static void *reader_acquire(void *arg)
{
    (void)arg;
    while (atomic_load_explicit(&flag, memory_order_acquire) == 0) {
        /* 等到发布方写完 */
    }
    /* acquire 读：能接住 release 之前的全部写入 */
    printf("reader sees data=%d\n", atomic_load_explicit(&data, memory_order_relaxed));
    return NULL;
}

static void *reader_relaxed(void *arg)
{
    (void)arg;
    while (atomic_load_explicit(&flag, memory_order_relaxed) == 0) {
    }
    /* 只有 release 没有 acquire：允许（但不保证）看到 data=42 */
    printf("relaxed reader sees data=%d (不保证 42)\n",
           atomic_load_explicit(&data, memory_order_relaxed));
    return NULL;
}

/* 2. RMW 的原子性不依赖内存序：两个线程各 fetch_add 1 万次，结果必为 2 万 */
static void *bumper(void *arg)
{
    (void)arg;
    for (int i = 0; i < 10000; i++)
        atomic_fetch_add_explicit(&counter, 1, memory_order_relaxed);
    return NULL;
}

int main(void)
{
    pthread_t w, r;

    atomic_store(&data, 0);
    atomic_store(&flag, 0);
    pthread_create(&w, NULL, writer_release, NULL);
    pthread_create(&r, NULL, reader_relaxed, NULL);
    pthread_join(w, NULL);
    pthread_join(r, NULL);

    atomic_store(&data, 0);
    atomic_store(&flag, 0);
    pthread_create(&w, NULL, writer_release, NULL);
    pthread_create(&r, NULL, reader_acquire, NULL);
    pthread_join(w, NULL);
    pthread_join(r, NULL);

    atomic_store(&data, 0);
    atomic_store(&flag, 0);
    pthread_create(&w, NULL, writer_relaxed, NULL);
    pthread_create(&r, NULL, reader_relaxed, NULL);
    pthread_join(w, NULL);
    pthread_join(r, NULL);

    atomic_store(&counter, 0);
    pthread_t a, b;
    pthread_create(&a, NULL, bumper, NULL);
    pthread_create(&b, NULL, bumper, NULL);
    pthread_join(a, NULL);
    pthread_join(b, NULL);
    printf("counter=%d (期望 20000)\n", atomic_load(&counter));
    return 0;
}
