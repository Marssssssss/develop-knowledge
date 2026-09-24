# dyld 链式修正(Chained Fixups)

Mach-O 里凡是"加载时要改写的指针"过去靠 `__LINKEDIT` 的两套操作码(opcode)流描述:`__dyld_rebase` 与 `__dyld_bind`。它们有两个代价:操作码必须**顺序解释**,无法随机访问;而且每一条都要进 LINKEDIT,体积占比可观。

链式修正(2018 起,iOS 12 的 dyld3 引入)换了个思路:**把"下一个待修正位置在哪"直接写进被修正的那个 64 位指针的空余位里**。于是修正信息从"一段需要解释的程序"变成"若干条可以并行、可以按页独立处理的链表",并且大部分比特复用指针本身的高位,不再额外占用 LINKEDIT。这也是它能支持**按页惰性修正**(kernel page-in linking)的前提——内核拿到一个页,只要顺着链走完就能把这一页修好,不需要碰别的页。

本 demo 按 `apple-oss-distributions/dyld@main` 的源码,把这套结构完整转写成可执行的模型。

## 一、整体布局

`LC_DYLD_CHAINED_FIXUPS` 的负载是一个 `dyld_chained_fixups_header`:

```text
fixups_version / starts_offset / imports_offset / symbols_offset / imports_count
imports_format (1=IMPORT, 2=IMPORT_ADDEND, 3=IMPORT_ADDEND64) / symbols_format
```

`starts_offset` 指向 `dyld_chained_starts_in_image`:先是一个 `seg_count`,接着 `seg_info_offset[]` 每段一项,段数据池跟在后面。每段的 `dyld_chained_starts_in_segment` 是:

```text
size / page_size(0x1000 或 0x4000) / pointer_format / segment_offset
max_valid_pointer / page_count / page_start[]
```

`page_start[]` 是**每页一项**的 uint16 数组,值就是该页第一条链在页内的偏移。这里有三态:

| 值 | 含义 |
| --- | --- |
| `0xFFFF` (`START_NONE`) | 本页没有任何修正 |
| 高位为 1 (`START_MULTI = 0x8000`) | 本页有多条链,低 15 位是 overflow 列表的**下标** |
| 其它 | 本页一条链,值就是页内偏移 |

overflow 列表排在 `page_start[]` 的数组尾部(下标 ≥ `page_count`),每项仍是 uint16,带 `START_LAST = 0x8000` 的那项是列表末尾。注意 `START_MULTI` 与 `START_LAST` 是**同一个位值**,靠"出现在哪个数组里"区分语义。

一个容易踩的点:`0xFFFF` 同时满足 NONE 与 MULTI,源码是**先判 NONE** 再判 MULTI(`ChainedFixups.cpp` 的 `forEachFixupChainStartLocation`),所以空页不会被当成"overflow 下标 0x7FFF"去乱读。

## 二、链怎么走

```text
loc = 页内链起点
while loc != nil:
    raw   = *(uint64*)loc
    next  = raw 的 next 字段          # 先算,因为回调会把 raw 改写成真正的指针
    处理(raw)
    if next == 0: 结束
    if loc + next*stride > 页末地址: 断链      # 判据是"严格大于"
    loc = loc + next*stride
```

三个边界(都在 `forEachFixupLocationInChain` 里):

1. **`next == 0` 是链结束符**,不是"下一个还是自己"。所以一条链最多能覆盖 `maxNext = stride * (2^next_bits - 1)` 字节,`PTR_64` 下是 `4 * 0xFFF = 16380`,arm64e 下是 `8 * 0x7FF = 16376`。链上两个修正点距离超过这个数,链接器就必须**另起一条链**(这正是 MULTI 存在的原因)。
2. **链起点必须落在本页**,否则整条链被丢弃。
3. **next 跨出页末才断链**,恰好落在页末地址(即下一页的第一个字节)不算出页,会继续走进下一页。

`stride` 由 `pointer_format` 决定,不是恒为 8:`PTR_ARM64E`/`USERLAND` 是 8,`PTR_ARM64E_KERNEL`/`FIRMWARE` 是 4,`PTR_64`/`PTR_32` 是 4。

## 三、条目格式

位域从最低位开始分配。以 `DYLD_CHAINED_PTR_64`(=2)为例:

```text
rebase : target:36 | high8:8 | reserved:7 | next:12 | bind:1
bind   : ordinal:24 | addend:8 | reserved:19 | next:12 | bind:1
```

- `bind` 位是最高位,决定这一格是 rebase 还是 bind。
- `target` 只有 36 位,装不下 64 位指针,所以**最高的 8 位单独存进 `high8`**,解析时拼回去:`(high8 << 56) | target`。
- **`target` 的口径随格式变**:`PTR_64` 存的是 vmaddr(解析时要减 `preferredLoadAddress`),`PTR_64_OFFSET` 存的就是 vm offset(不减)。同一串比特按错误口径读会整整差一个基址,而且因为是无符号运算,结果是绕回成一个巨大的数而不是负数。

arm64e 系列多了 `auth` 位(bit 63)与 PAC 相关字段:

```text
rebase     : target:43 | high8:8  | next:11 | bind:1 | auth:1
authRebase : target:32 | diversity:16 | addrDiv:1 | key:2 | next:11 | bind:1 | auth:1
bind       : ordinal:16 | zero:16 | addend:19 | next:11 | bind:1 | auth:1
```

未认证 rebase 的 target 有 43 位(上限 `0x7FFFFFFFFFF`),认证 rebase 只有 32 位(`0xFFFFFFFF`)——因为省下的位给了 diversity/key。认证条目**不嵌 addend**,`bindMaxEmbeddableAddend(auth=true)` 直接返回 0。

32 位格式更紧:`next` 只有 5 位,故一条链最长 `4 * 0x1F = 124` 字节;`addend` 只有 6 位(0 到 63);`ordinal` 20 位。

## 四、回写校验:超宽是"读回来"才发现的

链接器写条目时,源码先把值赋进位域(超出位宽被**静默截断**),然后**读回位域**跟原值比对,不一致才报错。这是本 demo 里最容易写错的一处:如果直接拿"赋值前的变量"去比,超宽的 `next` 永远比不出问题。

```text
bindPtr->addend = fixup.bind.embeddedAddend;      // 300 被截成 44
if (bindPtr->addend != fixup.bind.embeddedAddend) // 读回再比,才发现不对
    return badAddend(...);
```

四类错误:`badAddend`、`badBindOrdinal`、`badChainDistance`、`badVmAddr` / `badVmOffset`。

## 五、imports 表

`imports_format` 决定每个表项的宽度:`IMPORT` 一个 uint32(`lib_ordinal:8` 有符号、`weak_import:1`、`name_offset:23`);`IMPORT_ADDEND` 两个 uint32,后一个是 32 位有符号 addend;`IMPORT_ADDEND64` 两个 uint64,`lib_ordinal` 扩到 16 位、`name_offset` 扩到 32 位。`lib_ordinal` 是补码,按源码注释范围是 `-15 .. 240`(`0xF1 .. 0xF0`)。

## 六、边界与坑(自检里都有对应用例)

- `next` 是"步长倍数"不是字节数,`next = 1` 在 arm64e 上是跳 8 字节、在 `PTR_64` 上是跳 4 字节。
- `next == 0` 终止,因此**链首不能是链尾**:单元素链写作 `next = 0` 即可。
- `next` 恰好等于页末地址不算出页,会读进下一页;只有严格大于才断。
- `0xFFFF` 先按 NONE 处理,不会走进 MULTI 分支。
- MULTI 的 overflow 下标用的是 `ps & ~0x8000`,而列表项判末位用的是同一个 `0x8000`,两者共用一位但处于数组的不同区域。
- 若把"多链页"的 `page_start` 写成单个偏移,第二条链会被**静默漏掉**(demo 的 `main.py` 实测:5 个修正只剩 3 个)。
- arm64e 16 位 ordinal 的 bind addend 是 19 位(可嵌 ±256K);源码里对应的 `signExtendedAddend` 重载先把字段当 27 位拆分,但该位域只有 19 位,其高 8 位恒为 0,本 demo 只覆盖非负区间。
- `max_valid_pointer` 允许各段填 0,但非零时必须**所有段一致**,否则 `validLinkedit` 直接报错。

## 七、文件与运行

```text
python/chainfix_const.py   位域工具与 fixup-chains.h 常量
python/chainfix_format.py  各 pointer_format 的 parse / write 与超宽校验
python/chainfix_walk.py    page_start 解码、链遍历、imports 解析
python/chainfix_model.py   统一再导出入口
python/selfcheck_chainfix.py  87 条断言(实跑全绿)
python/main.py             拼一个两页的假段,走完全部链
go/chainfix.go             常量与格式层(Go 侧)
go/chainfix_walk.go        遍历层
go/main.go                 同题演示
```

```bash
cd python && python selfcheck_chainfix.py && python main.py
```

Go 侧本机无工具链(`which go` 为空),走人工审查 + `bracket_check` / `go_sanity` / `go_crossref` / `syntax_sanity` 四项静态检查,均通过。

## 八、参考资料(实际读过)

- `apple-oss-distributions/dyld@main` · `include/mach-o/fixup-chains.h`(12659 B,位域与常量唯一权威)— https://github.com/apple-oss-distributions/dyld/blob/main/include/mach-o/fixup-chains.h
- `apple-oss-distributions/dyld@main` · `mach_o/ChainedFixups.cpp`(71827 B,`forEachFixupChainStartLocation`、`forEachFixupLocationInChain`、各 `parseChainEntry` / `writeChainEntry`)— https://github.com/apple-oss-distributions/dyld/blob/main/mach_o/ChainedFixups.cpp
- `apple-oss-distributions/dyld@main` · `common/MachOAnalyzer.cpp`(251320 B,`validLinkedit` 与 `max_valid_pointer` 一致性检查)— https://github.com/apple-oss-distributions/dyld/blob/main/common/MachOAnalyzer.cpp

> 口径说明:本 demo 只做**编解码与遍历**建模,不模拟 slide 应用、PAC 签名与内核 page-in linking 的并发语义;`max_valid_pointer` 只实现"各段必须一致"的校验规则,没有复刻 32 位下"超出 `max_valid_pointer` 的值按 `(64MB + max_valid_pointer) / 2` 偏置还原"的非指针还原路径。
