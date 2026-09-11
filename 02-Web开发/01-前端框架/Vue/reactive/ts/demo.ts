// Vue 3 Proxy 响应式最小实现 - 演示 (TypeScript)
// 参考：cn.vuejs.org/guide/extras/reactivity-in-depth.html

import { reactive, ref, effect, _debugDumpSubscribers } from './index.ts';

console.log('=== Demo 1: 基本响应式 (reactive) ===');
const state = reactive({ count: 0, name: 'Alice' });

let rendered: string = '';
effect(() => {
  // 每次 count/name 变化，此函数会自动重跑
  rendered = `${state.name}: ${state.count}`;
});
console.log('[initial]', rendered);  // Alice: 0

state.count = 1;
console.log('[count=1]', rendered);  // Alice: 1

state.name = 'Bob';
console.log('[name=Bob]', rendered);  // Bob: 1

state.count = state.count;  // 同值赋值，不触发 (我们的 set 做了 !== 检查)
console.log('[no change]', rendered); // Bob: 1

console.log();
console.log('=== Demo 2: 嵌套响应式 (deep reactive) ===');
const user = reactive({
  profile: { age: 20, address: { city: 'Beijing' } },
});
let snapshot: string = '';
effect(() => {
  // 三层嵌套都自动转为 reactive
  snapshot = `${user.profile.age}/${user.profile.address.city}`;
});
console.log('[initial]', snapshot);  // 20/Beijing

user.profile.age = 21;
console.log('[age=21]', snapshot);    // 21/Beijing

user.profile.address.city = 'Shanghai';
console.log('[city=Shanghai]', snapshot);  // 21/Shanghai

console.log();
console.log('=== Demo 3: ref (原始值响应式) ===');
const rcount = ref(0);
let doubled: number = 0;
effect(() => {
  doubled = rcount.value * 2;
});
console.log('[initial]', doubled);    // 0

rcount.value = 5;
console.log('[value=5]', doubled);    // 10

rcount.value = 5;  // 同值不触发
console.log('[same value]', doubled); // 10

console.log();
console.log('=== Demo 4: 多个 effect 共享依赖 + Set 去重 ===');
const cart = reactive({ price: 10, qty: 2 });

let totalA = 0;
effect(() => { totalA = cart.price * cart.qty; });
let totalB = 0;
effect(() => { totalB = cart.price + cart.qty; });
// 即便 cart.price 在两个 effect 中都被读取，订阅 Set 自动去重
_debugDumpSubscribers(cart);
console.log('[A,B initial]', totalA, totalB); // 20, 12

cart.price = 20;
console.log('[price=20]', totalA, totalB);     // 40, 22

console.log();
console.log('=== Demo 5: 条件分支与 cleanup ===');
// 关键 demo：cleanup 机制保证 effect 重跑前先清掉旧订阅
// 没有 cleanup 时，flag=false 后访问 a+b 的 effect 不会重新订阅 a/b，
// 但仍然会被 a/b 的变化错误触发 -> 视图"看起来"一致但内部已 stale
const cond = reactive({ flag: true, a: 1, b: 2 });
let branchResult = '';
effect(() => {
  if (cond.flag) {
    branchResult = `A+B=${cond.a + cond.b}`;
  } else {
    branchResult = 'flag is false';
  }
});
console.log('[flag=true]', branchResult);     // A+B=3

cond.flag = false;
console.log('[flag=false]', branchResult);    // flag is false

// 此时若改 a/b，branchResult 不应变化（cleanup 后不再订阅 a/b）
cond.a = 100;
console.log('[flag=false, a=100]', branchResult);  // 仍然是 'flag is false'
cond.b = 200;
console.log('[flag=false, b=200]', branchResult);  // 仍然是 'flag is false'

// 再次打开 flag，effect 重跑，重新订阅 a/b
cond.flag = true;
console.log('[flag=true again]', branchResult);  // A+B=300

cond.a = 5;
console.log('[a=5]', branchResult);              // A+B=207

console.log();
console.log('=== Demo 6: 嵌套 effect (栈式 activeEffect) ===');
const outer = reactive({ x: 1 });
const inner = reactive({ y: 10 });

const outerEff = effect(() => {
  console.log('  outer run, activeEffect = outer');
  // 内层 effect：必须把自己的 fn 设为 activeEffect，结束后恢复外层
  effect(() => {
    console.log('  inner run, activeEffect = inner');
    console.log('  inner reads inner.y =', inner.y);
  });
  // 外层不应读到 inner.y 作为自己的依赖
  console.log('  outer reads outer.x =', outer.x);
});
outer.x = 2;  // 应触发 outer，但 inner 不应触发（因为 inner 已运行完，且没订阅 outer.x）
console.log('[after outer.x=2] done');

console.log();
console.log('=== All demos finished ===');