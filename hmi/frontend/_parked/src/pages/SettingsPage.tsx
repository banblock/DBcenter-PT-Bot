/** 시스템 설정 — 진압 모드 · 알림 · 테마 · 역할. */

import { Settings, Volume2, VolumeX } from "lucide-react";
import { configApi } from "../services/api";
import { useAsync } from "../hooks/useAsync";
import { useFacilityStore } from "../store/facilityStore";
import { useUiStore } from "../store/uiStore";
import { ROLES, ROLE_DESCRIPTION, ROLE_LABEL, ROLE_PERMISSIONS, type Role } from "../auth/permissions";
import { Badge, Button, Card, ErrorState, Skeleton } from "../components/ui";

export function SettingsPage() {
  const config = useAsync(() => configApi.get(), []);
  const suppressionMode = useFacilityStore((state) => state.suppressionMode);
  const setSuppressionMode = useFacilityStore((state) => state.setSuppressionMode);

  const theme = useUiStore((state) => state.theme);
  const setTheme = useUiStore((state) => state.setTheme);
  const role = useUiStore((state) => state.role);
  const setRole = useUiStore((state) => state.setRole);
  const operator = useUiStore((state) => state.operator);
  const setOperator = useUiStore((state) => state.setOperator);
  const muted = useUiStore((state) => state.alertMuted);
  const setAlertMuted = useUiStore((state) => state.setAlertMuted);

  async function changeMode(mode: "MANUAL" | "AUTO") {
    await configApi.setSuppressionMode(mode);
    setSuppressionMode(mode);
    config.reload();
  }

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h2 className="page__title">시스템 설정</h2>
          <p className="page__subtitle">서버 설정과 이 브라우저의 표시 설정</p>
        </div>
      </div>

      {/* 서버 설정 */}
      <Card title="진압 모드" icon={<Settings size={16} />}>
        <p style={{ color: "var(--ink-muted)", marginBottom: 12 }}>
          <b>MANUAL</b> — 인터락 통과 후에도 관리자 승인이 있어야 진행합니다.
          <br />
          <b>AUTO</b> — 인터락만 통과하면 자동 진행합니다. 인원 확인 절차가 갖춰진 현장에서만
          사용하세요.
        </p>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <Button
            variant={suppressionMode === "MANUAL" ? "primary" : "ghost"}
            onClick={() => changeMode("MANUAL")}
          >
            MANUAL
          </Button>
          <Button
            variant={suppressionMode === "AUTO" ? "danger" : "ghost"}
            onClick={() => changeMode("AUTO")}
          >
            AUTO
          </Button>
          <Badge tone={suppressionMode === "AUTO" ? "danger" : "ok"}>현재: {suppressionMode}</Badge>
        </div>
      </Card>

      <Card title="서버 설정값 (읽기 전용)" icon={<Settings size={16} />}>
        {config.loading && !config.data ? (
          <Skeleton count={5} height={16} />
        ) : config.error ? (
          <ErrorState description={config.error} onRetry={config.reload} />
        ) : config.data ? (
          <table className="data-table">
            <tbody>
              {Object.entries(config.data).map(([key, value]) => (
                <tr key={key}>
                  <td style={{ color: "var(--ink-muted)" }}>{key}</td>
                  <td>
                    <code>{JSON.stringify(value)}</code>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
      </Card>

      {/* 브라우저 설정 */}
      <Card title="이 브라우저 설정" icon={<Settings size={16} />}>
        <div style={{ display: "grid", gap: 20, maxWidth: 640 }}>
          <div>
            <div style={{ fontWeight: 700, marginBottom: 6 }}>테마</div>
            <div style={{ display: "flex", gap: 8 }}>
              <Button variant={theme === "light" ? "primary" : "ghost"} onClick={() => setTheme("light")}>
                라이트
              </Button>
              <Button variant={theme === "dark" ? "primary" : "ghost"} onClick={() => setTheme("dark")}>
                다크 (관제실 권장)
              </Button>
            </div>
          </div>

          <div>
            <div style={{ fontWeight: 700, marginBottom: 6 }}>알림음</div>
            <Button
              variant="ghost"
              icon={muted ? <VolumeX size={15} /> : <Volume2 size={15} />}
              onClick={() => setAlertMuted(!muted)}
            >
              {muted ? "음소거 해제" : "음소거"}
            </Button>
          </div>

          <div>
            <div style={{ fontWeight: 700, marginBottom: 6 }}>조작자 이름</div>
            <input
              value={operator}
              onChange={(e) => setOperator(e.target.value)}
              aria-label="조작자 이름"
              style={{
                font: "inherit",
                padding: "8px 10px",
                border: "1px solid var(--border-strong)",
                borderRadius: 8,
                minWidth: 220,
              }}
            />
            <p style={{ color: "var(--ink-faint)", fontSize: "var(--fs-xs)", marginTop: 6 }}>
              ACK·검수·진압 요청 기록에 이 이름이 남습니다.
            </p>
          </div>

          <div>
            <div style={{ fontWeight: 700, marginBottom: 6 }}>역할</div>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              {ROLES.map((r) => (
                <Button key={r} variant={role === r ? "primary" : "ghost"} onClick={() => setRole(r as Role)}>
                  {ROLE_LABEL[r]}
                </Button>
              ))}
            </div>
            <p style={{ color: "var(--ink-muted)", fontSize: "var(--fs-xs)", marginTop: 8 }}>
              {ROLE_DESCRIPTION[role]}
            </p>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 8 }}>
              {ROLE_PERMISSIONS[role].map((permission) => (
                <Badge key={permission} tone="neutral">
                  {permission}
                </Badge>
              ))}
            </div>
            <p
              style={{
                color: "var(--warn)",
                fontSize: "var(--fs-xs)",
                marginTop: 10,
                lineHeight: 1.5,
              }}
            >
              ⚠ 이 시스템은 로그인을 사용하지 않습니다. 역할 선택은 <b>오조작 방지</b>용이며 보안
              경계가 아닙니다. 실제 보호는 망 분리와 물리적 접근 통제에 의존합니다.
            </p>
          </div>
        </div>
      </Card>
    </div>
  );
}
