"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { useAuth } from "@/components/auth-provider";
import { ArrowIcon, CopyIcon, PlusIcon, RefreshIcon } from "@/components/icons";
import {
  EmptyState,
  ErrorNotice,
  SectionHeading,
  Spinner,
  StatusPill,
  fieldClass,
  formatDate,
  primaryButtonClass,
  secondaryButtonClass,
} from "@/components/ui";
import { apiRequest, jsonBody } from "@/lib/api";
import type { Course, CreatedCourse, JoinedCourse } from "@/lib/types";

const courseTemplates = [
  {
    value: "DATA_STRUCTURES",
    label: "数据结构",
    code: "DS101",
    name: "数据结构",
  },
  {
    value: "OPERATING_SYSTEMS",
    label: "操作系统",
    code: "OS101",
    name: "操作系统",
  },
] as const;

type CourseForm = {
  template: string;
  code: string;
  name: string;
  description: string;
  semester: string;
};

const initialCourseForm: CourseForm = {
  template: "DATA_STRUCTURES",
  code: "DS101",
  name: "数据结构",
  description: "",
  semester: "",
};

export function Dashboard() {
  const { user } = useAuth();
  const [courses, setCourses] = useState<Course[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [courseForm, setCourseForm] = useState<CourseForm>(initialCourseForm);
  const [inviteCode, setInviteCode] = useState("");
  const [createdCourse, setCreatedCourse] = useState<CreatedCourse | null>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  const loadCourses = useCallback(async () => {
    if (!user) return;
    setLoading(true);
    setLoadError(null);
    try {
      setCourses(await apiRequest<Course[]>("/courses"));
    } catch (error) {
      setLoadError(error);
    } finally {
      setLoading(false);
    }
  }, [user]);

  useEffect(() => {
    // The authenticated user determines the server-filtered course list.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadCourses();
  }, [loadCourses]);

  async function createCourse(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setActionError(null);
    setCreatedCourse(null);
    try {
      const created = await apiRequest<CreatedCourse>("/courses", {
        method: "POST",
        ...jsonBody(courseForm),
      });
      setCourses((current) => [created, ...current]);
      setCreatedCourse(created);
      setCourseForm((current) => ({ ...initialCourseForm, semester: current.semester }));
    } catch (error) {
      setActionError(error);
    } finally {
      setBusy(false);
    }
  }

  async function joinCourse(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setActionError(null);
    try {
      const joined = await apiRequest<JoinedCourse>("/courses/join", {
        method: "POST",
        ...jsonBody({ invite_code: inviteCode.trim() }),
      });
      setCourses((current) => {
        const withoutDuplicate = current.filter((course) => course.id !== joined.course.id);
        return [joined.course, ...withoutDuplicate];
      });
      setInviteCode("");
    } catch (error) {
      setActionError(error);
    } finally {
      setBusy(false);
    }
  }

  async function copyInvite() {
    if (!createdCourse?.invite_code) return;
    await navigator.clipboard.writeText(createdCourse.invite_code);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  }

  const isTeacher = user?.role === "TEACHER";

  return (
    <AppShell
      description={
        isTeacher
          ? "创建课程、管理课程资料与审核入口。所有学生可见内容仍由服务端权限和审核状态控制。"
          : "通过邀请码加入课程，从课程资料证据出发进行问答、测验与个性化学习。"
      }
      eyebrow={isTeacher ? "Teacher workspace" : "Student workspace"}
      title={isTeacher ? "教师课程工作台" : "我的课程"}
    >
      <div className="grid gap-8 lg:grid-cols-[minmax(0,1.3fr)_minmax(300px,0.7fr)] lg:items-start">
        <section>
          <SectionHeading
            action={
              <button
                className={secondaryButtonClass}
                disabled={loading}
                onClick={() => void loadCourses()}
                type="button"
              >
                <RefreshIcon className="size-4" />
                刷新
              </button>
            }
            description={isTeacher ? "你拥有的课程" : "你已加入且仍有效的课程"}
            title="课程列表"
          />

          <div className="mt-6">
            {loadError ? <ErrorNotice error={loadError} onRetry={() => void loadCourses()} /> : null}
            {loading ? (
              <div className="grid min-h-44 place-items-center rounded-2xl border border-[#172523]/10 bg-white/45 text-sm text-[#687370]">
                <Spinner label="正在读取课程" />
              </div>
            ) : courses.length ? (
              <div className="grid gap-4 sm:grid-cols-2">
                {courses.map((course) => (
                  <Link
                    className="group rounded-[22px] border border-[#172523]/10 bg-[#fbfaf6]/82 p-5 shadow-[0_14px_36px_rgba(32,50,47,0.06)] transition hover:-translate-y-0.5 hover:border-[#2f6961]/28 hover:shadow-[0_18px_44px_rgba(32,50,47,0.1)]"
                    href={`/courses/${course.id}`}
                    key={course.id}
                  >
                    <div className="flex items-start justify-between gap-4">
                      <span className="rounded-lg bg-[#e4ece8] px-2.5 py-1 font-mono text-[11px] font-bold text-[#31544f]">
                        {course.code}
                      </span>
                      <StatusPill value={course.status} />
                    </div>
                    <h3 className="mt-5 text-xl font-semibold tracking-[-0.03em]">
                      {course.name}
                    </h3>
                    <p className="mt-2 line-clamp-2 min-h-12 text-sm leading-6 text-[#687370]">
                      {course.description || "暂无课程简介"}
                    </p>
                    <div className="mt-5 flex items-center justify-between border-t border-[#172523]/9 pt-4 text-xs text-[#74807d]">
                      <span>{course.semester}</span>
                      <span className="inline-flex items-center gap-1 font-semibold text-[#2f6961]">
                        进入课程
                        <ArrowIcon className="size-4 transition-transform group-hover:translate-x-0.5" />
                      </span>
                    </div>
                  </Link>
                ))}
              </div>
            ) : (
              <EmptyState
                description={
                  isTeacher
                    ? "使用右侧表单创建第一门课程，系统会返回一次可复制的邀请码。"
                    : "向教师获取有效邀请码，然后使用右侧表单加入课程。"
                }
                title={isTeacher ? "还没有课程" : "还没有加入课程"}
              />
            )}
          </div>
        </section>

        <aside className="rounded-[24px] border border-[#172523]/10 bg-[#fbfaf6]/88 p-5 shadow-[0_18px_50px_rgba(32,50,47,0.08)] sm:p-6 lg:sticky lg:top-24">
          <p className="section-label">{isTeacher ? "Create course" : "Join course"}</p>
          <h2 className="mt-2 text-xl font-semibold tracking-[-0.03em]">
            {isTeacher ? "创建课程" : "使用邀请码加入"}
          </h2>

          {isTeacher ? (
            <form className="mt-6 space-y-4" onSubmit={createCourse}>
              <label className="block">
                <span className="mb-1.5 block text-sm font-semibold">课程模板</span>
                <select
                  className={fieldClass}
                  disabled={busy}
                  onChange={(event) => {
                    const template = courseTemplates.find(
                      (item) => item.value === event.target.value,
                    );
                    if (template) {
                      setCourseForm((current) => ({
                        ...current,
                        template: template.value,
                        code: template.code,
                        name: template.name,
                      }));
                    }
                  }}
                  value={courseForm.template}
                >
                  {courseTemplates.map((template) => (
                    <option key={template.value} value={template.value}>
                      {template.label}
                    </option>
                  ))}
                </select>
              </label>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-1 xl:grid-cols-2">
                <label className="block">
                  <span className="mb-1.5 block text-sm font-semibold">课程代码</span>
                  <input
                    className={fieldClass}
                    disabled={busy}
                    maxLength={64}
                    onChange={(event) =>
                      setCourseForm((current) => ({ ...current, code: event.target.value }))
                    }
                    required
                    value={courseForm.code}
                  />
                </label>
                <label className="block">
                  <span className="mb-1.5 block text-sm font-semibold">学期</span>
                  <input
                    className={fieldClass}
                    disabled={busy}
                    maxLength={64}
                    onChange={(event) =>
                      setCourseForm((current) => ({
                        ...current,
                        semester: event.target.value,
                      }))
                    }
                    placeholder="例如 2026 秋"
                    required
                    value={courseForm.semester}
                  />
                </label>
              </div>
              <label className="block">
                <span className="mb-1.5 block text-sm font-semibold">显示名称</span>
                <input
                  className={fieldClass}
                  disabled={busy}
                  maxLength={255}
                  onChange={(event) =>
                    setCourseForm((current) => ({ ...current, name: event.target.value }))
                  }
                  required
                  value={courseForm.name}
                />
              </label>
              <label className="block">
                <span className="mb-1.5 block text-sm font-semibold">课程简介</span>
                <textarea
                  className={`${fieldClass} min-h-24 resize-y`}
                  disabled={busy}
                  maxLength={10000}
                  onChange={(event) =>
                    setCourseForm((current) => ({
                      ...current,
                      description: event.target.value,
                    }))
                  }
                  placeholder="说明课程范围与学习目标"
                  value={courseForm.description}
                />
              </label>
              <ErrorNotice error={actionError} />
              <button className={`${primaryButtonClass} w-full`} disabled={busy} type="submit">
                {busy ? <Spinner label="正在创建" /> : <><PlusIcon className="size-4" />创建课程</>}
              </button>
            </form>
          ) : (
            <form className="mt-6 space-y-4" onSubmit={joinCourse}>
              <label className="block">
                <span className="mb-1.5 block text-sm font-semibold">课程邀请码</span>
                <input
                  autoComplete="off"
                  className={`${fieldClass} font-mono`}
                  disabled={busy}
                  maxLength={256}
                  minLength={8}
                  onChange={(event) => setInviteCode(event.target.value)}
                  placeholder="CP-…"
                  required
                  value={inviteCode}
                />
              </label>
              <p className="text-xs leading-5 text-[#74807d]">
                邀请码可能有有效期。重复加入会返回原有选课记录，不会创建重复数据。
              </p>
              <ErrorNotice error={actionError} />
              <button className={`${primaryButtonClass} w-full`} disabled={busy} type="submit">
                {busy ? <Spinner label="正在加入" /> : "加入课程"}
              </button>
            </form>
          )}

          {createdCourse ? (
            <div className="mt-5 rounded-2xl border border-[#2f6961]/16 bg-[#e8f1ed] p-4">
              <p className="text-sm font-semibold text-[#234944]">课程已创建</p>
              <p className="mt-1 text-xs leading-5 text-[#57706b]">
                邀请码仅在创建或重置时展示，请安全发送给本课程学生。
              </p>
              <div className="mt-3 flex items-center gap-2 rounded-xl bg-white/70 p-2 pl-3">
                <code className="min-w-0 flex-1 overflow-hidden text-ellipsis text-xs font-semibold">
                  {createdCourse.invite_code}
                </code>
                <button
                  aria-label="复制邀请码"
                  className="inline-flex shrink-0 items-center gap-1 rounded-lg bg-[#173f3b] px-2.5 py-2 text-xs font-semibold text-white"
                  onClick={() => void copyInvite()}
                  type="button"
                >
                  <CopyIcon className="size-3.5" />
                  {copied ? "已复制" : "复制"}
                </button>
              </div>
              <p className="mt-2 text-[11px] text-[#6c7d79]">
                有效期至 {formatDate(createdCourse.invite_expires_at)}
              </p>
            </div>
          ) : null}
        </aside>
      </div>
    </AppShell>
  );
}
