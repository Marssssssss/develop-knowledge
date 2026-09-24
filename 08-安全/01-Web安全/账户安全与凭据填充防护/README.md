# 账户安全：口令策略、黑名单与凭据填充防护

凭据填充（credential stuffing）用的是**从别处泄露的口令**，不是猜出来的口令。因此
「要求大小写数字符号混用」「每 90 天强制改密」这类传统策略几乎不产生收益：它们挡不住
真口令，只会把用户推向 `P@ssw0rd1` 这种可预测的变形。NIST SP 800-63B 的结论是反直觉的。

## 一、长度门槛（3.1.1.2）

| 场景 | 最短 |
| --- | --- |
| 单因素口令 | **15** 字符（SHALL） |
| 仅用于多因素流程的口令 | MAY 更短，但 **8** 字符（SHALL） |
| 最长 | SHOULD 允许至少 **64** 字符 |

长度是唯一被 SHALL 强制的复杂度维度。注意「MFA 下可短到 8」的前提是**这个口令只在
多因素流程里用**——单因素认证入口仍然要走 15。

## 二、四个 SHALL NOT（常见反模式）

1. **不得施加其他组成规则**（大小写/数字/符号混用）。纯小写 15 位、纯数字 15 位、
   甚至 15 个空格都必须被接受。本 demo 断言了这三种都通过。
2. **不得要求定期改密**。只有在**有失陷证据**时才 SHALL 强制改。
3. **不得存储可被未认证索取方读取的提示（hint）**。
4. **不得使用知识型认证（KBA）/ 安全问题**（`你第一只宠物叫什么` 之类）。

配套的 SHALL：

- 请求**完整**口令（不是"请输入第 3、5、7 位"），并校验**完整**提交的口令，**不得截断**。
  本 demo 断言 70 字符口令少一个字符即失败——如果实现截到 64 再比，这条会挂。
- SHOULD 接受全部可打印 ASCII 与空格；SHOULD 接受 Unicode。
- SHALL 允许密码管理器与自动填充；SHOULD 允许粘贴（禁用粘贴会逼用户选更弱的口令）。

## 三、Unicode：码点计数 + NFC

> Each Unicode code point SHALL be counted as a single character when evaluating
> password length.

长度按**码点**算，不是字节、不是 UTF-16 单元。所以 15 个中文字符＝15，15 个 emoji＝15；
而按 UTF-8 字节算的话 15 个中文是 45，会把「够长」误判成「超长」，反过来按 UTF-16
单元算又会让 emoji 被算成 2。

若接受 Unicode，**应在哈希前做 NFC 归一化**。否则同一串口令的两种写法
（`é` 合成式 U+00E9 与 `e` + U+0301 分解式）会得到两个不同哈希，用户在另一台
设备上就登不进去了。本 demo 断言两者归一后相等。

## 四、黑名单：整串比对，不是子串

> The entire password SHALL be subject to comparison, not substrings or words that
> might be contained therein.

黑名单来源至少三类：

1. 既往泄露语料（breach corpuses）
2. 词典词
3. **上下文相关词**：服务名、用户名及其派生

命中黑名单时 SHALL 要求另选，并且 **SHALL 给出拒绝理由**（否则用户只会做最小变形）。

「整串比对」是本 demo 重点断言的一条：黑名单里有 `password` 时，`mypassword1`
**不会**因为包含 `password` 而被拦——它只有在被语料本身收录时才被拦。这条经常被实现成
子串匹配，结果是把大量正常口令误杀。

另有一条容易被忽略的配套说明：黑名单是在**限流已经限制在线攻击**的前提下防御暴力破解的，
所以语料规模大到一定程度后边际收益很小。

## 五、限流：连续失败上限 100（3.2.2）

> The limit of 100 attempts is an upper bound, and agencies MAY impose lower limits.

要点：

- 限制的是**连续**失败次数，一次成功后计数归零。
- 计数是**按账户**的，不是全局的——本 demo 断言 u1 被锁时 u2 仍可登录。
- 100 是**上限**而非推荐值；实际部署通常低得多。
- 达到上限后，**正确口令也不放行**（本 demo 断言了这一点），必须走带外解锁流程。

## 六、其他认证器的门槛（实读）

| 认证器 | 门槛 |
| --- | --- |
| look-up secret | SHALL 至少 **6 位**十进制数字 |
| 带外（out-of-band）secret | SHALL 在 **10 分钟**内完成否则无效；同一 secret 有效期内**只接受一次** |
| 带外推送通知 | SHOULD 限制自上次成功认证以来的推送速率/总数 |
| 激活密钥（activation secret） | SHALL 至少 4 字符（SHOULD ≥6）；连续失败重试 SHALL 不超过 **10** |

「单次有效」这条是重放防护：本 demo 断言同一个 secret 第二次使用被拒，且**错误尝试不消耗
额度**（只有成功才置位）。

## 七、存储

> Passwords SHALL be salted and hashed using a suitable password hashing scheme.

口令哈希方案的三输入是**口令、盐、成本因子**。本 demo 用 sha256 做**占位**（只为演示
「不同用户同口令得到不同摘要」），真实实现必须用内存困难函数（Argon2id / scrypt / bcrypt），
sha256 这类快哈希不满足「抗离线攻击」的要求。

## 代码结构

| 文件 | 内容 |
| --- | --- |
| `accmgr.py` | 长度门槛、NFC、黑名单、限流、look-up/OOB/激活密钥 |
| `accmgr.go` | 同模型的 Go 转写（NFC 用最小组合表） |
| `selfcheck_accmgr.py` | Python 自检（实跑 **71** 断言） |
| `selfcheck_accmgr.go` | Go 自检（同套断言） |

**语言差异（已显式落地）**：Go 标准库没有 NFC，`accmgr.go` 用一张覆盖 Latin-1 Supplement
的最小规范组合表来演示「分解式与合成式归一到同值」；Python 直接用 `unicodedata.normalize`。
完整实现应改用 `golang.org/x/text/unicode/norm`。

## 参考资料（实际读过）

- NIST SP 800-63B《Digital Identity Guidelines — Authentication and Authenticator
  Management》— `https://pages.nist.gov/800-63-4/sp800-63b.html`（353757 B 全文实读）
  §3.1.1.2 Password Verifiers（长度/组成/Unicode/NFC/黑名单/限流/存储）、
  §3.1.2.1 look-up secrets、§3.1.3 Out-of-Band Devices（10 分钟与单次有效）、
  §3.2.2 Rate Limiting (Throttling)（上限 100）、激活密钥重试上限 10
