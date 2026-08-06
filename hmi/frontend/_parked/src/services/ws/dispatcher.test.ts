/** dispatcher 테스트 — 메시지 타입별 라우팅 (F-02) + SNAPSHOT 초기화 (F-03). */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { dispatch, handledTypes } from "./dispatcher";
import type { WsEnvelope } from "./messages";
import { resetAllStores } from "../../store";
import { useConnectionStore } from "../../store/connectionStore";
import { useEventStore } from "../../store/eventStore";
import { useFacilityStore } from "../../store/facilityStore";
import { useLogStore } from "../../store/logStore";
import { useMissionStore } from "../../store/missionStore";
import { useRobotStore } from "../../store/robotStore";
import { useUiStore } from "../../store/uiStore";

function envelope<T>(type: string, payload: T): WsEnvelope {
  return { type: type as never, timestamp: "2026-08-06 12:00:00", payload };
}

const SNAPSHOT = {
  robots: [
    { robot_id: "amr_1", name: "로봇 1", state: "PATROLLING", state_ko: "순찰 중", battery: 76, online: true },
    { robot_id: "amr_2", name: "로봇 2", state: "IDLE", state_ko: "대기", battery: 91, online: true },
  ],
  missions: [{ mission_id: "MSN-1", robot_id: "amr_1", status: "RUNNING", mission_type: "PATROL" }],
  events: [
    { event_id: "EV-1", type: "SMOKE", severity: "WARN", status: "QUEUED", zone_id: "Z02", detected_at: "2026-08-06T12:00:00Z" },
  ],
  zones: [{ zone_id: "Z01", name: "배전반", polygon: [], risk_base: 3, camera_ids: [] }],
  nodes: [{ node_id: "N-001", name: "배전반 앞", zone_id: "Z01", x: 1, y: 2, theta: 0, is_blindspot: false, priority_score: 0.4 }],
  map: { map_id: "MAP-1", name: "1F", image_url: null, resolution: 0.05, origin: [0, 0, 0], width: 1024, height: 768 },
  suppression_mode: "MANUAL" as const,
};

beforeEach(() => {
  resetAllStores();
  useUiStore.setState({ toasts: [], criticalAlert: null, dismissedAlertIds: [] });
});

describe("SNAPSHOT (F-03)", () => {
  it("한 프레임으로 모든 store 를 채운다", () => {
    dispatch(envelope("SNAPSHOT", SNAPSHOT));

    expect(useRobotStore.getState().order).toEqual(["amr_1", "amr_2"]);
    expect(useMissionStore.getState().order).toEqual(["MSN-1"]);
    expect(useEventStore.getState().order).toEqual(["EV-1"]);
    expect(useFacilityStore.getState().zones).toHaveLength(1);
    expect(useFacilityStore.getState().nodes).toHaveLength(1);
    expect(useFacilityStore.getState().map?.map_id).toBe("MAP-1");
  });

  it("스냅샷 수신을 연결 store 에 표시한다 (스켈레톤 해제 기준)", () => {
    expect(useConnectionStore.getState().snapshotLoaded).toBe(false);
    dispatch(envelope("SNAPSHOT", SNAPSHOT));
    expect(useConnectionStore.getState().snapshotLoaded).toBe(true);
  });

  it("robots 가 배열이 아니면 무시한다 (가드)", () => {
    dispatch(envelope("SNAPSHOT", { robots: "이상한 값" }));
    expect(useRobotStore.getState().order).toHaveLength(0);
  });

  it("두 번 받아도 중복되지 않고 최신으로 대체된다", () => {
    dispatch(envelope("SNAPSHOT", SNAPSHOT));
    dispatch(envelope("SNAPSHOT", SNAPSHOT));
    expect(useRobotStore.getState().order).toHaveLength(2);
    expect(useEventStore.getState().order).toHaveLength(1);
  });
});

describe("로봇 메시지", () => {
  it("ROBOT_STATUS 를 store 에 반영한다", () => {
    dispatch(envelope("ROBOT_STATUS", { robot_id: "amr_1", state: "PATROLLING", battery: 55 }));
    expect(useRobotStore.getState().byId.amr_1.battery).toBe(55);
  });

  it("같은 값이 반복되면 객체 참조를 유지한다 (불필요한 리렌더 방지)", () => {
    const payload = { robot_id: "amr_1", state: "IDLE" as const, battery: 90 };
    dispatch(envelope("ROBOT_STATUS", payload));
    const first = useRobotStore.getState().byId.amr_1;
    dispatch(envelope("ROBOT_STATUS", payload));
    expect(useRobotStore.getState().byId.amr_1).toBe(first);
  });

  it("ROBOT_STATE_CHANGED 는 상태를 바꾸고 로그를 남긴다", () => {
    dispatch(envelope("ROBOT_STATUS", { robot_id: "amr_1", state: "IDLE" }));
    dispatch(envelope("ROBOT_STATE_CHANGED", { robot_id: "amr_1", from: "IDLE", to: "PATROLLING" }));

    expect(useRobotStore.getState().byId.amr_1.state).toBe("PATROLLING");
    expect(useLogStore.getState().entries[0].message).toContain("IDLE → PATROLLING");
  });

  it("EMERGENCY_STOP 전이는 CRITICAL 로그를 남긴다", () => {
    dispatch(envelope("ROBOT_STATUS", { robot_id: "amr_1", state: "PATROLLING" }));
    dispatch(
      envelope("ROBOT_STATE_CHANGED", { robot_id: "amr_1", from: "PATROLLING", to: "EMERGENCY_STOP" }),
    );
    expect(useLogStore.getState().entries[0].level).toBe("CRITICAL");
  });

  it("ROBOT_OFFLINE 은 오프라인 처리 + 경고 토스트", () => {
    dispatch(envelope("ROBOT_STATUS", { robot_id: "amr_1", state: "PATROLLING", online: true }));
    dispatch(envelope("ROBOT_OFFLINE", { robot_id: "amr_1", last_seen: "2026-08-06T12:00:00Z" }));

    expect(useRobotStore.getState().byId.amr_1.online).toBe(false);
    expect(useRobotStore.getState().byId.amr_1.state).toBe("OFFLINE");
    expect(useUiStore.getState().toasts[0].tone).toBe("warn");
  });
});

describe("이벤트 심각도 라우팅 (B-72)", () => {
  it("CRITICAL 은 전체 모달을 띄운다", () => {
    dispatch(
      envelope("EVENT", {
        event_id: "EV-9", type: "FIRE", severity: "CRITICAL", status: "QUEUED",
        zone_id: "Z01", confidence: 0.92,
      }),
    );
    expect(useUiStore.getState().criticalAlert?.eventId).toBe("EV-9");
    expect(useLogStore.getState().entries[0].level).toBe("CRITICAL");
  });

  it("WARN 은 토스트만 띄운다", () => {
    dispatch(
      envelope("EVENT", { event_id: "EV-8", type: "LEAK", severity: "WARN", status: "QUEUED", zone_id: "Z03" }),
    );
    expect(useUiStore.getState().criticalAlert).toBeNull();
    expect(useUiStore.getState().toasts).toHaveLength(1);
  });

  it("INFO 는 로그만 남긴다", () => {
    dispatch(
      envelope("EVENT", { event_id: "EV-7", type: "PERSON", severity: "INFO", status: "QUEUED" }),
    );
    expect(useUiStore.getState().toasts).toHaveLength(0);
    expect(useUiStore.getState().criticalAlert).toBeNull();
    expect(useLogStore.getState().entries).toHaveLength(1);
  });

  it("같은 이벤트가 갱신되면 모달을 다시 띄우지 않는다", () => {
    const payload = { event_id: "EV-9", type: "FIRE", severity: "CRITICAL", status: "QUEUED", zone_id: "Z01" };
    dispatch(envelope("EVENT", payload));
    useUiStore.getState().dismissCriticalAlert();
    dispatch(envelope("EVENT", { ...payload, status: "ASSIGNED" }));
    expect(useUiStore.getState().criticalAlert).toBeNull();
  });

  it("종결된 이벤트는 경보를 띄우지 않는다", () => {
    dispatch(
      envelope("EVENT", { event_id: "EV-6", type: "FIRE", severity: "CRITICAL", status: "RESOLVED" }),
    );
    expect(useUiStore.getState().criticalAlert).toBeNull();
    expect(useUiStore.getState().toasts).toHaveLength(0);
  });
});

describe("기타 메시지", () => {
  it("DETECTION 은 카메라별 최신 프레임만 유지한다", () => {
    dispatch(envelope("DETECTION", { source: "cctv", camera_id: "CAM-01", boxes: [] }));
    dispatch(
      envelope("DETECTION", {
        source: "cctv", camera_id: "CAM-01",
        boxes: [{ label: "smoke", conf: 0.9, bbox: [0, 0, 10, 10] }],
      }),
    );
    const detections = useFacilityStore.getState().detections;
    expect(Object.keys(detections)).toEqual(["CAM-01"]);
    expect(detections["CAM-01"].boxes).toHaveLength(1);
  });

  it("ALIGN_RESULT MISMATCH 는 토스트를 띄운다", () => {
    dispatch(
      envelope("ALIGN_RESULT", {
        event_id: null, equipment_id: "EQ-BRK-01", observed_state: "ON",
        normal_state: "OFF", expected_state: "OFF", verdict: "MISMATCH",
        severity: "CRITICAL", reason: "작업 중 차단기 ON",
      }),
    );
    expect(useUiStore.getState().toasts[0].tone).toBe("danger");
  });

  it("ALIGN_RESULT OK 는 로그만 남긴다", () => {
    dispatch(
      envelope("ALIGN_RESULT", {
        event_id: null, equipment_id: "EQ-BRK-01", observed_state: "ON",
        normal_state: "ON", expected_state: "ON", verdict: "OK", severity: "INFO",
      }),
    );
    expect(useUiStore.getState().toasts).toHaveLength(0);
  });

  it("PRIORITY_UPDATED 는 노드 점수를 갱신한다", () => {
    dispatch(envelope("SNAPSHOT", SNAPSHOT));
    dispatch(envelope("PRIORITY_UPDATED", [{ node_id: "N-001", score: 0.86, rank: 1, visit_multiplier: 3 }]));
    expect(useFacilityStore.getState().nodes[0].priority_score).toBe(0.86);
  });

  it("SUPPRESSION_STATUS BLOCKED 는 닫히지 않는 토스트를 띄운다", () => {
    dispatch(
      envelope("SUPPRESSION_STATUS", {
        suppression_id: "SUP-0001", zone_id: "Z01", status: "BLOCKED",
        interlock: { passed: false, blockers: [{ code: "AMR_IN_ZONE", detail: "amr_2 대피 중" }] },
      }),
    );
    const toast = useUiStore.getState().toasts[0];
    expect(toast.tone).toBe("danger");
    expect(toast.duration).toBe(0);
    expect(toast.description).toContain("amr_2");
  });

  it("LOG 는 로그 store 에 그대로 쌓인다", () => {
    dispatch(envelope("LOG", { level: "INFO", actor: "로봇 1", message: "동작 재개 선택" }));
    expect(useLogStore.getState().entries[0]).toMatchObject({
      actor: "로봇 1",
      message: "동작 재개 선택",
    });
  });

  it("SUBSCRIBED ACK 를 연결 store 에 기록한다", () => {
    dispatch(envelope("SUBSCRIBED", { topics: ["EVENT", "LOG"] }));
    expect(useConnectionStore.getState().subscribedTopics).toEqual(["EVENT", "LOG"]);
  });
});

describe("견고성 (F-02)", () => {
  it("모르는 타입은 아무 것도 하지 않는다", () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => undefined);
    dispatch(envelope("PARTY_MODE", { anything: true }));
    expect(errorSpy).not.toHaveBeenCalled();
    expect(useLogStore.getState().entries).toHaveLength(0);
  });

  it("필수 필드가 빠진 페이로드는 store 에 넣지 않는다", () => {
    dispatch(envelope("ROBOT_STATUS", { battery: 50 })); // robot_id 없음
    dispatch(envelope("EVENT", { type: "FIRE" })); // event_id 없음
    expect(useRobotStore.getState().order).toHaveLength(0);
    expect(useEventStore.getState().order).toHaveLength(0);
  });

  it("어떤 프레임에도 예외를 밖으로 던지지 않는다", () => {
    const frames = [
      envelope("SNAPSHOT", null),
      envelope("EVENT", undefined),
      envelope("PRIORITY_UPDATED", "배열 아님"),
      envelope("LOG", { level: "INFO" }),
    ];
    for (const frame of frames) {
      expect(() => dispatch(frame)).not.toThrow();
    }
  });

  it("모든 프레임 수신 시각을 연결 store 에 남긴다", () => {
    dispatch(envelope("PARTY_MODE", {}));
    expect(useConnectionStore.getState().lastMessageAt).not.toBeNull();
  });

  it("명세서 §9-3 의 주요 타입을 모두 처리한다", () => {
    const handled = handledTypes();
    for (const type of [
      "SNAPSHOT", "ROBOT_STATUS", "ROBOT_STATE_CHANGED", "ROBOT_OFFLINE",
      "MISSION_STATUS", "EVENT", "DETECTION", "ALIGN_RESULT", "POSE_CORRECTED",
      "PRIORITY_UPDATED", "SUPPRESSION_STATUS", "LOG", "SYSTEM_ALERT",
    ]) {
      expect(handled).toContain(type);
    }
  });
});
