"""The release verifier installs what it is given, wherever it is run from (v4.1).

Validating the published v4.0.0 release failed after the tag existed:
``release.yml`` ran ``release_preflight.py distributions dist``, the script
handed ``dist/alphalab-4.0.0.tar.gz`` to ``pip`` as given, and ``pip`` ran in
the isolated ``outside`` directory the install had moved into -- where no such
file exists::

    Could not install packages due to an OSError: [Errno 2] No such file or
    directory: '/tmp/alphalab-preflight-.../outside/dist/alphalab-4.0.0.tar.gz'

The hosted preflight had passed because it gave an absolute ``${RUNNER_TEMP}/dist``;
nothing in the suite had ever installed an artifact through the script, so the
relative form was never exercised. These tests install real artifacts -- a wheel
and an sdist small enough to build offline, through an in-tree build backend --
through the script, from relative paths, from a directory outside the source
tree, with spaces in every path, and hold each refusal to its message.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import os
import sys
import tarfile
import zipfile
from collections.abc import Callable, Sequence
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
VERSION = "9.9.9"


def _script() -> ModuleType:
    path = ROOT / "docs" / "audit" / "scripts" / "release_preflight.py"
    spec = importlib.util.spec_from_file_location("release_preflight_paths", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------- #
# Real, installable artifacts, built here and offline
# --------------------------------------------------------------------------- #

_PACKAGE = {
    "alphalab/__init__.py": f'__version__ = "{VERSION}"\n',
    "alphalab/py.typed": "",
}

_METADATA = (
    f"Metadata-Version: 2.4\nName: alphalab\nVersion: {VERSION}\nRequires-Python: >=3.12\n"
    "License-File: LICENSE\n"
)

#: A PEP 517 backend in the sdist itself: ``requires = []``, so an isolated
#: build needs nothing from an index and the install works offline.
_BACKEND = """
import base64, hashlib, zipfile
from pathlib import Path

FILES = {files!r}
METADATA = {metadata!r}
VERSION = {version!r}


def _record_line(name, data):
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
    return f"{{name}},sha256={{digest}},{{len(data)}}"


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    name = f"alphalab-{{VERSION}}-py3-none-any.whl"
    info = f"alphalab-{{VERSION}}.dist-info"
    entries = dict(FILES)
    entries[f"{{info}}/METADATA"] = METADATA
    entries[f"{{info}}/WHEEL"] = (
        "Wheel-Version: 1.0\\nGenerator: stub\\nRoot-Is-Purelib: true\\nTag: py3-none-any\\n"
    )
    entries[f"{{info}}/licenses/LICENSE"] = "MIT\\n"
    lines = [_record_line(path, text.encode()) for path, text in entries.items()]
    lines.append(f"{{info}}/RECORD,,")
    entries[f"{{info}}/RECORD"] = "\\n".join(lines) + "\\n"
    with zipfile.ZipFile(Path(wheel_directory) / name, "w") as archive:
        for path, text in entries.items():
            archive.writestr(path, text)
    return name


def build_sdist(sdist_directory, config_settings=None):
    raise NotImplementedError("this stub is only ever built from")
"""


def _wheel_entries() -> dict[str, str]:
    info = f"alphalab-{VERSION}.dist-info"
    entries = dict(_PACKAGE)
    entries[f"{info}/METADATA"] = _METADATA
    entries[f"{info}/WHEEL"] = (
        "Wheel-Version: 1.0\nGenerator: stub\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
    )
    entries[f"{info}/licenses/LICENSE"] = "MIT\n"
    lines = []
    for path, text in entries.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(text.encode()).digest()).rstrip(b"=")
        lines.append(f"{path},sha256={digest.decode()},{len(text.encode())}")
    lines.append(f"{info}/RECORD,,")
    entries[f"{info}/RECORD"] = "\n".join(lines) + "\n"
    return entries


def _build(dist: Path) -> None:
    """This release's two artifacts in ``dist``: a wheel and an sdist pip can build offline."""

    dist.mkdir(parents=True)
    with zipfile.ZipFile(dist / f"alphalab-{VERSION}-py3-none-any.whl", "w") as wheel:
        for path, text in _wheel_entries().items():
            wheel.writestr(path, text)
    top = f"alphalab-{VERSION}"
    members = {
        **_PACKAGE,
        "PKG-INFO": _METADATA,
        "LICENSE": "MIT\n",
        "pyproject.toml": (
            "[build-system]\nrequires = []\nbuild-backend = 'stub_backend'\n"
            "backend-path = ['.']\n\n[project]\nname = 'alphalab'\n"
            f"version = '{VERSION}'\n"
        ),
        "stub_backend.py": _BACKEND.format(files=_PACKAGE, metadata=_METADATA, version=VERSION),
    }
    with tarfile.open(dist / f"alphalab-{VERSION}.tar.gz", "w:gz") as sdist:
        for name, text in members.items():
            data = text.encode()
            member = tarfile.TarInfo(f"{top}/{name}")
            member.size = len(data)
            sdist.addfile(member, io.BytesIO(data))


#: Run inside each fresh environment in place of the installed smoke test: it
#: holds the facts the path fix is about and prints them.
_SMOKE = """
import os, sys
from pathlib import Path

import alphalab

expected = sys.argv[1]
location = Path(alphalab.__file__).resolve()
prefix = Path(sys.prefix).resolve()
assert alphalab.__version__ == expected, (alphalab.__version__, expected)
assert prefix in location.parents, f"imported from {location}, not from {prefix}"
assert Path(os.getcwd()).name == "outside", os.getcwd()
assert not (Path(os.getcwd()) / "alphalab").exists()
print(f"smoke: alphalab {alphalab.__version__} from {location.parent}")
"""


@pytest.fixture
def layout(tmp_path: Path) -> tuple[Path, Path, Path]:
    """``(work, dist, smoke)``: every path holds a space, and ``dist`` is relative to ``work``."""

    work = tmp_path / "release work"
    dist = work / "built dist"
    _build(dist)
    smoke = tmp_path / "smoke checks" / "installed smoke.py"
    smoke.parent.mkdir()
    smoke.write_text(_SMOKE, encoding="utf-8")
    return work, dist, smoke


# --------------------------------------------------------------------------- #
# The failure v4.0.0 met, and the invocation release.yml makes
# --------------------------------------------------------------------------- #


def test_a_relative_wheel_and_sdist_install_and_pass_the_same_smoke_check(
    layout: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The v4.0.0 failure, reproduced: ``dist`` given relative, then pip run elsewhere."""

    work, _, smoke = layout
    monkeypatch.chdir(work)
    script = _script()

    problems = script.distributions(
        Path("built dist"), VERSION, Path("VERIFICATION.txt"), sys.executable, smoke
    )

    assert problems == []
    record = (work / "VERIFICATION.txt").read_text(encoding="utf-8")
    assert "distribution verification: PASSED" in record
    for name in (f"alphalab-{VERSION}-py3-none-any.whl", f"alphalab-{VERSION}.tar.gz"):
        assert f"- {name} install: exit 0" in record
        assert f"- {name} smoke: exit 0; smoke: alphalab {VERSION} from " in record
    assert (work / "built dist" / f"SHA256SUMS-{VERSION}").is_file()


def test_the_command_line_takes_a_relative_dist_as_release_yml_gives_it(
    layout: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``release_preflight.py distributions dist --version V --record dist/VERIFICATION-V.txt``."""

    work, _, smoke = layout
    monkeypatch.chdir(work)
    script = _script()
    monkeypatch.setattr(script, "SMOKE", smoke)

    status = script.main(
        [
            "distributions",
            "built dist",
            "--version",
            VERSION,
            "--record",
            f"built dist/VERIFICATION-{VERSION}.txt",
        ]
    )

    assert status == 0
    record = (work / "built dist" / f"VERIFICATION-{VERSION}.txt").read_text(encoding="utf-8")
    assert "PASSED" in record and record.count("install: exit 0") == 2


def test_it_runs_from_outside_the_source_tree(
    layout: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    work, dist, smoke = layout
    assert ROOT not in work.parents
    monkeypatch.chdir(work.parent)

    problems = _script().distributions(
        Path(work.name) / dist.name, VERSION, None, sys.executable, smoke
    )

    assert problems == []


def test_an_inherited_pythonpath_cannot_stand_in_for_the_installed_artifact(
    layout: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``PYTHONPATH`` naming a checkout must not reach the fresh environment (v4.1).

    Found by the v4.1 mutation harness's own baseline, which runs the suite with
    ``PYTHONPATH`` set to its copy: the smoke step imported that copy's
    ``alphalab`` (4.1.0) instead of the 9.9.9 artifact just installed. With a
    copy of a matching version it would have passed, checking the wrong code.
    """

    work, _, smoke = layout
    monkeypatch.chdir(work)
    monkeypatch.setenv("PYTHONPATH", str(ROOT))
    script = _script()
    assert "PYTHONPATH" not in script.isolated_environment()

    problems = script.distributions(Path("built dist"), VERSION, None, sys.executable, smoke)

    assert problems == [], "the smoke step imported the installed artifact, not the checkout"


def test_pip_is_handed_a_path_that_names_the_artifact_from_where_it_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mechanism, without building an environment: what each command is given, and where."""

    work = tmp_path / "work"
    _build(work / "dist")
    monkeypatch.chdir(work)
    script = _script()
    calls: list[tuple[Sequence[str], Path]] = []

    def recording(command: Sequence[str], cwd: Path) -> tuple[int, str]:
        calls.append((list(command), cwd))
        return 0, "ok"

    monkeypatch.setattr(script, "_run", recording)
    for name in script.artifact_names(VERSION):
        problems, _ = script.install_and_smoke(Path("dist") / name, VERSION, sys.executable)
        assert problems == []

    installs = [(command, cwd) for command, cwd in calls if "install" in command]
    assert len(installs) == 2
    for command, cwd in installs:
        given = Path(command[-1])
        assert given.is_absolute(), f"pip was handed {given}, relative to {cwd}"
        assert (cwd / given).is_file(), "from pip's own directory the path names the artifact"
        assert cwd.name == "outside"


# --------------------------------------------------------------------------- #
# What is refused, and how it is named
# --------------------------------------------------------------------------- #


def _recorder(ran: list[Sequence[str]]) -> Callable[[Sequence[str], Path], tuple[int, str]]:
    """A stand-in for ``_run`` that records each command and runs none."""

    def run(command: Sequence[str], cwd: Path) -> tuple[int, str]:
        ran.append(command)
        return 0, ""

    return run


def test_a_missing_artifact_is_named_and_nothing_is_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    script = _script()
    ran: list[Sequence[str]] = []
    monkeypatch.setattr(script, "_run", _recorder(ran))

    problems, log = script.install_and_smoke(
        Path("dist") / f"alphalab-{VERSION}.tar.gz", VERSION, sys.executable
    )

    assert problems == [
        f"dist/alphalab-{VERSION}.tar.gz: no such artifact (looked for "
        f"{tmp_path / 'dist' / f'alphalab-{VERSION}.tar.gz'}); nothing was installed"
    ]
    assert log == [] and ran == [], "no environment is created for an artifact that is not there"


def test_a_missing_distribution_directory_is_refused_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)

    status = _script().main(["distributions", "dist", "--version", VERSION])

    assert status == 1
    assert (
        f"FAILED: the distribution directory 'dist' does not exist (looked for {tmp_path / 'dist'})"
        in capsys.readouterr().err
    )


def test_a_file_given_as_the_distribution_directory_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "dist").write_text("", encoding="utf-8")
    script = _script()

    with pytest.raises(script.PathRefused, match="is not a directory"):
        script.distributions(Path("dist"), VERSION, None, sys.executable)


def test_a_record_in_a_missing_directory_is_refused_before_anything_runs(
    layout: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    work, _, smoke = layout
    monkeypatch.chdir(work)
    script = _script()

    with pytest.raises(script.PathRefused, match="the directory of the verification record"):
        script.distributions(
            Path("built dist"), VERSION, Path("nowhere/VERIFICATION.txt"), sys.executable, smoke
        )


@pytest.mark.parametrize("python", ["no-such-python-3.99", "bin/no-such-python"])
def test_an_interpreter_that_is_not_there_is_refused(python: str, tmp_path: Path) -> None:
    script = _script()

    with pytest.raises(script.PathRefused, match="interpreter"):
        script.interpreter(python)


def test_an_interpreter_named_by_command_or_by_relative_path_becomes_absolute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = _script()
    executable = Path(sys.executable)
    monkeypatch.chdir(executable.parent)

    assert Path(script.interpreter(f"./{executable.name}")).is_absolute()
    monkeypatch.setenv("PATH", str(executable.parent))
    assert Path(script.interpreter(executable.name)).is_absolute()


@pytest.mark.parametrize(
    "name",
    [
        "alphalab-9.9.8-py3-none-any.whl",
        "alphalab-9.9.9-cp312-cp312-linux_x86_64.whl",
        "other-9.9.9.tar.gz",
    ],
)
def test_only_this_release_s_artifacts_are_installed(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Resolving a path makes it valid from anywhere; it does not make any file acceptable."""

    (tmp_path / name).write_bytes(b"not installed")
    monkeypatch.chdir(tmp_path)
    script = _script()
    ran: list[Sequence[str]] = []
    monkeypatch.setattr(script, "_run", _recorder(ran))

    problems, _ = script.install_and_smoke(Path(name), VERSION, sys.executable)

    assert problems and "not one of alphalab 9.9.9's artifacts" in problems[0]
    assert ran == []


def test_a_directory_named_like_an_artifact_is_not_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / f"alphalab-{VERSION}.tar.gz").mkdir()
    monkeypatch.chdir(tmp_path)

    problems, _ = _script().install_and_smoke(
        Path(f"alphalab-{VERSION}.tar.gz"), VERSION, sys.executable
    )

    assert problems and "not one of alphalab" in problems[0]


@pytest.mark.parametrize("version", ["../9.9.9", "9.9", "v9.9.9", "9.9.9/.."])
def test_a_version_that_is_not_one_cannot_shape_a_path(version: str, tmp_path: Path) -> None:
    assert _script().distributions(tmp_path, version, None, sys.executable) == [
        f"the version {version!r} is not MAJOR.MINOR.PATCH"
    ]


def test_a_relative_path_still_means_the_directory_it_was_given_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Resolution is against the caller's directory, never the script's or the repository's."""

    (tmp_path / "here" / "dist").mkdir(parents=True)
    monkeypatch.chdir(tmp_path / "here")

    found = _script().existing_directory(Path("dist"), "the distribution directory")

    assert found == (tmp_path / "here" / "dist").resolve()
    assert os.path.isabs(found)
