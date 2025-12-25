import React, { useState, useMemo } from "react";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

function ClusterCard({ id, label, terms, silhouette, exemplars }) {
  return (
    <div className="card">
      <div className="card-header">
        <div className="card-title">Cluster {id} — <span className="muted">{label}</span></div>
        <div className="card-meta">Top: {terms.slice(0,4).join(", ")}</div>
      </div>
      <div className="card-body">
        <div className="exemplar-list">
          {exemplars && exemplars.map((e, i) => (
            <div className="exemplar" key={i}>
              <a href={e.url} target="_blank" rel="noreferrer" className="ex-url">{e.domain}</a>
              <div className="ex-text">{e.text}</div>
              <div className="ex-meta">pos {e.position} • dist {e.distance.toFixed(3)}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

export default function App() {
  const [keywordsText, setKeywordsText] = useState("dog food\nbest dog food");
  const [perKeyword, setPerKeyword] = useState(6);
  const [loading, setLoading] = useState(false);
  const [payload, setPayload] = useState(null);
  const [k, setK] = useState("");

  const results = payload ? payload.results || [] : [];
  const clusters = useMemo(() => {
    if (!payload) return {};
    const top_terms = payload.top_terms || {};
    const exemplars = payload.exemplars || {};
    return { top_terms, exemplars, meta: payload.meta };
  }, [payload]);

  async function runCluster() {
    setLoading(true);
    setPayload(null);
    const keywords = keywordsText.split("\n").map(s => s.trim()).filter(Boolean);
    try {
      const resp = await fetch(`${API_BASE}/api/cluster`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ keywords, per_keyword: perKeyword, k: k ? Number(k) : null })
      });
      if (!resp.ok) {
        const err = await resp.json();
        throw new Error(err.detail || JSON.stringify(err));
      }
      const data = await resp.json();
      setPayload(data);
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (err) {
      alert("Error: " + err.message);
    } finally {
      setLoading(false);
    }
  }

  function exportCSV() {
    if (!results || results.length === 0) return;
    const hdr = Object.keys(results[0]);
    const rows = results.map(r => hdr.map(h => JSON.stringify(r[h] ?? "").replaceAll('"', '""')));
    const csv = [hdr.join(","), ...rows.map(r => r.join(","))].join("\n");
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "clustered.csv";
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="container">
      <header className="topbar">
        <div className="brand">Keyword Intelligence</div>
        <div className="sub">TF-IDF • SVD • k-means • exemplars</div>
      </header>

      <main>
        <section className="controls">
          <div className="left">
            <textarea value={keywordsText} onChange={e => setKeywordsText(e.target.value)} />
            <div className="controls-row">
              <label>Per keyword</label>
              <input type="number" value={perKeyword} onChange={e => setPerKeyword(Number(e.target.value))} />
              <label>k (opt)</label>
              <input type="number" value={k} onChange={e => setK(e.target.value)} />
              <button className="btn primary" onClick={runCluster} disabled={loading}>{loading ? "Running…" : "Run"}</button>
              <button className="btn" onClick={exportCSV} disabled={results.length===0}>Export CSV</button>
            </div>
          </div>

          <aside className="right">
            <div className="stat">
              <div className="stat-value">{results.length}</div>
              <div className="stat-label">rows</div>
            </div>
            <div className="stat">
              <div className="stat-value">{payload?.meta?.k ?? "-"}</div>
              <div className="stat-label">clusters</div>
            </div>
            <div className="stat">
              <div className="stat-value">{payload?.meta?.silhouette ? payload.meta.silhouette.toFixed(3) : "-"}</div>
              <div className="stat-label">silhouette</div>
            </div>
          </aside>
        </section>

        <section className="results">
          <div className="grid-left">
            <div className="clusters-grid">
              {payload && Object.keys(clusters.top_terms || {}).length > 0 ? (
                Object.keys(clusters.top_terms).map(cid => {
                  const terms = clusters.top_terms[cid] || [];
                  const exs = clusters.exemplars[cid] || [];
                  const label = payload.meta.cluster_labels?.[cid] || terms.slice(0,3).join(", ");
                  return <ClusterCard key={cid} id={cid} label={label} terms={terms} exemplars={exs} />
                })
              ) : (
                <div className="placeholder">Clusters will appear here</div>
              )}
            </div>
          </div>

          <div className="grid-right">
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>keyword</th>
                    <th>title</th>
                    <th>excerpt</th>
                    <th>domain</th>
                    <th>cluster</th>
                  </tr>
                </thead>
                <tbody>
                  {results.map((r,i) => (
                    <tr key={i}>
                      <td>{r.keyword}</td>
                      <td className="nowrap"><a href={r.url} target="_blank" rel="noreferrer">{r.title}</a></td>
                      <td>{r.excerpt}</td>
                      <td>{r.domain}</td>
                      <td>{r.cluster}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </section>
      </main>

      <footer className="footer">
        <div>Respect site TOS. Debug files saved under backend/debug on block.</div>
      </footer>
    </div>
  );
}


