"""演示：一个小目录的处理结果（信封校验 + 关系推导）。

运行：python python/main.py
"""

from catalog import Entity, check_envelope, deduce_relations, parse_entity_ref

CATALOG = [
    {
        "apiVersion": "backstage.io/v1alpha1",
        "kind": "Component",
        "metadata": {
            "name": "artist-web",
            "namespace": "default",
            "tags": ["java"],
            "labels": {"example.com/custom": "custom_label_value"},
            "annotations": {"circleci.com/project-slug": "github/example-org/artist-website"},
            "links": [{"url": "https://admin.example-org.com", "title": "Admin Dashboard"}],
        },
        "spec": {
            "type": "website",
            "lifecycle": "production",
            "owner": "artist-relations-team",
            "system": "public-websites",
            "providesApis": ["artist-api"],
            "dependsOn": ["resource:default/artists-db"],
        },
    },
    {
        "apiVersion": "backstage.io/v1alpha1",
        "kind": "Component",
        "metadata": {"name": "artist_web_lookup", "namespace": "default"},
        "spec": {
            "type": "service",
            "lifecycle": "experimental",
            "owner": "artist-relations-team",
            "dependencyOf": ["artist-web"],
        },
    },
    {
        "apiVersion": "backstage.io/v1alpha1",
        "kind": "User",
        "metadata": {"name": "alice", "namespace": "ghe"},
        "spec": {"memberOf": ["ghe/artist-relations-team"]},
    },
]

KIND_MAP = {"Component": "Component", "User": "User"}


def main() -> None:
    for raw in CATALOG:
        errors = check_envelope(raw)
        name = raw["metadata"]["name"]
        if errors:
            print(f"[reject] {raw['kind']}/{name}: {errors}")
            continue
        entity = Entity(
            kind=raw["kind"],
            name=name,
            namespace=raw["metadata"].get("namespace", "default"),
            spec=raw["spec"],
        )
        print(f"[accept] {entity.kind}/{name} ({len(errors)} errors)")
        for source, rtype, target in deduce_relations(entity):
            print(f"    {source} --{rtype}--> {target}")

    print()
    print("实体引用的三种写法 → 规范化结果：")
    for text, kind in [
        ("artist-relations-team", "Group"),
        ("group:default/dev.infra", "Group"),
        ("resource:default/artists-db", "Component"),
        ("ghe/alice", "User"),
    ]:
        print(f"    {text!r:36} -> {parse_entity_ref(text, default_kind=kind)}")


if __name__ == "__main__":
    main()
