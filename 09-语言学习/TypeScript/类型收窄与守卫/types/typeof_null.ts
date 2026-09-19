// EXPECT: TS18047
// typeof 只能把这 8 种值区分开，而 null 的 typeof 恰好是 "object"：
// 于是 "typeof x === 'object'" 之后，x 依然可能是 null。
function printAllBadly(strs: string | string[] | null): void {
  if (typeof strs === "object") {
    for (const s of strs) {
      console.log(s);
    }
  } else if (typeof strs === "string") {
    console.log(strs);
  }
}

function printAllProperly(strs: string | string[] | null): void {
  if (strs && typeof strs === "object") {
    for (const s of strs) {
      console.log(s);
    }
  } else if (typeof strs === "string") {
    console.log(strs);
  }
}

// truthiness 的另一面：0 与 "" 会被当作缺失值吞掉
function countUsersOnline(numUsersOnline: number): string {
  if (numUsersOnline) {
    return "online: " + numUsersOnline;
  }
  return "nobody";
}

export { printAllBadly, printAllProperly, countUsersOnline };
