/** 통계 · 순찰 우선순위 화면 — 체크리스트 F-69~F-74. */

import { useState } from "react";
import { BarChart3, RefreshCw, TrendingUp } from "lucide-react";
import { priorityApi, statsApi, type EventStats, type PriorityNode } from "../services/api";
import { useAsync } from "../hooks/useAsync";
import { Badge, Button, Card, EmptyState, ErrorState, PermissionGate, Skeleton } from "../components/ui";

type Window = "24h" | "7d" | "30d";
type GroupBy = "zone" | "type" | "day";

export function StatsPage() {
  const [window, setWindow] = useState<Window>("7d");
  const [groupBy, setGroupBy] = useState<GroupBy>("zone");

  const stats = useAsync<EventStats>(() => statsApi.events({ group_by: groupBy }), [groupBy]);
  const priority = useAsync<PriorityNode[]>(() => priorityApi.nodes(window), [window]);

  async function recalculate() {
    await priorityApi.recalculate(window);
    priority.reload();
  }

  const maxCount = Math.max(1, ...(stats.data?.groups.map((g) => g.count) ?? [1]));

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h2 className="page__title">통계 · 순찰 우선순위</h2>
          <p className="page__subtitle">이벤트 발생 분포와 노드별 감시 우선순위 점수</p>
        </div>
        <div className="page__actions">
          <select
            value={window}
            onChange={(e) => setWindow(e.target.value as Window)}
            aria-label="집계 기간"
            className="filter-bar"
          >
            <option value="24h">최근 24시간</option>
            <option value="7d">최근 7일</option>
            <option value="30d">최근 30일</option>
          </select>
          <Button
            variant="ghost"
            icon={<RefreshCw size={14} />}
            onClick={() => {
              stats.reload();
              priority.reload();
            }}
          >
            새로고침
          </Button>
        </div>
      </div>

      {/* 지표 카드 (F-71) */}
      {stats.loading && !stats.data ? (
        <Skeleton count={1} height={90} />
      ) : stats.error ? (
        <ErrorState description={stats.error} onRetry={stats.reload} />
      ) : stats.data ? (
        <div className="metric-grid">
          <Metric label="총 이벤트" value={stats.data.total.toLocaleString()} hint="선택 기간 누적" />
          <Metric
            label="오탐률"
            value={`${(stats.data.false_positive_rate * 100).toFixed(1)}%`}
            hint="FALSE_POSITIVE / 전체"
          />
          <Metric
            label="평균 도착 시간"
            value={`${stats.data.avg_response_sec.detect_to_arrive.toFixed(1)}초`}
            hint="감지 → 현장 도착"
          />
          <Metric
            label="평균 확정 시간"
            value={`${stats.data.avg_response_sec.detect_to_confirm.toFixed(1)}초`}
            hint="감지 → 판정 확정"
          />
        </div>
      ) : null}

      {/* 그룹별 분포 (F-69, F-70) */}
      <Card
        title="이벤트 분포"
        icon={<BarChart3 size={16} />}
        actions={
          <select
            value={groupBy}
            onChange={(e) => setGroupBy(e.target.value as GroupBy)}
            aria-label="집계 기준"
            style={{ font: "inherit", fontSize: "var(--fs-xs)", padding: "6px 10px" }}
          >
            <option value="zone">구역별</option>
            <option value="type">타입별</option>
            <option value="day">일자별</option>
          </select>
        }
      >
        {!stats.data || stats.data.groups.length === 0 ? (
          <EmptyState title="집계할 이벤트가 없습니다" />
        ) : (
          stats.data.groups.map((group) => (
            <div className="bar-row" key={String(group.key)}>
              <span>{group.key ?? "미분류"}</span>
              <span className="bar-row__track">
                <span
                  className="bar-row__fill"
                  style={{ width: `${(group.count / maxCount) * 100}%` }}
                />
              </span>
              <span className="data-table__numeric">{group.count}</span>
            </div>
          ))
        )}
      </Card>

      {/* 노드별 우선순위 랭킹 (F-72) */}
      <Card
        title="노드별 순찰 우선순위"
        icon={<TrendingUp size={16} />}
        hint={priority.data ? `${priority.data.length}개 노드` : undefined}
        actions={
          <PermissionGate permission="MASTER_EDIT" mode="disable">
            <Button size="sm" variant="ghost" onClick={recalculate}>
              재계산
            </Button>
          </PermissionGate>
        }
        flush
      >
        {priority.loading && !priority.data ? (
          <div style={{ padding: 16 }}>
            <Skeleton count={5} height={18} />
          </div>
        ) : priority.error ? (
          <ErrorState description={priority.error} onRetry={priority.reload} />
        ) : !priority.data || priority.data.length === 0 ? (
          <EmptyState title="순찰 노드가 없습니다" />
        ) : (
          <div className="data-table__scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>순위</th>
                  <th>노드</th>
                  <th>구역</th>
                  <th className="data-table__numeric">이벤트</th>
                  <th>최대 심각도</th>
                  <th className="data-table__numeric">마지막 순찰</th>
                  <th className="data-table__numeric">수동 가중치</th>
                  <th className="data-table__numeric">점수</th>
                  <th className="data-table__numeric">방문 배수</th>
                </tr>
              </thead>
              <tbody>
                {priority.data.map((node) => (
                  <tr key={node.node_id}>
                    <td>{node.rank}</td>
                    <td>
                      <code>{node.node_id}</code> {node.name}
                    </td>
                    <td>{node.zone_id ?? "—"}</td>
                    <td className="data-table__numeric">{node.event_count}</td>
                    <td>
                      {node.max_severity ? (
                        <Badge tone={node.max_severity === "CRITICAL" ? "danger" : "warn"}>
                          {node.max_severity}
                        </Badge>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="data-table__numeric">
                      {node.hours_since_last_patrol != null
                        ? `${node.hours_since_last_patrol.toFixed(1)}h`
                        : "미방문"}
                    </td>
                    <td className="data-table__numeric">{node.manual_weight.toFixed(2)}</td>
                    <td className="data-table__numeric">
                      <b>{node.score.toFixed(3)}</b>
                    </td>
                    <td className="data-table__numeric">×{node.visit_multiplier}</td>
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

function Metric({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="metric">
      <div className="metric__label">{label}</div>
      <div className="metric__value">{value}</div>
      {hint && <div className="metric__hint">{hint}</div>}
    </div>
  );
}
