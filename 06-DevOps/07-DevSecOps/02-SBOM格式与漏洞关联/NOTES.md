# NOTES — demo572 口径说明与踩坑

## 一、口径说明（官方未给定量处，本 demo 选的读法）

1. **vers 语法只实现子集**：CycloneDX 只在 schema 里说 `range` 采用 vers 语法并给出几个例子
   （`vers:cargo/9.0.14`、`vers:npm/1.2.3|>=2.0.0|<5.0.0`），**没有给出求值算法**。
   本 demo 实现 `>=` `>` `<=` `<` `!=` `==` 与裸版本六种约束、多约束取**合取**，
   未支持的写法抛 `ValueError` —— 不静默放行。
2. **VEX 判定的胜负规则**：schema 只定义了 `status` 的缺省值与枚举，没定义多个条目冲突时谁赢。
   本 demo 取「按 `vulnerabilities` 顺序扫描，首个匹配胜出」，并在断言 V4 里把顺序敏感性显式钉住。
3. **qualifier 排序的是整个 `key=value` 串**：how-to-build 写的是
   "Sort this list of qualifier strings lexicographically"，排的是拼好的串而不是 key。

## 二、开发期修掉的问题

1. 自检消息里写 `"空格应编码为 %20, 实际 %s" % ...` 会触发
   `ValueError: unsupported format character ','` —— `%2` 被当成格式占位符。
   含百分号的消息一律改成字符串拼接。
2. `parse_purl` 最初用 `text.split("@")` 取版本，遇到 name 里含编码后的 `@` 会切错；
   改成只在**最后一个** `@` 处切分（`LastIndex` / `rsplit`）。

## 三、容易写反的断言

- **M1b 是负控**：只断言 `CmpVersion("1.10","1.9") == 1` 不够，必须同时断言
  `("1.10" < "1.9") is True`，否则「版本比较」这条性质恒真（任何比较方式都可能给出 1）。
- **X3 有环闭包**：不写这条，`dep_closure` 里的 `seen` 是否有用就测不出来。
- **V9/V9b 成对**：只测「合法 component 通过」是伪断言，必须同时测「缺 type 被拦下」。
