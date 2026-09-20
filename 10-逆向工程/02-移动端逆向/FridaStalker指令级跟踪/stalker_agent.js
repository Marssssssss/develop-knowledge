// stalker_agent.js — Frida Stalker 指令级跟踪的真实 agent 脚本。
// 与 stalker_trace.py 的模型一一对应; 用法:
//   frida -U -f com.example.app -l stalker_agent.js --no-pause
//   frida -U -n "App" -l stalker_agent.js
//
// 对照 Frida JavaScript API 文档 Stalker 一节:
//   https://frida.re/docs/javascript-api/
'use strict';

// ---------------------------------------------------------------- 配置

const CONFIG = {
  // events 的五个开关直接对应 gumevent.h 的 GumEventType 位:
  //   call = 1, ret = 2, exec = 4, block = 8, compile = 16
  events: { call: true, ret: true, exec: false, block: true, compile: false },
  trustThreshold: 1,        // -1 永不信任(慢) / 0 立即信任 / N 执行 N 次后信任
  queueCapacity: 16384,     // 事件队列容量(条)
  queueDrainInterval: 250   // 毫秒; 设 0 则关闭周期排空, 只能靠 Stalker.flush()
};

// 想盯的模块范围(填实际 so 名); 留空表示跟踪该线程跑到的所有代码
const TARGET = { module: null };

let followState = { threadId: null, probes: [] };

// ---------------------------------------------------------------- 工具

function moduleRange(name) {
  const m = Process.getModuleByName(name);
  return { start: m.base, end: m.base.add(m.size), base: m.base, size: m.size };
}

// ---------------------------------------------------------------- transform

// 默认实现就是: while (iterator.next() !== null) iterator.keep();
// 不调 keep() 的指令会被丢弃, 这让我们能整段替换某些指令。
function makeTransform(appStart, appEnd) {
  return function (iterator) {
    let instruction = iterator.next();
    do {
      const startAddress = instruction.address;
      const isAppCode = startAddress.compare(appStart) >= 0 &&
                        startAddress.compare(appEnd) === -1;

      // ARM/ARM64 上必须小心: 插桩可能破坏处理独占访存的指令序列,
      // 只有 memoryAccess 为 'open' 时才允许插入"噪声"代码。
      const canEmitNoisyCode = iterator.memoryAccess === 'open';

      if (isAppCode && canEmitNoisyCode && instruction.mnemonic === 'ret') {
        // 文档示例: 在每条 ret 前插入比较 + 同步 callout
        iterator.putCmpRegI32('eax', 60);
        iterator.putJccShortLabel('jb', 'nope', 'no-hint');
        iterator.putCmpRegI32('eax', 90);
        iterator.putJccShortLabel('ja', 'nope', 'no-hint');
        iterator.putCallout(onMatch);
        iterator.putLabel('nope');
      }

      iterator.keep();
    } while ((instruction = iterator.next()) !== null);
  };
}

function onMatch(context) {
  send({ kind: 'match', pc: context.pc.toString(), rax: context.rax.toInt32() });
}

// ---------------------------------------------------------------- 事件消费

// onReceive 与 onCallSummary 只能指定其中一个:
//   onReceive 拿到原始 GumEvent 二进制流(保序, 开销大)
//   onCallSummary 只拿到 "调用目标 -> 次数" 的映射(不保序, 开销小)
function makeOnReceive() {
  return function (events) {
    // annotate 显示事件类型, stringify 把指针格式化成字符串(便于直接 send)
    const parsed = Stalker.parse(events, { annotate: true, stringify: true });
    send({ kind: 'events', payload: parsed });
  };
}

function makeOnCallSummary() {
  return function (summary) {
    send({ kind: 'summary', payload: summary });
  };
}

// ---------------------------------------------------------------- 主体

function start() {
  const threadId = Process.getCurrentThreadId();

  let app = null;
  if (TARGET.module !== null) {
    app = moduleRange(TARGET.module);
  } else {
    const m = Process.enumerateModules()[0];
    app = { start: m.base, end: m.base.add(m.size) };
  }

  // 把 libSystem / libc 这类高频库排除掉: 仍能看到入参与返回值,
  // 但看不到它们内部的指令 —— 显著降噪提速。
  ['libc.so', 'libart.so', 'libbase.so'].forEach(function (name) {
    try {
      const r = moduleRange(name);
      Stalker.exclude(r);
    } catch (e) {
      // 模块不存在就跳过
    }
  });

  Stalker.trustThreshold = CONFIG.trustThreshold;
  Stalker.queueCapacity = CONFIG.queueCapacity;
  Stalker.queueDrainInterval = CONFIG.queueDrainInterval;

  Stalker.follow(threadId, {
    events: CONFIG.events,
    transform: makeTransform(app.start, app.end),
    onReceive: makeOnReceive()
    // onCallSummary: makeOnCallSummary()   // 与 onReceive 二选一
  });

  followState.threadId = threadId;

  // call probe: 命中即同步回调, 返回 id 供 removeCallProbe 使用
  const probeId = Stalker.addCallProbe(app.start.add(0x1234), function (args) {
    send({ kind: 'probe', args: [args[0].toString(), args[1].toString()] });
  });
  followState.probes.push(probeId);
}

function stop() {
  if (followState.threadId === null) return;
  Stalker.unfollow(followState.threadId);
  // unfollow 之后要在安全点释放累积内存, 否则刚 unfollow 的线程还在执行
  // 它的最后几条指令 —— 这就是 garbageCollect 存在的理由。
  Stalker.garbageCollect();
  followState.probes.forEach(function (id) { Stalker.removeCallProbe(id); });
  followState = { threadId: null, probes: [] };
}

rpc.exports = {
  start: start,
  stop: stop,
  // queueDrainInterval 设为 0 后, 只能靠 flush() 手动排空
  flush: function () { Stalker.flush(); },
  // invalidate 只作废指定基本块的已翻译代码, 比 unfollow + follow 便宜得多
  invalidate: function (addrStr) { Stalker.invalidate(ptr(addrStr)); }
};

// 立即启动
start();
