// 백엔드 없을 때 가짜 데이터

import type { AppEvent, Command, InboundMessage, LogTag, Robot, RobotState } from "../types";

/**
 * 백엔드 없이 화면을 구동하는 내장 시뮬레이터.
 * 실제 WS 서버가 하는 일(로봇 텔레메트리 생성, 이벤트 생성/갱신)을 흉내 낸다.
 * 실연동 시 이 파일은 더 이상 import되지 않으면 된다 — DashboardContext에서 분기만 걷어내면 삭제 가능.
 */

interface DemoDeps {
  applyMessage: (msg: InboundMessage) => void;
  addLog: (tag: LogTag, msg: string, hot?: boolean) => void;
}

const BASE: Record<string, Robot> = {
  "AMR-01": {
    id: "AMR-01",
    state: "PATROLLING",
    mission_type: "PATROL",
    step: 2,
    battery: 78,
    route: "Route A",
    zone: "존-1",
    task: "존-1 순찰",
    pose: { x: 250, y: 145 },
    ts: Date.now(),
    zones: [
      { id: "Z1", state: "NORMAL" },
      { id: "Z3", state: "STALE" },
    ],
  },
  "AMR-02": {
    id: "AMR-02",
    state: "PATROLLING",
    mission_type: "PATROL",
    step: 2,
    battery: 52,
    route: "Route B",
    zone: "존-2",
    task: "존-2 순찰",
    pose: { x: 402, y: 340 },
    ts: Date.now(),
    zones: [{ id: "Z2", state: "NORMAL" }],
  },
};

const COMMAND_STATE_MAP: Record<Command, RobotState> = {
  pause: "PATROL_PAUSED",
  resume: "PATROLLING",
  estop: "EMERGENCY_STOP",
  reset: "IDLE",
  dock: "DOCKING",
  start: "UNDOCKING",
  ack: "REPORTING",
};

export interface DemoHandle {
  stop: () => void;
  command: (id: string, cmd: Command) => void;
  goto: (id: string, x: number, y: number) => void;
}

export function startDemoSimulator({ applyMessage, addLog }: DemoDeps): DemoHandle {
  let t = 0;
  let tickTimer: number | null = null;

  const robots: Record<string, Robot> = {
    "AMR-01": clone(BASE["AMR-01"]),
    "AMR-02": clone(BASE["AMR-02"]),
  };
  let events: AppEvent[] = [];

  function clone<T>(value: T): T {
    return JSON.parse(JSON.stringify(value));
  }

  /** AMR-02 스토리라인 전용 이벤트 갱신 — events[0]으로 가정하지 않고 id로 찾는다
   *  (AMR-01의 무작위 차단기 불일치 이벤트가 같은 틱에 새로 unshift될 수 있으므로) */
  function setEventState(id: string, state: AppEvent["state"]) {
    events = events.map((e) => (e.id === id ? { ...e, state } : e));
  }

  function push(patch: InboundMessage & { robots?: Robot[] }) {
    patch.robots?.forEach((r) => {
      // 데모는 항상 완전한 Robot 을 만들어 넣는다 (부분 패치 아님).
      robots[r.id] = r as Robot;
    });
    applyMessage(patch);
  }

  function tick() {
    t++;
    const a1 = clone(robots["AMR-01"]);
    const a2 = clone(robots["AMR-02"]);
    a1.battery = Math.max(20, a1.battery - (t % 12 === 0 ? 1 : 0));
    a2.battery = Math.max(20, a2.battery - (t % 10 === 0 ? 1 : 0));

    /* --- AMR-01: 순찰 루프 --- */
    if (a1.mission_type === "PATROL" && a1.zones) {
      const cyc = t % 20;
      if (cyc === 2) {
        a1.state = "INSPECTING";
        a1.step = 3;
        a1.task = "존-1 차단기 점검";
        a1.step_note = "3각도 중 1";
        a1.zones[0].state = "SCANNING";
      }
      if (cyc === 5) a1.step_note = "3각도 중 3";
      if (cyc === 7) {
        a1.state = "PATROLLING";
        a1.step = 4;
        a1.task = "존-3 이동";
        a1.step_note = null;
        a1.zones[0].state = "NORMAL";
        a1.zone = "존-3";
      }
      if (cyc === 11) {
        a1.state = "INSPECTING";
        a1.step = 3;
        a1.task = "존-3 차단기 점검";
        a1.zones[1].state = "SCANNING";
      }
      if (cyc === 15) {
        a1.state = "PATROLLING";
        a1.step = 2;
        a1.task = "존-1 이동";
        a1.zone = "존-1";
        a1.zones[1].state = Math.random() < 0.25 ? "MISMATCH" : "NORMAL";
        if (a1.zones[1].state === "MISMATCH") {
          events = [
            {
              id: "EVT-B" + t,
              severity: "WARN",
              state: "ACK_WAIT",
              text: "존-3 차단기 DB 불일치 (기대 ON / 관측 OFF)",
              zone: "존-3",
              ts: Date.now(),
            },
            ...events,
          ];
        }
      }
      a1.pose = { x: 250 + Math.sin(t / 3) * 60, y: 145 + Math.cos(t / 3) * 25 };
    }

    /* --- AMR-02: 12틱째 연기 감지 → ANOMALY 미션 --- */
    if (t === 12) {
      events = [
        {
          id: "EVT-0142",
          severity: "DANGER",
          state: "ASSIGNED",
          assignee: "AMR-02",
          text: "존-2 연기 감지 (CCTV-02, conf 0.91)",
          zone: "존-2",
          ts: Date.now(),
        },
        ...events,
      ];
      a2.mission_type = "ANOMALY";
      a2.state = "DISPATCHING";
      a2.step = 2;
      a2.event_id = "EVT-0142";
      a2.event_type = "연기";
      a2.target_zone = "존-2";
      a2.task = "존-2 이상지점 이동";
      a2.patrol_resume = { step: 2, zone: "존-2 경로" };
      addLog("PC2", "CCTV-02 연기 감지 (conf 0.91) → 이벤트 생성", true);
    }
    if (t === 17) {
      a2.state = "INSPECTING";
      a2.step = 3;
      a2.task = "현장 다각도 확인";
      a2.step_note = "검증 2/3";
      setEventState("EVT-0142", "ON_SITE");
    }
    if (t === 22) {
      a2.state = "ALERTING";
      a2.step = 4;
      a2.task = "현장 경보 발령 (부저·음성)";
      a2.step_note = null;
      setEventState("EVT-0142", "ALERTING");
      addLog("Fleet", "AMR-02 현장 경보 발령 · 관제 ACK 대기", true);
    }
    if (t === 30) {
      a2.state = "REPORTING";
      a2.step = 5;
      a2.task = "결과 전송";
      setEventState("EVT-0142", "ACK_WAIT");
    }
    if (t === 34) {
      a2.state = "RESUMING";
      a2.task = "순찰 복귀 중";
    }
    if (t === 38) {
      a2.mission_type = "PATROL";
      a2.state = "PATROLLING";
      a2.step = 2;
      a2.task = "존-2 순찰";
      a2.event_id = null;
      a2.patrol_resume = null;
      setEventState("EVT-0142", "RESOLVED");
    }
    if (a2.mission_type === "ANOMALY") {
      a2.pose = { x: 402 + Math.min(180, (t - 12) * 18), y: 340 - Math.min(30, (t - 12) * 3) };
    } else if (t > 38) {
      a2.pose = { x: 500 + Math.sin(t / 4) * 80, y: 300 + Math.cos(t / 4) * 30 };
    }

    push({
      robots: [
        { ...a1, ts: Date.now() },
        { ...a2, ts: Date.now() },
      ],
      events: [...events],
      stats: t === 12 ? { fire: 1 } : undefined,
    });
  }

  function startTicking() {
    tickTimer = window.setInterval(tick, 1200);
  }

  addLog("PC1", "백엔드 미연결 · 내장 시뮬레이터로 화면 구동");
  push({
    robots: [
      { ...robots["AMR-01"], ts: Date.now() },
      { ...robots["AMR-02"], ts: Date.now() },
    ],
    events: [],
  });
  startTicking();

  function command(id: string, cmd: Command) {
    window.setTimeout(() => {
      const rb = clone(robots[id] ?? BASE[id]);
      if (!rb) return;
      rb.state = COMMAND_STATE_MAP[cmd] ?? rb.state;
      if (cmd === "estop") {
        if (tickTimer !== null) {
          window.clearInterval(tickTimer);
          tickTimer = null;
        }
        rb.task = "긴급정지 — 조작 대기";
      }
      if (cmd === "reset" && tickTimer === null) startTicking();
      push({ robots: [{ ...rb, ts: Date.now() }] });
    }, 600);
  }

  /** 지도 클릭으로 지정한 목표 좌표로 이동 — 데모에서는 즉시 pose를 옮기고 로그만 남긴다.
   *  (실제 주행 스크립트가 계속 도는 로봇이면 다음 tick에서 다시 덮어써질 수 있음 — 데모 한계) */
  function goto(id: string, x: number, y: number) {
    window.setTimeout(() => {
      const rb = clone(robots[id] ?? BASE[id]);
      if (!rb) return;
      rb.pose = { x, y };
      rb.task = `수동 목표 이동 · (${x}, ${y})`;
      push({ robots: [{ ...rb, ts: Date.now() }] });
    }, 400);
  }

  function stop() {
    if (tickTimer !== null) window.clearInterval(tickTimer);
  }

  return { stop, command, goto };
}
