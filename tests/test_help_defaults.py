"""Guard the single-default-source convention for CLI help text.

``build_parser()`` uses ``argparse.ArgumentDefaultsHelpFormatter``, which
appends ``(default: <value>)`` from ``default=`` automatically. Help strings
must therefore never hardcode a default value -- doing so produces the
doubled ``(default: ...) (default: ...)`` output and lets the help text drift
from the actual ``default=``.

Note: the import is done inside functions (like ``test_color_util.py``)
because pytest imports this file as ``lama_ole.tests.test_help_defaults``,
where ``import lama_ole`` resolves to the package rather than the CLI module.
"""

import argparse


def _cli_module():
    from lama_ole import lama_ole as lama_ole_module
    return lama_ole_module


def _parser():
    return _cli_module().build_parser()


def test_help_never_hardcodes_default():
    """Raw help strings must not write a default; the formatter owns it."""
    offenders = [
        action.dest for action in _parser()._actions
        if action.help and "(default:" in action.help
    ]
    assert not offenders, (
        "help strings must not hardcode '(default: ...)': ArgumentDefaultsHelpFormatter "
        "renders it from default=; offenders=%s" % offenders
    )


def test_no_help_shows_default_twice():
    """Rendered help must never show the default more than once per option.

    Checked on the formatter's rendered output (not the raw string) because
    text wrapping can split a doubled default across two lines.
    """
    parser = _parser()
    formatter = argparse.ArgumentDefaultsHelpFormatter(parser)
    offenders = [
        action.dest for action in parser._actions
        if action.help
        and formatter._get_help_string(action).count("(default:") > 1
    ]
    assert not offenders, (
        "help renders the default more than once (manual note + formatter append): "
        "offenders=%s" % offenders
    )
