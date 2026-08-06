/**
 * 메시지 타입별 dispatcher (F-02).
 *
 * ## 계약
 * 1. **정의되지 않은 타입은 조용히 무시한다.** 예외를 던지지 않고 콘솔 에러도
 *    남기지 않는다 (체크리스트 테스트: "정의 외 메시지 타입 무시 · 콘솔 에러 0").
 *    서버가 새 타입을 추가해도 구버전 프론트가 죽지 않아야 한다.
 * 2. **핸들러 하나가 터져도 나머지는 계속 돈다.** 한 프레임의 버그가 관제 화면
 *    전체를 멈추게 두지 않는다.
 * 3. 페이로드는 런타임 가드를 통과한 것만 store 에 넣는다.
 *
 * dev 모드에서는 무시된 타입을 `console.debug` 로만 남긴다 — 에러가 아니다.
 */

import { useConnectionStore } from "../../store/connectionStore";
import { useEventStore, CLOSED_STATUSES } from "../../store/eventStore";
import { useFacilityStore } from "../../store/facilityStore";
import { logActions, useLogStore } from "../../store/logStore";
import { useMissionStore } from "../../store/missionStore";
import { useRobotStore } from "../../store/robotStore";
import { useUiStore } from "../../store/uiStore";
import {
  isValidPayload,
  type EventPayload,
  type WsEnvelope,
  type WsMessageType,
  type WsPayloadMap,
} from "./messages";

type Handler<K extends WsMessageType> = (payload: WsPayloadMap[K], timestamp: string) => void;

type HandlerMap = { [K in WsMessageType]?: Handler<K> };

const EVENT_TYPE_KO: Record<string, string> = {
  FIRE: "화재",
  SMOKE: "연기",
  LEAK: "누수",
  PERSON: "인원 감지",
  INTRUSION: "침입",
  BREAKER_ABNORMAL: "차단기 이상",
  LOCK_ABNORMAL: "잠금장치 이상",
  PANEL_OUT_OF_RANGE: "패널 수치 이탈",
  ALIGN_MISMATCH: "대조 불일치",
};

function describeEvent(event: EventPayload): string {
  const type = EVENT_TYPE_KO[event.type] ?? event.type;
  return event.zone_id ? `${event.zone_id} ${type}` : type;
}

const handlers: HandlerMap = {
  // ── 초기 스냅샷 (F-03) — 이 한 프레임으로 화면 전체를 다시 그린다 ──────
  SNAPSHOT: (payload) => {
    useRobotStore.getState().upsertMany(payload.robots ?? []);
    useMissionStore.getState().upsertMany(payload.missions ?? []);
    useEventStore.getState().upsertMany(payload.events ?? []);
    useFacilityStore.getState().setSnapshot({
      map: payload.map ?? null,
      zones: payload.zones ?? [],
      nodes: payload.nodes ?? [],
      suppression_mode: payload.suppression_mode,
    });
    useConnectionStore.getState().markSnapshotLoaded();
    logActions.info("PC1", "관제 스냅샷 수신 — 화면 초기화");
  },

  // ── 로봇 ────────────────────────────────────────────────────────────────
  ROBOT_STATUS: (payload) => {
    useRobotStore.getState().upsert(payload);
  },

  ROBOT_STATE_CHANGED: (payload) => {
    useRobotStore.getState().applyStateChange(payload.robot_id, payload.from, payload.to);
    const reason = payload.reason ? ` (${payload.reason})` : "";
    const level = payload.to === "EMERGENCY_STOP" || payload.to === "ERROR" ? "critical" : "info";
    logActions[level](payload.robot_id, `상태 전이 ${payload.from} → ${payload.to}${reason}`);
  },

  ROBOT_OFFLINE: (payload) => {
    useRobotStore.getState().markOffline(payload.robot_id, payload.last_seen);
    logActions.warn(payload.robot_id, "heartbeat 끊김 — OFFLINE 처리");
    useUiStore.getState().pushToast({
      tone: "warn",
      title: `${payload.robot_id} 연결 끊김`,
      description: "heartbeat 미수신으로 오프라인 처리되었습니다",
    });
  },

  // ── 미션 ────────────────────────────────────────────────────────────────
  MISSION_STATUS: (payload) => {
    useMissionStore.getState().upsert(payload);
    if (payload.robot_id) {
      logActions.info(payload.robot_id, `미션 ${payload.mission_id} · ${payload.status}`);
    }
  },

  // ── 이벤트 ──────────────────────────────────────────────────────────────
  EVENT: (payload) => {
    const { isNew } = useEventStore.getState().upsert(payload);
    const ui = useUiStore.getState();
    const label = describeEvent(payload);

    if (CLOSED_STATUSES.has(payload.status)) {
      logActions.info("PC1", `${payload.event_id} ${label} · ${payload.status}`, payload.event_id);
      return;
    }

    // 심각도별 라우팅 (B-72): INFO=로그만 / WARN=토스트 / CRITICAL=모달
    if (payload.severity === "CRITICAL") {
      logActions.critical("PC2", `${label} 감지 (신뢰도 ${payload.confidence ?? 0})`, payload.event_id);
      if (isNew) {
        ui.showCriticalAlert({
          eventId: payload.event_id,
          title: `${label} 감지`,
          description: `신뢰도 ${((payload.confidence ?? 0) * 100).toFixed(0)}% · ${payload.node_id ?? "위치 미상"}`,
          zoneId: payload.zone_id,
          thumbnailUrl: payload.thumbnail_url,
          confidence: payload.confidence,
          at: Date.now(),
        });
      }
    } else if (payload.severity === "WARN") {
      logActions.warn("PC2", `${label} 감지`, payload.event_id);
      if (isNew) {
        ui.pushToast({ tone: "warn", title: `${label} 감지`, description: payload.event_id });
      }
    } else {
      logActions.info("PC2", `${label} 감지`, payload.event_id);
    }
  },

  // ── 비전 / 대조 / 보정 ──────────────────────────────────────────────────
  DETECTION: (payload) => {
    useFacilityStore.getState().setDetection(payload);
  },

  ALIGN_RESULT: (payload) => {
    const level = payload.verdict === "MISMATCH" ? "warn" : "info";
    logActions[level](
      "PC2",
      `${payload.equipment_id} 대조 ${payload.verdict}${payload.reason ? ` — ${payload.reason}` : ""}`,
      payload.event_id ?? undefined,
    );
    if (payload.verdict === "MISMATCH") {
      useUiStore.getState().pushToast({
        tone: payload.severity === "CRITICAL" ? "danger" : "warn",
        title: `${payload.equipment_id} 불일치`,
        description: payload.reason ?? undefined,
      });
    }
  },

  POSE_CORRECTED: (payload) => {
    // 보정 오차가 크면 위치 신뢰도가 떨어진 것 — 조용히 넘기면 안 된다
    const level = payload.error_m > 0.3 ? "warn" : "info";
    logActions[level](
      payload.robot_id,
      `ArUco #${payload.marker_id} 위치 보정 · 오차 ${payload.error_m.toFixed(2)}m`,
    );
  },

  PRIORITY_UPDATED: (payload) => {
    useFacilityStore.getState().setPriorities(payload);
    logActions.info("Fleet", `순찰 우선순위 재계산 — ${payload.length}개 노드`);
  },

  // ── 진압 ────────────────────────────────────────────────────────────────
  SUPPRESSION_STATUS: (payload) => {
    useFacilityStore.getState().setSuppression(payload);
    const blocked = payload.status === "BLOCKED";
    logActions[blocked ? "critical" : "info"](
      "PC1",
      `진압 ${payload.suppression_id} · ${payload.status}`,
      payload.event_id ?? undefined,
    );
    if (blocked) {
      const blockers = payload.interlock?.blockers?.map((b) => b.detail).join(", ");
      useUiStore.getState().pushToast({
        tone: "danger",
        title: "진압 인터락 차단",
        description: blockers || "안전 조건이 충족되지 않았습니다",
        duration: 0, // 수동으로 닫을 때까지 유지 — 놓치면 안 되는 알림
      });
    }
  },

  // ── 로그 / 시스템 ───────────────────────────────────────────────────────
  LOG: (payload, timestamp) => {
    useLogStore.getState().append({
      level: payload.level ?? "INFO",
      actor: payload.actor ?? "PC1",
      message: payload.message,
      refId: payload.ref_id,
      at: Date.parse(timestamp) || Date.now(),
    });
  },

  SYSTEM_ALERT: (payload) => {
    logActions.critical("PC1", `[${payload.code}] ${payload.message}`);
    useUiStore.getState().pushToast({
      tone: payload.severity === "CRITICAL" ? "danger" : "warn",
      title: "시스템 경고",
      description: payload.message,
    });
  },

  SUBSCRIBED: (payload) => {
    useConnectionStore.getState().setSubscribedTopics(payload.topics ?? []);
  },
};

/** 개발 중 무시된 타입을 한 번씩만 알린다 — 로그 폭주 방지. */
const warnedTypes = new Set<string>();

export function dispatch(envelope: WsEnvelope): void {
  useConnectionStore.getState().markMessage();

  const handler = handlers[envelope.type] as Handler<WsMessageType> | undefined;
  if (!handler) {
    // 정의는 됐지만 처리기가 없는 타입(PONG 등) — 정상. 조용히 넘어간다.
    return;
  }

  if (!isValidPayload(envelope.type, envelope.payload)) {
    if (import.meta.env.DEV && !warnedTypes.has(envelope.type)) {
      warnedTypes.add(envelope.type);
      console.debug(`[ws] ${envelope.type} 페이로드 형식 불일치 — 무시함`, envelope.payload);
    }
    return;
  }

  try {
    handler(envelope.payload as never, envelope.timestamp);
  } catch (error) {
    // 한 핸들러의 버그가 스트림 전체를 멈추면 안 된다.
    if (import.meta.env.DEV) {
      console.debug(`[ws] ${envelope.type} 핸들러 예외`, error);
    }
  }
}

/** 테스트에서 처리 가능한 타입을 확인할 때 쓴다. */
export function handledTypes(): WsMessageType[] {
  return Object.keys(handlers) as WsMessageType[];
}
