// EXPECT: TS2536
// 条件类型的真分支会把泛型进一步"约束"在被检查的类型上，于是 T["message"] 变成合法。
type MessageOfUnconstrained<T> = T["message"]; // TS2536：T 上不存在 message

interface Email {
  message: string;
}
interface Dog {
  bark(): void;
}

type MessageOf<T> = T extends { message: unknown } ? T["message"] : never;
declare const email: MessageOf<Email>;
declare const dogMsg: MessageOf<Dog>;
const s: string = email; // OK
const neverValue: never = dogMsg; // OK：Dog 走假分支 → never

export { s, neverValue };
