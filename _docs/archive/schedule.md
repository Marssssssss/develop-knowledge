# archive/schedule.md — 调度日志全本

> 由自动化任务每轮 append。**默认 agent 不读**,需要"上一轮做了什么/权威资料/坑"等细节时 Grep 此处。
> rotate 规则见 `STATE.md §4`:> 30 行 或 > 20 KB → 截断至最近 30 条。
>
> ⚠️ 2026-09-11 22:32 rotate 触发(> 30 行):丢弃 18:00 之前的条目,历史回溯靠 `git log -p _docs/archive/schedule.md`。

## 调度日志(保留最近 5 条)

| 时间 | 索引 | 主任务 | 副任务 | 推进 | 累计 demo | 备注 |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-09-11 18:00 | 14 | bump-allocator + slab-allocator + gc-tri-color 三 demo | OK | +3 | 20 | C/Python/Go 三语言 ×3 demo;权威资料 6 源;Python 全 py_compile 干净 + 3 demo run PASS;本机无 gcc/Go 工具链走人工代码审查;next → 15 |
| 2026-09-11 19:00 | 15 | mmap + ext4 JBD2 + Page Cache 三 demo | OK | +3 | 23 | C/Python/Go 三语言 ×3 demo;权威来源 8 源全 WebFetch 命中;mmap 4 子 demo + JBD2 4 子 demo + Page Cache 4 子 demo;Python 3 demo 干净 + 2 demo run PASS;本机无 gcc/Go 走人工代码审查;next → 16 |
| 2026-09-11 20:16 | 16 | Swift/ObjC ARC + GCD 同步原语 + RunLoop 三 demo | OK | +3 | 26 | Swift/Objective-C 双语 ×3 demo;权威来源 5 源;本机无 Swift/ObjC 工具链走人工代码审查;next → 17 |
| 2026-09-11 21:22 | 17 | Android Handler/Looper + Activity launchMode + Compose 重组 三 demo | OK | +3 | 29 | Kotlin/Java 双语 ×2 demo + Kotlin ×1 demo;权威来源 5 源;本机无 Kotlin/Android SDK 走人工代码审查;next → 18 |
| 2026-09-11 22:30 | 18 | RN Bridge vs JSI + Flutter 三棵树 + KMP expect/actual 三 demo | OK | +3 | 32 | TS+JS / Dart / Kotlin 三 demo = 12 源文件 + 3 README;权威来源 3 源全 WebFetch 全文命中;纯 Node.js 可运行 RN 部分;Flutter/KMP 走人工代码审查;next → 19 |
| 2026-09-11 23:34 | 19 | 反向传播 + 2D 卷积 im2col + 多头自注意力 三 demo | OK | +3 | 35 | Python ×3 demo = 3 源文件 + 3 README;权威来源 3 源全 WebFetch 全文命中(d2l.ai §5.3 / arXiv 2408.12561 Eq 3-5 / Vaswani 2017 §3.2 公式 1-3);Python py_compile 干净;pip install numpy 卡死 11 分钟未完成,数值梯度实测留给用户执行;next → 20 |

---
> 写入规则:每轮结束 append 一行(7 列)。监控:行数 > 30 或 大小 > 20 KB → 触发 rotate(见 STATE.md §4)。