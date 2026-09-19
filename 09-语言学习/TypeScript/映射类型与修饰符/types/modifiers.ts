// EXPECT: TS2540, TS2739
// 映射修饰符：readonly 与 ? 都可以用 - 剥掉、用 + 加上（无前缀默认 +）。
type CreateMutable<T> = { -readonly [P in keyof T]: T[P] };
type Concrete<T> = { [P in keyof T]-?: T[P] };

interface LockedAccount {
  readonly id: string;
  readonly name: string;
}
declare const raw: LockedAccount;
raw.id = "x"; // TS2540：readonly 阻断写入

declare const unlocked: CreateMutable<LockedAccount>;
unlocked.id = "x"; // OK：-readonly 剥掉了只读

interface MaybeUser {
  id: string;
  name?: string;
  age?: number;
}
declare const partialUser: MaybeUser;
const stillPartial: MaybeUser = { id: "u1" }; // OK：name/age 可选

const full: Concrete<MaybeUser> = { id: "u1" }; // TS2739：-? 让 name 与 age 变成必填

export { unlocked, stillPartial, full };
