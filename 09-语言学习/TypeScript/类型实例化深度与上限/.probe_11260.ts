type GetChars<S> = S extends `${infer C}${infer R}`
  ? C | GetChars<R>
  : never;
type T = GetChars<"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa">;
export type { T };
