"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";

import { SendIcon, ShieldIcon } from "@/components/icons";
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
import { ApiError, apiRequest, jsonBody, streamChatMessage } from "@/lib/api";
import type {
  ApprovedGraph,
  Citation,
  Course,
  LearningHistory,
  LearningPath,
  MasteryState,
  MasteryUpdate,
  QuizAttempt,
  QuizHistoryAttempt,
  QuizItem,
} from "@/lib/types";

type StudentTab =
  | "overview"
  | "chat"
  | "graph"
  | "quiz"
  | "mastery"
  | "path"
  | "history";

export function StudentCourseTabs({
  course,
  activeTab,
}: {
  course: Course;
  activeTab: StudentTab;
}) {
  if (activeTab === "chat") return <StudentChat course={course} />;
  if (activeTab === "graph") return <StudentGraph course={course} />;
  if (activeTab === "quiz") return <StudentQuiz course={course} />;
  if (activeTab === "mastery") return <StudentMastery course={course} />;
  if (activeTab === "path") return <StudentLearningPath course={course} />;
  if (activeTab === "history") return <StudentHistory course={course} />;
  return <StudentOverview course={course} />;
}

function StudentOverview({ course }: { course: Course }) {
  const actions = [
    ["课程问答", "仅使用当前课程资料作答，并返回可核验引用。"],
    ["诊断测验", "作答教师已审核题目，结果才会更新掌握度。"],
    ["学习路径", "根据已审核前置关系和你的掌握度组织学习顺序。"],
  ] as const;
  return (
    <div className="space-y-7">
      <SectionHeading
        description="这里的学习动作都受课程权限、证据与教师审核边界约束。"
        eyebrow="Course actions"
        title={`开始学习 ${course.name}`}
      />
      <div className="grid gap-4 md:grid-cols-3">
        {actions.map(([title, description], index) => (
          <article
            className="rounded-[20px] border border-[#172523]/10 bg-[#fbfaf6]/78 p-5"
            key={title}
          >
            <span className="font-mono text-xs font-semibold text-[#9b682c]">
              0{index + 1}
            </span>
            <h3 className="mt-7 text-lg font-semibold tracking-[-0.025em]">{title}</h3>
            <p className="mt-2 text-sm leading-6 text-[#65716e]">{description}</p>
          </article>
        ))}
      </div>
      <div className="flex items-start gap-3 rounded-2xl border border-[#2f6961]/14 bg-[#e7f0ec] p-4 text-sm leading-6 text-[#31524d]">
        <ShieldIcon className="mt-0.5 size-5 shrink-0" />
        <p>
          未经审核的知识关系和题目不会进入你的正式问答、掌握度或学习路径；证据不足时系统会明确拒答。
        </p>
      </div>
    </div>
  );
}

type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations: Citation[];
  intent?: string;
};

function StudentChat({ course }: { course: Course }) {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [content, setContent] = useState("");
  const [intent, setIntent] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [stage, setStage] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function ensureSession() {
    if (sessionId) return sessionId;
    const session = await apiRequest<{ id?: string; session_id?: string }>(
      `/courses/${course.id}/chat/sessions`,
      { method: "POST", ...jsonBody({}) },
    );
    const nextSessionId = session.id ?? session.session_id;
    if (!nextSessionId) {
      throw new ApiError("服务端没有返回学习会话 ID", {
        code: "INVALID_API_RESPONSE",
      });
    }
    setSessionId(nextSessionId);
    return nextSessionId;
  }

  async function sendMessage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const question = content.trim();
    if (!question || busy) return;
    setBusy(true);
    setError(null);
    setContent("");
    setStage("正在建立学习会话");
    const assistantId = crypto.randomUUID();
    setMessages((current) => [
      ...current,
      { id: crypto.randomUUID(), role: "user", content: question, citations: [] },
      { id: assistantId, role: "assistant", content: "", citations: [] },
    ]);

    try {
      const activeSessionId = await ensureSession();
      setStage("正在检索课程证据");
      await streamChatMessage(
        activeSessionId,
        {
          content: question,
          requested_intent: intent || null,
          target_concept_ids: [],
        },
        (streamEvent) => {
          if (streamEvent.type === "status") {
            const labels: Record<string, string> = {
              retrieving: "正在检索课程证据",
              reranking: "正在重排证据",
              generating: "正在组织回答",
              verifying: "正在校验引用",
            };
            const nextStage = streamEvent.data.stage;
            setStage((nextStage && labels[nextStage]) || nextStage || "正在处理");
          } else if (streamEvent.type === "token") {
            const text = streamEvent.data.text ?? "";
            setMessages((current) =>
              current.map((message) =>
                message.id === assistantId
                  ? { ...message, content: message.content + text }
                  : message,
              ),
            );
          } else if (streamEvent.type === "citation") {
            setMessages((current) =>
              current.map((message) =>
                message.id === assistantId
                  ? { ...message, citations: [...message.citations, streamEvent.data] }
                  : message,
              ),
            );
          } else if (streamEvent.type === "done") {
            setMessages((current) =>
              current.map((message) =>
                message.id === assistantId
                  ? { ...message, intent: streamEvent.data.intent }
                  : message,
              ),
            );
            setStage("回答与引用校验完成");
          } else if (streamEvent.type === "error") {
            throw new ApiError(
              streamEvent.data.message ?? "课程问答失败",
              {
                code: streamEvent.data.code,
                requestId: streamEvent.data.request_id,
              },
            );
          }
        },
      );
    } catch (nextError) {
      setError(nextError);
      setStage("回答失败");
      setMessages((current) =>
        current.filter(
          (message) => message.id !== assistantId || message.content || message.citations.length,
        ),
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <SectionHeading
        description="回答必须绑定当前课程版本的原文证据；没有充分证据时会明确说明资料不足。"
        eyebrow="Evidence-backed chat"
        title="课程问答"
      />

      <div className="grid min-h-[520px] gap-5 lg:grid-cols-[minmax(0,1fr)_280px]">
        <section className="flex min-h-[520px] flex-col overflow-hidden rounded-[22px] border border-[#172523]/10 bg-[#fbfaf6]/86">
          <div className="flex min-h-11 items-center justify-between gap-3 border-b border-[#172523]/9 px-4 py-2.5 text-xs text-[#687572]">
            <span aria-live="polite">{stage || "可以开始提问"}</span>
            {sessionId ? <span className="hidden font-mono sm:block">会话已建立</span> : null}
          </div>

          <div className="flex-1 space-y-5 overflow-y-auto p-4 sm:p-6">
            {messages.length ? (
              messages.map((message) => (
                <article
                  className={`flex ${message.role === "user" ? "justify-end" : "justify-start"}`}
                  key={message.id}
                >
                  <div
                    className={`max-w-[88%] rounded-2xl px-4 py-3 text-sm leading-7 sm:max-w-[78%] ${
                      message.role === "user"
                        ? "rounded-br-md bg-[#173f3b] text-[#f8f5ed]"
                        : "rounded-bl-md border border-[#172523]/9 bg-white text-[#263936]"
                    }`}
                  >
                    {message.content ? (
                      <p className="whitespace-pre-wrap">{message.content}</p>
                    ) : (
                      <Spinner label="正在生成回答" />
                    )}
                    {message.intent ? (
                      <p className="mt-2 text-[11px] opacity-60">意图：{message.intent}</p>
                    ) : null}
                    {message.citations.length ? (
                      <div className="mt-3 flex flex-wrap gap-1.5 border-t border-current/10 pt-3">
                        {message.citations.map((citation, index) => (
                          <a
                            aria-label={`查看引用 ${citation.label ?? index + 1}：${citation.document ?? "课程资料"}${citation.page ? `第 ${citation.page} 页` : ""}`}
                            className="rounded-md border border-[#2f6961]/14 bg-[#eaf2ee] px-2 py-1 text-xs font-semibold text-[#2f6961]"
                            href={`#citation-${message.id}-${index}`}
                            key={`${citation.citation_id ?? citation.chunk_id ?? "citation"}-${index}`}
                          >
                            [{citation.label ?? index + 1}]
                          </a>
                        ))}
                      </div>
                    ) : null}
                  </div>
                </article>
              ))
            ) : (
              <EmptyState
                description="例如：为什么虚拟内存需要页面置换算法？"
                title="向课程资料提问"
              />
            )}
          </div>

          <form className="border-t border-[#172523]/9 bg-white/45 p-3 sm:p-4" onSubmit={sendMessage}>
            <div className="mb-3 flex flex-col gap-2 sm:flex-row sm:items-center">
              <label className="text-xs font-semibold text-[#596864]" htmlFor="chat-intent">
                学习动作
              </label>
              <select
                className="rounded-lg border border-[#172523]/12 bg-white px-2.5 py-2 text-xs"
                disabled={busy}
                id="chat-intent"
                onChange={(event) => setIntent(event.target.value)}
                value={intent}
              >
                <option value="">自动识别</option>
                <option value="TUTOR_QA">课程问答</option>
                <option value="CONCEPT_COMPARE">概念比较</option>
                <option value="DIAGNOSE">前置诊断</option>
              </select>
            </div>
            <label className="sr-only" htmlFor="chat-content">
              输入课程问题
            </label>
            <div className="flex items-end gap-2">
              <textarea
                className={`${fieldClass} min-h-12 max-h-36 resize-y`}
                disabled={busy}
                id="chat-content"
                maxLength={4000}
                onChange={(event) => setContent(event.target.value)}
                placeholder="输入一个课程问题…"
                required
                rows={1}
                value={content}
              />
              <button
                aria-label="发送问题"
                className={`${primaryButtonClass} size-12 shrink-0 px-0`}
                disabled={busy || !content.trim()}
                type="submit"
              >
                <SendIcon className="size-5" />
              </button>
            </div>
          </form>
        </section>

        <aside className="rounded-[22px] border border-[#172523]/10 bg-[#f8f3e8] p-4 sm:p-5">
          <h3 className="font-semibold tracking-[-0.02em]">本轮引用</h3>
          <p className="mt-1 text-xs leading-5 text-[#727b78]">
            文档、章节与页码均来自服务端引用事件。
          </p>
          <div className="mt-4 space-y-3">
            {messages.flatMap((message) =>
              message.citations.map((citation, index) => (
                <article
                  className="rounded-xl border border-[#172523]/9 bg-white/70 p-3"
                  id={`citation-${message.id}-${index}`}
                  key={`${message.id}-${citation.citation_id ?? citation.chunk_id ?? index}`}
                  tabIndex={-1}
                >
                  <p className="text-xs font-semibold text-[#2f6961]">
                    [{citation.label ?? index + 1}] {citation.document ?? "课程资料"}
                  </p>
                  <p className="mt-1 text-[11px] text-[#75807d]">
                    {citation.section || "章节未标注"}
                    {citation.page ? ` · 第 ${citation.page} 页` : ""}
                  </p>
                  {citation.quote ? (
                    <blockquote className="mt-2 border-l-2 border-[#b77b2f]/45 pl-2 text-xs leading-5 text-[#53615e]">
                      {citation.quote}
                    </blockquote>
                  ) : null}
                </article>
              )),
            )}
            {!messages.some((message) => message.citations.length) ? (
              <p className="rounded-xl border border-dashed border-[#172523]/14 px-3 py-5 text-center text-xs text-[#7a8481]">
                回答返回引用后显示在这里
              </p>
            ) : null}
          </div>
        </aside>
      </div>
      <ErrorNotice error={error} />
    </div>
  );
}

function normalizeQuizItem(data: QuizItem | QuizItem[] | { items?: QuizItem[] }) {
  if (Array.isArray(data)) return data[0] ?? null;
  if ("items" in data) return data.items?.[0] ?? null;
  return data as QuizItem;
}

function optionEntries(options: QuizItem["options"]) {
  if (Array.isArray(options)) {
    return options.map((option, index) => ({
      answer: option,
      label: String.fromCharCode(65 + index),
    }));
  }
  return Object.entries(options).map(([label, option]) => ({
    answer: option,
    label,
  }));
}

function StudentQuiz({ course }: { course: Course }) {
  const [item, setItem] = useState<QuizItem | null>(null);
  const [answer, setAnswer] = useState("");
  const [result, setResult] = useState<QuizAttempt | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const loadNext = useCallback(async () => {
    setLoading(true);
    setError(null);
    setResult(null);
    setAnswer("");
    try {
      const data = await apiRequest<QuizItem | QuizItem[] | { items?: QuizItem[] }>(
        `/courses/${course.id}/quizzes/next`,
      );
      setItem(normalizeQuizItem(data));
    } catch (nextError) {
      setError(nextError);
      setItem(null);
    } finally {
      setLoading(false);
    }
  }, [course.id]);

  useEffect(() => {
    // Fetch the next approved item when this course tab mounts.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadNext();
  }, [loadNext]);

  async function submitAttempt(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!item || !answer) return;
    setSubmitting(true);
    setError(null);
    try {
      setResult(
        await apiRequest<QuizAttempt>(`/quizzes/${item.id}/attempts`, {
          method: "POST",
          ...jsonBody({ answer, idempotency_key: crypto.randomUUID() }),
        }),
      );
    } catch (nextError) {
      setError(nextError);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="space-y-6">
      <SectionHeading
        action={
          <button className={secondaryButtonClass} disabled={loading} onClick={() => void loadNext()} type="button">
            换一道审核题
          </button>
        }
        description="只有教师审核通过的题目与有效作答才能更新个人掌握度。提交使用唯一幂等键，避免重复更新。"
        eyebrow="Approved quiz"
        title="诊断测验"
      />
      <ErrorNotice error={error} onRetry={!item ? () => void loadNext() : undefined} />
      {loading ? (
        <div className="grid min-h-72 place-items-center rounded-2xl border border-[#172523]/10 bg-white/45 text-sm text-[#687370]">
          <Spinner label="正在获取审核题目" />
        </div>
      ) : item ? (
        <form
          className="mx-auto max-w-3xl rounded-[22px] border border-[#172523]/10 bg-[#fbfaf6]/86 p-5 sm:p-7"
          onSubmit={submitAttempt}
        >
          <div className="flex flex-wrap items-center gap-2">
            <StatusPill value={item.difficulty} />
            {item.concept_name ? (
              <span className="text-xs text-[#697572]">知识点：{item.concept_name}</span>
            ) : null}
          </div>
          <fieldset className="mt-6" disabled={submitting || Boolean(result)}>
            <legend className="text-lg leading-8 font-semibold tracking-[-0.02em]">
              {item.question}
            </legend>
            <div className="mt-5 space-y-3">
              {optionEntries(item.options).map(({ answer: option, label }) => (
                <label
                  className={`flex cursor-pointer items-start gap-3 rounded-xl border p-4 transition ${
                    answer === option
                      ? "border-[#2f6961] bg-[#e9f1ed]"
                      : "border-[#172523]/11 bg-white hover:border-[#2f6961]/30"
                  }`}
                  key={`${label}:${option}`}
                >
                  <input
                    checked={answer === option}
                    className="mt-1 accent-[#2f6961]"
                    name={`quiz-${item.id}`}
                    onChange={() => setAnswer(option)}
                    type="radio"
                    value={option}
                  />
                  <span className="text-sm leading-6">
                    <strong className="mr-2">{label}.</strong>
                    {option}
                  </span>
                </label>
              ))}
            </div>
          </fieldset>
          {result ? (
            <div
              className={`mt-5 rounded-xl border p-4 text-sm ${
                result.correct
                  ? "border-[#2f6961]/16 bg-[#e8f1ed] text-[#294e49]"
                  : "border-[#a84235]/16 bg-[#fff2ef] text-[#753a32]"
              }`}
              role="status"
            >
              <p className="font-semibold">{result.correct ? "回答正确" : "回答错误"}</p>
              {result.explanation || item.explanation ? (
                <p className="mt-2 leading-6">{result.explanation ?? item.explanation}</p>
              ) : null}
            </div>
          ) : null}
          <div className="mt-6 flex justify-end">
            <button className={primaryButtonClass} disabled={!answer || submitting || Boolean(result)} type="submit">
              {submitting ? <Spinner label="正在提交" /> : result ? "已提交" : "提交答案"}
            </button>
          </div>
        </form>
      ) : (
        <EmptyState
          description="当前课程暂时没有可用于诊断的教师审核题目。"
          title="没有可用题目"
        />
      )}
    </div>
  );
}

const relationLabels: Record<string, string> = {
  PREREQUISITE_OF: "是前置知识",
  RELATED_TO: "相关",
  PART_OF: "属于",
  CONTRASTS_WITH: "对比",
};

function StudentGraph({ course }: { course: Course }) {
  const [graph, setGraph] = useState<ApprovedGraph | null>(null);
  const [mastery, setMastery] = useState<MasteryState[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  const loadGraph = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [approvedGraph, masteryData] = await Promise.all([
        apiRequest<ApprovedGraph>(`/courses/${course.id}/graph`),
        apiRequest<
          MasteryState[] | { items?: MasteryState[]; mastery?: MasteryState[] }
        >(`/courses/${course.id}/mastery`),
      ]);
      setGraph(approvedGraph);
      setMastery(normalizeMastery(masteryData));
    } catch (nextError) {
      setError(nextError);
      setGraph(null);
      setMastery([]);
    } finally {
      setLoading(false);
    }
  }, [course.id]);

  useEffect(() => {
    // Both resources enforce the same active-enrollment boundary server-side.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadGraph();
  }, [loadGraph]);

  const masteryByConcept = useMemo(
    () => new Map(mastery.map((state) => [state.concept_id, state])),
    [mastery],
  );
  const conceptNames = useMemo(
    () => new Map((graph?.concepts ?? []).map((concept) => [concept.id, concept.name])),
    [graph],
  );

  return (
    <div className="space-y-6">
      <SectionHeading
        description="这里只展示教师已审核的知识点与关系；掌握度覆盖层只读取你自己的有效测验记录。"
        eyebrow="Approved knowledge graph"
        title="知识图谱"
      />
      <ErrorNotice error={error} onRetry={() => void loadGraph()} />
      {loading ? (
        <div className="grid min-h-72 place-items-center rounded-2xl border border-[#172523]/10 bg-white/45 text-sm text-[#687370]">
          <Spinner label="正在读取审核图谱" />
        </div>
      ) : graph?.concepts.length ? (
        <div className="space-y-6">
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-[#172523]/10 bg-[#f8f3e8] px-4 py-3 text-xs text-[#66716e]">
            <span>
              {graph.concepts.length} 个审核知识点 · {graph.relations.length} 条审核关系
            </span>
            <span className="font-mono">
              {graph.published_index
                ? `课程索引 v${graph.published_index.version}`
                : "尚无已发布索引"}
            </span>
          </div>

          <section>
            <h3 className="mb-3 text-sm font-semibold text-[#334643]">知识点与个人掌握度</h3>
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {graph.concepts.map((concept) => {
                const state = masteryByConcept.get(concept.id);
                const percentage = state ? Math.round(state.mastery * 100) : null;
                return (
                  <article
                    className="rounded-[18px] border border-[#172523]/10 bg-[#fbfaf6]/86 p-5"
                    key={concept.id}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <h4 className="font-semibold tracking-[-0.015em]">{concept.name}</h4>
                      <span
                        className={`shrink-0 rounded-full px-2.5 py-1 text-[11px] font-semibold ${
                          percentage === null
                            ? "bg-[#eceae3] text-[#747d7a]"
                            : percentage >= 60
                              ? "bg-[#e5f0eb] text-[#2f6961]"
                              : "bg-[#fff0e4] text-[#9b6327]"
                        }`}
                      >
                        {percentage === null ? "尚未测评" : `掌握 ${percentage}%`}
                      </span>
                    </div>
                    <p className="mt-3 text-sm leading-6 text-[#66716e]">
                      {concept.description || "教师尚未补充知识点说明。"}
                    </p>
                    {state ? (
                      <div className="mt-4 h-1.5 overflow-hidden rounded-full bg-[#dfe5e1]">
                        <span
                          aria-label={`掌握度 ${percentage}%`}
                          className="block h-full rounded-full bg-[#2f6961]"
                          style={{ width: `${percentage}%` }}
                        />
                      </div>
                    ) : null}
                  </article>
                );
              })}
            </div>
          </section>

          <section>
            <h3 className="mb-3 text-sm font-semibold text-[#334643]">审核关系</h3>
            {graph.relations.length ? (
              <div className="grid gap-2 lg:grid-cols-2">
                {graph.relations.map((relation) => (
                  <article
                    className="flex items-center gap-3 rounded-xl border border-[#172523]/10 bg-white/65 px-4 py-3 text-sm"
                    key={relation.id}
                  >
                    <strong className="min-w-0 flex-1 truncate">
                      {conceptNames.get(relation.from_concept_id) ?? relation.from_concept_id}
                    </strong>
                    <span className="shrink-0 rounded-md bg-[#e7efeb] px-2 py-1 text-[11px] font-semibold text-[#41635e]">
                      {relationLabels[relation.type] ?? relation.type}
                    </span>
                    <strong className="min-w-0 flex-1 truncate text-right">
                      {conceptNames.get(relation.to_concept_id) ?? relation.to_concept_id}
                    </strong>
                  </article>
                ))}
              </div>
            ) : (
              <EmptyState
                description="教师已经审核了知识点，但暂时还没有发布知识关系。"
                title="暂无审核关系"
              />
            )}
          </section>
        </div>
      ) : (
        <EmptyState
          description="教师审核并发布知识点后，这里才会显示课程图谱；候选内容不会提前暴露。"
          title="暂无已审核知识图谱"
        />
      )}
    </div>
  );
}

type StudentHistoryEvent =
  | {
      kind: "CHAT_SESSION";
      occurredAt: string;
      session: LearningHistory["chat_sessions"][number];
    }
  | {
      kind: "QUIZ_ATTEMPT";
      occurredAt: string;
      attempt: QuizHistoryAttempt;
      mastery?: MasteryUpdate;
    };

function StudentHistory({ course }: { course: Course }) {
  const [history, setHistory] = useState<LearningHistory | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  const loadHistory = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setHistory(
        await apiRequest<LearningHistory>(`/courses/${course.id}/learning-history`),
      );
    } catch (nextError) {
      setError(nextError);
      setHistory(null);
    } finally {
      setLoading(false);
    }
  }, [course.id]);

  useEffect(() => {
    // History is derived from this student's stored sessions and quiz attempts.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadHistory();
  }, [loadHistory]);

  const events = useMemo<StudentHistoryEvent[]>(() => {
    if (!history) return [];
    const masteryByAttempt = new Map(
      history.mastery_updates.map((update) => [update.attempt_id, update]),
    );
    return [
      ...history.chat_sessions.map(
        (session): StudentHistoryEvent => ({
          kind: "CHAT_SESSION",
          occurredAt: session.last_message_at ?? session.created_at,
          session,
        }),
      ),
      ...history.quiz_attempts.map(
        (attempt): StudentHistoryEvent => ({
          kind: "QUIZ_ATTEMPT",
          occurredAt: attempt.graded_at ?? attempt.submitted_at,
          attempt,
          mastery: masteryByAttempt.get(attempt.id),
        }),
      ),
    ].sort(
      (left, right) =>
        new Date(right.occurredAt).getTime() - new Date(left.occurredAt).getTime(),
    );
  }, [history]);

  return (
    <div className="space-y-6">
      <SectionHeading
        description="按时间汇总你的课程会话、测验结果与可审计的掌握度变化，不展示其他学生记录。"
        eyebrow="Personal learning record"
        title="学习历史"
      />
      <ErrorNotice error={error} onRetry={() => void loadHistory()} />
      {loading ? (
        <div className="grid min-h-72 place-items-center rounded-2xl border border-[#172523]/10 bg-white/45 text-sm text-[#687370]">
          <Spinner label="正在读取学习历史" />
        </div>
      ) : events.length ? (
        <div className="mx-auto max-w-4xl space-y-3">
          {events.map((event) =>
            event.kind === "CHAT_SESSION" ? (
              <article
                className="rounded-[18px] border border-[#172523]/10 bg-[#fbfaf6]/86 p-5"
                key={`chat-${event.session.id}`}
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <p className="text-xs font-semibold text-[#2f6961]">课程会话</p>
                    <h3 className="mt-1 font-semibold">
                      {event.session.title ||
                        event.session.first_user_message_preview ||
                        "未命名会话"}
                    </h3>
                  </div>
                  <time className="text-xs text-[#7a8582]">
                    {formatDate(event.occurredAt)}
                  </time>
                </div>
                <p className="mt-3 text-sm leading-6 text-[#65716e]">
                  {event.session.latest_message_preview || "该会话还没有消息。"}
                </p>
                <p className="mt-3 text-xs text-[#7a8582]">
                  共 {event.session.message_count} 条消息 · 课程索引 v
                  {event.session.index_version}
                </p>
              </article>
            ) : (
              <article
                className="rounded-[18px] border border-[#172523]/10 bg-white/72 p-5"
                key={`quiz-${event.attempt.id}`}
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="flex items-center gap-2">
                    <span
                      className={`rounded-full px-2.5 py-1 text-xs font-semibold ${
                        event.attempt.correct
                          ? "bg-[#e5f0eb] text-[#2f6961]"
                          : "bg-[#fff0e9] text-[#9a493b]"
                      }`}
                    >
                      {event.attempt.correct ? "回答正确" : "回答错误"}
                    </span>
                    <span className="text-xs text-[#727d7a]">
                      {event.attempt.concept_name}
                    </span>
                  </div>
                  <time className="text-xs text-[#7a8582]">
                    {formatDate(event.occurredAt)}
                  </time>
                </div>
                <h3 className="mt-3 text-sm font-semibold leading-6">
                  {event.attempt.question}
                </h3>
                <p className="mt-2 text-sm text-[#65716e]">
                  你的答案：{event.attempt.answer}
                </p>
                {event.mastery ? (
                  <p className="mt-3 rounded-lg bg-[#f1efe7] px-3 py-2 text-xs text-[#596864]">
                    掌握度 {Math.round(event.mastery.before_mastery * 100)}% → {" "}
                    <strong>{Math.round(event.mastery.after_mastery * 100)}%</strong>
                    <span className="ml-2 text-[#7a8582]">
                      （基于本次权重 {event.mastery.weight.toFixed(2)}）
                    </span>
                  </p>
                ) : (
                  <p className="mt-3 text-xs text-[#7a8582]">
                    此记录未产生有效掌握度更新。
                  </p>
                )}
              </article>
            ),
          )}
        </div>
      ) : (
        <EmptyState
          description="完成一次课程问答或教师审核测验后，记录会按时间显示在这里。"
          title="还没有学习记录"
        />
      )}
    </div>
  );
}

function normalizeMastery(data: MasteryState[] | { items?: MasteryState[]; mastery?: MasteryState[] }) {
  if (Array.isArray(data)) return data;
  return data.items ?? data.mastery ?? [];
}

function StudentMastery({ course }: { course: Course }) {
  const [states, setStates] = useState<MasteryState[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  const loadMastery = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await apiRequest<
        MasteryState[] | { items?: MasteryState[]; mastery?: MasteryState[] }
      >(`/courses/${course.id}/mastery`);
      setStates(normalizeMastery(data));
    } catch (nextError) {
      setError(nextError);
    } finally {
      setLoading(false);
    }
  }, [course.id]);

  useEffect(() => {
    // Fetch the learner's auditable mastery state when the tab mounts.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadMastery();
  }, [loadMastery]);

  return (
    <div className="space-y-6">
      <SectionHeading
        description="掌握度由教师审核题目的有效作答更新。未作答知识点不以聊天内容推断。"
        eyebrow="Learner state"
        title="个人掌握度"
      />
      <ErrorNotice error={error} onRetry={() => void loadMastery()} />
      {loading ? (
        <div className="grid min-h-64 place-items-center rounded-2xl border border-[#172523]/10 bg-white/45 text-sm text-[#687370]">
          <Spinner label="正在读取掌握度" />
        </div>
      ) : states.length ? (
        <div className="grid gap-3 md:grid-cols-2">
          {states.map((state) => {
            const percentage = Math.max(0, Math.min(100, Math.round(state.mastery * 100)));
            return (
              <article
                className="rounded-[18px] border border-[#172523]/10 bg-[#fbfaf6]/84 p-5"
                key={state.id ?? state.concept_id}
              >
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <h3 className="font-semibold">{state.concept_name ?? state.name ?? state.concept_id}</h3>
                    <p className="mt-1 text-xs text-[#75807d]">
                      有效作答 {state.attempt_count ?? 0} 次
                    </p>
                  </div>
                  <span className="text-lg font-semibold text-[#2f6961]">{percentage}%</span>
                </div>
                <div
                  aria-label={`掌握度 ${percentage}%`}
                  aria-valuemax={100}
                  aria-valuemin={0}
                  aria-valuenow={percentage}
                  className="mt-4 h-2 overflow-hidden rounded-full bg-[#dfe4e0]"
                  role="progressbar"
                >
                  <div className="h-full rounded-full bg-[#2f6961]" style={{ width: `${percentage}%` }} />
                </div>
                <p className="mt-3 text-[11px] text-[#7b8582]">
                  最近评估：{formatDate(state.last_assessed_at)}
                </p>
              </article>
            );
          })}
        </div>
      ) : (
        <EmptyState
          description="完成教师审核的诊断题后，这里会显示对应知识点的可解释掌握度。"
          title="还没有掌握度记录"
        />
      )}
    </div>
  );
}

type ApprovedConcept = { id: string; name: string; status?: string };

function normalizeConcepts(data: unknown): ApprovedConcept[] {
  if (!data || typeof data !== "object") return [];
  const object = data as Record<string, unknown>;
  const raw = Array.isArray(data)
    ? data
    : Array.isArray(object.concepts)
      ? object.concepts
      : Array.isArray(object.nodes)
        ? object.nodes
        : [];
  return raw.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const concept = item as Record<string, unknown>;
    const id = concept.id ?? concept.concept_id;
    const name = concept.name ?? concept.label;
    if (typeof id !== "string" || typeof name !== "string") return [];
    return [{ id, name, status: typeof concept.status === "string" ? concept.status : undefined }];
  });
}

function normalizePath(data: LearningPath | LearningPath["steps"]) {
  return Array.isArray(data) ? { steps: data } : data;
}

function StudentLearningPath({ course }: { course: Course }) {
  const [concepts, setConcepts] = useState<ApprovedConcept[]>([]);
  const [targetConceptId, setTargetConceptId] = useState("");
  const [path, setPath] = useState<LearningPath | null>(null);
  const [loadingConcepts, setLoadingConcepts] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    let cancelled = false;
    void apiRequest<unknown>(`/courses/${course.id}/graph`)
      .then((data) => {
        if (!cancelled) {
          const approved = normalizeConcepts(data).filter(
            (concept) => !concept.status || concept.status === "APPROVED",
          );
          setConcepts(approved);
          if (approved[0]) setTargetConceptId(approved[0].id);
        }
      })
      .catch((nextError) => {
        if (!cancelled) setError(nextError);
      })
      .finally(() => {
        if (!cancelled) setLoadingConcepts(false);
      });
    return () => {
      cancelled = true;
    };
  }, [course.id]);

  async function generatePath(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!targetConceptId.trim()) return;
    setBusy(true);
    setError(null);
    setPath(null);
    try {
      const data = await apiRequest<LearningPath | LearningPath["steps"]>(
        `/courses/${course.id}/learning-path`,
        { method: "POST", ...jsonBody({ target_concept_id: targetConceptId.trim() }) },
      );
      setPath(normalizePath(data));
    } catch (nextError) {
      setError(nextError);
    } finally {
      setBusy(false);
    }
  }

  const targetName = useMemo(
    () => concepts.find((concept) => concept.id === targetConceptId)?.name,
    [concepts, targetConceptId],
  );

  return (
    <div className="space-y-6">
      <SectionHeading
        description="路径只使用教师审核的前置关系，优先安排掌握度不足的前置知识，并按拓扑顺序输出。"
        eyebrow="Personalized path"
        title="学习路径"
      />
      <div className="grid gap-6 lg:grid-cols-[320px_minmax(0,1fr)] lg:items-start">
        <form
          className="rounded-[20px] border border-[#172523]/10 bg-[#fbfaf6]/84 p-5"
          onSubmit={generatePath}
        >
          <label className="block">
            <span className="mb-2 block text-sm font-semibold">目标知识点</span>
            {concepts.length ? (
              <select
                className={fieldClass}
                disabled={busy || loadingConcepts}
                onChange={(event) => setTargetConceptId(event.target.value)}
                required
                value={targetConceptId}
              >
                {concepts.map((concept) => (
                  <option key={concept.id} value={concept.id}>
                    {concept.name}
                  </option>
                ))}
              </select>
            ) : (
              <input
                className={fieldClass}
                disabled={busy || loadingConcepts}
                onChange={(event) => setTargetConceptId(event.target.value)}
                placeholder="输入已审核知识点 ID"
                required
                value={targetConceptId}
              />
            )}
          </label>
          <p className="mt-3 text-xs leading-5 text-[#74807d]">
            若目标未审核或图中存在未解决的前置环，服务端会拒绝生成路径。
          </p>
          <button className={`${primaryButtonClass} mt-5 w-full`} disabled={busy || loadingConcepts} type="submit">
            {busy ? <Spinner label="正在生成" /> : loadingConcepts ? "正在读取知识点" : "生成学习路径"}
          </button>
        </form>

        <section>
          <ErrorNotice error={error} />
          {path?.steps?.length ? (
            <div className="space-y-3">
              <p className="mb-4 text-sm text-[#65716e]">
                目标：<strong className="text-[#2f6961]">{path.target_concept_name ?? targetName ?? targetConceptId}</strong>
              </p>
              {path.steps.map((step, index) => (
                <article
                  className="relative rounded-[18px] border border-[#172523]/10 bg-[#fbfaf6]/86 p-5 pl-16"
                  key={`${step.concept_id ?? step.name ?? step.title ?? "step"}-${index}`}
                >
                  <span className="absolute top-5 left-5 grid size-8 place-items-center rounded-full bg-[#173f3b] text-xs font-bold text-white">
                    {index + 1}
                  </span>
                  <h3 className="font-semibold">
                    {step.concept_name ?? step.name ?? step.title ?? step.concept_id ?? `步骤 ${index + 1}`}
                  </h3>
                  {step.reason || step.recommended_reason ? (
                    <p className="mt-2 text-sm leading-6 text-[#63706d]">
                      {step.reason ?? step.recommended_reason}
                    </p>
                  ) : null}
                  {typeof step.mastery === "number" ? (
                    <p className="mt-3 text-xs text-[#7a8582]">
                      当前掌握度 {Math.round(step.mastery * 100)}%
                    </p>
                  ) : null}
                </article>
              ))}
            </div>
          ) : (
            <EmptyState
              description="选择一个已审核目标知识点，系统会结合前置关系和你的掌握度返回学习顺序。"
              title="尚未生成路径"
            />
          )}
        </section>
      </div>
    </div>
  );
}
