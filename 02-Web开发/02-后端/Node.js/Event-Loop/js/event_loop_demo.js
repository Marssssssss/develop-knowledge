// Node.js Event Loop — 6 阶段 + 微任务演示
// 运行: node js/event_loop_demo.js
//
// 本文件包含 6 个独立小 demo，按编号执行；注释里会标明每个 demo
// 想要观察的现象及对应 Node.js 官方文档/HTML Standard 里的概念。
//
// 关键概念速览（详见 README.md）：
//   - 6 阶段: timers -> pending callbacks -> idle/prepare -> poll -> check -> close callbacks
//   - 微任务: process.nextTick 队列 (Node 独有，最高优先级) + Promise 微任务队列
//   - 在每个阶段回调之间（"phase boundary"）先 drain nextTick 队列，
//     再 drain 其他微任务队列，然后才进入下一阶段

'use strict';

const fs = require('node:fs');
const path = require('node:path');

// 帮助函数：打印一行带阶段标签的日志，避免 Promise 里 await 影响阅读。
function tag(label) {
  return `[${label.padEnd(14, ' ')}]`;
}

async function sleep(ms, label) {
  console.log(tag(label || 'sleep'), `await ${ms}ms`);
  await new Promise(resolve => setTimeout(resolve, ms));
}

// =====================================================================
// Demo 1：同步 vs microtask vs 阶段宏任务
// 观察点：主模块同步代码 -> nextTick -> Promise.then -> setTimeout (timers)
//         -> setImmediate (check) -> I/O 回调 (poll)
// =====================================================================
function demo1_basicOrder() {
  console.log('\n=== Demo 1: sync -> nextTick -> Promise -> timers -> check ===');

  // 同步：第一时间打
  console.log(tag('sync'), '1. main script');

  // nextTick：Node 独有，最高优先级，排在主同步代码结束之后、下一次微任务检查之前
  process.nextTick(() => {
    console.log(tag('nextTick'), '2. nextTick (nextTick queue)');
  });

  // Promise.then：标准微任务；Node 在每个阶段回调之间清空两个微任务队列，
  // 先 nextTick 再 Promise（见 nodejs.org 官方文档 "process.nextTick" 章节）
  Promise.resolve().then(() => {
    console.log(tag('promise'), '3. promise.then (microtask queue)');
  });

  // setTimeout 0：扔到 timers 阶段
  setTimeout(() => {
    console.log(tag('timers'), '4. setTimeout 0 (timers phase)');
  }, 0);

  // setImmediate：扔到 check 阶段（poll 之后）
  setImmediate(() => {
    console.log(tag('check'), '5. setImmediate (check phase)');
  });

  // 同步：最后打
  console.log(tag('sync'), '6. main script end');
}

// =====================================================================
// Demo 2：nextTick 的"插队"行为 + 递归 nextTick 的 starvation 风险
// 观察点：nextTick 在每次 C/C++ handler -> JS 边界之后立即执行，
//         因此可以"插队"在每个宏任务回调结束之后。
//         如果递归调用 process.nextTick 会让事件循环永远不进入 poll 阶段。
// =====================================================================
function demo2_nextTickStarvation() {
  console.log('\n=== Demo 2: nextTick 插队 vs 递归 starvation ===');

  setImmediate(() => {
    console.log(tag('check'), 'setImmediate fired');
  });

  // 只递归一次 nextTick：观察 immediate 是否能被执行
  let ticks = 0;
  const recursiveNextTick = () => {
    if (ticks++ < 1) {
      process.nextTick(recursiveNextTick);
      console.log(tag('nextTick'), `recursive nextTick depth=${ticks}`);
    }
  };
  recursiveNextTick();

  // 短超时：观察 nextTick 是否比 setTimeout 早跑
  setTimeout(() => {
    console.log(tag('timers'), 'setTimeout fired');
  }, 50);

  // 警告：在真实代码中，递归 nextTick 会"饿死"事件循环 —
  // 见 nodejs.org 文档原话："it allows you to 'starve' your I/O"
  console.log(tag('sync'), '(请勿在生产代码里递归 process.nextTick, 会阻塞 I/O)');
}

// =====================================================================
// Demo 3：setTimeout vs setImmediate 在 I/O 回调中的稳定顺序
// 观察点：官方文档指出：在 I/O 回调内，setImmediate 总是先于 setTimeout 0
//         （因为 I/O 回调位于 poll 阶段，下一阶段就是 check）。
// =====================================================================
function demo3_timeoutVsImmediateInIO() {
  console.log('\n=== Demo 3: setTimeout vs setImmediate (在 I/O 回调内) ===');

  // 读一个真实小文件触发 poll 阶段 I/O 回调
  const file = path.join(__dirname, 'event_loop_demo.js');
  fs.readFile(file, () => {
    console.log(tag('poll'), 'I/O callback fired (poll phase)');

    setTimeout(() => {
      console.log(tag('timers'), 'setTimeout 0 inside I/O');
    }, 0);

    setImmediate(() => {
      console.log(tag('check'), 'setImmediate inside I/O');
    });
  });
}

// =====================================================================
// Demo 4：await 拆解 — async function 的微任务链
// 观察点：async function 中每个 await 都对应一个 Promise 微任务，
//         等价于 .then(continuation)。演示 await 与 process.nextTick
//         的相对顺序。
// =====================================================================
async function demo4_awaitVsNextTick() {
  console.log('\n=== Demo 4: await vs process.nextTick ===');

  console.log(tag('sync'), 'before await');

  // nextTick 注册时机早于 await，所以它先于 await 之后的代码执行
  process.nextTick(() => {
    console.log(tag('nextTick'), 'nextTick before await resolution');
  });

  await sleep(0, 'await');

  // 这里是 await 之后的微任务，排在 .then(continuation) 里
  console.log(tag('microtask'), 'after await resolved');

  // 此刻再注册 nextTick，会在后续每个 C/C++ handler 边界先跑
  process.nextTick(() => {
    console.log(tag('nextTick'), 'nextTick after await resolved');
  });

  // 跨一个真正的宏任务，看阶段顺序
  setImmediate(() => {
    console.log(tag('check'), 'setImmediate after await');
  });
}

// =====================================================================
// Demo 5：libuv 1.45.0 / Node 20+ 之后的变化
// 观察点：官方文档写明：在 libuv 1.45.0 (Node.js 20) 之后，
//         timers 在 poll 阶段之后才执行（即 "after poll"），
//         但仍保留向后兼容性 — 进入主循环前先跑一次 timers。
//         这是个较新的细节，老博客的图仍画 timers 在 poll 之前。
// =====================================================================
function demo5_libuv145Change() {
  console.log('\n=== Demo 5: 同一 I/O 周期里注册的 timers 与 check 顺序 ===');

  // 用 setImmediate 标记 "进入主循环"
  setImmediate(() => {
    console.log(tag('check'), '(round 1) setImmediate fired');

    // 在 check 阶段里注册一个 setTimeout 0 + 另一个 setImmediate
    setTimeout(() => {
      console.log(tag('timers'), '(round 1) setTimeout fired inside check');
    }, 0);

    setImmediate(() => {
      console.log(tag('check'), '(round 2) setImmediate fired inside check');
    });
  });
}

// =====================================================================
// 主函数：按顺序串起来。注意 demo4 是 async，要 await 它。
// =====================================================================
async function main() {
  console.log(`Node.js 版本: ${process.version}`);
  console.log(`平台: ${process.platform} (libuv 实现存在 Windows/Unix 差异)`);

  demo1_basicOrder();
  demo2_nextTickStarvation();
  demo3_timeoutVsImmediateInIO();
  await demo4_awaitVsNextTick();
  demo5_libuv145Change();

  console.log('\n=== 全部 demo 已派发，等待宏任务完成 ===');
}

main().catch(err => {
  console.error('fatal:', err);
  process.exit(1);
});
