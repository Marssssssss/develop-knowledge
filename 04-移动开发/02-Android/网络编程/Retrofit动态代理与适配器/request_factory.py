"""RequestFactory 的注解校验模型（对应 retrofit2.RequestFactory）。

所有错误消息与判据均取自 square/retrofit 主干 `RequestFactory.java` 的原文字符串。
"""

import re

PATH_PARAM_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_-]*")

# 官方 parseHttpMethodAndPath 的第三个参数：哪些方法带请求体
BODY_METHODS = {"POST", "PUT", "PATCH"}
NON_BODY_METHODS = {"GET", "HEAD", "DELETE", "OPTIONS"}

URL_TYPES = {"okhttp3.HttpUrl", "String", "java.net.URI", "android.net.Uri"}


class RetrofitError(Exception):
    pass


class Annotation:
    def __init__(self, kind, value=None):
        self.kind = kind
        self.value = value

    def __repr__(self):
        return "@%s(%s)" % (self.kind, self.value)


class Param:
    def __init__(self, index, annos, type_name="String"):
        self.index = index
        self.annos = list(annos)
        self.type_name = type_name

    @property
    def kind(self):
        return self.annos[0].kind if self.annos else None


class MethodSpec:
    def __init__(self, name, annotations, params, return_type="Call<X>",
                 declaring_class="Object", is_default=False, is_static=False,
                 is_synthetic=False, is_suspend=False):
        self.is_suspend = is_suspend
        self.name = name
        self.annotations = list(annotations)
        self.params = list(params)
        self.return_type = return_type
        self.declaring_class = declaring_class
        self.is_default = is_default
        self.is_static = is_static
        self.is_synthetic = is_synthetic

    def anno(self, kind):
        for a in self.annotations:
            if a.kind == kind:
                return a
        return None


def parse_http_method_and_path(method, path):
    """返回 (http_method, has_body, relative_path, query_part)。"""
    if not path:
        raise RetrofitError("Missing either @%s URL or @Url parameter." % method)
    if "?" in path:
        base, query = path.split("?", 1)
    else:
        base, query = path, None
    if query is not None and "{" in query:
        raise RetrofitError(
            "URL query string \"%s\" must not have replace block. "
            "For dynamic query parameters use @Query." % query
        )
    for name in re.findall(r"\{([^}]*)\}", base):
        if not PATH_PARAM_RE.fullmatch(name):
            raise RetrofitError(
                "@Path parameter name must match %s. Found: %s" % (PATH_PARAM_RE.pattern, name)
            )
    return base, query


class RequestFactory:
    def __init__(self, spec):
        self.spec = spec
        self.http_method = None
        self.has_body = False
        self.relative_url = None
        self.is_multipart = False
        self.is_form_encoded = False
        self.has_body_param = False
        self.has_url_param = False
        self._parse(spec)

    def _parse(self, spec):
        method_annos = [a for a in spec.annotations
                        if a.kind in BODY_METHODS or a.kind in NON_BODY_METHODS]
        if not method_annos:
            raise RetrofitError("HTTP method annotation is required (e.g., @GET, @POST, etc.).")
        if len(method_annos) > 1:
            raise RetrofitError(
                "Only one HTTP method is allowed. Found: %s and %s."
                % (method_annos[0].kind, method_annos[1].kind)
            )
        m = method_annos[0]
        self.http_method = m.kind
        self.has_body = m.kind in BODY_METHODS

        encodings = [a for a in spec.annotations if a.kind in ("Multipart", "FormUrlEncoded")]
        if len(encodings) > 1:
            raise RetrofitError("Only one encoding annotation is allowed.")
        if encodings:
            if not self.has_body:
                kind = encodings[0].kind
                raise RetrofitError(
                    "%s can only be specified on HTTP methods with request body (e.g., @POST)."
                    % ("Multipart" if kind == "Multipart" else "FormUrlEncoded")
                )
            self.is_multipart = encodings[0].kind == "Multipart"
            self.is_form_encoded = not self.is_multipart

        for a in spec.annotations:
            if a.kind == "Headers":
                if not a.value:
                    raise RetrofitError("@Headers annotation is empty.")
                for header in a.value:
                    if ":" not in header:
                        raise RetrofitError(
                            "@Headers value must be in the form \"Name: Value\". Found: \"%s\"" % header
                        )

        if m.value is None:
            # 没有 URL（全靠 @Url 参数）
            self.relative_url = None
        else:
            self.relative_url, _ = parse_http_method_and_path(m.kind, m.value)

        self._parse_params(spec)

    def _parse_params(self, spec):
        seen_query = False
        for p in spec.params:
            if len(p.annos) > 1:
                raise RetrofitError("Multiple Retrofit annotations found, only one allowed.")
            if not p.annos:
                raise RetrofitError("No Retrofit annotation found.")
            kind = p.annos[0].kind
            if kind == "Body" and not self.has_body:
                raise RetrofitError("Non-body HTTP method cannot contain @Body.")
            if kind == "Body":
                self.has_body_param = True
            if kind == "Url":
                if self.has_url_param:
                    raise RetrofitError("Multiple @Url method annotations found.")
                if spec.anno(self.http_method) is not None and spec.anno(self.http_method).value:
                    raise RetrofitError("@Url cannot be used with @%s URL" % self.http_method)
                if p.type_name not in URL_TYPES:
                    raise RetrofitError(
                        "@Url must be okhttp3.HttpUrl, String, java.net.URI, or android.net.Uri type."
                    )
                self.has_url_param = True
            if kind == "Path" and self.has_url_param:
                raise RetrofitError("@Path parameters may not be used with @Url.")
            if kind in ("Query", "QueryName", "QueryMap"):
                seen_query = True
            if kind == "Url" and seen_query:
                raise RetrofitError("A @Url parameter must not come after a @%s." % "Query")
            if kind == "Path":
                if seen_query:
                    raise RetrofitError("A @Path parameter must not come after a @Query.")
                if self.relative_url is None:
                    raise RetrofitError(
                        "@Path can only be used with relative url on @%s" % self.http_method
                    )
                name = p.annos[0].value or ""
                if name not in re.findall(r"\{([^}]*)\}", self.relative_url or ""):
                    raise RetrofitError(
                        "URL \"%s\" does not contain \"{%s}\"." % (self.relative_url, name)
                    )

        if self.relative_url is None and not self.has_url_param:
            raise RetrofitError("Missing either @%s URL or @Url parameter." % self.http_method)

        if self.is_form_encoded:
            if not any(p.kind == "Field" for p in spec.params):
                raise RetrofitError("Form-encoded method must contain at least one @Field.")
        if self.is_multipart:
            if not any(p.kind == "Part" for p in spec.params):
                raise RetrofitError("Multipart method must contain at least one @Part.")
