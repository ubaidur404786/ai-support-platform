import { useEffect, useState } from "react";
import { listDocuments, uploadDocument } from "../api.js";

export default function Documents({ token }) {
  const [documents, setDocuments] = useState([]);
  const [error, setError] = useState("");
  const [uploading, setUploading] = useState(false);

  async function refresh() {
    try {
      const data = await listDocuments(token);
      setDocuments(data.items);
    } catch (err) {
      setError(err.message);
    }
  }

  // Load the list once, then again every 2 seconds while a document is still
  // waiting or being processed, so its status changes on screen by itself.
  useEffect(() => {
    refresh();
  }, [token]);

  const stillWorking = documents.some((d) => d.status === "queued" || d.status === "processing");
  useEffect(() => {
    if (!stillWorking) return;
    const timer = setInterval(refresh, 2000);
    return () => clearInterval(timer); // stop polling when nothing is left to wait for
  }, [stillWorking]);

  async function upload(event) {
    const file = event.target.files[0];
    if (!file) return;
    setError("");
    setUploading(true);
    try {
      await uploadDocument(token, file);
      await refresh();
    } catch (err) {
      setError(err.message);
    } finally {
      setUploading(false);
      event.target.value = ""; // allow choosing the same file again
    }
  }

  return (
    <section className="card">
      <h2>Knowledge base</h2>
      <label className="upload">
        {uploading ? "Uploading…" : "Upload a document (.txt, .md, .pdf)"}
        <input type="file" accept=".txt,.md,.pdf" onChange={upload} disabled={uploading} hidden />
      </label>
      {error && <p className="error">{error}</p>}
      {documents.length === 0 ? (
        <p className="muted">No documents yet. Upload a help article to start.</p>
      ) : (
        <ul className="documents">
          {documents.map((d) => (
            <li key={d.id}>
              <span>{d.title}</span>
              <span className={`badge ${d.status}`}>{d.status}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
