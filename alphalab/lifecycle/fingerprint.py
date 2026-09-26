"""What a strategy version *is*, as one immutable identity.

AlphaLab already identifies almost everything around a strategy, and nothing
identified the strategy itself:

* :class:`~alphalab.lifecycle.identity.StrategyVersionRef` is ``name@N``, a
  *registration-order* label. Two registries number the same content
  differently, and one registry can give two different contents the same
  number.
* ``DeploymentSpecification.specification_id`` identifies what a *deployment*
  needs -- capital, limits, broker and market requirements.
* ``ValidationEvidence.evidence_id`` identifies one *measurement*, and
  ``ResearchStudy.study_id`` one *experiment*.
* ``StrategyRegistration.qualified_name`` names a factory -- a name, never the
  code behind it.

None of them says *which code, with which dependencies, configured how,
researched how, on which engine*. A :class:`StrategyFingerprint` does, and it is
the one place in AlphaLab that does.

Five defining inputs
--------------------

``code``
    :class:`CodeIdentity` -- the package the strategy ships in, its declared
    version, the entry point a class registry records, and, when the caller
    supplies the source, a digest of it.
``dependencies``
    :class:`DependencyManifest` -- exact pins, and a stated
    :class:`DependencyCompleteness`. AlphaLab has no resolver and reads no
    environment, so it can never *establish* a dependency closure; a caller
    declares one, and the declaration says how complete it is. "Every package,
    pinned" and "the direct ones" and "nobody said" are three different claims,
    and a fingerprint that could not tell them apart would be claiming a
    reproducibility it does not have.
``parameters``
    :attr:`~alphalab.studio.strategy.StrategyDefinition.parameters`, read from
    the registered version by :func:`fingerprint_for_version` rather than typed
    again -- ADR-0017's lesson.
``research``
    :class:`ResearchConfiguration` -- the declared methodology the version was
    researched under, and, where it came from a v3.2 study, that study's
    *derived* identity.
``engine``
    :class:`EngineIdentity` -- which AlphaLab. Supplied, like every other input:
    :func:`running_engine` reads the running interpreter's version when a caller
    asks it to, and nothing here asks on the caller's behalf.

What is deliberately *not* an input: an environment, a broker, a deployment, a
dataset, a clock, a path, a user. A fingerprint is the identity a strategy
carries *unchanged* from research to paper to live, so nothing that differs
between those can be part of it. :mod:`alphalab.lifecycle.portability` depends
on exactly that.

Identity
--------

:func:`derive_strategy_fingerprint` is ``"<name>@<sha256>"`` over
:func:`canonical_fingerprint_key`, the house construction
``derive_dataset_version`` and ``derive_study_id`` use: scheme tag first, fixed
sections, open key sets sorted. One refinement, applied to everything v3.6
renders: every caller-supplied string and every number is rendered with
``repr``. A declared value can then never be read as a separator, a section
header or an absent value -- ``version='none'`` and ``version=None`` are
different lines -- so the rendering is injective without refusing any
character a package name or a setting might legitimately contain.
:data:`STRATEGY_FINGERPRINT_SCHEME` tags it, and changing it is an ADR.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum, auto
from types import MappingProxyType
from typing import Final, Protocol

from alphalab.common.version import __version__
from alphalab.lifecycle.exceptions import LifecycleInputError
from alphalab.lifecycle.strategy_version import StrategyVersion
from alphalab.model_registry.artifact_store import compute_digest
from alphalab.strategy.registry import StrategyRegistration

__all__ = [
    "NO_DEPENDENCIES",
    "STRATEGY_FINGERPRINT_SCHEME",
    "STRATEGY_SOURCE_SCHEME",
    "UNDECLARED_DEPENDENCIES",
    "CodeIdentity",
    "DependencyCompleteness",
    "DependencyManifest",
    "DependencyPin",
    "EngineIdentity",
    "ResearchConfiguration",
    "StrategyFingerprint",
    "StudyIdentity",
    "build_fingerprint",
    "canonical_fingerprint_key",
    "code_identity_for",
    "derive_strategy_fingerprint",
    "differing_parameters",
    "fingerprint_differences",
    "fingerprint_for_version",
    "normalize_distribution_name",
    "research_configuration",
    "research_configuration_for_study",
    "running_engine",
    "source_digest",
    "verify_fingerprint",
]

#: Scheme tag, and the first line of every canonical fingerprint key.
#:
#: Frozen for the life of the scheme. Changing it changes every fingerprint in
#: existence and requires an ADR, exactly as ``DATASET_KEY_SCHEME`` does.
STRATEGY_FINGERPRINT_SCHEME: Final = "alphalab.strategy_fingerprint.v1"

#: Scheme tag of a strategy's source digest. Separate from the fingerprint's own
#: tag because a source digest is a component: it names files, not a strategy.
STRATEGY_SOURCE_SCHEME: Final = "alphalab.strategy_source.v1"

#: PEP 508's distribution-name grammar, which is what a lock file writes.
_DISTRIBUTION_NAME = re.compile(r"^([A-Za-z0-9]|[A-Za-z0-9][A-Za-z0-9._-]*[A-Za-z0-9])$")

#: An exact version: PEP 440 characters and nothing that reads as a range. A
#: ``>=``, a ``*`` or a comma is a *specifier*, and a specifier is not a pin.
_EXACT_VERSION = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._+!-]*[A-Za-z0-9])?$")

#: PEP 503's normalization: runs of ``-``, ``_`` and ``.`` are one separator.
_NAME_SEPARATORS = re.compile(r"[-_.]+")

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")

#: A drive-qualified path, which names one machine's filesystem.
_DRIVE = re.compile(r"^[A-Za-z]:")


def _digest(lines: list[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _require_text(value: str, field: str) -> None:
    if not value.strip():
        raise LifecycleInputError(f"{field} cannot be empty.")


def _require_sha256(value: str, field: str) -> None:
    if not _SHA256_HEX.match(value):
        raise LifecycleInputError(
            f"{field} is {value!r}; expected a SHA-256 digest as 64 lowercase hex characters."
        )


# --------------------------------------------------------------------------- #
# Code
# --------------------------------------------------------------------------- #


def _require_relative_path(path: str) -> None:
    """Refuse a path that names one machine rather than a file in a package.

    The digest is meant to be the same on every machine that holds the same
    files, so the path it hashes must be the file's place *in the package*. An
    absolute path, a home directory, a drive letter or a backslash would carry
    the machine -- or the operating system -- into the identity.
    """

    problem = ""
    if not path.strip():
        problem = "it is empty"
    elif path.startswith(("/", "~")) or _DRIVE.match(path):
        problem = "it is absolute, and would carry one machine's filesystem into the identity"
    elif "\\" in path:
        problem = (
            "it contains a backslash; the same file would hash under a different name on "
            "another operating system"
        )
    elif any(segment in ("", ".", "..") for segment in path.split("/")):
        problem = "it has an empty, '.' or '..' segment, so two spellings would name one file"
    elif "\n" in path or "\r" in path:
        problem = "it contains a line break"
    if problem:
        raise LifecycleInputError(
            f"Source path {path!r} is not a relative POSIX path inside a package: {problem}."
        )


def source_digest(sources: Mapping[str, bytes]) -> str:
    """The digest identifying a strategy's source files, wherever they are.

    ``sources`` maps each file's path *relative to its package root* -- POSIX
    separators, no leading slash -- to its exact bytes. Each file's bytes are
    identified by :func:`~alphalab.model_registry.artifact_store.compute_digest`,
    the identity AlphaLab already gives an artifact's bytes -- and a strategy's
    source is an artifact in exactly that sense -- and the paths are sorted, so
    neither the order a directory was listed in nor where the package sits on
    disk can reach the result.

    Nothing here reads a file. Which files make up a strategy is the caller's
    knowledge -- its module, the helpers it imports, a model file it loads -- and
    a function that globbed a directory would silently include a stale cache or
    leave out a helper in the next directory.

    Raises:
        LifecycleInputError: If ``sources`` is empty, or a path is absolute,
            contains a backslash, a line break, or an empty, ``.`` or ``..``
            segment.
    """

    if not sources:
        raise LifecycleInputError(
            "A source digest over no files identifies nothing; name the files the strategy is."
        )
    lines = [STRATEGY_SOURCE_SCHEME]
    for path in sorted(sources):
        _require_relative_path(path)
        lines.append(f"{path!r}={compute_digest(sources[path])!r}")
    return _digest(lines)


@dataclass(frozen=True, slots=True)
class CodeIdentity:
    """Which code a strategy is.

    Attributes:
        package: What the code ships in -- a distribution name, or a repository
            name for code that is not packaged.
        version: The package's declared version, or ``None`` when none was
            declared. ``None`` is not "latest": it is the honest record that the
            code carries no version, and a fingerprint without a
            :attr:`source_digest` then identifies it by name alone.
        entry_point: ``module.QualName`` of the class or factory that builds the
            strategy -- the form
            :attr:`~alphalab.strategy.registry.StrategyRegistration.qualified_name`
            records, and which :func:`code_identity_for` reads from there.
        source_digest: :func:`source_digest` over the strategy's files, or
            ``None`` when the source was not hashed. With it, the identity is
            the code's *content*; without it, it is the *declaration* of a
            package and version, which is a weaker claim and says so through
            :attr:`content_addressed`.

    Raises:
        LifecycleInputError: If the package or entry point is blank, the entry
            point contains whitespace, the version is not an exact version, or
            the source digest is not a SHA-256 digest.
    """

    package: str
    version: str | None
    entry_point: str
    source_digest: str | None

    def __post_init__(self) -> None:
        _require_text(self.package, "CodeIdentity.package")
        _require_text(self.entry_point, "CodeIdentity.entry_point")
        if any(character.isspace() for character in self.entry_point):
            raise LifecycleInputError(
                f"CodeIdentity.entry_point {self.entry_point!r} contains whitespace; it is a "
                "dotted 'module.QualName', which has none."
            )
        if self.version is not None and not _EXACT_VERSION.match(self.version):
            raise LifecycleInputError(
                f"CodeIdentity.version {self.version!r} is not one exact version. A range or "
                "a wildcard is a specifier, not a version, and would identify more than one "
                "release of the code."
            )
        if self.source_digest is not None:
            _require_sha256(self.source_digest, "CodeIdentity.source_digest")

    @property
    def content_addressed(self) -> bool:
        """Whether the code is identified by its bytes rather than its declaration."""

        return self.source_digest is not None


def code_identity_for(
    registration: StrategyRegistration,
    package: str,
    version: str | None,
    sources: Mapping[str, bytes] | None,
) -> CodeIdentity:
    """The code identity of a strategy a class registry holds.

    The entry point is **read** from
    :attr:`~alphalab.strategy.registry.StrategyRegistration.qualified_name`, the
    one record AlphaLab keeps of which factory a strategy identity maps to, so
    a fingerprint and a run cannot name the code two different ways. A factory
    defined in a script's ``__main__`` records itself as ``__main__.Name``,
    which changes with how the script is launched; declare a
    :class:`CodeIdentity` directly for such code.

    Args:
        registration: What the class registry holds for the strategy.
        package: What the code ships in.
        version: Its declared version, or ``None``.
        sources: ``relative path -> bytes`` for the strategy's files, or
            ``None`` to identify the code by declaration only.
    """

    return CodeIdentity(
        package=package,
        version=version,
        entry_point=registration.qualified_name,
        source_digest=None if sources is None else source_digest(sources),
    )


# --------------------------------------------------------------------------- #
# Dependencies
# --------------------------------------------------------------------------- #


def normalize_distribution_name(name: str) -> str:
    """PEP 503's normal form: lowercase, with ``-``, ``_`` and ``.`` runs as ``-``.

    The packaging specification defines ``Requests``, ``requests`` and
    ``re_quests``-style spellings of one project as the same distribution, so
    they identify alike. This is the specification's equivalence, not a
    judgement AlphaLab makes.
    """

    return _NAME_SEPARATORS.sub("-", name).lower()


class DependencyCompleteness(Enum):
    """How much of a strategy's dependency set a manifest actually records.

    AlphaLab cannot check any of these: it has no resolver, reads no installed
    environment and declares no runtime dependency of its own. Which one is true
    is the caller's declaration -- typically read from a lock file -- and it is
    recorded *as* a declaration, inside the fingerprint, so a consumer can see
    exactly how strong the claim is.
    """

    #: Every package the strategy imports, directly or transitively, pinned to
    #: one exact version. What a lock file records. An empty exact closure is
    #: the real statement "nothing beyond AlphaLab and the standard library".
    EXACT_CLOSURE = auto()

    #: The direct dependencies are pinned exactly; what they pull in is not
    #: recorded, so the same pins can resolve to different transitive versions.
    DIRECT_ONLY = auto()

    #: Nothing was declared. Not the same as "no dependencies", which is an
    #: empty :attr:`EXACT_CLOSURE`.
    UNDECLARED = auto()


@dataclass(frozen=True, slots=True)
class DependencyPin:
    """One distribution at one exact version.

    Attributes:
        name: The distribution name, as a lock file spells it. Identified by
            :func:`normalize_distribution_name`, so case and separator spelling
            do not change a fingerprint.
        version: One exact version. A specifier such as ``">=2.0"`` is refused:
            it names a range of releases, and a fingerprint over a range is not
            an identity.
        artifact_sha256: The SHA-256 of the exact artifact installed -- the
            ``--hash`` a lock file records -- or ``None`` when it was not
            recorded. Where it is present it is part of the identity, so two
            builds published under one version number are told apart.

    Raises:
        LifecycleInputError: If the name is not a PEP 508 distribution name,
            the version is not exact, or the artifact digest is malformed.
    """

    name: str
    version: str
    artifact_sha256: str | None = None

    def __post_init__(self) -> None:
        if not _DISTRIBUTION_NAME.match(self.name):
            raise LifecycleInputError(
                f"DependencyPin.name {self.name!r} is not a distribution name."
            )
        if not _EXACT_VERSION.match(self.version):
            raise LifecycleInputError(
                f"DependencyPin {self.name!r} version {self.version!r} is not one exact "
                "version. A specifier names a range of releases; pin one."
            )
        if self.artifact_sha256 is not None:
            _require_sha256(self.artifact_sha256, f"DependencyPin {self.name!r} artifact_sha256")

    @property
    def normalized_name(self) -> str:
        """The name a fingerprint identifies this distribution by."""

        return normalize_distribution_name(self.name)


@dataclass(frozen=True, slots=True)
class DependencyManifest:
    """A strategy's dependencies, and how complete that record is.

    Attributes:
        completeness: What the pins claim to cover. See
            :class:`DependencyCompleteness`.
        pins: The pinned distributions, in any order: identity sorts them.

    Raises:
        LifecycleInputError: If an ``UNDECLARED`` manifest carries pins, a
            ``DIRECT_ONLY`` manifest carries none -- a strategy with no direct
            dependency has no transitive one either, which is an empty
            ``EXACT_CLOSURE``, and one fact must not have two spellings -- or two
            pins name one distribution.
    """

    completeness: DependencyCompleteness
    pins: tuple[DependencyPin, ...]

    def __post_init__(self) -> None:
        if self.completeness is DependencyCompleteness.UNDECLARED and self.pins:
            raise LifecycleInputError(
                "An UNDECLARED dependency manifest lists pins. Listing them is a declaration; "
                "say how complete it is with EXACT_CLOSURE or DIRECT_ONLY."
            )
        if self.completeness is DependencyCompleteness.DIRECT_ONLY and not self.pins:
            raise LifecycleInputError(
                "A DIRECT_ONLY manifest with no pins says the strategy has no direct "
                "dependency, and then it has no transitive one either. That is an empty "
                "EXACT_CLOSURE, which is the one spelling of that fact."
            )
        names = [pin.normalized_name for pin in self.pins]
        if len(set(names)) != len(names):
            repeated = sorted({name for name in names if names.count(name) > 1})
            raise LifecycleInputError(
                f"The dependency manifest pins {repeated} more than once. Two versions of one "
                "distribution cannot both be installed, so the manifest cannot be true."
            )

    @property
    def exact(self) -> bool:
        """Whether the manifest claims the whole closure, exactly pinned."""

        return self.completeness is DependencyCompleteness.EXACT_CLOSURE


#: A strategy with no dependency beyond AlphaLab and the standard library,
#: stated as the complete statement it is.
NO_DEPENDENCIES: Final = DependencyManifest(DependencyCompleteness.EXACT_CLOSURE, ())

#: A strategy whose dependencies nobody declared.
UNDECLARED_DEPENDENCIES: Final = DependencyManifest(DependencyCompleteness.UNDECLARED, ())


# --------------------------------------------------------------------------- #
# Engine
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class EngineIdentity:
    """Which engine, at which version.

    Attributes:
        name: The engine's distribution name.
        version: Its exact version.

    Raises:
        LifecycleInputError: If the name is blank or the version is not exact.
    """

    name: str
    version: str

    def __post_init__(self) -> None:
        _require_text(self.name, "EngineIdentity.name")
        if not _EXACT_VERSION.match(self.version):
            raise LifecycleInputError(
                f"EngineIdentity.version {self.version!r} is not one exact version."
            )

    def __str__(self) -> str:
        return f"{self.name}=={self.version}"


def running_engine() -> EngineIdentity:
    """The AlphaLab running in this interpreter, as an identity to record.

    Reads :data:`alphalab.__version__`, which is the installed distribution's
    metadata version (falling back to the version declared in source when the
    package is not installed). That is an *observation of this interpreter*: a
    caller records it when fingerprinting or producing a result here, and
    verification later compares against what was recorded, never against
    whatever happens to be installed then. Nothing in this module calls it on a
    caller's behalf.
    """

    return EngineIdentity(name="alphalab", version=__version__)


# --------------------------------------------------------------------------- #
# Research configuration
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ResearchConfiguration:
    """The declared configuration a strategy version was researched under.

    Attributes:
        settings: The methodology as the caller states it -- ``"validation" ->
            "walk-forward 5x252"``, ``"costs" -> "PercentageCommission(0.001)"``.
            Text, because the objects a run is configured with (a simulator, a
            sizing model, a fill policy) are live objects AlphaLab records only
            by type (ADR-0023); a setting is how their parameters enter an
            identity. Keys are sorted by the identity, so their order does not.
        study_id: The derived identity of the
            :class:`~alphalab.research.study.ResearchStudy` the version came
            from, or ``None``. Read from the study by
            :func:`research_configuration_for_study`, which is what makes it a
            reference rather than a string somebody typed.

    Raises:
        LifecycleInputError: If neither a setting nor a study is given -- a
            configuration that states nothing would identify every strategy
            alike -- or a key or value is blank.
    """

    settings: Mapping[str, str]
    study_id: str | None = None

    def __post_init__(self) -> None:
        if not self.settings and self.study_id is None:
            raise LifecycleInputError(
                "A research configuration states neither a setting nor a study. One that "
                "states nothing would give every strategy researched any way the same "
                "identity."
            )
        for key, value in self.settings.items():
            _require_text(key, "A research setting's name")
            _require_text(value, f"Research setting {key!r}")
        if self.study_id is not None:
            _require_text(self.study_id, "ResearchConfiguration.study_id")


def research_configuration(
    settings: Mapping[str, str], study_id: str | None = None
) -> ResearchConfiguration:
    """A :class:`ResearchConfiguration` whose settings nobody can edit afterwards."""

    return ResearchConfiguration(settings=MappingProxyType(dict(settings)), study_id=study_id)


class StudyIdentity(Protocol):
    """Anything that carries a v3.2 study's derived identity.

    Structural, so this module reaches a study without importing
    :mod:`alphalab.research`: :class:`~alphalab.research.study.ResearchStudy`
    and :class:`~alphalab.research.study.StudyResult` both satisfy it as they
    stand. ``alphalab.api`` is the one module that imports both the data layer
    and the research layer (``test_one_research_authority_per_concept.py``), and
    a fingerprint needs a study's identity, not its machinery.
    """

    @property
    def study_id(self) -> str:
        """The study's derived, reproducible identity."""
        ...


def research_configuration_for_study(
    study: StudyIdentity, settings: Mapping[str, str] | None = None
) -> ResearchConfiguration:
    """The research configuration of a strategy that came from a v3.2 study.

    The study's identity is **derived** from the study -- its dataset version,
    universe, features, horizons, splits, parameters and seed -- rather than
    accepted as a string, so the fingerprint names the experiment that actually
    ran.
    """

    return research_configuration(settings or {}, study_id=study.study_id)


# --------------------------------------------------------------------------- #
# The fingerprint
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class StrategyFingerprint:
    """One strategy version's immutable identity, with everything it was derived from.

    Built by :func:`build_fingerprint` or :func:`fingerprint_for_version`, which
    compute :attr:`fingerprint`. Constructing one by hand with a fingerprint of
    your own choosing is possible, and :func:`verify_fingerprint` will say it
    does not match -- the contract
    :class:`~alphalab.lifecycle.evidence.ValidationEvidence` has.

    Attributes:
        fingerprint: ``"<name>@<sha256>"``, from
            :func:`derive_strategy_fingerprint`.
        name: The strategy line.
        strategy_id: The :attr:`~alphalab.studio.strategy.StrategyDefinition.strategy_id`
            a class registry resolves to code and a run executes under. Part of
            the identity, and what lets a manifest or a certification check that
            a run executed *this* strategy.
        code: Which code.
        dependencies: With what.
        parameters: Configured how.
        research: Researched how.
        engine: On which engine.
    """

    fingerprint: str
    name: str
    strategy_id: str
    code: CodeIdentity
    dependencies: DependencyManifest
    parameters: Mapping[str, float]
    research: ResearchConfiguration
    engine: EngineIdentity

    @property
    def digest(self) -> str:
        """The SHA-256 part of :attr:`fingerprint`."""

        return self.fingerprint.rpartition("@")[2]


def _require_name(name: str) -> None:
    _require_text(name, "A strategy fingerprint's name")
    if "@" in name or "\n" in name or "\r" in name:
        raise LifecycleInputError(
            f"Strategy name {name!r} cannot contain '@' or a line break; '@' separates the "
            "name from the digest in a fingerprint."
        )


def _require_parameters(parameters: Mapping[str, float]) -> None:
    for key, value in parameters.items():
        _require_text(key, "A parameter name")
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise LifecycleInputError(
                f"Parameter {key!r} is {value!r}; a strategy parameter is a number."
            )
        if not math.isfinite(value):
            raise LifecycleInputError(
                f"Parameter {key!r} is {value!r}. A non-finite parameter is not equal to "
                "itself, so no identity built on it could ever verify."
            )


def canonical_fingerprint_key(
    name: str,
    strategy_id: str,
    code: CodeIdentity,
    dependencies: DependencyManifest,
    parameters: Mapping[str, float],
    research: ResearchConfiguration,
    engine: EngineIdentity,
) -> str:
    """Render the canonical key a fingerprint is derived from.

    Public so the rendering can be pinned by a test and read by anyone auditing
    a fingerprint. Sections are fixed and in this order; within the three open
    sections -- pins, parameters, settings -- entries are sorted, because a
    mapping's order is not part of what it means. Every string and number is
    rendered with ``repr``; see the module docstring for why.

    A number's *spelling* is kept, not normalized: ``10`` and ``10.0`` render
    differently, exactly as
    :func:`~alphalab.lifecycle.specification.specification_id_for` renders them,
    so a specification and a fingerprint over one parameter set agree about
    what counts as the same value.
    """

    pins = sorted(dependencies.pins, key=lambda pin: pin.normalized_name)
    return "\n".join(
        [
            STRATEGY_FINGERPRINT_SCHEME,
            f"name={name!r}",
            f"strategy_id={strategy_id!r}",
            "code",
            f"package={code.package!r}",
            f"version={code.version!r}",
            f"entry_point={code.entry_point!r}",
            f"source={code.source_digest!r}",
            "dependencies",
            f"completeness={dependencies.completeness.name}",
            *(f"{pin.normalized_name!r}=={pin.version!r}#{pin.artifact_sha256!r}" for pin in pins),
            "parameters",
            *(f"{key!r}={parameters[key]!r}" for key in sorted(parameters)),
            "research",
            f"study={research.study_id!r}",
            *(f"setting.{key!r}={research.settings[key]!r}" for key in sorted(research.settings)),
            "engine",
            f"name={engine.name!r}",
            f"version={engine.version!r}",
        ]
    )


def derive_strategy_fingerprint(
    name: str,
    strategy_id: str,
    code: CodeIdentity,
    dependencies: DependencyManifest,
    parameters: Mapping[str, float],
    research: ResearchConfiguration,
    engine: EngineIdentity,
) -> str:
    """``"<name>@<sha256 of the canonical key>"``. Derived, never minted.

    The full digest, matching ``evidence_id`` and ``dataset_version``, so that
    no collision argument is ever needed.

    Raises:
        LifecycleInputError: If the name is blank or contains ``"@"``, the
            strategy id is blank, or a parameter is not a finite number.
    """

    _require_name(name)
    _require_text(strategy_id, "A strategy fingerprint's strategy_id")
    _require_parameters(parameters)
    key = canonical_fingerprint_key(
        name, strategy_id, code, dependencies, parameters, research, engine
    )
    return f"{name}@{hashlib.sha256(key.encode('utf-8')).hexdigest()}"


def build_fingerprint(
    name: str,
    strategy_id: str,
    code: CodeIdentity,
    dependencies: DependencyManifest,
    parameters: Mapping[str, float],
    research: ResearchConfiguration,
    engine: EngineIdentity,
) -> StrategyFingerprint:
    """Build a :class:`StrategyFingerprint` with its identity computed.

    The parameters are copied into a read-only mapping, so neither the caller's
    dictionary nor anybody holding the fingerprint can change them afterwards.
    """

    frozen = MappingProxyType(dict(parameters))
    return StrategyFingerprint(
        fingerprint=derive_strategy_fingerprint(
            name, strategy_id, code, dependencies, frozen, research, engine
        ),
        name=name,
        strategy_id=strategy_id,
        code=code,
        dependencies=dependencies,
        parameters=frozen,
        research=research,
        engine=engine,
    )


def fingerprint_for_version(
    version: StrategyVersion,
    code: CodeIdentity,
    dependencies: DependencyManifest,
    research: ResearchConfiguration,
    engine: EngineIdentity,
) -> StrategyFingerprint:
    """Fingerprint a registered strategy version.

    The name, the strategy id and the parameters are **derived** from
    ``version`` rather than supplied -- the rule ADR-0017 records about
    :func:`~alphalab.lifecycle.evidence.evidence_from_backtest` and
    :func:`~alphalab.lifecycle.specification.specification_for_version`
    applies: a value a caller types is hashed into the identity and is only as
    trustworthy as the typing. The version *number* is not an input; see the
    module docstring.
    """

    return build_fingerprint(
        name=version.name,
        strategy_id=version.definition.strategy_id,
        code=code,
        dependencies=dependencies,
        parameters=version.definition.parameters,
        research=research,
        engine=engine,
    )


def verify_fingerprint(fingerprint: StrategyFingerprint) -> bool:
    """Whether the stored fingerprint still matches the content it names."""

    try:
        derived = derive_strategy_fingerprint(
            fingerprint.name,
            fingerprint.strategy_id,
            fingerprint.code,
            fingerprint.dependencies,
            fingerprint.parameters,
            fingerprint.research,
            fingerprint.engine,
        )
    except LifecycleInputError:
        return False
    return fingerprint.fingerprint == derived


def differing_parameters(left: Mapping[str, float], right: Mapping[str, float]) -> tuple[str, ...]:
    """The parameter names whose values differ between two parameter sets, sorted.

    Compared as :func:`canonical_fingerprint_key` renders them -- by ``repr`` --
    so ``10`` and ``10.0`` differ here exactly as they differ in an identity,
    and a parameter present on one side only differs from its absence. The one
    comparison every v3.6 module uses, so a fingerprint, a certification and a
    portability check cannot disagree about whether two strategies are
    configured alike.
    """

    return tuple(
        key for key in sorted({*left, *right}) if repr(left.get(key)) != repr(right.get(key))
    )


def fingerprint_differences(
    left: StrategyFingerprint, right: StrategyFingerprint
) -> tuple[str, ...]:
    """Which defining inputs differ between two fingerprints, in plain terms.

    Empty exactly when the two derive from the same inputs. The inspection
    counterpart to comparing the two strings: a changed fingerprint says
    *that* something changed, and this says *what*.
    """

    differences: list[str] = []
    if left.name != right.name:
        differences.append(f"name: {left.name!r} -> {right.name!r}")
    if left.strategy_id != right.strategy_id:
        differences.append(f"strategy_id: {left.strategy_id!r} -> {right.strategy_id!r}")
    for field in ("package", "version", "entry_point", "source_digest"):
        before = getattr(left.code, field)
        after = getattr(right.code, field)
        if before != after:
            differences.append(f"code.{field}: {before!r} -> {after!r}")
    if left.dependencies.completeness is not right.dependencies.completeness:
        differences.append(
            f"dependencies.completeness: {left.dependencies.completeness.name} -> "
            f"{right.dependencies.completeness.name}"
        )
    before_pins = {
        pin.normalized_name: (pin.version, pin.artifact_sha256) for pin in left.dependencies.pins
    }
    after_pins = {
        pin.normalized_name: (pin.version, pin.artifact_sha256) for pin in right.dependencies.pins
    }
    for pin_name in sorted({*before_pins, *after_pins}):
        if before_pins.get(pin_name) != after_pins.get(pin_name):
            differences.append(
                f"dependency {pin_name!r}: {before_pins.get(pin_name)!r} -> "
                f"{after_pins.get(pin_name)!r}"
            )
    for key in differing_parameters(left.parameters, right.parameters):
        differences.append(
            f"parameter {key!r}: {left.parameters.get(key)!r} -> {right.parameters.get(key)!r}"
        )
    if left.research.study_id != right.research.study_id:
        differences.append(
            f"research.study_id: {left.research.study_id!r} -> {right.research.study_id!r}"
        )
    for key in sorted({*left.research.settings, *right.research.settings}):
        if left.research.settings.get(key) != right.research.settings.get(key):
            differences.append(
                f"research setting {key!r}: {left.research.settings.get(key)!r} -> "
                f"{right.research.settings.get(key)!r}"
            )
    if left.engine != right.engine:
        differences.append(f"engine: {left.engine} -> {right.engine}")
    return tuple(differences)
