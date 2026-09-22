"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, APIError, type ApiSession } from "@/lib/api";
import { Loading, Spinner } from "@/components/States";
import type {
  Room,
  RoomType,
  SectionType,
  SpaceTree,
  StorageSection,
  StorageUnit,
  UnitType,
} from "@/lib/types";
import { ROOM_TYPE_OPTIONS, SECTION_TYPE_OPTIONS, UNIT_TYPE_OPTIONS } from "./labels";

/**
 * The no-AI path: room → cabinet → layer → slot, one level at a time.
 *
 * Each 「新建」 button posts to the same write API the AI path uses, so a home
 * built here is indistinguishable from one built there. It exists so a working
 * model is not a prerequisite for having a home — and it is the fallback when
 * the description is not something you can put into words.
 */
export function ManualBuilder({ session }: { session: ApiSession }) {
  const router = useRouter();
  const [tree, setTree] = useState<SpaceTree | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [roomId, setRoomId] = useState<string>("");
  const [unitId, setUnitId] = useState<string>("");
  const [sectionId, setSectionId] = useState<string>("");

  const [roomName, setRoomName] = useState("");
  const [roomType, setRoomType] = useState<RoomType>("kitchen");
  const [unitName, setUnitName] = useState("");
  const [unitType, setUnitType] = useState<UnitType>("cabinet");
  const [sectionName, setSectionName] = useState("");
  const [sectionType, setSectionType] = useState<SectionType>("layer");
  const [slotCode, setSlotCode] = useState("");

  useEffect(() => {
    void (async () => {
      try {
        setTree(await api.getSpaceTree(session));
      } catch {
        setError("无法加载现有的收纳结构。");
      } finally {
        setLoading(false);
      }
    })();
  }, [session]);

  const rooms: Array<Room & { units: StorageUnit[] }> = tree?.rooms ?? [];
  const room = rooms.find((r) => r.id === roomId) ?? null;
  const unit: StorageUnit | null = room?.units.find((u) => u.id === unitId) ?? null;
  const section: StorageSection | null =
    unit?.sections.find((s) => s.id === sectionId) ?? null;

  /** Run one write, surfacing the backend's Chinese message on failure. */
  async function perform<T>(fn: () => Promise<T>): Promise<T | null> {
    setBusy(true);
    setError(null);
    try {
      return await fn();
    } catch (err) {
      setError(err instanceof APIError ? err.message : "操作失败，请重试。");
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function addRoom() {
    const name = roomName.trim();
    if (!name) return;
    const created = await perform(() =>
      api.createRoom({ name, room_type: roomType }, session),
    );
    if (!created) return;
    setTree((prev) =>
      prev ? { ...prev, rooms: [...prev.rooms, { ...created, units: [] }] } : prev,
    );
    setRoomId(created.id);
    setUnitId("");
    setSectionId("");
    setRoomName("");
  }

  async function addUnit() {
    const name = unitName.trim();
    if (!name || !room) return;
    const created = await perform(() =>
      api.createUnit(room.id, { name, unit_type: unitType }, session),
    );
    if (!created) return;
    setTree((prev) =>
      prev
        ? {
            ...prev,
            rooms: prev.rooms.map((r) =>
              r.id === room.id
                ? { ...r, units: [...r.units, { ...created, sections: [] }] }
                : r,
            ),
          }
        : prev,
    );
    setUnitId(created.id);
    setSectionId("");
    setUnitName("");
  }

  async function addSection() {
    const name = sectionName.trim();
    if (!name || !unit || !room) return;
    const created = await perform(() =>
      api.createSection(unit.id, { name, section_type: sectionType }, session),
    );
    if (!created) return;
    setTree((prev) =>
      prev
        ? {
            ...prev,
            rooms: prev.rooms.map((r) =>
              r.id === room.id
                ? {
                    ...r,
                    units: r.units.map((u) =>
                      u.id === unit.id
                        ? { ...u, sections: [...u.sections, { ...created, slots: [] }] }
                        : u,
                    ),
                  }
                : r,
            ),
          }
        : prev,
    );
    setSectionId(created.id);
    setSectionName("");
  }

  async function addSlot() {
    const code = slotCode.trim();
    if (!code || !section || !unit || !room) return;
    const created = await perform(() => api.createSlot(section.id, { code }, session));
    if (!created) return;
    setTree((prev) =>
      prev
        ? {
            ...prev,
            rooms: prev.rooms.map((r) =>
              r.id === room.id
                ? {
                    ...r,
                    units: r.units.map((u) =>
                      u.id === unit.id
                        ? {
                            ...u,
                            sections: u.sections.map((s) =>
                              s.id === section.id
                                ? { ...s, slots: [...s.slots, created] }
                                : s,
                            ),
                          }
                        : u,
                    ),
                  }
                : r,
            ),
          }
        : prev,
    );
    setSlotCode("");
  }

  if (loading) {
    return (
      <div className="card p-8">
        <Loading label="正在加载现有的收纳结构…" />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="card space-y-4 p-5">
        {/* room */}
        <div>
          <label className="label" htmlFor="manual-room">
            1. 房间
          </label>
          <div className="flex flex-wrap gap-2">
            <select
              id="manual-room"
              className="input max-w-56"
              value={roomId}
              onChange={(e) => {
                setRoomId(e.target.value);
                setUnitId("");
                setSectionId("");
              }}
            >
              <option value="">选择已有房间…</option>
              {rooms.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.name}
                </option>
              ))}
            </select>
            <input
              className="input max-w-48"
              placeholder="新房间名称"
              value={roomName}
              onChange={(e) => setRoomName(e.target.value)}
              aria-label="新房间名称"
            />
            <select
              className="input max-w-32"
              value={roomType}
              onChange={(e) => setRoomType(e.target.value as RoomType)}
              aria-label="新房间类型"
            >
              {ROOM_TYPE_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="btn-secondary"
              disabled={busy || roomName.trim().length === 0}
              onClick={() => void addRoom()}
            >
              新建房间
            </button>
          </div>
        </div>

        {/* unit */}
        {room ? (
          <div>
            <label className="label" htmlFor="manual-unit">
              2. 柜子 / 架子（建在「{room.name}」里）
            </label>
            <div className="flex flex-wrap gap-2">
              <select
                id="manual-unit"
                className="input max-w-56"
                value={unitId}
                onChange={(e) => {
                  setUnitId(e.target.value);
                  setSectionId("");
                }}
              >
                <option value="">选择已有柜子…</option>
                {room.units.map((u) => (
                  <option key={u.id} value={u.id}>
                    {u.name}
                  </option>
                ))}
              </select>
              <input
                className="input max-w-48"
                placeholder="新柜子名称"
                value={unitName}
                onChange={(e) => setUnitName(e.target.value)}
                aria-label="新柜子名称"
              />
              <select
                className="input max-w-32"
                value={unitType}
                onChange={(e) => setUnitType(e.target.value as UnitType)}
                aria-label="新柜子类型"
              >
                {UNIT_TYPE_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
              <button
                type="button"
                className="btn-secondary"
                disabled={busy || unitName.trim().length === 0}
                onClick={() => void addUnit()}
              >
                新建柜子
              </button>
            </div>
          </div>
        ) : null}

        {/* section */}
        {unit ? (
          <div>
            <label className="label" htmlFor="manual-section">
              3. 层 / 抽屉（建在「{unit.name}」里）
            </label>
            <div className="flex flex-wrap gap-2">
              <select
                id="manual-section"
                className="input max-w-56"
                value={sectionId}
                onChange={(e) => setSectionId(e.target.value)}
              >
                <option value="">选择已有的层…</option>
                {unit.sections.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
              <input
                className="input max-w-48"
                placeholder="新的层名称"
                value={sectionName}
                onChange={(e) => setSectionName(e.target.value)}
                aria-label="新的层名称"
              />
              <select
                className="input max-w-32"
                value={sectionType}
                onChange={(e) => setSectionType(e.target.value as SectionType)}
                aria-label="新的层类型"
              >
                {SECTION_TYPE_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
              <button
                type="button"
                className="btn-secondary"
                disabled={busy || sectionName.trim().length === 0}
                onClick={() => void addSection()}
              >
                新建层
              </button>
            </div>
          </div>
        ) : null}

        {/* slot */}
        {section ? (
          <div>
            <label className="label" htmlFor="manual-slot">
              4. 格（建在「{section.name}」里）
            </label>
            <div className="flex flex-wrap gap-2">
              <input
                id="manual-slot"
                className="input max-w-48"
                placeholder="格子编号，例如 K1"
                value={slotCode}
                onChange={(e) => setSlotCode(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") void addSlot();
                }}
              />
              <button
                type="button"
                className="btn-primary"
                disabled={busy || slotCode.trim().length === 0}
                onClick={() => void addSlot()}
              >
                新建格
              </button>
            </div>
            <p className="mt-1.5 text-xs text-ink-500">
              同一层内的编号不能重复，编号重复会提示错误。
            </p>
          </div>
        ) : null}

        {error ? <p className="text-sm text-red-600">{error}</p> : null}
      </div>

      <Summary room={room} unit={unit} section={section} />

      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          className="btn-primary"
          disabled={busy}
          onClick={() => {
            router.refresh();
            router.push("/home/storage");
          }}
        >
          {busy ? <Spinner className="h-4 w-4" /> : null}
          完成，去看收纳空间
        </button>
      </div>
    </div>
  );
}

function Summary({
  room,
  unit,
  section,
}: {
  room: (Room & { units: StorageUnit[] }) | null;
  unit: StorageUnit | null;
  section: StorageSection | null;
}) {
  if (!room) return null;
  return (
    <div className="card p-4 text-sm text-ink-600">
      <p className="font-medium text-ink-800">已建成</p>
      <ul className="mt-2 space-y-1">
        <li>
          {room.name} · {room.units.length} 个柜子
        </li>
        {unit ? (
          <li className="pl-4">
            ↳ {unit.name} · {unit.sections.length} 层
          </li>
        ) : null}
        {section ? (
          <li className="pl-8">
            ↳ {section.name} · {section.slots.length} 个格
          </li>
        ) : null}
      </ul>
    </div>
  );
}
