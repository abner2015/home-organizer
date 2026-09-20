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

export interface Item {
  id: UUID;
  home_id: UUID;
  name: string;
  description?: string;
  category?: string;
  subcategory?: string;
  estimated_size?: "small" | "medium" | "large";
  is_sensitive?: boolean;
  needs_lock?: boolean;
  primary_image_url?: string;
  image_urls?: string[];
  current_placement?: {
    slot_id: UUID;
    slot_path: string;
  } | null;
  created_at?: ISODateTime;
  updated_at?: ISODateTime;
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

export interface VisionOutput {
  name: string;
  category: string;
  subcategory?: string;
  description?: string;
  confidence: number;
  is_sensitive: boolean;
  needs_lock: boolean;
  attributes?: string[];
  estimated_size?: "small" | "medium" | "large";
}

export interface RecognizeResponse {
  item_id: UUID;
  vision: VisionOutput;
  trace_id: UUID;
}

// ------------------------------------------------------------- recommendations

export interface CandidateView {
  slot_id: UUID;
  code: string;
  label: string;
  full_path: string;
  slot_path?: string;
  room_name: string;
  unit_name: string;
  section_name?: string;
  score: number;
  confidence: number;
  reason: string;
  matched_rules?: string[];
  evidence_item_ids?: UUID[];
  is_recommended?: boolean;
}

export interface RecommendResponse {
  recommendation_id: UUID | null;
  trace_id: UUID;
  candidates: CandidateView[];
  pre_filter_count: number;
  post_filter_count: number;
  verifier_passed: boolean;
  retry_count: number;
  error?: { code: string; message: string };
}

export interface AcceptRequest {
  slot_id: UUID;
}

export interface RejectRequest {
  reason?: string;
}

export interface PatchRequest {
  slot_id: UUID;
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
}

export interface SearchResponseBody {
  answer_text: string;
  state: SearchState;
  intent: SearchIntent;
  matches: CandidateMatch[];
  clarification_question: string | null;
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
