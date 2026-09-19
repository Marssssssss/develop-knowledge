# Web Components：Custom Elements 的升级与 reaction 队列

## 一、简介

Custom Elements 最反直觉的地方不在 API，而在**时机**：`customElements.define()` 之后，页面上已经存在的元素不会立刻变成自定义元素，而是被塞进一条 **custom element reaction 队列**，按顺序升级；构造函数与 `connectedCallback` 的调用顺序、`isConnected` 在回调里的取值、元素移动时回调怎么走，全都由这条队列决定。

本 demo 用 Python（+ Go 对照）实现一个最小 custom elements 运行时，把 WHATWG 规范里最容易写错的几条变成可执行判据（27 断言全通过）。

代码：`web_components.py` + `selfcheck_web_components.py` + `web_components.go`。

## 二、原理详解

### 2.1 五种 element state 与升级

元素状态：`undefined` →（`define` 或插入文档时）→ `custom`，失败则 `failed`；另有 `uncustomized` / `precustomized`。

- 升级**只作用于** state 为 `undefined` / `uncustomized` 的元素，因此重复升级是幂等的；
- *"upgrades only apply to elements in the document"* —— **游离的元素不会被自动升级**，必须先插入文档，或者显式调用 `customElements.upgrade(root)`。

### 2.2 构造函数里什么都看不到

规范明确要求：构造函数**不得**检查 attributes / children，也不得新增它们。理由很实在——在 non-upgrade 情形下（例如 `document.createElement` 之后才 define），此刻属性与子节点都还不存在。

所以本 demo 在调用构造函数前，临时把 `attributes` / `children` 摘走，构造完再挂回去。断言直接验证「构造期间读到的是空」。

正确做法是 *"work should be deferred to connectedCallback as much as possible"*。

### 2.3 reaction 队列的三个后果

1. **`connectedCallback` 可能被调用多次**：元素断开再连上，就会再一次。所以初始化代码必须能重入。
2. **入队后断开，回调照跑**：规范原文是 *"An element's connectedCallback can be queued before the element is disconnected, but as the callback queue is still processed, it results in a connectedCallback for an element that is no longer connected"*。此时回调里 `isConnected === false` —— 想在回调里发起请求的话，这一条直接决定你会不会发出去一堆无用请求。
3. **处理期间新入队的 reaction 追加到同一轮尾部**，不会开新一轮（对应规范里的 reaction stack / backup element queue）。

### 2.4 移动元素

默认行为是依次调用 `disconnectedCallback` + `connectedCallback`；如果元素实现了 `connectedMoveCallback`，则**取代**默认的这两段（§4.13.2.1 Preserving custom element state when moved）。这条是为了让元素在 DOM 内移动时保住内部状态（比如视频播放位置、滚动位置）。

### 2.5 attributeChangedCallback 的门槛

只有出现在 `observedAttributes` 里的属性才会触发；在元素升级**之前**设置的属性也不会补发（升级后才开始观察）。

## 三、对比

| 机制 | 时机 | 可否多次 |
| --- | --- | --- |
| `constructor` | 升级那一刻 | 否（每个元素一次） |
| `connectedCallback` | 每次进入文档 | **是** |
| `disconnectedCallback` | 每次离开文档 | 是 |
| `attributeChangedCallback` | 观察中的属性变化 | 是 |
| `connectedMoveCallback` | DOM 内移动 | 是（取代默认两段） |

与之相比，React / Vue 的生命周期由框架调度器驱动、与 DOM 插入时机解耦；custom elements 的回调则**直接挂在 DOM 操作上**，这也是它难写对的根本原因。

## 四、环境

- Python 3.8+（标准库）
- Go 1.20+（对照实现）

## 五、运行方式

```bash
python selfcheck_web_components.py
# web_components: 27/27 assertions passed
```

## 六、注意事项与常见坑

1. **负向断言要先确认前提成立。** 开发期「未定义 connectedMoveCallback 时应走默认两段」一直失败，查到最后是**基类自己就定义了这个回调**——负向判据的前提被自己破坏了。写负向用例时先断言「前提为假」。
2. **升级已连接的元素要补一次 connectedCallback**，且只能补一次：把这次入队放在 `upgrade_element` 里（而不是 `connect` 里），否则「connect 触发升级」的路径会触发两次。
3. **未定义的名字（普通 `<div>`）不该有任何 reaction**。`connect` 里若无条件入队 `connectedCallback`，普通元素也会被回调。
4. **构造期间摘走 attributes/children 后必须挂回去**，且构造抛异常时要正确置 `failed` 并恢复。
5. **`observedAttributes` 是静态清单**，改它不会追溯已发生的变化。

## 七、性能边界

- reaction 队列是 FIFO，单轮处理是 O(reaction 数)；处理期间新入队的 reaction 会在同一轮尾部继续，最坏情况下一轮可能很长。
- 升级是逐个元素的；一次性插入大量未升级节点会产生等量的 upgrade reaction。
- 属性回调只对观察清单生效，清单过长会让每次 `setAttribute` 多一次 O(清单长度) 扫描。

## 八、参考资料（实际读过）

- WHATWG HTML · 4.13 Custom elements（§4.13.5 Upgrades、§4.13.6 Custom element reactions、§4.13.2.1 Preserving custom element state when moved、构造函数约束、`upgrade()` 方法、reaction 队列示例输出）— https://html.spec.whatwg.org/multipage/custom-elements.html
- MDN · Using custom elements / Using shadow DOM（可达，用于交叉核对术语）— https://developer.mozilla.org/en-US/docs/Web/API/Web_components/Using_custom_elements
