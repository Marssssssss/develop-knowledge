// check.js —— DataLoader 语义自检。运行：node check.js
'use strict';

const assert = require('assert');
const { DataLoader } = require('./dataloader');

let COUNT = 0;
let FAIL = 0;
function ck(label, cond) {
  COUNT += 1;
  if (!cond) {
    FAIL += 1;
    console.log('FAIL: ' + label);
  }
}

const USERS = {
  1: { id: 1, name: 'Alice' },
  2: { id: 2, name: 'Bob' },
  3: { id: 3, name: 'Carol' },
};

/** 后端故意乱序返回、且缺 key —— 用来验证 batch 函数必须重排对齐。 */
function batchGetUsers(keys) {
  const found = {};
  for (const k of [3, 1, 2]) {
    if (keys.includes(k) && USERS[k]) found[k] = USERS[k];
  }
  return keys.map((k) => (found[k] ? found[k] : null));
}

async function main() {
  // --- 1. 同一 tick 内的 load 合并成一个批次 -------------------------------
  {
    const loader = new DataLoader(batchGetUsers);
    const [a, b, c] = await Promise.all([
      loader.load(1),
      loader.load(2),
      loader.load(3),
    ]);
    ck('一次 tick 内三个 load 合成 1 个批次', loader.batchCount === 1);
    ck('批次里的 keys 顺序即 load 顺序', String(loader.batches[0]) === '1,2,3');
    ck('结果按 keys 顺序返回', a.name === 'Alice' && b.name === 'Bob' && c.name === 'Carol');
  }

  // --- 2. batch 函数必须长度相等、按索引对齐（官方两条硬约束）---------------
  {
    const loader = new DataLoader((keys) => keys.map((k) => USERS[k] || null));
    const vals = await loader.loadMany([3, 1, 2]);
    ck('乱序请求也按请求顺序返回', vals.map((v) => v.id).join(',') === '3,1,2');
    const missing = await loader.load(99);
    ck('缺值用 null 占位而不是缩短数组', missing === null);
  }
  {
    const loader = new DataLoader((keys) => keys.slice(0, 1)); // 长度不符
    let threw = false;
    try {
      await loader.loadMany([1, 2]);
    } catch (e) {
      threw = true;
    }
    ck('返回值长度不符 → 整批拒绝', threw);
  }

  // --- 3. 缓存：命中缓存的 key 不进批次 ------------------------------------
  {
    const loader = new DataLoader(batchGetUsers);
    await loader.load(1);
    const before = loader.batchCount;
    await loader.loadMany([1, 2]); // 1 已缓存，只有 2 进新批次
    ck('重复 load 不产生新批次', loader.batchCount === before + 1);
    ck('新批次只包含未缓存的 key', String(loader.batches[1]) === '2');
  }

  // --- 4. 缓存命中但仍等当前批次：依赖 load 才能落在同一 tick --------------
  {
    const loader = new DataLoader(batchGetUsers);
    loader.prime(1, USERS[1]);
    const p = loader.load(1);
    const q = loader.load(2);
    ck('prime 后 load 不进批次（尚未 dispatch）', loader.batchCount === 0);
    await Promise.all([p, q]);
    ck('缓存值与未缓存值同时 resolve', loader.batchCount === 1);
  }

  // --- 5. cache: false → keys 会重复 ---------------------------------------
  {
    const loader = new DataLoader(batchGetUsers, { cache: false });
    await Promise.all([loader.load('A'), loader.load('B'), loader.load('A')]);
    ck('关闭缓存后 keys 含重复', String(loader.batches[0]) === 'A,B,A');
    ck('关闭缓存后只有一个批次', loader.batchCount === 1);
  }

  // --- 6. maxBatchSize 切分批次 --------------------------------------------
  {
    const loader = new DataLoader(batchGetUsers, { maxBatchSize: 2 });
    await loader.loadMany([1, 2, 3, 4, 5]); // 全不同 key，专测切分
    ck('maxBatchSize=2 把 5 个 load 切成 3 批', loader.batchCount === 3);
    ck('每批不超过 2', loader.batches.every((b) => b.length <= 2));
  }
  {
    const loader = new DataLoader(batchGetUsers, { batch: false });
    await loader.loadMany([1, 2]);
    ck('batch:false 等价于 maxBatchSize:1', loader.batchCount === 2);
  }

  // --- 7. 错误语义 ---------------------------------------------------------
  {
    const loader = new DataLoader(async () => {
      throw new Error('boom');
    });
    let caught = null;
    try {
      await loader.load(1);
    } catch (e) {
      caught = e;
    }
    ck('batch 抛错 → 不缓存', caught !== null);
    await loader.load(2).catch(() => {});
    ck('抛错后重试会再打一次后端', loader.batchCount === 2);
  }
  {
    const perValueErr = new Error('no such user');
    const loader = new DataLoader((keys) => keys.map(() => perValueErr));
    const v = await loader.load(7);
    ck('Error 实例作为值返回（不抛）', v instanceof Error);
    const before = loader.batchCount;
    await loader.load(7);
    ck('Error 实例会被缓存，不再打后端', loader.batchCount === before);
  }

  // --- 8. clear / clearAll --------------------------------------------------
  {
    const loader = new DataLoader(batchGetUsers);
    await loader.load(1);
    loader.clear(1);
    await loader.load(1);
    ck('clear 之后重新打后端', loader.batchCount === 2);
  }

  // --- 9. cacheKeyFn：对象键按某字段归并 -----------------------------------
  {
    const loader = new DataLoader(
      (keys) => keys.map((k) => USERS[k.id]),
      { cacheKeyFn: (k) => k.id }
    );
    await Promise.all([loader.load({ id: 1 }), loader.load({ id: 1 })]);
    ck('cacheKeyFn 让两个不同对象视为同一 key', loader.batches[0].length === 1);
  }

  // --- 10. 同一 tick 内的重复 key 也被缓存去重 ------------------------------
  {
    const loader = new DataLoader(batchGetUsers);
    await loader.loadMany([1, 1, 2, 2, 3]);
    ck('同一 tick 内重复 key 只出现一次', String(loader.batches[0]) === '1,2,3');
    ck('去重后仍是单个批次', loader.batchCount === 1);
  }

  console.log('assertions=' + COUNT + ' fail=' + FAIL);
  if (FAIL) process.exit(1);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
