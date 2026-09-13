/**
 * Vite 冷启动原理最小模拟 — TypeScript 版
 *
 * 与 JS 版(vite-sim.js)等价,加类型约束。
 * 演示 Vite 的 ESM + deps 预构建 + on-demand transform + HMR 数据流。
 *
 * 运行:npx tsc --noEmit vite-sim.ts
 */

// ---------------- 类型 ----------------
export type UrlKind = 'html' | 'dep-cached' | 'dep-built' | 'source-transformed';

export interface ResolveResult {
  kind: UrlKind;
  url: string;
  size?: number;
  out?: string;
  mtime?: number;
}

interface ViteStats {
  cacheMiss: number;
  transform: number;
}

// ---------------- ViteDevServer 类 ----------------
export class ViteDevServer {
  private depsCache = new Map<string, string>();
  private sourceCache = new Map<string, { out: string; mtime: number }>();
  private timers: ViteStats = { cacheMiss: 0, transform: 0 };

  async resolve(url: string): Promise<ResolveResult> {
    if (url.endsWith('.html')) return { kind: 'html', url };
    if (this.depsCache.has(url)) return { kind: 'dep-cached', url };

    const isDep = url.includes('node_modules') || url.includes('/@deps/');
    if (isDep) {
      this.timers.cacheMiss += 1;
      const esm = this.pretendEsbuildBundle(url);
      this.depsCache.set(url, esm);
      return { kind: 'dep-built', url, size: esm.length };
    }

    // 源代码 on-demand transform
    this.timers.transform += 1;
    const t = this.pretendTransform(url);
    return { kind: 'source-transformed', url, size: t.out.length, out: t.out, mtime: t.mtime };
  }

  private pretendEsbuildBundle(url: string): string {
    return `// bundled by esbuild: ${url}\nexport default {};\nexport const named = 'cached';`;
  }

  private pretendTransform(url: string): { out: string; mtime: number } {
    return { out: `// transformed: ${url}\nconsole.log('hello from ${url}');`, mtime: Date.now() };
  }

  stats(): ViteStats { return { ...this.timers }; }
}

// ---------------- 模拟浏览器加载序列 ----------------
export async function browserLoad(server: ViteDevServer, urls: string[]): Promise<string[]> {
  const log: string[] = [];
  for (const url of urls) {
    const res = await server.resolve(url);
    log.push(`[browser import] ${url} → ${res.kind}${res.size ? ` (${res.size}B)` : ''}`);
  }
  return log;
}

// ---------------- demo ----------------
export async function runDemos(): Promise<void> {
  // demo 1
  console.log('--- demo 1: on-demand source transform ---');
  const server1 = new ViteDevServer();
  console.log((await browserLoad(server1, ['/index.html', '/src/main.js', '/src/App.jsx'])).join('\n'));

  // demo 2
  console.log('--- demo 2: CommonJS dep pre-bundling ---');
  const server2 = new ViteDevServer();
  const urls = ['/node_modules/.vite/deps/lodash.js', '/node_modules/.vite/deps/react.js', '/node_modules/.vite/deps/react-dom_client.js'];
  for (const u of urls) {
    const r = await server2.resolve(u);
    console.log(`  ${u} → ${r.kind}${r.size ? ` (${r.size}B bundled)` : ''}`);
  }
  console.log('  -- 再次访问 (命中缓存) --');
  for (const u of urls) {
    const r = await server2.resolve(u);
    console.log(`  ${u} → ${r.kind}`);
  }
  console.log('  stats:', server2.stats());

  // demo 3 量级对比(直接 console)
  console.log('--- demo 3: 量级对比 ---');
  console.log('  Vite: 100 个 dep 预构建 ≈ 5000ms;首屏 30 个模块 on-demand ≈ 150ms');
  console.log('  Webpack: 100 文件 + 100 dep 全图打包 ≈ 5000ms 起');

  // demo 4 HMR
  console.log('--- demo 4: HMR ---');
  const server4 = new ViteDevServer();
  console.log('[editor save] /src/App.jsx');
  const r = await server4.resolve('/src/App.jsx');
  console.log(`[vite] transform → out=${r.out?.length ?? 0}B, mtime=${r.mtime ?? 0}`);
  console.log('[vite ws] push update 事件 → import.meta.hot.accept 回调替换模块');

  // demo 5 import specifier 分类
  console.log('--- demo 5: specifier 分类 ---');
  const cases: Array<[string, string]> = [
    ['./utils.js', '浏览器原生'],
    ['/src/utils.js', 'dev server 静态服'],
    ['react', 'Vite 解析到 .vite/deps/react.js'],
    ['lodash-es/debounce', '子路径映射'],
    ['node:fs', 'SSR 模块图'],
    ['virtual:foo', '插件 generateCode'],
  ];
  for (const [spec, expect] of cases) {
    console.log(`  import ${spec.padEnd(28)} → ${expect}`);
  }
}
