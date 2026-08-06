/** 이상 이벤트 이력 조회 — 체크리스트 F-47~F-49. */

import { useState } from "react";
import { Activity, Download, Search } from "lucide-react";
import {
  EVENT_STATUS_LABEL,
  EVENT_STATUS_TONE,
  EVENT_TYPE_LABEL,
  SEVERITY_LABEL,
} from "../constants/dashboard";
import { eventApi, type EventDetail } from "../services/api";
import { useAsync } from "../hooks/useAsync";
import { useZones } from "../store/facilityStore";
import { EVENT_TYPES, SEVERITIES, EVENT_STATUSES } from "../services/ws/messages";
import type { EventPayload } from "../services/ws/messages";
import { Badge, Button, Card, EmptyState, ErrorState, Modal, Skeleton } from "../components/ui";

const PAGE_SIZE = 50;

interface Filters {
  from: string;
  to: string;
  zone_id: string;
  type: string;
  severity: string;
  status: string;
}

const EMPTY_FILTERS: Filters = { from: "", to: "", zone_id: "", type: "", severity: "", status: "" };

export function HistoryPage() {
  const zones = useZones();
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [applied, setApplied] = useState<Filters>(EMPTY_FILTERS);
  const [page, setPage] = useState(1);
  const [detailId, setDetailId] = useState<string | null>(null);

  const clean = (f: Filters) =>
    Object.fromEntries(Object.entries(f).filter(([, v]) => v !== "")) as Record<string, string>;

  const listing = useAsync(
    () => eventApi.query({ ...clean(applied), page, size: PAGE_SIZE }),
    [applied, page],
  );

  const detail = useAsync<EventDetail | null>(
    () => (detailId ? eventApi.get(detailId) : Promise.resolve(null)),
    [detailId],
  );

  const totalPages = listing.data ? Math.max(1, Math.ceil(listing.data.count / PAGE_SIZE)) : 1;

  function applyFilters() {
    setPage(1);
    setApplied(filters);
  }

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h2 className="page__title">이상 이벤트 이력</h2>
          <p className="page__subtitle">기간·구역·타입으로 검색하고 CSV 로 내보냅니다</p>
        </div>
        <div className="page__actions">
          <Button
            variant="ghost"
            icon={<Download size={14} />}
            onClick={() => eventApi.exportCsv(clean(applied))}
          >
            CSV 내보내기
          </Button>
        </div>
      </div>

      <Card title="검색 조건" icon={<Search size={16} />}>
        <div className="filter-bar">
          <label>
            <span className="sr-only">시작일</span>
            <input
              type="datetime-local"
              value={filters.from}
              onChange={(e) => setFilters({ ...filters, from: e.target.value })}
              aria-label="시작 일시"
            />
          </label>
          <label>
            <span className="sr-only">종료일</span>
            <input
              type="datetime-local"
              value={filters.to}
              onChange={(e) => setFilters({ ...filters, to: e.target.value })}
              aria-label="종료 일시"
            />
          </label>
          <select
            value={filters.zone_id}
            onChange={(e) => setFilters({ ...filters, zone_id: e.target.value })}
            aria-label="구역"
          >
            <option value="">전체 구역</option>
            {zones.map((z) => (
              <option key={z.zone_id} value={z.zone_id}>
                {z.zone_id} · {z.name}
              </option>
            ))}
          </select>
          <select
            value={filters.type}
            onChange={(e) => setFilters({ ...filters, type: e.target.value })}
            aria-label="이벤트 타입"
          >
            <option value="">전체 타입</option>
            {EVENT_TYPES.map((t) => (
              <option key={t} value={t}>
                {EVENT_TYPE_LABEL[t] ?? t}
              </option>
            ))}
          </select>
          <select
            value={filters.severity}
            onChange={(e) => setFilters({ ...filters, severity: e.target.value })}
            aria-label="심각도"
          >
            <option value="">전체 심각도</option>
            {SEVERITIES.map((s) => (
              <option key={s} value={s}>
                {SEVERITY_LABEL[s]}
              </option>
            ))}
          </select>
          <select
            value={filters.status}
            onChange={(e) => setFilters({ ...filters, status: e.target.value })}
            aria-label="처리 상태"
          >
            <option value="">전체 상태</option>
            {EVENT_STATUSES.map((s) => (
              <option key={s} value={s}>
                {EVENT_STATUS_LABEL[s] ?? s}
              </option>
            ))}
          </select>
          <Button variant="primary" onClick={applyFilters} loading={listing.loading}>
            조회
          </Button>
          <Button
            variant="subtle"
            onClick={() => {
              setFilters(EMPTY_FILTERS);
              setApplied(EMPTY_FILTERS);
              setPage(1);
            }}
          >
            초기화
          </Button>
        </div>
      </Card>

      <Card
        title="검색 결과"
        icon={<Activity size={16} />}
        hint={listing.data ? `${listing.data.count.toLocaleString()}건` : undefined}
        flush
      >
        {listing.loading && !listing.data ? (
          <div style={{ padding: 16 }}>
            <Skeleton count={8} height={18} />
          </div>
        ) : listing.error ? (
          <ErrorState description={listing.error} onRetry={listing.reload} />
        ) : !listing.data || listing.data.items.length === 0 ? (
          <EmptyState title="조건에 맞는 이벤트가 없습니다" />
        ) : (
          <>
            <div className="data-table__scroll">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>이벤트 ID</th>
                    <th>감지 시각</th>
                    <th>출처</th>
                    <th>타입</th>
                    <th>심각도</th>
                    <th>구역</th>
                    <th className="data-table__numeric">신뢰도</th>
                    <th className="data-table__numeric">중복</th>
                    <th>담당 로봇</th>
                    <th>상태</th>
                  </tr>
                </thead>
                <tbody>
                  {listing.data.items.map((event: EventPayload) => (
                    <tr
                      key={event.event_id}
                      onClick={() => setDetailId(event.event_id)}
                      style={{ cursor: "pointer" }}
                    >
                      <td>
                        <code>{event.event_id}</code>
                      </td>
                      <td>
                        {event.detected_at
                          ? new Date(event.detected_at).toLocaleString("ko-KR")
                          : "—"}
                      </td>
                      <td>{event.source ?? "—"}</td>
                      <td>{EVENT_TYPE_LABEL[event.type] ?? event.type}</td>
                      <td>
                        <Badge
                          tone={
                            event.severity === "CRITICAL"
                              ? "danger"
                              : event.severity === "WARN"
                                ? "warn"
                                : "neutral"
                          }
                        >
                          {SEVERITY_LABEL[event.severity]}
                        </Badge>
                      </td>
                      <td>{event.zone_id ?? "—"}</td>
                      <td className="data-table__numeric">
                        {event.confidence != null ? event.confidence.toFixed(2) : "—"}
                      </td>
                      <td className="data-table__numeric">{event.hit_count ?? 1}</td>
                      <td>{event.assigned_robot_id ?? "—"}</td>
                      <td>
                        <Badge tone={EVENT_STATUS_TONE[event.status] ?? "neutral"}>
                          {EVENT_STATUS_LABEL[event.status] ?? event.status}
                        </Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                gap: 12,
                padding: 12,
              }}
            >
              <Button size="sm" variant="ghost" disabled={page <= 1} onClick={() => setPage(page - 1)}>
                이전
              </Button>
              <span style={{ fontSize: "var(--fs-xs)", color: "var(--ink-muted)" }}>
                {page} / {totalPages}
              </span>
              <Button
                size="sm"
                variant="ghost"
                disabled={page >= totalPages}
                onClick={() => setPage(page + 1)}
              >
                다음
              </Button>
            </div>
          </>
        )}
      </Card>

      {/* 이력 상세 모달 — 감지 → 급파 → 검증 → 확정 → 진압 → 종결 타임라인 (F-47) */}
      <Modal
        open={detailId !== null}
        title={`이벤트 상세 · ${detailId ?? ""}`}
        size="lg"
        onClose={() => setDetailId(null)}
      >
        {detail.loading ? (
          <Skeleton count={6} height={18} />
        ) : detail.error ? (
          <ErrorState description={detail.error} onRetry={detail.reload} />
        ) : detail.data ? (
          <>
            <h4 style={{ margin: "0 0 8px" }}>타임라인</h4>
            {detail.data.timeline.length === 0 ? (
              <p style={{ color: "var(--ink-muted)" }}>기록된 단계가 없습니다.</p>
            ) : (
              <ol style={{ paddingLeft: 18, display: "grid", gap: 6 }}>
                {detail.data.timeline.map((step, index) => (
                  <li key={index}>
                    <b>{step.stage}</b>{" "}
                    <span style={{ color: "var(--ink-faint)" }}>
                      {new Date(step.at).toLocaleTimeString("ko-KR")}
                    </span>
                    {step.actor && <> · {step.actor}</>}
                    {step.detail && <> — {step.detail}</>}
                  </li>
                ))}
              </ol>
            )}

            <h4 style={{ margin: "20px 0 8px" }}>
              근거 이미지 ({detail.data.media.length}장)
            </h4>
            {detail.data.media.length === 0 ? (
              <p style={{ color: "var(--ink-muted)" }}>저장된 이미지가 없습니다.</p>
            ) : (
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                {detail.data.media.map((media) => (
                  <img
                    key={media.media_id}
                    src={media.uri}
                    alt={`각도 ${media.angle_idx ?? "-"}`}
                    style={{ width: 160, borderRadius: 8, border: "1px solid var(--border)" }}
                  />
                ))}
              </div>
            )}

            <h4 style={{ margin: "20px 0 8px" }}>
              다각도 관측 ({detail.data.observations.length}건)
            </h4>
            {detail.data.observations.length === 0 ? (
              <p style={{ color: "var(--ink-muted)" }}>관측 기록이 없습니다.</p>
            ) : (
              <table className="data-table">
                <thead>
                  <tr>
                    <th>각도</th>
                    <th>관측 상태</th>
                    <th className="data-table__numeric">값</th>
                    <th className="data-table__numeric">신뢰도</th>
                    <th>로봇</th>
                  </tr>
                </thead>
                <tbody>
                  {detail.data.observations.map((obs, index) => (
                    <tr key={index}>
                      <td>{String(obs.angle_idx)}</td>
                      <td>{String(obs.observed_state)}</td>
                      <td className="data-table__numeric">{obs.value != null ? String(obs.value) : "—"}</td>
                      <td className="data-table__numeric">{String(obs.confidence)}</td>
                      <td>{String(obs.robot_id ?? "—")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </>
        ) : null}
      </Modal>
    </div>
  );
}
