// Vue 3 Proxy 响应式最小实现 - 演示 (JavaScript)
// 与 ts/demo.ts 完全一致的演示脚本

import { reactive, ref, effect, _debugDumpSubscribers } from './index.js';

console.log('=== Demo 1: 基本响应式 (reactive) ===');
const state = reactive({ count: 0, name: 'Alice' });

let rendered = '';
effect(() => {
  rendered = `${state.name}: ${state.count}`;
});
console.log('[initial]', rendered);  // Alice: 0

state.count = 1;
console.log('[count=1]', rendered);  // Alice: 1

state.name = 'Bob';
console.log('[name=Bob]', rendered);  // Bob: 1

state.count = state.count;  // 同值赋值，不触发
console.log('[no change]', rendered); // Bob: 1

console.log();
console.log('=== Demo 2: 嵌套响应式 (deep reactive) ===');
const user = reactive({
  profile: { age: 20, address: { city: 'Beijing' } },
});
let snapshot = '';
effect(() => {
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
let doubled = 0;
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
_debugDumpSubscribers(cart);
console.log('[A,B initial]', totalA, totalB); // 20, 12

cart.price = 20;
console.log('[price=20]', totalA, totalB);     // 40, 22

console.log();
console.log('=== Demo 5: 条件分支与 cleanup ===');
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

// cleanup 后 a/b 不再被订阅
cond.a = 100;
console.log('[flag=false, a=100]', branchResult);  // 仍然是 'flag is false'
cond.b = 200;
console.log('[flag=false, b=200]', branchResult);  // 仍然是 'flag is false'

cond.flag = true;
console.log('[flag=true again]', branchResult);  // A+B=300

cond.a = 5;
console.log('[a=5]', branchResult);              // A+B=207

console.log();
console.log('=== Demo 6: 嵌套 effect (栈式 activeEffect) ===');
const outer = reactive({ x: 1 });
const inner = reactive({ y: 10 });

effect(() => {
  console.log('  outer run, activeEffect = outer');
  effect(() => {
    console.log('  inner run, activeEffect = inner');
    console.log('  inner reads inner.y =', inner.y);
  });
  console.log('  outer reads outer.x =', outer.x);
});
outer.x = 2;
console.log('[after outer.x=2] done');

console.log();
console.log('=== All demos finished ===');