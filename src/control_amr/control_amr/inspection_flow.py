"""Gate and anomaly inspection scaffolding for the AMR Control Node."""


class InspectionFlowSupport:
    """Hooks for camera/vision based inspection flows."""

    def __init__(self, namespace, navigator):
        self.namespace = namespace
        self.navigator = navigator

    def inspect_gate(self):
        # TODO: camera/vision node에 게이트 또는 차단기 상태 확인을 요청한다.
        # TODO: 열림/닫힘/인식 실패 결과를 구분하고 Fleet Node에 보고한다.
        # TODO: 닫힘 또는 위험 상태면 대기, 우회, mission 실패 처리 정책을 적용한다.
        return True

    def wait_for_anomaly_arrival(self):
        # TODO: anomaly 위치로 이동 명령을 보낸 뒤 실제 도착 완료까지 대기한다.
        # TODO: 이동 실패, timeout, 추가 anomaly 신호를 처리한다.
        return True

    def inspect_anomaly(self, location):
        # TODO: camera/vision node로 anomaly 실제 여부와 종류를 확인한다.
        # TODO: 점검 이미지, anomaly type, confidence, 처리 결과를 Fleet Node에 보고한다.
        # TODO: 이상 없음/이상 확인/인식 실패별 후속 동작을 정의한다.
        return {'confirmed': None, 'location': location}
