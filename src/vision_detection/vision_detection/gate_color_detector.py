import cv2
import numpy as np


class GateColorDetector:
    """차단기의 원형 상태 표시등을 찾아 색상으로 열림/닫힘을 판별하는 순수 CV 로직.
    ROS에 의존하지 않는 클래스라서 ROS 없이도(numpy 이미지만으로) 단독 테스트가 가능하다.
    1) HoughCircles로 원형 인디케이터 위치를 찾고
    2) 그 원 내부 픽셀을 HSV로 변환해 '닫힘 색상'/'열림 색상' 비율을 비교해 판정한다.
    """

    def __init__(self, hough_params, closed_hsv_ranges, open_hsv_ranges, min_color_ratio=0.3):
        self.dp = hough_params['dp']
        self.min_dist = hough_params['min_dist']
        self.param1 = hough_params['param1']
        self.param2 = hough_params['param2']
        self.min_radius = hough_params['min_radius']
        self.max_radius = hough_params['max_radius']
        # 각 range는 (lower_hsv, upper_hsv). 색상 하나가 범위 2개를 필요로 할 수도 있음(예: 빨강은 hue 0/180 양쪽)
        self.closed_hsv_ranges = closed_hsv_ranges
        self.open_hsv_ranges = open_hsv_ranges
        self.min_color_ratio = min_color_ratio

    def detect(self, cv_image, draw=True):
        """(주석 그려진 이미지 또는 draw=False면 None, gate_closed)를 반환.
        원을 못 찾으면 gate_closed는 None. draw=False는 판정 결과 이미지를 안 쓰는
        호출부(detect_station_node)를 위한 것 - HoughCircles/HSV 판정과 무관한
        cv2.circle/putText 그리기 비용을 아낀다.
        """
        gray = cv2.cvtColor(cv_image, cv2.COLOR_BGR2GRAY)
        blurred = cv2.medianBlur(gray, 5)
        circles = cv2.HoughCircles(
            blurred, cv2.HOUGH_GRADIENT, dp=self.dp, minDist=self.min_dist,
            param1=self.param1, param2=self.param2,
            minRadius=self.min_radius, maxRadius=self.max_radius)

        if circles is None:
            return (cv_image.copy() if draw else None), None

        hsv = cv2.cvtColor(cv_image, cv2.COLOR_BGR2HSV)
        # 원이 여러 개 검출되는 경우, 가장 큰(카메라와 가장 가까운) 원을 인디케이터로 가정
        x, y, r = max(np.round(circles[0]).astype(int), key=lambda c: c[2])

        mask = np.zeros(hsv.shape[:2], dtype=np.uint8)
        cv2.circle(mask, (x, y), r, 255, -1)

        closed_ratio = self._color_ratio(hsv, mask, self.closed_hsv_ranges)
        open_ratio = self._color_ratio(hsv, mask, self.open_hsv_ranges)
        gate_closed = closed_ratio >= self.min_color_ratio and closed_ratio >= open_ratio

        if not draw:
            return None, gate_closed

        # 판정 결과를 원 + 텍스트로 그려서 디버깅/모니터링용 이미지로 사용
        annotated = cv_image.copy()
        label = f'CLOSED {closed_ratio:.2f}' if gate_closed else f'OPEN {open_ratio:.2f}'
        color = (0, 0, 255) if gate_closed else (0, 255, 0)
        cv2.circle(annotated, (x, y), r, color, 3)
        cv2.putText(annotated, label, (max(x - r, 0), max(y - r - 10, 0)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)

        return annotated, gate_closed

    @staticmethod
    def _color_ratio(hsv, mask, ranges):
        """원(mask) 내부 픽셀 중 주어진 HSV 색상 범위(ranges)에 속하는 비율을 계산."""
        matched = np.zeros(mask.shape, dtype=np.uint8)
        for lower, upper in ranges:
            matched |= cv2.inRange(hsv, np.array(lower), np.array(upper))
        matched &= mask

        circle_area = cv2.countNonZero(mask)
        if circle_area == 0:
            return 0.0
        return cv2.countNonZero(matched) / circle_area
