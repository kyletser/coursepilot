import Link from "next/link";

import { ArrowIcon, BookIcon, CheckIcon, ShieldIcon } from "@/components/icons";
import { Brand } from "@/components/ui";

const capabilities = [
  [BookIcon, "引用问答"],
  [CheckIcon, "掌握度诊断"],
  [ShieldIcon, "学习路径"],
] as const;

export default function Home() {
  return (
    <div className="relative min-h-screen overflow-hidden bg-[#f3f1ea] text-[#172523]">
      <a className="skip-link" href="#main-content">
        跳到主要内容
      </a>
      <div aria-hidden="true" className="page-aura" />

      <header className="relative z-20 border-b border-[#172523]/10">
        <div className="mx-auto flex min-h-18 max-w-7xl items-center justify-between gap-4 px-5 py-3 sm:px-8 lg:px-10">
          <Brand />
          <div className="flex items-center gap-2">
            <Link
              className="hidden min-h-11 items-center rounded-xl px-3.5 text-sm font-semibold text-[#38514d] hover:bg-white/55 sm:inline-flex"
              href="/auth?mode=login"
            >
              登录
            </Link>
            <Link
              className="inline-flex min-h-11 items-center gap-1 rounded-xl bg-[#102b2a] px-4 text-sm font-semibold text-[#f8f5ed] shadow-[0_8px_22px_rgba(16,43,42,0.16)] transition hover:bg-[#173f3b]"
              href="/auth?mode=register"
            >
              开始使用
              <ArrowIcon className="size-4" />
            </Link>
          </div>
        </div>
      </header>

      <main
        className="relative z-10 mx-auto grid min-h-[calc(100vh-72px)] max-w-7xl items-center gap-12 px-5 py-16 sm:px-8 lg:grid-cols-[1.1fr_0.9fr] lg:gap-20 lg:px-10"
        id="main-content"
      >
        <section>
          <p className="section-label">CoursePilot</p>
          <h1 className="mt-5 max-w-3xl text-[clamp(3rem,6.5vw,5.7rem)] font-semibold leading-[0.98] tracking-[-0.06em] text-balance">
            基于课程资料，
            <span className="text-[#2f6961]">问清楚，学明白。</span>
          </h1>
          <p className="mt-7 max-w-xl text-base leading-7 text-[#53615e] sm:text-lg">
            每个回答都有出处，每条学习建议都有依据。
          </p>
          <div className="mt-9 flex flex-col gap-3 sm:flex-row">
            <Link
              className="inline-flex min-h-12 items-center justify-center gap-2 rounded-xl bg-[#102b2a] px-5 text-sm font-semibold text-[#f8f5ed] shadow-[0_12px_30px_rgba(16,43,42,0.18)] transition hover:-translate-y-0.5 hover:bg-[#173f3b]"
              href="/auth?mode=register"
            >
              免费开始
              <ArrowIcon className="size-4" />
            </Link>
            <Link
              className="inline-flex min-h-12 items-center justify-center rounded-xl border border-[#172523]/15 bg-white/50 px-5 text-sm font-semibold text-[#243634] transition hover:bg-white/80"
              href="/auth?mode=login"
            >
              登录
            </Link>
          </div>
        </section>

        <aside className="foundation-panel rounded-[30px] border border-white/80 bg-[#fcfbf7]/88 p-5 shadow-[0_32px_90px_rgba(32,50,47,0.13)] backdrop-blur sm:p-7">
          <ul className="grid gap-3">
            {capabilities.map(([Icon, label]) => (
              <li
                className="flex items-center gap-4 rounded-2xl border border-[#172523]/8 bg-white/65 px-4 py-4"
                key={label}
              >
                <span className="grid size-11 shrink-0 place-items-center rounded-xl bg-[#e5eeea] text-[#2f6961]">
                  <Icon className="size-5" />
                </span>
                <span className="font-semibold">{label}</span>
              </li>
            ))}
          </ul>
          <p className="mt-5 text-center text-xs text-[#687370]">
            资料不足时，明确说明，不编答案。
          </p>
        </aside>
      </main>
    </div>
  );
}
