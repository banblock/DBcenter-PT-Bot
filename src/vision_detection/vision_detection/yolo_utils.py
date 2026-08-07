from cv_bridge import CvBridge
from ultralytics import YOLO


class YoloDetector:
    """ROS2 노드에서 쓰기 편하도록 ultralytics YOLO 모델을 감싼 래퍼.

    sensor_msgs/Image <-> OpenCV 이미지 변환(cv_bridge)까지 함께 처리해준다.
    """

    def __init__(self, model_path, conf_threshold=0.5):
        self.model = YOLO(model_path)
        self.conf_threshold = conf_threshold
        self.bridge = CvBridge()

    def infer_from_msg(self, image_msg):
        """sensor_msgs/Image를 받아 추론하고 (박스가 그려진 이미지, 탐지 결과 목록)을 반환.

        detections는 {class_name, confidence, xyxy} 딕셔너리의 리스트.
        """
        cv_image = self.bridge.imgmsg_to_cv2(image_msg, desired_encoding='bgr8')
        result = self.model.predict(cv_image, conf=self.conf_threshold, verbose=False)[0]

        detections = []
        for box in result.boxes:
            class_id = int(box.cls[0])
            detections.append({
                'class_name': result.names[class_id],
                'confidence': float(box.conf[0]),
                'xyxy': [float(v) for v in box.xyxy[0]],
            })

        annotated_image = result.plot()  # 탐지 박스가 그려진 이미지
        return annotated_image, detections

    def to_image_msg(self, cv_image, frame_id=''):
        msg = self.bridge.cv2_to_imgmsg(cv_image, encoding='bgr8')
        msg.header.frame_id = frame_id
        return msg
