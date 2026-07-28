import tomllib
from pathlib import Path


ROOT = Path(__file__).parents[2]


def _packages(lock: dict[str, object], name: str) -> list[dict[str, object]]:
    return [
        package
        for package in lock["package"]
        if package["name"] == name
    ]


def test_torch_and_torchvision_are_locked_to_the_same_cpu_index():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))

    dependencies = project["project"]["dependencies"]
    assert any(value.split("=", 1)[0] == "torchvision" for value in dependencies)
    assert project["tool"]["uv"]["sources"]["torch"] == {"index": "pytorch"}
    assert project["tool"]["uv"]["sources"]["torchvision"] == {"index": "pytorch"}

    for name in ("torch", "torchvision"):
        packages = _packages(lock, name)
        assert packages
        assert all(
            package["source"]
            == {"registry": "https://download.pytorch.org/whl/cpu"}
            for package in packages
        )
        package = next(
            package for package in packages if "+cpu" in package["version"]
        )
        linux_wheels = [
            wheel["url"]
            for wheel in package["wheels"]
            if "manylinux" in wheel["url"] and "x86_64" in wheel["url"]
        ]
        assert linux_wheels
        assert all(
            url.startswith("https://download-r2.pytorch.org/whl/cpu/")
            for url in linux_wheels
        )
    assert {package["version"] for package in _packages(lock, "torch")} == {
        "2.11.0",
        "2.11.0+cpu",
    }
    assert {
        package["version"] for package in _packages(lock, "torchvision")
    } == {"0.26.0", "0.26.0+cpu"}
