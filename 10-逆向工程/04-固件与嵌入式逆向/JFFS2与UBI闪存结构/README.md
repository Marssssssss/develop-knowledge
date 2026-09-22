# 593 JFFS2 与 UBI 闪存结构

NOR/NAND 上的固件不走块设备文件系统，而是 JFFS2（日志型）或 UBIFS（建在 UBI 之上）。
这两层的解析要点是 **nodetype 的位组合** 与 **CRC 到底覆盖哪些字节**。

## 1. JFFS2：magic + nodetype 的两层信息

`include/uapi/linux/jffs2.h` 里的 nodetype 不是顺序编号，而是三个位域拼出来的：

```c
#define JFFS2_COMPAT_MASK    0xc000   /* 未知结点该怎么办 */
#define JFFS2_NODE_ACCURATE  0x2000
#define JFFS2_FEATURE_INCOMPAT        0xc000   /* 拒绝挂载 */
#define JFFS2_FEATURE_ROCOMPAT        0x8000   /* 只读挂载 */
#define JFFS2_FEATURE_RWCOMPAT_COPY   0x4000   /* GC 时拷贝 */
#define JFFS2_FEATURE_RWCOMPAT_DELETE 0x0000   /* GC 时直接删 */
```

由此得到：DIRENT=`0xE001`、INODE=`0xE002`、CLEANMARKER=`0x2003`、PADDING=`0x2004`、
SUMMARY=`0x2006`、XATTR=`0xE008`、XREF=`0xE009`。
判断兼容性只看高两位 —— `node_compat(t) = t & 0xc000`。

其它常量：`JFFS2_MAGIC_BITMASK 0x1985`、旧版 `0x1984`、`KSAMTIB_CIGAM_2SFFJ 0x8519`
（读到它就是**字节序反了**）、`JFFS2_EMPTY_BITMASK 0xffff`（擦除后的空位）、
`JFFS2_SUM_MAGIC 0x02851885`、名字最长 254（留一个空位给 0xff）。

## 2. 三个 CRC，覆盖范围各不相同

`fs/jffs2/summary.c` 与 `readinode.c` 给出了精确口径（**全部 seed 0**）：

```c
isum.hdr_crc  = crc32(0, &isum, sizeof(struct jffs2_unknown_node) - 4);  // 12-4 = 8 字节
rd->node_crc  = crc32(0, rd, sizeof(*rd) - 8);      // 掐掉末尾的 node_crc 与 name_crc
rd->name_crc  = crc32(0, fd->name, rd->nsize);      // 只覆盖名字
tn->data_crc  = crc32(0, buf, len);                 // 只覆盖数据
```

也就是说：

- `hdr_crc` 覆盖 **magic + nodetype + totlen**（8 字节），不含自己。
- dirent 的 `node_crc` 覆盖到 `unused[2]` 为止，**不含** `node_crc` / `name_crc` / `name`。
- raw inode 的 `node_crc` 覆盖前 60 字节，**不含** `data_crc` 与 `node_crc`，更不含 `data[]`。
- `data_crc` 只算数据。

实现时有个顺序陷阱：`hdr_crc` **本身也在 `node_crc` 的覆盖范围内**，
所以必须先算完 `hdr_crc` 落位，再算 `node_crc`；反过来会得到永远对不上的摘要。

## 3. UBI：两个 64 字节的大端头

`drivers/mtd/ubi/ubi-media.h`：

```c
#define UBI_EC_HDR_MAGIC   0x55424923   /* "UBI#" */
#define UBI_VID_HDR_MAGIC  0x55424921   /* "UBI!" */
#define UBI_CRC32_INIT     0xFFFFFFFFU
```

- EC 头：`magic / version / ec(u64) / vid_hdr_offset / data_offset / image_seq`，
  末尾 `hdr_crc` 覆盖前 60 字节。
- VID 头：`magic / version / vol_type / copy_flag / compat / vol_id / lnum /
  data_size / used_ebs / data_pad / data_crc / sqnum(u64)`，同样前 60 字节。
- CRC 种子是 `0xFFFFFFFF` 而**不是 0**（与 JFFS2 相反），且 `drivers/mtd/ubi/io.c` 里
  直接 `crc32(UBI_CRC32_INIT, hdr, UBI_EC_HDR_SIZE_CRC)`，不做首尾取反。
- 内部卷：`UBI_INTERNAL_VOL_START = 0x7FFFFFFF - 4096 = 0x7FFFEFFF`，
  layout volume 就用这个 id；用户卷上限 128，卷名最长 127。
- fastmap 有自己的一串魔数（`0x7B11D69F` 等），只在前 **64** 个 PEB 里找，最多用 **32** 块。

## 4. 同一个 LEB 出现两份时选谁

磨损均衡会搬移逻辑块，掉电后可能出现两个 PEB 都声称自己是同一个 `(vol_id, lnum)`。
`ubi-media.h` 的注释给全了规则：

1. 取 `sqnum` 更大的那个（新的）。
2. 若它的 `copy_flag` 为 0 —— 说明不是副本 —— 直接选它。
3. 若 `copy_flag` 为 1，还要看副本的 `data_crc` 是否正确：正确选新块，否则**退回旧块**
   （因为拷贝过程可能被掉电打断）。

注意「只选 sqnum 大的」是不够的，也不是「只选 sqnum 小的」。

## 5. 为什么这两层常常一起出现

常见布局是：UBI 提供卷与磨损均衡 → 某个卷里放 UBIFS，或直接放一个 squashfs/cramfs 镜像；
老设备则是裸 NOR + JFFS2。识别顺序一般是：先扫 `UBI#`（大端、每擦除块开头），
没命中再看有没有 `0x1985` 的 JFFS2 结点流；两者都没有才考虑裸 squashfs（`hsqs`）。

## 运行

```bash
python python/main.py
python python/selfcheck_jffs2ubi.py    # 106 条断言
cd go && go run .
```

## 参考资料

- [Linux `include/uapi/linux/jffs2.h`](https://github.com/torvalds/linux/blob/master/include/uapi/linux/jffs2.h) — magic / nodetype / 兼容标志 / 各结点结构
- [Linux `fs/jffs2/summary.c`](https://github.com/torvalds/linux/blob/master/fs/jffs2/summary.c) — `hdr_crc` 与 summary 的 `node_crc`
- [Linux `fs/jffs2/readinode.c`](https://github.com/torvalds/linux/blob/master/fs/jffs2/readinode.c) — `node_crc` / `name_crc` / `data_crc`
- [Linux `drivers/mtd/ubi/ubi-media.h`](https://github.com/torvalds/linux/blob/master/drivers/mtd/ubi/ubi-media.h) — EC/VID 头、内部卷、fastmap
- [Linux `drivers/mtd/ubi/io.c`](https://github.com/torvalds/linux/blob/master/drivers/mtd/ubi/io.c) — CRC 计算
- [Linux `include/linux/crc32.h`](https://github.com/torvalds/linux/blob/master/include/linux/crc32.h) — 「不首尾取反」
