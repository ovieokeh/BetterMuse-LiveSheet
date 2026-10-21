#!/usr/bin/env python3
"""Separate correction of the exposed meter-ties reference; originals stay frozen.

The old third bar omitted a 4/4 change despite containing four beats. This
revision changes only that printed meter. It does not change expected attacks,
comparison tolerances, the original manifest, or production recognition inputs.
"""
import argparse
from copy import deepcopy
from fractions import Fraction as F
import json
from pathlib import Path
from livesheet.testing import pdf_performance_fixtures as fixtures
from livesheet.testing import check_pdf_performance as benchmark

DEFAULT = benchmark.DEFAULT / "corrected-meter-v1"


from livesheet.testing.pdf_reference_validation import validate_authored as validate_budgets


def corrected_spec():
    spec = deepcopy(fixtures.CASES["meter-ties"])
    spec["measures"][2]["time"] = [4, 4]
    spec["split"] = "development"
    validate_budgets(spec)
    return spec

