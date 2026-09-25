# 最小 WASM 模块手工解码:magic、段结构、LEB128 与栈机

> 不用任何工具链,手工**构造**一个可导出 `add` 函数的最小 WebAssembly 模块字节,
> 再**解码**回来并用栈机求值——WASM 的"可移植、可验证、紧凑"三个卖点,
> 在字节层看得分外清楚。

## 1. 模块骨架(W3C 二进制格式规范)

```text
magic    ::= 0x00 0x61 0x73 0x6D        ;; 字面 "\0asm"
version ::= 0x01 0x00 0x00 0x00        ;; 小端 u32 = 1
section ::= id:u8 + size:u32 + 内容     ;; size 只数内容字节
```

本 demo 的四段:id 1(type)/ 3(function)/ 7(export)/ 10(code);
custom 段(id 0)语义上被忽略——调试信息与第三方扩展的官方后门。

## 2. LEB128 变长整数(conventions.html)

- 7 位一组、高位续接、**低组在前**;
- u32 无符号直接切组;s32 用符号扩展终止判定(`-1` 编成单字节 `0x7F`);
- 教科书案例:624485 → `E5 8E 26`;-123456 → `C0 BB 78`。

## 3. 四个操作码搭一个 add(instructions.html)

| 字节 | 指令 | 栈动作 |
| --- | --- | --- |
| `0x20 x` | local.get | 压入第 x 个局部变量 |
| `0x41 n` | i32.const | 压入 s32 立即数 |
| `0x6A` | i32.add | 弹两个 i32,压回和(按 2^32 回绕) |
| `0x0B` | end | 函数体终结 |

functype 一律以 `0x60` 开头(param\* → result\*);export 段是
`count + (name + kind + idx)` 的**向量套向量**——name 自身又是字节向量。

## 4. 求值与回绕

i32 是补码、按位运算:`(2^31-1) + 1` 回绕成 `-2^31`——
"溢出"在 WASM 语义里不存在,只有回绕。类型检查(验证阶段)
保证栈上弹的一定是 i32。

## 自检

`python python/wasm_min.py` —— 5 项断言:LEB128 六个经典值 /
magic+version 字节 / 段框架(解码→重编码逐字节相等)/ functype 0x60 与
export 向量套向量 / 栈机求值与 2^32 回绕。Go 侧 `go/wasm_min.go`
为同语义复刻(静态审查)。

## 参考资料(实读)

- [W3C WebAssembly Spec — Binary Format: Modules(magic/version/section)](https://webassembly.github.io/spec/core/binary/modules.html)
- [W3C WebAssembly Spec — Binary Format: Conventions(LEB128)](https://webassembly.github.io/spec/core/binary/conventions.html)
- [W3C WebAssembly Spec — Binary Format: Types(functype 0x60)](https://webassembly.github.io/spec/core/binary/types.html)
- [W3C WebAssembly Spec — Binary Format: Instructions(0x41/0x20/0x6A/0x0B)](https://webassembly.github.io/spec/core/binary/instructions.html)
