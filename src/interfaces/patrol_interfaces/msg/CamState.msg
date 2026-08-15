# 카메라 이상 상태 — 감지/해제 "변화 시"에만 발행 (detect_cctv_node, detect_ambient_node)
int32 camera_id	#0=cctv1, 1=cctv2, 2=robot3cam, 3=robot8cam
int32 state     #FIRE=0 SMOKE=1 COOLANT=2

# 감지 객체 bounding box — 이미지 픽셀 좌표(좌상단 x1,y1 / 우하단 x2,y2).
# 백엔드가 호모그래피(픽셀→맵)로 로봇 급파 좌표를 계산하는 입력.
# 감지 없음/해제/미설정 시 -1 또는 0 → 백엔드는 x2<=x1 이면 무효 bbox 로 처리(좌표 미기입).
float32 bbox_x1
float32 bbox_y1
float32 bbox_x2
float32 bbox_y2
float32 confidence   #감지 신뢰도 0~1 (해제/미설정 시 0)
