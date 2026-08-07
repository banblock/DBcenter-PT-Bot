#!/usr/bin/env python3

from __future__ import annotations

from typing import Callable, Optional

from action_msgs.msg import GoalStatus
from irobot_create_msgs.action import Dock, Undock
from rclpy.action import ActionClient
from rclpy.node import Node
from rclpy.task import Future


class StopController:
    """도킹 스테이션 복귀 -> 대기(standby) -> 언도킹까지의 "정지" 동작을 담당한다.

    Dock/Undock 액션과 대기 Timer는 로봇 자기 자신의 베이스 드라이버를
    다루는 patrol 내부 동작이라(Fleet/Nav2처럼 타 팀 시스템과의 통신이
    아님) 여기서 ROS 자원(ActionClient, Timer)을 직접 만들어 사용한다.
    ActionClient/Timer 생성에는 Node 핸들이 필요하므로 patrol_node.py가
    자기 자신(self)을 넘겨준다.
    """

    def __init__(
        self,
        node: Node,
        robot_id: int,
        standby_duration_sec: float,
        on_resume: Callable[[], None],
        logger,
    ) -> None:
        self._node = node
        self._on_resume = on_resume
        self._logger = logger
        self.standby_duration_sec = standby_duration_sec

        self.dock_action_name = f"/robot{robot_id}/dock"
        self.undock_action_name = f"/robot{robot_id}/undock"

        self.dock_action_client = ActionClient(node, Dock, self.dock_action_name)
        self.undock_action_client = ActionClient(
            node, Undock, self.undock_action_name
        )

        self.standby_timer = None

    # ------------------------------------------------------------------
    # 진입점: 순찰 리스트 소진 -> 도킹 스테이션 복귀
    # ------------------------------------------------------------------
    def enter_standby(self) -> None:
        self._logger.info("도킹 스테이션으로 복귀합니다.")
        self._send_dock_goal()

    # ------------------------------------------------------------------
    # Dock
    # ------------------------------------------------------------------
    def _send_dock_goal(self) -> None:
        if not self.dock_action_client.wait_for_server(timeout_sec=5.0):
            self._logger.error(
                f"Dock 액션 서버를 찾을 수 없습니다: {self.dock_action_name}"
            )
            return

        goal = Dock.Goal()
        future = self.dock_action_client.send_goal_async(goal)
        future.add_done_callback(self._on_dock_goal_response)

    def _on_dock_goal_response(self, future: Future) -> None:
        goal_handle = future.result()
        if not goal_handle.accepted:
            self._logger.error("Dock 목표가 거부되었습니다.")
            return
        result_future = goal_handle.get_result_async()
        result_future.add_done_callback(self._on_dock_result)

    def _on_dock_result(self, future: Future) -> None:
        status = future.result().status
        if status != GoalStatus.STATUS_SUCCEEDED:
            self._logger.error(f"도킹 실패 (status={status})")
            return

        self._logger.info("도킹 완료 - 대기 상태로 전환합니다.")
        self._start_standby_timer()

    # ------------------------------------------------------------------
    # 대기 (Timer)
    # ------------------------------------------------------------------
    def _start_standby_timer(self) -> None:
        self._logger.info(f"{self.standby_duration_sec:.1f}초 대기를 시작합니다.")
        self.standby_timer = self._node.create_timer(
            self.standby_duration_sec, self._on_standby_timer_fired
        )

    def _on_standby_timer_fired(self) -> None:
        self._logger.info("대기 시간 종료 - 순찰 재개를 위해 언도킹합니다.")
        self._cancel_standby_timer()
        self._send_undock_goal()

    def _cancel_standby_timer(self) -> None:
        if self.standby_timer is None:
            return
        self.standby_timer.cancel()
        self._node.destroy_timer(self.standby_timer)
        self.standby_timer = None

    # ------------------------------------------------------------------
    # Undock
    # ------------------------------------------------------------------
    def _send_undock_goal(self) -> None:
        if not self.undock_action_client.wait_for_server(timeout_sec=5.0):
            self._logger.error(
                f"Undock 액션 서버를 찾을 수 없습니다: {self.undock_action_name}"
            )
            return

        goal = Undock.Goal()
        future = self.undock_action_client.send_goal_async(goal)
        future.add_done_callback(self._on_undock_goal_response)

    def _on_undock_goal_response(self, future: Future) -> None:
        goal_handle = future.result()
        if not goal_handle.accepted:
            self._logger.error("Undock 목표가 거부되었습니다.")
            return
        result_future = goal_handle.get_result_async()
        result_future.add_done_callback(self._on_undock_result)

    def _on_undock_result(self, future: Future) -> None:
        status = future.result().status
        if status != GoalStatus.STATUS_SUCCEEDED:
            self._logger.error(f"언도킹 실패 (status={status})")
            return

        self._logger.info("언도킹 완료.")
        self._on_resume()

    def shutdown(self) -> None:
        self._cancel_standby_timer()
