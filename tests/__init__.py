"""Test bootstrap: redirect every writable root to a throwaway directory.

`app.config.settings` is built once, at first import of any `app` module, from
the environment as it stood at that moment. A per-module
`os.environ.setdefault` therefore protects nothing: whichever test module
imports `app` first decides the roots for the entire run, and modules that only
import `app.auth` or `app.control` set no environment at all.

That matters because the suite deletes every `*.json` it finds in
`settings.cabinets_root` between tests. Run from a checkout that also serves
real cabinets, it wipes their registration and selection.

This package `__init__` runs before any test module is imported — by
`unittest discover` and by `python -m unittest tests.test_x` alike — so it is
the one place that can set the environment early enough.
"""

from __future__ import annotations

import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="zucchini-tests-")

for _name, _leaf in (
    ("CABINETS_ROOT", "cabinets"),
    ("UPDATES_ROOT", "updates"),
    ("CONVERT_ROOT", "converted"),
    ("DATABASE_PATH", "connector.db"),
):
    os.environ[f"CONNECTOR_{_name}"] = os.path.join(_TMP, _leaf)

if "app.config" in sys.modules:  # pragma: no cover - import-order safety net
    raise RuntimeError(
        "app.config was imported before tests/__init__.py; the suite would "
        "delete files under the real storage root"
    )
