# TUF：更新框架的四角色与四类攻击

## 一、简介

TUF（The Update Framework）解决的是**软件更新系统自身的信任问题**：当攻击者能冒充镜像站、
能做中间人、甚至能拿到一把在线签名钥匙时，客户端凭什么相信自己装的是正版？

答案不是"签个名就行"，而是把职责拆成四个角色，每个角色只挡一类攻击：

| 角色 | 谁来签 | 挡什么 |
| --- | --- | --- |
| **root** | 离线钥匙，阈值 ≥2 | 钥匙本身的信任根；钥匙轮换的唯一合法通道 |
| **targets** | 离线/半离线 | 哪些文件是可信的（签元数据，不签文件本身） |
| **snapshot** | 在线 | **混合搭配攻击**：防止把 A 版本的 targets 和 B 版本的 targets 拼在一起喂给客户端 |
| **timestamp** | 在线 | **冻结攻击 / 重放**：用一个带时间戳的短命声明告诉客户端"现在最新的是哪份" |

本 demo 把规范 §5.3 的客户端工作流、§5.6.7 的委派搜索、§6 的一致快照命名转成 Python 与 Go，
并用断言把每一条攻击检查钉死。

## 二、四类攻击与对应的检查点

### 2.1 任意软件攻击（Arbitrary software attack）

**每个 keyid 只能贡献一次签名。** 规范在 root / timestamp / snapshot / targets 四步里
用完全相同的措辞重复了这一点：

> each SIGNATURE which is counted towards the THRESHOLD MUST have a unique KEYID.
> Even if a KEYID is listed more than once in the "signatures" list a client MUST NOT
> count more than one verified SIGNATURE from that KEYID towards the THRESHOLD.

所以 `threshold=2` 时，同一把钥匙签两遍**不算两个**（自检里有专门的用例和它的对照组）。

root 更新还多一条：**必须同时被旧 root 的阈值和新 root 的阈值签名**。这是"信任链连续性"的
实现方式 —— 光有新钥匙的签名，等于谁都能自封为新的信任根。

### 2.2 回滚攻击（Rollback attack）

- **root 版本号必须恰好 +1**（不是 `>= N+1`）。1 → 3 是攻击，因为跳过的那一份可能是合法轮换。
- **timestamp 版本号必须严格递增**；相等是**正常中止**，规范明确说"这不该报错"。
- **snapshot 里登记的每个下游元数据版本都不能倒退**，而且**已经出现过的文件名不能消失**。
- timestamp 里登记的 snapshot 版本不能倒退。

### 2.3 冻结攻击（Freeze attack）

每一步都要查 `expires > fixed_update_start_time`。两个细节：

- **时间在工作流开始时就被"固定"**（§5.1 `Record fixed update start time`）：
  中途不会因为又跑了十分钟而把一份本来有效的元数据判成过期。
- **root 链条里的中间版本不查过期**，只有走到链条末尾才查一次（§5.3.6 明说
  "the expiration of the new (intermediate) root metadata file does not matter yet"）。
  因为客户端是从 N 一路下载 N+1、N+2… 直到拿不到为止，中间的过期是历史，不重要。

### 2.4 混合搭配攻击（Mix-and-match attack）

客户端**先**用 timestamp 里登记的哈希比对 snapshot 文件内容，**然后**才验 snapshot 的签名。
顺序是刻意的：哈希来自已经验证过的 timestamp，比签名更快，能提前把坏数据扔掉（§5.5.2 在 §5.5.3 之前）。

自检里有一个"签名错 + 内容也被换"的用例，断言报的是 `mix-and-match` 而不是
`arbitrary software attack` —— 这就是检查顺序的证据。

## 三、快速前推攻击（Fast-forward attack）的恢复

如果仓库被攻破后恢复，攻击者在被踢出之前可能已经把 timestamp / snapshot 的版本号
**恶意推到很高**。恢复之后，诚实的版本号会比客户端已信任的低，客户端就会永远拒绝更新。

规范的解法（§5.3.11）：**更新 root 时，如果发现 timestamp 或 snapshot 的钥匙集合变了，
就把已信任的这两份元数据删掉**。删掉之后"版本号必须递增"的前提不复存在，客户端重新接受。

自检里用「轮换钥匙 → `rotated=True` 且 `timestamp`/`snapshot` 被清空」和
「不轮换 → `rotated=False` 且保留」这一对用例验证。

## 四、委派：先序深度优先搜索（§5.6.7）

targets 可以把信任**部分**委派给别的角色（按路径或路径哈希前缀）。客户端找某个目标文件时：

1. 先序 DFS，**按委派出现的顺序**处理（顺序即优先级，冲突时先出现者赢）；
2. **访问过的角色直接跳过** —— 委派图可以成环，靠 visited 集合终止；
3. 命中 **terminating** 委派但没找到目标 → **立刻停止**，不再看后面的委派；
4. 角色访问数超过应用设定的上限 → 放弃（防带宽耗尽）。

第 3 条最容易被写反：terminating 的语义是"我说没有就没有，别再问别人"，
自检用「terminating 未命中 → None」与「非 terminating 未命中 → 继续找到 B」成对验证。

`path_hash_prefixes` 与 `paths` 是两种互斥的过滤方式：前者按 `sha256(路径)` 的十六进制
前缀匹配，用来把海量文件切分给多个委派而不暴露文件清单。自检里有一个反直觉用例 ——
声明了哈希前缀的委派**不会**因为路径长得像就命中。

## 五、Consistent snapshots（§6）

仓库在写、客户端在读，会读到半截状态。解法是让每份快照自包含：

- 元数据文件名加版本前缀：`root.json` → `42.root.json`；
- 目标文件名加哈希前缀：`foo.tar.gz` → `<sha256>.foo.tar.gz`，
  **有几个哈希就要有几份拷贝**；
- **timestamp 例外**：它必须同时以不带前缀的 `timestamp.json` 存在，
  否则客户端没有任何已知版本号可以起手；
- 元数据内部引用下游文件时，**仍然写不带前缀的名字**（`snapshot.json` 而不是 `3.snapshot.json`），
  否则每次滚动版本都要让离线钥匙重新签一次。

## 六、代码结构与验证

| 文件 | 内容 |
| --- | --- |
| `python/tuf.py` | 角色/元数据模型、四步工作流、委派搜索、一致快照命名 |
| `python/selfcheck_tuf.py` | 四步工作流的断言 |
| `python/selfcheck_deleg.py` | 委派搜索与一致快照命名的断言（被上一文件调用） |
| 合计 | 61 条断言 |
| `python/main.py` | 演示 |
| `go/tuf.go` `go/walk.go` `go/main.go` | 同构 Go 实现（无 Go 工具链，走人工审查 + 三项静态检查） |

断言设计上刻意做了几组**成对用例**：重复签名 vs 不同签名、terminating vs 非 terminating、
轮换 vs 不轮换、中间 root 过期 vs 末尾 root 过期 —— 单侧通过说明不了检查真的生效。

## 七、参考资料（本轮实际读取）

- TUF 规范正文：`theupdateframework/specification` 的 `tuf-spec.md`
  （§5.1 固定更新起始时间、§5.3 更新 root、§5.4 timestamp、§5.5 snapshot、
  §5.6 targets 与委派搜索、§6 Consistent snapshots、§7 仓库操作）
  - https://github.com/theupdateframework/specification/blob/master/tuf-spec.md
- 规范里引用的快速前推攻击论文：https://theupdateframework.io/papers/prevention-rollback-attacks-atc2017.pdf
