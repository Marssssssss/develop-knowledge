// Node.js Event Loop — TypeScript 版本（与 js/event_loop_demo.js 行为一致）
// 编译: tsc --target es2022 --module commonjs --strict event_loop_demo.ts
// 运行: node event_loop_demo.js   (编译产物)
//
// 与 JS 版差异：仅在签名上加类型标注（用 void / Promise<void> / NodeJS.Timeout），
// 行为代码逐行对应，便于把演示代码搬进生产项目时直接做类型检查。

import * as fs from 'node:fs';
import * as path from 'node:path';

function tag(label: string): string {
  return `[${label.padEnd(14, ' ')}]`;
}

function sleep(ms: number, label?: string): Promise<void> {
  console.log(tag(label || 'sleep'), `await ${ms}ms`);
  return new Promise<void>(resolve => setTimeout(() => resolve(), ms));
}

// =====================================================================
// Demo 1：sync -> nextTick -> Promise -> timers -> check
// =====================================================================
function demo1_basicOrder(): void {
  console.log('\n=== Demo 1: sync -> nextTick -> Promise -> timers -> check ===');

  console.log(tag('sync'), '1. main script');

  process.nextTick((): void => {
    console.log(tag('nextTick'), '2. nextTick (nextTick queue)');
  });

  Promise.resolve().then((): void => {
    console.log(tag('promise'), '3. promise.then (microtask queue)');
  });

  const t1: NodeJS.Timeout = setTimeout((): void => {
    console.log(tag('timers'), '4. setTimeout 0 (timers phase)');
  }, 0);
  void t1;

  const i1: NodeJS.Immediate = setImmediate((): void => {
    console.log(tag('check'), '5. setImmediate (check phase)');
  });
  void i1;

  console.log(tag('sync'), '6. main script end');
}

// =====================================================================
// Demo 2：nextTick 插队 + 递归 starvation 风险
// =====================================================================
function demo2_nextTickStarvation(): void {
  console.log('\n=== Demo 2: nextTick 插队 vs 递归 starvation ===');

  setImmediate((): void => {
    console.log(tag('check'), 'setImmediate fired');
  });

  let ticks = 0;
  const recursiveNextTick = (): void => {
    if (ticks++ < 1) {
      process.nextTick(recursiveNextTick);
      console.log(tag('nextTick'), `recursive nextTick depth=${ticks}`);
    }
  };
  recursiveNextTick();

  setTimeout((): void => {
    console.log(tag('timers'), 'setTimeout fired');
  }, 50);

  console.log(tag('sync'), '(请勿在生产代码里递归 process.nextTick, 会阻塞 I/O)');
}

// =====================================================================
// Demo 3：setTimeout vs setImmediate 在 I/O 回调中
// =====================================================================
function demo3_timeoutVsImmediateInIO(): void {
  console.log('\n=== Demo 3: setTimeout vs setImmediate (在 I/O 回调内) ===');

  const file = path.join(__dirname, 'event_loop_demo.js');
  fs.readFile(file, (_err: NodeJS.ErrnoException | null, _data: Buffer): void => {
    console.log(tag('poll'), 'I/O callback fired (poll phase)');

    setTimeout((): void => {
      console.log(tag('timers'), 'setTimeout 0 inside I/O');
    }, 0);

    setImmediate((): void => {
      console.log(tag('check'), 'setImmediate inside I/O');
    });
  });
}

// =====================================================================
// Demo 4：await vs process.nextTick
// =====================================================================
async function demo4_awaitVsNextTick(): Promise<void> {
  console.log('\n=== Demo 4: await vs process.nextTick ===');

  console.log(tag('sync'), 'before await');

  process.nextTick((): void => {
    console.log(tag('nextTick'), 'nextTick before await resolution');
  });

  await sleep(0, 'await');

  console.log(tag('microtask'), 'after await resolved');

  process.nextTick((): void => {
    console.log(tag('nextTick'), 'nextTick after await resolved');
  });

  setImmediate((): void => {
    console.log(tag('check'), 'setImmediate after await');
  });
}

// =====================================================================
// Demo 5：libuv 1.45.0 / Node 20+ 变化 — timers 在 poll 之后执行
// =====================================================================
function demo5_libuv145Change(): void {
  console.log('\n=== Demo 5: 同一 I/O 周期里 timers 与 check 顺序 ===');

  setImmediate((): void => {
    console.log(tag('check'), '(round 1) setImmediate fired');

    setTimeout((): void => {
      console.log(tag('timers'), '(round 1) setTimeout fired inside check');
    }, 0);

    setImmediate((): void => {
      console.log(tag('check'), '(round 2) setImmediate fired inside check');
    });
  });
}

async function main(): Promise<void> {
  console.log(`Node.js 版本: ${process.version}`);
  console.log(`平台: ${process.platform} (libuv 实现存在 Windows/Unix 差异)`);

  demo1_basicOrder();
  demo2_nextTickStarvation();
  demo3_timeoutVsImmediateInIO();
  await demo4_awaitVsNextTick();
  demo5_libuv145Change();

  console.log('\n=== 全部 demo 已派发，等待宏任务完成 ===');
}

main().catch((err: unknown): void => {
  console.error('fatal:', err);
  process.exit(1);
});
