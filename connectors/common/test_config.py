"""Unit tests for the SC-96 recording opt-in gate (common/config.py). pytest-style; a __main__
fallback runs them with plain python where pytest is not installed. Run from connectors/
(so the shared common/ package resolves): `python -m pytest common/` or
`python -m common.test_config`."""

import contextlib
import io

from common.config import RECORDING_OPT_IN_ENV, capability_env, require_recording_opt_in


def _gate(environ):
    """(refusal message or None, what the gate printed)."""
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            require_recording_opt_in("recorder", environ)
    except SystemExit as e:
        return str(e.code), err.getvalue()
    return None, err.getvalue()


def test_unset_refuses():
    refused, printed = _gate({})
    assert refused is not None and "FATAL" in refused
    assert f"{RECORDING_OPT_IN_ENV}=<unset>" in printed


def test_values_other_than_yes_refuse():
    for value in ("", "true", "1", "on", "no", "y"):
        refused, _ = _gate({RECORDING_OPT_IN_ENV: value})
        assert refused is not None, value


def test_yes_allows():
    for value in ("yes", "YES", " yes "):
        refused, printed = _gate({RECORDING_OPT_IN_ENV: value})
        assert refused is None, value
        assert f"{RECORDING_OPT_IN_ENV}={value}" in printed


# ---- slice 0.7b: CONNECTOR_CAP_URL / CONNECTOR_CAP_SECRET, both or neither ----

def _cap(environ):
    try:
        return capability_env("recorder", environ), None
    except SystemExit as e:
        return None, str(e.code)


def test_capability_neither_is_todays_join():
    assert _cap({}) == ((None, None), None)
    assert _cap({"CONNECTOR_CAP_URL": "", "CONNECTOR_CAP_SECRET": ""}) == ((None, None), None)


def test_capability_both_configures():
    got, err = _cap({"CONNECTOR_CAP_URL": "http://sim:9000/voice/connector/R/join-cap", "CONNECTOR_CAP_SECRET": "s" * 40})
    assert err is None and got == ("http://sim:9000/voice/connector/R/join-cap", "s" * 40)


def test_capability_one_without_the_other_is_fatal():
    for environ in ({"CONNECTOR_CAP_URL": "http://sim/x"}, {"CONNECTOR_CAP_SECRET": "s" * 40}):
        got, err = _cap(environ)
        assert got is None and "FATAL" in err, environ
        assert ("s" * 40) not in err, "the secret is never echoed"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok  ", name)
