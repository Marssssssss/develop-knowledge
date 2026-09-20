/* spinlock_sem.c —— TAS 自旋锁 / 排队自旋锁 / POSIX 信号量有界缓冲
 *
 * 依据 IEEE Std 1003.1-2017：
 *   - pthread_spin_lock：*"The calling thread shall acquire the lock if it is not held by
 *     another thread. Otherwise, the thread shall spin ... until the lock becomes available"*；
 *     持有者再次上锁 *"The results are undefined"*（Issue 7 已删掉 EDEADLK 这条必须错误）；
 *     *"These functions shall not return an error code of [EINTR]"*；
 *     APPLICATION USAGE：*"Applications using this function may be subject to priority inversion"*
 *   - pthread_spin_trylock 被持有时 **shall fail** with EBUSY
 *
 * 信号量依据 sem_overview(7) / sem_wait(3)：值永不为负；EINTR 时值保持不变；
 * 命名信号量最长 NAME_MAX-4 = 251 字符，落在 /dev/shm 下形如 sem.somename。
 *
 * 编译：cc -std=c11 -Wall -pthread spinlock_sem.c -o spinlock_sem
 */
#include <errno.h>
#include <pthread.h>
#include <semaphore.h>
#include <stdatomic.h>
#include <stdio.h>
#include <string.h>

/* ---------- 1. TAS 自旋锁：抢到算谁的，不排队 ---------- */
typedef struct { atomic_flag flag; } tas_lock;

static void tas_init(tas_lock *l) { atomic_flag_clear(&l->flag); }

/* atomic_flag_test_and_set 即 test-and-set；acquire 语义保证临界区不会被提到取锁前 */
static void tas_lock_it(tas_lock *l)
{
    while (atomic_flag_test_and_set_explicit(&l->flag, memory_order_acquire)) {
        /* 纯空转：单核且不可抢占时，持有者永远拿不到 CPU —— 这就是自旋死锁 */
        while (atomic_load_explicit(&l->flag, memory_order_relaxed)) { /* spin */ }
    }
}

static int tas_trylock(tas_lock *l)
{
    return atomic_flag_test_and_set_explicit(&l->flag, memory_order_acquire) ? EBUSY : 0;
}

static void tas_unlock(tas_lock *l) { atomic_flag_clear_explicit(&l->flag, memory_order_release); }

/* ---------- 2. 排队自旋锁：先取号再等叫号，先来后到 ---------- */
typedef struct {
    atomic_uint next_ticket;
    atomic_uint now_serving;
} ticket_lock;

static void ticket_init(ticket_lock *l)
{
    atomic_store(&l->next_ticket, 0u);
    atomic_store(&l->now_serving, 0u);
}

static unsigned ticket_acquire(ticket_lock *l)
{
    unsigned t = atomic_fetch_add_explicit(&l->next_ticket, 1u, memory_order_relaxed);
    while (atomic_load_explicit(&l->now_serving, memory_order_acquire) != t) { /* spin */ }
    return t;
}

static void ticket_release(ticket_lock *l)
{
    atomic_fetch_add_explicit(&l->now_serving, 1u, memory_order_release);
}

/* ---------- 3. 信号量有界缓冲 ---------- */
#define CAP 2

static sem_t empty_slots, full_slots, mutex_sem;
static int buffer[CAP];
static int count = 0;

static void *producer(void *arg)
{
    (void)arg;
    for (int i = 0; i < 3; i++) {
        /* 值为 0 时阻塞；被信号打断返回 -1/EINTR 且**值不变**，所以必须重试 */
        while (sem_wait(&empty_slots) == -1 && errno == EINTR)
            ;
        sem_wait(&mutex_sem);
        buffer[count++] = i;                    /* 能到这就一定没满 */
        sem_post(&mutex_sem);
        sem_post(&full_slots);                  /* async-signal-safe，可在信号处理器里调 */
    }
    return NULL;
}

static void *consumer(void *arg)
{
    (void)arg;
    for (int i = 0; i < 3; i++) {
        while (sem_wait(&full_slots) == -1 && errno == EINTR)
            ;
        sem_wait(&mutex_sem);
        int v = buffer[--count];                /* 能到这就一定没空 */
        sem_post(&mutex_sem);
        sem_post(&empty_slots);
        printf("consume %d (depth=%d)\n", v, count);
    }
    return NULL;
}

int main(void)
{
    tas_lock tl;
    tas_init(&tl);
    printf("TAS 首次上锁: trylock -> %d (0=成功)\n", tas_trylock(&tl));
    printf("TAS 已被持有: trylock -> %d (EBUSY=%d)\n", tas_trylock(&tl), EBUSY);
    tas_unlock(&tl);

    ticket_lock ql;
    ticket_init(&ql);
    unsigned a = ticket_acquire(&ql);
    printf("ticket 取号 %u 立即放行\n", a);
    ticket_release(&ql);
    unsigned b = ticket_acquire(&ql);
    printf("ticket 取号 %u 立即放行（叫号已推进）\n", b);
    ticket_release(&ql);

    sem_init(&empty_slots, 0, CAP);   /* 线程共享：pshared=0 */
    sem_init(&full_slots, 0, 0);
    sem_init(&mutex_sem, 0, 1);

    pthread_t p, c;
    pthread_create(&p, NULL, producer, NULL);
    pthread_create(&c, NULL, consumer, NULL);
    pthread_join(p, NULL);
    pthread_join(c, NULL);
    printf("结束 depth=%d（0 表示不溢不欠）\n", count);

    sem_destroy(&empty_slots);
    sem_destroy(&full_slots);
    sem_destroy(&mutex_sem);
    return 0;
}
