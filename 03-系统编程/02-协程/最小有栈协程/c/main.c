/*
 * 最小有栈协程:C 实现(ucontext + 调度器模式)
 *
 * 依据 man7 makecontext(3):getcontext 快照 -> uc_stack 挂栈 ->
 * uc_link 后继 -> makecontext 绑定入口;swapcontext 双向切换。
 *
 * 结构:
 *   - 每个协程独立 64KiB 栈,run 两轮;非末轮让出回调度器(swapcontext),
 *     末轮走 return -> 经 uc_link 回调度器(两条"回 main"路径都演示)。
 *   - id 保存在协程自己的栈帧里,挂起/恢复都不丢 —— 有栈协程的本质。
 *   - makecontext 只保证 int 参数,故 id 经全局 active_id 只在首次激活时
 *     传递一次,后续恢复完全靠栈帧。
 */
#include <stdio.h>
#include <stdlib.h>
#include <ucontext.h>

#define N_CO 3
#define ROUNDS 2
#define STACK_SIZE (64 * 1024)   /* 演示级固定栈;深递归会溢出 */

static ucontext_t main_ctx;
static ucontext_t co_ctx[N_CO];
static char co_stacks[N_CO][STACK_SIZE];
static int rounds_done[N_CO];
static int active_id = 0;       /* 仅在协程"首次"激活时被读取 */

static char trace[32];
static int trace_len = 0;

static void check(int cond, const char *label)
{
    if (cond) {
        printf("PASS: %s\n", label);
    } else {
        printf("FAIL: %s\n", label);
        exit(EXIT_FAILURE);
    }
}

static void trace_mark(char c)
{
    if (trace_len < (int)sizeof(trace) - 1)
        trace[trace_len++] = c;
}

static void coroutine_main(void)
{
    /* id 在协程自己的栈帧里:挂起再恢复依然有效(有栈的本质) */
    int id = active_id;
    for (int r = 0; r < ROUNDS; r++) {
        rounds_done[id]++;
        trace_mark((char)('0' + id));
        printf("co%d round %d\n", id, r);
        if (r < ROUNDS - 1) {
            /* 非末轮:让出。把"回来点"存进自己,激活调度器 */
            swapcontext(&co_ctx[id], &main_ctx);
        }
        /* 末轮直接走 return -> uc_link 链回 main_ctx */
    }
    trace_mark((char)('A' + id));
    printf("co%d returning (uc_link -> main)\n", id);
}

int main(void)
{
    /* 四件套:快照 -> 挂栈 -> 链接后继 -> 绑定入口 */
    for (int i = 0; i < N_CO; i++) {
        if (getcontext(&co_ctx[i]) == -1) {
            perror("getcontext");
            return EXIT_FAILURE;
        }
        co_ctx[i].uc_stack.ss_sp = co_stacks[i];
        co_ctx[i].uc_stack.ss_size = sizeof(co_stacks[i]);
        co_ctx[i].uc_link = &main_ctx;         /* return 后回调度器 */
        makecontext(&co_ctx[i], coroutine_main, 0);
    }

    for (int pass = 0; pass < ROUNDS + 1; pass++) {
        int advanced = 0;
        for (int i = 0; i < N_CO; i++) {
            if (rounds_done[i] < ROUNDS) {
                active_id = i;                  /* 仅首次激活被读走 */
                swapcontext(&main_ctx, &co_ctx[i]);
                advanced = 1;
            }
        }
        if (!advanced)
            break;
    }

    trace[trace_len] = '\0';
    printf("trace: %s\n", trace);
    check(rounds_done[0] == ROUNDS && rounds_done[1] == ROUNDS
              && rounds_done[2] == ROUNDS,
          "each coroutine completed 2 rounds");
    check(trace_len == 9, "9 trace marks (3 co x 2 rounds + 3 uc_link returns)");
    check(trace[0] == '0' && trace[3] == '0' && trace[4] == 'A',
          "switch order: round-robin then uc_link per coroutine");
    puts("ucontext coroutine demo passed");
    return 0;
}
