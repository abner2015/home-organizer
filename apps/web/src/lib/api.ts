// Browser-side API client. All requests go through Next.js rewrites
// (configured in `next.config.mjs`) which proxy to the FastAPI backend —
// so the browser only ever talks to its own origin, and no API key
// is ever exposed to the client.
//
// Identity is passed in explicitly as an `ApiSession` rather than read from a
// module-level global. Server Components get theirs from `session.server.ts`
// (request cookies) and Client Components from `session.ts` (document
// cookies); a shared mutable global would leak one visitor's token into
// another's request on the server.

import type {
  AcceptRequest,
  AcceptResponse,
  AssetRecognizeResponse,
  AssetUploadResponse,
  CandidateListResponse,
  Home,
  HomeUpdateBody,
  InferItemBody,
  InferItemResponse,
  Item,
  ItemCreateBody,
  ItemPlacement,
  ItemUpsertBody,
  ItemVisionResponse,
  LoginBody,
  PaginatedItems,
  PatchRequest,
  PatchResponse,
  PresignRequest,
  PresignResponse,
  ProposeStructureBody,
  RecommendResponse,
  RejectRequest,
  RejectResponse,
  Room,
  RoomCreateBody,
  SearchRequestBody,
  SearchResponseBody,
  SectionCreateBody,
  SignupBody,
  SlotCreateBody,
  SpaceTree,
  StorageSection,
  StorageSlot,
  StorageUnit,
  StructureProposalResponse,
  TokenResponse,
  UnitCreateBody,
  User,
  UUID,
} from "./types";
import { clearSession, getSession, updateSessionTokens } from "./session";

// In the browser we hit relative URLs so the Next.js rewrite kicks in.
// During SSR we can fall back to the configured API base.
const isBrowser = typeof window !== "undefined";

/**
 * What `request()` needs to talk to the API: a bearer token, plus the home to
 * act in. `homeId` is optional because the calls made *before* a home is known
 * (login, signup, listing your homes) have no home to name yet.
 */
export interface ApiSession {
  token: string;
  homeId?: UUID;
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

/**
 * Trade the stored refresh token for a new pair, in the browser only.
 *
 * Reads the token straight from the cookie rather than from a `Session`, so it
 * still works when the caller is holding a session object it captured earlier
 * in the page's life. Returns whether the cookies were replaced.
 */
async function renewSession(): Promise<boolean> {
  const refreshToken = getSession()?.refreshToken;
  if (!refreshToken) return false;
  try {
    const tokens = await api.refresh(refreshToken);
    updateSessionTokens(tokens);
    return true;
  } catch {
    // Either the refresh token is no longer good or the API is unreachable.
    // Both mean "this session cannot continue"; the caller signs out.
    return false;
  }
}

async function request<T>(
  path: string,
  init: RequestInit & { session?: ApiSession },
  retried = false,
): Promise<T> {
  const { session, ...rest } = init;
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
    "Content-Type": "application/json",
    ...(session ? { Authorization: `Bearer ${session.token}` } : {}),
    ...(session?.homeId ? { "X-Home-Id": session.homeId } : {}),
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
    // A 401 usually just means the access token aged out while this page was
    // open — the common case, since a page can sit idle for an hour without any
    // navigation for the middleware to renew on. Try the refresh token once
    // before concluding anything; only if that also fails is the session
    // genuinely over, and then the user belongs on /login rather than on a page
    // that can only ever fail.
    if (res.status === 401 && session && isBrowser) {
      if (!retried && (await renewSession())) {
        // The session object the caller passed in still holds the dead token,
        // so re-read it rather than reusing `session` — and keep the caller's
        // choice about whether to name a home.
        const current = getSession();
        return request<T>(
          path,
          {
            ...init,
            session: current ? { ...session, token: current.token } : session,
          },
          true,
        );
      }
      clearSession();
      window.location.href = "/login";
    }
    const message =
      (body && typeof body === "object" && "error" in body
        ? String((body as { error: { message?: string } }).error?.message ?? "")
        : "") || `请求失败：${res.status}`;
    throw new APIError(message || `请求失败：${res.status}`, res.status, body);
  }
  return body as T;
}

export const api = {
  // ------------------------------------------------------------- auth

  async signup(body: SignupBody): Promise<{ user: User }> {
    return request<{ user: User }>("/api/v1/auth/signup", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  async login(body: LoginBody): Promise<TokenResponse> {
    return request<TokenResponse>("/api/v1/auth/login", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  /**
   * Rotate a refresh token for a new access + refresh pair.
   *
   * Deliberately takes no `session`: a 401 here must not trigger the retry
   * above, or a dead refresh token would recurse instead of signing out.
   */
  async refresh(refreshToken: string): Promise<TokenResponse> {
    return request<TokenResponse>("/api/v1/auth/refresh", {
      method: "POST",
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
  },

  /** The signed-in user. Needs only the token — there is no home yet. */
  async me(token: string): Promise<User> {
    return request<User>("/api/v1/auth/me", {
      method: "GET",
      session: { token },
    });
  },

  // ------------------------------------------------------------- homes

  /**
   * Homes the caller belongs to. Takes the session without `homeId` because
   * this is exactly the call that *discovers* which home to use next.
   */
  async listHomes(session: ApiSession): Promise<Home[]> {
    return request<Home[]>("/api/v1/homes", { method: "GET", session });
  },

  async getHome(targetHomeId: UUID, session: ApiSession): Promise<Home> {
    return request<Home>(`/api/v1/homes/${targetHomeId}`, {
      method: "GET",
      session,
    });
  },

  async getSpaceTree(session: ApiSession): Promise<SpaceTree> {
    return request<SpaceTree>(`/api/v1/homes/${session.homeId}/space-tree`, {
      method: "GET",
      session,
    });
  },

  // ------------------------------------------------------------- rooms / storage

  async listRooms(session: ApiSession): Promise<Room[]> {
    return request<Room[]>(`/api/v1/homes/${session.homeId}/rooms`, {
      method: "GET",
      session,
    });
  },

  async listStorageUnits(roomId: UUID, session: ApiSession): Promise<StorageUnit[]> {
    return request<StorageUnit[]>(`/api/v1/rooms/${roomId}/storage-units`, {
      method: "GET",
      session,
    });
  },

  async listAllSlots(session: ApiSession): Promise<StorageSlot[]> {
    return request<StorageSlot[]>(`/api/v1/homes/${session.homeId}/slots`, {
      method: "GET",
      session,
    });
  },

  // ------------------------------------------------------------- structure writes

  async updateHome(
    body: HomeUpdateBody,
    session: ApiSession,
  ): Promise<Home> {
    return request<Home>(`/api/v1/homes/${session.homeId}`, {
      method: "PATCH",
      session,
      body: JSON.stringify(body),
    });
  },

  async createRoom(body: RoomCreateBody, session: ApiSession): Promise<Room> {
    return request<Room>(`/api/v1/homes/${session.homeId}/rooms`, {
      method: "POST",
      session,
      body: JSON.stringify(body),
    });
  },

  async createUnit(
    roomId: UUID,
    body: UnitCreateBody,
    session: ApiSession,
  ): Promise<StorageUnit> {
    return request<StorageUnit>(`/api/v1/rooms/${roomId}/storage-units`, {
      method: "POST",
      session,
      body: JSON.stringify(body),
    });
  },

  async createSection(
    unitId: UUID,
    body: SectionCreateBody,
    session: ApiSession,
  ): Promise<StorageSection> {
    return request<StorageSection>(`/api/v1/storage-units/${unitId}/sections`, {
      method: "POST",
      session,
      body: JSON.stringify(body),
    });
  },

  async createSlot(
    sectionId: UUID,
    body: SlotCreateBody,
    session: ApiSession,
  ): Promise<StorageSlot> {
    return request<StorageSlot>(`/api/v1/sections/${sectionId}/slots`, {
      method: "POST",
      session,
      body: JSON.stringify(body),
    });
  },

  // ------------------------------------------------------------- structure proposal

  // Ask for a structure. An empty body selects the server-side template: no
  // model is called and nothing is written except (for the other two branches)
  // an AgentTrace. The proposal is not persisted anywhere — the user's ticks in
  // the confirm step are what drive `createRoom`/`createUnit`/… below.
  async proposeStructure(
    body: ProposeStructureBody,
    session: ApiSession,
  ): Promise<StructureProposalResponse> {
    return request<StructureProposalResponse>("/api/v1/structures/propose", {
      method: "POST",
      session,
      body: JSON.stringify(body),
    });
  },

  // ------------------------------------------------------------- items

  async listItems(
    session: ApiSession,
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
      session,
    });
  },

  async getItem(itemId: UUID, session: ApiSession): Promise<Item> {
    return request<Item>(`/api/v1/items/${itemId}`, {
      method: "GET",
      session,
    });
  },

  async getItemPlacements(itemId: UUID, session: ApiSession): Promise<ItemPlacement[]> {
    return request<ItemPlacement[]>(`/api/v1/items/${itemId}/placements`, {
      method: "GET",
      session,
    });
  },

  async createItem(body: ItemCreateBody, session: ApiSession): Promise<Item> {
    return request<Item>("/api/v1/items", {
      method: "POST",
      session,
      body: JSON.stringify(body),
    });
  },

  // PATCH is a sparse diff: omitted keys are left untouched server-side.
  async updateItem(
    itemId: UUID,
    body: Partial<ItemUpsertBody>,
    session: ApiSession,
  ): Promise<Item> {
    return request<Item>(`/api/v1/items/${itemId}`, {
      method: "PATCH",
      session,
      body: JSON.stringify(body),
    });
  },

  // ------------------------------------------------------------- vision

  async recognizeItem(itemId: UUID, session: ApiSession): Promise<ItemVisionResponse> {
    return request<ItemVisionResponse>(`/api/v1/items/${itemId}/vision`, {
      method: "POST",
      session,
    });
  },

  // Vision on a bare uploaded asset. Not used by the item flow (which goes
  // through `recognizeItem`) but kept because the endpoint exists and is the
  // stricter ingest path.
  async recognizeImage(
    body: { asset_id: UUID; description?: string },
    session: ApiSession,
  ): Promise<AssetRecognizeResponse> {
    return request<AssetRecognizeResponse>("/api/v1/items/recognize", {
      method: "POST",
      session,
      body: JSON.stringify(body),
    });
  },

  // ------------------------------------------------------------- recommendation

  async recommend(
    itemId: UUID,
    session: ApiSession,
    body: Record<string, never> = {},
  ): Promise<RecommendResponse> {
    return request<RecommendResponse>(
      `/api/v1/recommendations/items/${itemId}/recommend`,
      { method: "POST", session, body: JSON.stringify(body) },
    );
  },

  async acceptRecommendation(
    recId: UUID,
    body: AcceptRequest,
    session: ApiSession,
  ): Promise<AcceptResponse> {
    return request<AcceptResponse>(`/api/v1/recommendations/${recId}/accept`, {
      method: "POST",
      session,
      body: JSON.stringify(body),
    });
  },

  async rejectRecommendation(
    recId: UUID,
    body: RejectRequest,
    session: ApiSession,
  ): Promise<RejectResponse> {
    return request<RejectResponse>(`/api/v1/recommendations/${recId}/reject`, {
      method: "POST",
      session,
      body: JSON.stringify(body),
    });
  },

  async patchRecommendation(
    recId: UUID,
    body: PatchRequest,
    session: ApiSession,
  ): Promise<PatchResponse> {
    return request<PatchResponse>(`/api/v1/recommendations/${recId}`, {
      method: "PATCH",
      session,
      body: JSON.stringify(body),
    });
  },

  async getRecommendation(recId: UUID, session: ApiSession): Promise<RecommendResponse> {
    return request<RecommendResponse>(`/api/v1/recommendations/${recId}`, {
      method: "GET",
      session,
    });
  },

  async listCandidates(itemId: UUID, session: ApiSession): Promise<CandidateListResponse> {
    return request<CandidateListResponse>(`/api/v1/items/${itemId}/candidates`, {
      method: "GET",
      session,
    });
  },

  // ------------------------------------------------------------- search

  async search(
    body: SearchRequestBody,
    session: ApiSession,
  ): Promise<SearchResponseBody> {
    return request<SearchResponseBody>("/api/v1/search", {
      method: "POST",
      session,
      body: JSON.stringify(body),
    });
  },

  // ------------------------------------------------------------- uploads

  async presignUpload(body: PresignRequest, session: ApiSession): Promise<PresignResponse> {
    return request<PresignResponse>("/api/v1/uploads/presign", {
      method: "POST",
      session,
      body: JSON.stringify(body),
    });
  },

  // Send the bytes through the API instead of direct-to-storage. Works with
  // every storage backend (the presign path needs an object store the browser
  // can reach).
  async uploadAsset(file: File, session: ApiSession): Promise<AssetUploadResponse> {
    const form = new FormData();
    form.append("file", file);
    return request<AssetUploadResponse>("/api/v1/assets/upload", {
      method: "POST",
      session,
      body: form,
    });
  },

  // Fill an item's attributes from its name. Read-only server-side (one trace
  // row), so calling it repeatedly while the user types is safe.
  async inferItem(body: InferItemBody, session: ApiSession): Promise<InferItemResponse> {
    return request<InferItemResponse>("/api/v1/items/infer", {
      method: "POST",
      session,
      body: JSON.stringify(body),
    });
  },

  // `contentType` must be byte-identical to the one passed to `presignUpload`:
  // the backend includes it in the signature, so a mismatch is a 403 from
  // MinIO. Callers should compute it once and pass the same value to both.
  // No Authorization header — the presigned signature *is* the credential.
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
