import { dockPixelPosition, ZONE_AMR, ZONES } from "../constants/dashboard";
import type {
  AppEvent,
  Command,
  InboundMessage,
  LinkMode,
  LogTag,
  PopupData,
  Robot,
  RobotState,
  WaypointMap,
} from "../types";

/**
 * 백엔드 없이 화면을 구동하는 내장 시뮬레이터 (기능 1~6).
 * - 순찰 시작 전: 두 대 모두 도킹 스테이션에서 IDLE 대기
 * - 통합 순찰 시작(startPatrol): 각 AMR을 지정된 waypoint로 순환 이동(도달 시점에만 갱신)
 * - waypoint 도달마다 CheckGate 판별(정상 O / 비정상 X → 알림)
 * - 낮은 확률로 AMR 자체 이벤트(기능3) / CCTV 감지(기능4) 발생 → 팝업
 * - 긴급정지/전체 재개/도킹 복귀(기능5)
 * 실연동 시 이 파일은 import만 걷어내면 삭제 가능.
 */

interface DemoDeps {
  applyMessage: (msg: InboundMessage) => void;
  addLog: (tag: LogTag, msg: string, hot?: boolean) => void;
  getWaypoints: () => WaypointMap;
  openPopup: (popup: PopupData) => void;
  closePopup: () => void;
  setLink: (mode: LinkMode, text: string) => void;
}

const COMMAND_STATE_MAP: Record<Command, RobotState> = {
  pause: "PATROL_PAUSED",
  resume: "PATROLLING",
  anomaly_resume: "RESUMING",
  estop: "EMERGENCY_STOP",
  reset: "IDLE",
  dock: "DOCKING",
  stop_and_dock: "DOCKING",
  start: "PATROLLING",
  ack: "REPORTING",
};

const DOCK_POSE: Record<string, { x: number; y: number }> = {
  "AMR-01": dockPixelPosition("AMR-01", 113, 66),
  "AMR-02": dockPixelPosition("AMR-02", 113, 66),
};

export interface DemoHandle {
  stop: () => void;
  command: (id: string, cmd: Command) => void;
  goto: (id: string, x: number, y: number) => void;
  startPatrol: () => void;
  estopAll: () => void;
  resumeAll: () => void;
  dockAll: () => void;
}

export function startDemoSimulator(deps: DemoDeps): DemoHandle {
  const { applyMessage, getWaypoints, openPopup, setLink } = deps;

  let timer: number | null = null;
  const robots: Record<string, Robot> = {};
  let events: AppEvent[] = [];
  const prog: Record<string, { zone: string; i: number; wps: { x: number; y: number }[] }> = {};
  const paused = new Set<string>();
  const dispatch: Record<string, { x: number; y: number; zone: string }> = {};
  let popupBusy = false;

  const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v));

  /** 내부 robots 갱신 + 컨텍스트로 push (이벤트는 항상 함께 보내 최신 목록 반영) */
  function emit(patch: Robot[], detectedCctvEvent?: AppEvent) {
    patch.forEach((r) => {
      robots[r.id] = r;
    });
    applyMessage({ robots: patch, events: events.map((e) => ({ ...e })), detectedCctvEvent });
  }

  const one = (amr: string, extra: Partial<Robot>): Robot =>
    ({ ...clone(robots[amr]), ...extra, ts: Date.now() }) as Robot;

  function resolveEvt(amr: string, kind: AppEvent["kind"]) {
    events = events.map((e) =>
      e.assignee === amr && e.kind === kind && e.state !== "RESOLVED" ? { ...e, state: "RESOLVED" } : e,
    );
  }

  /* 순찰 시작 전: 두 대 모두 도킹 스테이션에서 IDLE 대기 */
  function seedIdle() {
    const seeded = ZONES.map((z, idx) => ({
      id: ZONE_AMR[z],
      state: "IDLE" as RobotState,
      mission_type: "PATROL" as const,
      step: 1,
      battery: idx === 0 ? 92 : 88,
      route: z,
      zone: z,
      task: "도킹 스테이션 대기",
      pose: DOCK_POSE[ZONE_AMR[z]],
      atWaypoint: 0,
      zones: [],
      ts: Date.now(),
    }));
    events = [];
    emit(seeded);
  }

  /* 기능6: waypoint(차단기) 도착 → CheckGate.srv 판별 (정상 O / 비정상 X) */
  function checkGate(amr: string, zone: string, seq: number, r: Robot) {
    const robot_id = amr === "AMR-01" ? 1 : 2;
    const rack_id = (robot_id === 1 ? 100 : 200) + seq;
    const crossinggate_state = Math.random() > 0.28; // true = 정상(O)
    if (crossinggate_state) {
      r.zones = [{ id: zone, state: "NORMAL" }];
    } else {
      r.zones = [{ id: zone, state: "MISMATCH" }];
      events = [
        {
          id: `GATE-${robot_id}-${rack_id}-${Date.now() % 100000}`,
          kind: "GATE",
          severity: "WARN",
          state: "ACK_WAIT",
          zone,
          text: `차단기 비정상 · rack #${rack_id} (AMR-0${robot_id} · crossinggate OFF)`,
          ts: Date.now(),
        } as AppEvent,
        ...events,
      ].slice(0, 100);
      deps.addLog("PC2", `차단기 비정상 · rack #${rack_id} (${amr})`, true);
    }
  }

  /* 기능2·4·6: waypoint 도달 현황 / CCTV 급파 도착 / 차단기 확인 */
  function tick() {
    const patch: Robot[] = [];
    Object.keys(prog).forEach((amr) => {
      if (paused.has(amr)) return; // 기능3/5: 일시정지 → 위치·현황 유지
      if (dispatch[amr]) {
        // 기능4: 이번 틱에 문제지점 도착
        const d = dispatch[amr];
        delete dispatch[amr];
        patch.push(
          one(amr, {
            pose: { x: d.x, y: d.y },
            mission_type: "ANOMALY",
            state: "DISPATCHING",
            task: `CCTV 감지지점 도착 (${d.zone})`,
          }),
        );
        paused.add(amr);
        onCctvArrive(amr, d);
        return;
      }
      const p = prog[amr]; // 기능2·6: 다음 waypoint 도달 + 차단기 확인
      p.i = (p.i + 1) % p.wps.length;
      const w = p.wps[p.i];
      const r = one(amr, {
        pose: { x: w.x, y: w.y },
        atWaypoint: p.i + 1,
        mission_type: "PATROL",
        state: "INSPECTING",
        step: 3,
        task: `${p.zone} · waypoint ${p.i + 1}/${p.wps.length} (rack #${(amr === "AMR-01" ? 100 : 200) + (p.i + 1)}) 점검`,
      });
      checkGate(amr, p.zone, p.i + 1, r);
      r.battery = Math.max(20, (r.battery ?? 80) - (Math.random() < 0.35 ? 1 : 0));
      patch.push(r);
    });
    if (patch.length) emit(patch);

    if (!popupBusy) {
      // 기능3: 낮은 확률로 AMR 자체 이벤트
      const cand = Object.keys(prog).filter((a) => !paused.has(a) && !dispatch[a]);
      if (cand.length && Math.random() < 0.06) triggerAmrEvent(cand[Math.floor(Math.random() * cand.length)]);
    }
    if (!popupBusy && Object.keys(dispatch).length === 0 && Math.random() < 0.05) triggerCctv();
  }

  /* 기능3: AMR 발행 이벤트 → 해당 AMR 일시정지 + 카메라 팝업 */
  function triggerAmrEvent(amr: string) {
    paused.add(amr);
    popupBusy = true;
    const rid = amr === "AMR-01" ? 1 : 2;
    const ev = ["연기 감지", "과열 감지", "장애물 감지"][Math.floor(Math.random() * 3)];
    const zone = robots[amr]?.zone ?? "";
    events = [
      {
        id: `AMR-${rid}-${Date.now() % 100000}`,
        kind: "AMR",
        severity: "DANGER",
        state: "ASSIGNED",
        assignee: amr,
        zone,
        text: `${amr} ${ev} — 순찰 일시정지`,
        ts: Date.now(),
      } as AppEvent,
      ...events,
    ].slice(0, 100);
    emit([one(amr, { state: "PATROL_PAUSED", task: `이벤트 발생 — 판단 대기 (${ev})` })]);
    openPopup({
      title: `⚠ ${amr} 이벤트 — 카메라 확인`,
      camLabel: `AMR CAM (${amr})`,
      camHot: true,
      evtHtml: `<b>${amr}</b> · ${zone} · <b>${ev}</b> 로 순찰을 일시정지했습니다. 영상 확인 후 조치를 선택하세요.`,
      actions: [
        { label: "🔌 도킹 스테이션 복귀", cls: "ghost", onClick: () => { popupBusy = false; dockRobot(amr); } },
        { label: "▶ 순찰 재개", cls: "start", onClick: () => { popupBusy = false; resumeRobot(amr); } },
      ],
    });
  }

  /* 기능4: CCTV 문제 감지 → 가까운 AMR 급파 + 이벤트 로그·긴급알림 즉시 */
  function triggerCctv() {
    const zone = ZONES[Math.floor(Math.random() * ZONES.length)];
    const P = { x: 120 + Math.random() * 640, y: 90 + Math.random() * 300, zone };
    const cand = Object.keys(prog).filter((a) => !paused.has(a) && !dispatch[a]);
    if (!cand.length) return;
    let best = cand[0];
    let bd = Infinity;
    cand.forEach((a) => {
      const q = robots[a].pose ?? { x: 0, y: 0 };
      const d = (q.x - P.x) ** 2 + (q.y - P.y) ** 2;
      if (d < bd) {
        bd = d;
        best = a;
      }
    });
    dispatch[best] = P;
    const cctvEvent: AppEvent = {
        id: `CCTV-${Date.now() % 100000}`,
        kind: "CCTV",
        severity: "DANGER",
        state: "ASSIGNED",
        assignee: best,
        zone,
        text: `CCTV ${zone} 이상 감지 → ${best} 급파`,
        ts: Date.now(),
      };
    events = [cctvEvent, ...events].slice(0, 100);
    emit(
      [one(best, { mission_type: "ANOMALY", state: "DISPATCHING", task: `CCTV 감지지점 이동 (${zone})` })],
      cctvEvent,
    );
  }

  /* 기능4: 급파 AMR 도착 → CCTV 팝업 (오작동 확인·작업 재개) */
  function onCctvArrive(amr: string, d: { zone: string }) {
    popupBusy = true;
    openPopup({
      title: `📹 CCTV 확인 — ${amr} 도착 (${d.zone})`,
      camLabel: `CCTV (${d.zone})`,
      camHot: true,
      evtHtml: `<b>${amr}</b> 이(가) CCTV 감지지점 <b>${d.zone}</b> 에 도착했습니다. 영상 확인 후 오작동이면 작업을 재개하세요.`,
      actions: [
        { label: "✓ CCTV 오작동 확인 및 작업 재개", cls: "start", onClick: () => { popupBusy = false; resolveCctv(amr); } },
      ],
    });
  }

  function resolveCctv(amr: string) {
    paused.delete(amr);
    resolveEvt(amr, "CCTV");
    emit([one(amr, { mission_type: "PATROL", state: "PATROLLING", task: "순찰 재개" })]);
  }
  function dockRobot(amr: string) {
    paused.delete(amr);
    delete prog[amr];
    resolveEvt(amr, "AMR");
    emit([one(amr, { mission_type: "PATROL", state: "DOCKING", task: "도킹 스테이션 복귀 중", pose: DOCK_POSE[amr] })]);
  }
  function resumeRobot(amr: string) {
    paused.delete(amr);
    resolveEvt(amr, "AMR");
    emit([one(amr, { mission_type: "PATROL", state: "PATROLLING", task: "순찰 재개" })]);
  }

  /* --- 초기: 도킹 대기 상태 시드 --- */
  seedIdle();

  return {
    stop() {
      if (timer !== null) window.clearInterval(timer);
    },

    /* 기능1: 통합 순찰 시작 → 각 AMR을 첫 waypoint로 출발 */
    startPatrol() {
      const wpMap = getWaypoints();
      const patch: Robot[] = [];
      ZONES.forEach((z, idx) => {
        const amr = ZONE_AMR[z];
        const wps = wpMap[z] ?? [];
        if (!wps.length) return;
        prog[amr] = { zone: z, i: 0, wps: wps.map((w) => ({ x: w.x, y: w.y })) };
        patch.push({
          id: amr,
          state: "PATROLLING",
          mission_type: "PATROL",
          step: 2,
          battery: idx === 0 ? 90 : 86,
          route: z,
          zone: z,
          task: `${z} · waypoint 1/${wps.length} 이동`,
          pose: { x: wps[0].x, y: wps[0].y },
          atWaypoint: 1,
          zones: [{ id: z, state: "NORMAL" }],
          ts: Date.now(),
        });
      });
      emit(patch);
      setLink("demo", "데모 · 순찰 진행 중");
      if (timer !== null) window.clearInterval(timer);
      timer = window.setInterval(tick, 3000);
    },

    /* 기능5: 긴급정지 → 2대 정지 */
    estopAll() {
      if (timer !== null) {
        window.clearInterval(timer);
        timer = null;
      }
      popupBusy = false;
      deps.closePopup();
      Object.keys(dispatch).forEach((k) => delete dispatch[k]);
      Object.keys(robots).forEach((id) => paused.add(id));
      emit(Object.keys(robots).map((id) => one(id, { state: "EMERGENCY_STOP", task: "긴급정지 — 조작 대기" })));
      setLink("demo", "데모 · 긴급정지");
    },
    resumeAll() {
      paused.clear();
      emit(Object.keys(robots).map((id) => one(id, { mission_type: "PATROL", state: "PATROLLING", task: "전체 작업 재개" })));
      if (timer === null) timer = window.setInterval(tick, 3000);
      setLink("demo", "데모 · 순찰 진행 중");
    },
    dockAll() {
      if (timer !== null) {
        window.clearInterval(timer);
        timer = null;
      }
      paused.clear();
      emit(
        Object.keys(robots).map((id) =>
          one(id, { state: "DOCKING", task: "도킹 스테이션 복귀 중", pose: DOCK_POSE[id] ?? robots[id].pose }),
        ),
      );
      setLink("demo", "데모 · 도킹 복귀");
    },

    command(id: string, cmd: Command) {
      window.setTimeout(() => {
        const extra: Partial<Robot> = { state: COMMAND_STATE_MAP[cmd] ?? robots[id]?.state };
        if (cmd === "estop") {
          if (timer !== null) {
            window.clearInterval(timer);
            timer = null;
          }
          extra.task = "긴급정지 — 조작 대기";
          paused.add(id);
        }
        if (cmd === "stop_and_dock") {
          paused.delete(id);
          delete prog[id];
          delete dispatch[id];
          extra.task = "정지 완료 · 도킹 스테이션 복귀 중";
          extra.pose = DOCK_POSE[id] ?? robots[id]?.pose;
        }
        if (cmd === "dock") extra.task = "도킹 스테이션 복귀 중";
        if (cmd === "pause") paused.add(id);
        if (cmd === "resume") paused.delete(id);
        if (cmd === "anomaly_resume") {
          // 이상 대응 hold 해제 → 순찰 복귀 (resumeRobot 이 이벤트 해제·상태 전이 처리)
          resumeRobot(id);
          return;
        }
        if (robots[id]) emit([one(id, extra)]);
      }, 500);
    },

    goto(id: string, x: number, y: number) {
      window.setTimeout(() => {
        if (robots[id]) emit([one(id, { pose: { x, y }, task: `수동 목표 이동 · (${x}, ${y})` })]);
      }, 400);
    },
  };
}
