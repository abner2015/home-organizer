// Cookie names shared by the browser session store (`session.ts`), the server
// reader (`session.server.ts`) and the redirect middleware. Kept in their own
// dependency-free module so the Edge middleware doesn't have to import the
// browser-oriented `session.ts`.

export const TOKEN_COOKIE = "ho_token";
export const REFRESH_COOKIE = "ho_refresh";
export const HOME_COOKIE = "ho_home";
export const NAME_COOKIE = "ho_name";
