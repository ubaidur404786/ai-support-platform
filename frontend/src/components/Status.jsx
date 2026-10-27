import { useEffect, useState } from "react";
import { health } from "../api.js";

// A small line at the bottom of the page: is every part of the system up?
// It reads the API's /health, which also checks the two model services (v14).
export default function Status() {
  const [status, setStatus] = useState(null);

  useEffect(() => {
    health()
      .then(setStatus)
      .catch(() => setStatus({ status: "down" }));
  }, []);

  if (!status) return null;
  const parts = [
    ["API", status.status],
    ["database", status.database_reachable === false ? "down" : status.database_reachable ? "ok" : null],
    ["answer model", status.model_service],
    ["search model", status.embedding_service],
  ].filter(([, value]) => value);

  return (
    <footer className="status">
      {parts.map(([name, value]) => (
        <span key={name} className={value === "ok" ? "ok" : "bad"}>
          {name}: {value}
        </span>
      ))}
    </footer>
  );
}
