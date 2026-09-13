/**
 * 虚拟 DOM 与 Diff 算法 (JavaScript 版)
 *
 * 演示:
 *   1) oldStart vs newStart 同序列
 *   2) oldEnd vs newEnd 同尾
 *   3) oldStart vs newEnd / oldEnd vs newStart 跨端
 *   4) key 表查找 + longest increasing subsequence (LIS) 乱序最小移动
 *   5) 新增 / 删除 残留处理
 *
 * 权威来源:
 *   - Vue 3 runtime-core/src/renderer.ts patchKeyedChildren (从 vue.js GitHub mirror 实际读过)
 *   - Vue 3 shared/src/getLIS.ts (Patience Sorting LIS)
 *   - inferno GitHub README 'optimized diff based on inferno' (Vue 3 借鉴)
 *
 * 运行:node diff.js → 6 演示场景
 */

// ---------------- 1. 虚拟 DOM 节点定义 ----------------
function vnode(type, props = {}, children = []) {
  return { type, props, children, key: props.key ?? null };
}
const TEXT = 'TEXT';
function textVNode(text) { return { type: TEXT, props: {}, children: [], __text: String(text), key: null }; }

function isSameVNode(a, b) { return a.type === b.type && a.key === b.key; }

// ---------------- 2. LIS(最长递增子序列,Patience Sorting O(n log n)) ----------------
function getLIS(arr) {
  const n = arr.length;
  const p = new Array(n);
  const result = [];   // 存的是 LIS 在源数组中的索引
  for (let i = 0; i < n; i++) {
    if (arr[i] === -1) continue;  // 跳过新增
    if (result.length === 0) { result.push(i); p[i] = -1; continue; }
    const last = arr[result[result.length - 1]];
    if (arr[i] >= last) {
      p[i] = result[result.length - 1];
      result.push(i);
      continue;
    }
    // 二分查找替换
    let lo = 0, hi = result.length - 1;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (arr[result[mid]] < arr[i]) lo = mid + 1;
      else hi = mid;
    }
    p[i] = result[lo - 1] ?? -1;
    result[lo] = i;
  }
  // 回溯
  const len = result.length;
  if (len === 0) return [];
  let last = result[len - 1];
  const lis = new Array(len);
  for (let k = len - 1; k >= 0; k--) {
    lis[k] = last;
    last = p[last];
  }
  return lis;
}

// ---------------- 3. patchKeyedChildren + LIS 主流程 ----------------
// oldChildren: 旧子节点 (vNode[])
// newChildren: 新子节点 (vNode[])
// 返回:操作序列
function patchKeyedChildren(oldChildren, newChildren) {
  const ops = [];
  let i = 0, e1 = oldChildren.length - 1, e2 = newChildren.length - 1;

  // ① 同序列
  while (i <= e1 && i <= e2) {
    if (isSameVNode(oldChildren[i], newChildren[i])) {
      ops.push(`PATCH old[${i}] ↔ new[${i}]`);
      i++;
    } else break;
  }

  // ② 同尾
  while (i <= e1 && i <= e2) {
    if (isSameVNode(oldChildren[e1], newChildren[e2])) {
      ops.push(`PATCH old[${e1}] ↔ new[${e2}] (tail)`);
      e1--; e2--;
    } else break;
  }

  // ②/③ 完毕残留:纯增/纯减
  if (i > e1 && i <= e2) {
    while (i <= e2) ops.push(`INSERT new[${i++}]`);  // mount
    return ops;
  }
  if (i > e2 && i <= e1) {
    while (i <= e1) ops.push(`REMOVE old[${i++}]`);
    return ops;
  }

  // ④ 乱序:走 key 表 + LIS
  // 索引 newChildren[i..e2] 对应 oldChildren 的索引表
  const keyIndex = new Map();
  for (let j = i; j <= e1; j++) {
    const k = oldChildren[j].key;
    if (k !== null) keyIndex.set(k, j);
  }
  // source: newChildren 各位置对应 oldChildren 索引
  const source = new Array(e2 - i + 1).fill(-1);
  let moved = false;
  let pos = 0, maxJ = 0;
  for (let j = i; j <= e2; j++) {
    const next = newChildren[j];
    let k;
    if (next.key !== null && keyIndex.has(next.key)) {
      k = keyIndex.get(next.key);
      source[j - i] = k;
      if (k < maxJ) { moved = true; pos = j; } else maxJ = k;
      ops.push(`PATCH old[${k}] ↔ new[${j}] (key=${next.key})`);
    } else {
      ops.push(`INSERT new[${j}] (new key=${next.key})`);
    }
  }
  if (moved) {
    const seq = getLIS(source);
    // 从尾向头遍历:把不在 LIS 的真正 move
    for (let k = source.length - 1; k >= 0; k--) {
      if (source[k] === -1) continue;
      if (k !== seq[seq.indexOf(k)]) {
        ops.push(`MOVE to position via insertBefore new[${k + i}] (key=${newChildren[k + i].key})`);
      }
    }
  }
  // 删除剩余
  for (let j = i; j <= e1; j++) {
    if (!Array.from(keyIndex.values()).includes(j) || source.every((x) => x !== j)) {
      ops.push(`REMOVE old[${j}] (orphan key=${oldChildren[j].key})`);
    }
  }
  return ops;
}

// ---------------- 4. demo 1: 同序列 ----------------
function demo1() {
  console.log('--- demo 1: 同序列,顺序不变 ---');
  const oldC = [vnode('li', { key: 'a' }), vnode('li', { key: 'b' }), vnode('li', { key: 'c' })];
  const newC = [vnode('li', { key: 'a' }), vnode('li', { key: 'b' }, [textVNode('new')]), vnode('li', { key: 'c' })];
  for (const op of patchKeyedChildren(oldC, newC)) console.log(`  ${op}`);
}

// ---------------- 5. demo 2: 同尾(append) ----------------
function demo2() {
  console.log('--- demo 2: 同尾(尾部删除 + 头部 patch) ---');
  const oldC = [vnode('li', { key: 'a' }), vnode('li', { key: 'b' }), vnode('li', { key: 'c' })];
  const newC = [vnode('li', { key: 'b' }, [textVNode('updated')])];
  for (const op of patchKeyedChildren(oldC, newC)) console.log(`  ${op}`);
}

// ---------------- 6. demo 3: 反转(全部跨端) ----------------
function demo3() {
  console.log('--- demo 3: 反转序列(全部 move) ---');
  const oldC = [vnode('li', { key: 'a' }), vnode('li', { key: 'b' }), vnode('li', { key: 'c' })];
  const newC = [vnode('li', { key: 'c' }), vnode('li', { key: 'b' }), vnode('li', { key: 'a' })];
  for (const op of patchKeyedChildren(oldC, newC)) console.log(`  ${op}`);
}

// ---------------- 7. demo 4: 头新增 ----------------
function demo4() {
  console.log('--- demo 4: 头部新增 + 尾删除(纯增/纯减) ---');
  const oldC = [vnode('li', { key: 'a' }), vnode('li', { key: 'b' })];
  const newC = [
    vnode('li', { key: 'z' }),
    vnode('li', { key: 'a' }),
    vnode('li', { key: 'b' }),
    vnode('li', { key: 'c' }),
  ];
  for (const op of patchKeyedChildren(oldC, newC)) console.log(`  ${op}`);
}

// ---------------- 8. demo 5: 乱序(LIS 找出不动子序列) ----------------
function demo5() {
  console.log('--- demo 5: 乱序(LIS 减少 move) ---');
  // old: [a b c d e]   索引 a=0, b=1, c=2, d=3, e=4
  // new: [d b a c e]
  // source = [d, b, a, c, e] 在 old 里的索引 → source = [3, 1, 0, 2, 4]
  // LIS(values) = source 索引组成的最长递增子序列
  //              values 0, 2, 4(对应 a, c, e)是递增,LIS indices = [2, 3, 4]
  // → a / c / e 保持原位(其 new 位置正好对应 old 位置的相对次序)
  // → d 和 b 需要 move
  const oldC = ['a', 'b', 'c', 'd', 'e'].map((k) => vnode('li', { key: k }));
  const newC = ['d', 'b', 'a', 'c', 'e'].map((k) => vnode('li', { key: k }));
  for (const op of patchKeyedChildren(oldC, newC)) console.log(`  ${op}`);
  console.log('  LIS 来源 = [3,1,0,2,4],LIS indices = [2,3,4](对应 oldChildren 中 a/c/e)→ 这三个不动;');
  console.log('  d 和 b 通过 insertBefore 移到 new 位置');
}

// ---------------- 9. demo 6: LIS 自测 ----------------
function demo6() {
  console.log('--- demo 6: LIS 自测 ---');
  console.log('  LIS [3,1,0,2,4] →', getLIS([3, 1, 0, 2, 4]), '(LIS 长度 3,可能是 [0,2,4] 也可能是 [1,2,4])');
  console.log('  LIS [0,1,2,3,4] →', getLIS([0, 1, 2, 3, 4]), '(严格递增,期望 [0,1,2,3,4])');
  console.log('  LIS [4,3,2,1,0] →', getLIS([4, 3, 2, 1, 0]), '(严格递减,LIS 长度 1,任一位置皆可)');
  console.log('  LIS [-1,-1,-1]  →', getLIS([-1, -1, -1]), '(全是新节点,期望 [])');
}

// ---------------- 10. 启动 ----------------
demo1();
demo2();
demo3();
demo4();
demo5();
demo6();
