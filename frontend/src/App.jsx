import { useState } from "react";
import Login from "./components/Login.jsx";
import Documents from "./components/Documents.jsx";
import Ask from "./components/Ask.jsx";
import Status from "./components/Status.jsx";

export default function App() {
  // The access token is kept in memory only: refreshing the page logs you out.
  // Simple and safe enough for a demo; a real product would add refresh tokens.
  const [token, setToken] = useState(null);
  const [email, setEmail] = useState("");

  if (!token) {
    return (
      <main className="page narrow">
        <h1>Support Assistant</h1>
        <p className="muted">Upload your help articles, then ask questions about them.</p>
        <Login
          onLoggedIn={(newToken, userEmail) => {
            setToken(newToken);
            setEmail(userEmail);
          }}
        />
        <Status />
      </main>
    );
  }

  return (
    <main className="page">
      <header className="top">
        <h1>Support Assistant</h1>
        <div>
          <span className="muted">{email}</span>{" "}
          <button className="link" onClick={() => setToken(null)}>
            Log out
          </button>
        </div>
      </header>
      <div className="columns">
        <Ask token={token} />
        <Documents token={token} />
      </div>
      <Status />
    </main>
  );
}
