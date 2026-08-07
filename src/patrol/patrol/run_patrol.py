#!/usr/bin/env python3

from __future__ import annotations

from typing import Callable, List, Optional

from geometry_msgs.msg import Point


class PatrolController:
    """순찰 시퀀싱(다음 지점이 있는가?)만 담당하는 순수 제어 로직.

    Dock/Undock/대기(Timer) 같은 "정지" 관련 동작은 stop_patrol.py의
    StopController가 전담한다. 이 클래스는 리스트가 소진되면
    enter_standby_fn()을 호출해 넘기고, StopController가 대기 해제 후
    resume_from_start()를 호출해주면 처음 지점부터 다시 순찰을 시작한다.

    rclpy에 의존하지 않으므로 ROS 없이 단독으로 테스트할 수 있다.
    """

    def __init__(
        self,
        navigate_fn: Callable[[Point], None],
        enter_standby_fn: Callable[[], None],
        logger,
    ) -> None:
        self._navigate_fn = navigate_fn
        self._enter_standby_fn = enter_standby_fn
        self._logger = logger

        # Fleet로부터 받은 전체 순찰 경로(마스터 리스트, 재순찰 시 재사용)
        self.patrol_points: List[Point] = []
        self.point_type: str = ""
        # 현재 라운드에서 아직 방문하지 않은 지점 큐 (앞에서부터 pop)
        self.save_way_point: List[Point] = []
        # 지금 막 navigate_fn으로 보낸(Nav2 도착 확인 전) 지점.
        # 중단 신호가 오면 도착 여부를 알 수 없으므로 재시도 대상으로 남겨둔다.
        self._current_point: Optional[Point] = None

        self.is_patrolling = False

    # ------------------------------------------------------------------
    # Fleet 순찰 리스트 수신 (patrol_node.py가 통신으로 받아서 호출해준다)
    # ------------------------------------------------------------------
    def receive_patrol_route(
        self, patrol_points: List[Point], point_type: str = ""
    ) -> None:
        if not patrol_points:
            self._logger.warn("빈 순찰 리스트를 수신했습니다.")
            return

        self.patrol_points = list(patrol_points)
        self.point_type = point_type
        self.save_way_point = list(patrol_points)
        self.is_patrolling = True

        self._logger.info(
            f"순찰 리스트 수신 완료 - 총 {len(self.patrol_points)}개 지점 "
            f"(point_type={self.point_type!r})"
        )
        self._advance_patrol()

    # ------------------------------------------------------------------
    # 핵심 분기: 다음 순찰 지점이 있는가?
    # ------------------------------------------------------------------
    def _advance_patrol(self) -> None:
        next_point = self._pop_next_waypoint()
        if next_point is not None:
            self._current_point = next_point
            self._navigate_fn(next_point)
            return

        self._current_point = None
        self._logger.info("순찰 지점 소진 - 대기 상태로 전환합니다.")
        self.is_patrolling = False
        self._enter_standby_fn()

    def _pop_next_waypoint(self) -> Optional[Point]:
        if not self.save_way_point:
            return None
        return self.save_way_point.pop(0)

    def on_navigate_result(self, success: bool) -> None:
        """Nav2 이동 완료 결과를 patrol_node.py로부터 전달받는다.

        TODO(Nav2): 실제 NavigateToPose 연동 시, 목표 도달 결과 콜백에서
        이 메서드를 호출해야 다음 지점으로 순찰이 이어진다. 중단(abort)
        신호 처리를 위해 진행 중인 goal을 취소하는 로직도 함께 필요하다.
        """
        if not success:
            self._logger.error("Nav2 이동 실패 - 현재 지점 재시도 로직 필요 (TODO)")
            return
        self._current_point = None
        self._advance_patrol()

    # ------------------------------------------------------------------
    # StopController가 대기 해제(언도킹 완료) 후 호출하는 재개 진입점
    # (리스트 소진 -> 도킹 -> 1시간 대기 흐름 전용, 처음부터 재개)
    # ------------------------------------------------------------------
    def resume_from_start(self) -> None:
        self._logger.info("순찰을 재개합니다.")
        self.is_patrolling = True
        # 저장해둔 전체 경로(patrol_points)를 다시 큐에 채워 첫 지점부터 재개.
        self.save_way_point = list(self.patrol_points)
        self._advance_patrol()

    # ------------------------------------------------------------------
    # 이상 신호(Fleet 경유) 수신 -> 순찰 중단 -> 재개 지점 저장
    # ------------------------------------------------------------------
    def handle_abort_signal(self) -> None:
        """비전 이상 감지 등으로 Fleet가 보낸 중단 신호를 처리한다.

        순찰 중이 아니면(이미 대기 상태 등) 할 일이 없다. 순찰 중이었다면
        지금 이동 중이던 지점을 save_way_point 맨 앞에 되돌려 넣어, 나중에
        복귀 신호를 받았을 때 그 자리부터 이어갈 수 있게 남겨두고 중단한다.
        도킹/대기(StopController)는 여기서 트리거하지 않는다 - 이 경로는
        리스트 소진에 의한 정상 종료와는 별개의 비상 중단 경로다.
        """
        if not self.is_patrolling:
            self._logger.info("순찰 중이 아니므로 중단할 것이 없습니다.")
            return

        if self._current_point is not None:
            self.save_way_point.insert(0, self._current_point)
            self._current_point = None

        self.is_patrolling = False
        self._logger.warn(
            "이상 신호 수신 - 순찰을 중단합니다. "
            f"재개 대기 지점 {len(self.save_way_point)}개 저장됨."
        )

    # ------------------------------------------------------------------
    # 복귀 신호(Fleet 경유) 수신 -> 저장해둔 지점부터 순찰 재개
    # ------------------------------------------------------------------
    def resume_from_saved_point(self) -> None:
        if not self.save_way_point:
            self._logger.warn("저장된 재개 지점이 없어 순찰을 재개할 수 없습니다.")
            return

        self._logger.info(
            f"저장된 지점부터 순찰을 재개합니다 (남은 지점 {len(self.save_way_point)}개)."
        )
        self.is_patrolling = True
        self._advance_patrol()
