# Swift 宏的展开机制(swift-syntax)

Swift 宏不是"文本替换",也不是 C 预处理器那套。它是一个**语法树到语法树的变换**:编译器把带宏属性的语法节点序列化后交给宏插件,插件里的宏实现拿到的是 `SwiftSyntax` 的语法树,返回的也是语法树(或字符串),再由编译器把结果重新解析并插回原位置。

支撑这套机制的库是 `apple/swift-syntax`。本 demo 读的是其中两个文件:`MacroSystem.swift`(宏的注册、查找、在语法树上批量应用)与 `MacroExpansion.swift`(角色分派、结果合并、上下文)。

## 一、宏角色

`MacroRole` 一共 11 个有对应协议的角色:

| 角色 | 协议 | 独立 / 附着 |
| --- | --- | --- |
| `expression` | `ExpressionMacro` | 独立 |
| `declaration` | `DeclarationMacro` | 独立 |
| `codeItem` | `CodeItemMacro` | 独立 |
| `accessor` | `AccessorMacro` | 附着 |
| `memberAttribute` | `MemberAttributeMacro` | 附着 |
| `member` | `MemberMacro` | 附着 |
| `peer` | `PeerMacro` | 附着 |
| `conformance` | `ConformanceMacro` | 附着 |
| `extension` | `ExtensionMacro` | 附着 |
| `preamble` | `PreambleMacro` | 附着(实验特性) |
| `body` | `BodyMacro` | 附着 |

"独立宏"写作 `#name`,出现在表达式、声明或代码项的位置;"附着宏"写作 `@name`,挂在某个声明上。宏实现类型**同时**可以遵循多个协议,于是同一个宏在不同语境下扮演不同角色。

只有一个历史遗留入口需要"猜角色":`inferFreestandingMacroRole` 按 **expression → declaration → codeItem** 的顺序试,一个都不符合就报 `noFreestandingMacroRoles`。这是给"新插件配旧编译器"用的兜底,正常路径都应该显式传角色。

## 二、注册与查找

```swift
struct MacroSystem {
  var macros: [String: MacroSpec] = [:]
  mutating func add(_ macroSpec: MacroSpec, name: String) throws   // 重名抛 alreadyDefined
  func lookup(_ macroName: String, moduleName: String? = nil) -> MacroSpec?
}
```

两个容易忽略的点:

1. **重名注册会抛错,而且已注册的那条保持原样**——不是覆盖。
2. **模块名是双向校验**:调用方传了 `moduleName`,而注册项的 `moduleName` 与之不同 → 返回 `nil`。反过来,如果注册时**没有**写模块名,那么调用方一旦指定了模块就**查不到**。

属性语法上的写法有三种都能提取出模块:`@Name`、`@Module.Name`、`@Module::Name`。`attachedMacroReference` 分两个分支:`IdentifierTypeSyntax` 直接读 `moduleSelector`;`MemberTypeSyntax`(即 `@Outer.Inner` 这种嵌套类型写法)要求**自身没有 moduleSelector、基类型是 IdentifierType 且基类型既没有 moduleSelector 也没有泛型参数**,否则返回 `nil`。所以 `@A.B.C`(三段)和 `@Gen<Int>.Name`(带泛型)都解析不出来。

## 三、结果合并:collapse

一个附着宏可能返回多个展开片段,`collapse` 负责把它们拼成一个字符串。默认分隔符是**空行**,但角色会改这个默认值:

| 角色 | 分隔符 | 额外处理 |
| --- | --- | --- |
| 默认(member/peer/extension/…) | `\n\n` | — |
| `memberAttribute` | ` ` (一个空格) | — |
| `preamble` | `\n` | 语句之间只留一个换行 |
| `accessor` | 包裹时变 `\n` | **仅当声明本来没有 accessorBlock** 才包花括号 |
| `body` | 包裹时变 `\n` | 总是包花括号 |

包裹(`wrapInBraces`)做三件事:每一段**逐行**缩进(默认 4 空格,首行也缩进)、首段前加 `{\n`、末段后加 `\n}`。

还有一条细节:拼接时如果某一段**已经以分隔符开头**,就不再重复加分隔符。这条对 `memberAttribute` 特别重要——返回的往往是 `@available(...)` 这类以空格或无空格开头的东西。

## 四、独立宏的三态结果

`expandFreestandingMacro` 的返回是一个枚举,不是 Optional:

```swift
enum MacroExpansionResult<ResultType> {
  case success(expansion: MacroExpansion<ResultType>)  // 展开成功
  case failure                                          // 找到了宏,但展开抛错或返回 nil
  case notAMacro                                        // 这个节点压根不是已注册的宏
}
```

区分 `failure` 与 `notAMacro` 的意义在于:`#unknownMacro` 不应该被当成"宏坏了"来报错,它是普通的无法识别语法。

## 五、递归检测

宏 A 的展开结果里如果又出现了宏 A,就会无限展开。swift-syntax 的做法是维护一个**正在展开的独立宏类型栈** `expandingFreestandingMacros`:

```swift
guard !expandingFreestandingMacros.contains(where: { $0 == macro }) else {
    throw MacroExpansionError.recursiveExpansion(macro)
}
```

三个要点:

- 检测的是**宏实现类型**,不是名字。所以同一个类型注册了两个名字,嵌套使用也算递归。
- 进出栈由 `MacroExpansion.withExpandedNode` 精确配对(push → 调用 body → `defer` pop),源码注释特意要求"对展开结果的进一步展开只能在 body 里做",否则栈就对不上了。
- 递归被拦下时,**内层**返回 `failure` 并记诊断;内层的 `nil` 结果会让**外层**也随之变成 `failure`。也就是说一次递归会让整条嵌套链一起失败,而不是只砍掉最里面那一层。

## 六、附着宏:一个抛错不影响其它

`expandMacros` 遍历某个声明上所有符合角色的宏属性,逐个展开;每个属性单独 `do/catch`,抛错就 `addDiagnostics` 然后**继续处理下一个**。所以一个声明上挂三个 member 宏,中间那个抛错,另外两个的展开结果仍然会合并进去。

过滤条件是"实现类型必须遵循该角色对应的协议",不符合的直接跳过(不报错)。

## 七、展开上下文

`MacroExpansionContext` 提供四样东西:`makeUniqueName`、诊断、`location(of:)`、`lexicalContext`。

`lexicalContext` 是"由内到外"的语法上下文数组,第一项是**最内层**(对附着宏来说通常就是被挂的那个声明);独立宏在文件作用域时它可以是空数组。为了保护宏不窥探无关代码,源码会**剥掉细节**:函数与闭包去掉 body、类型与 extension 清空成员列表、属性与下标去掉 accessor block。

`PrependLexicalContextWrapperContext` 是一个只做"在前面追加节点"的包装:`lexicalContext == prepend + wrapped.lexicalContext`。但注意它的 `makeUniqueName` 是**直接转发**给被包装的 context——唯一名的计数器不因包装层而另起一套。

## 八、边界与坑(自检里都有对应用例)

- `accessor` 角色**只在声明本来没有 accessorBlock 时**才包花括号;已有 `{ get { ... } }` 的声明再展开,结果是裸片段而不是再套一层。
- `memberAttribute` 的分隔符是空格,而且如果片段本身已以分隔符开头就不重复加。
- `wrapInBraces` 的缩进是逐行的,首行也要缩进。
- 重复注册同名宏抛 `alreadyDefined`,旧条目**不被覆盖**。
- 注册时没写模块名 + 调用时指定了模块 → 查不到(不是"放行")。
- `@A.B.C` 与 `@Gen<Int>.Name` 解析不出宏引用。
- 递归判定按**实现类型**而非名字;递归会让内层与外层一起 `failure`。
- 多个附着宏中一个抛错,其余照常合并,诊断各记一条。

## 九、文件与运行

```text
python/macro_const.py        角色、协议名、诊断文案
python/macro_system.py       注册/查找、属性名解析、角色推断、上下文包装
python/macro_expand.py       collapse、独立宏三态、递归栈、附着宏批量展开
python/selfcheck_macro.py    71 条断言(实跑全绿)
python/main.py               注册几个宏走一遍全流程
go/macroexpand.go            Go 侧同题实现
go/main.go                   演示入口
```

```bash
cd python && python selfcheck_macro.py && python main.py
```

Go 侧本机无工具链(`which go` 为空),走人工审查 + `bracket_check` / `go_sanity` / `go_crossref` / `syntax_sanity` 四项静态检查,均通过。

## 十、参考资料(实际读过)

- `apple/swift-syntax@main` · `Sources/SwiftSyntaxMacroExpansion/MacroExpansion.swift`(24641 B,`MacroRole` 与 `protocolName`、`inferFreestandingMacroRole`、`collapse`、`PrependLexicalContextWrapperContext`)— https://github.com/apple/swift-syntax/blob/main/Sources/SwiftSyntaxMacroExpansion/MacroExpansion.swift
- `apple/swift-syntax@main` · `Sources/SwiftSyntaxMacroExpansion/MacroSystem.swift`(57589 B,`MacroSystem.add/lookup`、`attachedMacroReference`、`expandMacros`、`expandFreestandingMacro`、`MacroExpansion.withExpandedNode`)— https://github.com/apple/swift-syntax/blob/main/Sources/SwiftSyntaxMacroExpansion/MacroSystem.swift
- `apple/swift-syntax@main` · `Sources/SwiftSyntaxMacros/MacroExpansionContext.swift`(7316 B,`makeUniqueName`、`lexicalContext` 的剥离规则、`PositionInSyntaxNode`)— https://github.com/apple/swift-syntax/blob/main/Sources/SwiftSyntaxMacros/MacroExpansionContext.swift

> 口径说明:本 demo 只建模**展开编排层**(注册查找、角色分派、结果合并、递归检测、上下文),不涉及真正的 Swift 语法树构造与解析;`AttributeRemover` 对 trivia 的搬迁规则、`MacroSpec` 的 `inheritedTypeList` 合成、以及编译器侧的插件进程通信都不在建模范围内。
