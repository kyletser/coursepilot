"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { useAuth } from "@/components/auth-provider";
import { StudentCourseTabs } from "@/components/student-course-tabs";
import { TeacherCourseTabs } from "@/components/teacher-course-tabs";
import {
  ErrorNotice,
  Spinner,
  StatusPill,
  formatDate,
  secondaryButtonClass,
} from "@/components/ui";
import { apiRequest } from "@/lib/api";
import type { Course } from "@/lib/types";

type TeacherTab = "overview" | "documents" | "graph" | "quizzes" | "students";
type StudentTab = "overview" | "chat" | "quiz" | "mastery" | "path";
type WorkspaceTab = TeacherTab | StudentTab;

const teacherTabs: Array<{ id: TeacherTab; label: string }> = [
  { id: "overview", label: "课程概览" },
  { id: "documents", label: "资料管理" },
  { id: "graph", label: "图谱审核" },
  { id: "quizzes", label: "题库审核" },
  { id: "students", label: "学生列表" },
];

const studentTabs: Array<{ id: StudentTab; label: string }> = [
  { id: "overview", label: "课程概览" },
  { id: "chat", label: "课程问答" },
  { id: "quiz", label: "诊断测验" },
  { id: "mastery", label: "掌握度" },
  { id: "path", label: "学习路径" },
];

export function CourseWorkspace() {
  const params = useParams<{ courseId: string }>();
  const courseId = params.courseId;
  const { user } = useAuth();
  const [course, setCourse] = useState<Course | null>(null);
  const [activeTab, setActiveTab] = useState<WorkspaceTab>("overview");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  const loadCourse = useCallback(async () => {
    if (!user || !courseId) return;
    setLoading(true);
    setError(null);
    try {
      setCourse(await apiRequest<Course>(`/courses/${courseId}`));
    } catch (nextError) {
      setError(nextError);
    } finally {
      setLoading(false);
    }
  }, [courseId, user]);

  useEffect(() => {
    // Route changes require a fresh permission-checked course resource.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadCourse();
  }, [loadCourse]);

  const isTeacher = user?.role === "TEACHER";
  const tabs = useMemo(() => (isTeacher ? teacherTabs : studentTabs), [isTeacher]);

  return (
    <AppShell
      description={course ? `${course.code} · ${course.semester}` : "读取课程信息与权限状态"}
      eyebrow={isTeacher ? "Teacher course" : "Student course"}
      title={course?.name ?? "课程工作区"}
    >
      <Link
        className={`${secondaryButtonClass} mb-6 min-h-10 px-3.5 py-2`}
        href="/dashboard"
      >
        ← 返回课程列表
      </Link>

      {error ? <ErrorNotice error={error} onRetry={() => void loadCourse()} /> : null}

      {loading ? (
        <div className="grid min-h-72 place-items-center rounded-2xl border border-[#172523]/10 bg-white/45 text-sm text-[#687370]">
          <Spinner label="正在读取课程" />
        </div>
      ) : course ? (
        <>
          <section className="mb-6 grid gap-4 rounded-[22px] border border-[#172523]/10 bg-[#fbfaf6]/82 p-5 sm:grid-cols-[1fr_auto] sm:items-center sm:p-6">
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <span className="rounded-lg bg-[#e4ece8] px-2.5 py-1 font-mono text-xs font-bold text-[#31544f]">
                  {course.code}
                </span>
                <StatusPill value={course.status} />
              </div>
              <p className="mt-3 max-w-3xl text-sm leading-6 text-[#5f6c69]">
                {course.description || "暂无课程简介"}
              </p>
            </div>
            <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-xs sm:text-right">
              <div>
                <dt className="text-[#7a8582]">课程模板</dt>
                <dd className="mt-1 font-semibold text-[#334643]">
                  {course.template === "DATA_STRUCTURES"
                    ? "数据结构"
                    : course.template === "OPERATING_SYSTEMS"
                      ? "操作系统"
                      : course.template}
                </dd>
              </div>
              <div>
                <dt className="text-[#7a8582]">更新时间</dt>
                <dd className="mt-1 font-semibold text-[#334643]">
                  {formatDate(course.updated_at)}
                </dd>
              </div>
            </dl>
          </section>

          <div className="mb-7 overflow-x-auto border-b border-[#172523]/11" role="tablist">
            <div className="flex min-w-max gap-1">
              {tabs.map((tab) => (
                <button
                  aria-controls={`panel-${tab.id}`}
                  aria-selected={activeTab === tab.id}
                  className={`min-h-11 border-b-2 px-4 py-3 text-sm font-semibold transition ${
                    activeTab === tab.id
                      ? "border-[#2f6961] text-[#173f3b]"
                      : "border-transparent text-[#6c7774] hover:border-[#2f6961]/25 hover:text-[#28433f]"
                  }`}
                  id={`tab-${tab.id}`}
                  key={tab.id}
                  onClick={() => setActiveTab(tab.id)}
                  role="tab"
                  type="button"
                >
                  {tab.label}
                </button>
              ))}
            </div>
          </div>

          <section
            aria-labelledby={`tab-${activeTab}`}
            id={`panel-${activeTab}`}
            role="tabpanel"
            tabIndex={0}
          >
            {isTeacher ? (
              <TeacherCourseTabs
                activeTab={activeTab as TeacherTab}
                course={course}
              />
            ) : (
              <StudentCourseTabs
                activeTab={activeTab as StudentTab}
                course={course}
              />
            )}
          </section>
        </>
      ) : null}
    </AppShell>
  );
}
