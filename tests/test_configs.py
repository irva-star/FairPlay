"""The real config files load the way the analysis expects (audit C1).

PyYAML reads a bare `on` or `off` as a boolean. That once turned the ON batch's
label into "True", so no contrast ever matched and every analysis came back
empty. These tests load the committed files, not hand-written dicts.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from stats.__main__ import contrasts_for, load_prereg
from walker.cli import build_plan

BATCHES = {
    "configs/run_pilot.yaml": ("pilot", "on"),
    "configs/run_on.yaml": ("on", "on"),
    "configs/run_off.yaml": ("off", "off"),
}


@pytest.mark.parametrize("path", sorted(BATCHES))
def test_batch_configs_load_with_string_label_and_switch(path: str) -> None:
    plan = build_plan(path)
    assert (plan.label, plan.switch) == BATCHES[path]


def test_prereg_contrasts_match_each_batch_switch() -> None:
    prereg = load_prereg()
    assert set(contrasts_for("on", prereg)) == {"V1_sensitivity", "V3_aa"}
    assert set(contrasts_for("off", prereg)) == {"V2_specificity"}


def test_every_run_switch_has_a_contrast() -> None:
    prereg = load_prereg()
    for _, switch in BATCHES.values():
        assert contrasts_for(switch, prereg)


@pytest.mark.parametrize("field, value", [("label", "on"), ("switch", "on"), ("switch", "off")])
def test_an_unquoted_on_or_off_is_refused(tmp_path: Path, field: str, value: str) -> None:
    text = Path("configs/run_on.yaml").read_text(encoding="utf-8")
    quoted = f'{field}: "on"'
    assert quoted in text
    config = tmp_path / "bad.yaml"
    config.write_text(text.replace(quoted, f"{field}: {value}"), encoding="utf-8")
    assert not isinstance(yaml.safe_load(config.read_text(encoding="utf-8"))[field], str)
    with pytest.raises(ValueError, match=field):
        build_plan(config)


def test_an_unquoted_prereg_batch_is_refused() -> None:
    prereg = {"contrasts": {"V1": {"batch": True, "a": "spender", "b": "free"}}}
    with pytest.raises(ValueError, match="quoted"):
        contrasts_for("on", prereg)
