#!/usr/bin/env python3
"""Validate or submit one ordinary steel-module-stack-v1 OSC campaign."""

from __future__ import annotations

import sys

import submit_realistic_neutron_campaign as implementation
from record_realistic_neutron_task_result import integer_field, read_single_csv_row
from record_steel_module_stack_task_result import validate_run_config
from steel_module_stack_campaign_lib import (
    RESULT_SCHEMA_VERSION,
    CampaignBundle,
    CampaignTask,
    load_campaign,
    load_json,
    read_attempt_rows,
    resolve_campaign_path,
    task_by_logical_id,
)


implementation.__doc__ = __doc__
implementation.CampaignBundle = CampaignBundle
implementation.CampaignTask = CampaignTask
implementation.RESULT_SCHEMA_VERSION = RESULT_SCHEMA_VERSION
implementation.load_campaign = load_campaign
implementation.load_json = load_json
implementation.read_attempt_rows = read_attempt_rows
implementation.resolve_campaign_path = resolve_campaign_path
implementation.task_by_logical_id = task_by_logical_id
implementation.integer_field = integer_field
implementation.read_single_csv_row = read_single_csv_row
implementation.validate_run_config = validate_run_config


if __name__ == "__main__":
    raise SystemExit(implementation.main())
