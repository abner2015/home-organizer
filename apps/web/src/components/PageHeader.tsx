import { clsx } from "@/lib/format";

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: string;
  actions?: React.ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-ink-900 sm:text-3xl">{title}</h1>
        {description ? <p className="mt-1 text-sm text-ink-500">{description}</p> : null}
      </div>
      {actions ? <div className="flex flex-wrap gap-2">{actions}</div> : null}
    </div>
  );
}

export function StatCard({
  label,
  value,
  hint,
  tone = "default",
}: {
  label: string;
  value: string | number;
  hint?: string;
  tone?: "default" | "brand";
}) {
  return (
    <div
      className={clsx(
        "card p-4",
        tone === "brand" && "bg-gradient-to-br from-brand-500 to-brand-600 text-white border-0",
      )}
    >
      <p
        className={clsx(
          "text-xs font-medium",
          tone === "brand" ? "text-brand-100" : "text-ink-500",
        )}
      >
        {label}
      </p>
      <p className="mt-1.5 text-2xl font-semibold tracking-tight">{value}</p>
      {hint ? (
        <p
          className={clsx(
            "mt-1 text-xs",
            tone === "brand" ? "text-brand-100" : "text-ink-400",
          )}
        >
          {hint}
        </p>
      ) : null}
    </div>
  );
}
