// Minimal JWT payload reader.
//
// Only the `exp` claim is read, and nothing is verified here — the backend
// re-verifies the signature on every request. This exists so the client can tell
// "this token has run out, mint another one" apart from "this token was
// rejected", which are different situations: the first should be invisible to
// the user, the second means they have to sign in again.
//
// Deliberately dependency-free, because `middleware.ts` runs on the Edge
// runtime and pulling in a JWT library to read one number would not pay for
// itself.

/** `exp` (seconds since the epoch) from a JWT, or `null` if it cannot be read. */
export function decodeJwtExp(token: string): number | null {
  const payload = token.split(".")[1];
  if (!payload) return null;
  try {
    // base64url -> base64, then pad back to a multiple of 4 for atob.
    const base64 = payload.replace(/-/g, "+").replace(/_/g, "/");
    const padded = base64.padEnd(
      base64.length + ((4 - (base64.length % 4)) % 4),
      "=",
    );
    const claims: unknown = JSON.parse(atob(padded));
    if (claims && typeof claims === "object" && "exp" in claims) {
      const exp = (claims as { exp: unknown }).exp;
      if (typeof exp === "number") return exp;
    }
    return null;
  } catch {
    return null;
  }
}

/**
 * Whether `token` is expired already, or will be within `skewSeconds`.
 *
 * A token whose `exp` cannot be read counts as expired: renewing costs one
 * request, and a malformed token is not going to start working on its own.
 */
export function isExpired(token: string, skewSeconds = 30): boolean {
  const exp = decodeJwtExp(token);
  if (exp === null) return true;
  return exp - skewSeconds <= Date.now() / 1000;
}

/** Seconds `token` stays valid, or `null` when that cannot be determined. */
export function secondsUntilExpiry(token: string): number | null {
  const exp = decodeJwtExp(token);
  if (exp === null) return null;
  return Math.max(0, Math.floor(exp - Date.now() / 1000));
}
