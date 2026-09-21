// Domain types shared by the Web app. These mirror the FastAPI Pydantic
// schemas on the backend but are kept independent — the Web app never
// imports the backend's Python types directly.

export type UUID = string;

export type ISODateTime = string;

// ------------------------------------------------------------- identity / homes

export interface Home {
  id: UUID;
  name: string;
  timezone?: string;
  member_count?: number;
  item_count?: number;
  rule_count?: number;
}

export interface User {
  id: UUID;
  email: string;
  display_name?: string;
}

// ------------------------------------------------------------- auth
// Mirrors `app/schemas/auth.py`. A session is a bearer access token (who you
// are) plus a home id (which of your homes you are acting in) — the backend
// re-verifies that pairing on every request.

export interface SignupBody {
  email: string;
  password: string;
  display_name: string;
}

export interface LoginBody {
  email: string;
  password: string;
}

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: "bearer";
  expires_in: number;
}

// ------------------------------------------------------------- storage tree

export type RoomType = "bedroom" | "kitchen" | "living" | "bathroom" | "study" | "garage" | "other";

export interface Room {
  id: UUID;
  home_id: UUID;
  name: string;
  room_type: RoomType;
  sort_order?: number;
  unit_count?: number;
}

export type UnitType = "cabinet" | "shelf" | "drawer" | "box" | "rack" | "other";
export type SectionType = "layer" | "drawer" | "compartment" | "shelf" | "other";

export interface StorageSlot {
  id: UUID;
  section_id: UUID;
  code: string;
  label?: string;
  capacity_hint?: string;
  allowed_categories?: string[];
  active_count?: number;
}

export interface StorageSection {
  id: UUID;
  unit_id: UUID;
  name: string;
  section_type: SectionType;
  sort_order?: number;
  slots: StorageSlot[];
}

export interface StorageUnit {
  id: UUID;
  room_id: UUID;
  name: string;
  unit_type: UnitType;
  sort_order?: number;
  sections: StorageSection[];
}

export interface SpaceTree {
  home: Home;
  rooms: Array<Room & { units: StorageUnit[] }>;
}

export interface SlotRef {
  slot_id: UUID;
  code: string;
  label: string;
  room_name: string;
  unit_name: string;
  section_name: string;
  full_path: string;
}

// ------------------------------------------------------------- items

// The backend's PlacementRefView: an item's current slot, already resolved to
// a display path. Distinct from SlotRef (which carries the room/unit/section
// parts individually) — see `SlotLocation` in `lib/format.ts`.
export interface PlacementRefView {
  slot_id: UUID;
  slot_path: string;
}

export type EstimatedSize = "small" | "medium" | "large";

export interface Item {
  id: UUID;
  home_id: UUID;
  name: string;
  description?: string;
  category?: string;
  subcategory?: string;
  estimated_size?: EstimatedSize | null;
  is_sensitive?: boolean;
  needs_lock?: boolean;
  primary_image_url?: string;
  image_urls?: string[];
  current_placement?: PlacementRefView | null;
  created_at?: ISODateTime;
  updated_at?: ISODateTime;
}

// Body for POST /items and (partially) PATCH /items/{id}.
export interface ItemUpsertBody {
  name: string;
  description?: string | null;
  category?: string | null;
  subcategory?: string | null;
  estimated_size?: EstimatedSize | null;
  is_sensitive?: boolean;
  needs_lock?: boolean;
}

export interface ItemCreateBody extends ItemUpsertBody {
  image_object_keys?: string[];
  primary_image_object_key?: string | null;
}

export interface ItemPlacement {
  id: UUID;
  item_id: UUID;
  slot_id: UUID;
  slot_path?: string;
  source: "user_manual" | "ai_recommendation";
  placed_at: ISODateTime;
  removed_at?: ISODateTime | null;
  note?: string;
}

export interface PaginatedItems {
  items: Item[];
  page: number;
  page_size: number;
  total: number;
}

// ------------------------------------------------------------- AI / vision

// Vision on an item: POST /items/{id}/vision. Mirrors the backend's
// ItemVisionView.
//
// `confidence` is null and `attributes` empty by design — the Phase 4 Vision
// schema (usage_scene/fragility/notes) doesn't produce them and the backend
// refuses to invent values, so a fragile glass vase is never reported as
// "sensitive". `is_sensitive`/`needs_lock` ARE model-produced (vision.v2.md);
// the form still lets the user override them.
export interface ItemVision {
  name: string;
  category: string;
  subcategory: string;
  description: string;
  confidence: number | null;
  is_sensitive: boolean;
  needs_lock: boolean;
  attributes: string[];
  estimated_size: EstimatedSize | null;
}

export interface ItemVisionResponse {
  item_id: UUID;
  vision: ItemVision;
  trace_id: UUID | null;
}

// Guess an item's attributes from its name alone: POST /items/infer.
// The response carries the *same* `vision` shape as POST /items/{id}/vision,
// so both prefill paths share one code path. Nothing is persisted.
export interface InferItemBody {
  name: string;
  description?: string | null;
}

export interface InferItemResponse {
  vision: ItemVision;
  trace_id: UUID | null;
}

// Vision on a previously uploaded asset: POST /items/recognize. This is the
// Phase 4 contract, which names its fields differently from ItemVision.
export interface AssetRecognitionResult {
  name: string;
  category: string;
  subcategory?: string;
  usage_scene?: string;
  usage_frequency?: "high" | "medium" | "low";
  size_class?: EstimatedSize;
  fragility?: "low" | "medium" | "high";
  notes?: string;
}

export interface AssetRecognizeResponse {
  result: AssetRecognitionResult;
  trace_id: UUID | null;
  attempts: number;
  duration_ms: number;
  provider: string;
}

// ------------------------------------------------------------- recommendations

export interface CandidateView {
  slot_id: UUID;
  code: string;
  label: string;
  full_path: string;
  room_name: string;
  unit_name: string;
  section_name: string;
  score: number;
  confidence: number;
  reason: string;
  matched_rules?: string[];
  evidence_item_ids?: UUID[];
  is_recommended?: boolean;
}

// Mirrors the backend's RecommendResponse (POST /recommendations/items/{id}/recommend
// and GET /recommendations/{rec_id}).
export interface RecommendResponse {
  recommendation_id: UUID;
  item_id: UUID;
  trace_id: UUID;
  chosen_slot_id: UUID | null;
  status: RecommendationStatus;
  state: "answer" | "failed";
  retries_used: number;
  pre_filter_count: number;
  post_filter_count: number;
  candidates: CandidateView[];
  error?: string | null;
}

export type RecommendationStatus = "pending" | "accepted" | "rejected" | "superseded";

// Mirrors the backend's CandidateListResponse
// (GET /items/{id}/candidates — deterministic, no LLM call).
export interface CandidateListResponse {
  pre_filter_count: number;
  post_filter_count: number;
  final_candidates: CandidateView[];
}

export interface PlacementView {
  id: UUID;
  item_id: UUID;
  slot_id: UUID;
  source: "user_manual" | "ai_recommendation";
  is_active: boolean;
  note?: string | null;
}

export interface AcceptRequest {
  note?: string;
}

export interface AcceptResponse {
  recommendation_id: UUID;
  status: RecommendationStatus;
  placement: PlacementView;
}

export interface RejectRequest {
  note?: string;
}

export interface RejectResponse {
  recommendation_id: UUID;
  status: RecommendationStatus;
  note?: string | null;
}

export interface PatchRequest {
  chosen_slot_id: UUID;
  reason?: string;
}

export interface PatchResponse {
  recommendation_id: UUID;
  status: RecommendationStatus;
  chosen_slot_id: UUID;
  candidates: CandidateView[];
}

// ------------------------------------------------------------- search

export type SearchState =
  | "answer"
  | "needs_clarification"
  | "not_found"
  | "exists_but_not_placed"
  | "error";

export type SearchIntent =
  | "find_item"
  | "find_items"
  | "find_location"
  | "check_existence"
  | "list_category"
  | "suggest_placement"
  // Answers questions about the home's storage structure itself (房间/柜子/
  // 收纳位 的数量与布局). Its answer_text comes from the hierarchy, not from
  // items, and `matches` is always empty.
  | "describe_storage"
  | "unknown";

export interface CandidateMatch {
  item_id: UUID;
  name: string;
  category: string;
  subcategory: string;
  is_sensitive: boolean;
  location: SlotRef | null;
}

export interface SearchRequestBody {
  query: string;
  // Omit to start a new conversation; pass the previous turn's
  // conversation_id to keep the assistant's memory of it.
  conversation_id?: UUID;
}

export interface SearchResponseBody {
  answer_text: string;
  state: SearchState;
  intent: SearchIntent;
  matches: CandidateMatch[];
  clarification_question: string | null;
  // Only populated for intent="suggest_placement": where a hypothetical item
  // should go. The backend computes this in memory and persists nothing, so
  // the UI turns it into a CTA into the real add-item flow rather than a link
  // to a recommendation (there isn't one).
  suggested_slot: SlotRef | null;
  suggested_reason: string;
  suggested_item_name: string;
  // Send this back on the next turn so the assistant remembers this one.
  conversation_id: UUID;
  trace_id: UUID | null;
}

// ------------------------------------------------------------- presign / upload

export interface PresignRequest {
  file_name: string;
  content_type: string;
}

export interface PresignResponse {
  upload_url: string;
  object_key: string;
  method: "PUT";
  expires_in: number;
}

// Result of POST /api/v1/assets/upload. Unlike the presign flow the bytes go
// through the API, so this works with any storage backend.
export interface AssetUploadResponse {
  asset_id: UUID;
  object_key: string;
  url: string;
  content_type: string;
  size: number;
  width: number | null;
  height: number | null;
  sha256: string | null;
  deduplicated: boolean;
}
