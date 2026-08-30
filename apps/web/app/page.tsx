const roleTracks = [
  {
    id: "teacher",
    eyebrow: "TEACHER",
    title: "把课程知识，放进可治理的边界",
    description:
      "教师负责课程、资料与发布决策。AI 提出的知识关系和题目，都要经过审核才能影响学生。",
    capabilities: ["创建课程与管理邀请", "审核知识关系与诊断题", "查看课程级学习信号"],
    cardClass: "border-[#c9d8d2] bg-[#edf4f0]",
    badgeClass: "bg-[#173f3b] text-[#f8f5ed]",
  },
  {
    id: "student",
    eyebrow: "STUDENT",
    title: "沿着证据，找到下一步学习动作",
    description:
      "学生在已加入的课程内提问、测验和规划学习。每个课程结论都应能回到原始资料。",
    capabilities: ["通过邀请码加入课程", "获取带原文引用的回答", "用审核题更新个人掌握度"],
    cardClass: "border-[#ddd3bf] bg-[#f8f3e8]",
    badgeClass: "bg-[#aa7430] text-white",
  },
] as const;

const principles = [
  {
    index: "01",
    title: "证据优先",
    description: "课程结论绑定原文引用；资料不足时明确拒答。",
  },
  {
    index: "02",
    title: "教师治理",
    description: "候选知识、关系与题目先审核，再进入学生学习链路。",
  },
  {
    index: "03",
    title: "课程隔离",
    description: "每次访问都校验用户、角色、课程与资源归属。",
  },
] as const;

function ArrowUpRightIcon() {
  return (
    <svg
      aria-hidden="true"
      className="size-4"
      fill="none"
      viewBox="0 0 16 16"
    >
      <path
        d="M4 12 12 4m0 0H5.5M12 4v6.5"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.5"
      />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg
      aria-hidden="true"
      className="mt-0.5 size-4 shrink-0"
      fill="none"
      viewBox="0 0 16 16"
    >
      <path
        d="m3.5 8.25 2.75 2.75 6.25-6.5"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.5"
      />
    </svg>
  );
}

function BookIcon() {
  return (
    <svg
      aria-hidden="true"
      className="size-5"
      fill="none"
      viewBox="0 0 20 20"
    >
      <path
        d="M4.25 3.5h7.25a2 2 0 0 1 2 2v11H6.25a2 2 0 0 1-2-2v-11Z"
        stroke="currentColor"
        strokeLinejoin="round"
        strokeWidth="1.5"
      />
      <path
        d="M6.25 16.5a2 2 0 0 1 2-2h7.5v-9h-2.25M7.25 7h3.5M7.25 10h3.5"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.5"
      />
    </svg>
  );
}

function ShieldIcon() {
  return (
    <svg
      aria-hidden="true"
      className="size-5"
      fill="none"
      viewBox="0 0 20 20"
    >
      <path
        d="M10 2.75 16 5v4.25c0 3.65-2.38 6.72-6 8-3.62-1.28-6-4.35-6-8V5l6-2.25Z"
        stroke="currentColor"
        strokeLinejoin="round"
        strokeWidth="1.5"
      />
      <path
        d="m7.25 9.75 1.75 1.75 3.75-4"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.5"
      />
    </svg>
  );
}

export default function Home() {
  return (
    <main className="relative min-h-screen overflow-hidden bg-[#f3f1ea] text-[#172523]">
      <div aria-hidden="true" className="page-aura" />

      <header className="relative z-20 border-b border-[#172523]/10">
        <div className="mx-auto flex h-18 max-w-7xl items-center justify-between px-5 sm:px-8 lg:px-10">
          <a
            aria-label="CoursePilot 首页"
            className="group flex items-center gap-3"
            href="#top"
          >
            <span className="grid size-9 place-items-center rounded-[12px] bg-[#102b2a] text-[#f8f5ed] shadow-[0_8px_24px_rgba(16,43,42,0.18)] transition-transform group-hover:-rotate-2">
              <BookIcon />
            </span>
            <span className="text-[17px] font-semibold tracking-[-0.02em]">
              CoursePilot
            </span>
          </a>

          <nav
            aria-label="页面导航"
            className="hidden items-center gap-7 text-sm text-[#51605d] md:flex"
          >
            <a className="transition-colors hover:text-[#102b2a]" href="#roles">
              角色
            </a>
            <a
              className="transition-colors hover:text-[#102b2a]"
              href="#principles"
            >
              可信边界
            </a>
            <a className="transition-colors hover:text-[#102b2a]" href="#status">
              当前进度
            </a>
          </nav>

          <a
            className="inline-flex items-center gap-2 rounded-full border border-[#173f3b]/20 bg-white/55 px-4 py-2 text-sm font-medium text-[#173f3b] backdrop-blur transition-colors hover:bg-white"
            href="/healthz"
          >
            <span className="status-dot" />
            Web 健康状态
          </a>
        </div>
      </header>

      <section
        className="relative z-10 mx-auto grid max-w-7xl gap-12 px-5 pb-22 pt-16 sm:px-8 sm:pt-22 lg:grid-cols-[1.08fr_0.92fr] lg:items-center lg:gap-16 lg:px-10 lg:pb-28 lg:pt-24"
        id="top"
      >
        <div>
          <div className="mb-7 inline-flex items-center gap-2 rounded-full border border-[#173f3b]/15 bg-white/50 px-3 py-1.5 text-[11px] font-semibold tracking-[0.14em] text-[#31514d] uppercase backdrop-blur">
            <span className="size-1.5 rounded-full bg-[#b77b2f]" />
            MVP · Foundation
          </div>

          <h1 className="max-w-3xl text-[clamp(2.75rem,6.4vw,5.4rem)] leading-[0.98] font-semibold tracking-[-0.055em] text-balance">
            把课程资料，变成
            <span className="text-[#2f6961]">可以追溯</span>
            的学习依据。
          </h1>

          <p className="mt-7 max-w-2xl text-base leading-7 text-[#53615e] sm:text-lg sm:leading-8">
            CoursePilot 面向计算机专业课程，以教师资料为唯一权威来源。
            它连接带引用的问答、经审核的知识关系，以及可解释的个人学习路径。
          </p>

          <div className="mt-9 flex flex-col gap-3 sm:flex-row">
            <a
              className="inline-flex items-center justify-center gap-2 rounded-full bg-[#102b2a] px-5 py-3 text-sm font-semibold text-[#f8f5ed] shadow-[0_12px_30px_rgba(16,43,42,0.18)] transition-all hover:-translate-y-0.5 hover:bg-[#173f3b]"
              href="#roles"
            >
              了解双角色入口
              <ArrowUpRightIcon />
            </a>
            <a
              className="inline-flex items-center justify-center gap-2 rounded-full border border-[#172523]/15 bg-white/45 px-5 py-3 text-sm font-semibold text-[#243634] transition-colors hover:bg-white/80"
              href="#status"
            >
              查看当前基础
            </a>
          </div>

          <div className="mt-10 flex max-w-xl items-start gap-3 border-t border-[#172523]/12 pt-5 text-sm leading-6 text-[#5c6865]">
            <span className="mt-0.5 text-[#2f6961]">
              <ShieldIcon />
            </span>
            <p>
              没有充分课程证据时，系统明确说明资料不足，不用模型常识补齐答案。
            </p>
          </div>
        </div>

        <aside
          aria-label="CoursePilot 当前基础范围"
          className="foundation-panel relative mx-auto w-full max-w-xl overflow-hidden rounded-[30px] border border-white/80 bg-[#fcfbf7]/86 p-4 shadow-[0_32px_90px_rgba(32,50,47,0.13)] backdrop-blur sm:p-6"
        >
          <div className="absolute inset-x-12 top-0 h-px bg-gradient-to-r from-transparent via-[#b77b2f]/55 to-transparent" />

          <div className="flex items-center justify-between px-1 pb-5">
            <div>
              <p className="text-[10px] font-bold tracking-[0.18em] text-[#78827f] uppercase">
                Trust pipeline
              </p>
              <h2 className="mt-1 text-base font-semibold tracking-[-0.02em]">
                从资料到学习动作
              </h2>
            </div>
            <span className="rounded-full border border-[#2f6961]/15 bg-[#e6f0eb] px-3 py-1 text-xs font-semibold text-[#2f6961]">
              范围已锁定
            </span>
          </div>

          <div className="relative grid grid-cols-[1fr_auto_1fr_auto_1fr] items-center gap-2 rounded-2xl border border-[#172523]/8 bg-white/65 p-3 sm:p-4">
            <div className="text-center">
              <span className="mx-auto grid size-9 place-items-center rounded-xl bg-[#f0ede3] text-[#4f615e]">
                <BookIcon />
              </span>
              <p className="mt-2 text-xs font-semibold">课程资料</p>
            </div>
            <span className="text-[#9ba4a1]">→</span>
            <div className="text-center">
              <span className="mx-auto grid size-9 place-items-center rounded-xl bg-[#e3efea] text-[#2f6961]">
                <ShieldIcon />
              </span>
              <p className="mt-2 text-xs font-semibold">证据校验</p>
            </div>
            <span className="text-[#9ba4a1]">→</span>
            <div className="text-center">
              <span className="mx-auto grid size-9 place-items-center rounded-xl bg-[#f2e9d9] text-[#9b6629]">
                <CheckIcon />
              </span>
              <p className="mt-2 text-xs font-semibold">学习动作</p>
            </div>
          </div>

          <div className="mt-4 grid gap-3 sm:grid-cols-3">
            <div className="rounded-2xl border border-[#172523]/8 bg-white/55 p-4">
              <p className="text-[10px] font-bold tracking-[0.14em] text-[#89918f] uppercase">
                Course scope
              </p>
              <p className="mt-2 text-sm font-semibold leading-5">
                数据结构
                <br />
                操作系统
              </p>
            </div>
            <div className="rounded-2xl border border-[#172523]/8 bg-white/55 p-4">
              <p className="text-[10px] font-bold tracking-[0.14em] text-[#89918f] uppercase">
                Publish gate
              </p>
              <p className="mt-2 text-sm font-semibold leading-5">
                教师审核
                <br />
                方可生效
              </p>
            </div>
            <div className="rounded-2xl border border-[#172523]/8 bg-white/55 p-4">
              <p className="text-[10px] font-bold tracking-[0.14em] text-[#89918f] uppercase">
                Answer rule
              </p>
              <p className="mt-2 text-sm font-semibold leading-5">
                有引用回答
                <br />
                无证据拒答
              </p>
            </div>
          </div>

          <div className="mt-4 rounded-2xl bg-[#102b2a] p-4 text-[#f8f5ed] sm:p-5">
            <div className="flex items-center justify-between gap-4">
              <div>
                <p className="text-[10px] font-semibold tracking-[0.16em] text-[#b9cbc6] uppercase">
                  Foundation scope
                </p>
                <p className="mt-1 text-sm font-medium">
                  身份、课程、邀请与统一契约
                </p>
              </div>
              <span className="shrink-0 rounded-full bg-white/10 px-3 py-1 text-xs text-[#dbe5e2]">
                  已建立
              </span>
            </div>
          </div>
        </aside>
      </section>

      <section
        className="relative z-10 border-y border-[#172523]/10 bg-[#faf9f5]/75"
        id="roles"
      >
        <div className="mx-auto max-w-7xl px-5 py-20 sm:px-8 lg:px-10 lg:py-26">
          <div className="grid gap-8 lg:grid-cols-[0.72fr_1.28fr] lg:gap-16">
            <div>
              <p className="section-label">Two roles, one boundary</p>
              <h2 className="mt-4 max-w-md text-3xl leading-tight font-semibold tracking-[-0.035em] sm:text-4xl">
                角色不同，可信规则一致。
              </h2>
              <p className="mt-5 max-w-md text-base leading-7 text-[#5f6b68]">
                前端入口会按角色组织任务，但权限始终由服务端校验。隐藏按钮不等于安全边界。
              </p>
            </div>

            <div className="grid gap-4 md:grid-cols-2">
              {roleTracks.map((role) => (
                <article
                  className={`rounded-[26px] border p-6 sm:p-7 ${role.cardClass}`}
                  key={role.id}
                >
                  <div className="flex items-center justify-between gap-3">
                    <span
                      className={`rounded-full px-3 py-1 text-[10px] font-bold tracking-[0.16em] ${role.badgeClass}`}
                    >
                      {role.eyebrow}
                    </span>
                    <span className="text-xs text-[#6b7572]">角色入口规划</span>
                  </div>
                  <h3 className="mt-6 text-xl leading-7 font-semibold tracking-[-0.025em]">
                    {role.title}
                  </h3>
                  <p className="mt-3 text-sm leading-6 text-[#586663]">
                    {role.description}
                  </p>
                  <ul className="mt-6 space-y-3 border-t border-[#172523]/10 pt-5 text-sm text-[#334643]">
                    {role.capabilities.map((capability) => (
                      <li className="flex items-start gap-2" key={capability}>
                        <CheckIcon />
                        <span>{capability}</span>
                      </li>
                    ))}
                  </ul>
                </article>
              ))}
            </div>
          </div>
        </div>
      </section>

      <section
        className="relative z-10 mx-auto max-w-7xl px-5 py-20 sm:px-8 lg:px-10 lg:py-26"
        id="principles"
      >
        <div className="flex flex-col justify-between gap-6 border-b border-[#172523]/12 pb-9 sm:flex-row sm:items-end">
          <div>
            <p className="section-label">Non-negotiable principles</p>
            <h2 className="mt-4 text-3xl font-semibold tracking-[-0.035em] sm:text-4xl">
              先建立可信边界，再扩展智能能力。
            </h2>
          </div>
          <p className="max-w-md text-sm leading-6 text-[#66716e]">
            CoursePilot 不替代教师，也不把模型输出直接当作课程事实。
          </p>
        </div>

        <div className="grid gap-px overflow-hidden rounded-[26px] border border-[#172523]/10 bg-[#172523]/10 md:grid-cols-3">
          {principles.map((principle) => (
            <article className="bg-[#f8f7f2] p-7 sm:p-8" key={principle.index}>
              <span className="font-mono text-xs text-[#a36f2e]">
                {principle.index}
              </span>
              <h3 className="mt-12 text-xl font-semibold tracking-[-0.02em]">
                {principle.title}
              </h3>
              <p className="mt-3 max-w-sm text-sm leading-6 text-[#606c69]">
                {principle.description}
              </p>
            </article>
          ))}
        </div>
      </section>

      <section
        className="relative z-10 border-t border-[#172523]/10 bg-[#102b2a] text-[#f8f5ed]"
        id="status"
      >
        <div className="mx-auto grid max-w-7xl gap-12 px-5 py-20 sm:px-8 lg:grid-cols-[0.78fr_1.22fr] lg:px-10 lg:py-24">
          <div>
            <p className="text-xs font-bold tracking-[0.18em] text-[#b8cbc6] uppercase">
              Current foundation
            </p>
            <h2 className="mt-5 max-w-lg text-3xl leading-tight font-semibold tracking-[-0.035em] sm:text-4xl">
              先把可信基础搭稳，再连续扩展核心能力。
            </h2>
            <p className="mt-5 max-w-lg text-sm leading-7 text-[#c4d2ce]">
              此启动页用于确认产品方向、双角色入口和可信边界。学生与教师工作台将随核心链路连续落地。
            </p>
          </div>

          <div className="divide-y divide-white/10 rounded-[26px] border border-white/12 bg-white/[0.04] px-5 sm:px-7">
            <div className="grid gap-2 py-5 sm:grid-cols-[120px_1fr_auto] sm:items-center">
              <span className="text-xs font-semibold text-[#8fb1a9]">已具备</span>
              <span className="text-sm font-medium">Web 应用骨架与健康检查</span>
              <span className="w-fit rounded-full bg-[#d9a441]/15 px-2.5 py-1 text-[11px] text-[#efc87d]">
                Foundation
              </span>
            </div>
            <div className="grid gap-2 py-5 sm:grid-cols-[120px_1fr_auto] sm:items-center">
              <span className="text-xs font-semibold text-[#8fb1a9]">核心闭环</span>
              <span className="text-sm font-medium">
                JWT、双角色、课程、邀请码与 Enrollment
              </span>
              <span className="w-fit rounded-full bg-white/8 px-2.5 py-1 text-[11px] text-[#c5d2cf]">
                Foundation
              </span>
            </div>
            <div className="grid gap-2 py-5 sm:grid-cols-[120px_1fr_auto] sm:items-center">
              <span className="text-xs font-semibold text-[#8fb1a9]">连续推进</span>
              <span className="text-sm font-medium">文档解析、切片与入库任务</span>
              <span className="w-fit rounded-full bg-white/8 px-2.5 py-1 text-[11px] text-[#c5d2cf]">
                Ingestion
              </span>
            </div>
          </div>
        </div>
      </section>

      <footer className="relative z-10 bg-[#0c2423] text-[#b5c5c1]">
        <div className="mx-auto flex max-w-7xl flex-col gap-3 border-t border-white/8 px-5 py-7 text-xs sm:flex-row sm:items-center sm:justify-between sm:px-8 lg:px-10">
          <p>CoursePilot · 证据优先的课程学习 Agent</p>
          <p>Foundation ready · 不展示未验证指标</p>
        </div>
      </footer>
    </main>
  );
}
