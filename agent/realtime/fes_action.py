from __future__ import annotations

import json
import threading
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

from maa.agent.agent_server import AgentServer
from maa.context import Context
from maa.custom_action import CustomAction

try:
    from ..common_recover import CommonRecover
    from ..foreground_guard import GAME_PACKAGE
    from ..task_reporting import TaskProgress, record_failure_reason
except ImportError:
    from common_recover import CommonRecover
    from foreground_guard import GAME_PACKAGE
    from task_reporting import TaskProgress, record_failure_reason

from .difficulty_action import RealtimeDifficultySelect
from .game_effect_settings_action import _click as _maa_click
from .live_session import (
    append_current_run_event,
    current_live_run,
)
from .performance_settings_action import RealtimePerformanceSettingsGate
from .profile_play_action import RealtimeProfilePlay
from .profile_store import RealtimeProfileStore


PROJECT_ROOT = Path(__file__).resolve().parents[2]
# 与协力保持一致：准备页环境参数同时用于 Profile 预检，避免两处硬编码漂移。
FES_DPI = 240
FES_GAME_FPS = 60
FES_RENDER_QUALITY = "standard"
# Fes 活动的准备页与协力/单人共用同一布局，难度按钮沿用标准目标点。
FES_DIFFICULTY_TARGETS = {
    "Easy": (602, 575),
    "Normal": (687, 575),
    "Hard": (769, 575),
    "Expert": (852, 575),
    "Special": (942, 575),
}

DEFAULT_SETTINGS: dict[str, object] = {
    "entry_method": "normal",
    "room_code": "",
    "difficulty": "Expert",
    "count": 1,
    "debug_recording": False,
    "diagnostic_trace": True,
}
_SETTINGS = dict(DEFAULT_SETTINGS)
_SETTINGS_LOCK = threading.Lock()


def configure_fes_settings(params: dict[str, object]) -> dict[str, object]:
    with _SETTINGS_LOCK:
        candidate = (
            dict(DEFAULT_SETTINGS)
            if bool(params.get("reset", False))
            else dict(_SETTINGS)
        )
        for key in DEFAULT_SETTINGS:
            if key in params:
                candidate[key] = params[key]
        count = int(candidate.get("count", 1))
        if not 0 <= count <= 999:
            raise ValueError("团队演出 Fes 次数必须是0到999的整数，0表示无限")
        candidate["count"] = count
        _SETTINGS.clear()
        _SETTINGS.update(candidate)
        return dict(_SETTINGS)


def current_fes_settings() -> dict[str, object]:
    with _SETTINGS_LOCK:
        return dict(_SETTINGS)


def fes_play_params(
    settings: dict[str, object],
    *,
    effective_difficulty: str | None = None,
) -> dict[str, object]:
    """构造 RealtimeProfilePlay 的演奏参数，run_mode=fes。

    骨架阶段沿用协力的演奏约束（等最终封面、成员下载窗口、结算导航），
    但不启用任何协力专属机制（断网跳车、成员退出监听、结算后留在房间）。
    """
    return {
        "difficulty": str(effective_difficulty or settings["difficulty"]),
        "require_profile": True,
        "settings_gate_required": True,
        "debug_recording": bool(settings["debug_recording"]),
        "diagnostic_trace": bool(settings["diagnostic_trace"]),
        "duration_seconds": 600,
        "startup_timeout_seconds": 60,
        "dpi": FES_DPI,
        "game_fps": FES_GAME_FPS,
        "render_quality": FES_RENDER_QUALITY,
        "wait_for_completion": True,
        "completion_missing_frames": 30,
        "require_completion": True,
        "save_result_frame": True,
        "result_back_attempts": 30,
        "result_back_interval_seconds": 1.5,
        "continue_after_life_depleted": True,
        "run_mode": "fes",
        "confirm_final_cover": True,
        "native_prearm_deferred": True,
    }


def fes_profile_preflight(context: Context, difficulty: str) -> str | None:
    """任务一开始就校验 Profile 与环境签名，失败返回可读原因。

    与协力相同：把 Profile 解析提前到导航之前，避免自动化完成整段
    导航后才被准备页门禁拒绝。
    """
    from .rehearsal_action import frame_resolution
    from .profile_store import EnvironmentSignature, engine_from_native_flag

    store = RealtimeProfileStore(PROJECT_ROOT / "profiles")
    try:
        image = context.tasker.controller.post_screencap().wait().get()
    except Exception:
        # 控制器尚未就绪时无法构造签名，交给准备页门禁处理。
        return None
    options = store.runtime_options()
    signature = EnvironmentSignature(
        frame_resolution(image),
        FES_DPI,
        FES_GAME_FPS,
        FES_RENDER_QUALITY,
        1.0,
        engine=engine_from_native_flag(
            options.get("native_realtime_enabled", False)
        ),
    )
    try:
        store.resolve_latest_for_environment(
            difficulty=difficulty,
            current_signature=signature,
        )
    except ValueError as exc:
        return f"开局前 Profile 环境校验失败：{exc}"
    return None


class FesLiveFlow:
    def __init__(
        self,
        context: Context,
        settings: dict[str, object],
        *,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> None:
        self.context = context
        self.settings = settings
        self.effective_difficulty = str(settings["difficulty"])
        self.progress_callback = progress_callback

    def stopped(self) -> bool:
        return bool(self.context.tasker.stopping)

    @property
    def controller(self):
        """Always return MaaFramework's current reverse-controller proxy."""
        return self.context.tasker.controller

    def capture(self):
        if self.stopped():
            raise InterruptedError("用户已停止任务")
        return self.context.tasker.controller.post_screencap().wait().get()

    @staticmethod
    def action_argv(params: dict[str, object]):
        return SimpleNamespace(
            custom_action_param=json.dumps(params, ensure_ascii=False),
        )

    def click(self, point: tuple[int, int]) -> None:
        _maa_click(self.context, point)

    def enter_room(self) -> None:
        """房间创建/加入的界面识别待真机截图补充。

        骨架阶段此方法只确认现状：任务启动后由 pipeline 导航进入活动，
        房间流程期望设备已经处于可进入准备页的状态（例如用户已建房或
        已加入房间）。补充 `resource/image/fes/` 截图模板后，这里负责
        建房/入房/等待成员的完整自动化。
        """
        print(
            "FesLive room_navigation=pending-screenshots "
            "expect=prepare-page-reachable",
            flush=True,
        )

    def prepare(self) -> None:
        difficulty = str(self.settings["difficulty"])
        difficulty_params = {
            "difficulty": difficulty,
            "max_attempts": 3,
            "verify_delay_seconds": 0.25,
            "identity_read_attempts": 2,
            "identity_retry_delay_seconds": 0.15,
            "difficulty_targets": FES_DIFFICULTY_TARGETS,
            "song_level_roi": (130, 580, 56, 38),
            "song_title_roi": (105, 535, 290, 52),
            "song_identity": False,
            "mode": "fes",
            "debug_recording": bool(self.settings["debug_recording"]),
        }
        if difficulty == "Special":
            # 与协力一致：Special 缺席时显式回退 Expert，后续流程只消费
            # 实际选中的难度，不能继续拿 Special 谱面演奏。
            difficulty_params["fallback_difficulties"] = ["Expert"]
        if not RealtimeDifficultySelect().run(
            self.context, self.action_argv(difficulty_params)
        ):
            raise RuntimeError(
                f"团队演出 Fes 准备页未能选择并复核 {difficulty} 难度"
            )
        run = current_live_run()
        if run is None or not run.prepared_for_play:
            raise RuntimeError("团队演出 Fes 难度选择成功但缺少本局实际难度证据")
        effective_difficulty = str(run.difficulty)
        if effective_difficulty != difficulty and not (
            difficulty == "Special" and effective_difficulty == "Expert"
        ):
            raise RuntimeError(
                "团队演出 Fes 实际难度不符合回退策略："
                f"请求 {difficulty}，实际 {effective_difficulty}"
            )
        self.effective_difficulty = effective_difficulty

        performance_params = {
            "difficulty": effective_difficulty,
            "require_profile": True,
            "dpi": FES_DPI,
            "game_fps": FES_GAME_FPS,
            "render_quality": FES_RENDER_QUALITY,
            "coordinates": {"gear": (946, 650)},
            "defer_native_prearm": True,
            "cache_preparation_image": True,
        }
        if not RealtimePerformanceSettingsGate().run(
            self.context, self.action_argv(performance_params)
        ):
            raise RuntimeError("团队演出 Fes 准备页流速复核失败")
        print(
            "FesLive prepare=ready "
            f"requested_difficulty={difficulty} "
            f"effective_difficulty={effective_difficulty} "
            "speed_gate=verified",
            flush=True,
        )

    def play(self) -> bool:
        params = fes_play_params(
            self.settings,
            effective_difficulty=getattr(
                self,
                "effective_difficulty",
                str(self.settings.get("difficulty", "Expert")),
            ),
        )
        return bool(RealtimeProfilePlay().run(self.context, self.action_argv(params)))

    def run_attempt(self) -> bool:
        self.enter_room()
        self.prepare()
        return self.play()

    def recover_after_play_failure(self, reason: str) -> None:
        """完整清理失败单局并从主页重新进入活动，禁止在旧会话中续跑。"""
        from .native_prearm import discard_prearmed_backend

        discard_prearmed_backend("fes-play-retry")
        recovery_params = {
            "home_node": "FesHomeMarker",
            "modal_cancel_nodes": ["QuitConfirmCancel"],
            "click_nodes": [
                "AutoLiveLoginTap",
                "AutoLiveLoginNext",
                "AutoLiveCommonClose",
                "AutoLiveStorySkipConfirmLarge",
                "AutoLiveStorySkipConfirm",
                "AutoLiveStorySkip",
                "AutoLiveStoryMenu",
            ],
            "escape_interval_ms": 1500,
            "escape_timeout_ms": 60000,
            "restart_limit": 2,
            "restart_wait_ms": 5000,
            "startup_grace_ms": 12000,
            "login_start_node": "AutoLiveLoginScreenMarker",
            "login_start_target": [640, 635],
            "login_marker_priority_attempts": 3,
            "escape_after_login_start": True,
            "package": GAME_PACKAGE,
        }
        argv = SimpleNamespace(
            custom_action_param=json.dumps(
                recovery_params,
                ensure_ascii=False,
            )
        )
        if not CommonRecover().run(self.context, argv):
            raise RuntimeError(
                f"团队演出 Fes 单局失败后无法恢复主页：{reason}"
            )

    def run(self) -> bool:
        total = int(self.settings.get("count", 1))
        completed = 0
        play_failures = 0
        retry_count = max(
            0,
            min(99, int(self.settings.get("play_failure_retry_count", 0))),
        )
        while total == 0 or completed < total:
            if self.stopped():
                return True
            print(
                f"FesLive round={completed + 1}/{total or '无限'}",
                flush=True,
            )
            try:
                success = self.run_attempt()
            except InterruptedError:
                raise
            except Exception as exc:
                if play_failures >= retry_count:
                    raise
                play_failures += 1
                reason = f"{type(exc).__name__}: {exc}"
                try:
                    append_current_run_event(
                        PROJECT_ROOT,
                        "retry",
                        "scheduled",
                        details={
                            "mode": "fes",
                            "attempt": play_failures + 1,
                            "attempt_limit": retry_count + 1,
                            "reason": reason,
                        },
                    )
                except Exception as evidence_error:
                    print(
                        "FesLive retry_evidence_failed="
                        f"{type(evidence_error).__name__}: {evidence_error}",
                        flush=True,
                    )
                print(
                    "FesLive play_retry=true "
                    f"attempt={play_failures + 1}/{retry_count + 1} "
                    f"reason={reason}",
                    flush=True,
                )
                self.recover_after_play_failure(reason)
                continue
            if self.stopped():
                return True
            if not success:
                if play_failures >= retry_count:
                    return False
                play_failures += 1
                reason = "RealtimeProfilePlay 返回失败"
                print(
                    "FesLive play_retry=true "
                    f"attempt={play_failures + 1}/{retry_count + 1} "
                    f"reason={reason}",
                    flush=True,
                )
                self.recover_after_play_failure(reason)
                continue

            completed += 1
            play_failures = 0
            callback = getattr(self, "progress_callback", None)
            if callback is not None:
                try:
                    callback(completed, total)
                except Exception as exc:
                    print(
                        f"FesLive progress_warning={type(exc).__name__}: {exc}",
                        flush=True,
                    )
            if self.stopped():
                return True
            if not (total == 0 or completed < total):
                break
            # 多轮之间回主页再重新导航，保持每一轮的起点可预期。
            try:
                self.recover_after_play_failure("进入下一轮")
            except InterruptedError:
                raise
            except Exception as exc:
                print(
                    f"FesLive post_round_recover_warning={type(exc).__name__}: {exc}",
                    flush=True,
                )
        return True


@AgentServer.custom_action("FesLiveConfigure")
class FesLiveConfigure(CustomAction):
    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        if context.tasker.stopping:
            return True
        try:
            settings = configure_fes_settings(
                json.loads(argv.custom_action_param or "{}")
            )
            print(f"FesLive configured={settings}", flush=True)
            return True
        except Exception as exc:
            record_failure_reason(
                f"团队演出 Fes 选项无效：{type(exc).__name__}: {exc}"
            )
            traceback.print_exc()
            return False


@AgentServer.custom_action("FesLiveFlow")
class FesLiveAction(CustomAction):
    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        try:
            if context.tasker.stopping:
                return True
            settings = current_fes_settings()
            settings["play_failure_retry_count"] = int(
                RealtimeProfileStore(
                    PROJECT_ROOT / "profiles"
                ).runtime_options().get("play_failure_retry_count", 1)
            )
            preflight_error = fes_profile_preflight(
                context, str(settings["difficulty"])
            )
            if preflight_error is not None:
                record_failure_reason(preflight_error)
                print(
                    f"[任务][团队演出 Fes][流程][ERROR] {preflight_error}",
                    flush=True,
                )
                return False

            def progress_argv(phase: str, total: int):
                return SimpleNamespace(
                    custom_action_param=json.dumps(
                        {
                            "task_name": "FesLive",
                            "label": "团队演出 Fes",
                            "total": total,
                            "phase": phase,
                        },
                        ensure_ascii=False,
                    ),
                    task_detail=getattr(argv, "task_detail", None),
                    node_name=getattr(argv, "node_name", "FesRun"),
                )

            total = int(settings["count"])
            if not TaskProgress().run(context, progress_argv("start", total)):
                raise RuntimeError("团队演出 Fes 次数初始化失败")

            def report_progress(_completed: int, expected_total: int) -> None:
                if not TaskProgress().run(
                    context,
                    progress_argv("completed", expected_total),
                ):
                    raise RuntimeError("团队演出 Fes 次数进度记录失败")

            return FesLiveFlow(
                context,
                settings,
                progress_callback=report_progress,
            ).run()
        except InterruptedError:
            return True
        except Exception as exc:
            if context.tasker.stopping:
                return True
            reason = f"团队演出 Fes 失败：{type(exc).__name__}: {exc}"
            record_failure_reason(reason)
            traceback.print_exc()
            print(f"[任务][团队演出 Fes][流程][ERROR] {reason}", flush=True)
            return False


@AgentServer.custom_action("FesLiveFinalize")
class FesLiveFinalize(CustomAction):
    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        try:
            if context.tasker.stopping:
                return True
            if not CommonRecover().run(context, argv):
                print(
                    "FesLive finalize_warning=演出已完成，主页恢复失败不终止任务",
                    flush=True,
                )
            return True
        except Exception as exc:
            if context.tasker.stopping:
                return True
            reason = f"团队演出 Fes 结束导航失败：{type(exc).__name__}: {exc}"
            traceback.print_exc()
            print(f"FesLive finalize_warning={reason}", flush=True)
            return True
