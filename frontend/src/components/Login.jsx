import { useState } from "react";
import { login, register } from "../api.js";

export default function Login({ onLoggedIn }) {
  const [mode, setMode] = useState("login"); // "login" or "register"
  const [organization, setOrganization] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event) {
    event.preventDefault(); // stop the browser from reloading the page
    setError("");
    setBusy(true);
    try {
      const token =
        mode === "login"
          ? await login(email, password)
          : await register(organization, email, password);
      onLoggedIn(token, email);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card" onSubmit={submit}>
      <h2>{mode === "login" ? "Log in" : "Create an account"}</h2>
      {mode === "register" && (
        <label>
          Organisation
          <input value={organization} onChange={(e) => setOrganization(e.target.value)} required />
        </label>
      )}
      <label>
        Email
        <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
      </label>
      <label>
        Password
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          minLength={mode === "register" ? 12 : undefined}
          required
        />
      </label>
      {error && <p className="error">{error}</p>}
      <button disabled={busy}>{busy ? "Please wait…" : mode === "login" ? "Log in" : "Create account"}</button>
      <button type="button" className="link" onClick={() => setMode(mode === "login" ? "register" : "login")}>
        {mode === "login" ? "No account yet? Create one" : "Already have an account? Log in"}
      </button>
    </form>
  );
}
