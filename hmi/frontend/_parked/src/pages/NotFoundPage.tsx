import { Link, useRouteError } from "react-router-dom";
import { Compass } from "lucide-react";

export function NotFoundPage() {
  // 라우터 errorElement 로도 쓰이므로, 실제 예외가 있으면 함께 보여준다.
  const error = useRouteError() as { statusText?: string; message?: string } | undefined;
  const detail = error?.statusText ?? error?.message;

  return (
    <div className="ui-state" role="alert">
      <span className="ui-state__icon">
        <Compass size={30} />
      </span>
      <span className="ui-state__title">화면을 찾을 수 없습니다</span>
      <span className="ui-state__description">
        {detail ? detail : "주소를 확인하거나 관제 화면으로 돌아가세요."}
      </span>
      <span className="ui-state__action">
        <Link to="/" className="ui-button ui-button--primary ui-button--md">
          관제 화면으로
        </Link>
      </span>
    </div>
  );
}
