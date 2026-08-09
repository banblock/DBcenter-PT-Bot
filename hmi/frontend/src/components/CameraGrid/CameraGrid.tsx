import { Camera } from "lucide-react";
import { useEffect, useState } from "react";
import { useDashboard } from "../../hooks/useDashboard";
import { fmt } from "../../utils/robot";
import "./CameraGrid.css";

// 백엔드 MJPEG 스트림 베이스 (backendClient와 동일 규칙)
const API_BASE: string = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";

interface CamSpec {
  id: string;        // 리액트 key
  streamId: string;  // 백엔드 카메라 id (/api/cameras/{streamId}/stream)
  name: string;
  hot: boolean;
}

/** 카메라 한 대 = MJPEG <img> 실피드 + 헤더/푸터. 스트림 실패 시 '신호 없음'으로 폴백하고
 *  5초마다 자동 재연결(비전이 나중에 붙어도 새로고침 없이 복구). 실피드엔 비전이 그린
 *  YOLO 박스가 이미 포함돼 있어 가짜 bbox 오버레이는 두지 않는다. */
function CamTile({ cam, ts }: { cam: CamSpec; ts: string }) {
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
  const { now, isZone2Hot } = useDashboard();
  const ts = fmt(now);

  const cams: CamSpec[] = [
    { id: "cctv-01", streamId: "cctv1", name: "CCTV-01 (존-1)", hot: false },
    { id: "cctv-02", streamId: "cctv2", name: "CCTV-02 (존-2)", hot: isZone2Hot },
    // AMR 카메라는 상시 표시하지 않고, 이벤트 발생 시 Modal 팝업으로만 띄운다.
  ];

  return (
    <section className="panel camera-panel">
      <header className="panel-header">
        <h2>
          <Camera size={16} /> 실시간 카메라 피드
        </h2>
      </header>
      <div className="camera-grid">
        {cams.map((cam) => (
          <CamTile key={cam.id} cam={cam} ts={ts} />
        ))}
      </div>
      <p className="camera-panel__note">※ AMR 카메라는 이벤트 발생 시 팝업으로 표시됩니다.</p>
    </section>
  );
}
