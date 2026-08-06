/**
 * 라우트 권한 가드.
 *
 * 권한이 없으면 대시보드로 되돌리지 않고 **이유를 보여준다**. 조용히 리다이렉트하면
 * "링크가 고장났다"고 오해하게 된다. 관제실에서는 왜 못 들어가는지가 즉시 보여야 한다.
 */

import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { ShieldAlert } from "lucide-react";
import { ROLE_LABEL, type Permission } from "../auth/permissions";
import { useUiStore } from "../store/uiStore";
import { usePermission } from "../components/ui";

export function RequirePermission({
  permission,
  children,
}: {
  permission: Permission;
  children: ReactNode;
}) {
  const allowed = usePermission(permission);
  const role = useUiStore((state) => state.role);

  if (allowed) return <>{children}</>;

  return (
    <div className="ui-state ui-state--error" role="alert" data-testid="permission-denied">
      <span className="ui-state__icon">
        <ShieldAlert size={28} />
      </span>
      <span className="ui-state__title">이 화면을 볼 권한이 없습니다</span>
      <span className="ui-state__description">
        현재 역할은 <b>{ROLE_LABEL[role]}</b> 이고, 이 화면에는 <code>{permission}</code> 권한이
        필요합니다. 헤더에서 역할을 바꾸거나 관리자에게 문의하세요.
      </span>
      <span className="ui-state__action">
        <Link to="/" className="ui-button ui-button--ghost ui-button--sm">
          관제 화면으로
        </Link>
      </span>
    </div>
  );
}
