/**
 * 虚拟 DOM 与 Diff 算法 — TypeScript 版
 *
 * 与 JS 版(diff.js)等价,加类型约束。
 * 演示:同序 / 同尾 / 跨端 / 乱序 + LIS 的 patchKeyedChildren 五步流程。
 *
 * 运行:npx tsc --noEmit diff.ts
 */

// ---------------- 类型 ----------------
export interface VNode {
  type: string;
  props: Record<string, unknown> & { key?: string | number | null };
  children: VNode[];
  key: string | number | null;
}

export const TEXT_NODE = '__TEXT__';

export interface TextVNode extends Omit<VNode, 'type'> {
  type: typeof TEXT_NODE;
  text: string;
}

export type ChildVNode = VNode | TextVNode;

export type DiffOp =
  | { kind: 'patch'; oldIndex: number; newIndex: number; tail?: boolean }
  | { kind: 'insert'; newIndex: number }
  | { kind: 'remove'; oldIndex: number }
  | { kind: 'move'; newIndex: number };

// ---------------- vnode 工厂 ----------------
export function vnode(type: string, props: Record<string, unknown> & { key?: string | number | null } = {}, children: VNode[] = []): VNode {
  return { type, props, children, key: props.key ?? null };
}
export function textVNode(text: string): TextVNode {
  return { type: TEXT_NODE, props: {}, children: [], text, key: null };
}
export function isSameVNode(a: ChildVNode, b: ChildVNode): boolean {
  return a.type === b.type && a.key === b.key;
}

// ---------------- LIS ----------------
export function getLIS(arr: number[]): number[] {
  const n = arr.length;
  const p = new Array<number>(n).fill(-1);
  const result: number[] = [];
  for (let i = 0; i < n; i++) {
    if (arr[i] === -1) continue;
    if (result.length === 0) { result.push(i); continue; }
    const lastIdx = result[result.length - 1];
    if (arr[i] >= arr[lastIdx]) {
      p[i] = lastIdx;
      result.push(i);
      continue;
    }
    let lo = 0, hi = result.length - 1;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (arr[result[mid]] < arr[i]) lo = mid + 1;
      else hi = mid;
    }
    p[i] = result[lo - 1] ?? -1;
    result[lo] = i;
  }
  const len = result.length;
  if (len === 0) return [];
  let last = result[len - 1];
  const lis = new Array<number>(len);
  for (let k = len - 1; k >= 0; k--) {
    lis[k] = last;
    last = p[last];
  }
  return lis;
}

// ---------------- patchKeyedChildren ----------------
export function patchKeyedChildren(oldC: ChildVNode[], newC: ChildVNode[]): DiffOp[] {
  const ops: DiffOp[] = [];
  let i = 0, e1 = oldC.length - 1, e2 = newC.length - 1;

  // ① 同序列
  while (i <= e1 && i <= e2 && isSameVNode(oldC[i], newC[i])) {
    ops.push({ kind: 'patch', oldIndex: i, newIndex: i });
    i++;
  }

  // ② 同尾
  while (i <= e1 && i <= e2 && isSameVNode(oldC[e1], newC[e2])) {
    ops.push({ kind: 'patch', oldIndex: e1, newIndex: e2, tail: true });
    e1--; e2--;
  }

  // ②/③ 完毕残留
  if (i > e1 && i <= e2) {
    while (i <= e2) ops.push({ kind: 'insert', newIndex: i++ });
    return ops;
  }
  if (i > e2 && i <= e1) {
    while (i <= e1) ops.push({ kind: 'remove', oldIndex: i++ });
    return ops;
  }

  // ④ 乱序:key 表 + source + LIS
  const keyIndex = new Map<string | number | null, number>();
  for (let j = i; j <= e1; j++) {
    const k = oldC[j].key;
    if (k !== null) keyIndex.set(k, j);
  }
  const source = new Array<number>(e2 - i + 1).fill(-1);
  let moved = false;
  let maxJ = 0;
  const keySet = new Set<string | number | null>();
  for (let j = i; j <= e2; j++) {
    const next = newC[j];
    const k = keyIndex.get(next.key);
    if (k !== undefined) {
      source[j - i] = k;
      if (k < maxJ) moved = true;
      else maxJ = k;
      keySet.add(next.key);
      ops.push({ kind: 'patch', oldIndex: k, newIndex: j });
    } else {
      ops.push({ kind: 'insert', newIndex: j });
    }
  }
  if (moved) {
    const seq = getLIS(source);
    for (let k2 = source.length - 1; k2 >= 0; k2--) {
      if (source[k2] === -1) continue;
      if (!seq.includes(k2)) {
        ops.push({ kind: 'move', newIndex: k2 + i });
      }
    }
  }
  // 清理老节点中没被引用的
  for (let j = i; j <= e1; j++) {
    if (!keySet.has(oldC[j].key)) {
      ops.push({ kind: 'remove', oldIndex: j });
    }
  }
  return ops;
}

// ---------------- demo ----------------
export function runDemos(): void {
  function go(name: string, oldC: ChildVNode[], newC: ChildVNode[]): void {
    console.log(`--- ${name} ---`);
    const ops = patchKeyedChildren(oldC, newC);
    for (const op of ops) console.log(`  ${JSON.stringify(op)}`);
  }

  go('demo 1: 同序列', [
    vnode('li', { key: 'a' }), vnode('li', { key: 'b' }), vnode('li', { key: 'c' }),
  ], [
    vnode('li', { key: 'a' }), vnode('li', { key: 'b' }, [textVNode('new')]), vnode('li', { key: 'c' }),
  ]);

  go('demo 2: 同尾(尾部删除)', [
    vnode('li', { key: 'a' }), vnode('li', { key: 'b' }), vnode('li', { key: 'c' }),
  ], [vnode('li', { key: 'b' }, [textVNode('updated')])]);

  go('demo 3: 反转', [
    vnode('li', { key: 'a' }), vnode('li', { key: 'b' }), vnode('li', { key: 'c' }),
  ], [vnode('li', { key: 'c' }), vnode('li', { key: 'b' }), vnode('li', { key: 'a' })]);

  go('demo 4: 头部新增+尾部新增', [
    vnode('li', { key: 'a' }), vnode('li', { key: 'b' }),
  ], [
    vnode('li', { key: 'z' }), vnode('li', { key: 'a' }),
    vnode('li', { key: 'b' }), vnode('li', { key: 'c' }),
  ]);

  go('demo 5: 乱序 [a b c d e] → [d b a c e]', ['a','b','c','d','e'].map((k) => vnode('li', { key: k })),
    ['d','b','a','c','e'].map((k) => vnode('li', { key: k })));

  console.log('--- demo 6: LIS 自测 ---');
  console.log('  LIS [3,1,0,2,4] →', getLIS([3, 1, 0, 2, 4]), '(期望 [0,2,4])');
  console.log('  LIS [0,1,2,3,4] →', getLIS([0, 1, 2, 3, 4]));
  console.log('  LIS [4,3,2,1,0] →', getLIS([4, 3, 2, 1, 0]));
}
