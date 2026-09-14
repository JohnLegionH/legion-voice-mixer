"""Unit tests for the SC-96 recording opt-in gate (common/config.py). pytest-style; a __main__
fallback runs them with plain python where pytest is not installed. Run from connectors/
(so the shared common/ package resolves): `python -m pytest common/` or
`python -m common.test_config`."""

import contextlib
import io

from common.config import RECORDING_OPT_IN_ENV, require_recording_opt_in


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


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok  ", name)
