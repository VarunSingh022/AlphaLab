"""A module no registry may import: a name that points here is looked up, never resolved.

``tests/unit/common/test_runner_registry.py`` and
``tests/unit/cloud_research/test_registered_tasks.py`` name an attribute of this
module where a registry is asked for code. Importing it fails, so a registry
that resolved the name would fail those tests twice over: by raising here, and
by leaving the module in ``sys.modules``.
"""

raise AssertionError("a registry resolved a name to code: BDY-028")
