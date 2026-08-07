#!/usr/bin/env python3

from __future__ import annotations

from typing import List, Optional

import rclpy
from geometry_msgs.msg import Point
from rclpy.node import Node

from patrol.run_patrol import PatrolController
from patrol.stop_patrol import StopController


class PatrolNode(Node):
    """Fleet/Nav2 등 외부 노드와의 통신(topic/service/action)만 담당하는 노드.

    순찰 시퀀싱(run_patrol.PatrolController)과 도킹/대기/언도킹
    (stop_patrol.StopController)은 patrol 자신의 내부 동작이라 각각의
    모듈에 위임하고, 이 노드는 둘을 엮어주는 배선(wiring)과 Fleet/Nav2처럼
    진짜 외부 시스템과의 정보 송수신만 처리한다.

    현재 단계 구현 범위:
      - Fleet 리스트 수신(receive_patrol_route), 이상 신호 수신
        (receive_abort_signal), 복귀 신호 수신(receive_resume_signal)은
        진입점만 마련하고 실제 구독/서비스/액션 방식은 Fleet 인터페이스
        확정 후 연동 예정(TODO).
      - Nav2 이동(_send_navigate_goal)은 NavigateToPose 연동 전이라 스텁.
      - Dock/Undock/대기 타이머는 StopController를 통해 실제로 동작한다.
    """

    def __init__(self) -> None:
        super().__init__("patrol_node")

        self.declare_parameter("robot_id", 1)
        self.declare_parameter("standby_duration_sec", 10.0)

        robot_id = int(self.get_parameter("robot_id").value)
        standby_duration_sec = float(
            self.get_parameter("standby_duration_sec").value
        )

        # StopController를 먼저 만들어야 PatrolController에 바로 넘길 수 있다.
        # StopController의 on_resume은 지연 평가(lambda)라 아직 존재하지
        # 않는 self.controller를 미리 참조해도 문제없다 (호출 시점엔 존재).
        self.stop_controller = StopController(
            node=self,
            robot_id=robot_id,
            standby_duration_sec=standby_duration_sec,
            on_resume=lambda: self.controller.resume_from_start(),
            logger=self.get_logger(),
        )
        self.controller = PatrolController(
            navigate_fn=self._send_navigate_goal,
            enter_standby_fn=self.stop_controller.enter_standby,
            logger=self.get_logger(),
        )

        # TODO: Fleet 인터페이스(patrol_points: geometry_msgs/Point[],
        # point_type: string)가 확정되면 여기서 구독/서비스/액션 서버를
        # 생성하고, 콜백에서 self.controller.receive_patrol_route(...)를
        # 호출하면 된다.
        # TODO: 비전 이상 감지 -> UI -> Fleet 이상 신호 구독 -> receive_abort_signal(),
        # 복귀 신호 구독 -> receive_resume_signal() 도 같은 방식으로 연결 예정.
        # TODO: Fleet로 순찰 종료 신호를 보낼 퍼블리셔/서비스/액션도 여기서 생성.

        # TODO: Nav2 NavigateToPose 액션 클라이언트도 여기에 추가 예정.

        self.get_logger().info(
            f"patrol_node 준비 완료 | robot_id={robot_id} | "
            f"dock_action={self.stop_controller.dock_action_name} | "
            f"undock_action={self.stop_controller.undock_action_name} | "
            f"standby={standby_duration_sec:.1f}s"
        )

    # ------------------------------------------------------------------
    # Fleet 통신 진입점 (임시: 실제 구독/액션 확정 전까지 직접 호출용)
    # ------------------------------------------------------------------
    def receive_patrol_route(
        self, patrol_points: List[Point], point_type: str = ""
    ) -> None:
        self.controller.receive_patrol_route(patrol_points, point_type)

    def receive_abort_signal(self) -> None:
        """비전 이상 감지 -> UI -> Fleet를 거쳐 전달된 순찰 중단 신호.

        컨트롤러가 현재 지점을 save_way_point에 남기고 순찰을 중단한 뒤,
        그 결과를 Fleet에 종료 신호로 발행한다.
        """
        self.controller.handle_abort_signal()
        self._publish_patrol_stopped()

    def receive_resume_signal(self) -> None:
        """이상 상황 해제 후 Fleet가 보내는 순찰 재개 신호.

        저장해둔 지점(save_way_point)부터 순찰을 이어간다. 리스트 소진 후
        도킹 대기 재개(resume_from_start, 처음부터)와는 다른 경로다.
        """
        self.controller.resume_from_saved_point()

    def _publish_patrol_stopped(self) -> None:
        self.get_logger().info("[TODO] Fleet로 순찰 종료 신호 발행 예정")
        # TODO: Fleet 발행용 퍼블리셔/서비스/액션이 확정되면 여기서 실제 발행.

    # ------------------------------------------------------------------
    # Nav2 통신 (TODO: 실제 NavigateToPose 액션 연동 예정)
    # ------------------------------------------------------------------
    def _send_navigate_goal(self, point: Point) -> None:
        self.get_logger().info(
            "[TODO] Nav2 연동 예정 - 다음 순찰 지점으로 이동: "
            f"(x={point.x:.2f}, y={point.y:.2f}, z={point.z:.2f})"
        )
        # TODO: NavigateToPose 결과 콜백에서
        # self.controller.on_navigate_result(성공여부)를 호출해야
        # 다음 지점으로 순찰이 이어진다.

    def destroy_node(self) -> bool:
        self.stop_controller.shutdown()
        return super().destroy_node()


def main(args: Optional[list] = None) -> None:
    rclpy.init(args=args)
    node: Optional[PatrolNode] = None

    try:
        node = PatrolNode()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        if node is not None:
            node.get_logger().fatal(str(exc))
        else:
            print(f"patrol_node 시작 실패: {exc}")
        raise
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
