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
    # run_recognition 默认未命中：FesHomeLive 模板判定由各测试自行 stub。
    flow.context = SimpleNamespace(
        tasker=SimpleNamespace(stopping=False),
        run_recognition=lambda _node, _image: None,
    )
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
    settings = configure_fes_settings({"difficulty": "Expert"})
    assert settings["count"] == 2
    assert settings["difficulty"] == "Expert"
    # 未提供的键保留默认值
    assert settings["diagnostic_trace"] == DEFAULT_SETTINGS["diagnostic_trace"]


def test_fes_rejects_special_difficulty():
    # Fes 活动只有四档难度，Special 必须在配置阶段被拒绝。
    with pytest.raises(ValueError, match="Special"):
        configure_fes_settings({"reset": True, "difficulty": "Special"})


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
    # 四档难度；Fes 没有 Special。
    assert set(FES_DIFFICULTY_TARGETS) == {"Easy", "Normal", "Hard", "Expert"}


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


def _box(x=1000, y=600, w=80, h=40):
    return SimpleNamespace(x=x, y=y, w=w, h=h)


def _ocr_stub(responses: dict):
    """按 expected 文本返回预设 box；列表按调用次序弹出，标量重复返回。"""

    def ocr(image, text, **_kwargs):
        value = responses.get(text)
        if isinstance(value, list):
            return value.pop(0) if value else None
        return value

    return ocr


def test_fes_enter_room_confirms_when_final_page_visible():
    flow = _bare_flow()
    flow.match_timeout_seconds = 5.0
    flow.capture = lambda: None
    flow._ocr_box = _ocr_stub({"最终确认": _box()})
    assert flow.enter_room() is None


def test_fes_enter_room_waits_through_matching_state():
    flow = _bare_flow()
    flow.match_timeout_seconds = 10.0
    flow.capture = lambda: None
    # 前两次最终确认未出现（列表弹出 None），匹配中一直可见，第三次命中。
    flow._ocr_box = _ocr_stub({
        "最终确认": [None, None, _box()],
        "匹配中": _box(),
    })
    assert flow.enter_room() is None


def test_fes_enter_room_times_out_with_clear_reason():
    flow = _bare_flow()
    flow.match_timeout_seconds = 0.0
    flow.capture = lambda: None
    flow._ocr_box = _ocr_stub({})
    with pytest.raises(RuntimeError, match="最终确认页"):
        flow.enter_room()


def test_fes_enter_room_navigates_from_home_between_rounds():
    flow = _bare_flow()
    flow.match_timeout_seconds = 5.0
    flow.capture = lambda: None
    # 第一次 OCR 全未命中（不在流程中），主页模板命中触发导航；
    # 导航后等待循环第一次就看到最终确认页。
    flow._ocr_box = _ocr_stub({"最终确认": [None, _box()]})
    flow.context.run_recognition = (
        lambda node, _image: (
            SimpleNamespace(hit=True, box=_box())
            if node == "FesHomeLive" else None
        )
    )
    navigated = []
    flow._navigate_to_entry = lambda: navigated.append(True)
    assert flow.enter_room() is None
    assert navigated == [True]


def test_fes_enter_room_skips_navigation_when_already_in_flow():
    flow = _bare_flow()
    flow.match_timeout_seconds = 5.0
    flow.capture = lambda: None
    navigated = []
    flow._navigate_to_entry = lambda: navigated.append(True)
    # 匹配中可见 → 已在流程中，跳过导航；最终确认随后出现。
    flow._ocr_box = _ocr_stub({
        "最终确认": [None, _box()],
        "匹配中": _box(),
    })
    assert flow.enter_room() is None
    assert navigated == []


def test_fes_navigate_requires_home_entry():
    flow = _bare_flow()
    flow.entry_home_timeout_seconds = 0.0
    flow.capture = lambda: None
    flow.context.run_recognition = lambda _node, _image: None
    with pytest.raises(RuntimeError, match="演出"):
        flow._navigate_to_entry()


def test_fes_ready_up_taps_prepare_and_waits_for_departure():
    flow = _bare_flow()
    flow.ready_departure_timeout_seconds = 10.0
    flow.capture = lambda: None
    clicks = []
    flow.click = clicks.append
    flow._ocr_box = _ocr_stub({
        "准备完": _box(x=1000, y=600, w=80, h=40),
        "最终确认": None,  # 点击后页面离开
    })
    flow._ready_up_and_wait()
    assert clicks == [(1040, 620)]


def test_fes_ready_up_fails_when_button_missing():
    flow = _bare_flow()
    flow.capture = lambda: None
    flow._ocr_box = _ocr_stub({"准备完": None})
    with pytest.raises(RuntimeError, match="准备完"):
        flow._ready_up_and_wait()


def test_fes_ready_up_times_out_if_page_never_departs():
    flow = _bare_flow()
    flow.ready_departure_timeout_seconds = 0.0
    flow.capture = lambda: None
    flow.click = lambda _point: None
    flow._ocr_box = _ocr_stub({"准备完": _box()})
    with pytest.raises(RuntimeError, match="未离开最终确认页"):
        flow._ready_up_and_wait()


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
        "FesDifficultyConfigure",
        "FesCountConfigure",
        "FesDebugConfigure",
    ):
        assert pipeline[name]["custom_action"] == "FesLiveConfigure"
    # v1 只支持自动匹配：无房间号配置节点。
    assert "FesRoomCodeConfigure" not in pipeline
    assert pipeline["FesEntryConfigure"]["next"] == ["FesDifficultyConfigure"]
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
        "Easy", "Normal", "Hard", "Expert",
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
