/**
 * 로봇/미션 명령 훅.
 *
 * ## 낙관적 업데이트를 하지 않는 이유
 * 관제 화면이 "정지됨"을 보여주는데 로봇이 실제로 돌고 있으면 사고가 난다.
 * 그래서 버튼을 누르면 **pending 표시만** 하고, 상태는 서버가 WS 로 알려줄 때만
 * 바꾼다. 서버가 일정 시간 안에 답하지 않으면 pending 을 풀고 "응답 지연"을 로그에 남긴다.
 */

import { useCallback, useRef, useState } from "react";
import { patrolApi, robotApi } from "../services/api";
import { ApiError } from "../services/http/httpClient";
import { logActions } from "../store/logStore";
import { useMissionStore } from "../store/missionStore";
import { useRobotStore } from "../store/robotStore";
import { useUiStore } from "../store/uiStore";

export type RobotCommand =
  | "start"
  | "pause"
  | "resume"
  | "dock"
  | "estop"
  | "reset"
  | "goto"
  | "ack";

export const COMMAND_LABEL: Record<RobotCommand, string> = {
  start: "▶ 순찰",
  pause: "⏸ 일시정지",
  resume: "▶ 재개",
  dock: "🔌 복귀",
  estop: "⨯ 정지",
  reset: "↺ 리셋",
  goto: "→ 이동",
  ack: "✓ 경보 확인",
};

/** 서버가 이 시간 안에 상태로 답하지 않으면 pending 을 푼다. */
const PENDING_TIMEOUT_MS = 5000;

export function useCommands() {
  const [pending, setPending] = useState<Record<string, RobotCommand>>({});
  const timers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});
  const pushToast = useUiStore((s) => s.pushToast);

  const clearPending = useCallback((robotId: string) => {
    setPending((prev) => {
      if (!(robotId in prev)) return prev;
      const next = { ...prev };
      delete next[robotId];
      return next;
    });
    if (timers.current[robotId]) {
      clearTimeout(timers.current[robotId]);
      delete timers.current[robotId];
    }
  }, []);

  const run = useCallback(
    async (robotId: string, command: RobotCommand, action: () => Promise<unknown>) => {
      setPending((prev) => ({ ...prev, [robotId]: command }));
      logActions.info("PC1", `${robotId} 명령 전송 · ${COMMAND_LABEL[command]}`);

      timers.current[robotId] = setTimeout(() => {
        clearPending(robotId);
        logActions.warn("PC1", `${robotId} 명령 응답 지연 — 상태 미변경`);
      }, PENDING_TIMEOUT_MS);

      try {
        await action();
        // 성공해도 pending 은 유지한다 — 서버가 WS 로 상태를 알려줄 때까지가 '진행 중'이다.
      } catch (error) {
        clearPending(robotId);
        const message = error instanceof ApiError ? error.message : String(error);
        logActions.critical("PC1", `${robotId} 명령 실패 · ${message}`);
        pushToast({ tone: "danger", title: `${robotId} 명령 실패`, description: message });
      }
    },
    [clearPending, pushToast],
  );

  /** 이 로봇이 지금 물고 있는 미션 ID — pause/resume/cancel 에 필요하다. */
  const activeMissionId = useCallback((robotId: string): string | null => {
    const robot = useRobotStore.getState().byId[robotId];
    if (robot?.mission_id) return robot.mission_id;
    const missions = useMissionStore.getState();
    const found = missions.order
      .map((id) => missions.byId[id])
      .find((m) => m?.robot_id === robotId && (m.status === "RUNNING" || m.status === "PREEMPTED"));
    return found?.mission_id ?? null;
  }, []);

  const sendCommand = useCallback(
    (robotId: string, command: RobotCommand) => {
      switch (command) {
        case "estop":
          return run(robotId, command, () => robotApi.emergencyStop(robotId));
        case "reset":
          return run(robotId, command, () => robotApi.resume(robotId, "RESUME_CHECKPOINT"));
        case "dock":
          return run(robotId, command, () => robotApi.dock(robotId));
        case "pause": {
          const missionId = activeMissionId(robotId);
          if (!missionId) {
            pushToast({ tone: "warn", title: `${robotId} 진행 중인 미션이 없습니다` });
            return Promise.resolve();
          }
          return run(robotId, command, () => patrolApi.pause(missionId));
        }
        case "resume": {
          const missionId = activeMissionId(robotId);
          if (!missionId) {
            // 미션이 없으면 긴급정지 해제로 해석한다
            return run(robotId, command, () => robotApi.resume(robotId));
          }
          return run(robotId, command, () => patrolApi.resume(missionId));
        }
        default:
          return Promise.resolve();
      }
    },
    [activeMissionId, run, pushToast],
  );

  const sendGoto = useCallback(
    (robotId: string, x: number, y: number) =>
      run(robotId, "goto", () => robotApi.goto(robotId, [{ x, y }])),
    [run],
  );

  /** 헤더 [통합 시작] (F-08) — 선택된 경로로 온라인 로봇 전체에 순찰을 건다. */
  const startPatrolAll = useCallback(
    async (routeId: string, robotIds?: string[]) => {
      const robots = useRobotStore.getState();
      const targets =
        robotIds ??
        robots.order.filter((id) => {
          const robot = robots.byId[id];
          return robot?.online && robot.state !== "EMERGENCY_STOP" && robot.state !== "ERROR";
        });

      if (targets.length === 0) {
        pushToast({ tone: "warn", title: "순찰을 시작할 수 있는 로봇이 없습니다" });
        return;
      }

      try {
        const result = await patrolApi.start({ route_id: routeId, robot_ids: targets });
        logActions.info("PC1", `순찰 시작 — 경로 ${routeId}, 로봇 ${targets.join(", ")}`);
        pushToast({
          tone: "success",
          title: "순찰을 시작했습니다",
          description: `${result.assigned.length}대 배정`,
        });
      } catch (error) {
        const message = error instanceof ApiError ? error.message : String(error);
        logActions.critical("PC1", `순찰 시작 실패 · ${message}`);
      }
    },
    [pushToast],
  );

  /** 헤더 [긴급정지] (F-09) — 전체 정지는 개별 반복이 아니라 전용 엔드포인트. */
  const emergencyStopAll = useCallback(async () => {
    logActions.critical("PC1", "전체 긴급정지 요청");
    try {
      const result = await robotApi.emergencyStopAll();
      pushToast({
        tone: "danger",
        title: "전체 긴급정지",
        description: `${result.stopped.length}대 정지`,
      });
    } catch (error) {
      const message = error instanceof ApiError ? error.message : String(error);
      pushToast({ tone: "danger", title: "긴급정지 실패", description: message });
    }
  }, [pushToast]);

  const dockAll = useCallback(async () => {
    const robots = useRobotStore.getState();
    for (const id of robots.order) {
      if (robots.byId[id]?.online) await sendCommand(id, "dock");
    }
  }, [sendCommand]);

  return {
    pending,
    clearPending,
    sendCommand,
    sendGoto,
    startPatrolAll,
    emergencyStopAll,
    dockAll,
  };
}
