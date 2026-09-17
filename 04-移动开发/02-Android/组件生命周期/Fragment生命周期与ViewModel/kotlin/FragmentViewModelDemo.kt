package demo.fragment

import android.content.Context
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import androidx.fragment.app.Fragment
import androidx.fragment.app.activityViewModels
import androidx.fragment.app.viewModels
import androidx.lifecycle.LiveData
import androidx.lifecycle.MutableLiveData
import androidx.lifecycle.ViewModel
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/**
 * Fragment 生命周期 + ViewModel 作用域(Kotlin 用法)。
 *
 * 依赖:androidx.fragment:fragment-ktx、androidx.lifecycle:lifecycle-viewmodel-ktx。
 *
 * 两个最容易踩的点:
 *  1) Fragment 的 **view 生命周期** 与 **Fragment 生命周期** 是两套:
 *     入返回栈时 onDestroyView 会跑,但 onDestroy/onDetach 不会;再次显示时 onCreateView 重跑,
 *     而 onCreate 只跑一次。所以绑 UI 的协程/观察者要用 viewLifecycleOwner,
 *     与 Fragment 等长的资源才用 this(Fragment 本身)。
 *  2) ViewModel 的作用域由 ViewModelStoreOwner 决定:
 *     `by viewModels()` 取 Fragment 自己的 store,`by activityViewModels()` 取 Activity 的 store —
 *     后者才能在多个 Fragment 之间共享数据,也只有 Activity 真正销毁时才会 onCleared。
 */
class DetailFragment : Fragment() {

    // 作用域 = 本 Fragment 的 ViewModelStore
    private val selfVm: DetailViewModel by viewModels()

    // 作用域 = 宿主 Activity 的 ViewModelStore → 可在多个 Fragment 间共享
    private val sharedVm: SharedViewModel by activityViewModels()

    override fun onAttach(context: Context) {
        super.onAttach(context)
        log("onAttach")
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        log("onCreate")                     // 入返回栈再回来时不会再触发
        sharedVm.register(this)
    }

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View {
        log("onCreateView")                 // 每次显示都会触发
        return View(requireContext())
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        log("onViewCreated")

        // 绑定 UI 的观察者用 viewLifecycleOwner,而不是 this
        selfVm.title.observe(viewLifecycleOwner) { title -> log("observe title=$title") }

        // 与 view 同生命周期的协程:onDestroyView 时自动取消
        viewLifecycleOwner.lifecycleScope.launch { log("view-scoped coroutine") }

        // 与 Fragment 同生命周期的协程
        lifecycleScope.launch { log("fragment-scoped coroutine") }
    }

    override fun onStart() = super.onStart().also { log("onStart") }
    override fun onResume() = super.onResume().also { log("onResume") }
    override fun onPause() = super.onPause().also { log("onPause") }
    override fun onStop() = super.onStop().also { log("onStop") }

    override fun onDestroyView() {
        log("onDestroyView")                // 在 onStop 之后、onDestroy 之前;与 onCreateView 是否返回非空无关
        super.onDestroyView()
    }

    override fun onDestroy() {
        log("onDestroy")                    // 只有 Fragment 真正被移除时才触发
        super.onDestroy()
    }

    override fun onDetach() = super.onDetach().also { log("onDetach") }

    private fun log(what: String) = println("DetailFragment.$what")
}

class DetailViewModel : ViewModel() {

    private val _title = MutableLiveData("initial")
    val title: LiveData<String> = _title

    private val _items = MutableStateFlow<List<String>>(emptyList())
    val items: StateFlow<List<String>> = _items.asStateFlow()

    init {
        // viewModelScope 在 onCleared 之前被取消,所以这里启动的协程不会泄漏
        viewModelScope.launch {
            println("viewModelScope started, scope=$this")
        }
    }

    fun load() {
        _title.value = "loaded"
        _items.value += "row"
    }

    override fun onCleared() {
        println("DetailViewModel.onCleared —— 只有 owner 被永久销毁时才触发(配置变更不会)")
    }
}

/** Activity 作用域的 ViewModel:多个 Fragment 通过 activityViewModels() 共享同一实例。 */
class SharedViewModel : ViewModel() {
    private val fragments = mutableListOf<Any>()

    fun register(fragment: Any) {
        fragments += fragment
    }

    override fun onCleared() {
        fragments.clear()
        println("SharedViewModel.onCleared —— 宿主 Activity 结束时才清理")
    }
}
