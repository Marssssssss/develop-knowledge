# in-toto：工件规则与供应链布局验证

## 一、简介

签名能证明"这个制品是谁做的"，但证明不了"它是按正确流程、用正确的输入做出来的"。
in-toto 补的是后一半：项目所有者先写一份 **layout**（布局），声明有哪些步骤、
每步允许谁签、允许消费哪些材料、允许产出哪些产物；执行者交付 **link** 元数据
（实际执行的命令、材料与产物的哈希）；客户端最后按 layout 逐条验证整条链。

本 demo 关注的是其中最核心、也最容易写错的部分：**工件规则（Artifact Rules）**。

## 二、七条规则（规范 4.3.3）

| 规则 | 语义 |
| --- | --- |
| `MATCH <pattern> [IN <src>] WITH (MATERIALS\|PRODUCTS) [IN <dst>] FROM <step>` | 本步的工件必须与**指定步骤**的材料/产物**同名且同哈希** |
| `CREATE <pattern>` | 匹配到的产物**不能**同时是本步的材料（必须是新建的） |
| `DELETE <pattern>` | 匹配到的材料**不能**同时是本步的产物（必须被删掉了） |
| `MODIFY <pattern>` | 匹配到的产物**必须**同时是材料，且**哈希变了** |
| `ALLOW <pattern>` | 明确授权 |
| `REQUIRE <name>` | 指定的工件**必须**出现（按名字，不是通配符） |
| `DISALLOW <pattern>` | 匹配到的工件**不允许**出现（未授权） |

### 2.1 规则是"防火墙式"的顺序执行

规范 4.3.3.1 的措辞很关键：

> They operate in a similar fashion as firewall rules do. This means if an artifact is
> successfully consumed by a rule, it is removed from the queue and cannot be consumed by
> subsequent rules.

所以**顺序决定结果**：`ALLOW *` 写在前头会把后面的 `DISALLOW *.c` 彻底架空。
自检里用这一对用例钉死（`ALLOW * + DISALLOW *.c` 通过，反过来报错）。

### 2.2 末尾的隐式 `ALLOW *`

> There is an implicit `"ALLOW *"` at the end of each rule list.

也就是说：**不写 `DISALLOW *` 的规则列表等于放行一切**。规范因此建议每条规则列表都以
`DISALLOW *` 结尾。演示第 4 项量化了这个后果 —— 只写 `MATCH src/main.c ...` 时，
多出来的 `src/backdoor.c` 会被静默放行；加上 `DISALLOW *` 才被拦下。

### 2.3 `MATCH` 不消费时不报错

`MATCH` 找不到对应工件（哈希不同、名字对不上、引用的步骤不存在）时，
按规范伪代码只是**不加入 consumed**，规则本身**不返回错误**。
真正把它变成安全事件的是后面的 `DISALLOW *`。

这条很容易被实现错：如果把"没匹配上"直接当失败，`MATCH` 就退化成了 `REQUIRE`。
自检里专门有「哈希不一致 → 通过但剩余」与「再加 `DISALLOW *` → 报错」的成对用例。

### 2.4 规范正文与语法块不一致：`PRODUCT` vs `PRODUCTS`

4.3.3 的语法块写的是 `WITH (MATERIALS|PRODUCTS)`，而同节的示例写的是：

```
MATCH foo IN lib WITH PRODUCT IN build/lib FROM compilation
```

单复数两种形态在规范里同时存在。本 demo 的解析器**两种都接受**，统一归一成复数，
并在自检里各留一条用例。

### 2.5 `IN` 子句：路径重定位

步骤之间经常搬文件。上面的示例保证的是 `lib/foo` 与 `compilation` 步的 `build/lib/foo` 对应：
先按前缀过滤，再把前缀从名字上**摘掉**，然后按摘掉后的名字比对（名字 + 哈希都要一致）。

## 三、阈值、命令与签名

- **签名**：link 的签名者必须在 step 的 `pubkeys` 里。
- **阈值**（4.3.1）：`threshold` 表示要有多少份 link 才算数，用于"多个执行者独立跑同一操作"。
  规范原话是这些执行者必须 "perform the operation and **report the same results**"，
  所以本 demo 在 `threshold > 1` 时额外要求各份 link 的 materials/products **完全一致**。
- **`expected_command` 只告警，不判失败**（4.3.1）：规范给了两条理由 —— 函数可以通过改
  `PATH` 伪造命令，而合规的执行者也可能加 `--color=full` 之类的个人偏好参数。
  所以命令不符时客户端"should only show a warning"。

## 四、Sublayout 的"虚拟 link"（4.5.1）

项目所有者往往没法把第三方库的内部构建步骤写细。这时第三方可以自己写一份子布局
（**sublayout**），存成 `[name].[keyid-prefix].link`，签名仍是上层期望的那把钥匙。

关键机制（规范 4.5.1）：

> the verification algorithm will recurse into Bob's layout, perform verification and
> present a "virtual" piece of link metadata that can be used to verify Alice's layout.
> The materials of this virtual piece of link metadata will be those of the first step
> on Bob's sublayout, and the products will be those on the last step.

也就是说**对外只暴露首步的材料与末步的产物**，中间过程对外层不可见。
本 demo 用一条 `fetch -> compile` 子布局验证了这一点：外层如果按中间产物写 `MATCH src ...`，
会因为虚拟 link 只暴露 `seed` 而匹配不上（配合 `DISALLOW *` 才报错）；
改成 `MATCH seed ...` 后通过。子布局内部的失败也会带前缀向上传播。

另外规范 4.5.2 要求子布局的 link 放进同名目录（去掉 `.link` 后缀）以避免步骤名冲突。

## 五、link 文件名（4.4）

`[name].[KEYID-PREFIX].link`，其中 KEYID-PREFIX 是 **keyid 的前六个字节**。
keyid 在实现里是十六进制串，六个字节 = **12 个十六进制字符**。
后缀的作用是在阈值大于 1 时避免多份 link 互相覆盖。

## 六、代码结构与验证

| 文件 | 内容 |
| --- | --- |
| `python/rules.py` | 规则解析 + 规则引擎（顺序执行、消费语义） |
| `python/intoto.py` | Link / Step / Inspection / Layout、签名与阈值、sublayout 递归 |
| `python/selfcheck_intoto.py` | 56 条断言 |
| `python/main.py` | 演示 |
| `go/rules.go` `go/intoto.go` `go/main.go` | 同构 Go 实现（无 Go 工具链，走人工审查 + 三项静态检查） |

断言刻意做成对的：单复数形态、MATCH 匹配与否、规则顺序正反、阈值一致与否、
`expected_command` 命中与不命中 —— 单边通过说明不了检查真的生效。

## 七、参考资料（本轮实际读取）

- in-toto 规范：`in-toto/specification` 的 `in-toto-spec.md`
  - https://github.com/in-toto/specification/blob/master/in-toto-spec.md
  - §4.3 layout 格式、§4.3.3 工件规则与规则处理、§4.4 link 格式与文件名、
    §4.5 sublayout（含 4.5.1 虚拟 link、4.5.2 命名空间）、§5.2 最终产品验证
