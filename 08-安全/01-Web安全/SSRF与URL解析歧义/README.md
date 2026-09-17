# SSRF 防护与 URL 解析歧义

> SSRF 的防线很少败在"忘了黑名单某个 IP",而是败在**同一个字符串被两个解析器读成不同主机**,
> 或者"校验完 host 之后又跟随了 302"。本 demo 把这两处连同 IPv4 的十几种写法一起做成可断言的实现。

## 简介

`http://127.0.0.1/` 这一个地址至少有以下等价写法,而**它们都是合法 URL**:

```text
127.0.0.1        2130706433      0x7f000001      0x7f.1
0177.0.0.1       0x7f.0x0.0x0.0x1    127.1       127.0.0.1.(尾点)
```

任何"把 host 当字符串比对黑名单"的实现都会漏。真正可靠的姿势是:**按 URL 规范把主机名规范化成
唯一的 IP 表示,再对被规范化后的地址做判定**;规范化失败就拒绝。本 demo 复刻了 WHATWG URL
Standard 的三个算法(IPv4 number parser / IPv4 parser / ends-in-a-number checker),并用
node 的**原生 URL 实现**逐条对照。

## 原理详解

**1. IPv4 number parser(§3.5):一个"数字"可以有三进制**

```text
"0x" / "0X" 前缀 → 十六进制(并报 validation error)
前导 "0"(且长度 ≥2) → 八进制(并报 validation error)
否则 → 十进制
"0x" 或 "0" 自身 → 值 0
含非该进制的字符 → failure
```

`0177` = 127(八进制)、`0x7f` = 127、`2130706433` = 0x7F000001。**validation error 不等于失败** —— 浏览器照收,只是记一笔。

**2. IPv4 parser(§3.5):最后一段可以"吃掉"剩余字节**

```text
按 "." 拆段;尾点去掉;段数 >4 → failure
前 n-1 段必须 ≤255
最后一段必须 < 256^(5-段数)      # 1 段 ≤2^32-1、2 段 ≤2^24-1、3 段 ≤2^16-1、4 段 ≤255
最后一段填充到高位字节,前面各段依次左移 8 位
```

所以 `127.1` = `127.0.0.1`,`2130706433` 单段也是 `127.0.0.1`。**"段数越少,最后一段能表示的数值越大"** 是这套规则的全部玄机。

**3. ends-in-a-number checker(§3.5):决定"这算不算 IP 写法"**

最后一段是纯 ASCII 数字、或能当 IPv4 number 解析(即 `0x` 后跟若干十六进制位)—— 就按 IPv4 解析,解析失败则**整个 URL 非法**(而不是"当作域名")。这就是 `http://1.2.3.4.5/` 打不开的原因;反过来,`dead.beef` 仍是合法域名(它的最后一段 `beef` 不是十进制数,也不是 `0x` 开头)。

**4. 解析器分歧(OWASP 明示的例子)**

```text
http://example.com\@evil.com
  WHATWG(浏览器/node):反斜杠在特殊 scheme 下是路径分隔符 → host = example.com
  RFC 3986:userinfo 不允许反斜杠 → 该串不是合法 URI
  CPython urllib.parse:按最后一个 '@' 切 userinfo → hostname = evil.com
```

三种读法,两个不同答案。**正确做法是"只要 host 不能被一致读出就拒绝"**,而不是挑一个"更严格"的读法 —— 因为你挑的那个未必是后端真正用来发请求的那个。

**5. 校验之后还有两步**

- **禁止跟随重定向**:请求转发到已校验的 host 之后,一个 302 就能把流量带去 `169.254.169.254`。OWASP 的建议是**在 HTTP 客户端上禁用重定向**,收到 3xx 即当作失败。
- **DNS pinning**:域名合法但 A 记录指向内网。要求校验**全部 A + AAAA 记录**,并优先用内部 DNS 解析自有域名。

## 对比:两种场景、两种策略

| | Case 1(目标已知) | Case 2(目标任意,如 Webhook) |
| --- | --- | --- |
| 主策略 | 允许列表(精确比对) | 阻止列表 + 逐个解析结果校验 |
| host 校验 | 比对 parser 输出值 | 格式合法性 + 非公网判定 |
| 请求构造 | **只用允许列表里的那条重建**,不复制用户 URL 的 path/query | 仅用已验证信息构造 POST |
| 附加 | 解析器分歧即拒绝 | 协议允许列表、token 参数名/值字符集、禁重定向 |
| 兜底 | 网络分段 / 防火墙 | 同左 |

被 OWASP 归为"最后手段"的黑名单条目:`169.254.169.254`(AWS/GCP/Azure 元数据)、`metadata.google.internal`、`127.0.0.0/8`、`0.0.0.0/8`、`::1/128`、RFC1918 三段、`224.0.0.0/4`、`ff00::/8`。云上还应**迁移到 IMDSv2 并关闭 IMDSv1**。

## 环境

- Python 3.9+(标准库 `ipaddress`/`urllib.parse`)
- Node.js 18+(原生 `URL`,无需第三方库)

## 运行方式

```bash
python ssrf_check.py     # Python 侧 74 项断言(解析算法 + 防护策略)
node   ssrf_guard.mjs    # JavaScript 侧 29 项断言:用原生 WHATWG URL 给 Python 复刻当标尺
```

## 关键代码

```python
# url_ipv4.py:最后一段"吃掉"剩余字节
if numbers[-1] >= 256 ** (5 - len(numbers)):
    return None
value = numbers[-1]
for counter, n in enumerate(numbers[:-1]):
    value += n * 256 ** (3 - counter)

# ssrf_guard.py:解析器分歧直接拒绝,不做调和
whatwg = whatwg_authority_host(url)         # http://example.com\@evil.com → example.com
py = urlsplit(url).hostname                 # → evil.com
if whatwg != py:
    raise SsrfBlocked(f"解析器分歧:{whatwg} vs {py}")
```

## 性能边界

- 纯字符串 + 整数运算,单次判定微秒级;DNS 解析是唯一的外部依赖,本 demo 用注入式 resolver 保持确定性(不真发 DNS 查询)。
- `is_public()` 先做内嵌 v4 解包(IPv4-mapped / NAT64 / 6to4)再判 `is_global`,并叠加 OWASP 明示的网段 —— **任何一步缺失都会漏掉一类绕过**。
- 本 demo 用 `ipaddress` 处理 IPv6(未复刻规范的 IPv6 parser),且不做 IDNA/ToASCII;已在代码注释标注。

## 注意事项与常见坑

- **`0177.0.0.1`、`0x7f.1`、`2130706433` 全是 `127.0.0.1`**,但 `256.1.1.1`、`1.2.3.4.5`、`1.2.3.65536` 是**非法 URL**(浏览器地址栏直接报错,攻击者用不了)。
- **IPv6 形态会被"重新序列化"**:`http://[::ffff:127.0.0.1]/` 在浏览器里 hostname 变成 `[::ffff:7f00:1]`。拿 `"127.0.0.1"` 做字符串黑名单的实现必然漏 —— 必须先解包成 v4 再判。
- **CPython 的 `IPv6Address.__str__` 保留点分四段写法**(`::ffff:127.0.0.1`),而 WHATWG 的序列化器输出纯十六进制(`[::ffff:7f00:1]`)。两边的"规范化结果字符串"不一致是**正常**的,判定必须走"解包 + 地址比较",不能比字符串。
- **主机名会先降为小写再比对**(WHATWG host parser):OWASP 说的"大小写敏感比对"指的是**比 parser 的输出**,不是比用户原始输入。而尾点 FQDN(`api.example.com.`)不会被去掉,因此会与允许列表失配 —— 这是 fail-closed 的,不用担心,但要意识到"同一个主机有多个字符串形态"。
- **`ends-in-a-number` 判定失败会让整个 URL 非法**,而不是回落成域名解析。写正则做 host 校验时如果只允许"看起来像域名"的形式,反而会把合法的纯数字主机名(`http://12345/`)拒掉 —— 这类域名在实际内网里存在。
- **别忘 `gopher://`/`file://` 这类 scheme**:SSRF 的利用不只要读元数据,`gopher` 打 Redis/Memcached 是很老的套路。协议必须走允许列表(仅 http/https)。
- **IMDSv2 只是纵深防御**:OWASP 的措辞是"可缓解**部分** SSRF 实例",它不替代上面的校验。

## 参考资料(实际联网读过)

- OWASP *Server Side Request Forgery Prevention Cheat Sheet* — <https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html>(Case 1/Case 2 策略、绕过技术表、解析器分歧例 `http://example.com\@evil.com`、DNS pinning、禁重定向、云元数据与 IMDSv2、阻止列表条目)
- WHATWG URL Standard — <https://url.spec.whatwg.org/>(§3.5 IPv4 number parser / IPv4 parser / ends in a number checker / IPv6 parser、§4.4 host parser 的 IPv4 分支)
