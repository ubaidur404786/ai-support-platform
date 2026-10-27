// Every call to the backend goes through this file.
//
// The page only ever talks to "/api/...". In development Vite forwards that
// to the API; in Docker and Kubernetes nginx does. The page never needs to
// know where the API really runs.

const BASE = "/api";

// Turn an error response into a readable message for the screen.
async function errorMessage(response) {
  try {
    const body = await response.json();
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail)) return body.detail.map((d) => d.msg).join(", ");
  } catch {
    // not JSON - fall through
  }
  return `Request failed (${response.status})`;
}

async function request(path, { token, ...options } = {}) {
  const headers = { ...(options.headers || {}) };
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(BASE + path, { ...options, headers });
  if (!response.ok) throw new Error(await errorMessage(response));
  return response.json();
}

function postJson(path, body, token) {
  return request(path, {
    method: "POST",
    token,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function login(email, password) {
  const data = await postJson("/auth/login", { email, password });
  return data.access_token;
}

export async function register(organizationName, email, password) {
  await postJson("/auth/register", { organization_name: organizationName, email, password });
  return login(email, password);
}

export function listDocuments(token) {
  return request("/documents?limit=50", { token });
}

export function uploadDocument(token, file) {
  // FormData builds a multipart/form-data body, the format file uploads use.
  const form = new FormData();
  form.append("file", file);
  return request("/documents", { method: "POST", token, body: form });
}

export function health() {
  return request("/health");
}

// Ask a question and receive the answer while it is being written.
//
// POST /answers/stream sends one JSON object per line (NDJSON):
//   {"type": "sources", ...}  then many {"type": "text", ...}  then {"type": "done", ...}
// onEvent is called once per line, as soon as the line arrives.
export async function askStream(token, question, onEvent) {
  const response = await fetch(BASE + "/answers/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify({ question }),
  });
  if (!response.ok) throw new Error(await errorMessage(response));

  // Read the body bit by bit instead of waiting for all of it.
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffered = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffered += decoder.decode(value, { stream: true });
    // A chunk can end in the middle of a line: keep the unfinished part.
    const lines = buffered.split("\n");
    buffered = lines.pop();
    for (const line of lines) {
      if (line.trim()) onEvent(JSON.parse(line));
    }
  }
  if (buffered.trim()) onEvent(JSON.parse(buffered));
}
