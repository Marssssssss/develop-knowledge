# 595 MCUboot 镜像验签与安全启动

MCUboot 是 Zephyr / Mynewt 生态的事实标准 bootloader。它的镜像格式只有 32 字节头 +
一串 TLV，但**字节序与 U-Boot 相反（小端）**，哈希覆盖范围也有一个容易读漏的分支。

## 1. 头部：32 字节、全小端

`boot/bootutil/include/bootutil/image.h` 与 `docs/design.md`：

```c
#define IMAGE_MAGIC              0x96f3b83d
#define IMAGE_MAGIC_V1           0x96f3b83c      /* 只差最后一位 */
#define IMAGE_MAGIC_NONE         0xffffffff      /* 擦除后的 flash */
#define IMAGE_HEADER_SIZE        32

struct image_header {
    uint32_t ih_magic;            /* 0  */
    uint32_t ih_load_addr;        /* 4  */
    uint16_t ih_hdr_size;         /* 8  —— 镜像体的偏移 */
    uint16_t ih_protect_tlv_size; /* 10 —— 受保护 TLV 区长度 */
    uint32_t ih_img_size;         /* 12 */
    uint32_t ih_flags;            /* 16 */
    struct image_version ih_ver;  /* 20 —— u8 major / u8 minor / u16 revision / u32 build */
    uint32_t _pad1;               /* 28 */
};
```

`ih_hdr_size` 的存在是为了向后兼容：将来头变长时，老的 bootloader 也能靠它找到镜像体。
标志位里 `IMAGE_F_RAM_LOAD 0x20`（要搬进 RAM 再跑）、`IMAGE_F_NON_BOOTABLE 0x10`
（split image 的非启动半边）、`IMAGE_F_ENCRYPTED_AES128/256`。

## 2. TLV 区：紧跟 `hdr_size + img_size`

```c
struct image_tlv_info { uint16_t it_magic; uint16_t it_tlv_tot; };  /* tot 含自身 4 字节 */
struct image_tlv      { uint16_t it_type;  uint16_t it_len; };      /* len 不含 4 字节头 */
```

`docs/design.md` 的校验顺序：

1. magic 必须是 `IMAGE_MAGIC`；
2. `hdr_size + img_size` 处必须有一个 `image_tlv_info`；
3. 若它的 magic 是 `IMAGE_TLV_PROT_INFO_MAGIC`（0x6908），则
   **`ih_protect_tlv_size` 不得为 0**，且跳过该长度后必须再有一个
   magic 为 `IMAGE_TLV_INFO_MAGIC`（0x6907）的 `image_tlv_info`；
4. 必须存在 SHA256 TLV，且长度 32；
5. 计算出的摘要必须与 TLV 里的值一致。

常用 TLV 类型：KEYHASH `0x01`、PUBKEY `0x02`、SHA256 `0x10`、
RSA2048-PSS `0x20`、ECDSA `0x22`、ED25519 `0x24`、SEC_CNT `0x50`、
DECOMP_SHA `0x71`。`image_validate.c` 里的 `allowed_unprot_tlvs` 白名单
**不含 `SEC_CNT`** —— 安全计数器必须放在受保护区。

## 3. 哈希覆盖：受保护 TLV 参与，普通 TLV 不参与

```
sha256 = H( image_header || image_body || protected_tlv_area )
```

- `ih_protect_tlv_size == 0` 时只算前两段；
- 非 0 时把**整段受保护 TLV 区**（含 4 字节 info 头）一起算进去。

这条规则的后果值得用对照实验记住：

- 改受保护区里的一个字节 ⇒ 摘要失配 ⇒ 校验失败；
- 改普通区里 KEYHASH / 签名的值 ⇒ **摘要不变**（它们根本不在覆盖范围内）。
  签名另行校验，但「摘要比对」这一步照过。

`KEYHASH` TLV 的内容是 `SHA256(公钥)`：bootloader 里烧了若干把公钥，
启动时先算出每把公钥的摘要去匹配 KEYHASH，再用匹配上的那把验签名。
`MCUBOOT_HW_KEY` 模式下则改为把公钥整把放进 PUBKEY TLV、摘要由设备侧提供。

## 4. 镜像尾（trailer）在 slot 末尾

`boot/bootutil/include/bootutil/bootutil.h`：

```c
struct image_trailer {
    uint8_t swap_type;  uint8_t pad1[BOOT_MAX_ALIGN - 1];
    uint8_t copy_done;  uint8_t pad2[BOOT_MAX_ALIGN - 1];
    uint8_t image_ok;   uint8_t pad3[BOOT_MAX_ALIGN - 1];
    uint8_t magic[BOOT_MAGIC_SZ];
};
```

`BOOT_MAX_ALIGN` 默认 8，`BOOT_MAGIC_SZ` 为 16（由 `union boot_img_magic_t`
的 `uint8_t val[16]` 与 `_Static_assert(sizeof(boot_img_magic) == BOOT_MAGIC_SZ)` 反推）。
于是 trailer 长 40 字节，四个字段相对 slot 末尾依次是 `-40 / -32 / -24 / -16`。
magic 段供升级状态机（swap / overwrite）记录进度，`image_ok` 则是「新固件自检通过、
不要再回滚」的确认位。

## 运行

```bash
python python/main.py
python python/selfcheck_mcuboot.py    # 75 条断言
cd go && go run .
```

## 参考资料

- [MCUboot `docs/design.md` §Image format / §Security](https://github.com/mcu-tools/mcuboot/blob/main/docs/design.md) — 头结构、TLV info、校验顺序、KEYHASH
- [MCUboot `boot/bootutil/include/bootutil/image.h`](https://github.com/mcu-tools/mcuboot/blob/main/boot/bootutil/include/bootutil/image.h) — 全部 TLV 类型与标志位
- [MCUboot `boot/bootutil/src/image_validate.c`](https://github.com/mcu-tools/mcuboot/blob/main/boot/bootutil/src/image_validate.c) — `allowed_unprot_tlvs` 与摘要计算
- [MCUboot `boot/bootutil/include/bootutil/bootutil.h`](https://github.com/mcu-tools/mcuboot/blob/main/boot/bootutil/include/bootutil/bootutil.h) — `struct image_trailer`
- [MCUboot `boot/bootutil/src/bootutil_priv.h`](https://github.com/mcu-tools/mcuboot/blob/main/boot/bootutil/src/bootutil_priv.h) — trailer 的 ASCII 布局图
