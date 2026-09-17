/**
 * native_hook_agent.js — Frida Interceptor 真实 API 风格(需真机 frida-server)
 * 语义对应 frida.re/docs/javascript-api 官方 Interceptor 章节。
 */
'use strict';

// 1) 官方 read() 观测示例: onEnter 存参, onLeave hexdump 缓冲区
const libc = Process.getModuleByName('libc.so');
Interceptor.attach(libc.getExportByName('read'), {
  onEnter(args) {
    console.log('Context  : ' + JSON.stringify(this.context)); // pc/sp/rax...
    console.log('Return   : ' + this.returnAddress);
    console.log('ThreadId : ' + this.threadId);
    console.log('Depth    : ' + this.depth);
    this.fd = args[0].toInt32();   // 存给 onLeave 用(this 跨 onEnter/onLeave 存活)
    this.buf = args[1];
    this.count = args[2].toInt32();
  },
  onLeave(retval) {
    const numBytes = retval.toInt32();
    if (numBytes > 0) console.log(hexdump(this.buf, { length: numBytes, ansi: true }));
    // retval 对象跨 onLeave 复用——不要存到回调外
  }
});

// 2) 改返回值: retval.replace
Interceptor.attach(libc.getExportByName('open'), {
  onLeave(retval) { retval.replace(-1); }        // 让 open() 永远失败
});

// 3) 整函数替换 + NativeFunction 链回原实现(不递归)
const openPtr = libc.getExportByName('open');
const open = new NativeFunction(openPtr, 'int', ['pointer', 'int']); // 替换前包装
Interceptor.replace(openPtr, new NativeCallback((pathPtr, flags) => {
  const path = pathPtr.readUtf8String();
  console.log('Opening "' + path + '"');
  return open(pathPtr, flags);                   // 官方: 经 NativeFunction 绕过 hook 直达原实现
}, 'int', ['pointer', 'int']));

// 4) 热路径: onEnter/onLeave 直接指 CModule 编译的 C 函数(签名 void onEnter(GumInvocationContext*))
//    官方基准 iPhone 5S: onEnter-only ~6us, 两者 ~11us —— 只挂需要的回调。

// 5) patch 生效时机: 离开 JS 运行时 / 调 send() 时自动 flush;也可手动
// Interceptor.flush();
// Interceptor.revert(openPtr);      // 撤销单个
// Interceptor.detachAll();          // 全撤

// 6) 注意 32 位 ARM: Thumb 函数地址 LSB=1,ARM 函数 LSB=0;Frida API 返回值已处理。
