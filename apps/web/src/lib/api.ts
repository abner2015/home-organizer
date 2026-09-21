// Browser-side API client. All requests go through Next.js rewrites
// (configured in `next.config.mjs`) which proxy to the FastAPI backend —
// so the browser only ever talks to its own origin, and no API key
// is ever exposed to the client.

import type {
  AcceptRequest,
  AcceptResponse,
  AssetRecognizeResponse,
  AssetUploadResponse,
  CandidateListResponse,
  InferItemBody,
  InferItemResponse,
  Item,
  ItemCreateBody,
  ItemPlacement,
  ItemUpsertBody,
  ItemVisionResponse,
  PaginatedItems,
  PatchRequest,
  PatchResponse,
  PresignRequest,
  PresignResponse,
  RecommendResponse,
  RejectRequest,
  RejectResponse,
  SearchRequestBody,
  SearchResponseBody,
  SpaceTree,
  StorageSlot,
  StorageUnit,
  UUID,
  Home,
  Room,
} from "./types";

// In the browser we hit relative URLs so the Next.js rewrite kicks in.
// During SSR we can fall back to the configured API base.
const isBrowser = typeof window !== "undefined";

function defaultHeaders(userId: UUID, homeId: UUID): Record<string, string> {
  return {
    "Content-Type": "application/json",
    "X-User-Id": userId,
    "X-Home-Id": homeId,
  };
}

export class APIError extends Error {
  status: number;
  payload: unknown;
  constructor(message: string, status: number, payload: unknown) {
    super(message);
    this.name = "APIError";
    this.status = status;
    this.payload = payload;
  }
}

async function request<T>(
  path: string,
  init: RequestInit & { userId: UUID; homeId: UUID },
): Promise<T> {
  const { userId, homeId, ...rest } = init;
  const overrideHeaders: Record<string, string> = {};
  if (rest.headers) {
    if (rest.headers instanceof Headers) {
      rest.headers.forEach((v, k) => {
        overrideHeaders[k] = v;
      });
    } else if (Array.isArray(rest.headers)) {
      for (const [k, v] of rest.headers) overrideHeaders[k] = v;
    } else {
      Object.assign(overrideHeaders, rest.headers);
    }
  }
  const headers: Record<string, string> = {
    ...defaultHeaders(userId, homeId),
    ...overrideHeaders,
  };
  // FormData must keep the browser-generated multipart boundary; the default
  // `application/json` would make the server unable to parse the body.
  if (rest.body instanceof FormData) delete headers["Content-Type"];
  const url = isBrowser ? path : `${process.env.NEXT_PUBLIC_API_BASE_URL ?? ""}${path}`;
  const res = await fetch(url, { ...rest, headers, cache: "no-store" });
  const text = await res.text();
  let body: unknown = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }
  if (!res.ok) {
    const message =
      (body && typeof body === "object" && "error" in body
        ? String((body as { error: { message?: string } }).error?.message ?? "")
        : "") || `请求失败：${res.status}`;
    throw new APIError(message || `请求失败：${res.status}`, res.status, body);
  }
  return body as T;
}

// ------------------------------------------------------------- identity

export const api = {
  // NOTE: there is deliberately no client method for GET /api/v1/auth/me.
  // That route requires a JWT bearer token, while every other route this app
  // calls authenticates with the stub X-User-Id / X-Home-Id headers (see
  // `session.ts`). Adding a client method that could only ever 401 would be
  // worse than omitting it. Wiring real JWTs is a separate piece of work.

  // ------------------------------------------------------------- homes

  async listHomes(userId: UUID, homeId: UUID): Promise<Home[]> {
    return request<Home[]>("/api/v1/homes", { method: "GET", userId, homeId });
  },

  async getHome(targetHomeId: UUID, userId: UUID, homeId: UUID): Promise<Home> {
    return request<Home>(`/api/v1/homes/${targetHomeId}`, {
      method: "GET",
      userId,
      homeId,
    });
  },

  async getSpaceTree(userId: UUID, homeId: UUID): Promise<SpaceTree> {
    return request<SpaceTree>(`/api/v1/homes/${homeId}/space-tree`, {
      method: "GET",
      userId,
      homeId,
    });
  },

  // ------------------------------------------------------------- rooms / storage

  async listRooms(userId: UUID, homeId: UUID): Promise<Room[]> {
    return request<Room[]>(`/api/v1/homes/${homeId}/rooms`, {
      method: "GET",
      userId,
      homeId,
    });
  },

  async listStorageUnits(
    roomId: UUID,
    userId: UUID,
    homeId: UUID,
  ): Promise<StorageUnit[]> {
    return request<StorageUnit[]>(`/api/v1/rooms/${roomId}/storage-units`, {
      method: "GET",
      userId,
      homeId,
    });
  },

  async listAllSlots(userId: UUID, homeId: UUID): Promise<StorageSlot[]> {
    return request<StorageSlot[]>(`/api/v1/homes/${homeId}/slots`, {
      method: "GET",
      userId,
      homeId,
    });
  },

  // ------------------------------------------------------------- items

  async listItems(
    userId: UUID,
    homeId: UUID,
    params: { q?: string; category?: string; page?: number; page_size?: number } = {},
  ): Promise<PaginatedItems> {
    const qs = new URLSearchParams();
    if (params.q) qs.set("q", params.q);
    if (params.category) qs.set("category", params.category);
    if (params.page) qs.set("page", String(params.page));
    if (params.page_size) qs.set("page_size", String(params.page_size));
    const suffix = qs.toString() ? `?${qs.toString()}` : "";
    return request<PaginatedItems>(`/api/v1/items${suffix}`, {
      method: "GET",
      userId,
      homeId,
    });
  },

  async getItem(itemId: UUID, userId: UUID, homeId: UUID): Promise<Item> {
    return request<Item>(`/api/v1/items/${itemId}`, {
      method: "GET",
      userId,
      homeId,
    });
  },

  async getItemPlacements(
    itemId: UUID,
    userId: UUID,
    homeId: UUID,
  ): Promise<ItemPlacement[]> {
    return request<ItemPlacement[]>(`/api/v1/items/${itemId}/placements`, {
      method: "GET",
      userId,
      homeId,
    });
  },

  async createItem(
    body: ItemCreateBody,
    userId: UUID,
    homeId: UUID,
  ): Promise<Item> {
    return request<Item>("/api/v1/items", {
      method: "POST",
      userId,
      homeId,
      body: JSON.stringify(body),
    });
  },

  // PATCH is a sparse diff: omitted keys are left untouched server-side.
  async updateItem(
    itemId: UUID,
    body: Partial<ItemUpsertBody>,
    userId: UUID,
    homeId: UUID,
  ): Promise<Item> {
    return request<Item>(`/api/v1/items/${itemId}`, {
      method: "PATCH",
      userId,
      homeId,
      body: JSON.stringify(body),
    });
  },

  // ------------------------------------------------------------- vision

  async recognizeItem(
    itemId: UUID,
    userId: UUID,
    homeId: UUID,
  ): Promise<ItemVisionResponse> {
    return request<ItemVisionResponse>(`/api/v1/items/${itemId}/vision`, {
      method: "POST",
      userId,
      homeId,
    });
  },

  // Vision on a bare uploaded asset. Not used by the item flow (which goes
  // through `recognizeItem`) but kept because the endpoint exists and is the
  // stricter ingest path.
  async recognizeImage(
    body: { asset_id: UUID; description?: string },
    userId: UUID,
    homeId: UUID,
  ): Promise<AssetRecognizeResponse> {
    return request<AssetRecognizeResponse>("/api/v1/items/recognize", {
      method: "POST",
      userId,
      homeId,
      body: JSON.stringify(body),
    });
  },

  // ------------------------------------------------------------- recommendation

  async recommend(
    itemId: UUID,
    userId: UUID,
    homeId: UUID,
    body: Record<string, never> = {},
  ): Promise<RecommendResponse> {
    return request<RecommendResponse>(
      `/api/v1/recommendations/items/${itemId}/recommend`,
      { method: "POST", userId, homeId, body: JSON.stringify(body) },
    );
  },

  async acceptRecommendation(
    recId: UUID,
    body: AcceptRequest,
    userId: UUID,
    homeId: UUID,
  ): Promise<AcceptResponse> {
    return request<AcceptResponse>(`/api/v1/recommendations/${recId}/accept`, {
      method: "POST",
      userId,
      homeId,
      body: JSON.stringify(body),
    });
  },

  async rejectRecommendation(
    recId: UUID,
    body: RejectRequest,
    userId: UUID,
    homeId: UUID,
  ): Promise<RejectResponse> {
    return request<RejectResponse>(`/api/v1/recommendations/${recId}/reject`, {
      method: "POST",
      userId,
      homeId,
      body: JSON.stringify(body),
    });
  },

  async patchRecommendation(
    recId: UUID,
    body: PatchRequest,
    userId: UUID,
    homeId: UUID,
  ): Promise<PatchResponse> {
    return request<PatchResponse>(`/api/v1/recommendations/${recId}`, {
      method: "PATCH",
      userId,
      homeId,
      body: JSON.stringify(body),
    });
  },

  async getRecommendation(
    recId: UUID,
    userId: UUID,
    homeId: UUID,
  ): Promise<RecommendResponse> {
    return request<RecommendResponse>(`/api/v1/recommendations/${recId}`, {
      method: "GET",
      userId,
      homeId,
    });
  },

  async listCandidates(
    itemId: UUID,
    userId: UUID,
    homeId: UUID,
  ): Promise<CandidateListResponse> {
    return request<CandidateListResponse>(`/api/v1/items/${itemId}/candidates`, {
      method: "GET",
      userId,
      homeId,
    });
  },

  // ------------------------------------------------------------- search

  async search(
    body: SearchRequestBody,
    userId: UUID,
    homeId: UUID,
  ): Promise<SearchResponseBody> {
    return request<SearchResponseBody>("/api/v1/search", {
      method: "POST",
      userId,
      homeId,
      body: JSON.stringify(body),
    });
  },

  // ------------------------------------------------------------- uploads

  async presignUpload(
    body: PresignRequest,
    userId: UUID,
    homeId: UUID,
  ): Promise<PresignResponse> {
    return request<PresignResponse>("/api/v1/uploads/presign", {
      method: "POST",
      userId,
      homeId,
      body: JSON.stringify(body),
    });
  },

  // Send the bytes through the API instead of direct-to-storage. Works with
  // every storage backend (the presign path needs an object store the browser
  // can reach).
  async uploadAsset(
    file: File,
    userId: UUID,
    homeId: UUID,
  ): Promise<AssetUploadResponse> {
    const form = new FormData();
    form.append("file", file);
    return request<AssetUploadResponse>("/api/v1/assets/upload", {
      method: "POST",
      userId,
      homeId,
      body: form,
    });
  },

  // Fill an item's attributes from its name. Read-only server-side (one trace
  // row), so calling it repeatedly while the user types is safe.
  async inferItem(
    body: InferItemBody,
    userId: UUID,
    homeId: UUID,
  ): Promise<InferItemResponse> {
    return request<InferItemResponse>("/api/v1/items/infer", {
      method: "POST",
      userId,
      homeId,
      body: JSON.stringify(body),
    });
  },

  // `contentType` must be byte-identical to the one passed to `presignUpload`:
  // the backend includes it in the signature, so a mismatch is a 403 from
  // MinIO. Callers should compute it once and pass the same value to both.
  async putToPresignedUrl(url: string, file: File, contentType: string): Promise<void> {
    const res = await fetch(url, {
      method: "PUT",
      headers: { "Content-Type": contentType },
      body: file,
    });
    if (!res.ok) {
      throw new APIError(`上传失败：${res.status}`, res.status, null);
    }
  },
};
