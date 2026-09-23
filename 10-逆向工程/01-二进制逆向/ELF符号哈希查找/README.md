# ELF 符号哈希查找：DT_HASH 与 DT_GNU_HASH

> 逆向一个 stripped 的 `.so` 时，"这个名字在不在它里面、在第几号符号" 这件事不是靠 `.symtab`（可能根本没有），而是靠动态段里的两张哈希表。**DT_HASH（SysV）** 是桶 + 链表，**DT_GNU_HASH（GNU）** 在桶前面加了一层 64 位 bloom 过滤器。本 demo 把两者的哈希函数、表布局、查找路径逐行复刻成 Python / Go。

## 一、两套哈希函数

```text
SysV (_dl_elf_hash)                GNU (_dl_new_hash)
h = 0                              h = 5381
for c in name:                     for c in name:
    h = (h << 4) + c                   h = h*33 + c      # uint32 回绕
    hi = h & 0xf0000000
    h ^= hi >> 24
    h &= 0x0fffffff                # 结果恒 < 2^28
```

- SysV 每轮都 `&= 0x0fffffff`，所以返回值只有 **28 位**；GNU 是完整 uint32。这是两者「不可混用」的根源：同一个名字在两张表里的桶位几乎必不相同（实测 `aa`：SysV `1649 % 8 = 1`，GNU `5863207 % 8 = 7`）。
- `hi = h & 0xf0000000` 不是多余的：进入本轮时 `h < 2^28`，`h << 4` 可以顶到 32 位，高 4 位要折回低位。

## 二、DT_GNU_HASH 的表布局（`_dl_setup_hash`）

```
uint32 nbuckets
uint32 symbias          # 下标 < symbias 的符号不进哈希表
uint32 bitmask_nwords   # 必须是 2 的幂，源码里直接 assert
uint32 shift
uint64 bitmask[bitmask_nwords]
uint32 buckets[nbuckets]
uint32 chain[]          # l_gnu_chain_zero = chain - symbias
```

三个容易记反的点：

1. **`l_gnu_chain_zero = hash32 - symbias`**。chain 数组本体从符号 `symbias` 才开始，减掉偏移之后 `chain_zero[i]` 的下标 **就是符号索引** —— 这也正是 `ELF_MACHINE_HASH_SYMIDX(map, hasharr) = hasharr - l_gnu_chain_zero` 能直接当符号下标用的原因。
2. **`bitmask_nwords` 必须是 2 的幂**，因为下标是 `& (nwords-1)` 而不是取模。
3. bloom 字的下标是 **`(hash / __ELF_NATIVE_CLASS) & (nwords-1)`**，即「先除以 64 再掩码」。写成 `hash & (nwords-1)` 会在 `hash=64` 这种值上立刻错位（`64/64 & 1 = 1`，而 `64 & 1 = 0`）。

## 三、查找路径（`do_lookup_x`）

```text
h = _dl_new_hash(name)
w = bitmask[(h / 64) & idxbits]
b1 = h & 63 ;  b2 = (h >> shift) & 63
if ((w >> b1) & (w >> b2) & 1) == 0:      -> 不在（bloom 拦截）
    return NOT_FOUND
bucket = buckets[h % nbuckets]
if bucket == 0:                            -> 不在（空桶）
    return NOT_FOUND
i = bucket
loop:
    if ((chain_zero[i] ^ h) >> 1) == 0:    -> 候选，还要比名字
        if name matches: return i
    if chain_zero[i] & 1: break            -> 链尾
    i += 1
```

### 三个语义细节

- **bloom 只负责"拦截"，不负责"判定"**。它放行之后仍然要走 chain 比名字。实测在 60 个导出符号的表上，`q0`（`h=0x00597906`）能通过 bloom 但 chain 落空 —— 假阳性是常态，不是边界情况。
- **`((hv ^ h) >> 1) == 0` 只比较高 31 位**：最低位被让给了 chain 终止标志。所以两个哈希只差最低位的符号会**互相成为候选**，靠名字比较区分。实测 `s0 → 5863752`、`s1 → 5863753` 就是这样一对（末字符只差 1）。查找 `s1` 时 `s0` 会先被判为候选再刷掉。
- **`shift = 0` 会让过滤器退化**：此时 `b1 == b2`，两个判据其实是同一个比特，只查了 1 位。

## 四、DT_HASH 的对照

`nbuckets / nchain / buckets[] / chain[]`，查找是纯粹的桶链表：

```text
for (y = buckets[h % nbuckets]; y != STN_UNDEF; y = chain[y])
    if (name matches) return y;
```

**`STN_UNDEF == 0` 同时是"空桶"和"链表尾"的哨兵**，因此符号 0 永远无法被查找命中 —— 这也是 `symbias` 通常至少为 1 的现实理由（下标 0 是未定义符号）。本 demo 断言：chain 里值为 0 的那些位置，恰好就是各桶链表的表尾。

## 五、代码结构

| 文件 | 内容 |
| --- | --- |
| `python/elf_hash.py` | 两套哈希、建表（按 bucket 排序使 chain 连续）、bloom、chain 扫描、`_dl_setup_hash` 解析 |
| `python/selfcheck_elfhash.py` | **488 条断言实跑全绿** |
| `python/main.py` | 逐步演示（含 bloom 假阳性实例） |
| `go/elf_hash.go` + `go/main.go` | Go 侧同题实现（无工具链，走人工审查 + 四项静态检查） |

自检分三类：手算可复现的哈希常量（`dl_new_hash("a") = 5381*33+97 = 177670`、`dl_elf_hash("aa") = 1649`、`s0/s1` 相差 1）／与暴力线性扫描的等价性（60 个导出符号逐个对拍）／负控（非 2 的幂报错、混用哈希必进错桶、清掉链尾位后仍不凭空命中）。

## 六、实测输出（节选）

```text
nbuckets=4 symbias=1 bitmask_nwords=2 shift=6
符号表顺序 = ['', 'printf', 'memcpy', 'my_export', 'malloc', 'strlen', 'free']
chain_zero = ['0x0', '0x156b2bb8', '0xd827590', '0xbb66a7cd', '0xd39ad3c', '0x1c93bb9d', '0x7c96f087']
查 printf: bloom(word=0, bits=(56,46)) -> bucket 0 -> 候选 sym1 -> 命中
bloom 假阳性: q0 bloom=True  lookup=None
SysV: buckets=[6,3,1,2] chain=[0,5,0,4,0,0,0]  free -> symidx 3
```

## 参考资料（已读）

- [glibc `elf/simple-dl-new-hash.h`](https://raw.githubusercontent.com/bminor/glibc/master/elf/simple-dl-new-hash.h) — GNU hash `h = h*33 + c`
- [glibc `elf/simple-dl-hash.h`](https://raw.githubusercontent.com/bminor/glibc/master/elf/simple-dl-hash.h) — SysV hash 的 `hi` 折回
- [glibc `elf/dl-setup_hash.c`](https://raw.githubusercontent.com/bminor/glibc/master/elf/dl-setup_hash.c) — DT_GNU_HASH / DT_HASH 表头解析、`bitmask_nwords` 的 2 的幂断言
- [glibc `elf/dl-lookup.c`](https://raw.githubusercontent.com/bminor/glibc/master/elf/dl-lookup.c) — `do_lookup_x` 的 bloom 与 chain 分支（第 405–437 行）
- [glibc `sysdeps/generic/ldsodefs.h`](https://raw.githubusercontent.com/bminor/glibc/master/sysdeps/generic/ldsodefs.h) — `ELF_MACHINE_HASH_SYMIDX` 默认定义（第 57–59 行）
- [glibc `elf/elf.h`](https://raw.githubusercontent.com/bminor/glibc/master/elf/elf.h) — `DT_HASH` / `DT_GNU_HASH` / `STN_UNDEF` 常量
