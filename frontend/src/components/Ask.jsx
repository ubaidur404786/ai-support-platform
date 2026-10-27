import { useState } from "react";
import { askStream } from "../api.js";

export default function Ask({ token }) {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [sources, setSources] = useState([]);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function ask(event) {
    event.preventDefault();
    setAnswer("");
    setSources([]);
    setNote("");
    setError("");
    setBusy(true);
    try {
      await askStream(token, question, (line) => {
        if (line.type === "sources") setSources(line.sources);
        // Add each new piece of text to what is already on screen.
        if (line.type === "text") setAnswer((soFar) => soFar + line.text);
        if (line.type === "done" && !line.answered) {
          setAnswer(line.answer);
          setNote("Nothing in your documents answers this question.");
        }
        if (line.type === "error") setError(line.detail);
      });
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card">
      <h2>Ask a question</h2>
      <form onSubmit={ask} className="ask">
        <input
          placeholder="e.g. Can I get my money back?"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          required
        />
        <button disabled={busy}>{busy ? "Thinking…" : "Ask"}</button>
      </form>
      {busy && !answer && (
        <p className="muted">Reading your documents… on a CPU the first words take a few seconds.</p>
      )}
      {error && <p className="error">{error}</p>}
      {answer && <p className="answer">{answer}</p>}
      {note && <p className="muted">{note}</p>}
      {sources.length > 0 && (
        <div className="sources">
          <h3>Sources</h3>
          <ol>
            {sources.map((s) => (
              <li key={s.number}>
                <strong>{s.document_title}</strong> <span className="muted">(score {s.score})</span>
                <p>{s.text.slice(0, 220)}{s.text.length > 220 ? "…" : ""}</p>
              </li>
            ))}
          </ol>
        </div>
      )}
    </section>
  );
}
