// EXPECT: OK
// 模块增强：给已有模块的**具名**导出打补丁。
// 惯例是把内容放进declare module "./observable"，然后运行时再去动 prototype。

import { Observable } from "./observable";

declare module "./observable" {
  interface Observable<T> {
    map<U>(f: (x: T) => U): Observable<U>;
  }
}

declare const o: Observable<number>;

// 没有上面那段 declare module，这一行会 TS2339
const doubled = o.map((x) => x.toFixed());

export { doubled };
