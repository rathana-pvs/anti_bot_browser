"""Facebook steps shared by every workflow.

Each skill is a small class built on a host task, like ``ComposerEntry``. The
host supplies ``client`` (perception only), ``human`` (all input), ``vision``,
``recognizer``, ``log``, ``capture_evidence`` and ``telemetry``. Skills keep no
state between calls, so ``BaseTask`` creates them on demand.
"""
