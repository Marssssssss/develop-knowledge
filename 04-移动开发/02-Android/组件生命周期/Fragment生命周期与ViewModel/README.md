# Fragment 生命周期与 ViewModel

## 简介

Fragment 是 Android 的"可复用 UI 片段",它比 Activity 多出一层**视图生命周期**。官方源码里 `Fragment` 用一个整数状态机(`mState`,9 个常量)串起两套并行的生命周期,并在状态切换之间插入两个**过渡态**(`AWAITING_EXIT_EFFECTS` / `AWAITING_ENTER_EFFECTS`)。**ViewModel** 则解决另一半问题:把"数据"从"视图"里剥出来,让它在配置变更(旋转屏幕)中存活。

**关键概念清单**：

- **Fragment 状态机**：`mState ∈ {-1,0,1,2,3,4,5,6,7}`,两个过渡态只在执行事务动画/副作用期间出现
- **视图生命周期**：`onCreateView` → `onViewCreated` → `onDestroyView`,与 Fragment 生命周期**不同频**
- **返回栈行为**：Fragment 进返回栈时 `onDestroyView` 会跑,但 `onCreate`/`onDestroy` 不跑;回到前台时 `onCreateView` 重跑
- **viewLifecycleOwner**：`getViewLifecycleOwner()` 在视图销毁后立即抛 `IllegalStateException`
- **ViewModelStoreOwner**：`by viewModels()` 取 Fragment 自己的 store;`by activityViewModels()` 取宿主的 store
- **viewModelScope**：自动取消的协程作用域,只随 ViewModel 存亡,不随视图

**历史背景**：Framework 的 `android.app.Fragment` 在 API 28 废弃,现行为 `androidx.fragment.app.Fragment`。`VIEW_CREATED` 状态与 `getViewLifecycleOwner()` 在 AndroidX 1.0 引入,用来修掉"观察者在视图重建后仍绑定旧视图 → 内存泄漏 / 空指针"这一类经典 bug。

## 原理详解

### 状态机分步说明

`Fragment.mState` 的 9 个常量(取自 `androidx/fragment/app/Fragment.java`):

| 常量 | 值 | 含义 |
| --- | --- | --- |
| `INITIALIZING` | -1 | 刚构造,尚未 `onAttach` |
| `ATTACHED` | 0 | `onAttach` 已跑,`mHost` 可用 |
| `CREATED` | 1 | `onCreate` 已跑 |
| `VIEW_CREATED` | 2 | `onCreateView`/`onViewCreated` 已跑 |
| `AWAITING_EXIT_EFFECTS` | 3 | **过渡态**:已到 `VIEW_CREATED`,但退出副作用(`onDestroyView`)尚未执行 |
| `ACTIVITY_CREATED` | 4 | 宿主 Activity 已 `onCreate` 完成,Fragment 视图已就位 |
| `STARTED` | 5 | `onStart` 已跑 |
| `AWAITING_ENTER_EFFECTS` | 6 | **过渡态**:已到 `STARTED`,但进入副作用(`onResume`)尚未执行 |
| `RESUMED` | 7 | `onResume` 已跑,可见且可交互 |

状态推进由 `FragmentStateManager` 驱动,核心规则是:**状态只能逐级升降**,每次 `computeExpectedState()` 算出目标状态后,一层一层地补跑回调。两个 `AWAITING_*` 就是"状态已经改了、回调还没跑"的中间拍点 —— 它们存在的意义是让 `FragmentManager` 在跑动画/执行事务时能看清"这个 Fragment 到底处在回调的哪一侧",避免重复执行副作用。

### 两套生命周期的对照

| 时机 | Fragment 生命周期 | 视图生命周期 |
| --- | --- | --- |
| 首次显示 | `onAttach` → `onCreate` | `onCreateView` → `onViewCreated` |
| 进返回栈(被覆盖) | `onPause` `onStop`(不销毁) | `onDestroyView` ← **会跑** |
| 返回栈回来 | `onStart` `onResume`(不再 `onCreate`) | `onCreateView` ← **重跑** |
| 真正移除 | `onDestroy` → `onDetach` | 已在前面 `onDestroyView` 跑过 |

**结论**:任何"绑定 UI"的资源 —— 观察者、`view` 引用、`lifecycleScope` —— 必须挂在 `viewLifecycleOwner` 上;只有与 Fragment 等长的资源(如共享的 `ViewModel` 引用)才挂 `this`。

### 状态推进的 ASCII 图

```
  INITIALIZING(-1)
        | onAttach
  ATTACHED(0)
        | onCreate            ← 进返回栈再回来时**不重跑**
  CREATED(1)
        | onCreateView + onViewCreated
  VIEW_CREATED(2)  ←──────────────┐
        | (宿主 Activity onCreate 完成)  │ 返回栈回来:onCreateView 重跑,
  ACTIVITY_CREATED(4)              │ 从 VIEW_CREATED 再往上走
        | onStart                │
  STARTED(5)  ───────────────────┘
        | onResume
  RESUMED(7)

  向上推进时:AWAITING_ENTER_EFFECTS(6) 是 STARTED→RESUMED 的中间拍点
  向下回退时:AWAITING_EXIT_EFFECTS(3)  是 VIEW_CREATED→ACTIVITY_CREATED 之间的中间拍点
```

### ViewModel 的作用域与清理顺序

```
Activity(ViewModelStoreOwner)
  └─ ViewModelStore
       ├─ DetailViewModel      ← by viewModels()    (Fragment 自己的 store)
       └─ SharedViewModel      ← by activityViewModels() (宿主的 store)

配置变更(旋转屏幕):
  Activity 销毁重建 → ViewModelStore 被**保留** → 同一个 ViewModel 实例交还给新 Activity
  onCleared() **不触发**

真正 finish Activity:
  ViewModelStore.clear() → 遍历 mBagOfTags 逐个 close()
      → viewModelScope 的 Job 在此被 cancel   ← 顺序在前
      → onCleared()                          ← 顺序在后
```

**注意 `viewModelScope` 先于 `onCleared()` 取消**:`viewModelScope` 是懒创建的,创建时通过 `putCloseable()` 存进 `mBagOfTags`;`ViewModel.clear()` 先关闭 bag 里的所有 closeable,再调 `onCleared()`。所以 `onCleared()` 里可以安全地做最后清理,而不必担心协程还在跑。

### 核心 API

| 类 / 方法 | 签名 | 说明 |
| --- | --- | --- |
| `Fragment.getViewLifecycleOwner()` | `LifecycleOwner` | 视图销毁后抛 `IllegalStateException` |
| `Fragment.getViewLifecycleOwnerLiveData()` | `LiveData<LifecycleOwner>` | 观察视图生命周期本身 |
| `Fragment.getLifecycle()` | `Lifecycle` | Fragment 自身生命周期 |
| `Fragment.getViewModelStore()` | `ViewModelStore` | `by viewModels()` 的底层来源 |
| `Fragment.getParentFragment()` | `Fragment?` | `by viewModels({requireParentFragment()})` 用于父子共享 |
| `ViewModel.getTag(String)` / `setTagIfAbsent` | `<T> T?` / `<T> T` | `mBagOfTags` 的公开入口 |

## 对比 / 选型

| 需求 | 选择 | 原因 |
| --- | --- | --- |
| 数据跨配置变更存活 | `ViewModel` | 唯一官方推荐的屏幕旋转存活载体 |
| 多个 Fragment 共享数据 | `by activityViewModels()` | 作用域 = 宿主 Activity 的 `ViewModelStore` |
| 父子 Fragment 共享 | `by viewModels({ requireParentFragment() })` | 作用域 = 父 Fragment |
| 观察 UI 状态 | `viewLifecycleOwner` + `LiveData`/`StateFlow` | 自动随视图销毁解绑 |
| 与视图同命的异步任务 | `viewLifecycleOwner.lifecycleScope` | `onDestroyView` 时自动取消 |
| 与 Fragment 同命的异步任务 | `lifecycleScope` | 跨视图重建继续存活 |

## 环境准备

- Android Studio Hedgehog(2023.1+)或更新
- minSdk 21,compileSdk 34
- `androidx.fragment:fragment-ktx:1.6.2+`(提供 `by viewModels()` 委托)
- `androidx.lifecycle:lifecycle-viewmodel-ktx:2.7.0+`(提供 `viewModelScope`)
- Python 3.8+(跑 `python/fragment_state_check.py` 自检)

## 运行方式

```bash
# 1) 跑 Python 自检(33 条断言,验证状态机与 ViewModel 作用域语义)
python "04-移动开发/02-Android/组件生命周期/Fragment生命周期与ViewModel/python/fragment_state_check.py"

# 2) Kotlin 用法:把 kotlin/FragmentViewModelDemo.kt 放进 Android 工程,
#    在布局里挂载 DetailFragment,logcat 过滤 "DetailFragment." 观察回调序列
```

## 关键代码片段

### 两套生命周期各挂各的(对应 Kotlin Demo)

```kotlin
override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
    super.onViewCreated(view, savedInstanceState)

    // 绑 UI 的观察者 → viewLifecycleOwner,onDestroyView 自动解绑
    selfVm.title.observe(viewLifecycleOwner) { title -> log("observe title=$title") }

    // 与视图同命的协程 → onDestroyView 时自动取消
    viewLifecycleOwner.lifecycleScope.launch { log("view-scoped coroutine") }

    // 与 Fragment 同命的协程 → 跨视图重建继续活着
    lifecycleScope.launch { log("fragment-scoped coroutine") }
}
```

### 两个 ViewModel 作用域

```kotlin
private val selfVm: DetailViewModel by viewModels()        // Fragment 自己的 store
private val sharedVm: SharedViewModel by activityViewModels()  // 宿主 Activity 的 store
```

### ViewModel 里启动的协程不会泄漏

```kotlin
class DetailViewModel : ViewModel() {
    init {
        // viewModelScope 会在 onCleared() **之前**被 cancel
        viewModelScope.launch { println("viewModelScope started") }
    }
    override fun onCleared() {
        // 这里执行时,viewModelScope 已经停了
    }
}
```

## 性能与边界

- **状态机开销**:每次状态变更最多跑 8 次回调,但 `FragmentStateManager` 用 `mState` 做短路,不会重复调用;`FragmentTransaction.setReorderingAllowed(true)` 可减少中间态回调次数
- **视图重建成本**:返回栈来回一次 = 一次完整的 `onCreateView`/`onDestroyView`;列表页建议配合 `RecyclerView` 复用,或在 `ViewModel` 里缓存滚动位置
- **ViewModel 实例粒度**:`by viewModels()` 每个 Fragment 实例一份;列表里 100 个 Fragment 就是 100 个 store,注意 `ViewModelStore` 是按 key 存的 map,不会互相覆盖但会占内存
- **`getViewLifecycleOwner()` 的抛错边界**:在 `onCreateView` **之前**或 `onDestroyView` **之后**调用都会抛 `IllegalStateException("Can't access the Fragment View's LifecycleOwner ...")`
- **配置变更不算销毁**:`onCleared()` 只在 `ViewModelStoreOwner` 被永久销毁时触发 —— `isChangingConfigurations == true` 时系统保留 store

## 注意事项与常见坑

1. **观察者挂 `this` 而不是 `viewLifecycleOwner`**:视图重建后旧观察者仍持着已销毁的 `View`,是 `Fragment` 内存泄漏的头号来源;`LiveData.observe(this)` 在 Fragment 里几乎总是错的。
2. **`viewLifecycleOwner` 在视图销毁后立刻失效**:`onDestroyView` 之后访问会抛异常,所以不要把它缓存到字段里跨回调使用 —— 每次在 `onViewCreated` 内取。
3. **`onCreate` 只跑一次,`onCreateView` 每次显示都跑**:在 `onCreateView` 里做一次性初始化(比如注册广播)会重复注册;这类事情该放 `onCreate`。
4. **`viewModelScope` 早于 `onCleared()` 取消**:如果 `onCleared()` 里依赖协程已完成的中间结果,拿不到 —— 需要 `runBlocking` 或改在 `ViewModel` 内部记录状态。
5. **`by activityViewModels()` 在 `onCreate` 之前调用会崩**:需要 `mHost`/宿主已 attach;在 Fragment 字段初始化时用它是安全的(懒加载),但在 `Fragment()` 构造器里手动调 `ViewModelProvider` 就会 NPE。
6. **`Fragment` 之间不要直接传 `ViewModel` 实例**:共享要用 `activityViewModels()` / `viewModels({ requireParentFragment() })`,直接持有对方引用会让生命周期互相纠缠。
7. **`setReorderingAllowed(false)`(默认)会产生中间态回调**:同一事务里的 ADD + REMOVE,旧 Fragment 可能先 `onCreate` 再立刻 `onDestroyView`;合并事务时显式 `setReorderingAllowed(true)`。
8. **`AWAITING_*` 不是给你用的**:它是 `FragmentStateManager` 的内部拍点,业务代码里判断生命周期应使用 `lifecycle.currentState`,不要依赖 `mState`(反射读取版本间不稳定)。
9. **返回栈里 Fragment 的 `savedInstanceState` 与视图无关**:视图重建时 `onCreateView` 收到的是 Fragment 级 `savedInstanceState`,View 级的恢复走 `View.onRestoreInstanceState`,两者常被混淆。

## 参考资料(实际阅读过的权威来源)

- [Fragment.java — androidx/fragment (GitHub)](https://github.com/androidx/androidx/blob/androidx-main/fragment/fragment/src/main/java/androidx/fragment/app/Fragment.java) — 9 个 `mState` 常量定义与取值、`AWAITING_*` 过渡态注释、`getViewLifecycleOwner()` 的抛错条件
- [ViewModel.kt — androidx/lifecycle (GitHub)](https://github.com/androidx/androidx/blob/androidx-main/lifecycle/lifecycle-viewmodel/src/commonMain/kotlin/androidx/lifecycle/ViewModel.kt) — `viewModelScope` 的懒创建与 `putCloseable()` 注册
- [ViewModel.java — androidx/lifecycle (GitHub)](https://github.com/androidx/androidx/blob/androidx-main/lifecycle/lifecycle-viewmodel/src/main/java/androidx/lifecycle/ViewModel.java) — `clear()` 中"先 close 所有 closeable、再 `onCleared()`"的顺序
- [Fragments | Android Developers](https://developer.android.com/guide/fragments/lifecycle) — 视图生命周期与 Fragment 生命周期的官方对照(本轮仅取到该页的部分内容,`developer.android.com` 在抓取时有间歇性超时,以上源码链接为准)
- [ViewModel overview | Android Developers](https://developer.android.com/topic/libraries/architecture/viewmodel) — ViewModel 存活范围与配置变更语义

> 口径说明:`FragmentStateManager` 内部的**精确调用顺序**(哪个状态先 `computeExpectedState()`)本轮未取到源码,README 中关于 `AWAITING_*` 的解释依据 `Fragment.java` 中的常量注释归纳;Python 自检脚本验证的是**状态常量取值与升降规则**,不是 `FragmentStateManager` 的逐步复刻。
