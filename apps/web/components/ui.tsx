"use client";

import Link from "next/link";

import { ApiError } from "@/lib/api";
import { BookIcon } from "@/components/icons";

export const fieldClass =
  "w-full rounded-xl border border-[#172523]/14 bg-white px-3.5 py-3 text-sm text-[#172523] shadow-sm outline-none transition placeholder:text-[#8b9592] focus:border-[#2f6961] focus:ring-3 focus:ring-[#2f6961]/10 disabled:cursor-not-allowed disabled:bg-[#ecebe5]";

export const primaryButtonClass =
  "inline-flex min-h-11 items-center justify-center gap-2 rounded-xl bg-[#102b2a] px-4 py-2.5 text-sm font-semibold text-[#f8f5ed] shadow-[0_8px_20px_rgba(16,43,42,0.13)] transition hover:bg-[#1b4642] disabled:cursor-not-allowed disabled:opacity-55";

export const secondaryButtonClass =
  "inline-flex min-h-11 items-center justify-center gap-2 rounded-xl border border-[#172523]/14 bg-white/70 px-4 py-2.5 text-sm font-semibold text-[#263936] transition hover:bg-white disabled:cursor-not-allowed disabled:opacity-55";

export const dangerButtonClass =
  "inline-flex min-h-10 items-center justify-center gap-2 rounded-xl border border-[#a84235]/20 bg-[#fff4f1] px-3.5 py-2 text-sm font-semibold text-[#973c31] transition hover:bg-[#ffe9e4] disabled:cursor-not-allowed disabled:opacity-55";

export function Brand({ href = "/" }: { href?: string }) {
  return (
    <Link className="group inline-flex items-center gap-3" href={href}>
      <span className="grid size-9 place-items-center rounded-xl bg-[#102b2a] text-[#f8f5ed] shadow-[0_8px_22px_rgba(16,43,42,0.16)] transition-transform group-hover:-rotate-2">
        <BookIcon className="size-5" />
      </span>
      <span className="text-[17px] font-semibold tracking-[-0.025em]">
        CoursePilot
      </span>
    </Link>
  );
}

export function Spinner({ label = "正在加载" }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-2" role="status">
      <span
        aria-hidden="true"
        className="size-4 animate-spin rounded-full border-2 border-current border-r-transparent opacity-70 motion-reduce:animate-none"
      />
      <span>{label}</span>
    </span>
  );
}

export function PageLoading({ label = "正在加载工作台…" }: { label?: string }) {
  return (
    <main className="grid min-h-screen place-items-center bg-[#f3f1ea] px-5 text-[#52615e]">
      <Spinner label={label} />
    </main>
  );
}

export function ErrorNotice({
  error,
  onRetry,
}: {
  error: unknown;
  onRetry?: () => void;
}) {
  if (!error) return null;
  const message = error instanceof Error ? error.message : "请求失败，请稍后重试";
  const requestId = error instanceof ApiError ? error.requestId : undefined;
  return (
    <div
      aria-live="polite"
      className="rounded-2xl border border-[#a84235]/18 bg-[#fff4f1] p-4 text-sm text-[#71372f]"
      role="alert"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="font-semibold">{message}</p>
          {requestId ? (
            <p className="mt-1 font-mono text-[11px] text-[#9a625a]">
              请求 ID：{requestId}
            </p>
          ) : null}
        </div>
        {onRetry ? (
          <button
            className="rounded-lg border border-current/20 px-3 py-1.5 text-xs font-semibold hover:bg-white/60"
            onClick={onRetry}
            type="button"
          >
            重试
          </button>
        ) : null}
      </div>
    </div>
  );
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="rounded-2xl border border-dashed border-[#172523]/18 bg-white/38 px-5 py-10 text-center">
      <p className="font-semibold text-[#2b3e3b]">{title}</p>
      <p className="mx-auto mt-2 max-w-lg text-sm leading-6 text-[#6a7572]">
        {description}
      </p>
      {action ? <div className="mt-5">{action}</div> : null}
    </div>
  );
}

export function StatusPill({ value }: { value?: string | null }) {
  const text = value || "UNKNOWN";
  const normalized = text.toUpperCase();
  const positive = ["ACTIVE", "PUBLISHED", "APPROVED", "READY_FOR_REVIEW"].includes(
    normalized,
  );
  const negative = ["FAILED", "REJECTED", "CANCELLED", "ARCHIVED"].includes(
    normalized,
  );
  return (
    <span
      className={`inline-flex w-fit rounded-full border px-2.5 py-1 text-[10px] font-bold tracking-[0.08em] ${
        positive
          ? "border-[#2f6961]/15 bg-[#e6f0eb] text-[#2f6961]"
          : negative
            ? "border-[#a84235]/14 bg-[#fff0ec] text-[#944237]"
            : "border-[#9b6b2d]/14 bg-[#f5ecdc] text-[#8b602b]"
      }`}
    >
      {text}
    </span>
  );
}

export function SectionHeading({
  eyebrow,
  title,
  description,
  action,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-end">
      <div>
        {eyebrow ? <p className="section-label">{eyebrow}</p> : null}
        <h2 className="mt-2 text-2xl font-semibold tracking-[-0.035em] text-[#172523] sm:text-3xl">
          {title}
        </h2>
        {description ? (
          <p className="mt-2 max-w-2xl text-sm leading-6 text-[#64706d]">
            {description}
          </p>
        ) : null}
      </div>
      {action}
    </div>
  );
}

export function formatDate(value?: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}
