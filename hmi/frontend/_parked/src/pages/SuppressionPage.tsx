/**
 * 화재진압 제어 패널 — 체크리스트 F-57~F-62.
 *
 * 안전 화면이다. 두 가지를 반드시 지킨다.
 * 1. **이중 확인** — 구역 ID 를 직접 타이핑해야 요청 버튼이 활성화된다 (F-58).
 * 2. **인터락 결과를 숨기지 않는다** — 무엇이 막고 있는지 그대로 보여준다 (F-59).
 */

import { useState } from "react";
import { Flame, ShieldCheck, Siren } from "lucide-react";
import { suppressionApi } from "../services/api";
import { useAsync } from "../hooks/useAsync";
import { useActiveSuppressions, useZones } from "../store/facilityStore";
import { useUiStore } from "../store/uiStore";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
  PermissionGate,
  Skeleton,
} from "../components/ui";

const SEQUENCE = [
  "REQUESTED",
  "INTERLOCK_CHECK",
  "APPROVED",
  "POWER_CUTTING",
  "SPRINKLER_ON",
  "COMPLETED",
] as const;

const TERMINAL_TONE: Record<string, "ok" | "warn" | "danger" | "neutral"> = {
  COMPLETED: "ok",
  BLOCKED: "danger",
  FAILED: "danger",
  ABORTED: "warn",
};

export function SuppressionPage() {
  const zones = useZones();
  const operator = useUiStore((state) => state.operator);
  const live = useActiveSuppressions();

  const [zoneId, setZoneId] = useState("");
  const [actions, setActions] = useState<Array<"POWER_CUT" | "SPRINKLER">>(["POWER_CUT"]);
  const [duration, setDuration] = useState(60);
  const [confirmText, setConfirmText] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const logs = useAsync(() => suppressionApi.logs(), []);

  // 확인 문구가 구역 ID 와 정확히 같아야 통과 (백엔드도 동일하게 검사한다)
  const confirmed = zoneId !== "" && confirmText.trim() === zoneId;

  async function submit() {
    if (!confirmed) return;
    setSubmitting(true);
    try {
      await suppressionApi.request({
        zone_id: zoneId,
        actions,
        sprinkler_duration_sec: duration,
        requested_by: operator,
        confirm_text: confirmText.trim(),
      });
      setConfirmText("");
      logs.reload();
    } catch {
      /* 실패 토스트는 httpClient 가 띄운다 */
    } finally {
      setSubmitting(false);
    }
  }

  function toggleAction(action: "POWER_CUT" | "SPRINKLER") {
    setActions((prev) =>
      prev.includes(action) ? prev.filter((a) => a !== action) : [...prev, action],
    );
  }

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h2 className="page__title">화재진압 제어</h2>
          <p className="page__subtitle">
            전력 차단 · 스프링클러 작동은 안전 인터락을 통과해야만 진행됩니다
          </p>
        </div>
      </div>

      <Card title="진압 요청" icon={<Flame size={16} />}>
        <div style={{ display: "grid", gap: 16, maxWidth: 560 }}>
          <label style={{ display: "grid", gap: 6 }}>
            <span style={{ fontSize: "var(--fs-xs)", fontWeight: 700 }}>대상 구역</span>
            <select
              value={zoneId}
              onChange={(e) => {
                setZoneId(e.target.value);
                setConfirmText(""); // 구역을 바꾸면 확인 문구를 다시 받는다
              }}
              aria-label="대상 구역"
              style={{ font: "inherit", padding: "8px 10px" }}
            >
              <option value="">구역 선택</option>
              {zones.map((zone) => (
                <option key={zone.zone_id} value={zone.zone_id}>
                  {zone.zone_id} · {zone.name}
                </option>
              ))}
            </select>
          </label>

          <fieldset style={{ border: "1px solid var(--border)", borderRadius: 10, padding: 12 }}>
            <legend style={{ fontSize: "var(--fs-xs)", fontWeight: 700, padding: "0 6px" }}>
              실행 액션
            </legend>
            <label style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 8 }}>
              <input
                type="checkbox"
                checked={actions.includes("POWER_CUT")}
                onChange={() => toggleAction("POWER_CUT")}
              />
              전력 차단 (POWER_CUT)
            </label>
            <label style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <input
                type="checkbox"
                checked={actions.includes("SPRINKLER")}
                onChange={() => toggleAction("SPRINKLER")}
              />
              스프링클러 작동 (SPRINKLER)
            </label>
            {actions.includes("SPRINKLER") && (
              <label style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 10 }}>
                작동 시간
                <input
                  type="number"
                  min={1}
                  max={1800}
                  value={duration}
                  onChange={(e) => setDuration(Number(e.target.value))}
                  style={{ width: 90, font: "inherit", padding: "6px 8px" }}
                />
                초
              </label>
            )}
          </fieldset>

          {/* 이중 확인 (F-58) */}
          <label style={{ display: "grid", gap: 6 }}>
            <span style={{ fontSize: "var(--fs-xs)", fontWeight: 700, color: "var(--danger)" }}>
              확인 문구 — 대상 구역 ID 를 그대로 입력하세요
              {zoneId && <> (예: {zoneId})</>}
            </span>
            <input
              value={confirmText}
              onChange={(e) => setConfirmText(e.target.value)}
              placeholder={zoneId || "먼저 구역을 선택하세요"}
              disabled={!zoneId}
              aria-label="확인 문구"
              style={{
                font: "inherit",
                padding: "8px 10px",
                border: `1px solid ${confirmed ? "var(--ok)" : "var(--border-strong)"}`,
                borderRadius: 8,
              }}
            />
          </label>

          <PermissionGate
            permission="SUPPRESSION_REQUEST"
            mode="disable"
            fallback={<p>진압 요청 권한이 없습니다.</p>}
          >
            <Button
              variant="danger"
              size="lg"
              icon={<Siren size={16} />}
              disabled={!confirmed || actions.length === 0}
              loading={submitting}
              onClick={submit}
            >
              진압 요청
            </Button>
          </PermissionGate>
        </div>
      </Card>

      {/* 진행 상태 (F-59, F-60) */}
      <Card title="진행 중인 진압 시퀀스" icon={<ShieldCheck size={16} />}>
        {live.length === 0 ? (
          <EmptyState title="진행 중인 진압이 없습니다" />
        ) : (
          live.map((item) => {
            const currentIndex = SEQUENCE.indexOf(item.status as never);
            return (
              <div
                key={item.suppression_id}
                style={{
                  borderBottom: "1px solid var(--border)",
                  paddingBottom: 14,
                  marginBottom: 14,
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
                  <b>{item.suppression_id}</b>
                  <span style={{ color: "var(--ink-muted)" }}>{item.zone_id}</span>
                  <Badge tone={TERMINAL_TONE[item.status] ?? "info"}>{item.status}</Badge>
                  <PermissionGate permission="SUPPRESSION_APPROVE">
                    <span style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => suppressionApi.approve(item.suppression_id, operator)}
                        disabled={item.status !== "INTERLOCK_CHECK"}
                      >
                        승인
                      </Button>
                      <Button
                        size="sm"
                        variant="danger"
                        onClick={() =>
                          suppressionApi.abort(item.suppression_id, operator, "관제 화면에서 중단")
                        }
                      >
                        중단
                      </Button>
                    </span>
                  </PermissionGate>
                </div>

                {/* 단계 프로그레스 */}
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                  {SEQUENCE.map((step, index) => (
                    <Badge
                      key={step}
                      tone={
                        currentIndex < 0
                          ? "neutral"
                          : index < currentIndex
                            ? "ok"
                            : index === currentIndex
                              ? "warn"
                              : "neutral"
                      }
                    >
                      {step}
                    </Badge>
                  ))}
                </div>

                {/* 인터락 차단 사유 — 절대 숨기지 않는다 */}
                {item.interlock && !item.interlock.passed && (
                  <div
                    style={{
                      marginTop: 10,
                      padding: 10,
                      borderRadius: 8,
                      background: "var(--danger-soft)",
                      color: "var(--danger)",
                      fontSize: "var(--fs-xs)",
                    }}
                    role="alert"
                  >
                    <b>인터락 차단</b>
                    <ul style={{ marginTop: 6, paddingLeft: 18 }}>
                      {item.interlock.blockers.map((blocker, index) => (
                        <li key={index}>
                          [{blocker.code}] {blocker.detail}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            );
          })
        )}
      </Card>

      {/* 감사 로그 (B-70) */}
      <Card title="진압 감사 로그" icon={<Flame size={16} />} flush>
        {logs.loading && !logs.data ? (
          <div style={{ padding: 16 }}>
            <Skeleton count={4} height={18} />
          </div>
        ) : logs.error ? (
          <ErrorState description={logs.error} onRetry={logs.reload} />
        ) : !logs.data || logs.data.length === 0 ? (
          <EmptyState title="진압 이력이 없습니다" />
        ) : (
          <div className="data-table__scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>ID</th>
                  <th>구역</th>
                  <th>액션</th>
                  <th>요청자</th>
                  <th>승인자</th>
                  <th>상태</th>
                  <th>시작</th>
                  <th>종료</th>
                </tr>
              </thead>
              <tbody>
                {logs.data.map((row, index) => (
                  <tr key={index}>
                    <td>
                      <code>{String(row.suppression_id)}</code>
                    </td>
                    <td>{String(row.zone_id ?? "—")}</td>
                    <td>{Array.isArray(row.actions) ? row.actions.join(", ") : "—"}</td>
                    <td>{String(row.requested_by ?? "—")}</td>
                    <td>{String(row.approved_by ?? "—")}</td>
                    <td>
                      <Badge tone={TERMINAL_TONE[String(row.status)] ?? "info"}>
                        {String(row.status)}
                      </Badge>
                    </td>
                    <td>{row.started_at ? new Date(String(row.started_at)).toLocaleString("ko-KR") : "—"}</td>
                    <td>{row.ended_at ? new Date(String(row.ended_at)).toLocaleString("ko-KR") : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
