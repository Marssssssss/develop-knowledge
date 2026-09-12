/* PLT/GOT 延迟绑定的"被观察对象":一个典型的动态链接程序。
 * 编译后用 objdump 观察它的 .plt 与对应的 JMP_SLOT 重定位:
 *
 *   gcc -no-pie -o plt_subject plt_subject.c
 *   objdump -d -j .plt plt_subject | head -30      # 看 PLT 条目三段式
 *   objdump -R plt_subject | grep JUMP             # JMP_SLOT 重定位 → GOT 槽
 *   objdump -R plt_subject | grep -c JUMP_SLOT     # 与调用过的外部函数数一致
 *
 * x86-64 上 PLT 条目形如(Taylor 描述的 i386 结构的直系后代):
 *   pltN:  jmp *offset(%rip)   ; 经 GOT 槽间接跳(GOT 初始指回 pltN+1)
 *          push $index         ; 重定位索引(改用栈传递而非 %ebx)
 *          jmp  plt0           ; PLT[0] → _dl_runtime_resolve
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

__attribute__((noinline))
static void greet(const char *who) {
    /* 三个外部函数调用 → 链接器各生成一个 PLT 条目 + 一个 JMP_SLOT 重定位 */
    char *buf = malloc(64);            /* malloc@plt */
    if (!buf) return;
    strcpy(buf, "hello, ");            /* strcpy@plt */
    strcat(buf, who);                  /* strcat@plt */
    printf("%s (printf@plt)\n", buf);  /* printf@plt */
    free(buf);                         /* free@plt */
}

int main(void) {
    greet("PLT/GOT");
    /* 关键观察:greet 只被调用一次,但 main 与 greet 内每次跨库调用
     * 都先穿过 PLT —— 只有"真正执行到"的那条才触发惰性解析。 */
    return 0;
}
