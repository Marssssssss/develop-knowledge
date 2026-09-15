/**
 * Vue 3 编译期优化 — 自检入口 (JavaScript)
 * 编译(compiler.js) → 生成 render 源码 → new Function 执行 → mount/patch(runtime.js) → 断言
 * 运行:node compiler_check.js(全部通过 exit 0)
 */

'use strict';

const C = require('./compiler.js');
const R = require('./runtime.js');
const { PatchFlags, flagComment } = C;

const TEMPLATE = [
  '<div id="app" :class="cls">',
  '  <h1>静态标题</h1>',
  '  <p class="static" id="p1">{{ msg }}</p>',
  '  <div class="box">静态块 A</div>',
  '  <div class="box">静态块 B</div>',
  '  <div class="box">静态块 C</div>',
  '  <div class="box">静态块 D</div>',
  '  <div class="box">静态块 E</div>',
  '  <span v-if="show" :style="style">{{ count }}</span>',
  '  <button @click="onClick">点击 {{ count }}</button>',
  '</div>',
].join('\n');

// ---------------- 编译 ----------------
const ast = C.parse(TEMPLATE);
const transformed = C.transform(ast);
const generated = C.codegen(transformed);
const render = new Function('__rt', generated.factorySource)(R.runtime);

const results = [];
function check(name, cond, detail) { results.push({ name, ok: !!cond, detail }); }
const blockText = (n) => `${n.type}`;

// ---------------- §1 PatchFlags 与源码一致 ----------------
function checkFlags() {
  const expect = {
    TEXT: 1, CLASS: 2, STYLE: 4, PROPS: 8, FULL_PROPS: 16, NEED_HYDRATION: 32,
    STABLE_FRAGMENT: 64, KEYED_FRAGMENT: 128, UNKEYED_FRAGMENT: 256, NEED_PATCH: 512,
    DYNAMIC_SLOTS: 1024, DEV_ROOT_FRAGMENT: 2048,
  };
  const bad = Object.keys(expect).filter((k) => PatchFlags[k] !== expect[k]);
  check('PatchFlags 位值 = vuejs/core patchFlags.ts', bad.length === 0, bad.join(',') || '12 项全对');
  check('CACHED/BAIL 为负数是特殊标记(不参与按位与)',
    PatchFlags.CACHED === -1 && PatchFlags.BAIL === -2 && (PatchFlags.CACHED > 0) === false,
    `CACHED=${PatchFlags.CACHED} BAIL=${PatchFlags.BAIL},旧名 HOISTED`);
  check('flagComment 能把位掩码还原成名字', flagComment(1 | 2) === 'TEXT, CLASS', flagComment(3));
}

// ---------------- §2 编译产物形状 ----------------
function checkCodegen() {
  const src = generated.source;
  check('静态元素被提升为模块级 _hoisted_N', /const _hoisted_1 = \/\*#__PURE__\*\//.test(src),
    (src.split('\n')[0] || '').slice(0, 60));
  check('提升节点带 CACHED(-1) 标记', /-1 \/\* CACHED \*\//.test(src), '');
  check('单插值走 children fast path(不包数组)',
    /createBlock\("p", \{ "class": "static", "id": "p1" \}, toDisplayString\(_ctx\.msg\), 1 \/\* TEXT \*\/\)/.test(src)
      || /createVNode\("p", \{ "class": "static", "id": "p1" \}, toDisplayString\(_ctx\.msg\), 1 \/\* TEXT \*\/\)/.test(src),
    '');
  check('patchFlag 组合位按位或(span = TEXT|STYLE = 5)', /, 5 \/\* TEXT, STYLE \*\//.test(src), '');
  check('事件处理器编译为 _cache[0] || (_cache[0] = ...)', /_cache\[0\] \|\| \(_cache\[0\] = /.test(src), '');
  check('v-if 编译为三元 + createCommentVNode', /createCommentVNode\("v-if", true\)/.test(src), '');
  check('根节点是 block(openBlock + createBlock)', /return \(\(openBlock\(\), createBlock\("div"/.test(src), '');
  check('v-if 另建一个块 → 源码出现 2 处 openBlock', (src.match(/openBlock\(\)/g) || []).length === 2,
    String((src.match(/openBlock\(\)/g) || []).length));
  const hoisted = transformed.hoisted;
  check('连续 5 个静态 div 被压缩为 1 个 static vnode(innerHTML)',
    hoisted.some((h) => h.type === 'STATIC_STRING' && h.count === 5),
    `hoisted=${hoisted.map((h) => h.type).join(',')}`);
  const staticString = hoisted.find((h) => h.type === 'STATIC_STRING');
  check('static vnode 的 HTML 串包含全部 5 个元素',
    (staticString.html.match(/<div class="box">/g) || []).length === 5, `len=${staticString.html.length}`);
  check('h1 与 static 块共 2 个提升项', hoisted.length === 2, String(hoisted.length));
}

// ---------------- §3 编译产物 → 执行 ----------------
function renderTree(ctx, cache) { return render(ctx, cache); }

function checkRuntime() {
  const cache = [];
  R.resetStats();
  const vnode1 = renderTree({ cls: 'a', msg: 'hello', count: 1, show: true, style: 'color:red' }, cache);
  const root1 = R.mount(vnode1);
  const html1 = R.serialize(root1);

  check('挂载后 DOM 形状正确', html1.startsWith('<div id="app"') && html1.includes('<h1>静态标题</h1>') && html1.includes('hello'),
    html1.slice(0, 70) + '...');
  check('static vnode 用 innerHTML 一次挂载 5 个元素', R.stats.staticVnodeMounts === 5, String(R.stats.staticVnodeMounts));

  // 单次 render 的 vnode 创建数:模板共 9 个元素,提升后每轮只创建 4 个(根块 + p + span块 + button)
  R.resetStats();
  renderTree({ cls: 'b', msg: 'world', count: 2, show: true, style: 'color:blue' }, cache);
  const creationsPerRender = R.stats.vnodeCreations;
  check('每轮 render 只创建 4 个 vnode(9 个元素中 6 个静态的已提升)',
    creationsPerRender === 4, `creations=${creationsPerRender},提升项=${transformed.hoisted.length}`);

  // 第 2 次渲染 + patch
  R.resetStats();
  const vnode2 = renderTree({ cls: 'b', msg: 'world', count: 2, show: true, style: 'color:blue' }, cache);
  const el = R.patchOptimized(vnode1, vnode2);
  const html2 = R.serialize(el);

  check('patch 后文本/class/style 都更新了', html2.includes('world') && html2.includes('class="b"') && html2.includes('color:blue'),
    html2.slice(0, 80) + '...');
  check('patch 只访问 4 个节点(根+p+span块+button),h1 与 static 块从未被遍历',
    R.stats.nodeVisits === 4, `nodeVisits=${R.stats.nodeVisits}`);
  check('数组 children 的文本走位置 diff(按钮文案 1 → 2)', html2.includes('点击 2'), '');
  check('未被遍历的静态子树计数为 0(比"跳过"更彻底:根本不进遍历)',
    R.stats.reusedStatic === 0, `reusedStatic=${R.stats.reusedStatic}`);

  // cacheHandler:同一 _cache 对象 → 处理器引用稳定
  const handler1 = vnode1.dynamicChildren.find((c) => c.type === 'button').props.onClick;
  const handler2 = vnode2.dynamicChildren.find((c) => c.type === 'button').props.onClick;
  check('内联事件被缓存,引用跨渲染不变(cacheHandler)', handler1 === handler2 && typeof handler1 === 'function', '');

  // block tree:dynamicChildren 只装动态节点
  check('根 block 的 dynamicChildren = 3(p / span / button)',
    vnode1.dynamicChildren.length === 3, vnode1.dynamicChildren.map(blockText).join(','));
  check('动态节点都带 patchFlag > 0', vnode1.dynamicChildren.every((c) => c.patchFlag > 0),
    vnode1.dynamicChildren.map((c) => c.patchFlag).join(','));
  const span = vnode1.dynamicChildren.find((c) => c.type === 'span');
  check('span 的 patchFlag = TEXT|STYLE = 5', span.patchFlag === (PatchFlags.TEXT | PatchFlags.STYLE), String(span.patchFlag));
  const button = vnode1.dynamicChildren.find((c) => c.type === 'button');
  check('button 的 patchFlag = NEED_HYDRATION = 32(数组子节点不设 TEXT)',
    button.patchFlag === PatchFlags.NEED_HYDRATION, String(button.patchFlag));
}

// ---------------- §4 两条 patch 路径对比 ----------------
function checkPatchPaths() {
  const ctx1 = { cls: 'a', msg: 'hello', count: 1, show: true, style: 'color:red' };
  const ctx2 = { cls: 'b', msg: 'world', count: 2, show: true, style: 'color:blue' };

  const cacheA = [];
  const t1 = renderTree(ctx1, cacheA);
  const rootA = R.mount(t1);
  R.resetStats();
  R.patchOptimized(t1, renderTree(ctx2, cacheA));
  const opt = { ...R.stats };
  const htmlOpt = R.serialize(rootA);

  const cacheB = [];
  const t2 = renderTree(ctx1, cacheB);
  const rootB = R.mount(t2);
  R.resetStats();
  R.patchFull(t2, renderTree(ctx2, cacheB));
  const full = { ...R.stats };
  const htmlFull = R.serialize(rootB);

  check('两条路径产出完全相同的 DOM', htmlOpt === htmlFull, `${htmlOpt.length} vs ${htmlFull.length} 字符`);
  check('优化路径访问节点数更少', opt.nodeVisits < full.nodeVisits, `优化 ${opt.nodeVisits} vs 全量 ${full.nodeVisits}`);
  check('差值恰好等于 2 个提升子树(h1 + static 块)——只有基线会去遍历它们',
    full.nodeVisits - opt.nodeVisits === 2, `${full.nodeVisits} - ${opt.nodeVisits} = ${full.nodeVisits - opt.nodeVisits}`);
  check('优化路径 props 比较次数更少', opt.propComparisons < full.propComparisons,
    `优化 ${opt.propComparisons} vs 全量 ${full.propComparisons}`);
  check('优化路径只比较带 patchFlag 的属性(root.class + span.style = 2)', opt.propComparisons === 2, `优化 ${opt.propComparisons}`);
}

// ---------------- §5 v-if 块动态增删 ----------------
function checkVIfBlock() {
  const cache = [];
  const withSpan = renderTree({ cls: 'a', msg: 'm', count: 1, show: true, style: 's' }, cache);
  const rootEl = R.mount(withSpan);
  const beforeHasSpan = R.serialize(rootEl).includes('<span');
  const withoutSpan = renderTree({ cls: 'a', msg: 'm', count: 1, show: false, style: 's' }, cache);
  R.resetStats();
  R.patchOptimized(withSpan, withoutSpan);
  check('v-if 成立时 span 是根 block 的一个 dynamicChildren 成员',
    withSpan.dynamicChildren.some((c) => c.type === 'span') && beforeHasSpan, '');
  check('v-if 关闭后该块不再出现在 dynamicChildren 中',
    !withoutSpan.dynamicChildren.some((c) => c.type === 'span'),
    withoutSpan.dynamicChildren.map(blockText).join(','));
  check('v-if 关闭后 span 子树在 patch 中被跳过(只访问根+p+button = 3)', R.stats.nodeVisits === 3, String(R.stats.nodeVisits));
}

const sections = [
  ['§1 PatchFlags 位值', checkFlags],
  ['§2 编译产物形状', checkCodegen],
  ['§3 运行时:提升 / 块树 / 事件缓存', checkRuntime],
  ['§4 优化 patch vs 全量 patch', checkPatchPaths],
  ['§5 v-if 块动态增删', checkVIfBlock],
];

console.log('=== Vue 3 编译期优化 自检 ===');
for (const [title, fn] of sections) {
  console.log('\n' + title);
  const before = results.length;
  fn();
  for (const r of results.slice(before)) {
    console.log(`  ${r.ok ? 'PASS' : 'FAIL'}  ${r.name}${r.detail ? '  [' + r.detail + ']' : ''}`);
  }
}
const failed = results.filter((r) => !r.ok);
console.log(`\n合计 ${results.length} 项断言,通过 ${results.length - failed.length},失败 ${failed.length}`);
if (failed.length > 0) {
  failed.forEach((f) => console.log('  FAIL → ' + f.name));
  process.exit(1);
}
console.log('全部通过 ✅');
