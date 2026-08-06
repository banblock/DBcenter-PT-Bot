/**
 * 권한 정책 — 로그인 없는 역할 기반 모델.
 *
 * ## 전제
 * 이 시스템은 폐쇄망 관제실 단말에서만 쓰이며 **로그인 화면을 두지 않는다**.
 * 따라서 여기서 하는 일은 인증이 아니라 **오조작 방지**다. 역할은 로컬에
 * 저장되고 사용자가 직접 바꿀 수 있다 — 즉 보안 경계가 아니다.
 *
 * 실제 보안 경계는 망 분리와 물리적 접근 통제다. 이 시스템이 외부망에 노출되는
 * 순간 이 모델은 무효가 되며, 반드시 서버 측 인증을 붙여야 한다.
 *
 * ## 백엔드와의 관계
 * 백엔드 `app/security.py` 의 ROLE_PERMISSIONS 와 **반드시 동일하게 유지**한다.
 * 프론트는 버튼을 숨기고(1차), 백엔드는 X-Role 헤더로 한 번 더 막는다(2차).
 */

export const ROLES = ["VIEWER", "OPERATOR", "ADMIN"] as const;
export type Role = (typeof ROLES)[number];

export const PERMISSIONS = [
  "VIEW",                  // 대시보드·이력·통계 조회
  "ROBOT_CONTROL",         // 순찰 시작/정지, goto, 도킹
  "EMERGENCY_STOP",        // 긴급정지
  "EVENT_HANDLE",          // ACK·급파·재검증·종결
  "EVENT_REVIEW",          // 오탐 마킹 등 사람 검수
  "SUPPRESSION_REQUEST",   // 진압 요청
  "SUPPRESSION_APPROVE",   // 진압 승인/중단
  "MASTER_EDIT",           // 맵·노드·경로·설비·룰 CRUD
  "CONFIG_EDIT",           // 진압 모드 등 시스템 설정
] as const;
export type Permission = (typeof PERMISSIONS)[number];

export const ROLE_PERMISSIONS: Record<Role, readonly Permission[]> = {
  // 긴급정지는 뷰어에게도 연다. 안전 기능을 권한으로 막으면 사고가 커진다.
  VIEWER: ["VIEW", "EMERGENCY_STOP"],
  OPERATOR: [
    "VIEW",
    "EMERGENCY_STOP",
    "ROBOT_CONTROL",
    "EVENT_HANDLE",
    "EVENT_REVIEW",
    "SUPPRESSION_REQUEST",
  ],
  ADMIN: [...PERMISSIONS],
};

export const ROLE_LABEL: Record<Role, string> = {
  VIEWER: "뷰어",
  OPERATOR: "운영자",
  ADMIN: "관리자",
};

export const ROLE_DESCRIPTION: Record<Role, string> = {
  VIEWER: "조회 전용 — 긴급정지만 가능",
  OPERATOR: "순찰·급파·이벤트 대응",
  ADMIN: "설정·마스터 데이터·진압 승인 포함 전체",
};

export function can(role: Role, permission: Permission): boolean {
  return ROLE_PERMISSIONS[role]?.includes(permission) ?? false;
}

export function canAll(role: Role, permissions: readonly Permission[]): boolean {
  return permissions.every((p) => can(role, p));
}

export function canAny(role: Role, permissions: readonly Permission[]): boolean {
  return permissions.some((p) => can(role, p));
}

export function isRole(value: unknown): value is Role {
  return typeof value === "string" && (ROLES as readonly string[]).includes(value);
}

/** 라우트별 요구 권한. 없는 경로는 VIEW 만 있으면 들어갈 수 있다. */
export const ROUTE_PERMISSIONS: Record<string, Permission> = {
  "/": "VIEW",
  "/equipment": "VIEW",
  "/stats": "VIEW",
  "/history": "VIEW",
  "/suppression": "SUPPRESSION_REQUEST",
  "/settings": "CONFIG_EDIT",
};
