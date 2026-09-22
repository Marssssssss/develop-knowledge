# 04 固件与嵌入式逆向

> 固件逆向（Firmware Reverse Engineering）：从路由器 / 摄像头 / IoT 设备的整包固件里还原出**镜像布局、文件系统、加载地址与安全启动链**。
> 与 `01-二进制逆向`（ELF/PE/指令集）和 `03-协议逆向`（报文格式）互补：本子类面对的是「没有符号、没有进程、甚至没有操作系统」的裸镜像。
> 2026-09-22 由自动巡检按索引表第 57 项新建（10-逆向工程 大类的第 4 个子类目）。

## 子目录

| 目录 | 说明 | demo |
| --- | --- | --- |
| [固件镜像头uImage与FIT/](./固件镜像头uImage与FIT/) | U-Boot legacy uImage 头（大端 + 双 CRC）与 FIT（FDT schema + 签名哈希选区） | 591 |
| [SquashFS固件文件系统解析/](./SquashFS固件文件系统解析/) | superblock、8K 元数据块、48 位 inode 引用、fragment 与 lookup 表 | 592 |
| [JFFS2与UBI闪存结构/](./JFFS2与UBI闪存结构/) | JFFS2 结点类型与三级 CRC 覆盖、UBI 的 EC/VID 头与双份 PEB 仲裁 | 593 |
| [Cortex-M向量表与固件基址定位/](./Cortex-M向量表与固件基址定位/) | VTOR 位域、16 个系统异常顺序、用向量表反推加载基址 | 594 |
| [MCUboot镜像验签与安全启动/](./MCUboot镜像验签与安全启动/) | 32 字节小端头、TLV 清单、哈希覆盖范围、镜像尾布局 | 595 |

## 一条典型的分析流水线

1. **认容器**：先找 `0x27051956`（uImage，大端）或 `hsqs`（SquashFS 魔数）或 `UBI#`。
2. **定基址**：裸机固件没有 ELF 头，靠中断向量表里的绝对地址反推（demo 594）。
3. **拆文件系统**：squashfs 只读、JFFS2/UBIFS 走闪存介质（demo 592 / 593）。
4. **验签名**：能过 MCUboot / FIT verified boot 的镜像才是官方原版（demo 595 / 591）。

## 待研究

- [ ] 加壳与去混淆在嵌入式固件上的形态（UPX 之外的私有壳、向量表混淆）
- [ ] 固件差分与补丁定位（bsdiff / 函数级 BinDiff 在裸机固件上的适配）
- [ ] 熵分析与压缩 / 加密段识别（binwalk 的滑动窗口熵与签名扫描）
- [ ] eMMC / NAND 的分区表（GPT / MBR 与厂商私有 partition table）
- [ ] 安全启动链（BootROM → SPL → U-Boot → FIT）逐级验签与回滚保护
- [ ] 固件仿真（QEMU 系统态模拟、Firmadyne 的网络重托管）

## 参考资料（本轮实读）

- [Flattened Image Tree Specification v1.0（fitspec.osfw.foundation，591）](https://fitspec.osfw.foundation/)
- [u-boot `include/image.h` — legacy_img_hdr 与 IH_* 枚举（591）](https://github.com/u-boot/u-boot/blob/master/include/image.h)
- [u-boot `doc/usage/fit/source_file_format.rst` — 已迁往 fitspec 站点（591）](https://github.com/u-boot/u-boot/blob/master/doc/usage/fit/source_file_format.rst)
- [u-boot `boot/image.c` 与 `tools/default_image.c` — hcrc / dcrc 的计算（591）](https://github.com/u-boot/u-boot/blob/master/boot/image.c)
- [Linux `fs/squashfs/squashfs_fs.h`（592）](https://github.com/torvalds/linux/blob/master/fs/squashfs/squashfs_fs.h)
- [Linux `Documentation/filesystems/squashfs.rst`（592）](https://github.com/torvalds/linux/blob/master/Documentation/filesystems/squashfs.rst)
- [Linux `fs/squashfs/dir.c` — `dir_count = count + 1`（592）](https://github.com/torvalds/linux/blob/master/fs/squashfs/dir.c)
- [Linux `include/uapi/linux/jffs2.h`（593）](https://github.com/torvalds/linux/blob/master/include/uapi/linux/jffs2.h)
- [Linux `drivers/mtd/ubi/ubi-media.h`（593）](https://github.com/torvalds/linux/blob/master/drivers/mtd/ubi/ubi-media.h)
- [Linux `drivers/mtd/ubi/io.c` — `crc32(UBI_CRC32_INIT, ...)`（593）](https://github.com/torvalds/linux/blob/master/drivers/mtd/ubi/io.c)
- [Linux `include/linux/crc32.h` — 明确 "does not invert"（591/593）](https://github.com/torvalds/linux/blob/master/include/linux/crc32.h)
- [CMSIS_5 `core_cm3.h` — SCB->VTOR 位域（594）](https://github.com/ARM-software/CMSIS_5/blob/master/CMSIS/Core/Include/core_cm3.h)
- [Zephyr `arch/arm/core/cortex_m/vector_table.S`（594）](https://github.com/zephyrproject-rtos/zephyr/blob/main/arch/arm/core/cortex_m/vector_table.S)
- [MCUboot `docs/design.md` §Image format（595）](https://github.com/mcu-tools/mcuboot/blob/main/docs/design.md)
- [MCUboot `boot/bootutil/include/bootutil/image.h`（595）](https://github.com/mcu-tools/mcuboot/blob/main/boot/bootutil/include/bootutil/image.h)
- [MCUboot `boot/bootutil/include/bootutil/bootutil.h` — struct image_trailer（595）](https://github.com/mcu-tools/mcuboot/blob/main/boot/bootutil/include/bootutil/bootutil.h)

> **口径说明**：ARM 官方文档站（`developer.arm.com`）在本机返回 HTTP 403，无法取到 ARMv7-M ARM 原文，
> 因此 demo 594 只钉住 CMSIS 头文件里能直接读到的 VTOR 位域（`TBLOFF` 从位 7 起 ⇒ 128 字节对齐）与
> Zephyr `vector_table.S` 的异常顺序；「表必须按不小于 异常数×4 的 2 的幂对齐」这条更一般的规则
> 在 README 里标注为**未取到官方原文、按 CMSIS 下限 128 实现**。
