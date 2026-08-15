# DBcenter-PT-Bot

## **실행 순서**

### **⓪ (한 번만) 클린 시작 — DB 리셋 + 잔여 프로세스 정리**

아무 새 터미널에서:

```
cd /home/rokey/Desktop/DBcenter-PT-Bot/hmi/backend && bash scripts/demo.sh fresh
```

> 이전 순찰 미션이 DB에 남아 있으면 "통합시작"이 `이미 수행 중`으로 막혀서, 리셋으로 비워줍니다.
> 

### **① 터미널 1 — 백엔드 (ROS2 연동)**

```
cd /home/rokey/Desktop/DBcenter-PT-Bot/hmi/backend && bash scripts/demo.sh backend
```

> 로그에 `▶ 워크스페이스 source: .../src/install/setup.bash` 와 `[vision_bridge] 기동`이 뜨면 OK.
> 

### **② 터미널 2 — 비전 노드 (CCTV)**

```
cd /home/rokey/Desktop/DBcenter-PT-Bot && source /opt/ros/humble/setup.bash && source src/install/setup.bash && ros2 run vision_detection detect_cctv_node
```

> `다중 CCTV 준비 완료 ... /ui/start 대기 중` 이 뜨면 OK. **비전 노드는 딱 하나만** 띄우세요 (웹캠 중복 점유 금지).
> 

### **③ 터미널 3 — 로봇 노드 (amr_1, amr_2)**

```
cd /home/rokey/Desktop/DBcenter-PT-Bot/hmi/backend && bash scripts/demo.sh robot
```

### **④ 터미널 4 — 프론트엔드 (웹)**

```
cd /home/rokey/Desktop/DBcenter-PT-Bot/hmi/frontend && npm run dev
```

> `http://localhost:5175` 접속.
> 

### **⑤ 웹에서**

①~④가 다 뜬 걸 확인한 뒤 → 맵에서 waypoint 지정 → **▶ 통합 순찰 시작** 클릭 → CCTV 카드에 실시간 영상.

---

## **주의 3가지**

1. **각 터미널을 새로 여세요.** 열려면서 `🟡 [로컬 모드]` 메시지가 떠야 정상입니다. (안심용 확인: `echo $ROS_DISCOVERY_SERVER` → 아무것도 안 나와야 함. 만약 나오면 그 터미널에서 `unset ROS_DISCOVERY_SERVER && ros2 daemon stop && ros2 daemon start` 후 실행)
2. **비전 노드보다 "통합 순찰 시작"을 먼저 누르지 마세요.** `/ui/start`가 latch되지 않아, 순찰 시작 뒤에 뜬 비전 노드는 신호를 놓쳐 대기만 합니다. (②가 뜬 뒤에 ⑤를 누르면 됨. 순서가 꼬였으면 순찰 취소 후 다시 시작.)
3. **비전 노드는 하나만** — 두 개 띄우면 나중 것이 "웹캠 못 찾음"으로 죽습니다.
