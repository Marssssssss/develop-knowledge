/**
 * Vite 冷启动原理最小模拟 (JavaScript)
 *
 * 演示:
 *   1) 浏览器原生 ESM import + import.meta.url + HTTP 304
 *   2) 依赖预构建:CommonJS / UMD 依赖用 esbuild 转 ESM 一次性缓存
 *   3) on-demand transform:源码每次修改只在请求时编译
 *   4) 与 Webpack 全量打包对比
 *
 * 权威来源:
 *   - vitejs.dev/guide/why.html (实际读过,lines 'The Origins' + 'A Unified Toolchain')
 *   - vitejs.dev/guide/dep-pre-bundling (esbuild 预构建)
 *   - MDN ES modules <script type="module">
 *
 * 运行:node vite-sim.js → 5 demo 输出
 */

// ---------------- 1. 模拟 Vite dev server 的核心数据流 ----------------
class ViteDevServer {
  constructor() {
    this.depsCache = new Map();   // node_modules/.vite/deps/<pkg>.js 路径 → ESM 编译后内容
    this.sourceCache = new Map(); // 源文件 → 已 transform 内容
    this.timers = { cacheMiss: 0, transform: 0 };
  }

  // 浏览器首次访问 .html → 浏览器解析 module 树
  // dev server 拦截 / 别解析源码里的 import,从这里分发
  async resolve(url) {
    if (url.endsWith('.html')) return { kind: 'html', url };
    if (this.depsCache.has(url)) return { kind: 'dep-cached', url };
    if (url.includes('node_modules') || url.includes('/@deps/')) {
      // 依赖:首次需要 esbuild 预构建(CommonJS → ESM)
      this.timers.cacheMiss++;
      const esm = this.pretendEsbuildBundle(url);
      this.depsCache.set(url, esm);
      return { kind: 'dep-built', url, size: esm.length };
    }
    // 源代码:on-demand transform
    this.timers.transform++;
    const transformed = this.pretendTransform(url);
    return { kind: 'source-transformed', url, size: transformed.length, ...transformed };
  }

  // esbuild 预构建 CommonJS/UMD → ESM(实 vite 调用 esbuild API)
  pretendEsbuildBundle(url) {
    return `// bundled by esbuild: ${url}\nexport default {};\nexport const named = 'cached';`;
  }

  // 源码 transform:JSX / TS / 现代语法 → 浏览器可执行
  pretendTransform(url) {
    return {
      out: `// transformed: ${url}\nconsole.log("hello from ${url}");`,
      mtime: Date.now(),
    };
  }

  stats() { return { ...this.timers }; }
}

// ---------------- 2. 模拟浏览器 ESM 加载顺序 ----------------
async function browserLoad(server, urls) {
  const log = [];
  for (const url of urls) {
    const res = await server.resolve(url);
    log.push(`[browser import] ${url}  →  ${res.kind}${res.size ? ` (${res.size}B)` : ''}`);
  }
  return log;
}

// ---------------- 3. demo 1: 浏览器 import 一个源码文件 ----------------
async function demo1() {
  console.log('--- demo 1: on-demand source transform ---');
  const server = new ViteDevServer();
  const lines = await browserLoad(server, [
    '/index.html',
    '/src/main.js',
    '/src/App.jsx',
  ]);
  console.log(lines.join('\n'));
}

// ---------------- 4. demo 2: CommonJS 依赖预构建 ----------------
async function demo2() {
  console.log('--- demo 2: CommonJS dep pre-bundling ---');
  const server = new ViteDevServer();
  const urls = [
    '/node_modules/.vite/deps/lodash.js',
    '/node_modules/.vite/deps/react.js',
    '/node_modules/.vite/deps/react-dom_client.js',
  ];
  for (const u of urls) {
    const r = await server.resolve(u);
    console.log(`  ${u} → ${r.kind}${r.size ? ` (${r.size}B bundled)` : ''}`);
  }
  // 第二次访问:命中 depsCache
  console.log('  -- 再次访问 (命中缓存) --');
  for (const u of urls) {
    const r = await server.resolve(u);
    console.log(`  ${u} → ${r.kind}`);
  }
  console.log('  stats:', server.stats());
}

// ---------------- 5. demo 3: 与 Webpack 对比量级 ----------------
function demo3() {
  console.log('--- demo 3: 与 Webpack 对比 ---');
  // 假设 1000 个源文件,100 个依赖
  const files = 1000, deps = 100;
  // Vite: 首屏只 transform 被 import 的模块
  console.log(`  Vite 冷启动估算`);
  console.log(`    - dev server 启动:依赖预构建打包 100 个 dep = ${deps * 50}ms(esbuild 单线程)`);
  console.log(`    - 首屏只 transform ${Math.min(files, 30)} 个 on-demand ESM 模块 ≈ ${Math.min(files, 30) * 5}ms`);
  console.log(`  Webpack 冷启动估算`);
  console.log(`    - dev compile:遍历 ${files} 文件 + ${deps} 依赖,全图打包 ≈ ${files * 30 + deps * 200}ms`);
  console.log(`    - 首屏等服务端 bundle 完成 → 浏览器才能收到`);
}

// ---------------- 6. demo 4: HMR ----------------
async function demo4() {
  console.log('--- demo 4: HMR 流程模拟 ---');
  const server = new ViteDevServer();
  const log = [];
  // 编辑文件 → Vite watch 触发 transform
  log.push('[editor save] /src/App.jsx');
  const r = await server.resolve('/src/App.jsx');
  log.push(`[vite] transform → out=${r.out.length}B, mtime=${r.mtime}`);
  log.push('[vite ws push] HMR API:`import.meta.hot.accept` callback');
  log.push('[browser] 仅替换 App.jsx 模块,不刷新页面');
  console.log(log.join('\n'));
}

// ---------------- 7. demo 5: import.meta.url 与裸 module specifier 解析 ----------------
function demo5() {
  console.log('--- demo 5: import.meta.url + 相对 / 绝对路径 ---');
  // 浏览器原生 ESM 必须显式 .js 后缀、不支持裸 specifier(必须 mappings)
  // Vite dev 会拦截并解析:
  const cases = [
    ['./utils.js',         '相对路径,浏览器原生 OK'],
    ['/src/utils.js',      'dev server 静态服'],
    ['react',              '裸 specifier,Vite 用 import-analysis 重写到 /node_modules/.vite/deps/react.js'],
    ['lodash-es/debounce','裸 + 子路径,Vite 用预构建映射'],
    ['node:fs',           'Node 内置,Vite 在 dev 中走 SSR 模块图'],
    ['virtual:foo',       '虚拟模块,Vite 插件 generateCode 提供'],
  ];
  for (const [spec, expect] of cases) {
    console.log(`  import ${spec.padEnd(30)} → ${expect}`);
  }
}

// ---------------- 8. 启动 demo 序列 ----------------
(async () => {
  await demo1();
  await demo2();
  demo3();
  await demo4();
  demo5();
})();
