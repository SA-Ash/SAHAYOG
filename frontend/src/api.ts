export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}
let csrfRequest: Promise<string> | undefined;
function csrfToken() {
  const token = document.cookie
    .split("; ")
    .find((c) => c.startsWith("sahyog_csrf="))
    ?.split("=")
    .slice(1)
    .join("=");
  if (token) return Promise.resolve(decodeURIComponent(token));
  csrfRequest ??= fetch("/api/v1/auth/csrf", { credentials: "include" })
    .then((r) => r.json())
    .then((d) => d.csrf_token as string)
    .finally(() => {
      csrfRequest = undefined;
    });
  return csrfRequest;
}
export async function api<T>(
  path: string,
  body?: unknown,
  method = body === undefined ? "GET" : "POST",
): Promise<T> {
  const headers: Record<string, string> = {};
  if (method !== "GET") {
    headers["X-CSRF-Token"] = await csrfToken();
    if (!(body instanceof FormData))
      headers["Content-Type"] = "application/json";
  }
  const response = await fetch("/api/v1" + path, {
    method,
    credentials: "include",
    headers,
    body:
      body === undefined
        ? undefined
        : body instanceof FormData
          ? body
          : JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok)
    throw new ApiError(
      response.status,
      data.code,
      data.details
        ?.map?.(
          (d: { field: string; message: string }) => `${d.field}: ${d.message}`,
        )
        .join("; ") ||
        data.message ||
        "Request failed",
    );
  return data;
}
