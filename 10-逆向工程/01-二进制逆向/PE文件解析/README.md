# PE 文件解析(DOS 头 / COFF 头 / 节表 / 导入表)

> Windows 逆向的"第一脚":malware 分析、脱壳、IAT 修复都以手工解析 PE 结构为地基。本 demo 依据 Microsoft 官方 PE Format 规范,用纯 Python(stdlib `struct`)实现 PE32 / PE32+ 双格式解析,核心是 **RVA → 文件偏移换算** 与**导入表遍历**。

## 简介

- **实现**:`pe_parser.py`(单文件,零依赖)
- **语言**:Python
- **输入**:任意 `.exe` / `.dll` / `.sys`(PE32 与 PE32+ / x86 / x64 / ARM64)

## 原理详解

### 头部链路(每一步都是下一步的偏移来源)

```text
偏移 0x00  DOS 头: e_magic = 'MZ'(0x5A4D)          ← 最古老的兼容层
偏移 0x3C  e_lfanew ──────────────┐                 ← PE 头的文件偏移(唯一桥梁)
                                    ▼
e_lfanew    NT 签名 'PE\0\0' + COFF 文件头(20B):Machine / NumberOfSections / SizeOfOptionalHeader
e_lfanew+24 可选头: Magic 0x10b(PE32)/ 0x20b(PE32+)→ ImageBase / EntryPoint / 两个对齐 / 16 个数据目录
可选头之后  节表: NumberOfSections × 40B
```

**`e_lfanew` 是整个格式的单点枢纽** —— 加壳器最爱的篡改目标之一:把它指向节区中间,可以让"按规范写的解析器"崩溃,而真正的 Windows 加载器容忍度更高。DOS stub 里那句 "This program cannot be run in DOS mode" 就是历史包袱:PE 借用 DOS 头纯粹为了向后兼容。

### PE32 vs PE32+(Magic 0x10b / 0x20b)三处布局差异

| 字段 | PE32(偏移) | PE32+(偏移) |
| --- | --- | --- |
| ImageBase | 可选头 +28,4 字节 | 可选头 +24,**8 字节** |
| BaseOfData | +24 存在 | **被删除** |
| 数据目录起点 | +96 | +112 |
| thunk 项宽 | 4 字节 | 8 字节 |

本 demo 用 `self.pe32plus` 分支处理所有差异 —— 手工解析器若只按 PE32 写,遇到 x64 程序会把 ImageBase 读成两截垃圾。

### RVA → 文件偏移(一切目录解析的公共前置)

```
fileOffset = RVA − section.VirtualAddress + section.PointerToRawData
```

节内虚拟区间用 `max(VirtualSize, SizeOfRawData)` 判断;`VirtualSize > SizeOfRawData` 的尾部是零填充区(BSS 语义),磁盘上**没有**对应数据。头部区(RVA < 首节 VA)磁盘与内存布局一致,直接返回 RVA。为什么需要换算:节在文件里按 `FileAlignment`(通常 512)对齐,加载进内存按 `SectionAlignment`(通常 4K 页)对齐,**同一个节在两个世界里"错开"了**。

### 导入表(数据目录 #1)与 IAT 双表结构

每个 `IMAGE_IMPORT_DESCRIPTOR`(20B)描述一个 DLL:

```text
OriginalFirstThunk ─→ ILT/INT(名字表): 项最高位=0 → IMAGE_IMPORT_BY_NAME(WORD Hint + 名字)
                                            最高位=1 → 序号导入(#ordinal)
FirstThunk         ─→ IAT(地址表):      加载器把它改写成真实函数地址
```

- **为什么两张表指向几乎相同的内容**:加载器改写 IAT 后名字信息就"没了"(IAT 全变成地址),ILT 留作静态工具的备份;绑定导入时两者还会被有意差异化。
- **逆向意义**:脱壳后重建导入表、检测可疑 DLL(如进程注入往 IAT 里塞 `LoadLibraryA`)、计算 **imphash**(导入表哈希,家族归类的经典特征)都从这张表出发。

## 运行方式

```bash
python pe_parser.py C:\Windows\System32\notepad.exe
python pe_parser.py .\some.dll
```

预期输出:头部链字段(机器/节数/ImageBase/EntryPoint 的 RVA 与 VA)→ 节表 → 数据目录前 8 项(附换算出的文件偏移)→ 每个 DLL 的导入函数列表。

## 关键代码说明

- `rva_to_offset()` 是全文件唯一做"内存视图 → 文件视图"翻译的地方,导入表/名字表全部经过它 —— 写 PE 工具时把换算集中到一处,可避免每个目录各写一份然后各自漏 `.bss` 边界的经典 bug。
- `_walk_thunks()` 用 `ordinal_flag = 1 << 63(PE32+)/1 << 31(PE32)` 判断序号导入;名字 RVA 前有 2 字节 Hint,所以 `cstr(hint_off + 2)`。

## 性能边界

- 全文件读入 + 线性扫描,复杂度 O(文件大小);导入表遍历对每个 DLL 上限 256 描述符 / 4096 thunk,防损坏文件死循环。
- 不解析绑定导入(TimeDateStamp ≠ 0 时 IAT 已是加载器可校验的预绑定地址)、延迟导入(#13)与导出表 —— 扩展点已按数据目录枚举留好。

## 注意事项与常见坑

1. **节名不是规范字段**:8 字节仅约定俗成,加壳器可任意改名;判断代码节应看 `Characteristics` 的 `IMAGE_SCN_MEM_EXECUTE`(0x20000000)而不是名字 `.text`。
2. **ASLR 下 ImageBase 只是"首选地址"**:`DllCharacteristics & 0x0040(DYNAMIC_BASE)` 时实际基址由加载器随机化,逆向时看到 VA 不等于 ImageBase + RVA 不要怀疑解析器。
3. **NumberOfSections 为 0 或节表重叠** 是文件损坏/反分析手法,工业级解析器(pefile 等)会做大量一致性校验,本 demo 只做基础边界检查。
4. **重叠节头 / 错误 e_lfanew** 可能导致 `rva_to_offset` 返回越界偏移后 `struct.unpack_from` 抛异常 —— 恶意样本面前,解析器崩溃本身就是要处理的信号。

## 参考资料(实际读过)

- [PE Format — Microsoft Learn(官方规范)](https://learn.microsoft.com/en-us/windows/win32/debug/pe-format):COFF 头、可选头 PE32/PE32+ 字段表、节表 40B 布局、数据目录 16 项定义、对齐规则
- [YARA 官方文档 writing-rules(PE 模块以本格式为前提)](https://yara.readthedocs.io/en/stable/writingrules.html):`pe.entry_point` / `pe.sections` / `pe.imphash()` 等模块字段与此处解析结果对应
