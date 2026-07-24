#!/usr/bin/env python3
"""Shared fail-closed Apptainer contract for managed steel-module work.

This module deliberately does not submit jobs and does not know how to run a
Geant4 scan.  It only constructs the container boundary used by both the
production worker and the R3 isolation probe.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence


NO_MOUNT_CLASSES = "hostfs,cwd,bind-paths"
CONTAINER_SIMULATION_ROOT = "/work/g4optics-simulation"
CONTAINER_CONTROL_ROOT = "/work/g4optics-control"
CONTAINER_EXECUTION_ROOT = "/work/g4optics-execution"
CONTAINER_DATA_ROOT = "/opt/geant4-data"
CONTAINER_PREBUILT_ROOT = "/work/g4optics-prebuilt"

_FORBIDDEN_ENVIRONMENT_PREFIXES = (
    "APPTAINER_",
    "APPTAINERENV_",
    "SINGULARITY_",
    "SINGULARITYENV_",
)


@dataclass(frozen=True)
class AdditionalBind:
    """One explicitly authorized extra bind mount."""

    host_path: Path
    container_path: str
    writable: bool


@dataclass(frozen=True)
class ProductionContainerInputs:
    """Checksum-validated host paths needed by the production-like boundary."""

    image: Path
    simulation_root: Path
    control_root: Path
    execution_root: Path
    data_root: Path
    executable_directory: Path


def forbidden_apptainer_environment_names(
    environment: Mapping[str, str],
) -> tuple[str, ...]:
    """Return host variables capable of changing binds or container payload."""

    return tuple(
        sorted(
            name
            for name in environment
            if name.startswith(_FORBIDDEN_ENVIRONMENT_PREFIXES)
        )
    )


def sanitize_apptainer_host_environment(
    environment: Mapping[str, str],
) -> tuple[dict[str, str], tuple[str, ...]]:
    """Strip every bind/payload injection variable from a copied environment."""

    forbidden = forbidden_apptainer_environment_names(environment)
    clean = {name: value for name, value in environment.items() if name not in forbidden}
    return clean, forbidden


def production_apptainer_host_environment(
    environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return a sanitized environment, rejecting any attempted host injection.

    Rejecting as well as stripping is intentional: silently accepting a tainted
    production shell would make the evidence ambiguous.  The stripped copy is
    what is actually supplied to ``subprocess`` after the clean check passes.
    """

    source = os.environ if environment is None else environment
    clean, forbidden = sanitize_apptainer_host_environment(source)
    if forbidden:
        raise ValueError(
            "forbidden Apptainer/Singularity host environment variables: "
            + ", ".join(forbidden)
        )
    return clean


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def inspect_apptainer_runtime_identity(
    apptainer: Path,
    *,
    environment: Mapping[str, str],
    cwd: Path,
) -> dict[str, str]:
    """Return the executable identity used by both R3 and production.

    The caller must supply the already-sanitized host environment.  Resolving
    and hashing the executable before invoking ``--version`` makes PATH drift
    or an in-place engine replacement visible before any container starts.
    """

    requested = apptainer.expanduser()
    resolved = requested.resolve()
    if (
        not resolved.is_file()
        or not os.access(resolved, os.X_OK)
        or cwd.is_symlink()
        or not cwd.is_dir()
    ):
        raise ValueError("Apptainer runtime identity cannot be inspected safely")
    try:
        result = subprocess.run(
            [str(resolved), "--version"],
            cwd=cwd.resolve(),
            env=dict(environment),
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Apptainer version query timed out") from exc
    version = result.stdout.strip()
    if result.returncode or not version or "\n" in version or "\r" in version:
        raise ValueError("cannot record one-line Apptainer version identity")
    return {
        "apptainer_path": str(resolved),
        "apptainer_sha256": _sha256_file(resolved),
        "apptainer_version": version,
    }


def validate_apptainer_runtime_identity(
    apptainer: Path,
    *,
    expected: Mapping[str, Any],
    environment: Mapping[str, str],
    cwd: Path,
) -> dict[str, str]:
    """Require production to use the exact engine accepted by R3."""

    required = {"apptainer_path", "apptainer_sha256", "apptainer_version"}
    if set(expected) != required or any(
        not isinstance(expected.get(name), str) or not expected.get(name)
        for name in required
    ):
        raise ValueError("R3 Apptainer runtime identity is incomplete")
    observed = inspect_apptainer_runtime_identity(
        apptainer, environment=environment, cwd=cwd
    )
    if observed != dict(expected):
        raise ValueError("production Apptainer runtime differs from accepted R3")
    return observed


def _host_directory(path: Path, label: str) -> Path:
    requested = path.expanduser()
    if requested.is_symlink():
        raise ValueError(f"{label} must not be a symlink")
    resolved = requested.resolve()
    if not resolved.is_dir():
        raise ValueError(f"missing {label}: {resolved}")
    return resolved


def _host_image(path: Path) -> Path:
    requested = path.expanduser()
    if requested.is_symlink():
        raise ValueError("Apptainer image must not be a symlink")
    resolved = requested.resolve()
    if not resolved.is_file():
        raise ValueError(f"missing Apptainer image: {resolved}")
    return resolved


def _container_destination(raw: str) -> str:
    destination = PurePosixPath(raw)
    if (
        not raw
        or not destination.is_absolute()
        or ".." in destination.parts
        or any(character in raw for character in (":", ",", "\n", "\r"))
        or str(destination) != raw
    ):
        raise ValueError(f"unsafe container bind destination: {raw!r}")
    return raw


def _bind_argument(host: Path, destination: str, *, writable: bool) -> str:
    if any(character in str(host) for character in (":", ",", "\n", "\r")):
        raise ValueError(f"unsupported character in host bind path: {host}")
    mode = "rw" if writable else "ro"
    return f"{host}:{_container_destination(destination)}:{mode}"


def build_production_apptainer_prefix(
    *,
    apptainer: Path,
    inputs: ProductionContainerInputs,
    additional_binds: Sequence[AdditionalBind] = (),
) -> list[str]:
    """Build the exact production-like ``apptainer exec`` prefix.

    The execution companion is always read-only.  Callers may add only
    explicitly named leaf binds; the production worker adds one task leaf and
    R3 adds one disposable probe leaf.
    """

    apptainer_path = apptainer.expanduser().resolve()
    if not apptainer_path.is_file() or not os.access(apptainer_path, os.X_OK):
        raise ValueError(f"Apptainer executable is unavailable: {apptainer_path}")
    image = _host_image(inputs.image)
    fixed = (
        (_host_directory(inputs.simulation_root, "simulation source"), CONTAINER_SIMULATION_ROOT),
        (_host_directory(inputs.control_root, "control source"), CONTAINER_CONTROL_ROOT),
        (_host_directory(inputs.execution_root, "managed execution"), CONTAINER_EXECUTION_ROOT),
        (_host_directory(inputs.data_root, "Geant4 data root"), CONTAINER_DATA_ROOT),
        (_host_directory(inputs.executable_directory, "prebuilt executable directory"), CONTAINER_PREBUILT_ROOT),
    )
    destinations = {destination for _, destination in fixed}
    normalized_extra: list[tuple[Path, str, bool]] = []
    for binding in additional_binds:
        host = _host_directory(binding.host_path, "additional bind source")
        destination = _container_destination(binding.container_path)
        if destination in destinations:
            raise ValueError(f"duplicate container bind destination: {destination}")
        if destination == "/" or any(
            destination.startswith(existing.rstrip("/") + "/")
            or existing.startswith(destination.rstrip("/") + "/")
            for existing in destinations
        ):
            # The task leaf is the sole intentional exception: it overlays one
            # leaf beneath the read-only execution companion.
            task_prefix = CONTAINER_EXECUTION_ROOT.rstrip("/") + "/attempts/"
            if not destination.startswith(task_prefix):
                raise ValueError(
                    f"additional bind overlaps a fixed container root: {destination}"
                )
        destinations.add(destination)
        normalized_extra.append((host, destination, binding.writable))

    command = [
        str(apptainer_path),
        "exec",
        "--cleanenv",
        "--containall",
        "--no-home",
        "--no-mount",
        NO_MOUNT_CLASSES,
        "--pwd",
        "/",
    ]
    for host, destination in fixed:
        command.extend(["--bind", _bind_argument(host, destination, writable=False)])
    for host, destination, writable in normalized_extra:
        command.extend(
            ["--bind", _bind_argument(host, destination, writable=writable)]
        )
    command.append(str(image))
    return command


__all__ = [
    "AdditionalBind",
    "CONTAINER_CONTROL_ROOT",
    "CONTAINER_DATA_ROOT",
    "CONTAINER_EXECUTION_ROOT",
    "CONTAINER_PREBUILT_ROOT",
    "CONTAINER_SIMULATION_ROOT",
    "NO_MOUNT_CLASSES",
    "ProductionContainerInputs",
    "build_production_apptainer_prefix",
    "forbidden_apptainer_environment_names",
    "inspect_apptainer_runtime_identity",
    "production_apptainer_host_environment",
    "sanitize_apptainer_host_environment",
    "validate_apptainer_runtime_identity",
]
