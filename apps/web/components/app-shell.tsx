"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { useAuth } from "@/components/auth-provider";
import { Brand, PageLoading } from "@/components/ui";

export function AppShell({
  eyebrow,
  title,
  description,
  children,
}: {
  eyebrow: string;
  title: string;
  description?: string;
  children: React.ReactNode;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const { ready, user, logout } = useAuth();
  const [loggingOut, setLoggingOut] = useState(false);

  useEffect(() => {
    if (ready && !user) {
      const returnTo = encodeURIComponent(pathname);
      router.replace(`/auth?mode=login&returnTo=${returnTo}`);
    }
  }, [pathname, ready, router, user]);

  if (!ready || !user) return <PageLoading />;

  async function handleLogout() {
    setLoggingOut(true);
    try {
      await logout();
    } finally {
      router.replace("/auth?mode=login");
      setLoggingOut(false);
    }
  }

  return (
    <main className="min-h-screen bg-[#f3f1ea] text-[#172523]">
      <header className="sticky top-0 z-40 border-b border-[#172523]/10 bg-[#f3f1ea]/90 backdrop-blur-xl">
        <div className="mx-auto flex min-h-18 max-w-7xl items-center justify-between gap-4 px-5 py-3 sm:px-8 lg:px-10">
          <div className="flex items-center gap-6">
            <Brand href="/dashboard" />
            <nav aria-label="应用导航" className="hidden sm:block">
              <Link
                className={`rounded-lg px-3 py-2 text-sm font-semibold transition ${
                  pathname === "/dashboard"
                    ? "bg-[#e3ebe7] text-[#173f3b]"
                    : "text-[#687370] hover:bg-white/60 hover:text-[#173f3b]"
                }`}
                href="/dashboard"
              >
                课程工作台
              </Link>
            </nav>
          </div>
          <div className="flex items-center gap-3">
            <div className="hidden text-right md:block">
              <p className="max-w-52 truncate text-xs font-semibold">{user.email}</p>
              <p className="mt-0.5 text-[10px] font-bold tracking-[0.1em] text-[#7a5a2f]">
                {user.role === "TEACHER" ? "TEACHER" : "STUDENT"}
              </p>
            </div>
            <button
              className="rounded-xl border border-[#172523]/13 bg-white/55 px-3.5 py-2 text-sm font-semibold text-[#475754] transition hover:bg-white disabled:opacity-50"
              disabled={loggingOut}
              onClick={handleLogout}
              type="button"
            >
              {loggingOut ? "退出中…" : "退出"}
            </button>
          </div>
        </div>
      </header>

      <div className="mx-auto max-w-7xl px-5 pb-20 pt-10 sm:px-8 lg:px-10 lg:pt-14">
        <header className="mb-9 border-b border-[#172523]/11 pb-8">
          <p className="section-label">{eyebrow}</p>
          <h1 className="mt-3 text-3xl font-semibold tracking-[-0.045em] sm:text-4xl">
            {title}
          </h1>
          {description ? (
            <p className="mt-3 max-w-3xl text-sm leading-7 text-[#63706d] sm:text-base">
              {description}
            </p>
          ) : null}
        </header>
        {children}
      </div>
    </main>
  );
}
