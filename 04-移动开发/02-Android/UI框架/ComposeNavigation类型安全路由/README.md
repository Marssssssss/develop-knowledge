# Compose Navigation 类型安全路由与嵌套图

Navigation 2.8 起，「route」不再要求手写字符串：`@Serializable` 的类/对象本身就是 route。
本 demo 把这套机制**剥离成纯逻辑**复现——不依赖 Android 运行时，可直接在 Python 里跑通官方源码的每一条判据。

对应官方源码：`navigation-common/.../NavGraph.kt`、`NavGraphNavigator.kt`、
`NavGraphBuilder.kt`，以及 `navigation-common/.../serialization/` 下的 `RouteBuilder.kt`、
`RouteEncoder.kt`、`RouteDecoder.kt`、`NavTypeConverter.kt`。

## 一、原理详解

### 1.1 类型安全路由的本质：route 就是 KSerializer 的 serialName

`RouteBuilder` 的第一个构造器只有一行赋值：

```kotlin
constructor(serializer: KSerializer<T>) {
    this.serializer = serializer
    path = serializer.descriptor.serialName   // ← route 的基准串
}
```

所以 `@Serializable data class Profile(val id: Int)` 的默认 route 是它的
**serialName（全限定类名）**，而不是开发者手写的东西。`build()` 把三段拼起来：
`path + pathArgs + queryArgs`。

### 1.2 PATH 还是 QUERY：只有一个判据

`RouteBuilder.computeParamType` 是唯一的决策点：

```kotlin
private fun computeParamType(index: Int, type: NavType<Any?>) =
    if (type is CollectionNavType || serializer.descriptor.isElementOptional(index)) {
        ParamType.QUERY
    } else {
        ParamType.PATH
    }
```

- **集合类型（CollectionNavType）** → 只能进 query，因为一个 path 段放不下多个值；
- **可选元素（有默认值 / 可空）** → 进 query，这样「不传」才是合法的；
- 其余（必填标量）→ 进 path，形如 `/{name}`。

拼装规则：`addPath` 前面带 `/`；`addQuery` 第一个用 `?`、之后用 `&`，且
**多值参数会重复同一个 key**（`?tags=1&tags=2`），不是拼成逗号串。

### 1.3 PATH 参数的单值约束

```kotlin
ParamType.PATH -> {
    require(value.size == 1) {
        "Expected one value for argument $name, found ${value.size}" + "values instead."
    }
    addPath(value.first())
}
```

`appendArg` 用于「把具体对象填进 pattern 得到可导航 route」，此时 path 参数若给了
多个值会直接 `IllegalArgumentException`。

### 1.4 RouteEncoder：值永远是 `List<String>`

```kotlin
private val map: MutableMap<String, List<String>> = mutableMapOf()
...
val parsedValue =
    if (navType is CollectionNavType) navType.serializeAsValues(value)
    else listOf(navType.serializeAsValue(value))
map[argName] = parsedValue
```

- 单个标量也会被包成**单元素列表**，`List<String>` 是统一形态；
- `typeMap` 里查不到 NavType 时 `checkNotNull` 抛
  `IllegalStateException("Cannot find NavType for argument ... Please provide NavType through typeMap.")`
  —— 自定义类型必须显式传 `typeMap`，否则运行时才炸。

### 1.5 RouteDecoder：缺失元素直接跳过

```kotlin
override fun decodeElementIndex(descriptor: SerialDescriptor): Int {
    var currentIndex = elementIndex
    while (true) {
        currentIndex++
        if (currentIndex >= descriptor.elementsCount) return CompositeDecoder.DECODE_DONE
        val currentName = descriptor.getElementName(currentIndex)
        if (store.contains(currentName)) { ...; return elementIndex }
        // 不含 → 不 return，继续 while，等于跳过这个元素
    }
}
```

因此**缺失的可选参数不会触发 decode**，也就不会拿到 null。只有当 store 里有这个 key、
但值是 null 且字段非可空时，`internalDecodeValue` 的 `checkNotNull` 才抛
`"Unexpected null value for non-nullable argument $elementName"`。

> 注意区分两种失败：参数**不在 store 里** → 解码阶段无感（后面由
> kotlinx.serialization 在构造对象时报缺字段）；参数**在 store 里但为 null** →
> 立即 `IllegalStateException`。

### 1.6 嵌套图：NavGraph 是「虚拟 destination」

`NavGraphNavigator.navigate` 的全部前置检查：

```kotlin
check(startId != 0 || startRoute != null) { "no start destination defined via app:startDestination ..." }
val startDestination = destination.findNode(startRoute, false)   // ← searchParents = false
requireNotNull(startDestination) {
    throw IllegalArgumentException("navigation destination $dest is not a direct child of this NavGraph")
}
```

关键在 `findNode(startRoute, false)`：**startDestination 必须是这个图的直接子节点**，
不能是孙子节点。把子图的某个 destination 直接指定成父图的 startDestination 会抛异常。

`findNode` 的语义是「精确命中优先，未命中再按 pattern 匹配」——官方紧接着的分支

```kotlin
if (startRoute != startDestination.route) {
    val matchingArgs = startDestination.matchRoute(startRoute)?.matchingArgs
    ...
}
```

正是为「startRoute 里带了字面参数值」（如 `.../Detail/77`）准备的：此时两者不相等，
需要从 startRoute 中解析出参数。

参数合并的顺序也很讲究：

```kotlin
args = savedState {
    putAll(matchingArgs)   // 先放从 startRoute 解析出来的
    args?.let { putAll(it) }   // 后放外部传入的 ⇒ 外部传入优先
}
```

最后做必填校验，缺失就抛
`"Cannot navigate to startDestination ... Missing required arguments [...]"`。

### 1.7 导航到 NavGraph ≠ 把它压栈

`NavGraph` 的 KDoc 写明：NavGraph 是 virtual destination，**它自己不会出现在 back stack 上**，
navigate 到它会把 `startDestination` 加进 back stack。本 demo 的 `navigate_to_graph`
返回值即为实际落地的 destination，`back_stack` 里也只有它。

## 二、与旧式字符串 route 的对比

| 维度 | 字符串 route | 类型安全 route |
| --- | --- | --- |
| route 来源 | 手写常量，易拼错 | `serializer.descriptor.serialName`，编译期检查 |
| 参数类型 | `navArgument("id") { type = NavType.IntType }` 手写 | 由 `NavTypeConverter` 自动推断，自定义类型仍需 `typeMap` |
| 参数位置 | path/query 全靠手写 | `computeParamType` 单一判据决定 |
| 传参 | `navController.navigate("profile/$id")` 字符串拼接 | `navController.navigate(Profile(id))`，序列化成 `Map<String, List<String>>` |
| 取值 | `it.arguments?.getInt("id")` | `val args = backStackEntry.toRoute<Profile>()` |

## 三、环境要求

- Python 3.8+（运行模型无第三方依赖）
- Kotlin 版本需 `androidx.navigation:navigation-compose 2.8+` + `kotlinx-serialization`

## 四、运行方式

```bash
cd 04-移动开发/02-Android/UI框架/ComposeNavigation类型安全路由
python selfcheck_nav.py      # 期望输出 PASS 31
```

## 五、关键代码

- `main.py`：`RouteBuilder` / `RouteEncoder` / `RouteDecoder` / `NavGraph` / `navigate_to_graph`
- `selfcheck_nav.py`：31 条断言，逐条对应上文的源码判据
- `NavigationTypeSafe.kt`：Kotlin 侧等价实现

## 六、性能边界与注意事项

- **每个 path 参数都要一次字符串拼接**，参数多时 route 串会很长；URI 长度上限（实践中约
  8 KB 以内安全）是硬边界，超长参数应放 body 或本地缓存。
- `NavHost` 的 `builder` 被 `remember` 包住（`NavHost.kt` 原文：*"The builder passed into
  this method is remembered. This means that for this NavHost, the contents of the builder
  cannot be changed."*），**运行时不能改图结构**，否则要换 `key`。
- 自定义类型必须给 `typeMap`；`@Serializable` 的类若含 `List` 字段，会自动变成 query 参数
  （`computeParamType`），这常常是「为什么我的参数跑到了 query 里」的答案。
- `matchRouteComprehensive` 的 `searchChildren/searchParent/lastVisited` 三参数控制跨图搜索范围；
  默认导航只查直接子节点，深层跨图需显式深链。
- **常见坑**：把「子图的 destination」当父图的 startDestination → `not a direct child`；
  在 RouteDecoder 里指望缺失参数返回 null → 实际是被跳过，字段压根不出现。

## 七、参考资料

实际读取并据以实现的官方源码（raw.githubusercontent.com/androidx/androidx@androidx-main）：

- `navigation/navigation-common/src/commonMain/kotlin/androidx/navigation/serialization/RouteBuilder.kt`
- `.../serialization/RouteEncoder.kt`、`.../serialization/RouteDecoder.kt`、`.../serialization/NavTypeConverter.kt`
- `navigation/navigation-common/src/commonMain/kotlin/androidx/navigation/NavGraph.kt`
- `.../NavGraphBuilder.kt`、`.../NavGraphNavigator.kt`、`.../NavType.kt`、`.../NavDestination.kt`
- `navigation/navigation-compose/src/commonMain/kotlin/androidx/navigation/compose/NavHost.kt`

> 口径说明：`developer.android.com` 本机不可达，全部结论取自 androidx 仓库主干源码与其中的
> KDoc 原文，未参考第三方博客。
