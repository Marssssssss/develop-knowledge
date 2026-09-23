// 类型级的字符串运算最终要落到运行时：这一段演示「类型算出来的东西」
// 与「运行时真的有的东西」如何一一对应，不一致的地方单独标注。

let pass = 0;
let fail = 0;

function check(label: string, cond: boolean): void {
  if (cond) {
    pass++;
    console.log("ok " + label);
  } else {
    fail++;
    console.log("FAIL " + label);
  }
}

// ---------- 1. ExtractParams 的运行时镜像 ----------
// 类型版：ExtractParams<"/users/:id/posts/:postId"> = "id" | "postId"
function extractParams(route: string): string[] {
  const out: string[] = [];
  for (const seg of route.split("/")) {
    if (seg.startsWith(":")) out.push(seg.slice(1));
  }
  return out;
}

const p1 = extractParams("/users/:id");
check("single param", JSON.stringify(p1) === JSON.stringify(["id"]));

const p2 = extractParams("/users/:id/posts/:postId");
check("two params keep source order",
  JSON.stringify(p2) === JSON.stringify(["id", "postId"]));

const p3 = extractParams("/static/home");
check("no params yields empty list", p3.length === 0);

const p4 = extractParams("/users/:id/");
check("trailing slash still yields the param", JSON.stringify(p4) === JSON.stringify(["id"]));

// 类型版里 never 表示「没有参数」；运行时的对应物是空数组，不是 undefined
check("never <-> empty array, not undefined", Array.isArray(p3) && p3 !== undefined);

// ---------- 2. Split 的运行时镜像 ----------
// 类型版：Split<"a-b-c", "-"> = ["a", "b", "c"]，Split<"", "-"> = []
function splitKeepEmpty(s: string, d: string): string[] {
  return s.split(d);
}
check("split three parts", JSON.stringify(splitKeepEmpty("a-b-c", "-")) === JSON.stringify(["a", "b", "c"]));
check("split without delimiter keeps whole string",
  JSON.stringify(splitKeepEmpty("a", "-")) === JSON.stringify(["a"]));
// 这是类型版与运行时版的第一个真实差异：Split<"", D> 的类型结果是 []，
// 而 String.prototype.split 给 [""]（长度 1）。语言的生态里两侧不一致时要显式记账。
check("empty string runtime split gives ['']",
  JSON.stringify(splitKeepEmpty("", "-")) === JSON.stringify([""]));
check("type-level rule and runtime rule disagree here",
  JSON.stringify(splitKeepEmpty("", "-")) !== JSON.stringify([]));

// ---------- 3. intrinsic 字符串操作与 String.prototype 同源 ----------
// 文档给出的 applyStringMapping 实现直接调用 str.toUpperCase() 等，不做 locale 处理。
check("Uppercase<'ß'> is 'SS', same as toUpperCase()", "ß".toUpperCase() === "SS");
check("type-level Uppercase is NOT locale aware", "i".toUpperCase() === "I");
check("toLocaleUpperCase('tr') would give a different answer", "i".toLocaleUpperCase("tr") === "İ");
check("Capitalize matches the docs implementation",
  "hello, world".charAt(0).toUpperCase() + "hello, world".slice(1) === "Hello, world");

// ---------- 4. 由类型产出的一张 key 表，运行时照样能用 ----------
const routeKeyMap: Record<string, string[]> = {
  "/users/:id": extractParams("/users/:id"),
  "/users/:id/posts/:postId": extractParams("/users/:id/posts/:postId"),
};
check("route table is total over the literal routes", Object.keys(routeKeyMap).length === 2);
check("table agrees with the extractor",
  JSON.stringify(routeKeyMap["/users/:id/posts/:postId"]) === JSON.stringify(["id", "postId"]));

console.log("runtime: pass=" + pass + " fail=" + fail);
if (fail > 0) process.exit(1);
