"use client";

import { useCallback, useEffect, useMemo, useState, type FormEvent, type ReactNode } from "react";

import { RefreshIcon } from "@/components/icons";
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
import { apiRequest } from "@/lib/api";

type Page<T> = { items: T[]; total: number; limit: number; offset: number };

type BadCase = {
  id: string;
  course_id: string;
  source_type: "MESSAGE" | "EVAL_RUN" | "QUIZ_ATTEMPT" | "RETRIEVAL_TRACE";
  source_id: string;
  category: string;
  status: "OPEN" | "FIXED" | "WONT_FIX";
  notes: string;
  resolved_at: string | null;
  created_at: string;
  updated_at: string;
};

type EvalDataset = {
  id: string;
  course_id: string;
  name: string;
  description: string;
  type: "RETRIEVAL" | "END_TO_END_QA" | "INTENT_ROUTING" | "LEARNING_PATH";
  version: number;
  status: "DRAFT" | "FROZEN" | "ARCHIVED";
  case_count: number;
  content_sha256: string | null;
  frozen_at: string | null;
  created_at: string;
};

type EvalRun = {
  id: string;
  dataset_id: string;
  dataset_name?: string;
  dataset_type?: string;
  dataset_version?: number;
  status: string;
  metrics: Record<string, unknown> | null;
  config: Record<string, unknown>;
  trace_id: string;
  git_commit: string;
  index_version: number;
  error_code: string | null;
  error_message: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
};

function Panel({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <div className={`rounded-[22px] border border-[#172523]/10 bg-[#fbfaf6]/82 p-5 sm:p-6 ${className}`}>
      {children}
    </div>
  );
}

function FieldLabel({ children }: { children: ReactNode }) {
  return <span className="mb-1.5 block text-xs font-semibold text-[#61716d]">{children}</span>;
}

function parseObject(value: string, label: string): Record<string, unknown> {
  if (!value.trim()) throw new Error(`${label} 不能为空`);
  let parsed: unknown;
  try {
    parsed = JSON.parse(value);
  } catch {
    throw new Error(`${label} 必须是有效 JSON`);
  }
  if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
    throw new Error(`${label} 必须是 JSON 对象`);
  }
  return parsed as Record<string, unknown>;
}

export function BadCasesPanel({ courseId }: { courseId: string }) {
  const [page, setPage] = useState<Page<BadCase>>({ items: [], total: 0, limit: 100, offset: 0 });
  const [statusFilter, setStatusFilter] = useState("");
  const [sourceFilter, setSourceFilter] = useState("");
  const [sourceType, setSourceType] = useState<BadCase["source_type"]>("MESSAGE");
  const [sourceId, setSourceId] = useState("");
  const [category, setCategory] = useState("");
  const [notes, setNotes] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState("");

  const loadCases = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const query = new URLSearchParams({ limit: "100", offset: "0" });
      if (statusFilter) query.set("status", statusFilter);
      if (sourceFilter) query.set("source_type", sourceFilter);
      setPage(await apiRequest<Page<BadCase>>(`/courses/${courseId}/bad-cases?${query}`));
    } catch (nextError) {
      setError(nextError);
    } finally {
      setLoading(false);
    }
  }, [courseId, sourceFilter, statusFilter]);

  useEffect(() => {
    // Initial and filter-driven server synchronization.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadCases();
  }, [loadCases]);

  async function createCase(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    setNotice("");
    try {
      await apiRequest<BadCase>("/bad-cases", {
        method: "POST",
        body: JSON.stringify({
          course_id: courseId,
          source_type: sourceType,
          source_id: sourceId.trim(),
          category: category.trim(),
          notes,
        }),
      });
      setSourceId("");
      setCategory("");
      setNotes("");
      setNotice("Bad Case 已记录；来源已由服务端校验为本课程真实记录。");
      await loadCases();
    } catch (nextError) {
      setError(nextError);
    } finally {
      setSaving(false);
    }
  }

  async function updateStatus(id: string, status: BadCase["status"]) {
    setBusyId(id);
    setError(null);
    setNotice("");
    try {
      await apiRequest<BadCase>(`/bad-cases/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ status }),
      });
      await loadCases();
    } catch (nextError) {
      setError(nextError);
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="space-y-5">
      <SectionHeading
        description="只能标注本课程已存在的消息、评测运行、测验作答或检索 Trace；来源 ID 不存在时会明确失败。"
        eyebrow="Quality governance"
        title="Bad Case"
      />
      {error ? <ErrorNotice error={error} onRetry={() => void loadCases()} /> : null}
      {notice ? <p className="rounded-xl bg-[#e6f0eb] px-4 py-3 text-sm text-[#2f6961]" role="status">{notice}</p> : null}

      <Panel>
        <h3 className="font-semibold text-[#263936]">标注真实来源</h3>
        <form className="mt-4 grid gap-4 md:grid-cols-2" onSubmit={createCase}>
          <label>
            <FieldLabel>来源类型</FieldLabel>
            <select className={fieldClass} onChange={(event) => setSourceType(event.target.value as BadCase["source_type"])} value={sourceType}>
              <option value="MESSAGE">回答消息</option>
              <option value="EVAL_RUN">评测运行</option>
              <option value="QUIZ_ATTEMPT">测验作答</option>
              <option value="RETRIEVAL_TRACE">检索 Trace</option>
            </select>
          </label>
          <label>
            <FieldLabel>真实来源 UUID</FieldLabel>
            <input className={fieldClass} onChange={(event) => setSourceId(event.target.value)} placeholder="粘贴消息、作答、运行或 Trace UUID" required value={sourceId} />
          </label>
          <label>
            <FieldLabel>分类</FieldLabel>
            <input className={fieldClass} maxLength={64} onChange={(event) => setCategory(event.target.value)} placeholder="例如：引用不支持结论" required value={category} />
          </label>
          <label>
            <FieldLabel>备注</FieldLabel>
            <input className={fieldClass} onChange={(event) => setNotes(event.target.value)} placeholder="记录现象与修复线索，不会修改原始记录" value={notes} />
          </label>
          <div className="md:col-span-2">
            <button className={primaryButtonClass} disabled={saving} type="submit">
              {saving ? <Spinner label="正在校验并记录" /> : "记录 Bad Case"}
            </button>
          </div>
        </form>
      </Panel>

      <div className="flex flex-wrap gap-3">
        <select aria-label="按处理状态筛选" className={`${fieldClass} w-auto`} onChange={(event) => setStatusFilter(event.target.value)} value={statusFilter}>
          <option value="">全部状态</option><option value="OPEN">OPEN</option><option value="FIXED">FIXED</option><option value="WONT_FIX">WONT_FIX</option>
        </select>
        <select aria-label="按来源类型筛选" className={`${fieldClass} w-auto`} onChange={(event) => setSourceFilter(event.target.value)} value={sourceFilter}>
          <option value="">全部来源</option><option value="MESSAGE">MESSAGE</option><option value="EVAL_RUN">EVAL_RUN</option><option value="QUIZ_ATTEMPT">QUIZ_ATTEMPT</option><option value="RETRIEVAL_TRACE">RETRIEVAL_TRACE</option>
        </select>
        <button className={secondaryButtonClass} disabled={loading} onClick={() => void loadCases()} type="button"><RefreshIcon className={`size-4 ${loading ? "animate-spin" : ""}`} />刷新</button>
      </div>

      {loading ? <Panel className="grid min-h-40 place-items-center"><Spinner label="正在读取 Bad Case" /></Panel> : page.items.length ? (
        <ul className="grid gap-4" aria-label="Bad Case 列表">
          {page.items.map((item) => (
            <li key={item.id}><Panel>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div><div className="flex flex-wrap gap-2"><StatusPill value={item.status} /><StatusPill value={item.source_type} /><span className="text-xs font-semibold text-[#687370]">{item.category}</span></div><p className="mt-3 break-all font-mono text-xs text-[#52615e]">{item.source_id}</p>{item.notes ? <p className="mt-3 text-sm leading-6 text-[#5f6c69]">{item.notes}</p> : null}<p className="mt-3 text-xs text-[#8a9491]">记录于 {formatDate(item.created_at)}</p></div>
                <div className="flex flex-wrap gap-2"><button className={secondaryButtonClass} disabled={busyId !== null} onClick={() => void updateStatus(item.id, "OPEN")} type="button">重新打开</button><button className={secondaryButtonClass} disabled={busyId !== null} onClick={() => void updateStatus(item.id, "FIXED")} type="button">标记已修复</button><button className={secondaryButtonClass} disabled={busyId !== null} onClick={() => void updateStatus(item.id, "WONT_FIX")} type="button">不修复</button></div>
              </div>
            </Panel></li>
          ))}
        </ul>
      ) : <EmptyState description="还没有符合当前筛选条件的真实问题记录。" title="暂无 Bad Case" />}
    </div>
  );
}

export function EvaluationPanel({ courseId }: { courseId: string }) {
  const [datasets, setDatasets] = useState<EvalDataset[]>([]);
  const [runs, setRuns] = useState<Page<EvalRun>>({ items: [], total: 0, limit: 100, offset: 0 });
  const [selectedId, setSelectedId] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [datasetType, setDatasetType] = useState<EvalDataset["type"]>("RETRIEVAL");
  const [caseKey, setCaseKey] = useState("");
  const [caseInput, setCaseInput] = useState("");
  const [expected, setExpected] = useState("");
  const [labels, setLabels] = useState("");
  const [indexVersion, setIndexVersion] = useState("1");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState("");

  const selected = useMemo(() => datasets.find((dataset) => dataset.id === selectedId) ?? null, [datasets, selectedId]);

  const loadEvaluation = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [nextDatasets, nextRuns] = await Promise.all([
        apiRequest<EvalDataset[]>(`/courses/${courseId}/eval-datasets`),
        apiRequest<Page<EvalRun>>(`/courses/${courseId}/eval-runs?limit=100&offset=0`),
      ]);
      setDatasets(nextDatasets);
      setRuns(nextRuns);
      setSelectedId((current) => current || nextDatasets[0]?.id || "");
    } catch (nextError) {
      setError(nextError);
    } finally {
      setLoading(false);
    }
  }, [courseId]);

  useEffect(() => {
    // Initial server synchronization for evaluation governance.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadEvaluation();
  }, [loadEvaluation]);

  async function createDataset(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setSaving(true); setError(null); setNotice("");
    try {
      const created = await apiRequest<EvalDataset>(`/courses/${courseId}/eval-datasets`, { method: "POST", body: JSON.stringify({ name: name.trim(), description, type: datasetType }) });
      setName(""); setDescription(""); setSelectedId(created.id); setNotice("评测集已创建。请先添加真实标注 Case，再冻结版本。"); await loadEvaluation();
    } catch (nextError) { setError(nextError); } finally { setSaving(false); }
  }

  async function addCase(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!selected) return; setSaving(true); setError(null); setNotice("");
    try {
      const input = parseObject(caseInput, "Input");
      const expectedObject = parseObject(expected, "Expected");
      const labelObject = parseObject(labels, "Labels");
      await apiRequest(`/eval-datasets/${selected.id}/cases`, { method: "POST", body: JSON.stringify({ case_key: caseKey.trim(), input, expected: expectedObject, labels: labelObject }) });
      setCaseKey(""); setCaseInput(""); setExpected(""); setLabels(""); setNotice("Case 已写入草稿评测集，未填充任何预设结果。"); await loadEvaluation();
    } catch (nextError) { setError(nextError); } finally { setSaving(false); }
  }

  async function freezeDataset() {
    if (!selected || !window.confirm("冻结后 Case 不可编辑。确定继续吗？")) return;
    setSaving(true); setError(null); setNotice("");
    try { await apiRequest(`/eval-datasets/${selected.id}/freeze`, { method: "POST" }); setNotice("评测集已冻结，可以启动服务端三基线实验。"); await loadEvaluation(); }
    catch (nextError) { setError(nextError); } finally { setSaving(false); }
  }

  async function launchEvaluation() {
    if (!selected) return;
    setSaving(true); setError(null); setNotice("");
    try {
      await apiRequest(`/eval-datasets/${selected.id}/runs`, {
        method: "POST",
        body: JSON.stringify({ index_version: Number(indexVersion) }),
      });
      setNotice("真实三基线评测已进入服务端队列，可在运行列表中刷新查看状态。");
      await loadEvaluation();
    } catch (nextError) { setError(nextError); } finally { setSaving(false); }
  }

  const runnableDataset = selected?.type === "RETRIEVAL" ? selected : null;
  const command = `uv run python -m app.evaluation.runner --dataset-id ${runnableDataset?.id ?? "<retrieval-dataset-uuid>"} --index-version ${indexVersion || "<version>"}`;

  return (
    <div className="space-y-5">
      <SectionHeading description="数据集先添加人工确认的 Case 再冻结；页面只展示持久化的真实运行指标和来源。" eyebrow="Offline evaluation" title="评测中心" />
      {error ? <ErrorNotice error={error} onRetry={() => void loadEvaluation()} /> : null}
      {notice ? <p className="rounded-xl bg-[#e6f0eb] px-4 py-3 text-sm text-[#2f6961]" role="status">{notice}</p> : null}

      <div className="grid gap-5 lg:grid-cols-2">
        <Panel><h3 className="font-semibold text-[#263936]">创建评测集</h3><form className="mt-4 grid gap-4" onSubmit={createDataset}><label><FieldLabel>名称</FieldLabel><input className={fieldClass} onChange={(event) => setName(event.target.value)} required value={name} /></label><label><FieldLabel>类型</FieldLabel><select className={fieldClass} onChange={(event) => setDatasetType(event.target.value as EvalDataset["type"])} value={datasetType}><option value="RETRIEVAL">Retrieval</option><option value="END_TO_END_QA">End-to-End QA</option><option value="INTENT_ROUTING">Intent Routing</option><option value="LEARNING_PATH">Learning Path</option></select></label><label><FieldLabel>描述</FieldLabel><textarea className={fieldClass} onChange={(event) => setDescription(event.target.value)} rows={3} value={description} /></label><button className={primaryButtonClass} disabled={saving} type="submit">创建草稿</button></form></Panel>
        <Panel><h3 className="font-semibold text-[#263936]">数据集版本</h3>{loading ? <div className="mt-6"><Spinner /></div> : datasets.length ? <div className="mt-4 space-y-4"><select className={fieldClass} onChange={(event) => setSelectedId(event.target.value)} value={selectedId}>{datasets.map((dataset) => <option key={dataset.id} value={dataset.id}>{dataset.name} · v{dataset.version} · {dataset.status}</option>)}</select>{selected ? <div className="rounded-xl border border-[#172523]/10 bg-white/60 p-4"><div className="flex flex-wrap gap-2"><StatusPill value={selected.status} /><StatusPill value={selected.type} /></div><p className="mt-3 text-sm text-[#52615e]">{selected.case_count} 个 Case · 创建于 {formatDate(selected.created_at)}</p>{selected.content_sha256 ? <p className="mt-2 break-all font-mono text-[10px] text-[#8a9491]">SHA-256 {selected.content_sha256}</p> : null}<button className={`${primaryButtonClass} mt-4`} disabled={saving || selected.status !== "DRAFT" || selected.case_count < 1} onClick={() => void freezeDataset()} type="button">冻结数据集</button>{selected.status === "DRAFT" && selected.case_count < 1 ? <p className="mt-2 text-xs text-[#8b602b]">至少添加一个真实标注 Case 才能冻结。</p> : null}</div> : null}</div> : <EmptyState description="先创建一个草稿数据集。" title="暂无数据集" />}</Panel>
      </div>

      {selected?.status === "DRAFT" ? <Panel><h3 className="font-semibold text-[#263936]">添加人工标注 Case</h3><p className="mt-1 text-xs text-[#75817e]">Input、Expected 和 Labels 均为必填 JSON 对象；页面不会预填标签或结果。</p><form className="mt-4 grid gap-4" onSubmit={addCase}><label><FieldLabel>Case key</FieldLabel><input className={fieldClass} onChange={(event) => setCaseKey(event.target.value)} required value={caseKey} /></label><div className="grid gap-4 lg:grid-cols-3"><label><FieldLabel>Input JSON</FieldLabel><textarea className={`${fieldClass} font-mono text-xs`} onChange={(event) => setCaseInput(event.target.value)} placeholder="{}" required rows={7} value={caseInput} /></label><label><FieldLabel>Expected JSON</FieldLabel><textarea className={`${fieldClass} font-mono text-xs`} onChange={(event) => setExpected(event.target.value)} placeholder="{}" required rows={7} value={expected} /></label><label><FieldLabel>Labels JSON</FieldLabel><textarea className={`${fieldClass} font-mono text-xs`} onChange={(event) => setLabels(event.target.value)} placeholder="{}" required rows={7} value={labels} /></label></div><button className={primaryButtonClass} disabled={saving} type="submit">{saving ? <Spinner label="正在写入" /> : "添加 Case"}</button></form></Panel> : null}

      <Panel><h3 className="font-semibold text-[#263936]">启动真实三基线评测</h3><p className="mt-2 text-sm leading-6 text-[#687370]">服务端将真实运行 dense_only、hybrid_rerank 和 kg_personalized；页面不接收或生成伪造 Case 结果。</p>{selected && selected.type !== "RETRIEVAL" ? <p className="mt-3 rounded-xl bg-[#f5ecdc] px-3 py-2 text-xs text-[#8b602b]">三基线 Runner 只接受已冻结的 Retrieval 数据集；请改选 Retrieval 版本。</p> : null}<div className="mt-4 flex flex-wrap items-end gap-3"><label><FieldLabel>索引版本</FieldLabel><input className={`${fieldClass} w-32`} min="1" onChange={(event) => setIndexVersion(event.target.value)} type="number" value={indexVersion} /></label><button className={primaryButtonClass} disabled={saving || selected?.status !== "FROZEN" || selected?.type !== "RETRIEVAL" || Number(indexVersion) < 1} onClick={() => void launchEvaluation()} type="button">{saving ? <Spinner label="正在排队" /> : "启动服务端评测"}</button></div><details className="mt-4"><summary className="cursor-pointer text-xs font-semibold text-[#61716d]">CLI 备用命令</summary><pre className="mt-2 overflow-x-auto rounded-xl bg-[#102b2a] p-4 text-xs leading-6 text-[#f8f5ed]"><code>{`cd apps/api\n${command}`}</code></pre></details></Panel>

      <section><div className="mb-4 flex flex-wrap items-center justify-between gap-3"><div><p className="section-label">Recorded runs</p><h3 className="mt-2 text-xl font-semibold text-[#263936]">已持久化运行</h3></div><button className={secondaryButtonClass} disabled={loading} onClick={() => void loadEvaluation()} type="button"><RefreshIcon className={`size-4 ${loading ? "animate-spin" : ""}`} />刷新</button></div>{loading ? <Panel><Spinner label="正在读取评测运行" /></Panel> : runs.items.length ? <ul className="grid gap-4">{runs.items.map((run) => <li key={run.id}><Panel><div className="flex flex-wrap items-start justify-between gap-3"><div><div className="flex flex-wrap gap-2"><StatusPill value={run.status} />{run.dataset_type ? <StatusPill value={run.dataset_type} /> : null}</div><p className="mt-3 font-semibold text-[#263936]">{run.dataset_name ?? run.dataset_id}{run.dataset_version ? ` · v${run.dataset_version}` : ""}</p><p className="mt-2 text-xs text-[#687370]">Git {run.git_commit} · Index v{run.index_version} · {formatDate(run.finished_at ?? run.created_at)}</p><p className="mt-1 break-all font-mono text-[10px] text-[#8a9491]">Trace {run.trace_id}</p></div></div>{run.error_message ? <p className="mt-4 rounded-xl bg-[#fff0ec] p-3 text-sm text-[#944237]">{run.error_code}: {run.error_message}</p> : null}<div className="mt-4 grid gap-3 lg:grid-cols-2"><div><p className="text-xs font-semibold text-[#61716d]">真实指标</p><pre className="mt-2 max-h-72 overflow-auto rounded-xl bg-white/70 p-3 text-xs text-[#30433f]">{run.metrics ? JSON.stringify(run.metrics, null, 2) : "暂无指标"}</pre></div><details><summary className="cursor-pointer text-xs font-semibold text-[#61716d]">运行配置与来源</summary><pre className="mt-2 max-h-72 overflow-auto rounded-xl bg-white/70 p-3 text-xs text-[#30433f]">{JSON.stringify(run.config, null, 2)}</pre></details></div></Panel></li>)}</ul> : <EmptyState description="执行上方 CLI 后，真实指标与可追溯配置会显示在这里。" title="尚无评测运行" />}</section>
    </div>
  );
}
