import Link from "next/link";

import { ArrowIcon, BookIcon, CheckIcon, ShieldIcon } from "@/components/icons";
import { Brand } from "@/components/ui";

const roleCards = [
  {
    role: "教师",
    eyebrow: "TEACHER",
    title: "把课程知识放进可治理的边界",
    description:
      "创建课程、上传资料并审核知识关系与题目。只有审核通过的内容才能进入学生学习链路。",
    items: ["课程与邀请码管理", "资料入库与审核入口", "学生名单与课程信号"],
    className: "border-[#c9d8d2] bg-[#edf4f0]",
  },
  {
    role: "学生",
    eyebrow: "STUDENT",
    title: "沿着证据找到下一步学习动作",
    description:
      "通过邀请码加入课程，获取带原文引用的回答，完成审核题并生成可解释的学习路径。",
    items: ["课程资料证据问答", "审核题诊断与掌握度", "前置关系学习路径"],
    className: "border-[#ddd3bf] bg-[#f8f3e8]",
  },
] as const;

const principles = [
  ["01", "证据优先", "课程结论绑定原文引用；资料不足时明确拒答。"],
  ["02", "教师治理", "知识关系与题目先审核，再影响学生学习状态。"],
  ["03", "课程隔离", "每次访问都在服务端校验用户、角色和课程归属。"],
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
          <nav aria-label="首页导航" className="hidden items-center gap-7 text-sm text-[#51605d] md:flex">
            <a className="transition-colors hover:text-[#102b2a]" href="#roles">
              双角色
            </a>
            <a className="transition-colors hover:text-[#102b2a]" href="#principles">
              可信边界
            </a>
          </nav>
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

      <main id="main-content">
        <section className="relative z-10 mx-auto grid max-w-7xl gap-12 px-5 pb-20 pt-14 sm:px-8 sm:pt-20 lg:grid-cols-[1.06fr_0.94fr] lg:items-center lg:gap-16 lg:px-10 lg:pb-28 lg:pt-24">
          <div>
            <div className="mb-7 inline-flex items-center gap-2 rounded-full border border-[#173f3b]/15 bg-white/55 px-3 py-1.5 text-xs font-semibold tracking-[0.1em] text-[#31514d] backdrop-blur">
              <span className="size-1.5 rounded-full bg-[#b77b2f]" />
              面向计算机课程的学习 Agent
            </div>
            <h1 className="max-w-3xl text-[clamp(2.75rem,6.3vw,5.35rem)] leading-[0.99] font-semibold tracking-[-0.055em] text-balance">
              把课程资料变成
              <span className="text-[#2f6961]">可以追溯</span>
              的学习依据。
            </h1>
            <p className="mt-7 max-w-2xl text-base leading-8 text-[#53615e] sm:text-lg">
              CoursePilot 以教师提供的资料为权威来源，连接带引用的课程问答、经审核的知识关系和可解释的个人学习路径。
            </p>
            <div className="mt-9 flex flex-col gap-3 sm:flex-row">
              <Link
                className="inline-flex min-h-12 items-center justify-center gap-2 rounded-xl bg-[#102b2a] px-5 text-sm font-semibold text-[#f8f5ed] shadow-[0_12px_30px_rgba(16,43,42,0.18)] transition hover:-translate-y-0.5 hover:bg-[#173f3b]"
                href="/auth?mode=register"
              >
                创建账户
                <ArrowIcon className="size-4" />
              </Link>
              <Link
                className="inline-flex min-h-12 items-center justify-center rounded-xl border border-[#172523]/15 bg-white/50 px-5 text-sm font-semibold text-[#243634] transition hover:bg-white/80"
                href="/auth?mode=login"
              >
                登录工作台
              </Link>
            </div>
            <div className="mt-9 flex max-w-xl items-start gap-3 border-t border-[#172523]/12 pt-5 text-sm leading-6 text-[#5c6865]">
              <ShieldIcon className="mt-0.5 size-5 shrink-0 text-[#2f6961]" />
              <p>没有充分课程证据时，系统明确说明资料不足，不用模型常识补齐答案。</p>
            </div>
          </div>

          <aside className="foundation-panel relative mx-auto w-full max-w-xl overflow-hidden rounded-[30px] border border-white/80 bg-[#fcfbf7]/88 p-5 shadow-[0_32px_90px_rgba(32,50,47,0.13)] backdrop-blur sm:p-6">
            <p className="section-label">Trust pipeline</p>
            <h2 className="mt-2 text-xl font-semibold tracking-[-0.025em]">从资料到学习动作</h2>
            <div className="mt-5 grid grid-cols-[1fr_auto_1fr_auto_1fr] items-center gap-2 rounded-2xl border border-[#172523]/8 bg-white/70 p-3 sm:p-4">
              {[
                [BookIcon, "课程资料"],
                [ShieldIcon, "证据校验"],
                [CheckIcon, "学习动作"],
              ].map(([Icon, label], index) => (
                <div className="contents" key={label as string}>
                  {index ? <span className="text-[#78827f]">→</span> : null}
                  <div className="text-center">
                    <span className="mx-auto grid size-10 place-items-center rounded-xl bg-[#e5eeea] text-[#2f6961]">
                      <Icon className="size-5" />
                    </span>
                    <p className="mt-2 text-xs font-semibold">{label as string}</p>
                  </div>
                </div>
              ))}
            </div>
            <dl className="mt-4 grid gap-3 sm:grid-cols-3">
              <div className="rounded-2xl border border-[#172523]/8 bg-white/60 p-4">
                <dt className="text-xs font-semibold text-[#66736f]">课程范围</dt>
                <dd className="mt-2 text-sm font-semibold leading-6">数据结构<br />操作系统</dd>
              </div>
              <div className="rounded-2xl border border-[#172523]/8 bg-white/60 p-4">
                <dt className="text-xs font-semibold text-[#66736f]">发布门槛</dt>
                <dd className="mt-2 text-sm font-semibold leading-6">教师审核<br />方可生效</dd>
              </div>
              <div className="rounded-2xl border border-[#172523]/8 bg-white/60 p-4">
                <dt className="text-xs font-semibold text-[#66736f]">回答规则</dt>
                <dd className="mt-2 text-sm font-semibold leading-6">有引用回答<br />无证据拒答</dd>
              </div>
            </dl>
          </aside>
        </section>

        <section className="relative z-10 border-y border-[#172523]/10 bg-[#faf9f5]/78" id="roles">
          <div className="mx-auto max-w-7xl px-5 py-18 sm:px-8 lg:px-10 lg:py-22">
            <div className="mb-9 max-w-2xl">
              <p className="section-label">Two roles, one boundary</p>
              <h2 className="mt-3 text-3xl font-semibold tracking-[-0.04em] sm:text-4xl">
                角色不同，可信规则一致。
              </h2>
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              {roleCards.map((card) => (
                <article className={`rounded-[24px] border p-6 sm:p-7 ${card.className}`} key={card.role}>
                  <span className="text-xs font-bold tracking-[0.12em] text-[#43605b]">{card.eyebrow}</span>
                  <h3 className="mt-5 text-xl font-semibold tracking-[-0.025em]">{card.title}</h3>
                  <p className="mt-3 text-sm leading-6 text-[#586663]">{card.description}</p>
                  <ul className="mt-5 space-y-2.5 border-t border-[#172523]/10 pt-5 text-sm text-[#334643]">
                    {card.items.map((item) => (
                      <li className="flex items-center gap-2" key={item}>
                        <CheckIcon className="size-4 shrink-0 text-[#2f6961]" />
                        {item}
                      </li>
                    ))}
                  </ul>
                </article>
              ))}
            </div>
          </div>
        </section>

        <section className="relative z-10 mx-auto max-w-7xl px-5 py-18 sm:px-8 lg:px-10 lg:py-22" id="principles">
          <div className="border-b border-[#172523]/12 pb-8">
            <p className="section-label">Non-negotiable principles</p>
            <h2 className="mt-3 text-3xl font-semibold tracking-[-0.04em] sm:text-4xl">
              智能能力始终留在可信边界内。
            </h2>
          </div>
          <div className="grid gap-px overflow-hidden rounded-[24px] border border-[#172523]/10 bg-[#172523]/10 md:grid-cols-3">
            {principles.map(([index, title, description]) => (
              <article className="bg-[#f8f7f2] p-7" key={index}>
                <span className="font-mono text-xs text-[#8f6129]">{index}</span>
                <h3 className="mt-10 text-xl font-semibold">{title}</h3>
                <p className="mt-3 text-sm leading-6 text-[#606c69]">{description}</p>
              </article>
            ))}
          </div>
        </section>

        <section className="relative z-10 bg-[#102b2a] text-[#f8f5ed]">
          <div className="mx-auto flex max-w-7xl flex-col justify-between gap-7 px-5 py-14 sm:px-8 md:flex-row md:items-center lg:px-10">
            <div>
              <h2 className="text-2xl font-semibold tracking-[-0.035em] sm:text-3xl">进入 CoursePilot 工作台</h2>
              <p className="mt-2 text-sm leading-6 text-[#c1d0cc]">按你的真实身份创建账户，角色权限由服务端持续校验。</p>
            </div>
            <Link
              className="inline-flex min-h-12 shrink-0 items-center justify-center gap-2 rounded-xl bg-[#f5f1e7] px-5 text-sm font-semibold text-[#102b2a]"
              href="/auth?mode=register"
            >
              开始使用
              <ArrowIcon className="size-4" />
            </Link>
          </div>
        </section>
      </main>

      <footer className="relative z-10 bg-[#0c2423] text-[#b5c5c1]">
        <div className="mx-auto flex max-w-7xl flex-col gap-2 border-t border-white/8 px-5 py-7 text-xs sm:flex-row sm:items-center sm:justify-between sm:px-8 lg:px-10">
          <p>CoursePilot · 证据优先的课程学习 Agent</p>
          <p>不展示未验证指标</p>
        </div>
      </footer>
    </div>
  );
}
