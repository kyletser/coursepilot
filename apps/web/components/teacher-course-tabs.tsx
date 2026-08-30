"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";

import {
  CheckIcon,
  CopyIcon,
  RefreshIcon,
  ShieldIcon,
  UploadIcon,
  XIcon,
} from "@/components/icons";
import {
  EmptyState,
  ErrorNotice,
  SectionHeading,
  Spinner,
  StatusPill,
  dangerButtonClass,
  formatDate,
  primaryButtonClass,
  secondaryButtonClass,
} from "@/components/ui";
import {
  BadCasesPanel,
  EvaluationPanel,
} from "@/components/teacher-governance-tabs";
import { apiRequest } from "@/lib/api";
import type {
  Course,
  DocumentRecord,
  GraphCandidate,
  GraphCandidates,
  Invite,
  QuizItem,
} from "@/lib/types";

export type TeacherCourseTab =
  | "overview"
  | "documents"
  | "graph"
  | "quizzes"
  | "students"
  | "bad-cases"
  | "evaluation";

type TeacherCourseTabsProps = {
  course: Course;
  activeTab: TeacherCourseTab;
};

type ReviewKind = "concepts" | "relations";
type ReviewDecision = "approve" | "reject";

type ReviewQuizItem = QuizItem & {
  answer?: string;
  correct_answer?: string;
  source_chunk_id?: string;
};

type StudentLearningSummary = {
  id: string;
  email: string;
  user_status: string;
  enrollment_id: string;
  enrollment_status: string;
  joined_at: string;
  quiz_attempt_count: number;
  quiz_correct_count: number;
  mastery_concept_count: number;
  average_mastery: number | null;
  weak_concept_count: number;
  last_assessed_at: string | null;
};

type WeakConceptSummary = {
  concept_id: string;
  concept_name: string;
  assessed_student_count: number;
  weak_student_count: number;
  average_mastery: number;
};

type CourseLearningSummary = {
  students: StudentLearningSummary[];
  weak_concepts: WeakConceptSummary[];
};

type PublishedIndex = {
  id: string;
  version: number;
  published_at?: string | null;
};

type ApprovedGraph = {
  published_index: PublishedIndex | null;
};

type PublishGraphResult = {
  index_id: string;
  version: number;
  status: string;
  published_at?: string | null;
};

function PanelCard({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={`rounded-[22px] border border-[#172523]/10 bg-[#fbfaf6]/82 p-5 shadow-[0_12px_35px_rgba(24,42,38,0.04)] sm:p-6 ${className}`}
    >
      {children}
    </div>
  );
}

function OverviewPanel({ course }: { course: Course }) {
  const [invite, setInvite] = useState<Invite | null>(null);
  const [resetting, setResetting] = useState(false);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function resetInvite() {
    const confirmed = window.confirm(
      "重置后，之前的邀请码会立即失效。确定生成新的课程邀请码吗？",
    );
    if (!confirmed) return;

    setResetting(true);
    setCopied(false);
    setError(null);
    try {
      const nextInvite = await apiRequest<Invite>(
        `/courses/${course.id}/invite/reset`,
        { method: "POST" },
      );
      setInvite(nextInvite);
    } catch (nextError) {
      setError(nextError);
    } finally {
      setResetting(false);
    }
  }

  async function copyInvite() {
    if (!invite) return;
    setError(null);
    try {
      if (!navigator.clipboard) {
        throw new Error("当前浏览器不支持自动复制，请手动选择邀请码");
      }
      await navigator.clipboard.writeText(invite.invite_code);
      setCopied(true);
    } catch (nextError) {
      setCopied(false);
      setError(nextError);
    }
  }

  return (
    <div className="space-y-5">
      <SectionHeading
        description="管理课程加入入口。出于安全考虑，课程详情不会返回现有邀请码；重置后请及时保存本次生成结果。"
        eyebrow="Course access"
        title="课程与邀请码"
      />

      {error ? <ErrorNotice error={error} /> : null}

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1.2fr)_minmax(18rem,0.8fr)]">
        <PanelCard>
          <div className="flex items-start gap-3">
            <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-[#e4ece8] text-[#2f6961]">
              <ShieldIcon className="size-5" />
            </span>
            <div className="min-w-0">
              <p className="font-semibold text-[#263936]">学生加入入口</p>
              <p className="mt-1 text-sm leading-6 text-[#687370]">
                重置会撤销旧邀请码。只有拿到新邀请码的学生才能申请加入这门课程。
              </p>
            </div>
          </div>

          {invite ? (
            <div className="mt-6 rounded-2xl border border-[#2f6961]/14 bg-[#edf3ef] p-4">
              <p className="text-xs font-semibold text-[#61716d]">新邀请码</p>
              <div className="mt-2 flex flex-col gap-3 sm:flex-row sm:items-center">
                <code className="min-w-0 flex-1 select-all overflow-x-auto rounded-xl bg-white px-4 py-3 font-mono text-lg font-bold tracking-[0.16em] text-[#173f3b]">
                  {invite.invite_code}
                </code>
                <button
                  aria-label="复制新课程邀请码"
                  className={secondaryButtonClass}
                  onClick={() => void copyInvite()}
                  type="button"
                >
                  {copied ? (
                    <CheckIcon className="size-4" />
                  ) : (
                    <CopyIcon className="size-4" />
                  )}
                  {copied ? "已复制" : "复制"}
                </button>
              </div>
              <p className="mt-3 text-xs text-[#66736f]">
                有效期至 {formatDate(invite.expires_at)}
              </p>
              <p aria-live="polite" className="sr-only">
                {copied ? "邀请码已复制到剪贴板" : ""}
              </p>
            </div>
          ) : (
            <p className="mt-6 rounded-2xl border border-dashed border-[#172523]/15 bg-white/45 px-4 py-5 text-sm leading-6 text-[#6c7774]">
              当前页面没有可展示的邀请码。点击重置后，新的邀请码会在这里显示一次。
            </p>
          )}

          <button
            className={`${dangerButtonClass} mt-5`}
            disabled={resetting}
            onClick={() => void resetInvite()}
            type="button"
          >
            <RefreshIcon className={`size-4 ${resetting ? "animate-spin" : ""}`} />
            {resetting ? "正在重置" : "重置邀请码"}
          </button>
        </PanelCard>

        <PanelCard>
          <p className="text-xs font-bold uppercase tracking-[0.12em] text-[#75817e]">
            Course record
          </p>
          <dl className="mt-4 grid gap-4 text-sm">
            <div>
              <dt className="text-[#77827f]">课程状态</dt>
              <dd className="mt-1.5">
                <StatusPill value={course.status} />
              </dd>
            </div>
            <div>
              <dt className="text-[#77827f]">课程代码</dt>
              <dd className="mt-1 font-mono font-semibold text-[#30433f]">
                {course.code}
              </dd>
            </div>
            <div>
              <dt className="text-[#77827f]">学期</dt>
              <dd className="mt-1 font-semibold text-[#30433f]">{course.semester}</dd>
            </div>
            <div>
              <dt className="text-[#77827f]">创建时间</dt>
              <dd className="mt-1 font-semibold text-[#30433f]">
                {formatDate(course.created_at)}
              </dd>
            </div>
          </dl>
        </PanelCard>
      </div>
    </div>
  );
}

function DocumentsPanel({ courseId }: { courseId: string }) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState<unknown>(null);

  const loadDocuments = useCallback(
    async (showLoading = true) => {
      if (showLoading) setLoading(true);
      setError(null);
      try {
        setDocuments(
          await apiRequest<DocumentRecord[]>(`/courses/${courseId}/documents`),
        );
      } catch (nextError) {
        setError(nextError);
      } finally {
        if (showLoading) setLoading(false);
      }
    },
    [courseId],
  );

  useEffect(() => {
    // Initial server synchronization for the tab's review data.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadDocuments();
  }, [loadDocuments]);

  async function uploadDocument(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedFile) return;

    setUploading(true);
    setNotice("");
    setError(null);
    try {
      const body = new FormData();
      body.append("file", selectedFile);
      await apiRequest<DocumentRecord>(`/courses/${courseId}/documents`, {
        method: "POST",
        body,
      });
      setSelectedFile(null);
      if (fileInputRef.current) fileInputRef.current.value = "";
      setNotice("资料已提交，入库状态以列表中的服务端结果为准。");
      await loadDocuments(false);
    } catch (nextError) {
      setError(nextError);
    } finally {
      setUploading(false);
    }
  }

  return (
    <div className="space-y-5">
      <SectionHeading
        action={
          <button
            className={secondaryButtonClass}
            disabled={loading || uploading}
            onClick={() => void loadDocuments()}
            type="button"
          >
            <RefreshIcon className={`size-4 ${loading ? "animate-spin" : ""}`} />
            刷新列表
          </button>
        }
        description="支持文本型 PDF、DOCX、PPTX、Markdown 和 TXT。处理失败时会保留可诊断的错误状态。"
        eyebrow="Ingestion"
        title="资料管理"
      />

      {error ? <ErrorNotice error={error} onRetry={() => void loadDocuments()} /> : null}
      {notice ? (
        <p
          aria-live="polite"
          className="rounded-2xl border border-[#2f6961]/15 bg-[#edf3ef] px-4 py-3 text-sm text-[#31544f]"
        >
          {notice}
        </p>
      ) : null}

      <PanelCard>
        <form className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-end" onSubmit={uploadDocument}>
          <div>
            <label className="text-sm font-semibold text-[#30433f]" htmlFor="course-document">
              选择课程资料
            </label>
            <input
              accept=".pdf,.docx,.pptx,.md,.txt,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/vnd.openxmlformats-officedocument.presentationml.presentation,text/markdown,text/plain"
              className="mt-2 block min-h-11 w-full rounded-xl border border-[#172523]/14 bg-white px-3 py-2 text-sm text-[#334643] file:mr-3 file:rounded-lg file:border-0 file:bg-[#e4ece8] file:px-3 file:py-2 file:text-xs file:font-semibold file:text-[#31544f] hover:file:bg-[#d9e6e0] focus:outline-none focus:ring-3 focus:ring-[#2f6961]/10"
              disabled={uploading}
              id="course-document"
              onChange={(event) => {
                setSelectedFile(event.target.files?.[0] ?? null);
                setNotice("");
              }}
              ref={fileInputRef}
              type="file"
            />
            <p className="mt-2 text-xs leading-5 text-[#77827f]">
              单文件服务端上限默认 50 MB；扫描件、加密或损坏文件会明确失败。
            </p>
          </div>
          <button
            className={primaryButtonClass}
            disabled={!selectedFile || uploading}
            type="submit"
          >
            {uploading ? <Spinner label="正在上传" /> : <><UploadIcon className="size-4" />上传资料</>}
          </button>
        </form>
      </PanelCard>

      {loading ? (
        <PanelCard className="grid min-h-44 place-items-center text-sm text-[#687370]">
          <Spinner label="正在读取资料列表" />
        </PanelCard>
      ) : documents.length ? (
        <ul className="grid gap-4" aria-label="课程资料列表">
          {documents.map((document) => {
            const displayName =
              document.logical_name ||
              document.original_filename ||
              document.name ||
              "未提供文件名";
            const status =
              document.ingestion_job?.stage ||
              document.latest_version?.status ||
              document.status;
            return (
              <li key={document.id}>
                <PanelCard className="sm:flex sm:items-start sm:justify-between sm:gap-6">
                  <div className="min-w-0">
                    <p className="break-words font-semibold text-[#263936]">{displayName}</p>
                    <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-[#75817e]">
                      {status ? <StatusPill value={status} /> : null}
                      {document.latest_version?.version !== undefined ? (
                        <span>版本 {document.latest_version.version}</span>
                      ) : null}
                      {document.created_at || document.updated_at ? (
                        <span>{formatDate(document.updated_at || document.created_at)}</span>
                      ) : null}
                    </div>
                    {document.ingestion_job?.error_code ? (
                      <p className="mt-3 break-words font-mono text-xs text-[#944237]">
                        错误码：{document.ingestion_job.error_code}
                      </p>
                    ) : null}
                  </div>
                  {typeof document.ingestion_job?.progress === "number" ? (
                    <div className="mt-4 w-full shrink-0 sm:mt-0 sm:w-44">
                      <div className="flex justify-between text-xs text-[#6c7774]">
                        <span>处理进度</span>
                        <span>{Math.round(document.ingestion_job.progress)}%</span>
                      </div>
                      <progress
                        aria-label={`${displayName}处理进度`}
                        className="mt-2 h-2 w-full accent-[#2f6961]"
                        max={100}
                        value={Math.min(100, Math.max(0, document.ingestion_job.progress))}
                      />
                    </div>
                  ) : null}
                </PanelCard>
              </li>
            );
          })}
        </ul>
      ) : (
        <EmptyState
          description="上传第一份课程资料后，可以在这里跟踪解析、切片、索引和图谱抽取状态。"
          title="还没有课程资料"
        />
      )}
    </div>
  );
}

function canReview(status?: string) {
  return !status || ["PENDING", "READY_FOR_REVIEW"].includes(status.toUpperCase());
}

function candidateTitle(candidate: GraphCandidate, kind: ReviewKind) {
  if (kind === "relations" && candidate.from_name && candidate.to_name) {
    return `${candidate.from_name} → ${candidate.to_name}`;
  }
  return candidate.name || (kind === "concepts" ? "未提供概念名称" : "未提供关系名称");
}

function GraphCandidateList({
  candidates,
  kind,
  busyKey,
  onReview,
}: {
  candidates: GraphCandidate[];
  kind: ReviewKind;
  busyKey: string | null;
  onReview: (kind: ReviewKind, id: string, decision: ReviewDecision) => void;
}) {
  const label = kind === "concepts" ? "概念" : "关系";
  if (!candidates.length) {
    return (
      <EmptyState
        description={`资料完成图谱抽取后，${label}候选会出现在这里。`}
        title={`没有${label}候选`}
      />
    );
  }

  return (
    <ul className="grid gap-4" aria-label={`${label}候选列表`}>
      {candidates.map((candidate) => {
        const title = candidateTitle(candidate, kind);
        const itemKey = `${kind}:${candidate.id}`;
        const reviewing = busyKey === itemKey;
        const reviewable = canReview(candidate.status);
        return (
          <li key={candidate.id}>
            <PanelCard>
              <div className="flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="break-words font-semibold text-[#263936]">{title}</h3>
                    {candidate.status ? <StatusPill value={candidate.status} /> : null}
                    {kind === "relations" && (candidate.relation_type || candidate.type) ? (
                      <span className="rounded-full border border-[#172523]/10 bg-white px-2.5 py-1 font-mono text-[10px] font-bold text-[#52615e]">
                        {candidate.relation_type || candidate.type}
                      </span>
                    ) : null}
                  </div>
                  {candidate.description ? (
                    <p className="mt-3 text-sm leading-6 text-[#5f6c69]">
                      {candidate.description}
                    </p>
                  ) : null}
                  {candidate.evidence || candidate.source_excerpt ? (
                    <blockquote className="mt-4 border-l-2 border-[#2f6961]/35 pl-4 text-sm leading-6 text-[#687370]">
                      {candidate.evidence || candidate.source_excerpt}
                    </blockquote>
                  ) : null}
                  <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-xs text-[#7a8582]">
                    {typeof candidate.confidence === "number" ? (
                      <span>置信度 {candidate.confidence.toLocaleString("zh-CN", { maximumFractionDigits: 3 })}</span>
                    ) : null}
                    {candidate.source_chunk_id ? (
                      <span className="break-all font-mono">来源 Chunk：{candidate.source_chunk_id}</span>
                    ) : null}
                  </div>
                </div>

                {reviewable ? (
                  <div className="flex shrink-0 flex-wrap gap-2">
                    <button
                      aria-label={`批准${label}候选：${title}`}
                      className={primaryButtonClass}
                      disabled={reviewing || busyKey !== null}
                      onClick={() => onReview(kind, candidate.id, "approve")}
                      type="button"
                    >
                      <CheckIcon className="size-4" />
                      批准
                    </button>
                    <button
                      aria-label={`拒绝${label}候选：${title}`}
                      className={dangerButtonClass}
                      disabled={reviewing || busyKey !== null}
                      onClick={() => onReview(kind, candidate.id, "reject")}
                      type="button"
                    >
                      <XIcon className="size-4" />
                      拒绝
                    </button>
                  </div>
                ) : null}
              </div>
            </PanelCard>
          </li>
        );
      })}
    </ul>
  );
}

function GraphPanel({ courseId }: { courseId: string }) {
  const [candidates, setCandidates] = useState<GraphCandidates>({
    concepts: [],
    relations: [],
  });
  const [publishedIndex, setPublishedIndex] = useState<PublishedIndex | null>(null);
  const [loading, setLoading] = useState(true);
  const [publishing, setPublishing] = useState(false);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState<unknown>(null);

  const loadGraphReview = useCallback(
    async (showLoading = true) => {
      if (showLoading) setLoading(true);
      setError(null);
      try {
        const [nextCandidates, graph] = await Promise.all([
          apiRequest<GraphCandidates>(`/courses/${courseId}/graph/candidates`),
          apiRequest<ApprovedGraph>(`/courses/${courseId}/graph`),
        ]);
        setCandidates(nextCandidates);
        setPublishedIndex(graph.published_index);
      } catch (nextError) {
        setError(nextError);
      } finally {
        if (showLoading) setLoading(false);
      }
    },
    [courseId],
  );

  useEffect(() => {
    // Initial server synchronization for the tab's review data.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadGraphReview();
  }, [loadGraphReview]);

  async function reviewCandidate(
    kind: ReviewKind,
    id: string,
    decision: ReviewDecision,
  ) {
    const itemKey = `${kind}:${id}`;
    setBusyKey(itemKey);
    setNotice("");
    setError(null);
    try {
      await apiRequest<GraphCandidate>(`/graph/${kind}/${id}/${decision}`, {
        method: "POST",
      });
      await loadGraphReview(false);
    } catch (nextError) {
      setError(nextError);
    } finally {
      setBusyKey(null);
    }
  }

  async function publishCourseVersion() {
    const confirmed = window.confirm(
      "发布后，新学习会话将使用本次审核完成的课程版本。确定继续吗？",
    );
    if (!confirmed) return;

    setPublishing(true);
    setNotice("");
    setError(null);
    try {
      const result = await apiRequest<PublishGraphResult>(
        `/courses/${courseId}/graph/publish`,
        { method: "POST" },
      );
      setPublishedIndex({
        id: result.index_id,
        version: result.version,
        published_at: result.published_at,
      });
      setNotice(`课程版本 ${result.version} 已发布`);
      await loadGraphReview(false);
    } catch (nextError) {
      setError(nextError);
    } finally {
      setPublishing(false);
    }
  }

  return (
    <div className="space-y-6">
      <SectionHeading
        action={
          <div className="flex flex-wrap gap-2">
            <button
              className={secondaryButtonClass}
              disabled={loading || publishing || busyKey !== null}
              onClick={() => void loadGraphReview()}
              type="button"
            >
              <RefreshIcon className={`size-4 ${loading ? "animate-spin" : ""}`} />
              刷新状态
            </button>
            <button
              className={primaryButtonClass}
              disabled={loading || publishing || busyKey !== null}
              onClick={() => void publishCourseVersion()}
              type="button"
            >
              {publishing ? <Spinner label="正在发布" /> : <CheckIcon className="size-4" />}
              {publishing ? null : "发布课程版本"}
            </button>
          </div>
        }
        description="审核全部候选后发布课程版本。只有教师批准且带有课程证据的概念和关系，才能进入正式知识图谱并影响学生学习路径。"
        eyebrow="Graph governance"
        title="图谱候选审核"
      />

      {error ? <ErrorNotice error={error} onRetry={() => void loadGraphReview()} /> : null}

      <PanelCard className="flex flex-col justify-between gap-3 sm:flex-row sm:items-center">
        <div>
          <p className="text-xs font-bold tracking-[0.08em] text-[#75817e] uppercase">
            Published course version
          </p>
          <p className="mt-2 font-semibold text-[#263936]">
            {publishedIndex ? `当前已发布版本 ${publishedIndex.version}` : "尚无已发布版本"}
          </p>
          {publishedIndex?.published_at ? (
            <p className="mt-1 text-xs text-[#75817e]">
              发布时间：{formatDate(publishedIndex.published_at)}
            </p>
          ) : null}
        </div>
        {notice ? (
          <p
            className="rounded-xl border border-[#2f6961]/15 bg-[#e6f0eb] px-4 py-3 text-sm font-semibold text-[#2f6961]"
            role="status"
          >
            {notice}
          </p>
        ) : null}
      </PanelCard>

      {loading ? (
        <PanelCard className="grid min-h-56 place-items-center text-sm text-[#687370]">
          <Spinner label="正在读取图谱候选" />
        </PanelCard>
      ) : (
        <div className="space-y-8">
          <section aria-labelledby="concept-candidates-title">
            <div className="mb-4 flex items-center justify-between gap-3">
              <h3 className="text-lg font-semibold text-[#263936]" id="concept-candidates-title">
                概念候选
              </h3>
              <span className="text-xs font-semibold text-[#75817e]">
                {candidates.concepts.length} 项
              </span>
            </div>
            <GraphCandidateList
              busyKey={busyKey}
              candidates={candidates.concepts}
              kind="concepts"
              onReview={(kind, id, decision) => void reviewCandidate(kind, id, decision)}
            />
          </section>

          <section aria-labelledby="relation-candidates-title">
            <div className="mb-4 flex items-center justify-between gap-3">
              <h3 className="text-lg font-semibold text-[#263936]" id="relation-candidates-title">
                关系候选
              </h3>
              <span className="text-xs font-semibold text-[#75817e]">
                {candidates.relations.length} 项
              </span>
            </div>
            <GraphCandidateList
              busyKey={busyKey}
              candidates={candidates.relations}
              kind="relations"
              onReview={(kind, id, decision) => void reviewCandidate(kind, id, decision)}
            />
          </section>
        </div>
      )}
    </div>
  );
}

function quizOptions(item: ReviewQuizItem) {
  return Array.isArray(item.options)
    ? item.options.map((value, index) => [String(index + 1), value] as const)
    : Object.entries(item.options);
}

function QuizzesPanel({ courseId }: { courseId: string }) {
  const [items, setItems] = useState<ReviewQuizItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState<unknown>(null);

  const loadItems = useCallback(
    async (showLoading = true) => {
      if (showLoading) setLoading(true);
      setError(null);
      try {
        setItems(
          await apiRequest<ReviewQuizItem[]>(`/courses/${courseId}/quizzes/review`),
        );
      } catch (nextError) {
        setError(nextError);
      } finally {
        if (showLoading) setLoading(false);
      }
    },
    [courseId],
  );

  useEffect(() => {
    // Initial server synchronization for the tab's review data.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadItems();
  }, [loadItems]);

  async function generateCandidates() {
    setGenerating(true);
    setNotice("");
    setError(null);
    try {
      await apiRequest<unknown>(`/courses/${courseId}/quizzes/generate-candidates`, {
        method: "POST",
      });
      setNotice("候选生成请求已提交，审核列表已按服务端最新结果刷新。");
      await loadItems(false);
    } catch (nextError) {
      setError(nextError);
    } finally {
      setGenerating(false);
    }
  }

  async function reviewQuiz(id: string, decision: ReviewDecision) {
    setBusyId(id);
    setNotice("");
    setError(null);
    try {
      await apiRequest<ReviewQuizItem>(`/quizzes/${id}/${decision}`, {
        method: "POST",
      });
      await loadItems(false);
    } catch (nextError) {
      setError(nextError);
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="space-y-5">
      <SectionHeading
        action={
          <div className="flex flex-wrap gap-2">
            <button
              className={secondaryButtonClass}
              disabled={loading || generating || busyId !== null}
              onClick={() => void loadItems()}
              type="button"
            >
              <RefreshIcon className={`size-4 ${loading ? "animate-spin" : ""}`} />
              刷新列表
            </button>
            <button
              className={primaryButtonClass}
              disabled={generating || busyId !== null}
              onClick={() => void generateCandidates()}
              type="button"
            >
              {generating ? <Spinner label="正在生成" /> : "生成候选题"}
            </button>
          </div>
        }
        description="候选题必须包含来源、目标知识点、唯一正确答案和解释；只有批准后的题目才可更新学生掌握度。"
        eyebrow="Quiz governance"
        title="题目候选审核"
      />

      {error ? <ErrorNotice error={error} onRetry={() => void loadItems()} /> : null}
      {notice ? (
        <p
          aria-live="polite"
          className="rounded-2xl border border-[#2f6961]/15 bg-[#edf3ef] px-4 py-3 text-sm text-[#31544f]"
        >
          {notice}
        </p>
      ) : null}

      {loading ? (
        <PanelCard className="grid min-h-56 place-items-center text-sm text-[#687370]">
          <Spinner label="正在读取题目候选" />
        </PanelCard>
      ) : items.length ? (
        <ul className="grid gap-4" aria-label="待审核题目列表">
          {items.map((item) => {
            const answer = item.correct_answer ?? item.answer;
            const reviewable = canReview(item.status);
            return (
              <li key={item.id}>
                <PanelCard>
                  <div className="flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        {item.difficulty ? <StatusPill value={item.difficulty} /> : null}
                        {item.status ? <StatusPill value={item.status} /> : null}
                        {item.concept_name ? (
                          <span className="text-xs font-semibold text-[#61716d]">
                            {item.concept_name}
                          </span>
                        ) : null}
                      </div>
                      <h3 className="mt-3 text-base font-semibold leading-7 text-[#263936]">
                        {item.question}
                      </h3>
                      <ol className="mt-4 grid gap-2 text-sm text-[#52615e] sm:grid-cols-2">
                        {quizOptions(item).map(([key, value]) => (
                          <li className="rounded-xl border border-[#172523]/10 bg-white/65 px-3.5 py-3" key={key}>
                            <span className="mr-2 font-mono text-xs font-bold text-[#2f6961]">
                              {key}
                            </span>
                            {value}
                          </li>
                        ))}
                      </ol>
                      <dl className="mt-4 grid gap-3 text-sm">
                        {answer ? (
                          <div>
                            <dt className="text-xs font-semibold text-[#77827f]">正确答案</dt>
                            <dd className="mt-1 font-semibold text-[#31544f]">{answer}</dd>
                          </div>
                        ) : null}
                        {item.explanation ? (
                          <div>
                            <dt className="text-xs font-semibold text-[#77827f]">解析</dt>
                            <dd className="mt-1 leading-6 text-[#5f6c69]">{item.explanation}</dd>
                          </div>
                        ) : null}
                        {item.source_excerpt ? (
                          <div>
                            <dt className="text-xs font-semibold text-[#77827f]">来源证据</dt>
                            <dd className="mt-1 border-l-2 border-[#2f6961]/35 pl-3 leading-6 text-[#5f6c69]">
                              {item.source_excerpt}
                            </dd>
                          </div>
                        ) : null}
                        {item.source_chunk_id ? (
                          <div>
                            <dt className="text-xs font-semibold text-[#77827f]">来源 Chunk</dt>
                            <dd className="mt-1 break-all font-mono text-xs text-[#5f6c69]">
                              {item.source_chunk_id}
                            </dd>
                          </div>
                        ) : null}
                      </dl>
                    </div>

                    {reviewable ? (
                      <div className="flex shrink-0 flex-wrap gap-2">
                        <button
                          aria-label={`批准题目：${item.question}`}
                          className={primaryButtonClass}
                          disabled={busyId !== null || generating}
                          onClick={() => void reviewQuiz(item.id, "approve")}
                          type="button"
                        >
                          <CheckIcon className="size-4" />
                          批准
                        </button>
                        <button
                          aria-label={`拒绝题目：${item.question}`}
                          className={dangerButtonClass}
                          disabled={busyId !== null || generating}
                          onClick={() => void reviewQuiz(item.id, "reject")}
                          type="button"
                        >
                          <XIcon className="size-4" />
                          拒绝
                        </button>
                      </div>
                    ) : null}
                  </div>
                </PanelCard>
              </li>
            );
          })}
        </ul>
      ) : (
        <EmptyState
          action={
            <button
              className={primaryButtonClass}
              disabled={generating}
              onClick={() => void generateCandidates()}
              type="button"
            >
              {generating ? <Spinner label="正在生成" /> : "生成候选题"}
            </button>
          }
          description="可以请求系统根据当前课程证据生成待审核的单选题。生成结果不会自动进入学生题库。"
          title="没有待审核题目"
        />
      )}
    </div>
  );
}

function StudentsPanel({ courseId }: { courseId: string }) {
  const [summary, setSummary] = useState<CourseLearningSummary>({
    students: [],
    weak_concepts: [],
  });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  const loadStudents = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setSummary(
        await apiRequest<CourseLearningSummary>(
          `/courses/${courseId}/students/learning-summary`,
        ),
      );
    } catch (nextError) {
      setError(nextError);
    } finally {
      setLoading(false);
    }
  }, [courseId]);

  useEffect(() => {
    // Initial server synchronization for the tab's review data.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadStudents();
  }, [loadStudents]);

  return (
    <div className="space-y-5">
      <SectionHeading
        action={
          <button
            className={secondaryButtonClass}
            disabled={loading}
            onClick={() => void loadStudents()}
            type="button"
          >
            <RefreshIcon className={`size-4 ${loading ? "animate-spin" : ""}`} />
            刷新列表
          </button>
        }
        description="仅汇总本课程的有效测验和已审核概念掌握度；不展示学生完整对话或作答正文。"
        eyebrow="Learning signals"
        title="学生概览"
      />

      {error ? <ErrorNotice error={error} onRetry={() => void loadStudents()} /> : null}

      {loading ? (
        <PanelCard className="grid min-h-52 place-items-center text-sm text-[#687370]">
          <Spinner label="正在读取学习汇总" />
        </PanelCard>
      ) : summary.students.length ? (
        <div className="space-y-5">
          {summary.weak_concepts.length ? (
            <PanelCard>
              <h3 className="font-semibold text-[#263936]">课程薄弱概念</h3>
              <p className="mt-1 text-xs leading-5 text-[#75817e]">
                只统计已有掌握度记录的学生，薄弱阈值为 0.60。
              </p>
              <ul className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {summary.weak_concepts.map((concept) => (
                  <li
                    className="rounded-xl border border-[#172523]/10 bg-white/60 p-4"
                    key={concept.concept_id}
                  >
                    <p className="font-semibold text-[#30433f]">{concept.concept_name}</p>
                    <p className="mt-2 text-xs text-[#687370]">
                      薄弱 {concept.weak_student_count} / 已评估 {concept.assessed_student_count}
                    </p>
                    <p className="mt-1 font-mono text-xs text-[#31544f]">
                      平均 {concept.average_mastery.toFixed(2)}
                    </p>
                  </li>
                ))}
              </ul>
            </PanelCard>
          ) : null}

          <div className="overflow-hidden rounded-[22px] border border-[#172523]/10 bg-[#fbfaf6]/82">
            <div className="hidden grid-cols-[minmax(0,1fr)_11rem_11rem_12rem] gap-4 border-b border-[#172523]/8 bg-[#eef0e9]/65 px-5 py-3 text-xs font-bold text-[#687370] lg:grid">
              <span>学生</span>
              <span>有效测验</span>
              <span>掌握度</span>
              <span>最后评估</span>
            </div>
            <ul aria-label="已加入课程的学生学习汇总">
              {summary.students.map((student) => (
              <li
                className="border-b border-[#172523]/8 px-5 py-4 last:border-b-0 lg:grid lg:grid-cols-[minmax(0,1fr)_11rem_11rem_12rem] lg:items-center lg:gap-4"
                key={student.enrollment_id}
              >
                <div className="min-w-0">
                  <p className="truncate font-semibold text-[#263936]">{student.email}</p>
                  <div className="mt-2 flex flex-wrap gap-2">
                    <StatusPill value={student.user_status} />
                    <StatusPill value={student.enrollment_status} />
                  </div>
                </div>
                <dl className="mt-4 grid grid-cols-2 gap-3 lg:contents">
                  <div>
                    <dt className="text-[10px] font-bold text-[#8a9491] lg:sr-only">有效测验</dt>
                    <dd className="mt-1 text-sm font-semibold text-[#52615e] lg:mt-0">
                      {student.quiz_correct_count} / {student.quiz_attempt_count} 正确
                    </dd>
                  </div>
                  <div>
                    <dt className="text-[10px] font-bold text-[#8a9491] lg:sr-only">掌握度</dt>
                    <dd className="mt-1 text-sm font-semibold text-[#52615e] lg:mt-0">
                      {student.average_mastery === null
                        ? "尚未评估"
                        : `${student.average_mastery.toFixed(2)} · ${student.weak_concept_count} 项薄弱`}
                    </dd>
                  </div>
                  <div className="col-span-2">
                    <dt className="text-[10px] font-bold text-[#8a9491] lg:sr-only">最后评估</dt>
                    <dd className="mt-1 text-xs font-semibold text-[#52615e] lg:mt-0">
                      {student.last_assessed_at
                        ? formatDate(student.last_assessed_at)
                        : `加入于 ${formatDate(student.joined_at)}`}
                    </dd>
                  </div>
                </dl>
              </li>
              ))}
            </ul>
          </div>
        </div>
      ) : (
        <EmptyState
          description="学生使用有效邀请码加入课程后，实际测验和掌握度汇总会出现在这里。"
          title="还没有学生加入"
        />
      )}
    </div>
  );
}

export function TeacherCourseTabs({ course, activeTab }: TeacherCourseTabsProps) {
  switch (activeTab) {
    case "overview":
      return <OverviewPanel course={course} />;
    case "documents":
      return <DocumentsPanel courseId={course.id} />;
    case "graph":
      return <GraphPanel courseId={course.id} />;
    case "quizzes":
      return <QuizzesPanel courseId={course.id} />;
    case "students":
      return <StudentsPanel courseId={course.id} />;
    case "bad-cases":
      return <BadCasesPanel courseId={course.id} />;
    case "evaluation":
      return <EvaluationPanel courseId={course.id} />;
  }
}
