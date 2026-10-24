"""Test environments for SWE-bench instances.

Each (repo, version) pair gets a Python version, pinned packages and an install
command, modelled on the specs used by the official SWE-bench harness. A spec is
only trusted once the gold patch passes through it (`python -m app.eval validate`);
an environment that cannot run the reference fix says nothing about the agent.
"""
from dataclasses import dataclass, field


class UnsupportedTask(ValueError):
    """The harness has no environment spec for this repository/version."""


@dataclass(frozen=True)
class EnvSpec:
    python: str
    pip_packages: tuple[str, ...] = ()
    install: str = "python -m pip install -e ."
    # Some old test suites call public services (httpbin.org); they can only be
    # graded when the run explicitly allows network access.
    needs_network: bool = False
    test_cmd: tuple[str, ...] = ("pytest", "-rA")
    extra_env: tuple[tuple[str, str], ...] = field(default=())


_PYTEST_PINS = {
    "4.4": ("atomicwrites==1.4.1", "attrs==23.1.0", "more-itertools==10.1.0", "pluggy==0.13.1",
            "py==1.11.0", "setuptools==68.0.0", "six==1.16.0"),
    "4.5": ("atomicwrites==1.4.1", "attrs==23.1.0", "more-itertools==10.1.0", "pluggy==0.11.0",
            "py==1.11.0", "setuptools==68.0.0", "six==1.16.0", "wcwidth==0.2.6"),
    "4.6": ("atomicwrites==1.4.1", "attrs==23.1.0", "more-itertools==10.1.0", "packaging==23.1",
            "pluggy==0.13.1", "py==1.11.0", "six==1.16.0", "wcwidth==0.2.6"),
    "5.0": ("atomicwrites==1.4.1", "attrs==23.1.0", "more-itertools==10.1.0", "packaging==23.1",
            "pluggy==0.13.1", "py==1.11.0", "wcwidth==0.2.6"),
    "5.2": ("atomicwrites==1.4.1", "attrs==23.1.0", "more-itertools==10.1.0", "packaging==23.1",
            "pluggy==0.13.1", "py==1.11.0", "wcwidth==0.2.6"),
    "5.4": ("py==1.11.0", "packaging==23.1", "attrs==23.1.0", "more-itertools==10.1.0", "pluggy==0.13.1"),
    "6.0": ("attrs==23.1.0", "iniconfig==2.0.0", "more-itertools==10.1.0", "packaging==23.1",
            "pluggy==0.13.1", "py==1.11.0", "toml==0.10.2"),
    "6.3": ("attrs==23.1.0", "iniconfig==2.0.0", "packaging==23.1", "pluggy==0.13.1", "py==1.11.0",
            "toml==0.10.2"),
    "7.0": ("attrs==23.1.0", "iniconfig==2.0.0", "packaging==23.1", "pluggy==0.13.1", "py==1.11.0"),
    "8.0": ("iniconfig==2.0.0", "packaging==23.1", "pluggy==1.3.0", "exceptiongroup==1.1.3", "tomli==2.0.1"),
}

# Tests written in 2014-2023 break on current pytest (removed private APIs), so
# repositories that are not pytest itself get the last release of that era.
_PYTEST_OF_THE_ERA = "pytest==7.4.4"

_FLASK_PINS = {
    "2.0": ("3.9", ("setuptools==70.0.0", "Werkzeug==2.3.7", "Jinja2==3.0.1", "itsdangerous==2.1.2",
                    "click==8.0.1", "MarkupSafe==2.1.3")),
    "2.3": ("3.11", ("setuptools==70.0.0", "click==8.1.3", "itsdangerous==2.1.2", "Jinja2==3.1.2",
                     "MarkupSafe==2.1.1", "Werkzeug==2.3.7")),
}


def _pytest_spec(version: str) -> EnvSpec:
    # pytest computes its version with setuptools_scm, which needs git metadata
    # that the test image deliberately does not contain.
    return EnvSpec(python="3.9", pip_packages=_PYTEST_PINS[version],
                   extra_env=(("SETUPTOOLS_SCM_PRETEND_VERSION", f"{version}.0"),))


def spec_for(repo: str, version: str) -> EnvSpec:
    if repo == "psf/requests":
        # The suites call httpbin.org (and a tarpit address for the timeout tests).
        return EnvSpec(python="3.9", pip_packages=(_PYTEST_OF_THE_ERA,), install="python -m pip install .",
                       needs_network=True)
    if repo == "pallets/flask" and version in _FLASK_PINS:
        python, pins = _FLASK_PINS[version]
        # python-dotenv comes from Flask's test requirements; without it the CLI tests skip.
        return EnvSpec(python=python, pip_packages=pins + (_PYTEST_OF_THE_ERA, "python-dotenv==1.0.0"))
    if repo == "pytest-dev/pytest" and version in _PYTEST_PINS:
        return _pytest_spec(version)
    if repo == "pylint-dev/pylint" and version in ("2.13", "2.14", "2.15"):
        return EnvSpec(
            python="3.9",
            # pytest-benchmark (from requirements_test_min.txt) uses py.io, which the
            # `py` shim bundled with pytest >= 7.2 no longer provides.
            pip_packages=("py==1.11.0",),
            install="python -m pip install -e . && python -m pip install -r requirements_test_min.txt",
            extra_env=(("SETUPTOOLS_SCM_PRETEND_VERSION", f"{version}.0"),),
        )
    raise UnsupportedTask(f"No test environment defined for {repo} {version}")
