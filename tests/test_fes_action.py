from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import agent.realtime.fes_action as fes_action
from agent.realtime.fes_action import (
    DEFAULT_SETTINGS,
    FES_DIFFICULTY_TARGETS,
    FesLiveFlow,
    configure_fes_settings,
    current_fes_settings,
    fes_play_params,
)
from agent.realtime.profile_play_action import _recording_kind


ROOT = Path(__file__).parents[1]


def _bare_flow():
    flow = object.__new__(FesLiveFlow)
    flow.context = SimpleNamespace(tasker=SimpleNamespace(stopping=False))
    return flow


@pytest.mark.parametrize("count", [0, 1, 100, 999])
def test_fes_accepts_infinite_and_count_limit(count):
    assert configure_fes_settings({"reset": True, "count": count})["count"] == count


@pytest.mark.parametrize("count", [-1, 1000, "abc"])
def test_fes_rejects_invalid_count(count):
    with pytest.raises(ValueError):
        configure_fes_settings({"reset": True, "count": count})


def test_fes_configure_merges_without_reset():
    configure_fes_settings({"reset": True, "count": 2, "difficulty": "Hard"})
    settings = configure_fes_settings({"difficulty": "Special"})
    assert settings["count"] == 2
    assert settings["difficulty"] == "Special"
    # 未提供的键保留默认值
    assert settings["entry_method"] == DEFAULT_SETTINGS["entry_method"]


def test_fes_play_params_declare_fes_run_mode():
    params = fes_play_params({"difficulty": "Expert", "debug_recording": False, "diagnostic_trace": True})
    assert params["run_mode"] == "fes"
    assert params["difficulty"] == "Expert"
    assert params["require_profile"] is True
    assert params["require_completion"] is True
    assert params["wait_for_completion"] is True
    # 骨架不启用协力专属机制
    assert "life_depleted_jump_request" not in params
    assert "defer_result_collection" not in params


def test_fes_recording_kind_registered():
    assert _recording_kind("fes") == "fes"


def test_fes_difficulty_targets_cover_all_difficulties():
    assert set(FES_DIFFICULTY_TARGETS) == {"Easy", "Normal", "Hard", "Expert", "Special"}


def test_fes_unlimited_continues_until_stop():
    flow = _bare_flow()
    flow.settings = {"count": 0}
    completed = []
    flow.run_attempt = lambda: True

    def progress(current, total):
        assert total == 0
        completed.append(current)
        if current == 5:
            flow.context.tasker.stopping = True

    flow.progress_callback = progress
    flow.recover_after_play_failure = lambda _reason: None
    assert flow.run() is True
    assert completed == [1, 2, 3, 4, 5]


def test_fes_count_stops_after_reaching_total():
    flow = _bare_flow()
    flow.settings = {"count": 3}
    completed = []
    flow.run_attempt = lambda: True
    flow.recover_after_play_failure = lambda _reason: None

    def progress(current, total):
        completed.append((current, total))

    flow.progress_callback = progress
    assert flow.run() is True
    assert [current for current, _ in completed] == [1, 2, 3]


def test_fes_retry_budget_not_clamped_to_one():
    flow = _bare_flow()
    flow.settings = {"count": 1, "play_failure_retry_count": 99}
    attempts = []
    flow.recover_after_play_failure = lambda _reason: None

    def attempt():
        attempts.append(True)
        return len(attempts) == 100

    flow.run_attempt = attempt
    assert flow.run() is True
    assert len(attempts) == 100


def test_fes_retry_exhaustion_propagates_failure():
    flow = _bare_flow()
    flow.settings = {"count": 1, "play_failure_retry_count": 0}
    flow.recover_after_play_failure = lambda _reason: None

    def attempt():
        raise RuntimeError("boom")

    flow.run_attempt = attempt
    with pytest.raises(RuntimeError, match="boom"):
        flow.run()


def test_fes_play_failure_returns_false_when_budget_exhausted():
    flow = _bare_flow()
    flow.settings = {"count": 1, "play_failure_retry_count": 0}
    flow.run_attempt = lambda: False
    assert flow.run() is False


def test_fes_user_stop_returns_true_mid_round():
    flow = _bare_flow()
    flow.settings = {"count": 5}

    def attempt():
        flow.context.tasker.stopping = True
        return True

    flow.run_attempt = attempt
    assert flow.run() is True


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_fes_pipeline_contract():
    pipeline = load(ROOT / "resource" / "pipeline" / "fes_live.json")
    interface = load(ROOT / "interface.json")
    options = interface["option"]

    assert pipeline["FesLive"]["next"] == ["FesProcessConflictGuard"]
    assert pipeline["FesProcessConflictGuard"]["custom_action"] == "ProcessConflictGuard"
    assert pipeline["FesRecover"]["custom_action"] == "CommonRecover"
    assert pipeline["FesRecover"]["next"] == ["FesEntryConfigure"]
    for name in (
        "FesEntryConfigure",
        "FesRoomCodeConfigure",
        "FesDifficultyConfigure",
        "FesCountConfigure",
        "FesDebugConfigure",
    ):
        assert pipeline[name]["custom_action"] == "FesLiveConfigure"
    assert pipeline["FesEntryConfigure"]["next"] == ["FesRoomCodeConfigure"]
    assert pipeline["FesRoomCodeConfigure"]["next"] == ["FesDifficultyConfigure"]
    assert pipeline["FesDifficultyConfigure"]["next"] == ["FesCountConfigure"]
    assert pipeline["FesCountConfigure"]["next"] == ["FesDebugConfigure"]
    assert pipeline["FesDebugConfigure"]["next"] == ["FesSpeedSettingsGate"]
    assert pipeline["FesSpeedSettingsGate"]["custom_action"] == "RealtimeGameSpeedSettingsGate"
    assert pipeline["FesRun"]["custom_action"] == "FesLiveFlow"
    assert pipeline["FesReturnHome"]["custom_action"] == "FesLiveFinalize"
    assert pipeline["FesComplete"]["custom_action"] == "TaskOutcome"
    assert pipeline["FesComplete"]["custom_action_param"]["status"] == "success"
    assert pipeline["FesFailure"]["custom_action"] == "TaskOutcome"
    assert pipeline["FesFailure"]["custom_action_param"]["status"] == "failure"
    assert pipeline["FesFailure"]["custom_action_param"]["reason_source"] == "latest"

    task = next(task for task in interface["task"] if task["name"] == "FesLive")
    assert task["entry"] == "FesLive"
    assert task["option"] == ["FesDifficulty", "FesCount", "FesDebug"]

    count = options["FesCount"]
    assert count["type"] == "input"
    assert count["inputs"][0]["pipeline_type"] == "int"
    assert count["pipeline_override"]["FesCountConfigure"]["custom_action_param"] == {
        "count": "{Count}"
    }

    difficulty_cases = options["FesDifficulty"]["cases"]
    assert [case["name"] for case in difficulty_cases] == [
        "Easy", "Normal", "Hard", "Expert", "Special",
    ]
    for case in difficulty_cases:
        override = case["pipeline_override"]["FesDifficultyConfigure"]
        assert override["custom_action_param"] == {"difficulty": case["name"]}
        gate = case["pipeline_override"]["FesSpeedSettingsGate"]["custom_action_param"]
        assert gate["difficulty"] == case["name"]

    debug_cases = options["FesDebug"]["cases"]
    assert [case["name"] for case in debug_cases] == ["Light", "Off", "Full"]

    # 导航入口使用 OCR（无 fes 专属模板），LiveSelectFind 先模板后 OCR，
    # 未提供 template_node 时直接 OCR 识别“团队演出”。
    assert pipeline["FesSelectPage"]["custom_action"] == "LiveSelectFind"
    assert pipeline["FesSelectPage"]["custom_action_param"]["click"] is False
    assert pipeline["FesEnterLive"]["custom_action_param"]["click"] is True
    assert pipeline["FesEnterLive"]["custom_action_param"]["expected"] == "团队演出"


def test_fes_interface_options_resolve_in_real_maafw(tmp_path):
    import os
    import subprocess
    import sys

    interface = load(ROOT / "interface.json")
    pipeline = load(ROOT / "resource/pipeline/fes_live.json")
    nodes = (
        "FesDifficultyConfigure",
        "FesDebugConfigure",
        "FesSpeedSettingsGate",
    )
    difficulty_case = next(
        case for case in interface["option"]["FesDifficulty"]["cases"]
        if case["name"] == "Expert"
    )
    debug_case = next(
        case for case in interface["option"]["FesDebug"]["cases"]
        if case["name"] == "Light"
    )
    code = """
import json, sys
from maa.resource import Resource
from maa.toolkit import Toolkit
data = json.load(sys.stdin)
Toolkit.init_option(data['log_dir'])
resource = Resource()
assert resource.override_pipeline({node: data['pipeline'][node] for node in data['nodes']})
assert resource.override_pipeline(data['difficulty'])
assert resource.override_pipeline(data['debug'])
effective = {}
for node in data['nodes']:
    effective[node] = resource.get_node_data(node)['action']['param']['custom_action_param']
print(json.dumps(effective))
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        input=json.dumps({
            "log_dir": str(tmp_path),
            "pipeline": pipeline,
            "nodes": nodes,
            "difficulty": difficulty_case["pipeline_override"],
            "debug": debug_case["pipeline_override"],
        }),
        text=True,
        capture_output=True,
        check=True,
        env={"PYTHONUTF8": "1", "PATH": os.environ["PATH"]},
    )
    effective = json.loads(result.stdout)
    assert effective["FesDifficultyConfigure"] == {"difficulty": "Expert"}
    assert effective["FesSpeedSettingsGate"]["difficulty"] == "Expert"
    assert effective["FesDebugConfigure"] == {
        "debug_recording": False,
        "diagnostic_trace": True,
    }
