// EXPECT: TS2345
// 「监听某属性变化」的类型：`${Key}Changed` 的正向用法与拼写保护。

type PropEventSource<Type> = {
  on<Key extends string & keyof Type>(
    eventName: `${Key}Changed`,
    callback: (newValue: Type[Key]) => void
  ): void;
};

declare function makeWatchedObject<Type>(obj: Type): Type & PropEventSource<Type>;

const person = makeWatchedObject({
  firstName: "Saoirse",
  lastName: "Ronan",
  age: 26,
});

// 正向：事件名合法，回调参数由 Type[Key] 反查出来
person.on("firstNameChanged", (newName) => {
  const upper: string = newName.toUpperCase();
  void upper;
});
person.on("ageChanged", (newAge) => {
  const ok: boolean = newAge > 0;
  void ok;
});

// ❌ 少了 Changed 后缀：把属性名当事件名用
person.on("firstName", () => {});

// ❌ 拼错：frstNameChanged 不在合法联合里
person.on("frstNameChanged", () => {});

export { person };
