import pytest

from ark_angel.registry import Registry


class Base:
    pass


def test_register_and_get() -> None:
    registry: Registry[Base] = Registry("widget")

    @registry.register("thing")
    class Thing(Base):
        pass

    assert registry.get("thing") is Thing
    assert registry.names() == ["thing"]


def test_create_instantiates_registered_class() -> None:
    registry: Registry[Base] = Registry("widget")

    @registry.register("thing")
    class Thing(Base):
        def __init__(self, value: int = 0) -> None:
            self.value = value

    instance = registry.create("thing", value=5)
    assert isinstance(instance, Thing)
    assert instance.value == 5


def test_register_duplicate_name_raises() -> None:
    registry: Registry[Base] = Registry("widget")

    @registry.register("thing")
    class Thing(Base):
        pass

    with pytest.raises(ValueError):

        @registry.register("thing")
        class OtherThing(Base):
            pass


def test_get_unknown_name_raises_key_error() -> None:
    registry: Registry[Base] = Registry("widget")

    with pytest.raises(KeyError):
        registry.get("missing")


def test_builtin_ingestion_source_and_analyzer_are_registered() -> None:
    import ark_angel.analysis.rules  # noqa: F401
    import ark_angel.ingest.file_source  # noqa: F401
    from ark_angel.registry import analyzers, ingestion_sources

    assert "file" in ingestion_sources.names()
    assert "rules" in analyzers.names()
