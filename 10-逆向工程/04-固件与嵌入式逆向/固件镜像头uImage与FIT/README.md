# 591 固件镜像头：U-Boot legacy uImage 与 FIT

拆固件的第一刀往往是「这个包是谁打出来的」。U-Boot 生态里有两种格式：
老的 **legacy uImage**（64 字节定长头 + payload）和新的 **FIT**（本质上是一个 FDT blob）。
两者在字节序、CRC 与安全模型上完全不同，混用必错。

## 1. legacy uImage：64 字节、大端、两个 CRC

`include/image.h` 里注释写得很直白 —— *all data in network byte order (aka natural aka bigendian)*：

```c
#define IH_MAGIC  0x27051956
#define IH_NMLEN  32
struct legacy_img_hdr {
    uint32_t ih_magic, ih_hcrc, ih_time, ih_size, ih_load, ih_ep, ih_dcrc;
    uint8_t  ih_os, ih_arch, ih_type, ih_comp;
    uint8_t  ih_name[IH_NMLEN];
};
```

- 头长 **64 字节**（7×4 + 4 + 32），`image_get_data()` 就是 `头地址 + 64`。
- `hcrc` 覆盖**头本身**，计算时先把 `ih_hcrc` 清零（`boot/image.c` 的 `image_check_hcrc`
  与 `tools/default_image.c` 的 `image_set_hcrc` 都是这个套路）。
- `dcrc` 覆盖 **payload**，`ih_size` 是 payload 长度，`image_get_image_size() = size + 64`。
- `IH_TYPE_MULTI`（值 4）的多文件镜像：payload 开头是一串 be32 子镜像长度，以 **0** 结尾，
  之后才是各子镜像的数据体。

## 2. CRC 口径：Linux `crc32()` 首尾都不取反

`include/linux/crc32.h` 明确写着 *"This does **not** invert the CRC at the beginning or end"*。
所以 `crc32(0xFFFFFFFF, b"123456789") == 0x340BC6D9`（JAMCRC 校验值），
而 `zlib.crc32` 自己额外做了首尾两次取反，两者差一个 `^ 0xFFFFFFFF`。
uImage 的两个 CRC 都用 **seed 0**；JFFS2 同样 seed 0，UBI 却是 seed `0xFFFFFFFF`（见 demo 593）。

## 3. FIT：一个 devicetree blob + 一套 schema

FIT 规范（v1.0）由 Open Source Firmware Foundation 维护，u-boot 的
`doc/usage/fit/source_file_format.rst` 已经把这页迁走：

- 根结点：`timestamp` **必选**；`description` 可选；
  `#address-cells` 是**条件必选** —— 只有子镜像用到 `load`/`entry` 时才必须有。
- `/images` 与 `/configurations` 是**必选结点**，各自至少含一个子结点。
- 每个子镜像：`description`/`type`/`arch`/`os`/`compression` 必选，`load`/`entry` 可选。
- `hash-1` 子结点：`algo` + `value`，`value` 长度**只由哈希算法决定**
  （crc32=4、md5=16、sha1=20、sha256=32、sha384=48、sha512=64）。
- 签名子结点：`algo` 写作 `"sha256,rsa2048"` 形式，`value` 长度**只跟签名算法走**
  （rsa2048=256、ecdsa256=64），所以哈希与签名可以任意混搭。

## 4. 两级安全：镜像哈希 + 配置签名

§7.2 的设计动机是防 **mix-and-match**：给每个镜像单独签名，攻击者可以把两个各自合法的镜像
拼成一个从未被批准过的组合。配置签名把「这一组镜像」绑在一起。

§7.3 定义了签名哈希到底覆盖哪些字节，这是本 demo 的重点：

| token | 是否计入 | 条件 |
| --- | --- | --- |
| `FDT_BEGIN_NODE` | 是 | 结点自己**或其父结点**在 node list 里 |
| `FDT_END_NODE` | 是 | 同上 |
| `FDT_PROP` | 否 | 属性名是 `data` / `data-size` / `data-position` / `data-offset` |
| `FDT_PROP` | 是 | 结点自己在 list 里，且属性名不在上面四个里 |
| `FDT_NOP` | 是 | 所在结点自己在 list 里 |
| `FDT_END` | **总是** | — |

node list = 根 + 配置结点 + 被引用的镜像结点 + 那些镜像的 hash 子结点。
「被引用」指的是配置结点里除 `description` / `compatible` / `default` 之外的每个字符串属性；
**引用不到对应镜像不算错误**，直接跳过。最后再拼上 strings 块里由 `hashed-strings` 记录的区间。

由此得到几个反直觉但必须记住的结论：

- 改镜像 `data` 的内容**不会**让配置签名失效 —— 数据完整性由镜像自己的 hash 子结点负责。
- 改镜像 hash 的 `value` **会**让配置签名失效（对偶）。
- 改签名结点自己的 `value` 不会 —— 签名结点不在 node list 里，先有鸡后有蛋的问题天然绕开。
- 改配置结点的 `description` **会**改变哈希：它只是「不算镜像引用」，但作为属性本身仍在覆盖内。

## 5. 实现里的两个大坑（本轮实跑踩到）

1. **FDT 的 strings 块按「首次出现」累积**，新增一个属性名会平移后续所有属性的 `nameoff`
   —— 于是结构块字节全变。做「某属性是否被排除」的对照实验时，对照组必须**属性名集合完全相同**，
   只改值，否则测到的是 FDT 编码位移而不是 §7.3 的选区规则。
2. **FDT 的 root 结点属性也要 emit**。手写序列化器时容易只递归子结点、漏掉根结点自己的
   `timestamp` / `#address-cells`，结果 schema 校验永远报「缺少必选属性」。

## 运行

```bash
python python/main.py                 # 打印 uImage 头与 FIT 结构
python python/selfcheck_uimage.py     # 94 条断言
# Go（本机无工具链，走人工审查 + bracket_check / go_sanity / go_crossref）
cd go && go run .
```

## 参考资料

- [Flattened Image Tree Specification v1.0](https://fitspec.osfw.foundation/) — §5.2/5.3/5.4/5.5/5.8–5.10/7.2/7.3
- [u-boot `include/image.h`](https://github.com/u-boot/u-boot/blob/master/include/image.h) — `IH_MAGIC`、`legacy_img_hdr`、`IH_OS_*`/`IH_ARCH_*`/`IH_TYPE_*`/`IH_COMP_*`
- [u-boot `boot/image.c`](https://github.com/u-boot/u-boot/blob/master/boot/image.c) — `image_check_hcrc` / `image_check_dcrc`
- [u-boot `tools/default_image.c`](https://github.com/u-boot/u-boot/blob/master/tools/default_image.c) — mkimage 侧的顺序
- [u-boot `doc/usage/fit/source_file_format.rst`](https://github.com/u-boot/u-boot/blob/master/doc/usage/fit/source_file_format.rst) — 指向 fitspec 站点
- [Linux `include/linux/crc32.h`](https://github.com/torvalds/linux/blob/master/include/linux/crc32.h) — 「不首尾取反」
