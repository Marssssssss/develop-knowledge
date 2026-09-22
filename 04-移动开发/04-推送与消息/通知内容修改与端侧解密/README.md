# 通知内容修改与端侧解密（Notification Service Extension）

> 推送的 payload 是明文穿过 APNs 的（APNs 只加密传输层，不加密端到端内容）。要在通知栏里显示"服务端加密、端侧解密"的内容，唯一官方手段是 **Notification Service App Extension**：系统在展示之前把通知交给它，它有一小段时间去改内容。
>
> 来源：Apple《Modifying content in newly delivered notifications》（DocC 原文实读，含 Listing 1 的 Swift 实现与 Listing 2 的载荷）。

## 一、启用条件比想象中窄

官方原话：

> Notification service app extensions only operate on remote notifications configured in the system to **display an alert** to the user. If alerts are disabled for your app, or if the payload specifies **only the playing of a sound or the badging of an icon**, the extension isn't employed.

再叠加两条载荷要求：

> - The payload must include the `mutable-content` key with a value of `1`.
> - The payload must include an `aps` dictionary with title, subtitle, or body information.

四条合起来：

| 条件 | 不满足时 |
| --- | --- |
| 应用没开 alert 权限 | 扩展不启用 |
| `mutable-content` 不是 `1` | 扩展不启用 |
| `aps.alert` 里没有 title/subtitle/body | 扩展不启用 |
| 载荷只有 `sound` / `badge` | 扩展不启用 |

所以"我加了扩展但没被调用"，八成是载荷里少了 `mutable-content: 1`，或者 alert 被写成了纯 badge。

### 一个 Python 侧的坑

`mutable-content` 必须是**数值** `1`。但 Python 里 `True == 1` 成立，因为 `bool` 是 `int` 的子类——裸写 `aps['mutable-content'] != 1` 会让 JSON 的 `true` 蒙混过关。本 demo 显式把 `bool` 排除掉：

```python
mutable = request.mutable()
if isinstance(mutable, bool) or mutable != 1:
    return False, 'mutable-content is not 1'
```

## 二、30 秒预算，以及"忘了调 handler"的后果

官方：

> Your `didReceive(_:withContentHandler:)` method has only **about 30 seconds** to modify the payload and call the provided completion handler. If your code takes longer than that, the system calls `serviceExtensionTimeWillExpire()`, at which point you must return whatever you can to the system immediately. **If you fail to call the completion handler from either method, the system displays the original contents of the notification.**

最后一句是最要命的：**两个方法里都没调 handler，系统就把原始内容弹出来**。对于加密通知，这意味着用户看到的是原始载荷里那个占位/密文串——所以官方 Listing 1 特意在超时分支里把 `subtitle` 设成 `(Encrypted)`、`body` 清空：

```swift
override func serviceExtensionTimeWillExpire() {
    if let contentHandler = contentHandler, let bestAttemptContent = bestAttemptContent {
        bestAttemptContent.subtitle = "(Encrypted)"
        bestAttemptContent.body = ""
        contentHandler(bestAttemptContent)
    }
}
```

三条结局：

| 场景 | 用户看到 |
| --- | --- |
| 30 秒内 `didReceive` 里调了 handler | 解密后的内容 |
| 超时，但 `serviceExtensionTimeWillExpire` 里调了 handler | 降级内容（`(Encrypted)`） |
| **两个方法都没调 handler** | **原始载荷内容** |

本 demo 用可注入的 `elapsed` 把 30 秒边界做成确定性行为（29.9 秒 vs 30.0 秒成对构造），不靠真实时钟——靠真实时钟"跑通"只是运气。

## 三、官方 Listing 2 的载荷长什么样

```json
{
   "aps" : {
      "category" : "SECRET",
      "mutable-content" : 1,
      "alert" : {
         "title" : "Secret Message!",
         "body"  : "(Encrypted)"
      },
   },
   "ENCRYPTED_DATA" : "Salted__·öîQÊ$UDì_¶Ù∞èΩ^¬%gq∞NÿÒQùw"
}
```

两个要点：

1. **密文放在 `aps` 之外**（`ENCRYPTED_DATA`）。自定义键放进 `aps` 会被静默忽略（见本目录已有的《载荷与 aps 字典》demo）；
2. `alert.body` 写的是占位 `(Encrypted)`，它同时也是"扩展没跑起来"时的兜底展示——**默认态是安全的**。

## 四、为什么要这么绕

因为推送链路是三方模型：provider → APNs → 设备。APNs 只对**传输**加密（TLS），端到端内容它看得见。要真正端到端，只能在端侧解密，而端侧解密需要一段能在"展示之前"运行的代码——这就是 service extension 存在的理由。

对应的服务端侧加密方案见本目录的《WebPush端到端加密》demo（RFC 8291）。两者是同一套思路在两条通道上的落地：Web Push 把密文塞进 `aes128gcm` 编码体，APNs 把密文塞进自定义键再靠扩展解密。

> **口径声明**：官方这篇只讲扩展机制，**没有**给出扩展可用的内存上限、附件大小上限等数值；本 demo 因此不写任何这类数字。

## 五、代码结构

| 文件 | 内容 |
| --- | --- |
| `python/service_extension.py` | 启用条件判定、`ServiceExtension` 状态机（30 秒预算 / 超时降级 / 原始回落） |
| `python/main.py` | 六段演示输出 |
| `python/selfcheck_serviceext.py` | 46 条断言 |
| `go/service_extension.go` | 同一套规则的 Go 版 |

```bash
cd python && python main.py && python selfcheck_serviceext.py
```

## 六、三个总结

1. **`mutable-content: 1` 是开关，不是建议值**。写 `true`/`"1"`/省略都不启用扩展，而且没有任何报错。
2. **超时不是失败，忘了交回才是失败**。超时分支里正确地调 handler，用户看到的是"(Encrypted)"；忘了调，用户看到的是原始载荷。
3. **默认态要安全**。官方示例把 `alert.body` 直接写成占位，正是为了"扩展没跑起来也不会泄漏"。自己实现时不要图省事把默认 body 留空或写成别的。

## 参考资料（实际读过的来源）

- [Modifying content in newly delivered notifications](https://developer.apple.com/documentation/usernotifications/modifying-content-in-newly-delivered-notifications) — 扩展适用场景、四个启用条件、30 秒预算与 `serviceExtensionTimeWillExpire`、Listing 1（Swift 解密实现）与 Listing 2（加密载荷）
