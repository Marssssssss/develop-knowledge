/**
 * React Fiber 架构最小实现 (JavaScript 版)
 *
 * 核心概念:
 *   1. FiberNode 链表: child/sibling/return 形成可中断的工作单元
 *   2. 双缓冲 (current / workInProgress): commit 后互换
 *   3. 调度与时间切片: shouldYield 让出主线程
 *   4. 两阶段: render (可中断) + commit (同步不可中断)
 *
 * 权威来源:
 *   - react.dev/learn/render-and-commit (Trigger / Render / Commit 三阶段)
 *   - Lin Clark 的 Code-Slinger "A Cartoon Intro to Fiber"
 *     https://www.youtube.com/watch?v=ZCuYPiUIONs
 *   - Andrew Clark (acdlite) React Fiber Architecture (公开镜像多处)
 *   - 实现参考 react/packages/react-reconciler/src/ReactFiber.js (简化版)
 *
 * 运行:node fiber.js → 输出"render OK / commit OK: <text>"
 */

// ---------------- 1. JSX → vnode-like 描述 ----------------
// 我们不实现完整的 JSX,直接以函数模拟组件(host 组件:文本/元素/函数组件)
function h(type, props = {}, ...children) {
  // 像 React.createElement 一样,children 也放到 props 里,统一 props.children 读
  const flat = children.flat();
  return { type, props: { ...props, children: flat }, children: flat };
}

// ---------------- 2. FiberNode ----------------
// Fiber 是一棵通过 child/sibling/return 串联的链表树(代替原来的 N 叉对象树,可中断遍历)
function createFiber(vnode, returnFiber) {
  const fiber = {
    type: vnode.type,
    key: vnode.key,
    props: vnode.props,
    stateNode: null,
    child: null,
    sibling: null,
    return: returnFiber,
    alternate: null,
    pendingProps: vnode.props,
    memoizedState: null,
    effectTag: 'PLACEMENT',
    nextEffect: null,
    firstEffect: null,    // effect 链表头(commit 阶段开始遍历)
    lastEffect: null,     // effect 链表尾(append 用)
  };
  return fiber;
}

// ---------------- 3. 双缓冲切换 ----------------
// currentFiber 与 workInProgressFiber 互为 alternate
function createWorkInProgress(currentFiber, pendingProps) {
  let wipFiber = currentFiber.alternate;
  if (wipFiber === null) {
    wipFiber = {
      ...createFiber({ type: currentFiber.type, props: pendingProps }, currentFiber.return),
      alternate: currentFiber,
      pendingProps,
    };
    currentFiber.alternate = wipFiber;
  } else {
    wipFiber.pendingProps = pendingProps;
    wipFiber.effectTag = 'UPDATE';
    wipFiber.child = wipFiber.sibling = null;
  }
  return wipFiber;
}

// ---------------- 4. render 阶段:beginWork + completeWork ----------------
// 可中断的关键:任何步骤执行完后都检查 shouldYield()
let nextUnitOfWork = null;
let wipRoot = null;
let currentRoot = null;

function shouldYield() {
  // 简化为:永远不主动让出;真实 React 通过 scheduler.shouldYield 检查 5ms 时间片
  return false;
}

function beginWork(wipFiber) {
  // 函数组件
  if (typeof wipFiber.type === 'function') {
    return updateFunctionComponent(wipFiber);
  }
  // host 组件 (字符串类型如 'div' 'h1')
  if (typeof wipFiber.type === 'string') {
    return updateHostComponent(wipFiber);
  }
  throw new Error('Unknown fiber type: ' + wipFiber.type);
}

function updateFunctionComponent(wipFiber) {
  const children = wipFiber.props.children || [];
  reconcileChildren(wipFiber, children);
  return wipFiber.child;
}

function updateHostComponent(wipFiber) {
  if (wipFiber.stateNode === null) {
    // 简化:不创建真 DOM,只挂标记 + tag 类型
    wipFiber.stateNode = { type: wipFiber.type, props: wipFiber.props, children: [] };
  }
  const children = wipFiber.props.children || [];
  reconcileChildren(wipFiber, children);
  return wipFiber.child;
}

// ---------------- 5. reconcileChildren / 简化版 diff ----------------
// 同父 vnode 子节点按顺序对齐:type 相同就复用 + UPDATE,否则 PLACEMENT 新增
function reconcileChildren(returnFiber, children) {
  let prevSibling = null;
  let oldFiber = returnFiber.alternate ? returnFiber.alternate.child : null;

  for (let i = 0; i < children.length; i++) {
    const child = children[i];
    let newFiber = null;
    const sameType = oldFiber && oldFiber.type === child.type;

    if (sameType) {
      // 复用旧 Fiber
      newFiber = createWorkInProgress(oldFiber, child.props);
      newFiber.effectTag = 'UPDATE';
    } else if (child && child.type !== undefined) {
      // 新建
      newFiber = createFiber(child, returnFiber);
      newFiber.stateNode = typeof child.type === 'string'
        ? { type: child.type, props: child.props, children: [] }
        : null;
      newFiber.effectTag = 'PLACEMENT';
    }

    if (oldFiber) oldFiber = oldFiber.sibling;
    if (newFiber === null) continue;

    if (prevSibling === null) {
      returnFiber.child = newFiber;
    } else {
      prevSibling.sibling = newFiber;
    }
    prevSibling = newFiber;
  }
}

// ---------------- 6. completeWork:旧版,改名为 completeUnitOfWork 已迁移;保留空函数避免外部引用报错 ----------------
function completeWork(wipFiber) {
  return completeUnitOfWork(wipFiber);
}

// ---------------- 7. workLoop 主循环 ----------------
function workLoop() {
  while (nextUnitOfWork !== null && !shouldYield()) {
    nextUnitOfWork = performUnitOfWork(nextUnitOfWork);
  }
  if (nextUnitOfWork === null && wipRoot !== null) {
    commitRoot();
  }
}

function performUnitOfWork(wipFiber) {
  const next = beginWork(wipFiber);
  if (next === null) {
    // 没 child → 自下而上 completeWork,沿途收集 effect,找下一个未处理的兄弟
    let cur = wipFiber;
    while (cur !== null) {
      const nextFiber = completeUnitOfWork(cur);
      if (nextFiber !== null) return nextFiber;
      // 这一支都处理完了,继续向上
      cur = cur.return;
    }
    return null;
  }
  return next;
}

function completeUnitOfWork(wipFiber) {
  const returnFiber = wipFiber.return;
  // effect 链表 append (真实 React 在这做 createInstance / appendAllChildren)
  if (returnFiber !== null) {
    if (wipFiber.effectTag === 'PLACEMENT' || wipFiber.effectTag === 'UPDATE') {
      if (returnFiber.firstEffect === null) {
        returnFiber.firstEffect = wipFiber;
      } else {
        returnFiber.lastEffect.nextEffect = wipFiber;
      }
      returnFiber.lastEffect = wipFiber;
    }
  }
  // 关键:有兄弟回兄弟,无兄弟回 parent(继续外层循环再判断)
  if (wipFiber.sibling) return wipFiber.sibling;
  return null;
}

// ---------------- 8. commitRoot:同步、不可中断、刷新 DOM ----------------
function commitRoot() {
  let effect = wipRoot.firstEffect;
  const ops = [];
  while (effect !== null) {
    ops.push(effect.effectTag);
    applyEffect(effect);
    effect = effect.nextEffect;
  }
  console.log('[render OK] effect ops:', ops.join(', ') || '(empty)');
  currentRoot = wipRoot;
  wipRoot = null;

  // 验证最终 fiber 树结构
  const tree = [];
  walkTree(currentRoot, tree);
  console.log('[commit OK] tree:', tree.join(' → '));
}

function walkTree(f, out, depth = 0) {
  if (!f) return;
  const tag = f.stateNode && f.stateNode.type ? f.stateNode.type : f.type;
  if (typeof tag === 'string') out.push(`[${tag}]`);
  else if (typeof tag === 'function') out.push(`[fn]`);
  walkTree(f.child, out, depth + 1);
  if (f.sibling) walkTree(f.sibling, out, depth);
}

function applyEffect(fiber) {
  // 简化:只收集 ops,不真创建 DOM (实际 React 用 document.createElement 等)
}

// ---------------- 9. render 入口 ----------------
function render(vnode, container) {
  const newFiber = createFiber(vnode, null);
  wipRoot = newFiber;
  currentRoot = currentRoot || { child: null, firstEffect: null, lastEffect: null }; // 首次占位
  newFiber.alternate = currentRoot;
  nextUnitOfWork = wipRoot;
  workLoop();
  return container;
}

// ---------------- 10. demo ----------------
const tree = h(
  'div',
  { id: 'app' },
  h('h1', {}, 'Hello Fiber'),
  h('p', {}, 'A tiny Fiber reconciler'),
  h('section', {},
    h('span', {}, 'one'),
    h('span', {}, 'two'),
  ),
);
render(tree, {});
