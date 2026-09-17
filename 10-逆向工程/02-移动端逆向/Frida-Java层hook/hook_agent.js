/**
 * hook_agent.js — 真实 Frida API 风格的 Java 层 hook agent(参考实现,需真机 frida-server)
 * 语义对应 frida.re/docs/javascript-api 的 Java 命名空间官方示例。
 */
'use strict';

// 1) 标准三段式: perform 等类加载器 -> use 取包装 -> 替换 implementation
Java.perform(() => {
  const Activity = Java.use('android.app.Activity');

  Activity.onResume.implementation = function () {
    send('onResume() got called! Let\'s call the original implementation');
    const ret = this.onResume();          // 调原始实现(Frida 保存的原 ArtMethod 入口)
    send({ hook: 'onResume', retval: String(ret) });
    return ret;
  };

  // 2) 重载: 必须显式选择,否则多重载方法报歧义
  const onCreateNoArgs = Activity.onCreate.overload();
  onCreateNoArgs.implementation = function () {
    send({ hook: 'onCreate()' });
    return this.onCreate();
  };

  // 3) 从替换函数里抛 Java 异常(官方 Exception.$new 示例)
  // Activity.onResume.implementation = function () {
  //   const Exception = Java.use('java.lang.Exception');
  //   throw Exception.$new('Oh noes!');
  // };

  // 4) 枚举已加载类 + glob 方法搜索(带 s 修饰符输出签名)
  // Java.enumerateLoadedClasses({ onMatch: n => send(n), onComplete: () => {} });
  // const groups = Java.enumerateMethods('*youtube*!on*/s');
  // send(JSON.stringify(groups, null, 2));

  // 5) 留存实例: Java.retain 避免替换帧结束后句柄失效
  let lastActivity = null;
  Activity.onResume.implementation = function () {
    lastActivity = Java.retain(this);
    return this.onResume();
  };
});

// 6) 宿主侧消息(在 Python 宿主中用 script.on('message', cb) 接收):
//    send(payload, arrayBuffer) 第二参走二进制快通道,高频场景应批量合并。
// 7) 独立 agent 必须手动包 Java.perform;REPL / frida-trace 会自动包装。
