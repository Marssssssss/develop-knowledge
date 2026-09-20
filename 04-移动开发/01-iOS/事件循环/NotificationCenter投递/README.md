# NotificationCenter 投递语义:同步、筛选与 observer 的生命周期

`NotificationCenter.default.post(...)` 这一行**不是**「把事件丢进队列然后返回」。
官方文档对 block 版注册的说明是:`queue` 为 `nil` 时「the block runs synchronously **on the
posting thread**」—— 也就是说 post 的那一行会**等到所有观察者的 block 跑完**才返回,
而且跑在**投递者的线程**上,不会自动切回主线程。

`python/notification_center.py` 把这套语义做成可执行模型(29 条断言),
`swift/NotificationDelivery.swift` 与 `objc/NotificationTraps.m` 是同题实现。

## 1. 一次 post 发生了什么

```
post(name:object:userInfo:)
   ├─ 按投递开始时的观察者列表做快照
   ├─ 逐个匹配 (name, object)
   ├─ queue == nil → 当场调用 block(在投递线程上)
   └─ queue != nil → 把 block 排进那个 OperationQueue
```

文档明说:**「If a notification triggers more than one observer block, the blocks can all
execute concurrently (but on their queue or on the current thread)」** —— 多个观察者之间
**没有先后保证**。

## 2. `name` / `object` 的筛选规则

| 注册时的值 | 含义 | 断言 |
| --- | --- | --- |
| `name` 给具体值 | 只收这个名字 | 02 |
| `name = nil` | **收所有名字** | 03 |
| `object` 给具体值 | 只收这个发送者发的 | 05 |
| `object = nil` | **收所有发送者** | 04–05 |

两个都给就**两个都要匹配**(05)。`name = nil` 常被当成「收不到东西」,其实正相反 ——
它是最宽的过滤器。

## 3. `queue`:同步还是排队,就在这一次参数上

| `queue` | 行为 | 断言 |
| --- | --- | --- |
| `nil` | block 在**投递线程**上同步执行,`post` 返回时已跑完 | 06–07 |
| 非 `nil` | 排进该 `OperationQueue`;`post` 返回时**还没跑** | 08–10 |

模型里把「post 之内跑完」写成可验证的顺序(06):

```python
c.add_observer(name="N", queue=None, using=lambda note: log.append(("block", note.name)))
c.post("N", thread="bg")
log.append(("post-returned",))
# log == [("block", "N"), ("post-returned",)]   ← block 先于 post 返回
```

这条推论的实践含义很直接:**在 `queue = nil` 的 block 里做耗时操作,等于把 post 那一行拖住**;
而在后台线程上 post,回调也会在后台线程 —— UI 更新要自己切回主线程。

## 4. 中心强持有两样东西

文档对返回值的说明:**「Notification center strongly holds this return value until you remove
the observer registration」**;对 block 的说明:**「The notification center copies the block.
The notification center strongly holds the copied block」**。

模型据此验了三件事:

* 拿到的是**不透明 observer 对象**(token),不是「注册凭证」这种可有可无的东西(12);
* 中心持有的是 block 的**拷贝**,与原闭包不是同一个对象(13);
* 同一个 `name`+`object` 注册两次 → **两个独立 token、两次投递**(14)。

## 5. `removeObserver`:精确、一次性、以及投递期间的移除

| 场景 | 结果 | 断言 |
| --- | --- | --- |
| 按 token 移除 | 只摘掉那一个,其他照常 | 15–16 |
| 在 block 里移除自己 | 一次性通知,第二次 post 不再命中 | 17–18 |
| 投递期间移除另一个观察者 | **本轮仍会收到**(快照语义),下一轮才没有 | 19–20 |

第三行是最容易踩的:文档说可以在 block 里移除自己实现一次性通知,但**移除不会让本轮
投递中途停下**。模型按「投递开始时做快照」实现。

> 口径说明:「投递期间移除仍会收到」是模型的读法(与一次性通知的推荐写法自洽);
> 官方文档没有显式描述投递过程中的增删行为。

## 6. weak self vs strong self:用真实 GC 结果说话

文档提醒:**「To avoid a retain cycle, use a weak reference to self inside the block when
self contains the observer as a strong reference」**。这一条不靠讲,模型用 Python 的 GC
直接验证:

| block 捕获 self 的方式 | 外部引用断开后 | 断言 |
| --- | --- | --- |
| strong | **对象仍然存活**(中心通过 block 一直持有它),post 还会命中 | 25–26 |
| weak | 对象被释放;block 仍会被调用,但拿不到 self | 27–29 |

最后一行还有个尾巴:**观察者条目不会自己消失**(29)。weak self 解决的是「self 被 block 吊住」,
不是「忘记注销」—— 该 `removeObserver` 还是得调。

## 7. 中心的作用域

每个 app 都有一个 `default` 中心,也可以自建中心来「organize communications in particular
contexts」。不同中心**互不相通**(24)。

另外文档明确:**通知中心只能在单个进程内投递**;macOS 上跨进程要用
`DistributedNotificationCenter`。

## 8. Swift 6 的新路径

文档把注册分成两类:一类是 Objective-C / 仅支持 `NSNotification` 的框架用的
`addObserver`/`addObserver`;另一类是 Swift 原生的 `addObserver(of:for:using:)`,
消息类型是 `MainActorMessage` / `AsyncMessage`,提供**强类型**与**正确的 actor 隔离**
(见 `swift/NotificationDelivery.swift` 第 6 节)。

## 9. 运行方式

```bash
cd python && python selfcheck_notification_center.py    # 29 条断言
```

Swift / Objective-C 侧只作人工对照(本机无 Xcode 工具链)。

## 10. 关键代码

```python
def post(self, name, obj=None, user_info=None, thread="main"):
    delivered = []
    snapshot = list(self._observers)          # 投递期间增删不影响本轮
    for obs in snapshot:
        if not self._matches(obs, name, obj):
            continue
        note = Notification(name, obj, user_info)
        if obs.queue is None:
            obs.block(note)                   # 当场在投递线程上跑
            delivered.append((obs.token, thread))
        else:
            self._queues.setdefault(obs.queue, []).append((obs, note))
    return delivered
```

## 11. 性能与适用边界

* 同步投递意味着 **post 的耗时 = 所有观察者 block 的耗时之和**。观察者多了以后,
  「发一个通知」会变成一次不可控的长调用。
* 观察者列表是**线性扫描**的;`name = nil` 的观察者会参与每一次 post 的匹配。
* 模型是单线程确定性的:真实环境里多个观察者的 block 可以在不同队列/线程上并发,
  顺序不保证(21–22 用两个队列验证「谁先执行谁先落地」)。

## 12. 注意事项与常见坑

1. **`queue = nil` 不等于「异步」**,恰恰相反,它是最同步的一种。
2. **后台线程 post → 后台线程回调**,UI 更新必须自己切主线程。
3. **忘记 `removeObserver`**:block 版注册的 observer 被中心强持有,不会因为持有者释放而消失。
4. **`weak self` 只解决 self 被吊住**,不解决「观察者条目还在」—— 仍要注销。
5. **`name = nil` 是「全收」**,调试期最容易看到「莫名其妙被调用」的就是它。
6. **在 block 里做重活**会把 post 那一行拖住,进而拖住整个投递线程。
7. 一次性通知靠「在 block 里移除自己」实现,但**本轮**的其他观察者照常收到。

## 参考资料

* Apple《NotificationCenter》
  <https://developer.apple.com/tutorials/data/documentation/foundation/notificationcenter.json>
* Apple《addObserver(forName:object:queue:using:)》(queue 为 nil 时同步执行、
  中心强持有 token 与 block 拷贝、weak self 提示)
  <https://developer.apple.com/tutorials/data/documentation/foundation/notificationcenter/addobserver(forname:object:queue:using:).json>
* Apple《post(name:object:userInfo:)》
  <https://developer.apple.com/tutorials/data/documentation/foundation/notificationcenter/post(name:object:userinfo:).json>
