"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { FormEvent, useEffect, useState } from "react";

import { useAuth } from "@/components/auth-provider";
import { ShieldIcon } from "@/components/icons";
import {
  Brand,
  ErrorNotice,
  Spinner,
  fieldClass,
  primaryButtonClass,
} from "@/components/ui";
import type { UserRole } from "@/lib/types";

type Mode = "login" | "register";

export function AuthScreen() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const { ready, user, login, register } = useAuth();
  const [mode, setMode] = useState<Mode>(
    searchParams.get("mode") === "register" ? "register" : "login",
  );
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<UserRole>("STUDENT");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    if (ready && user) router.replace("/dashboard");
  }, [ready, router, user]);

  function switchMode(nextMode: Mode) {
    setMode(nextMode);
    setError(null);
    const url = new URL(window.location.href);
    url.searchParams.set("mode", nextMode);
    window.history.replaceState(null, "", `${url.pathname}${url.search}`);
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (mode === "register") {
        await register(email, password, role);
      } else {
        await login(email, password);
      }
      router.replace("/dashboard");
    } catch (nextError) {
      setError(nextError);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="relative min-h-screen overflow-hidden bg-[#f3f1ea] text-[#172523]">
      <div aria-hidden="true" className="page-aura" />
      <header className="relative z-10 mx-auto flex h-20 max-w-7xl items-center justify-between px-5 sm:px-8 lg:px-10">
        <Brand />
        <Link
          className="text-sm font-medium text-[#53615e] transition hover:text-[#102b2a]"
          href="/"
        >
          返回产品介绍
        </Link>
      </header>

      <div className="relative z-10 mx-auto grid max-w-6xl items-center gap-12 px-5 pb-16 pt-7 sm:px-8 lg:grid-cols-[0.92fr_1.08fr] lg:px-10 lg:pb-24 lg:pt-14">
        <section className="hidden lg:block">
          <p className="section-label">Evidence-first learning</p>
          <h1 className="mt-5 max-w-lg text-5xl leading-[1.05] font-semibold tracking-[-0.05em]">
            在课程证据与教师审核的边界内学习。
          </h1>
          <p className="mt-6 max-w-lg text-base leading-8 text-[#5e6b68]">
            教师治理资料、知识关系与题目；学生只在已加入课程中获得带引用的回答与可解释学习建议。
          </p>
          <div className="mt-9 flex max-w-lg gap-3 rounded-2xl border border-[#2f6961]/12 bg-[#e8f0ec]/70 p-4 text-sm leading-6 text-[#36534f]">
            <ShieldIcon className="mt-0.5 size-5 shrink-0 text-[#2f6961]" />
            <p>课程资料证据不足时，系统明确拒答，不会用模型常识填补空白。</p>
          </div>
        </section>

        <section className="mx-auto w-full max-w-lg rounded-[28px] border border-white/80 bg-[#fcfbf7]/92 p-5 shadow-[0_30px_80px_rgba(32,50,47,0.14)] backdrop-blur sm:p-8">
          <div className="grid grid-cols-2 rounded-xl bg-[#ebeae3] p-1" role="tablist">
            <button
              aria-selected={mode === "login"}
              className={`rounded-lg px-4 py-2.5 text-sm font-semibold transition ${
                mode === "login"
                  ? "bg-white text-[#173f3b] shadow-sm"
                  : "text-[#6a7572] hover:text-[#273b38]"
              }`}
              onClick={() => switchMode("login")}
              role="tab"
              type="button"
            >
              登录
            </button>
            <button
              aria-selected={mode === "register"}
              className={`rounded-lg px-4 py-2.5 text-sm font-semibold transition ${
                mode === "register"
                  ? "bg-white text-[#173f3b] shadow-sm"
                  : "text-[#6a7572] hover:text-[#273b38]"
              }`}
              onClick={() => switchMode("register")}
              role="tab"
              type="button"
            >
              注册
            </button>
          </div>

          <div className="mt-7">
            <p className="text-[11px] font-bold tracking-[0.14em] text-[#7a5a2f] uppercase">
              {mode === "login" ? "Welcome back" : "Create account"}
            </p>
            <h2 className="mt-2 text-2xl font-semibold tracking-[-0.035em]">
              {mode === "login" ? "继续你的课程" : "创建 CoursePilot 账户"}
            </h2>
            <p className="mt-2 text-sm leading-6 text-[#687370]">
              {mode === "login"
                ? "登录后进入与你角色对应的工作台。"
                : "角色创建后不能自行切换，请按实际身份选择。"}
            </p>
          </div>

          <form className="mt-7 space-y-5" onSubmit={handleSubmit}>
            <label className="block">
              <span className="mb-2 block text-sm font-semibold">邮箱</span>
              <input
                autoComplete="email"
                className={fieldClass}
                disabled={busy}
                inputMode="email"
                onChange={(event) => setEmail(event.target.value)}
                placeholder="name@example.com"
                required
                type="email"
                value={email}
              />
            </label>

            <label className="block">
              <span className="mb-2 block text-sm font-semibold">密码</span>
              <input
                autoComplete={mode === "login" ? "current-password" : "new-password"}
                className={fieldClass}
                disabled={busy}
                minLength={mode === "register" ? 8 : 1}
                onChange={(event) => setPassword(event.target.value)}
                placeholder={mode === "register" ? "至少 8 个字符" : "输入密码"}
                required
                type="password"
                value={password}
              />
            </label>

            {mode === "register" ? (
              <fieldset>
                <legend className="mb-2 text-sm font-semibold">账户角色</legend>
                <div className="grid gap-3 sm:grid-cols-2">
                  {(
                    [
                      ["STUDENT", "学生", "加入课程并学习"],
                      ["TEACHER", "教师", "建课并治理内容"],
                    ] as const
                  ).map(([value, label, hint]) => (
                    <label
                      className={`cursor-pointer rounded-xl border p-3.5 transition ${
                        role === value
                          ? "border-[#2f6961] bg-[#e9f1ed]"
                          : "border-[#172523]/12 bg-white hover:border-[#2f6961]/35"
                      }`}
                      key={value}
                    >
                      <span className="flex items-center gap-2">
                        <input
                          checked={role === value}
                          className="accent-[#2f6961]"
                          disabled={busy}
                          name="role"
                          onChange={() => setRole(value)}
                          type="radio"
                        />
                        <span className="text-sm font-semibold">{label}</span>
                      </span>
                      <span className="mt-1 block pl-5 text-xs text-[#727c79]">
                        {hint}
                      </span>
                    </label>
                  ))}
                </div>
              </fieldset>
            ) : null}

            <ErrorNotice error={error} />

            <button className={`${primaryButtonClass} w-full`} disabled={busy} type="submit">
              {busy ? (
                <Spinner label={mode === "login" ? "正在登录" : "正在创建账户"} />
              ) : mode === "login" ? (
                "登录工作台"
              ) : (
                "注册并进入工作台"
              )}
            </button>
          </form>

          <p className="mt-5 text-center text-xs leading-5 text-[#7b8582]">
            登录状态保存在此浏览器；退出登录会撤销刷新令牌并清除本地状态。
          </p>
        </section>
      </div>
    </main>
  );
}
