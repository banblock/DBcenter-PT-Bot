import { Camera } from "lucide-react";
import { useDashboard } from "../../hooks/useDashboard";
import { fmt } from "../../utils/robot";
import "./CameraGrid.css";

interface CamSpec {
  id: string;
  name: string;
  hot: boolean;
  normalLabel: string;
  hotLabel: string;
  box: { left: number; top: number; width: number; height: number };
}

export function CameraGrid() {
  const { now, isZone2Hot } = useDashboard();
  const ts = fmt(now);

  const cams: CamSpec[] = [
    {
      id: "cctv-01",
      name: "CCTV-01 (존-1)",
      hot: false,
      normalLabel: "breaker ON 0.92",
      hotLabel: "breaker ON 0.92",
      box: { left: 30, top: 34, width: 22, height: 46 },
    },
    {
      id: "cctv-02",
      name: "CCTV-02 (존-2)",
      hot: isZone2Hot,
      normalLabel: "breaker ON 0.96",
      hotLabel: "smoke 0.91",
      box: { left: 28, top: 26, width: 44, height: 52 },
    },
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
          <article key={cam.id} className={`cam2${cam.hot ? " hot" : ""}`}>
            <div className="cam2__floor" />
            <div className="cam2__head">
              <span className="cam2__name">{cam.name}</span>
              <span className="cam2__status">{cam.hot ? "이상감지" : "정상"}</span>
            </div>
            <span
              className={`bbox2 ${cam.hot ? "orange" : "green"}`}
              style={{
                left: `${cam.box.left}%`,
                top: `${cam.box.top}%`,
                width: `${cam.box.width}%`,
                height: `${cam.box.height}%`,
              }}
            >
              <span className="bbox2__lbl">{cam.hot ? cam.hotLabel : cam.normalLabel}</span>
            </span>
            <div className="cam2__foot">
              <span>{ts}</span>
              <span className="cam2__live">
                <i /> LIVE
              </span>
            </div>
          </article>
        ))}
      </div>
      <p className="camera-panel__note">※ AMR 카메라는 이벤트 발생 시 팝업으로 표시됩니다.</p>
    </section>
  );
}
