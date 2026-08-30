import { Suspense } from "react";

import { AuthScreen } from "@/components/auth-screen";
import { PageLoading } from "@/components/ui";

export default function AuthPage() {
  return (
    <Suspense fallback={<PageLoading label="正在准备登录页…" />}>
      <AuthScreen />
    </Suspense>
  );
}
