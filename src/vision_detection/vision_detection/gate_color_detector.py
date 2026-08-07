import cv2
import numpy as np


class GateColorDetector:
    """Locates a circular gate indicator light with HoughCircles and classifies
    its on/off state by sampling the dominant color inside the circle (HSV)."""

    def __init__(self, hough_params, closed_hsv_ranges, open_hsv_ranges, min_color_ratio=0.3):
        self.dp = hough_params['dp']
        self.min_dist = hough_params['min_dist']
        self.param1 = hough_params['param1']
        self.param2 = hough_params['param2']
        self.min_radius = hough_params['min_radius']
        self.max_radius = hough_params['max_radius']
        # each range is (lower_hsv, upper_hsv); a color can need two ranges (e.g. red wraps at hue 0/180)
        self.closed_hsv_ranges = closed_hsv_ranges
        self.open_hsv_ranges = open_hsv_ranges
        self.min_color_ratio = min_color_ratio

    def detect(self, cv_image):
        """Returns (annotated_image, gate_closed). gate_closed is None if no
        indicator circle could be located."""
        gray = cv2.cvtColor(cv_image, cv2.COLOR_BGR2GRAY)
        blurred = cv2.medianBlur(gray, 5)
        circles = cv2.HoughCircles(
            blurred, cv2.HOUGH_GRADIENT, dp=self.dp, minDist=self.min_dist,
            param1=self.param1, param2=self.param2,
            minRadius=self.min_radius, maxRadius=self.max_radius)

        annotated = cv_image.copy()
        if circles is None:
            return annotated, None

        hsv = cv2.cvtColor(cv_image, cv2.COLOR_BGR2HSV)
        # HoughCircles can return several candidates; the largest is assumed to
        # be the indicator light closest to the camera.
        x, y, r = max(np.round(circles[0]).astype(int), key=lambda c: c[2])

        mask = np.zeros(hsv.shape[:2], dtype=np.uint8)
        cv2.circle(mask, (x, y), r, 255, -1)

        closed_ratio = self._color_ratio(hsv, mask, self.closed_hsv_ranges)
        open_ratio = self._color_ratio(hsv, mask, self.open_hsv_ranges)
        gate_closed = closed_ratio >= self.min_color_ratio and closed_ratio >= open_ratio

        label = f'CLOSED {closed_ratio:.2f}' if gate_closed else f'OPEN {open_ratio:.2f}'
        color = (0, 0, 255) if gate_closed else (0, 255, 0)
        cv2.circle(annotated, (x, y), r, color, 3)
        cv2.putText(annotated, label, (max(x - r, 0), max(y - r - 10, 0)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)

        return annotated, gate_closed

    @staticmethod
    def _color_ratio(hsv, mask, ranges):
        matched = np.zeros(mask.shape, dtype=np.uint8)
        for lower, upper in ranges:
            matched |= cv2.inRange(hsv, np.array(lower), np.array(upper))
        matched &= mask

        circle_area = cv2.countNonZero(mask)
        if circle_area == 0:
            return 0.0
        return cv2.countNonZero(matched) / circle_area
