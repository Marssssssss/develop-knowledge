# Swift 参数包(变长泛型)

`zip` 只能接受两个序列,`Zip2Sequence` 这个名字本身就写着"二"。想写一个接受任意多个序列的版本,在 Swift 5.9 之前没有语言层面的解法——你只能为 2、3、4… 各写一份重载。

参数包(Swift 5.9 的 SE-0393/0398/0399,Swift 6.0 补上 SE-0408)把"任意多个类型参数"和"任意多个值参数"变成了一等公民:

```swift
func zip<each S: Sequence>(_ seq: repeat each S) -> (repeat each S) { ... }
```

这里的 `<each S>` 是**类型参数包**,`repeat each S` 是**包扩展类型/表达式**。本 demo 按四份 Swift Evolution 提案把这套机制建模成可执行的代码。

## 一、三种"包"

| 概念 | 写法 | 含义 |
| --- | --- | --- |
| 类型参数包 | `<each T>` | 零个或多个类型参数 |
| 值参数包 | `func f(_ v: repeat each T)` | 零个或多个值参数 |
| 包扩展类型 | `(repeat each T)` | 由包逐元素构造出的类型(常见于元组) |
| 包扩展表达式 | `repeat each v` | 由包逐元素构造出的表达式 |

类型参数包**不能**直接写在返回类型、typealias 或局部变量类型上;解决办法就是把它包进元组——这正是 SE-0399 要处理的场景。

## 二、捕获:哪一层 repeat 捕获哪个包

包扩展表达式 `repeat <pattern>` 会**捕获**模式里出现的包。判据是:包以 `each p` 出现在模式中且**中间没有再隔着一层包扩展**。

提案原文的例子:

```swift
repeat foo(each x, (each T).self, (repeat each y))
```

外层捕获 `x` 与 `T`,**不**捕获 `y` —— 因为 `y` 被内层的 `repeat each y` 捕获了。

同一条规则也作用于"类型":如果被捕获的值包 `x` 的类型是 `Foo<U, (repeat each V)>`,那么这次捕获顺带捕获 `U`,但**不**捕获 `V`,理由相同。

## 三、同形状要求(same-shape requirement)

如果包扩展的模式里出现了**两个以上**的包,它们必须长度相同、且包扩展类型出现在相同的位置。这条约束叫同形状要求,`shape(T) == shape(U)`。

关键点:**同形状要求没有语法写法,只能推断**。推断有两个来源:

1. **同类型包要求**:`Pair<each First, each Second> == (each S).Element` 蕴含 `shape(First) == shape(Second)`、`shape(First) == shape(S)`、`shape(Second) == shape(S)` —— 两侧捕获到的包两两合并。
2. **出现在下列位置的包扩展类型**:① 泛型函数尾随 `where` 子句里的所有类型;② 泛型函数的**参数类型与返回类型**。这些位置上捕获到的包两两合并。

所以 `zip` 的返回类型 `(repeat (each T, each U))` 会自动推断出 `shape(T) == shape(U)`,调用 `zip(firsts: 1, 2, seconds: "hi")` 会报错。

而**不在**这两个位置的包扩展,不做推断、只做检查:包必须**已经**已知同形状,否则报错。提案里给的对照例子是

```swift
func foo<each T, each U>(t: repeat each T, u: repeat each U) {
  let tup: (repeat (each T, each U)) = ...   // 错:shape(T) == shape(U) 未被推断
}
```

因为局部变量的类型标注不属于上面两个位置。

### 形状只有"抽象"一种

为了不让同类型要求退化成整数线性方程组(提案里举了 `shape(Q) = 2*shape(R) + 1` 这种例子),本提案**只开放抽象形状**:每个包自带一个抽象形状,同形状要求把抽象形状合并成等价类;任何给包强加**具体形状**的要求都被诊断为 conflict,就像 `where T == Int, T == String` 那样。

## 四、变长泛型类型(SE-0398)

泛型类型也能抽象过包:`struct ZipSequence<each S: Sequence>`。约束是:

- **一个泛型类型最多声明一个类型参数包**(`struct S<each T, each U>` 非法);不过靠嵌套仍能抽象多个包。
- 引用时非包形参构成**固定的前缀与后缀**,包吃掉中间那一段:

```swift
struct S<T, each U, V> {}
S<Int, Float>                 // T := Int, U := Pack{}, V := Float
S<Int, Bool, Float>           // U := Pack{Bool}
S<Int, Bool, String, Float>   // U := Pack{Bool, String}
S<Int>                        // 错:至少要 2 个实参(非包形参数)
```

- **`V< >` 不等于 `V`**:前者把包替成空包;后者根本没约束包,只在能推断的语境里合法。
- 实参列表里的占位符 `_` 总是被理解为**一个**包元素,所以 `V<_>` 对不上 `V<Int, String>`。
- **实存属性的类型里可以含包扩展类型,但自身不能就是包扩展类型**——必须嵌在元组、函数类型或别的变长具名类型里。`var a: repeat each T` 非法,`var a: (repeat each T)` 合法。

### 要求推断的三条规则

1. 一个只会推断**标量**要求的泛型类型,被施加到包元素上时,推断出的是**要求扩展**(`repeat each U: P`)。
2. 一个自身带**要求扩展**的泛型类型,推断时会为每个具体实参各展开一条(`Int: P, V: P, repeat each U: P`)。
3. 若这种类型出现在包扩展内部:① 含多个**被不同深度扩展捕获**的包 → 非法;② 否则等价于**最内层**的那条要求扩展。

## 五、抽象元组(SE-0399)

`(repeat each T)` 这种"元素就是某个类型参数包、没有多余元素、没有标签"的元组叫**抽象元组类型**。提案允许在重复模式里直接引用它,从而把元组里的包取出来。

最有说服力的是提案里这四个 `print` 的输出差异(`value` 是值包、`tuple` 是抽象元组,都是 3 个元素):

```swift
print((repeat each value))            // (1, 2, 3)
print((repeat each tuple))            // (4, 5, 6)
print((repeat (each value, tuple)))   // ((1, (4,5,6)), (2, (4,5,6)), (3, (4,5,6)))
print((repeat (each value, each tuple)))  // ((1,4), (2,5), (3,6))
```

第 3 行与第 4 行的区别就是**有没有 `each`**:不带 `each` 的元组是**整体**参与每一轮,带 `each` 才按位配对。

另外,作为基的元组表达式会在遍历**之前**求值一次(`let tempTuple = <expr>` 再 `repeat each tempTuple`),而不是每轮求值一次。

具体元组、混合元组、带标签的元组都**不**适用这个转换。

## 六、包遍历(SE-0408)

包扩展表达式 `repeat expr` 会把模式**一次性求值 n 遍**,没法中途停下。要短路,过去只能把逻辑包进 throwing 函数再用 do/catch 兜住,很不自然。SE-0408 于是允许:

```swift
for (left, right) in repeat (each lhs, each rhs) {
  guard left == right else { return false }
}
```

三条语义:

1. 第 i 轮迭代时,循环变量的类型是"模式类型里的包被替换成一个带相同约束的**标量**类型参数"后的结果。
2. **模式表达式在每一轮才求值一次**,`break` / `continue` 之后剩下的不再求值。
3. 支持 `for case .one(let v) in ...` 这类模式匹配。

提案给的 `printAndReturn` 例子把第 2 条体现得很清楚(`break` 发生在 i == 1):

```text
Evaluated pack element value 1
Evaluating loop iteration 0
Evaluated pack element value "hello"
Evaluating loop iteration 1
Done iterating
```

三个元素只求了两个 —— 换成 `repeat printAndReturn(each t)` 就会求满三个。

## 七、边界与坑(自检里都有对应用例)

- 内层 `repeat` 捕获的包**不算**外层的;类型里的包扩展同样不穿透。
- 同形状要求只在"尾随 where"与"参数/返回类型"两处推断,局部变量的类型标注不做推断。
- 包被强加具体形状就是 conflict;本提案只有抽象形状。
- 一个泛型类型只能有一个类型参数包。
- `S<T, each U, V>` 的最少实参个数 = 非包形参个数,不是 1。
- `V< >` 与 `V` 语义不同。
- 实存属性的类型自身不能是包扩展类型,必须嵌一层。
- `repeat` 全量求值、`for-in repeat` 惰性求值,短路行为不同。
- 抽象元组要"单一包、无多余元素、无标签";`(each value, tuple)` 与 `(each value, each tuple)` 结果完全不同。

## 八、文件与运行

```text
python/packs_model.py        捕获、形状求解、变长类型绑定、要求推断、遍历、抽象元组
python/selfcheck_packs.py    49 条断言(实跑全绿)
python/main.py               逐项演示
go/packs.go                  Go 侧同题实现
go/main.go                   演示入口
```

```bash
cd python && python selfcheck_packs.py && python main.py
```

Go 侧本机无工具链(`which go` 为空),走人工审查 + `bracket_check` / `go_sanity` / `go_crossref` / `syntax_sanity` 四项静态检查,均通过。

## 九、参考资料(实际读过)

- `apple/swift-evolution@main` · `proposals/0393-parameter-packs.md`(57368 B,包的定义、捕获规则、同形状要求的推断与限制、值参数包的求值语义)— https://github.com/apple/swift-evolution/blob/main/proposals/0393-parameter-packs.md
- `apple/swift-evolution@main` · `proposals/0398-variadic-types.md`(11555 B,变长泛型类型的单包限制、实参绑定、实存属性、要求推断三条规则)— https://github.com/apple/swift-evolution/blob/main/proposals/0398-variadic-types.md
- `apple/swift-evolution@main` · `proposals/0399-tuple-of-value-pack-expansion.md`(7201 B,抽象元组类型与四组 print 输出)— https://github.com/apple/swift-evolution/blob/main/proposals/0399-tuple-of-value-pack-expansion.md
- `apple/swift-evolution@main` · `proposals/0408-pack-iteration.md`(8213 B,`for-in repeat` 的类型替换与惰性求值)— https://github.com/apple/swift-evolution/blob/main/proposals/0408-pack-iteration.md

> 口径说明:本 demo 只建模**类型检查层面的规则**(捕获集、同形状等价类、实参绑定、要求推断)与**求值顺序语义**(惰性 vs 全量),不做真正的类型替换与代码生成;变长泛型 enum、变长类的继承、以及"实存属性包"这些提案明确留作 future directions 的部分都不在范围内。
