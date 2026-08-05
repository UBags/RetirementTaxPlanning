# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""data_synthesizer -- fidelity metrics and forward synthesis for NAV series.

Read docs/FIDELITY_CRITERIA.md for what is being targeted and STATUS.md for
what has been measured, what was retracted, and why.

THE ORDER HERE IS DELIBERATE: metrics before generator. A synthesizer that
cannot be scored will be judged on the two statistics it happens to report.
"""
from . import engine, metrics, quality, verify  # noqa: F401

__all__ = ["engine", "metrics", "quality", "verify"]
