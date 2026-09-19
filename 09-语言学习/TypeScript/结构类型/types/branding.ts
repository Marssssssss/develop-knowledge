// EXPECT: TS2322, TS2345
// 用交叉类型 + unique symbol 手工模拟名义类型(branding):编译期不兼容,运行期零开销。
declare const brandKey: unique symbol;
type Brand<T, B extends string> = T & { readonly [brandKey]: B };

type UserId = Brand<string, "UserId">;
type OrderId = Brand<string, "OrderId">;

declare function loadOrder(id: OrderId): void;
declare const uid: UserId;

loadOrder(uid); // TS2345:实参位置的 brand 不匹配
const bad: OrderId = uid; // TS2322:赋值位置同样不匹配
const oid: OrderId = "o-1" as OrderId; // 断言是唯一入口
loadOrder(oid); // OK

export { oid, bad };
