# 读者-写者锁:并发读、独占写与两种偏好策略

## 简介

读写锁允许**任意多个读者同时持锁**或**单个写者独占**——读多写少场景下比互斥锁
吞吐高得多。但"谁在等、让谁先拿"是策略问题:**读者偏好**在读流不断时饿死写者,
**写者偏好**让后来的读者排在已阻塞写者后面。本 demo:

- **C**:`pthread_rwlock` 并发读重叠 + `tryrdlock`/`trywrlock` 的 `EBUSY` + 递归读锁
- **Python**:同一把 `Condition` 实现读者偏好/写者偏好两种锁,确定性编排验证
  策略差异(谁先拿到锁),并跑真实线程不变量(读者可重叠、写者独占)
- **Go**:`sync.RWMutex` + `TryRLock`/`TryLock`,对照官方文档的写者偏好语义

关键概念:

- **读者并发 / 写者独占**:读者间可重叠,写者与任何人互斥。
- **偏好(preference)**:有写者排队时,新读者的申请放行还是排队——POSIX **留给
  实现自定义**。
- **递归读锁**:同一线程可多次 `rdlock`(POSIX 明文允许),必须解锁相同次数。

## 原理详解

### 1. POSIX 对放行条件的规定(Open Group 规范原文语义)

`pthread_rwlock_rdlock` 的放行规则分两档:

- 支持 TPS(线程执行调度选项)且线程跑在 `SCHED_FIFO`/`SCHED_RR` 下:仅当
  **没有写者持锁、且没有优先级不低于自己的写者在排队**时才放行读者;
- 不支持 TPS 时:**"写者未持锁但有写者排队时,读者是否放行由实现自定义"**
  ——这正是"读者/写者偏好"的策略自由度所在。

其他硬性规定(规范原文):

- 写者持锁时读者必然阻塞;
- 调用线程**已持有写锁**再 rdlock:**可能死锁**(错误码 `EDEADLK`,仅"may fail");
- 同一线程可持有**多个并发读锁**,必须解锁相同次数;同时读锁总数上限
  **由实现定义**,超出时 rdlock "may fail" 并返回 `EAGAIN`;
- `tryrdlock` 拿不到锁**永不阻塞**,失败返回 `EBUSY`;
- 这些函数不返回 `EINTR`。

### 2. 两种偏好策略(Python 实现的两种锁)

```text
读者偏好 ReadersPref          写者偏好 WritersPref
rdlock:                       rdlock:
  while (writer) wait();        while (writer || waiting_writers) wait();
  readers++;                    readers++;
wrlock:                       wrlock:
  while (writer || readers)     waiting_writers++;
         wait();                while (writer || readers) wait();
                                waiting_writers--; writer = true;
```

- 读者偏好:读流不断 → `readers` 永不为 0 → **写者饿死**;
- 写者偏好:排队写者挡住一切新读者 → 写者最多等完当前读者即可进入;
  代价是读者吞吐在写压力大时下降。

### 3. Go 的 sync.RWMutex:写者偏好(官方文档明文)

go.dev/pkg/sync 原文:**只要有一个 goroutine 调用了 Lock(且有读者持锁),后续
并发的 RLock 调用就会阻塞,直到该写者获得并释放锁**——这保证写者最终能拿到锁,
同时**禁止了递归读锁**(持读锁的 goroutine 再 RLock 可能与自己的等待互锁)。
另规定:RLock 不能升级为 Lock,Lock 不能降级为 RLock;happens-before 边与
Mutex 同构(第 n 次 Unlock happens-before 第 m 次 Lock,n < m)。

### 4. 并发正确性不变量(三份实现共同验证)

| 不变量 | 含义 |
| --- | --- |
| 写者段内 `readers == 0` | 写者独占,无读者残留 |
| 写者段内 `writers == 1` | 写者间互斥 |
| 读者重叠计数 `max_concurrent >= 2` | 读者确实并发持锁(不是排队串行) |

## 对比 / 选型

| 维度 | 互斥锁 | 读写锁 |
| --- | --- | --- |
| 临界区读为主 | 全串行 | 读者并行,吞吐显著提升 |
| 写密集 | 等价 | 更差(额外状态维护 + 策略开销) |
| 递归/升级 | 视实现 | 递归读锁受限(Go 明文禁止;POSIX 允许但计数匹配) |
| 复杂度 | 低 | 高(偏好策略、饥饿、优先级反转都可能出现) |

## 环境准备

- C:Linux + glibc + gcc + pthread(`-lpthread`,链接 glibc 2.25+ 均可)。
- Python:3.8+,仅标准库(Windows 可跑,本仓库自检方式)。
- Go:1.21+(TryRLock/TryLock 需 1.18+),仅标准库。

## 运行方式

```bash
gcc -O2 -Wall -Wextra main.c -o rwlock_demo -lpthread && ./rwlock_demo
python3 main.py
go run .
```

## 关键代码片段

Python——两种偏好只差一个谓词(原理 §2):

```python
def rdlock(self):
    with self._cond:
        if self.writer_pref:
            while self._writer or self._waiting_writers:
                self._cond.wait()
        else:
            while self._writer:
                self._cond.wait()
        self._readers += 1
```

Go——TryRLock 的非阻塞探测(原理 §3):

```go
rw.Lock()
ok := rw.TryRLock()   // 写者持锁 → false,且永不阻塞
```

## 性能与边界

- 读写锁在**读临界区极短**时可能比互斥锁更慢(缓存行在核间弹跳)——
  先测后用。
- 读者偏好 + 持续读流 = 写者饥饿;写者偏好 + 写突发 = 读者延迟尖刺。
- 读锁总数上限实现自定义(POSIX `EAGAIN`);实际几乎不会触达。

## 注意事项与常见坑

- **现象**:持读锁再 RLock 的 Go 程序死锁 → **原因**:写者偏好下被阻塞的 Lock
  挡住自己的第二次 RLock → **规避**:Go 禁止递归读锁,重构代码。
- **现象**:C 里 `rdlock` 后写者永不运行 → **原因**:读者流不断 + 读者偏好实现
  → **规避**:换写者偏好实现或用 `pthread_rwlockattr` 查看/设置策略。
- **现象**:读写锁性能不如互斥锁 → **原因**:读临界区太短/核多导致缓存行竞争
  → **规避**:benchmark 后再选型。
- 升级(读→写)任何实现都不安全:必须先解锁再申请写锁,窗口内数据可能已被改。

## 参考资料(实际阅读过的权威来源)

- [pthread_rwlock_rdlock - The Open Group Base Specifications Issue 7](https://pubs.opengroup.org/onlinepubs/9699919799/functions/pthread_rwlock_rdlock.html) —
  放行条件的 TPS/implementation-defined 两档规定、递归读锁与 EAGAIN、
  tryrdlock 的 EBUSY、EDEADLK
- [sync package - Go Packages](https://go.dev/pkg/sync/) —
  RWMutex 写者偏好明文、递归读锁禁止、升降级禁止、happens-before 边
