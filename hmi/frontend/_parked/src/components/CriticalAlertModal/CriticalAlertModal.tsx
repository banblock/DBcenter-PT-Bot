/**
 * CRITICAL 전체 모달 (F-51~F-55).
 *
 * 화면 테두리 점멸 + 최상위 레이어 + (음소거가 아니면) 경고음.
 * 액션: [영상 확인] [AMR 급파] [오탐 처리] [ACK]
 *
 * 소리는 오디오 파일 없이 WebAudio 로 만든다. 관제 PC 에 에셋을 배포하지 않아도
 * 되고, 브라우저 자동재생 정책에 막히면 조용히 실패한다(화면 경보는 그대로).
 */

import { useCallback, useEffect } from "react";
import { AlertTriangle } from "lucide-react";
import { eventApi } from "../../services/api";
import { useEventStore } from "../../store/eventStore";
import { useUiStore } from "../../store/uiStore";
import { logActions } from "../../store/logStore";
import { Button, Modal } from "../ui";

/** 짧은 경고음 2회. 실패해도 예외를 밖으로 내보내지 않는다. */
function playAlertTone(): void {
  try {
    const AudioCtor =
      window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!AudioCtor) return;
    const ctx = new AudioCtor();
    const now = ctx.currentTime;
    for (const offset of [0, 0.35]) {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "square";
      osc.frequency.setValueAtTime(880, now + offset);
      gain.gain.setValueAtTime(0.06, now + offset);
      gain.gain.exponentialRampToValueAtTime(0.001, now + offset + 0.25);
      osc.connect(gain).connect(ctx.destination);
      osc.start(now + offset);
      osc.stop(now + offset + 0.26);
    }
    setTimeout(() => ctx.close().catch(() => undefined), 1200);
  } catch {
    /* 자동재생 차단 등 — 화면 경보만으로 진행한다 */
  }
}

export function CriticalAlertModal() {
  const alert = useUiStore((state) => state.criticalAlert);
  const dismiss = useUiStore((state) => state.dismissCriticalAlert);
  const muted = useUiStore((state) => state.alertMuted);
  const operator = useUiStore((state) => state.operator);
  const focusEvent = useUiStore((state) => state.focusEvent);
  const event = useEventStore((state) => (alert ? state.byId[alert.eventId] : undefined));

  useEffect(() => {
    if (alert && !muted) playAlertTone();
  }, [alert, muted]);

  const handleAck = useCallback(async () => {
    if (!alert) return;
    try {
      await eventApi.ack(alert.eventId, operator);
      logActions.info(operator, `${alert.eventId} 알림 확인(ACK)`);
    } finally {
      dismiss();
    }
  }, [alert, operator, dismiss]);

  const handleDispatch = useCallback(async () => {
    if (!alert) return;
    try {
      const result = await eventApi.dispatch(alert.eventId);
      logActions.info("PC1", `${alert.eventId} 급파 — ${result.assigned_robot_id}`);
      dismiss();
    } catch {
      /* 실패 토스트는 httpClient 가 띄운다. 모달은 열어 둔다. */
    }
  }, [alert, dismiss]);

  const handleFalsePositive = useCallback(async () => {
    if (!alert) return;
    try {
      await eventApi.review(alert.eventId, {
        reviewer: operator,
        verdict: "FALSE_POSITIVE",
        memo: "관제 화면에서 오탐 처리",
      });
      logActions.warn(operator, `${alert.eventId} 오탐 처리`);
      dismiss();
    } catch {
      /* 위와 동일 */
    }
  }, [alert, operator, dismiss]);

  if (!alert) return null;

  return (
    <Modal
      open
      critical
      title={alert.title}
      onClose={dismiss}
      disableScrimClose
      footer={
        <>
          <Button variant="subtle" onClick={handleFalsePositive}>
            오탐 처리
          </Button>
          <Button
            variant="ghost"
            onClick={() => {
              focusEvent(alert.eventId);
              dismiss();
            }}
          >
            지도에서 보기
          </Button>
          <Button variant="danger" onClick={handleDispatch}>
            AMR 급파
          </Button>
          <Button variant="primary" onClick={handleAck}>
            확인(ACK)
          </Button>
        </>
      }
    >
      <div style={{ display: "flex", gap: 16, alignItems: "flex-start" }}>
        {alert.thumbnailUrl ? (
          <img
            src={alert.thumbnailUrl}
            alt="탐지 스냅샷"
            style={{ width: 200, borderRadius: 10, border: "1px solid var(--border)" }}
          />
        ) : (
          <div
            style={{
              width: 200,
              height: 130,
              display: "grid",
              placeItems: "center",
              borderRadius: 10,
              background: "var(--surface-soft)",
              color: "var(--ink-faint)",
            }}
          >
            <AlertTriangle size={28} />
          </div>
        )}
        <dl style={{ margin: 0, display: "grid", gap: 6 }}>
          <Row label="이벤트" value={alert.eventId} />
          <Row label="구역" value={alert.zoneId ?? "미상"} />
          <Row label="위치" value={event?.node_id ?? "미상"} />
          <Row
            label="신뢰도"
            value={alert.confidence != null ? `${(alert.confidence * 100).toFixed(0)}%` : "—"}
          />
          <Row label="감지 시각" value={event?.detected_at ?? "—"} />
          <Row label="상태" value={event?.status ?? "—"} />
        </dl>
      </div>
      {alert.description && (
        <p style={{ marginTop: 16, color: "var(--ink-muted)" }}>{alert.description}</p>
      )}
    </Modal>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ display: "flex", gap: 10 }}>
      <dt style={{ color: "var(--ink-faint)", minWidth: 68 }}>{label}</dt>
      <dd style={{ margin: 0, fontWeight: 600 }}>{value}</dd>
    </div>
  );
}
