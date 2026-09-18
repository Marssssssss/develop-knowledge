// 平台通道的 Dart 侧用法(Flutter 3.x)。
//
// 本机无 Flutter SDK,不做编译;代码按官方文档真实 API 书写,供人工审查。
// 权威依据:docs.flutter.dev/platform-integration/platform-channels
//
// 三条要点在下面用 [NOTE] 标出:
//   1) 平台侧 handler 默认跑在平台主线程(Android 的 UI 线程 / iOS 的 main thread),
//      想挪到后台线程必须给通道传 TaskQueue;
//   2) 从 Dart 侧发起调用可以在任意 Isolate,但那个 Isolate 必须是 root Isolate
//      或被注册过的 background Isolate;
//   3) 平台侧回给 Flutter 的调用必须在平台主线程发起。

import 'dart:async';
import 'dart:isolate';

import 'package:flutter/services.dart';

/// 通道名必须全 App 唯一,官方建议加域名前缀。名字相同的两个通道会互相干扰。
const MethodChannel _batteryChannel =
    MethodChannel('samples.flutter.dev/battery');

/// 事件通道:平台侧持续推送,通常被包装成广播流。
const EventChannel _sensorChannel = EventChannel('samples.flutter.dev/sensors');

/// 基础消息通道:不区分"方法名/参数",只有原始消息 + 指定 codec。
const BasicMessageChannel<String> _logChannel =
    BasicMessageChannel<String>('samples.flutter.dev/log', StringCodec());

/// 方法通道的三种结果语义,对应平台侧 result.success / result.error /
/// result.notImplemented。错误分支里 code 与 message 都是字符串,details 任意。
Future<int?> readBatteryLevel() async {
  try {
    return await _batteryChannel.invokeMethod<int>('getBatteryLevel');
  } on PlatformException catch (e) {
    // 平台侧 result.error("UNAVAILABLE", "Battery level not available.", null)
    // 会在这里变成 PlatformException(code: 'UNAVAILABLE', ...)。
    return null;
  } on MissingPluginException {
    // 通道名写错、或插件没注册时抛这个 —— 它不属于 PlatformException。
    return null;
  }
}

/// 用 BasicMessageChannel 发一条消息给平台侧。
Future<String?> sendLog() => _logChannel.send('ui-ready');

/// 订阅事件流。receiveBroadcastStream 会在第一个监听者出现时向平台侧发
/// "listen" 调用,最后一个监听者取消时发 "cancel";超时/错误按 error 事件下推。
Stream<dynamic> sensorReadings({int samplingMs = 200}) {
  return _sensorChannel.receiveBroadcastStream({'intervalMs': samplingMs});
}

// ---------------------------------------------------------------------------
// 反向调用:Dart 侧实现方法,平台侧当 client
// ---------------------------------------------------------------------------

/// 平台侧调 `quickActions` 时走这里。handler 必须**同步返回** —— 只做"登记",
/// 真正的异步处理放到后面的 Future 里,否则平台侧会一直等这一帧。
void registerReverseChannel() {
  const MethodChannel channel = MethodChannel('samples.flutter.dev/quick-actions');
  channel.setMethodCallHandler((MethodCall call) async {
    switch (call.method) {
      case 'getStatus':
        return 'ready'; // 直接返回的值会被编码进成功信封
      case 'refresh':
        // 抛 PlatformException 等价于平台侧调 result.error(...)
        throw PlatformException(code: 'BUSY', message: 'refresh in flight');
      default:
        // 返回 FlutterMethodNotImplemented(或平台侧 result.notImplemented())
        // 是标准做法:让调用方知道"这个方法在本平台没有实现"。
        throw MissingPluginException('未实现:${call.method}');
    }
  });
}

// ---------------------------------------------------------------------------
// [NOTE 2] 后台 Isolate 使用插件:必须先绑 root isolate token
// ---------------------------------------------------------------------------

Future<void> _isolateMain(RootIsolateToken token) async {
  // 没有这一行,后台 Isolate 里调用任何通道都会失败。
  BackgroundIsolateBinaryMessenger.ensureInitialized(token);
  const MethodChannel store = MethodChannel('plugins.flutter.io/shared_preferences');
  await store.invokeMethod<bool>('reload');
}

void spawnWorker() {
  final RootIsolateToken? token = RootIsolateToken.instance; // 只能在 root Isolate 取
  if (token == null) return;
  Isolate.spawn(_isolateMain, token);
}

// ---------------------------------------------------------------------------
// [NOTE 1] TaskQueue:平台侧 handler 想跑在后台线程
// ---------------------------------------------------------------------------
//
// Dart 侧无法指定平台侧的线程 —— 这件事只能由**平台侧**在建通道时决定。
// Android( Kotlin )的做法是:
//
//   val taskQueue = flutterPluginBinding.binaryMessenger.makeBackgroundTaskQueue()
//   val channel = MethodChannel(binding.binaryMessenger, "com.example.foo",
//                               StandardMethodCodec.INSTANCE, taskQueue)
//
// iOS( Swift )同理用 registrar.messenger().makeBackgroundTaskQueue?()。
// 不传 TaskQueue 时 handler 跑在平台主线程,任何耗时操作都会卡住 UI。
//
// [NOTE 3] 反过来,平台侧想主动调 Dart,必须回到平台主线程再发:
//   Android: Handler(Looper.getMainLooper()).post { channel.invokeMethod(...) }
//   iOS:     DispatchQueue.main.async { channel.invokeMethod(...) }

/// 编解码器选择(官方列出的通用 codec):
///   StandardMessageCodec —— 二进制、支持 JSON 风格的值,平台通道默认用它;
///   JSONMessageCodec     —— UTF-8 的 JSON;
///   StringCodec          —— 纯 UTF-8 字符串;
///   BinaryCodec          —— 直接透传字节,不做任何结构化编解码。
/// 自定义 codec 也可以,但两侧必须配套。StandardMessageCodec 的位级布局见
/// ../python/sc_codec.py(Float64List 等要按元素宽度对齐、double 要按 8 字节对齐)。
