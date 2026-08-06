/** 설비 점검(align) 결과 화면 — 체크리스트 F-63, F-64. */

import { useState } from "react";
import { ClipboardList, RefreshCw } from "lucide-react";
import { CHECK_STATE_LABEL, SEVERITY_LABEL } from "../constants/dashboard";
import { equipmentApi, type Equipment } from "../services/api";
import { useAsync } from "../hooks/useAsync";
import { useZones } from "../store/facilityStore";
import { Badge, Button, Card, EmptyState, ErrorState, Skeleton } from "../components/ui";

const CHECK_TONE: Record<string, "neutral" | "info" | "ok" | "warn" | "danger"> = {
  NORMAL: "ok",
  MISMATCH: "danger",
  RECHECK: "warn",
  UNREADABLE: "warn",
  STALE: "neutral",
  SCANNING: "info",
  VERIFYING: "info",
  UNKNOWN: "neutral",
};

export function EquipmentPage() {
  const zones = useZones();
  const [zoneId, setZoneId] = useState("");
  const [type, setType] = useState("");

  const { data, loading, error, reload } = useAsync<Equipment[]>(
    () => equipmentApi.list({ zone_id: zoneId || undefined, type: type || undefined }),
    [zoneId, type],
  );

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h2 className="page__title">설비 점검 결과</h2>
          <p className="page__subtitle">
            관측 상태 · 기준 상태 · 작업지시 상태 · 판정을 나란히 비교합니다 (F-64)
          </p>
        </div>
        <div className="page__actions">
          <Button variant="ghost" icon={<RefreshCw size={14} />} onClick={reload} loading={loading}>
            새로고침
          </Button>
        </div>
      </div>

      <Card
        title="설비 목록"
        icon={<ClipboardList size={16} />}
        hint={data ? `${data.length}건` : undefined}
        flush
      >
        <div className="filter-bar" style={{ padding: "12px 16px" }}>
          <select value={zoneId} onChange={(e) => setZoneId(e.target.value)} aria-label="구역 필터">
            <option value="">전체 구역</option>
            {zones.map((zone) => (
              <option key={zone.zone_id} value={zone.zone_id}>
                {zone.zone_id} · {zone.name}
              </option>
            ))}
          </select>
          <select value={type} onChange={(e) => setType(e.target.value)} aria-label="설비 유형 필터">
            <option value="">전체 유형</option>
            <option value="BREAKER">차단기</option>
            <option value="LOCK">잠금장치</option>
            <option value="BATTERY_PANEL">배터리 패널</option>
            <option value="VALVE">밸브</option>
          </select>
        </div>

        {loading && !data ? (
          <div style={{ padding: 16 }}>
            <Skeleton count={6} height={18} />
          </div>
        ) : error ? (
          <ErrorState description={error} onRetry={reload} />
        ) : !data || data.length === 0 ? (
          <EmptyState
            title="설비가 없습니다"
            description="필터를 바꾸거나 설비 마스터를 등록하세요"
          />
        ) : (
          <div className="data-table__scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>설비 ID</th>
                  <th>이름</th>
                  <th>유형</th>
                  <th>구역</th>
                  <th>관측 상태</th>
                  <th>기준 상태</th>
                  <th className="data-table__numeric">측정값</th>
                  <th>판정</th>
                  <th>점검 상태</th>
                  <th>최종 점검</th>
                </tr>
              </thead>
              <tbody>
                {data.map((item) => (
                  <tr key={item.equipment_id}>
                    <td>
                      <code>{item.equipment_id}</code>
                    </td>
                    <td>{item.name}</td>
                    <td>{item.type}</td>
                    <td>{item.zone_id ?? "—"}</td>
                    <td>{item.last_observed_state ?? "—"}</td>
                    <td>{item.normal_state ?? "—"}</td>
                    <td className="data-table__numeric">
                      {item.last_value != null ? `${item.last_value}${item.unit ?? ""}` : "—"}
                    </td>
                    <td>
                      {item.last_verdict ? (
                        <Badge tone={item.last_verdict === "MISMATCH" ? "danger" : "ok"}>
                          {item.last_verdict}
                          {item.last_severity ? ` · ${SEVERITY_LABEL[item.last_severity as never] ?? item.last_severity}` : ""}
                        </Badge>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td>
                      <Badge tone={CHECK_TONE[item.check_state] ?? "neutral"}>
                        {CHECK_STATE_LABEL[item.check_state] ?? item.check_state}
                      </Badge>
                    </td>
                    <td>
                      {item.last_checked_at
                        ? new Date(item.last_checked_at).toLocaleString("ko-KR")
                        : "—"}
                    </td>
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
