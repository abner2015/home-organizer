// Session bootstrap. In the MVP we use a stub auth (X-User-Id / X-Home-Id
// headers) that matches the backend's test environment. A future phase
// will replace this with real JWT auth — the API client already accepts
// userId / homeId as parameters so the swap is local.

import type { UUID } from "./types";

const DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000001";
const DEFAULT_HOME_ID = "00000000-0000-0000-0000-000000000002";

export interface Session {
  userId: UUID;
  homeId: UUID;
  displayName: string;
}

export function getSession(): Session {
  const userId =
    process.env.NEXT_PUBLIC_DEFAULT_USER_ID ||
    (typeof window !== "undefined" ? window.localStorage?.getItem("userId") : null) ||
    DEFAULT_USER_ID;
  const homeId =
    process.env.NEXT_PUBLIC_DEFAULT_HOME_ID ||
    (typeof window !== "undefined" ? window.localStorage?.getItem("homeId") : null) ||
    DEFAULT_HOME_ID;
  return {
    userId,
    homeId,
    displayName: "我",
  };
}
