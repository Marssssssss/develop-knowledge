"""Retrofit 动态代理、CallAdapter/Converter 查找与 Platform 分支的可执行模型。

对应 square/retrofit 主干：
  - `Retrofit.java`      ：create()/validateServiceInterface()/loadServiceMethod()/nextCallAdapter()
  - `ServiceMethod.java` ：parseAnnotations() 的返回类型前置校验
  - `HttpServiceMethod.java`：suspend 改写、responseType 校验、三种 adapt 实现
  - `Platform.java`      ：按 java.vm.name 选择 callbackExecutor / Reflection / BuiltInFactories
"""

from request_factory import (
    Annotation, MethodSpec, Param, RequestFactory, RetrofitError,
    BODY_METHODS, NON_BODY_METHODS,
)

# Platform 分支（Platform.java 静态块）
PLATFORM_DALVIK = "Dalvik"
PLATFORM_ROBOVM = "RoboVM"
PLATFORM_JVM = "JVM"

SUSPEND_ADAPTER = "SkipCallbackExecutor"


class CallAdapter:
    def __init__(self, factory_name, response_type):
        self.factory_name = factory_name
        self.response_type = response_type


class CallAdapterFactory:
    """对应 CallAdapter.Factory.get(returnType, annotations, retrofit)，返回 None 表示不支持。"""

    def __init__(self, name, supported_prefixes, skip_callback_executor=False):
        self.name = name
        self.supported_prefixes = tuple(supported_prefixes)
        self.skip_callback_executor = skip_callback_executor

    def get(self, return_type, annotations, retrofit):
        if not return_type.startswith(self.supported_prefixes):
            return None
        if self.skip_callback_executor and SUSPEND_ADAPTER not in annotations:
            return None
        return CallAdapter(self.name, self._response_type(return_type))

    def _response_type(self, return_type):
        if "<" in return_type and return_type.endswith(">"):
            return return_type[return_type.index("<") + 1:-1]
        return return_type


class ConverterFactory:
    def __init__(self, name, supported):
        self.name = name
        self.supported = set(supported)

    def response_body_converter(self, type_name, annotations, retrofit):
        return self.name if type_name in self.supported else None


class PlatformInfo:
    """按 java.vm.name 决定回调执行器与反射实现（Platform.java 的 switch）。"""

    def __init__(self, vm_name, sdk_int=24):
        self.vm_name = vm_name
        self.sdk_int = sdk_int
        if vm_name == PLATFORM_DALVIK:
            self.callback_executor = "AndroidMainExecutor"
            self.reflection = "Android24" if sdk_int >= 24 else "Legacy"
            self.built_in_factories = "Java8" if sdk_int >= 24 else "Legacy"
        elif vm_name == PLATFORM_ROBOVM:
            self.callback_executor = None
            self.reflection = "Legacy"
            self.built_in_factories = "Legacy"
        else:
            self.callback_executor = None
            self.reflection = "Java8"
            self.built_in_factories = "Java8"


def resolve_platform(vm_name, sdk_int=24):
    return PlatformInfo(vm_name, sdk_int)


class ServiceInterface:
    def __init__(self, name, methods, is_interface=True, type_parameters=(), super_interfaces=()):
        self.name = name
        self.methods = {m.name: m for m in methods}
        self.is_interface = is_interface
        self.type_parameters = tuple(type_parameters)
        self.super_interfaces = tuple(super_interfaces)


class Retrofit:
    def __init__(self, call_adapter_factories, converter_factories,
                 validate_eagerly=False, platform=None):
        self.call_adapter_factories = list(call_adapter_factories)
        self.converter_factories = list(converter_factories)
        self.validate_eagerly = validate_eagerly
        self.platform = platform or resolve_platform(PLATFORM_JVM)
        self.service_method_cache = {}

    # ---- create() ----
    def create(self, service):
        self._validate_service_interface(service)
        return ServiceProxy(self, service)

    def _validate_service_interface(self, service):
        if not service.is_interface:
            raise RetrofitError("API declarations must be interfaces.")
        self._check_type_parameters(service)

    def _check_type_parameters(self, service, root=None):
        root = root or service
        if service.type_parameters:
            msg = "Type parameters are unsupported on " + service.name
            if service is not root:
                msg += " which is an interface of " + root.name
            raise RetrofitError(msg)
        for parent in service.super_interfaces:
            self._check_type_parameters(parent, root)

    # ---- ServiceMethod ----
    def load_service_method(self, service, method):
        cached = self.service_method_cache.get(method.name)
        if cached is not None:
            return cached
        sm = ServiceMethod.parse_annotations(self, service, method)
        self.service_method_cache[method.name] = sm
        return sm

    # ---- nextCallAdapter ----
    def next_call_adapter(self, skip_past, return_type, annotations):
        if return_type is None:
            raise RetrofitError("returnType == null")
        if annotations is None:
            raise RetrofitError("annotations == null")
        try:
            start = self.call_adapter_factories.index(skip_past) + 1
        except ValueError:
            start = 0  # indexOf 返回 -1 ⇒ 从头找
        tried = []
        for i in range(start, len(self.call_adapter_factories)):
            f = self.call_adapter_factories[i]
            adapter = f.get(return_type, annotations, self)
            if adapter is not None:
                return adapter
            tried.append(f.name)
        msg = "Could not locate call adapter for %s.\n" % return_type
        if skip_past is not None and start > 0:
            msg += "  Skipped:" + "".join(
                "\n   * " + self.call_adapter_factories[i].name for i in range(start)
            ) + "\n"
        msg += "  Tried:" + "".join("\n   * " + n for n in tried)
        raise RetrofitError(msg)

    def call_adapter(self, return_type, annotations):
        return self.next_call_adapter(None, return_type, annotations)

    def next_response_body_converter(self, skip_past, type_name, annotations):
        try:
            start = self.converter_factories.index(skip_past) + 1
        except ValueError:
            start = 0
        for i in range(start, len(self.converter_factories)):
            name = self.converter_factories[i].response_body_converter(type_name, annotations, self)
            if name is not None:
                return name
        raise RetrofitError("Could not locate ResponseBody converter for %s." % type_name)

    def response_body_converter(self, type_name, annotations):
        return self.next_response_body_converter(None, type_name, annotations)


class ServiceMethod:
    """对应 ServiceMethod + HttpServiceMethod 的 parseAnnotations。"""

    def __init__(self, spec, adapter, converter, kind):
        self.spec = spec
        self.adapter = adapter
        self.converter = converter
        self.kind = kind  # CallAdapted / SuspendForResponse / SuspendForBody

    @staticmethod
    def parse_annotations(retrofit, service, method):
        factory = RequestFactory(method)
        return_type = method.return_type

        if "<T>" in return_type or "<?" in return_type or "*" in return_type:
            raise RetrofitError(
                "Method return type must not include a type variable or wildcard: %s" % return_type
            )
        if return_type == "void":
            raise RetrofitError("Service methods cannot return void.")

        is_suspend = method.is_suspend if hasattr(method, "is_suspend") else False
        annotations = [a.kind for a in method.annotations]
        if is_suspend:
            # suspend：把返回类型改写成 Call<responseType>
            wants_response = return_type.startswith("Response<") and return_type.endswith(">")
            body_type = return_type[len("Response<"):-1] if wants_response else return_type
            adapter_type = "Call<%s>" % body_type   # 官方：先解包 Response<T> 再包成 Call<T>
            annotations = annotations + [SUSPEND_ADAPTER]
        else:
            adapter_type = return_type
            wants_response = False

        try:
            adapter = retrofit.call_adapter(adapter_type, annotations)
        except RetrofitError as e:
            raise RetrofitError("Unable to create call adapter for %s" % adapter_type) from e

        response_type = adapter.response_type
        if response_type == "okhttp3.Response":
            raise RetrofitError(
                "'okhttp3.Response' is not a valid response body type. Did you mean ResponseBody?"
            )
        if response_type == "Response":
            raise RetrofitError("Response must include generic type (e.g., Response<String>)")
        if factory.http_method == "HEAD" and response_type not in ("Void", "Unit"):
            raise RetrofitError("HEAD method must use Void or Unit as response type.")

        try:
            converter = retrofit.response_body_converter(response_type, annotations)
        except RetrofitError as e:
            raise RetrofitError("Unable to create converter for %s" % response_type) from e

        if not is_suspend:
            kind = "CallAdapted"
        elif wants_response:
            kind = "SuspendForResponse"
        else:
            kind = "SuspendForBody"
        return ServiceMethod(method, adapter, converter, kind)


class ServiceProxy:
    """对应 Proxy.newProxyInstance 生成的代理对象。"""

    def __init__(self, retrofit, service):
        self._retrofit = retrofit
        self._service = service

    def invoke(self, name, args=None):
        if name in ("toString", "equals", "hashCode"):
            # method.getDeclaringClass() == Object.class ⇒ method.invoke(this, args)，不建 ServiceMethod
            return ("object", name)
        method = self._service.methods[name]
        if method.is_default:
            return ("default", name, args)
        sm = self._retrofit.load_service_method(self._service, method)
        return ("call", sm.kind, sm.adapter.factory_name, sm.converter)
