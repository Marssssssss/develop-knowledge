// EXPECT: TS2339
// 类型谓词的价值：把「一次判斷」變成可被后续流程（含 filter）复用的收窄。
type Fish = { swim: () => void };
type Bird = { fly: () => void };
declare function getSmallPet(): Fish | Bird;
declare const zoo: (Fish | Bird)[];

function isFish(pet: Fish | Bird): pet is Fish {
  return (pet as Fish).swim !== undefined;
}

const pet = getSmallPet();
if (isFish(pet)) {
  pet.swim();
} else {
  pet.fly();
}

const typed: Fish[] = zoo.filter(isFish); // 谓词签名让数组元素类型跟着收窄
typed[0].swim();

const untyped: (Fish | Bird)[] = zoo.filter((p) => (p as Fish).swim !== undefined);
untyped[0].swim(); // 没有谓词签名 ⇒ 元素类型仍是联合，无法直接调用

export { typed, untyped };
