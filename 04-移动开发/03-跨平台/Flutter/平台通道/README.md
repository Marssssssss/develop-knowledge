# Flutter 平台通道(Platform Channel)与 StandardMethodCodec 二进制格式

## 简介

平台通道是 Flutter 里 Dart(客户端/UI 侧)与原生平台(主机侧)之间唯一的官方通信方式。
Dart 侧调用 `MethodChannel.invokeMethod` 时,消息被**编码成字节流**交给平台的
BinaryMessenger,原生侧解码、执行、再把结果编码成"应答信封"送回来 —— 整个过程
**异步**,官方明确说这是"to ensure the user interface remains responsive"。

关键概念:

- **BinaryMessenger** —— 只管字节的收发管道,不关心内容;所有通道都建在它之上。
- **MessageCodec / MethodCodec** —— 决定这些字节怎么解释。方法通道默认用
  `StandardMethodCodec`,它内部套一个 `StandardMessageCodec`。
- **应答信封(envelope)** —— 结果不是裸值,而是带一个"成功/错误"前导字节的包装。
- **TaskQueue** —— 让平台侧 handler 离开平台主线程执行的可选开关。
- **root / background Isolate** —— Dart 侧发起调用的合法主体。

## 原理详解

### 1. 一次方法调用的完整路径

1. Dart 侧 `invokeMethod('getBatteryLevel', args)` 把调用编码成
   **方法名 String 的编码 ++ 参数值的编码**(两段直接拼接,**没有信封**)。
2. 字节流经 BinaryMessenger 送往平台侧;调用立刻返回 Future,不阻塞当前 Isolate。
3. 平台侧按通道名找到 handler,在**平台主线程**执行(除非建通道时传了 TaskQueue)。
4. handler 调 `result.success(v)` / `result.error(code, msg, details)` /
   `result.notImplemented()`。
5. 返回值被编码成信封送回 Dart,`invokeMethod` 的 Future 完成或抛
   `PlatformException`。

### 2. 应答信封:一个前导字节决定分支

源码注释原文:*"Reply envelopes are encoded using first a single byte to distinguish
the success case (0) from the error case (1)."*

| 前导字节 | 后续内容 | Dart 侧观察到的结果 |
| --- | --- | --- |
| `0x00` | 结果值的编码 | Future 正常完成 |
| 非 0 | code 字符串 + message 字符串 + details 值 | 抛 `PlatformException` |
| 非 0 | 同上,再多一个值 | 第 4 个值被读成 `stacktrace` |

注意 `decodeEnvelope` 的判定是 **"非 0 即错误"**,而不是"等于 1 才错误":
源码注释写的是 *"First byte is zero in success case, and non-zero otherwise"*。
本 demo 里用前导字节 `2` 的信封做了断言验证。

> 口径说明:官方文档只给出了 `result.notImplemented()` 的 **API 语义**,没有公开它在
> 线上的字节取值。本 demo 因此不对 NOT_IMPLEMENTED 的首字节做数值断言。

### 3. StandardMessageCodec:类型字节 + expanding 长度

每个值先写**一个类型判定字节**,再写值本体。数字一律按**宿主字节序**。

| 取值 | 含义 | 取值 | 含义 |
| --- | --- | --- | --- |
| 0 / 1 / 2 | null / true / false(无后续字节) | 8 | Uint8List |
| 3 | int32(4 字节补码) | 9 | Int32List |
| 4 | int64(8 字节补码) | 10 | Int64List |
| 5 | "更大整数"(`writeValue` 从不产出,留给子类) | 11 | Float64List |
| 6 | float64(对齐到 8 字节) | 12 | List |
| 7 | String(UTF-8) | 13 | Map |
| | | 14 | Float32List |

0–127 保留给基类,≥128 留给扩展。

**长度/个数用 expanding 格式**:0–253 单字节;254–0xFFFF 用 `254` + 2 字节;
0x10000–2³²−1 用 `255` + 4 字节。字符串写的是 **UTF-8 字节数**,不是字符数 ——
本 demo 断言了 `"中文"` 的长度字段是 6 而非 2。

**对齐**:double 的值前面补零字节,使其落在 **64 位边界**;Uint8List/Int32List/
Int64List/Float32List/Float64List 先写元素个数,再补**最少数量**的零字节把元素区
对齐到元素宽度,然后连续写元素(元素之间不带类型信息)。

### 4. 三类通道

| 通道 | 形态 | 典型用途 |
| --- | --- | --- |
| `MethodChannel` | 异步方法调用 + 单个应答 | 一次性请求:读电量、调相机 |
| `EventChannel` | 平台侧推流,`receiveBroadcastStream()` 暴露为**广播流** | 传感器、定位、下载进度 |
| `BasicMessageChannel` | 只有原始消息 + 指定 codec,不区分方法名 | 自定义协议、透传字节 |

`MethodChannel` 与 `BasicMessageChannel` 在官方文档里都被明确标注为
**not type safe** —— 两侧必须自己保证方法名与参数类型一致,否则只能等到运行时才炸。
`Pigeon` 就是为消除这个问题而生的:由代码生成器产出类型安全的调用桩。

`EventChannel` 是建在方法通道语义之上的:设置订阅 → `listen` 调用,取消 → `cancel`
调用,事件按信封格式下推。

### 5. 线程模型(最容易踩的一节)

官方原话:*"When invoking channels on the platform side destined for Flutter, invoke
them on the platform's main thread. When invoking channels in Flutter destined for
the platform side, either invoke them from any Isolate that is the root Isolate, or
that is registered as a background Isolate."*

- 平台侧 handler 默认在**平台主线程**(Android 的 UI 线程 / iOS 的 main thread)执行;
  耗时操作必须靠 **TaskQueue** 挪到后台线程(`makeBackgroundTaskQueue()`)。
- 想主动从平台侧回调 Dart,必须**先跳回主线程**
  (Android:`Handler(Looper.getMainLooper()).post {}`;iOS:`DispatchQueue.main.async {}`)。
- 后台 Isolate 里用通道,必须先 `BackgroundIsolateBinaryMessenger.ensureInitialized(token)`,
  `token` 只能在 root Isolate 通过 `RootIsolateToken.instance` 取到。

## 对比 / 选型

| 方案 | 类型安全 | 通信模型 | 适用 |
| --- | --- | --- | --- |
| MethodChannel | ❌ 靠约定 | 请求/应答 | 偶发的一次性调用 |
| EventChannel | ❌ | 单向持续推流 | 高频事件流 |
| BasicMessageChannel | ❌ | 原始消息 | 自定义 codec / 透传 |
| Pigeon | ✅ 代码生成 | 请求/应答 | 插件对外 API,推荐 |

## 环境准备

- 操作系统:任意(自检脚本是纯 Python)
- Python 3.8+(无第三方依赖)
- Dart / Flutter:阅读代码用,本机未安装 SDK,不做编译

## 运行方式

### Python(编解码格式自检)

```bash
cd python
python3 main.py     # 75 项断言(sc_codec.py = 消息编解码,method_codec.py = 方法层与信封)
```

### Dart

`dart/platform_channel_demo.dart` 是 Flutter 工程内的用法示例,需放进 Flutter 项目
并配合平台侧实现才能运行;本 demo 只做人工审查。

## 关键代码片段

编码侧(节选自 `python/sc_codec.py`,行号对应"原理详解 3"):

```python
def write_size(buf, value):            # expanding 长度格式
    if value < 254:
        buf.put_uint8(value)
    elif value <= 0xFFFF:
        buf.put_uint8(254); buf.put_uint16(value)
    else:
        buf.put_uint8(255); buf.put_uint32(value)

def write_value(buf, value):           # 类型字节 + 值本体
    if isinstance(value, float):
        buf.put_uint8(TYPE_FLOAT64)
        buf.align_to(8)                # double 的值落在 64 位边界
        buf.data += struct.pack("<d", value)
    elif isinstance(value, str):
        buf.put_uint8(TYPE_STRING)
        raw = value.encode("utf-8")
        write_size(buf, len(raw))      # 写的是字节数
        buf.data += raw
```

信封解码(节选自 `python/method_codec.py`,对应"原理详解 2"):

```python
def decode_envelope(envelope):
    if len(envelope) == 0:
        raise FormatException("Expected envelope, got nothing")
    buf = ReadBuffer(envelope)
    if buf.get_uint8() == 0:           # 注释:first byte is zero in success case
        return read_value(buf)
    code = read_value(buf); msg = read_value(buf); details = read_value(buf)
    stack = read_value(buf) if buf.has_remaining else None
    raise PlatformException(code, msg, details, stack)
```

## 性能与边界

- 一次调用至少产生一次**编码 + 跨语言边界 + 解码**;参数越大、嵌套越深,成本越高。
  高频小消息应合并成一次批量调用。
- 单条消息可表达的长度上限由 expanding 格式给出:`255` 分支能表示到 2³²−1 字节。
- Android 上存在"每帧消息"的软限制场景,长任务必须走 TaskQueue 或后台 Isolate。
- 携带 `Uint8List` 的大块数据会有一次完整拷贝(编解码不是零拷贝)。

## 注意事项与常见坑

1. **把 NOT_IMPLEMENTED 当成 success**:平台侧返回 notImplemented 时 Dart 侧也会
   走错误分支,若只 catch `PlatformException` 而不区分 code,会把"没实现"当"成功"。
2. **通道名撞车**:官方明确说"Identically named channels will interfere with each
   other's communication",名字必须带域名前缀。
3. **在平台主线程做耗时活**:默认 handler 跑在 UI 线程,不做 TaskQueue 就等着掉帧。
4. **handler 里 await 太久**:handler 应尽快返回,异步工作交给返回的 Future 承载。
5. **后台 Isolate 直接用通道**:不调
   `BackgroundIsolateBinaryMessenger.ensureInitialized` 会直接失败。
6. **把原始事件对象透传给不可信代码**:反向调用时只回传数据,不要暴露底层事件对象。

## 参考资料(实际阅读过的权威来源)

- [Flutter 官方文档 · Platform channels](https://docs.flutter.dev/platform-integration/platform-channels)
  —— 架构、数据映射表、线程规则、TaskQueue、Pigeon 定位
- [Flutter 源码 · message_codecs.dart](https://raw.githubusercontent.com/flutter/flutter/master/packages/flutter/lib/src/services/message_codecs.dart)
  —— 类型字节常量、expanding 格式、信封前导字节、三处 FormatException 判定、对齐注释
- [API 文档 · StandardMethodCodec](https://api.flutter.dev/flutter/services/StandardMethodCodec-class.html)
  —— encode/decode 方法族与 "not type safe" 说明
- [API 文档 · EventChannel](https://api.flutter.dev/flutter/services/EventChannel-class.html)
  —— receiveBroadcastStream、同名字通道互相干扰
