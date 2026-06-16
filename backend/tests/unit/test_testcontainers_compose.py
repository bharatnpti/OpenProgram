from __future__ import annotations


def test_testcontainers_compose_import_is_available() -> None:
    from testcontainers.compose import DockerCompose

    assert DockerCompose.__name__ == "DockerCompose"
