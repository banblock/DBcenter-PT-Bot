import { Camera, Flame } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useDashboard } from "../../hooks/useDashboard";
import type { AppEvent, RobotState } from "../../types";
import { fmt } from "../../utils/robot";
import "./CameraGrid.css";

// 백엔드 MJPEG 스트림 베이스 (backendClient와 동일 규칙)
const API_BASE: string = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";

interface CamSpec {
  id: string;        // 리액트 key
  streamId: string;  // 백엔드 카메라 id (/api/cameras/{streamId}/stream)
  name: string;
  zone: string;      // 존 매칭(이상 이벤트 → 카메라 매핑)
  hot: boolean;
}

/** 카메라 한 대 = MJPEG <img> 실피드 + 헤더/푸터. 스트림 실패 시 '신호 없음'으로 폴백하고
 *  5초마다 자동 재연결(비전이 나중에 붙어도 새로고침 없이 복구). 실피드엔 비전이 그린
 *  YOLO 박스가 이미 포함돼 있어 가짜 bbox 오버레이는 두지 않는다.
 *  우상단 감지 버튼(기능6)은 실피드 위에서도 화재 대응 흐름을 수동으로 확인할 수 있게 한다. */
function CamTile({ cam, ts, onDetect }: { cam: CamSpec; ts: string; onDetect: (cam: CamSpec) => void }) {
  const [live, setLive] = useState(true);
  const [nonce, setNonce] = useState(0); // 재연결 시 캐시 무효화
  const src = `${API_BASE}/api/cameras/${cam.streamId}/stream?t=${nonce}`;

  useEffect(() => {
    if (live) return;
    const id = window.setTimeout(() => {
      setNonce((n) => n + 1);
      setLive(true);
    }, 5000);
    return () => window.clearTimeout(id);
  }, [live]);

  return (
    <article className={`cam2${cam.hot ? " hot" : ""}`}>
      <div className="cam2__floor" />
      {live ? (
        <img className="cam2__feed" src={src} alt={cam.name} onError={() => setLive(false)} />
      ) : (
        <div className="cam2__nosignal">신호 없음 · 재연결 중…</div>
      )}
      <div className="cam2__head">
        <span className="cam2__name">{cam.name}</span>
        <span className="cam2__status">{cam.hot ? "이상감지" : "정상"}</span>
      </div>
      <button
        type="button"
        className="cam2__detect"
        title="화재/이상 감지 시뮬레이션"
        onClick={() => onDetect(cam)}
      >
        <Flame size={12} /> 감지
      </button>
      <div className="cam2__foot">
        <span>{ts}</span>
        <span className="cam2__live">
          <i /> LIVE
        </span>
      </div>
    </article>
  );
}

export function CameraGrid() {
  const { now, robots, isZone2Hot, latestCctvDetection, latestAmrDetection, openPopup, sendCommand, dockAll, addLog, ackEvent } = useDashboard();
  const shownDetectionIds = useRef(new Set<string>());
  const shownAmrIds = useRef(new Set<string>());
  const ts = fmt(now);

  const cams: CamSpec[] = [
    { id: "cctv-01", streamId: "cctv1", name: "CCTV-01 (존-1)", zone: "존-1", hot: false },
    { id: "cctv-02", streamId: "cctv2", name: "CCTV-02 (존-2)", zone: "존-2", hot: isZone2Hot },
    // AMR 카메라는 상시 표시하지 않고, 이벤트 발생 시 Modal 팝업으로만 띄운다.
  ];

  /** 기능6/규칙3: CCTV 화재/이상 감지 → 전체 일시정지 → 오탐 재개 or 전체 복귀 */
  function triggerDetection(cam: CamSpec, event?: AppEvent) {
    const pauseableStates = new Set<RobotState>(["PATROLLING", "INSPECTING", "RESUMING", "DISPATCHING"]);
    const pausedRobotIds = robots.filter((robot) => pauseableStates.has(robot.state)).map((robot) => robot.id);
    pausedRobotIds.forEach((robotId) => sendCommand(robotId, "pause"));
    if (pausedRobotIds.length) {
      addLog("PC1", `${cam.name} 이상 감지 → 전체 AMR 일시정지 (${pausedRobotIds.join(" · ")})`, true);
    }

    openPopup({
      title: `🔥 CCTV 화재/이상 감지 — ${cam.name}`,
      camLabel: cam.name,
      camHot: true,
      evtHtml:
        `<b>${cam.name}</b> 에서 화재/이상 징후가 감지되었습니다.` +
        `${event ? `<br/><b>감지 내용:</b> ${event.text}` : ""}<br/>` +
        `${pausedRobotIds.length ? `<b>${pausedRobotIds.join(" · ")}</b> 순찰을 일시정지했습니다.<br/>` : ""}` +
        `오탐이면 <b>오탐 확인 및 전체 재개</b>, 실제 상황이면 <b>전체 AMR 복귀 요청</b>을 선택하세요.`,
      actions: [
        {
          label: "✓ 오탐 확인 및 전체 재개",
          cls: "ghost",
          onClick: () => {
            addLog("PC2", `${cam.name} 감지 · 오탐 처리 → 전체 AMR 재개`);
            pausedRobotIds.forEach((robotId) => sendCommand(robotId, "resume"));
          },
        },
        {
          label: "🔌 전체 AMR 복귀 요청",
          cls: "start",
          onClick: () => {
            addLog("PC1", `${cam.name} 화재 감지 → 전체 AMR 복귀 신호 전송`, true);
            dockAll();
          },
        },
      ],
    });
  }

  /** 새 CCTV 이상 이벤트를 최초 수신한 순간 자동으로 감지 팝업을 띄운다. */
  useEffect(() => {
    const event = latestCctvDetection;
    if (!event || shownDetectionIds.current.has(event.id)) return;
    shownDetectionIds.current.add(event.id);
    const cameraNumber = event.cameraId?.match(/(\d+)$/)?.[1];
    const cam =
      cams.find((item) => item.zone === event.zone) ??
      cams.find((item) => item.id.match(/(\d+)$/)?.[1] === cameraNumber) ??
      cams[0];
    triggerDetection(cam, event);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [latestCctvDetection]);

  /** AMR 카메라 화재/이상 감지 → 상시 표출 대신 팝업으로만. 긴급 알림 + AMR 캠 실피드 +
   *  '확인 — 사람 출동' 버튼. CCTV 와 달리 로봇을 세우거나 급파하지 않는다(사람이 출동). */
  useEffect(() => {
    const event = latestAmrDetection;
    if (!event || shownAmrIds.current.has(event.id)) return;
    shownAmrIds.current.add(event.id);
    const robotId = event.robotId ?? "AMR-01";
    addLog("PC2", `${robotId} 카메라 이상 감지 → 사람 출동 대기 (${event.text})`, true);
    openPopup({
      title: `🚨 AMR 카메라 화재/이상 감지 — ${robotId}`,
      camLabel: `${robotId} 순찰 카메라`,
      camHot: true,
      camStreamUrl: `${API_BASE}/api/cameras/${robotId}/stream`,
      evtHtml:
        `<b>${robotId}</b> 순찰 카메라에서 화재/이상 징후가 감지되었습니다.<br/>` +
        `<b>감지 내용:</b> ${event.text}<br/>` +
        `현장 확인이 필요합니다. <b>확인 — 사람 출동</b>을 누르면 담당자 출동으로 처리됩니다.`,
      actions: [
        {
          label: "✓ 확인 — 사람 출동",
          cls: "start",
          onClick: () => {
            addLog("PC1", `${robotId} 카메라 화재 감지 확인 → 사람 출동 (현장 확인)`, true);
            ackEvent(event.id);
          },
        },
      ],
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [latestAmrDetection]);

  return (
    <section className="panel camera-panel">
      <header className="panel-header">
        <h2>
          <Camera size={16} /> CCTV
        </h2>
      </header>
      <div className="camera-grid">
        {cams.map((cam) => (
          <CamTile key={cam.id} cam={cam} ts={ts} onDetect={triggerDetection} />
        ))}
      </div>
      <p className="camera-panel__note">
        ※ CCTV 이상 이벤트 발생 시 팝업이 자동 표시됩니다. · <b>감지</b> 버튼으로도 화재 대응 흐름을 확인할 수 있습니다.
      </p>
    </section>
  );
}
