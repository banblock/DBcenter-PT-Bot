import { Skeleton } from "../components/ui";

/** lazy 로딩 중 표시. 레이아웃이 튀지 않도록 실제 페이지와 비슷한 형태를 잡는다. */
export function PageFallback() {
  return (
    <div className="page" data-testid="page-fallback">
      <div className="page__header">
        <Skeleton width={220} height={26} />
      </div>
      <div className="panel" style={{ padding: 16 }}>
        <Skeleton count={8} height={18} />
      </div>
    </div>
  );
}
