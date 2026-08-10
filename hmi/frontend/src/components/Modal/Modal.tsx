import { useDashboard } from "../../hooks/useDashboard";
import "./Modal.css";

/** 기능3·4: AMR 이벤트 / CCTV 확인 팝업 — 카메라 영상 + 조치 버튼 */
export function Modal() {
  const { popup, closePopup } = useDashboard();
  if (!popup) return null;
  const t = new Date().toTimeString().slice(0, 8);

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true">
      <div className="modal">
        <div className="modal__hd">
          <span>{popup.title}</span>
        </div>
        <div className={`modal__cam${popup.camHot ? " modal__cam--hot" : ""}`}>
          <div className="modal__chd">
            <span className="modal__cname">{popup.camLabel}</span>
            <span className="modal__cstatus">{popup.camHot ? "이상감지" : "정상"}</span>
          </div>
          {popup.camStreamUrl ? (
            // 실제 카메라 MJPEG 실피드(비전이 그린 박스 포함). 팝업이 열려 있을 때만 마운트되어
            // 이때만 스트림이 열린다 → AMR 캠을 상시 표출하지 않아 부하를 아낀다.
            <img className="modal__feed" src={popup.camStreamUrl} alt={popup.camLabel} />
          ) : (
            <div className={`modal__bbox modal__bbox--${popup.camHot ? "red" : "green"}`}>
              <span className="modal__lbl">{popup.camHot ? "anomaly 0.9" : "normal"}</span>
            </div>
          )}
          <div className="modal__cfoot">
            <span>{t}</span>
            <span className="modal__live">
              <i />
              LIVE
            </span>
          </div>
        </div>
        <div className="modal__body">
          <div className="modal__evt" dangerouslySetInnerHTML={{ __html: popup.evtHtml }} />
          <div className="modal__actions">
            {popup.actions.map((a, i) => (
              <button
                key={i}
                type="button"
                className={`button button--${a.cls ?? "ghost"}`}
                onClick={() => {
                  a.onClick?.();
                  closePopup();
                }}
              >
                {a.label}
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
