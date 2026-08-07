from cv_bridge import CvBridge
from ultralytics import YOLO


class YoloDetector:
    """Thin wrapper around an ultralytics YOLO model for use inside ROS2 nodes."""

    def __init__(self, model_path, conf_threshold=0.5):
        self.model = YOLO(model_path)
        self.conf_threshold = conf_threshold
        self.bridge = CvBridge()

    def infer_from_msg(self, image_msg):
        """Run inference on a sensor_msgs/Image and return (annotated_cv_image, detections).

        detections is a list of dicts: {class_name, confidence, xyxy}
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

        annotated_image = result.plot()
        return annotated_image, detections

    def to_image_msg(self, cv_image, frame_id=''):
        msg = self.bridge.cv2_to_imgmsg(cv_image, encoding='bgr8')
        msg.header.frame_id = frame_id
        return msg
