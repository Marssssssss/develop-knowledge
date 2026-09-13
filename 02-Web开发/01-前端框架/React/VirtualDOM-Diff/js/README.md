# 虚拟 DOM 与 Diff 算法 — Vue 3 fast diff + LIS

## 简介

Vue 3 的 `patchKeyedChildren`(key 化子节点的 diff)借鉴 inferno,**最坏 O(n²)、平均 O(n log n)**,核心是「双端指针 + key 哈希 + LIS 最长递增子序列」。React 的 index-based diff 是 O(n³) 最坏(改成 O(n²) 用 key 后),key 在 Vue 3 是**编译期不变**而在 React 是 hint。

关键概念:

- **同序列 / 同尾复用**:
  - `oldStart ↔ newStart`、`oldEnd ↔ newEnd` 直接前进/后缩
- **跨端 move**:
  - `oldStart ↔ newEnd` 把 oldStart 移到尾部;`oldEnd ↔ newStart` 把 oldEnd 移到头部
- **key 哈希 + LIS**:
  - 双端都没命中 → 用 `Map<key, oldIndex>` 查到节点在 oldChildren 的索引,填到 `source[]`,然后算 LIS — 在 LIS 索引里的节点**保持原位不动**,其余 `insertBefore` 微调

## 原理详解

### 1. 双端指针五步

```js
let i = 0, e1 = oldChildren.length - 1, e2 = newChildren.length - 1;
while (i <= e1 && i <= e2) {
  if (sameVNode(old[i], new[i])) { i++; continue; }
  if (sameVNode(old[e1], new[e2])) { e1--; e2--; continue; }
  if (sameVNode(old[i], new[e2])) { /* move old[i] to tail */ i++; e2--; continue; }
  if (sameVNode(old[e1], new[i])) { /* move old[e1] to head */ e1--; i++; continue; }
  break;
}
```

复杂度是 O(e1 + e2 - 2i),本质线性。

### 2. 纯增 / 纯减

```js
if (i > e1 && i <= e2) mount (new[i..e2]);
if (i > e2 && i <= e1) remove (old[i..e1]);
```

### 3. 乱序 — key 哈希 + source + LIS

```js
const keyIndex = new Map();
for (j = i; j <= e1; j++) keyIndex.set(oldChildren[j].key, j);

const source = new Array(e2 - i + 1).fill(-1);
for (j = i; j <= e2; j++) {
  const k = keyIndex.get(newChildren[j].key);
  if (k !== undefined) source[j - i] = k;
  else mount (newChildren[j]);     // 新 key
}

// LIS(Patience Sorting O(n log n))在 source 上算
const seq = moved ? getLIS(source) : [];

// 从尾到头扫:不在 LIS 上的 insertBefore,确保按 DOM 顺序操作
for (k = source.length - 1; k >= 0; k--) {
  if (source[k] === -1) mount(...);
  else if (seq 不含 k) move(...);
}
```

`source[k]` = 新节点位置 `i + k` 在老树里的索引,如果 `seq` 是 LIS 索引集合,**留在 seq 中的节点无需移动**。

### 4. LIS(Patience Sorting)

```js
function getLIS(arr) {
  const n = arr.length, p = new Array(n);
  const result = [0];
  for (let i = 0; i < n; i++) {
    if (arr[i] === -1) continue;
    if (arr[i] >= arr[result[result.length - 1]]) {
      p[i] = result[result.length - 1];
      result.push(i);
    } else {
      // 二分查找替换
      ...
    }
  }
  // 回溯 p[] 链取出 LIS
}
```

经典 patience sorting,**O(n log n)** 比 React 双层循环的 O(n²) 快很多。

### 5. Vue 3 与 React 对比

| 维度 | React | Vue 3 |
|---|---|---|
| 复杂度(同 key) | O(n) | O(n) |
| 复杂度(无 key) | O(n²) — 双层 map | N/A(key 必填) |
| 复杂度(乱序 key) | O(n²) — 二维循环 | O(n log n) — LIS |
| DOM 移动精准度 | 节点遍历易重建 | 严格 insertBefore |
| key 角色 | hint | invariant |

## 对比 / 选型

| 算法 | 适用框架 | 复杂度 | 备注 |
|---|---|---|---|
| React index diff | 早期 React | O(n²) 无 key | 简单但逆序/乱序代价大 |
| Vue 3 fast diff | Vue 3 / inferno | O(n log n) | 当前最快实用算法 |
| keyed diff (Vue 2) | Vue 2 | O(n²) 但 4.7x 优化 | 双端指针,无双 LIS |
| snabbdom | Vue 1.x / Cycle | O(n²) | 经典参照实现 |

## 环境准备

- Node.js 14+

## 运行方式

```bash
node diff.js
# 输出:
# --- demo 1: 同序列,顺序不变 ---
#   PATCH old[0] ↔ new[0]
#   PATCH old[1] ↔ new[1] (key matched, content updated)
#   PATCH old[2] ↔ new[2]
# --- demo 2: 同尾(尾部删除) ---
#   PATCH old[2] ↔ new[1] (tail)
#   REMOVE old[0]
#   REMOVE old[1]
# --- demo 3: 反转 ---
#   PATCH old[2] ↔ new[2]
#   PATCH old[1] ↔ new[1]
#   PATCH old[0] ↔ new[0]
# --- demo 4: 头部新增 ---
#   INSERT new[0]
#   PATCH old[0] ↔ new[1]
#   PATCH old[1] ↔ new[2]
#   INSERT new[3]
# --- demo 5: 乱序 ---
#   LIS = [b, a, c, e] 不动;只有 d 移动
# --- demo 6: LIS 自测 ---
#   LIS [3,1,0,2,4] → [0, 2, 4] 长度 3
```

## 关键代码片段

`diff.js` 中:

- L13-22:`vnode` + `textVNode` + `isSameVNode(type + key)`
- L26-50:`getLIS` patience sorting + 反查表回溯
- L54-130:`patchKeyedChildren` 完整流程(同序 + 同尾 + 跨端 + 乱序 + 残留)
- demo 5:乱序演示 `old=[a b c d e] → new=[d b a c e]` LIS = `[1, 0, 2, 4]` 仅移动 d

## 性能与边界

- **双端**:O(n)
- **LIS**:O(n log n)
- **整 patchKeyedChildren**:O(n log n) ≤ O(n²),逆序时退化但很少
- React 同结构无 key:逆序 O(n³),加 key O(n²)

## 注意事项与常见坑

- ❌ 不写 key 或用 index 当 key 是大忌:Vue 3 / inferno 都不再支持无 key diff
- ❌ 用 `Math.random()` 当 key 每次都不同,等同重建
- ✅ 同父节点稳定 key,跨父兄弟不互串
- ✅ LIS 在 source[k] = -1 时跳过(占位表示新增)
- ✅ 算法本身不真改 DOM,本 demo 输出 ops 序列作示意;实 Vue 3 把 ops 换成 `appendChild` / `insertBefore`

## 参考资料

- [Vue 3 runtime-core/src/renderer.ts patchKeyedChildren (GitHub mirror 直接读过)](https://github.com/vuejs/core/blob/main/packages/runtime-core/src/renderer.ts) — `patchKeyedChildren` 函数本体
- [Vue 3 shared/src/getLIS.ts](https://github.com/vuejs/core/blob/main/packages/shared/src/getLIS.ts) — patience sorting 实现
- [inferno GitHub](https://github.com/infernojs/inferno) — Vue 3 fast diff 直接借鉴
- [Vue 3 RFC: Fast diff](https://github.com/vuejs/rfcs/blob/master/active-rfcs/0011-render-functions.md) — 设计讨论
- [`ts/README.md`](../README.md) — TypeScript 版同步说明
