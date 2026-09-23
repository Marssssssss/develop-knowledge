// satisfies 是纯编译期运算符：运行时没有任何残留，类型全部被擦除。
// 这一段演示它与「运行时兜底」的分工 —— satisfies 只能静态校验形状，
// 从外部进来的数据、以及写在类型里的业务规则，运行时仍要自己看一眼。

type ChannelKind = "email" | "sms" | "push";

const LIMITS = {
  email: 1024,
  sms: 140,
  push: 512,
} satisfies Record<ChannelKind, number>;

// 第二个 satisfies：每个 channel 一个 formatter，且 shape 必须一致
const FORMATTERS = {
  email: (body: string): string => body.trim(),
  sms: (body: string): string => body.slice(0, LIMITS.sms),
  push: (body: string): string => body.slice(0, LIMITS.push),
} satisfies Record<ChannelKind, (body: string) => string>;

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

// 1. satisfies 不改动对象本身：key 集合与可写性都是原始字面量的样子
check("keys are exactly the three channels",
  JSON.stringify(Object.keys(LIMITS)) === JSON.stringify(["email", "sms", "push"]));
check("no runtime marker injected", Object.keys(LIMITS).every((k) => k[0] !== "@"));
check("object stays mutable", Object.isExtensible(LIMITS) && !Object.isFrozen(LIMITS));

// 2. 编译期的「不多不少」不是运行时 invariant：JS 里照样能塞 key
const loose = LIMITS as unknown as Record<string, unknown>;
loose["carrier-pigeon"] = 1;
check("runtime can smuggle in a key the compiler would reject", loose["carrier-pigeon"] === 1);

// 3. 所以外部数据必须自行兜底，且与 ChannelKind 同源
const KINDS: ChannelKind[] = ["email", "sms", "push"];
check("registry covers every kind", KINDS.every((k) => typeof FORMATTERS[k] === "function"));
check("no extra formatter leaked in", Object.keys(FORMATTERS).length === KINDS.length);

// 4. 裁剪这条业务规则活在运行时，类型里看不出来（两者的 string 是同一个 string）
const long: string = "x".repeat(500);
check("sms truncates to its own limit", FORMATTERS.sms(long).length === 140);
check("email keeps more than sms", FORMATTERS.email(long).length === 500);
check("push keeps 500 chars when its 512 limit is not hit",
  FORMATTERS.push(long).length === 500);
check("push truncates once past its own limit",
  FORMATTERS.push("x".repeat(600)).length === 512);

// 5. LIMITS 里的数字是运行时真实读到的 —— 类型守 Bound，值守业务
check("limits dict is readable at runtime", LIMITS.sms < LIMITS.push && LIMITS.push < LIMITS.email);

console.log("runtime: pass=" + pass + " fail=" + fail);
if (fail > 0) process.exit(1);
