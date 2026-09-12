# STATE.md — 自动化巡检主状态文件

> 每轮结束由自动化任务追加/滚动。**永远 ≤ 5 KB,不再随 demo 数线性增长**。
> 历史全本见 `_docs/archive/`(默认不进 hot path,按需 Grep)。

> **调度频率**:每 1.5 小时一次(全天 24h,**无下午屏蔽**),由两个错开 1.5 小时的 `HOURLY;INTERVAL=3` 任务合成;全天 16 个触发点(00:00/01:30/03:00/.../22:30)。详见 [`SCHEDULE_QUOTA.md`](./SCHEDULE_QUOTA.md) 顶部说明。
> **每轮配额**:5 主 + ≤2 副;硬约束 ≤5 agentic_search / ≤15 WebSearch / ≤10 WebFetch / ≤5 新 demo。

> 推进配额与失败回退:见 [`SCHEDULE_QUOTA.md`](./SCHEDULE_QUOTA.md)
> Token 控制细节:见 [`OPTIMIZATION.md` §2.4](./OPTIMIZATION.md)

## 一、轮询顺序

按 `priority_score = base_score - done_count * 10 - last_done_days * 0.5`,每轮取最高分。

```text
轮询索引表(next_index 控制下次起点):
0  : 01-游戏开发/服务端
1  : 01-游戏开发/渲染
2  : 01-游戏开发/UI
3  : 01-游戏开发/游戏引擎
4  : 01-游戏开发/物理
5  : 01-游戏开发/AI
6  : 01-游戏开发/音频
7  : 01-游戏开发/动画
8  : 02-Web开发/前端框架
9  : 02-Web开发/后端
10 : 02-Web开发/数据库
11 : 02-Web开发/API设计
12 : 03-系统编程/网络编程
13 : 03-系统编程/进程线程协程
14 : 03-系统编程/内存管理
15 : 03-系统编程/文件系统
16 : 04-移动开发/iOS
17 : 04-移动开发/Android
18 : 04-移动开发/跨平台
19 : 05-AI与机器学习/深度学习
20 : 05-AI与机器学习/强化学习
21 : 05-AI与机器学习/LLM
22 : 05-AI与机器学习/计算机视觉
23 : 06-DevOps/容器化
24 : 06-DevOps/CI-CD
25 : 06-DevOps/监控
26 : 06-DevOps/Kubernetes
27 : 07-数据存储/关系型
28 : 07-数据存储/NoSQL
29 : 07-数据存储/缓存
30 : 07-数据存储/搜索引擎
31 : 08-安全/密码学
32 : 08-安全/Web安全
33 : 08-安全/网络安全
34 : 09-语言学习/Python     # 按语言组织,与按领域的 01-08 不同
35 : 10-逆向工程/二进制逆向   # 2026-09-12 用户指定新增顶层
36 : 10-逆向工程/移动端逆向
37 : 11-性能分析/系统级剖析     # 2026-09-12 新增顶层(Agent 判断放置)
38 : 11-性能分析/应用级剖析
39 : 05-AI与机器学习/经典机器学习   # 2026-09-12 巡检类目拓展新增(S2)
40 : 05-AI与机器学习/语音与多模态   # 2026-09-12 巡检类目拓展新增(S2)
41 : 06-DevOps/IaC与配置管理       # 2026-09-12 巡检类目拓展新增(S2)
42 : 06-DevOps/SRE与可靠性工程     # 2026-09-12 巡检类目拓展新增(S2)
43 : 06-DevOps/DevSecOps           # 2026-09-12 巡检类目拓展新增(S2,07-DevSecOps 目录)
44 : 09-语言学习/Golang             # 2026-09-12 巡检类目拓展新增(S2)
45 : 07-数据存储/图数据库             # 2026-09-12 巡检类目拓展新增(S2,Neo4j/Cypher/属性图占位)
```

> **索引表可动态增长**（2026-09-12 起）：巡检副任务自动拓展子类目时，在表末尾顺延编号追加（35、36…），`next_index` 取模基数以实际行数为准。类目拓展规则见 `AGENT_RULES.md` §一.5。

> **大类轮换约束（2026-09-12 用户新增，硬约束）**：每轮所选索引的**顶层大类**（路径 `NN-` 前缀，如 `08-安全`）**必须 ≠ 上一轮大类**。上轮大类优先看第二节 `last_top` 字段；字段缺失时以第三节滚动窗口最近一行索引的顶层前缀为准。若 `next_index` 指向的条目与上轮同大类 → **顺延到下一个不同大类的索引**执行本轮，结束时 `next_index` 直接推进到选中索引 + 1（被跳过的条目下轮自然轮到，**不记 skip、不算失败**）。

## 二、本轮状态

```
next_index     : 36    # 下次从索引 36 开始(即 10-逆向工程/移动端逆向);索引表现 46 行,取模基数 46
last_run       : 2026-09-13 06:43   # 任务 B 开局占位锁
last_top       : 10-逆向工程   # 本轮顶层大类(大类轮换约束用,见 §一)
skipped        : []
failed_attempts: []
```

## 三、最近 5 轮(滚动窗口)

| 时间 | 索引 | 主题 | demo |
| --- | --- | --- | --- |
| 2026-09-13 03:28 | 35 | ELF 文件解析(elf(5) Ehdr 64B 偏移表+节头/程序头双目录+sh_name 字符串表间接寻址+strip 判定)+ PE 文件解析(MS PE Format 官方规范:e_lfanew 枢纽+PE32/PE32+ 三差异+节表 40B+RVA→文件偏移换算+导入表 ILT/IAT 双表)+ x64 System V 调用约定(psABI Figure 3.4:INTEGER rdi-r9/SSE xmm0-7 并行计数+右到左压栈+16B 栈对齐+callee-saved+汇编猜签名)+ GOT/PLT 延迟绑定(Taylor Linkers part 4:PLT 三段式+GOT 槽初始指回第二条指令+首次调用 5 步解析+JMP_SLOT vs GLOB_DAT+GOT 劫持与 RELRO)+ YARA 静态特征匹配(官方文档:nocase/wide/xor/fullword 修饰符+hex 半字节通配/跳变+#+@/at/in/of 条件量词+引擎三层架构)五 demo | +5 |
| 2026-09-13 00:29 | 34 | 生成器 yield(§6.2.10 挂起/恢复 + send/throw/close + PEP 380 yield from 委派 + PEP 525 async 生成器)+上下文管理器(PEP 343 __enter__/__exit__ + __exit__ 返 True 抑制 + @contextmanager 生成器实现 + ExitStack 动态组合 + suppress/closing/nullcontext + @asynccontextmanager)+描述符协议(HowTo §__get__/__set__/__delete__ + 数据描述符优先级 > 实例字典 > 非数据 + property/staticmethod/classmethod 的 __get__ 实现 + Validator/ORM Field + PEP 487 __set_name__)+协程与 asyncio(async def + 三 awaitable coroutine/Task/Future + gather/TaskGroup/wait/as_completed 对比 + asyncio.timeout + to_thread)+元类与 __init_subclass__(type 三参数 + PEP 3115 metaclass= 关键字 + __prepare__/__new__/__init__ 三钩子 + PEP 487 __init_subclass__ 隐式 classmethod 插件注册 + 元类 vs __init_subclass__ vs 类装饰器取舍) 五 demo | +5 |
| 2026-09-12 21:39 | 33 | TLS 1.3 1-RTT 握手(X25519 ECDHE+HKDF-Expand-Label 派生 handshake_secret/traffic_secret,AES-128-GCM 加密 EncryptedExtensions/Cert/CertVerify/Finished,CertificateVerify 防 downgrade)+ SYN Cookie 防 SYN Flood(32bit ISN = t 5bit|mss 3bit|HMAC-SHA1 24bit,不分配 TCB 至 ACK 校验后才分配)+ eBPF/XDP 包过滤(SEC("xdp") 早期 hook 26Mpps 单核,XDP_DROP/PASS/TX/REDIRECT 5 action,BPF_MAP_TYPE_HASH blacklist)+ IKEv2-ESP 协商(RFC 7296 2 轮 4 消息,SKEYSEED→prf+ 派生 7 把密钥,SK_d→Child SA keymat,ESP 隧道 SPI/SeqNo/IV/ICV+anti-replay+MOBIKE)+ DNSSEC 链式信任(RFC 4033 Chain of Trust `DNSKEY->[DS->DNSKEY]*->RRset`,4 RR 类型,Secure/Insecure/Bogus/Indeterminate,KSK vs ZSK 分层,DS=SHA-256(KSK 公钥 wire),Ed25519 Test 2 命中) 五 demo | +5 |
| 2026-09-12 18:36 | 32 | XSS 三类型(Reflected/Stored/DOM)+HTML Entity 编码+CSP nonce+黑名单失败案例 + CSRF Synchronizer Token+Signed Double Submit+SameSite Lax/Strict/None+Fetch Metadata cross-site 拒绝+Origin 精确校验 + SQL 注入字符串拼接 vs ? 占位符+UNION 阻断+时间盲注+表名白名单+Second-order+Least Privilege 视图 + Same-Origin 三元组+CORS Simple vs Preflight+凭据请求 ACAO=* 硬约束+Vary: Origin 防 CDN 串味 + JWT HS256 签发验签+RFC 7515 Appendix A.1 向量验证(JWK 64 字节 key 命中)+Algorithm Confusion 防御+alg:none/篡改/错 secret 检测 五 demo | +5 |
| 2026-09-12 14:30 | 31 | AES-GCM 认证加密(NIST SP 800-38D §7.1/§7.2,J0 = IV || 0x00000001,GHASH over GF(2^128) x^128+x^7+x^2+x+1 0xe1<<120,GCTR CTR + AAD/lenA/lenC 长度块,GCM tag 16B)+ RSA-PSS 概率签名(RFC 8017 §8.1 + §9.1 EMSA-PSS-ENCODE M' = 8B零 || mHash || salt,MGF1(H, emLen-hLen-1) 掩码,EM = maskedDB || H || 0xbc,salt = hLen 32B SHA-256,RFC 8017 §10 probabilistic salt 防 Bleichenbacher 攻击)+ Ed25519 签名(RFC 8032 扭曲爱德华兹 -x²+y² = 1+d·x²y² a=-1,d=-121665/121666,基点 B_y = 4/5 mod p B_x 由 y 恢复,§5.1.5 KeyGen SHA-512(seed)[:32] clamp + mod L + [a]B,§5.1.6 Sign r = SHA-512(prefix || M) mod L 确定性,R = [r]B,s = (r + SHA-512(R || A || M)·a) mod L,§5.1.7 Verify k = SHA-512(R || A || M) mod L,[s]B = R + [k]A 简化公式完整 [8][s]B = [8]R + [8][k]A cofactor 防 small-subgroup 攻击)三 demo | +3 |

> 超出 5 轮的细节见 `_docs/archive/schedule.md`(完整日志)+ `git log -p`(历史回溯)。

## 四、归档与 rotate 规则

| 文件 | 内容 | rotate 触发 | 截断阈值 | 超出处理 |
| --- | --- | --- | --- | --- |
| `_docs/archive/completed.md` | 已完成 demo 全本 | 行数 > 100 或 大小 > 50 KB | 保留最近 100 行 | 直接丢弃(查 `git log`) |
| `_docs/archive/schedule.md` | 调度日志全本 | 行数 > 30 或 大小 > 20 KB | 保留最近 30 条 | 直接丢弃(查 `git log`) |

- rotate 作为**副任务**(修订/补全/索引 三选一)执行,不消耗主任务配额
- 截断部分**直接丢弃**,不另存冷归档 — 历史回溯靠 `git log -p _docs/archive/`
- 写入新数据时:本文件**只动第三节**(滚动窗口),archive 文件 append 即可

## 五、查找路径

| 想知道什么 | 看哪里 |
| --- | --- |
| 下次从哪个领域开始 | 第二节"本轮状态" |
| 最近 5 轮做了什么 | 第三节"滚动窗口" |
| 某 demo 是否做过 | Grep `_docs/archive/completed.md` `001-999`,或 `git log --all --oneline -- <demo-path>` |
| 上轮日志详细(权威资料/坑/代码量等) | Grep `_docs/archive/schedule.md`,或 `git log -p _docs/archive/` |
| 历史 demo 列表的总数 | 数 completed.md 行数(接近 100 时该 rotate) |

---

> 本文件由 `automation_update` 注册的巡检任务每轮末尾追加;人工编辑直接 Edit 即可,大改请同步更新 `_docs/OPTIMIZATION.md §2.4`。
