/* futex_mutex.c —— 用裸 futex 搭一把三态互斥锁
 *
 * 依据 futex(2)/futex(7)/FUTEX_WAIT(2const)：
 *   - futex word 是 32 位对齐整数，无竞争时全程在用户态（cmpxchg），零系统调用；
 *   - FUTEX_WAIT 是「原子比较-并-阻塞」：word 不等于期望值就立刻 EAGAIN（防丢失唤醒）；
 *   - FUTEX_WAIT 的 timeout 是相对值（FUTEX_WAIT_BITSET 才是绝对值）；
 *   - FUTEX_WAKE 返回**实际唤醒数**，不是剩余等待者数。
 *
 * 三态：0 = 未持有；1 = 持有且无等待者；2 = 持有且有等待者（必须唤醒）。
 * 之所以需要「2」，就是为了让 unlock 能判断到底要不要进内核。
 *
 * 编译：cc -std=c11 -Wall futex_mutex.c -o futex_mutex   （Linux 专用）
 */
#define _GNU_SOURCE
#include <errno.h>
#include <linux/futex.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <sys/syscall.h>
#include <unistd.h>

#define UNLOCKED 0u
#define LOCKED_NOWAITERS 1u
#define LOCKED_CONTENDED 2u

static long futex(uint32_t *uaddr, int op, uint32_t val,
                  const struct timespec *to, uint32_t *u2, uint32_t val3)
{
    return syscall(SYS_futex, uaddr, op, val, to, u2, val3);
}

static atomic_uint lock_word = UNLOCKED;

void lock_acquire(void)
{
    uint32_t expected = UNLOCKED;
    /* 快路径：0 -> 1，纯用户态 */
    if (atomic_compare_exchange_weak_explicit(&lock_word, &expected, LOCKED_NOWAITERS,
                                              memory_order_acquire, memory_order_relaxed))
        return;

    for (;;) {
        uint32_t cur = LOCKED_NOWAITERS;
        /* 1 -> 2：把「有等待者」这一事实写进 word，好让 unlock 知道要唤醒 */
        if (atomic_compare_exchange_weak_explicit(&lock_word, &cur, LOCKED_CONTENDED,
                                                  memory_order_relaxed, memory_order_relaxed))
            cur = LOCKED_CONTENDED;

        /* 比较-并-阻塞：只有此刻仍是 CONTENDED 才睡；否则立刻 EAGAIN 转回去重试。
         * 没有这一步，解锁发生在 FUTEX_WAIT 之前时唤醒就丢了。 */
        futex((uint32_t *)&lock_word, FUTEX_WAIT, LOCKED_CONTENDED, NULL, NULL, 0);

        expected = UNLOCKED;
        if (atomic_compare_exchange_weak_explicit(&lock_word, &expected, LOCKED_CONTENDED,
                                                  memory_order_acquire, memory_order_relaxed))
            return;
    }
}

void lock_release(void)
{
    /* 2 -> 0：有等待者，释放后必须唤醒一个 */
    if (atomic_exchange_explicit(&lock_word, UNLOCKED, memory_order_release) == LOCKED_CONTENDED)
        futex((uint32_t *)&lock_word, FUTEX_WAKE, 1, NULL, NULL, 0);
}

void show(const char *tag)
{
    printf("%-12s word=%u\n", tag, atomic_load(&lock_word));
}

int main(void)
{
    show("init");
    lock_acquire();
    show("locked");
    lock_release();
    show("released");

    /* 值不符时 FUTEX_WAIT 直接返回 -1/EAGAIN，绝不睡下 */
    atomic_store(&lock_word, UNLOCKED);
    long rc = futex((uint32_t *)&lock_word, FUTEX_WAIT, 0xdeadbeefu, NULL, NULL, 0);
    printf("FUTEX_WAIT 值不符 -> rc=%ld errno=%d (EAGAIN=%d)\n", rc, rc == -1 ? errno : 0, EAGAIN);

    /* WAKE 返回实际唤醒数：没有等待者就是 0 */
    long n = futex((uint32_t *)&lock_word, FUTEX_WAKE, 5, NULL, NULL, 0);
    printf("FUTEX_WAKE 无等待者 -> 唤醒 %ld 个\n", n);
    return 0;
}
