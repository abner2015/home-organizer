// Browser-side API client. All requests go through Next.js rewrites
// (configured in `next.config.mjs`) which proxy to the FastAPI backend —
// so the browser only ever talks to its own origin, and no API key
// is ever exposed to the client.

import type {
  AcceptRequest,
  CandidateView,
  Item,
  ItemPlacement,
  PaginatedItems,
  PresignRequest,
  PresignResponse,
  RecognizeResponse,
  RecommendResponse,
  RejectRequest,
  PatchRequest,
  SearchRequestBody,
  SearchResponseBody,
  SpaceTree,
  StorageSlot,
  StorageUnit,
  UUID,
  User,
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
  async getMe(userId: UUID, homeId: UUID): Promise<User> {
    return request<User>("/api/v1/auth/me", { method: "GET", userId, homeId });
  },

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
    body: {
      name: string;
      description?: string;
      category?: string;
      subcategory?: string;
      image_object_keys?: string[];
      primary_image_object_key?: string;
    },
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

  // ------------------------------------------------------------- vision

  async recognizeItem(
    itemId: UUID,
    userId: UUID,
    homeId: UUID,
  ): Promise<RecognizeResponse> {
    return request<RecognizeResponse>(`/api/v1/items/${itemId}/vision`, {
      method: "POST",
      userId,
      homeId,
    });
  },

  async recognizeImage(
    body: { asset_id: UUID; description?: string },
    userId: UUID,
    homeId: UUID,
  ): Promise<RecognizeResponse> {
    return request<RecognizeResponse>("/api/v1/items/recognize", {
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
    body: { include_history?: boolean } = {},
  ): Promise<RecommendResponse> {
    return request<RecommendResponse>(`/api/v1/items/${itemId}/recommend`, {
      method: "POST",
      userId,
      homeId,
      body: JSON.stringify(body),
    });
  },

  async acceptRecommendation(
    recId: UUID,
    body: AcceptRequest,
    userId: UUID,
    homeId: UUID,
  ): Promise<{ placement: ItemPlacement }> {
    return request<{ placement: ItemPlacement }>(
      `/api/v1/recommendations/${recId}/accept`,
      { method: "POST", userId, homeId, body: JSON.stringify(body) },
    );
  },

  async rejectRecommendation(
    recId: UUID,
    body: RejectRequest,
    userId: UUID,
    homeId: UUID,
  ): Promise<{ ok: true }> {
    return request<{ ok: true }>(`/api/v1/recommendations/${recId}/reject`, {
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
  ): Promise<{ placement: ItemPlacement }> {
    return request<{ placement: ItemPlacement }>(
      `/api/v1/recommendations/${recId}/adjust`,
      { method: "POST", userId, homeId, body: JSON.stringify(body) },
    );
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
  ): Promise<{
    vision?: unknown;
    pre_filter?: Array<{ slot_id: UUID; score: number }>;
    post_filter?: Array<{ slot_id: UUID; score: number }>;
    final_candidates?: CandidateView[];
  }> {
    return request(`/api/v1/items/${itemId}/candidates`, {
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
