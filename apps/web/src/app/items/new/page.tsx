"use client";

import { Suspense, useEffect, useState, useTransition, useRef } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { api, APIError } from "@/lib/api";
import { getSession } from "@/lib/session";
import { PageHeader } from "@/components/PageHeader";
import { Spinner, ErrorState, Loading } from "@/components/States";
import { RecommendationTree } from "@/components/RecommendationTree";
import { formatConfidence, formatSlotPath, CATEGORY_LABEL, clsx } from "@/lib/format";
import type {
  CandidateView,
  EstimatedSize,
  Item,
  ItemUpsertBody,
  ItemVision,
  RecommendResponse,
  StorageUnit,
} from "@/lib/types";

type Step = "upload" | "preview" | "recognize" | "confirm" | "recommend" | "save" | "done";

type FormState = {
  name: string;
  category: string;
  subcategory: string;
  description: string;
  estimated_size: EstimatedSize | "";
  is_sensitive: boolean;
  needs_lock: boolean;
};

const EMPTY_FORM: FormState = {
  name: "",
  category: "",
  subcategory: "",
  description: "",
  estimated_size: "",
  is_sensitive: false,
  needs_lock: false,
};

function toUpsertBody(form: FormState): ItemUpsertBody {
  return {
    name: form.name.trim(),
    description: form.description.trim() || null,
    category: form.category.trim() || null,
    subcategory: form.subcategory.trim() || null,
    estimated_size: form.estimated_size || null,
    is_sensitive: form.is_sensitive,
    needs_lock: form.needs_lock,
  };
}

const STEP_LABEL: Record<Step, string> = {
  upload: "上传照片",
  preview: "图片预览",
  recognize: "AI 识别",
  confirm: "确认信息",
  recommend: "AI 推荐",
  save: "确认位置",
  done: "完成",
};

const STEPS: Step[] = ["upload", "preview", "recognize", "confirm", "recommend", "save"];

export default function NewItemPage() {
  return (
    <Suspense
      fallback={
        <div>
          <PageHeader title="添加物品" />
          <div className="card p-8">
            <Loading label="正在准备页面…" />
          </div>
        </div>
      }
    >
      <NewItemPageInner />
    </Suspense>
  );
}

function NewItemPageInner() {
  const router = useRouter();
  const params = useSearchParams();
  const recommendFor = params.get("recommend_for");
  // The assistant's suggestion card links here with a name so the user lands
  // on the manual confirm step with the field already filled.
  const prefillName = params.get("name") ?? "";
  const session = getSession();
  const fileRef = useRef<HTMLInputElement | null>(null);
  const [step, setStep] = useState<Step>(
    recommendFor ? "recommend" : prefillName ? "confirm" : "upload",
  );
  const [file, setFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [objectKey, setObjectKey] = useState<string | null>(null);
  const [item, setItem] = useState<Item | null>(null);
  const [vision, setVision] = useState<ItemVision | null>(null);
  const [form, setForm] = useState<FormState>(() =>
    prefillName ? { ...EMPTY_FORM, name: prefillName } : EMPTY_FORM,
  );
  const [rec, setRec] = useState<RecommendResponse | null>(null);
  const [units, setUnits] = useState<StorageUnit[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [pending, startTransition] = useTransition();
  const [inferring, setInferring] = useState(false);
  const [inferNote, setInferNote] = useState<string | null>(null);
  // The name we last ran inference for. Guards React StrictMode's double
  // invocation and repeated blurs from re-billing the model for one name.
  const inferredFor = useRef<string | null>(null);

  // If we came from a "获取推荐位置" link on an existing item, load it
  // directly into the recommend step.
  useState(() => {
    if (!recommendFor) return;
    void (async () => {
      setBusy(true);
      try {
        const it = await api.getItem(recommendFor, session.userId, session.homeId);
        setItem(it);
        setForm({
          ...EMPTY_FORM,
          name: it.name,
          category: it.category ?? "",
          subcategory: it.subcategory ?? "",
          description: it.description ?? "",
          estimated_size: (it.estimated_size as EstimatedSize | null) ?? "",
          is_sensitive: it.is_sensitive ?? false,
          needs_lock: it.needs_lock ?? false,
        });
        await runRecommendFor(it);
      } catch (err) {
        setError(extractError(err, "无法加载物品"));
      } finally {
        setBusy(false);
      }
    })();
  });

  // Merge a model-produced patch into the form. Blank strings are skipped so a
  // partial answer never wipes something the user already typed; the booleans
  // are applied as-is (the form starts false, so a `false` is a no-op).
  function applyInferred(patch: {
    name?: string;
    category?: string;
    subcategory?: string;
    description?: string;
    estimated_size?: EstimatedSize | "";
    is_sensitive?: boolean;
    needs_lock?: boolean;
  }) {
    setForm((prev) => ({
      ...prev,
      name: patch.name?.trim() ? patch.name : prev.name,
      category: patch.category?.trim() ? patch.category : prev.category,
      subcategory: patch.subcategory?.trim() ? patch.subcategory : prev.subcategory,
      description: patch.description?.trim() ? patch.description : prev.description,
      estimated_size: patch.estimated_size || prev.estimated_size,
      is_sensitive: patch.is_sensitive ?? prev.is_sensitive,
      needs_lock: patch.needs_lock ?? prev.needs_lock,
    }));
  }

  // Ask the model to fill the rest of the form from the name alone. Used by
  // the manual (photo-free) path, including entries from the assistant's
  // suggestion card (`?name=…`). A failure is NOT fatal — the user can still
  // type everything by hand, which is why this never calls `setError`.
  async function runInfer(name: string) {
    const trimmed = name.trim();
    if (!trimmed || inferredFor.current === trimmed) return;
    inferredFor.current = trimmed;
    setInferring(true);
    setInferNote(null);
    try {
      const r = await api.inferItem(
        { name: trimmed },
        session.userId,
        session.homeId,
      );
      setVision(r.vision);
      applyInferred({
        name: r.vision.name,
        category: r.vision.category,
        subcategory: r.vision.subcategory,
        description: r.vision.description,
        estimated_size: r.vision.estimated_size ?? "",
        is_sensitive: r.vision.is_sensitive,
        needs_lock: r.vision.needs_lock,
      });
    } catch (err) {
      setInferNote(`AI 补全失败，请手动填写：${extractError(err, "未知错误")}`);
    } finally {
      setInferring(false);
    }
  }

  // Landing here from the assistant's "放哪里" CTA carries the name in the
  // query string, so the form can be filled before the user does anything.
  useEffect(() => {
    if (prefillName) void runInfer(prefillName);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefillName]);

  function onPickFile(f: File) {
    setFile(f);
    setPreviewUrl(URL.createObjectURL(f));
    setStep("preview");
  }

  async function uploadAndRecognize() {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      // 1) upload the bytes through the API (works with any storage backend —
      //    the direct-to-storage presign flow needs an object store the
      //    browser can reach).
      const uploaded = await api.uploadAsset(file, session.userId, session.homeId);
      setObjectKey(uploaded.object_key);
      setStep("recognize");
      // 2) create the item record (no vision yet)
      const created = await api.createItem(
        {
          name: "未命名物品",
          image_object_keys: [uploaded.object_key],
          primary_image_object_key: uploaded.object_key,
        },
        session.userId,
        session.homeId,
      );
      setItem(created);
      // 3) trigger vision recognition — it also writes the result onto the
      //    item, so the values below are already persisted.
      const r = await api.recognizeItem(created.id, session.userId, session.homeId);
      setVision(r.vision);
      applyInferred({
        name: r.vision.name,
        category: r.vision.category,
        subcategory: r.vision.subcategory,
        description: r.vision.description,
        estimated_size: r.vision.estimated_size ?? "",
        is_sensitive: r.vision.is_sensitive,
        needs_lock: r.vision.needs_lock,
      });
      setStep("confirm");
    } catch (err) {
      setError(extractError(err, "上传或识别失败"));
      setStep("upload");
    } finally {
      setBusy(false);
    }
  }

  async function requestRecommend() {
    setBusy(true);
    setError(null);
    setStep("recommend");
    try {
      // Persist the (possibly user-edited) fields before recommending: create
      // when this is the manual, photo-free path, otherwise PATCH the item
      // vision already created.
      const body = toUpsertBody(form);
      const target = item
        ? await api.updateItem(item.id, body, session.userId, session.homeId)
        : await api.createItem(body, session.userId, session.homeId);
      setItem(target);

      const r = await api.recommend(target.id, session.userId, session.homeId);
      setRec(r);
      // Load the storage tree so we can visualise cabinet → layer → slot
      try {
        const tree = await api.getSpaceTree(session.userId, session.homeId);
        setUnits(
          tree.rooms.flatMap((room) =>
            (room.units ?? []).map((u) => ({ ...u })),
          ),
        );
      } catch {
        // tree load is best-effort; recommendations still work without it.
      }
      setStep("save");
    } catch (err) {
      setError(extractError(err, "推荐失败"));
    } finally {
      setBusy(false);
    }
  }

  async function runRecommendFor(target: Item) {
    try {
      const r = await api.recommend(target.id, session.userId, session.homeId);
      setRec(r);
      try {
        const tree = await api.getSpaceTree(session.userId, session.homeId);
        setUnits(tree.rooms.flatMap((room) => (room.units ?? []).map((u) => ({ ...u }))));
      } catch {
        // ignore
      }
      setStep("save");
    } catch (err) {
      setError(extractError(err, "推荐失败"));
    }
  }

  async function acceptRecommendation(c: CandidateView) {
    if (!rec?.recommendation_id) return;
    setBusy(true);
    setError(null);
    try {
      await api.acceptRecommendation(
        rec.recommendation_id,
        {},
        session.userId,
        session.homeId,
      );
      setStep("done");
      startTransition(() => {
        setTimeout(() => router.push(`/items/${item?.id}`), 800);
      });
    } catch (err) {
      setError(extractError(err, "保存失败"));
    } finally {
      setBusy(false);
    }
  }

  async function rejectRecommendation(c: CandidateView) {
    if (!rec?.recommendation_id) return;
    try {
      await api.rejectRecommendation(
        rec.recommendation_id,
        { note: "用户选择了其他位置" },
        session.userId,
        session.homeId,
      );
    } catch (err) {
      // non-fatal
      console.error("reject failed", err);
    }
    const remaining = (rec?.candidates ?? []).filter((x) => x.slot_id !== c.slot_id);
    if (!remaining.length) {
      setError("没有更多候选位置了");
      return;
    }
    setRec({ ...(rec as RecommendResponse), candidates: remaining });
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title={recommendFor ? "获取推荐位置" : "添加物品"}
        description={
          recommendFor
            ? "AI 将基于物品属性推荐一个具体到格的位置"
            : "上传一张照片，AI 帮你完成识别和推荐"
        }
      />

      <Stepper current={step} />

      {error ? (
        <ErrorState title="出错了" description={error} onRetry={() => setError(null)} />
      ) : null}

      {step === "upload" ? (
        <UploadCard
          onPick={onPickFile}
          fileRef={fileRef}
          onManual={() => setStep("confirm")}
          busy={busy}
        />
      ) : null}

      {step === "preview" ? (
        <PreviewCard
          url={previewUrl}
          onBack={() => setStep("upload")}
          onContinue={uploadAndRecognize}
          busy={busy}
        />
      ) : null}

      {step === "recognize" ? (
        <div className="card p-8">
          <Loading label="AI 正在识别物品…" />
        </div>
      ) : null}

      {step === "confirm" ? (
        <ConfirmCard
          form={form}
          setForm={setForm}
          vision={vision}
          previewUrl={previewUrl}
          onBack={() => setStep("upload")}
          onContinue={requestRecommend}
          onInfer={() => void runInfer(form.name)}
          onNameBlur={() => void runInfer(form.name)}
          inferring={inferring}
          inferNote={inferNote}
          busy={busy}
        />
      ) : null}

      {step === "recommend" ? (
        <div className="card p-8">
          <Loading label="AI 正在分析最佳位置…" />
        </div>
      ) : null}

      {step === "save" && rec ? (
        <SaveCard
          rec={rec}
          units={units}
          onAccept={acceptRecommendation}
          onReject={rejectRecommendation}
          busy={busy}
        />
      ) : null}

      {step === "done" ? (
        <div className="card p-8 text-center">
          <div className="mx-auto grid h-12 w-12 place-items-center rounded-full bg-emerald-50 text-emerald-600">
            <svg className="h-6 w-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M5 12l4 4L19 7" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </div>
          <h2 className="mt-3 text-base font-semibold text-ink-900">位置已保存</h2>
          <p className="mt-1 text-sm text-ink-500">正在跳转到物品详情…</p>
        </div>
      ) : null}

      {pending ? (
        <div className="fixed inset-0 z-40 grid place-items-center bg-white/60">
          <Spinner className="h-8 w-8" />
        </div>
      ) : null}
    </div>
  );
}

// ------------------------------------------------------------- stepper

function Stepper({ current }: { current: Step }) {
  const idx = STEPS.indexOf(current);
  return (
    <ol className="card flex flex-wrap items-center gap-1 p-3 text-xs">
      {STEPS.map((s, i) => {
        const active = i === idx;
        const done = i < idx;
        return (
          <li
            key={s}
            className={clsx(
              "flex items-center gap-1.5 rounded-full px-2.5 py-1",
              active && "bg-brand-50 text-brand-700 font-medium",
              done && "text-emerald-600",
              !active && !done && "text-ink-400",
            )}
          >
            <span
              className={clsx(
                "grid h-5 w-5 place-items-center rounded-full text-[11px]",
                active && "bg-brand-500 text-white",
                done && "bg-emerald-500 text-white",
                !active && !done && "bg-ink-100 text-ink-500",
              )}
            >
              {done ? "✓" : i + 1}
            </span>
            {STEP_LABEL[s]}
          </li>
        );
      })}
    </ol>
  );
}

// ------------------------------------------------------------- upload

function UploadCard({
  onPick,
  fileRef,
  onManual,
  busy,
}: {
  onPick: (f: File) => void;
  fileRef: React.MutableRefObject<HTMLInputElement | null>;
  onManual: () => void;
  busy: boolean;
}) {
  return (
    <div className="card flex flex-col items-center gap-3 p-10 text-center">
      <div className="grid h-14 w-14 place-items-center rounded-full bg-brand-50 text-brand-500">
        <svg className="h-7 w-7" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
          <path d="M4 16v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2" />
          <path d="M12 4v12" />
          <path d="M7 9l5-5 5 5" />
        </svg>
      </div>
      <h2 className="text-base font-semibold text-ink-900">上传一张物品照片</h2>
      <p className="max-w-sm text-sm text-ink-500">
        支持 JPG / PNG / WebP。AI 会自动识别并推荐收纳位置。
      </p>
      <input
        ref={fileRef}
        type="file"
        accept="image/*"
        capture="environment"
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) onPick(f);
        }}
      />
      <div className="flex flex-wrap items-center justify-center gap-2">
        <button
          className="btn-primary"
          onClick={() => fileRef.current?.click()}
          disabled={busy}
        >
          选择照片
        </button>
        <button className="btn-ghost" onClick={onManual} disabled={busy}>
          跳过照片，手动填写
        </button>
      </div>
    </div>
  );
}

// ------------------------------------------------------------- preview

function PreviewCard({
  url,
  onBack,
  onContinue,
  busy,
}: {
  url: string | null;
  onBack: () => void;
  onContinue: () => void;
  busy: boolean;
}) {
  return (
    <div className="card p-5">
      <div className="overflow-hidden rounded-xl bg-ink-100">
        {url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={url} alt="预览" className="mx-auto max-h-[60vh] object-contain" />
        ) : (
          <div className="grid h-64 place-items-center text-ink-400">无图片</div>
        )}
      </div>
      <div className="mt-4 flex justify-between">
        <button className="btn-ghost" onClick={onBack} disabled={busy}>
          ← 重新选择
        </button>
        <button className="btn-primary" onClick={onContinue} disabled={busy}>
          {busy ? <Spinner className="h-4 w-4" /> : null}
          下一步：AI 识别
        </button>
      </div>
    </div>
  );
}

// ------------------------------------------------------------- confirm

function ConfirmCard({
  form,
  setForm,
  vision,
  previewUrl,
  onBack,
  onContinue,
  onInfer,
  onNameBlur,
  inferring,
  inferNote,
  busy,
}: {
  form: FormState;
  setForm: React.Dispatch<React.SetStateAction<FormState>>;
  vision: ItemVision | null;
  previewUrl: string | null;
  onBack: () => void;
  onContinue: () => void;
  onInfer: () => void;
  onNameBlur: () => void;
  inferring: boolean;
  inferNote: string | null;
  busy: boolean;
}) {
  return (
    <div className="card p-5">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-ink-400">
        {vision ? "AI 识别结果 — 请确认或修改" : "填写物品信息"}
      </h2>
      <div className="mt-4 grid gap-4 md:grid-cols-[200px,1fr]">
        <div className="overflow-hidden rounded-xl bg-ink-100">
          {previewUrl ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={previewUrl} alt="预览" className="aspect-square w-full object-cover" />
          ) : (
            <div className="grid aspect-square w-full place-items-center text-xs text-ink-400">
              未上传照片
            </div>
          )}
        </div>
        <div className="space-y-3">
          {vision ? (
            // No confidence chip: the Phase 4 Vision schema doesn't emit a
            // score, and the backend returns null rather than invent one.
            <div className="rounded-lg bg-brand-50/60 p-3 text-xs text-brand-700">
              AI 已识别：<strong>{vision.name}</strong>
              {vision.description ? <span> · {vision.description}</span> : null}
            </div>
          ) : null}
          <div>
            <label className="label">名称</label>
            <div className="flex gap-2">
              <input
                className="input"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                onBlur={onNameBlur}
                placeholder="例如：雨伞"
              />
              <button
                type="button"
                className="btn-secondary shrink-0 text-xs"
                onClick={onInfer}
                disabled={inferring || !form.name.trim()}
                title="让 AI 根据名称补全其余字段"
              >
                {inferring ? <Spinner className="h-4 w-4" /> : null}
                AI 补全
              </button>
            </div>
            {inferNote ? (
              <p className="mt-1 text-xs text-amber-700">{inferNote}</p>
            ) : null}
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              {/* The 9 values in CATEGORY_LABEL are the backend's whole
                  `Item.category` vocabulary (app/db/enums.py). A free-text
                  input let the user (and the model) type a value no storage
                  slot accepts, which dead-ends the recommendation with
                  `pre_filter_count == 0`. A select makes that impossible and
                  shows Chinese instead of "decor". */}
              <label className="label">分类</label>
              <select
                className="input"
                value={form.category}
                onChange={(e) => setForm({ ...form, category: e.target.value })}
              >
                <option value="">未分类</option>
                {Object.entries(CATEGORY_LABEL).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
                {form.category && !(form.category in CATEGORY_LABEL) ? (
                  <option value={form.category}>{form.category}</option>
                ) : null}
              </select>
            </div>
            <div>
              <label className="label">子分类</label>
              <input
                className="input"
                value={form.subcategory}
                onChange={(e) => setForm({ ...form, subcategory: e.target.value })}
              />
            </div>
          </div>
          <div>
            <label className="label">尺寸</label>
            <select
              className="input"
              value={form.estimated_size}
              onChange={(e) =>
                setForm({ ...form, estimated_size: e.target.value as EstimatedSize | "" })
              }
            >
              <option value="">未知</option>
              <option value="small">小</option>
              <option value="medium">中</option>
              <option value="large">大</option>
            </select>
          </div>
          <div>
            <label className="label">描述（可选）</label>
            <textarea
              className="input min-h-[60px]"
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
            />
          </div>
          {/* Model-filled (vision.v2.md / infer) but user-overridable: these
              two drive the hard-safety verifier, and the model is told to
              prefer false when unsure, so the user is the tie-breaker. */}
          <div className="flex flex-wrap gap-4 rounded-lg bg-ink-50 p-3 text-xs text-ink-700">
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={form.is_sensitive}
                onChange={(e) => setForm({ ...form, is_sensitive: e.target.checked })}
              />
              敏感物品（药品 / 证件 / 贵重品）
            </label>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={form.needs_lock}
                onChange={(e) => setForm({ ...form, needs_lock: e.target.checked })}
              />
              需要上锁存放
            </label>
          </div>
        </div>
      </div>
      <div className="mt-5 flex justify-between">
        <button className="btn-ghost" onClick={onBack} disabled={busy}>
          {previewUrl ? "← 重新拍照" : "← 返回"}
        </button>
        <button
          className="btn-primary"
          onClick={onContinue}
          disabled={busy || !form.name.trim()}
        >
          {busy ? <Spinner className="h-4 w-4" /> : null}
          下一步：获取推荐位置
        </button>
      </div>
    </div>
  );
}

// ------------------------------------------------------------- save

function SaveCard({
  rec,
  units,
  onAccept,
  onReject,
  busy,
}: {
  rec: RecommendResponse;
  units: StorageUnit[];
  onAccept: (c: CandidateView) => void;
  onReject: (c: CandidateView) => void;
  busy: boolean;
}) {
  const recommended = rec.candidates.find((c) => c.is_recommended) ?? rec.candidates[0];
  const alternatives = rec.candidates.filter((c) => c !== recommended);
  return (
    <div className="space-y-5">
      {rec.state === "failed" ? (
        <div className="card border-amber-200 bg-amber-50/50 p-4 text-sm text-amber-800">
          ⚠️ AI 推荐未通过硬规则验证，显示的为兜底结果。
        </div>
      ) : null}
      {recommended ? (
        <div className="card border-brand-200 p-5">
          <p className="text-xs font-semibold uppercase tracking-wide text-brand-600">⭐ 推荐位置</p>
          <h2 className="mt-1 text-lg font-semibold text-ink-900">
            {formatSlotPath(recommended)}
          </h2>
          <p className="mt-2 text-sm text-ink-700">{recommended.reason}</p>
          <div className="mt-3 flex items-center gap-2 text-xs text-ink-500">
            <span className="badge">置信度 {formatConfidence(recommended.confidence)}</span>
            {recommended.matched_rules && recommended.matched_rules.length > 0 ? (
              <span className="badge">匹配规则 {recommended.matched_rules.length}</span>
            ) : null}
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            <button
              className="btn-primary"
              onClick={() => onAccept(recommended)}
              disabled={busy}
            >
              {busy ? <Spinner className="h-4 w-4" /> : null}
              确认放在这里
            </button>
            {recommended ? (
              <button
                className="btn-ghost"
                onClick={() => onReject(recommended)}
                disabled={busy}
              >
                不合适，换一个
              </button>
            ) : null}
          </div>
        </div>
      ) : (
        <div className="card p-5 text-sm text-ink-500">没有可用候选</div>
      )}

      <section>
        <h3 className="mb-3 text-sm font-semibold uppercase tracking-wide text-ink-400">
          备选位置
        </h3>
        {alternatives.length === 0 ? (
          <p className="text-sm text-ink-500">暂无备选</p>
        ) : (
          <div className="space-y-2">
            {alternatives.map((c) => (
              <div key={c.slot_id} className="card flex items-center justify-between p-3">
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-ink-800">
                    {formatSlotPath(c)}
                  </p>
                  <p className="mt-0.5 truncate text-xs text-ink-500">{c.reason}</p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <span className="badge">{formatConfidence(c.confidence)}</span>
                  <button
                    className="btn-secondary !py-1.5 !px-3 text-xs"
                    onClick={() => onAccept(c)}
                    disabled={busy}
                  >
                    选这个
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {units.length > 0 ? (
        <section>
          <h3 className="mb-3 text-sm font-semibold uppercase tracking-wide text-ink-400">
            收纳空间结构
          </h3>
          <RecommendationTree units={units} candidates={rec.candidates} />
        </section>
      ) : null}
    </div>
  );
}

function extractError(err: unknown, fallback: string): string {
  if (err instanceof APIError) return err.message;
  if (err instanceof Error) return err.message;
  return fallback;
}
