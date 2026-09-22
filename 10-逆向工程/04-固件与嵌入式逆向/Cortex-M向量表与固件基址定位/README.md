# 594 Cortex-M 向量表与固件基址定位

裸机固件（STM32 / nRF / 各类 MCU）没有 ELF 头，IDA/Ghidra 打开后第一件事往往是
「Processor 选 ARM little-endian，但 ROM start address 填什么？」。
答案就藏在镜像开头的中断向量表里 —— 因为它存的是**绝对地址**。

## 1. VTOR：重定位表必须 128 字节对齐

ARM-software 官方 CMSIS `core_cm3.h`：

```c
__IOM uint32_t VTOR;                 /* Offset: 0x008 (R/W) Vector Table Offset Register */
#define SCB_VTOR_TBLBASE_Pos   29U
#define SCB_VTOR_TBLOFF_Pos     7U
#define SCB_VTOR_TBLOFF_Msk    (0x1FFFFFFUL << SCB_VTOR_TBLOFF_Pos)
```

`TBLOFF` 从第 7 位开始，意味着**低 7 位不可写** ⇒ 重定位的表至少 **128 字节对齐**；
`TBLBASE`（bit29）选择表放在代码区还是 RAM 区。
头文件里还有一档更窄的掩码 `0x3FFFFF << 7`，取决于芯片实现。

对照：`core_cm0.h`（ARMv6-M）的 `SCB_Type` **没有 VTOR 字段** ——
Cortex-M0 的向量表固定在 `0x00000000`，不能重定位。这是 M0 与 M3/M4 的一个硬区别。

## 2. 前 16 项是什么

Zephyr `arch/arm/core/cortex_m/vector_table.S` 逐条给出了顺序：

| 下标 | 内容 | 下标 | 内容 |
| --- | --- | --- | --- |
| 0 | 初始栈顶 `z_main_stack + CONFIG_MAIN_STACK_SIZE` | 8-10 | 保留 |
| 1 | `z_arm_reset` | 11 | `z_arm_svc` |
| 2 | `z_arm_nmi` | 12 | `z_arm_debug_monitor` |
| 3 | `z_arm_hard_fault` | 13 | 保留 |
| 4 | `z_arm_mpu_fault`（MemManage） | 14 | `z_arm_pendsv` |
| 5 | `z_arm_bus_fault` | 15 | SysTick |
| 6 | `z_arm_usage_fault` | 16+ | 外部中断 IRQ0.. |
| 7 | SecureFault（ARMv8-M SE）或保留 | | |

第 0 项是**栈顶不是函数指针**，硬件复位时把它装进 MSP；第 1 项是复位向量。

## 3. 基址推断：枚举 + 打分

核心观察：若向量表位于文件偏移 `f`，加载地址是 `base + f`，那么第 `i` 项的值
`v_i` 满足 `v_i = base + (该项指向的代码在文件里的偏移)`。于是：

1. 从每个非零向量清掉 Thumb 位后减去 `4*i`，收集候选基址。
2. 对每个候选打分：
   - 第 0 项（栈顶）落在 RAM 窗口内 `+10`，且 4 字节对齐再 `+2`；
   - 其余非零项是**奇数**（Thumb bit0）且落在镜像范围内 `+3`；
   - 零项 `+1`；
   - 基址本身 128 字节对齐（能被 VTOR 指向）`+1`，用于打平分。
3. 得分最高者即基址；入口点 = 第 1 项 `& ~1`。

两条必须记住的性质：

- **所有函数指针都是奇数**（Thumb 的 bit0=1），栈顶是偶数。看到一整列偶数说明找错了偏移。
- 候选 `base` 与 `base+1`、`base-4` 会让同样的向量「落回镜像内」，打分完全一样 ——
  所以必须有对齐这一项做平局裁决，否则会返回相差几个字节的错误结果。

## 4. 向量表不一定在偏移 0

很多镜像前面插了 bootloader / 分区头。demo 里演示了在镜像前插 64 字节 `0xff` 后，
偏移 0 处读出来全是 `0xffffffff`，必须**先扫到表的真实偏移**再算基址 ——
这两步不能合成一步。

## 5. 口径说明

ARM 官方文档站（`developer.arm.com/documentation/ddi0403/latest/`）在本机返回 **HTTP 403**，
取不到 ARMv7-M ARM 原文。因此本 demo **只钉住 CMSIS 头文件里能直接读到的 VTOR 位域**
（⇒ 128 字节对齐）与 Zephyr `vector_table.S` 的异常顺序；
「表必须按不小于 异常数×4 的 2 的幂对齐」这条更一般的规则在代码里按
`max(2 的幂, 128)` 实现，并在本 README 明确标注为**未取到官方原文**，请按 CMSIS 下限理解。

## 运行

```bash
python python/main.py
python python/selfcheck_vecbase.py    # 65 条断言
cd go && go run .
```

## 参考资料

- [CMSIS_5 `CMSIS/Core/Include/core_cm3.h`](https://github.com/ARM-software/CMSIS_5/blob/master/CMSIS/Core/Include/core_cm3.h) — `SCB->VTOR`、`SCB_VTOR_TBLOFF_Pos=7`、`SCB_VTOR_TBLBASE_Pos=29`
- [CMSIS_5 `CMSIS/Core/Include/core_cm0.h`](https://github.com/ARM-software/CMSIS_5/blob/master/CMSIS/Core/Include/core_cm0.h) — ARMv6-M 无 VTOR
- [Zephyr `arch/arm/core/cortex_m/vector_table.S`](https://github.com/zephyrproject-rtos/zephyr/blob/main/arch/arm/core/cortex_m/vector_table.S) — 前 16 项的顺序
- [Zephyr `arch/arm/core/cortex_m/vector_table.h`](https://github.com/zephyrproject-rtos/zephyr/blob/main/arch/arm/core/cortex_m/vector_table.h) — 异常处理函数命名约定
