const $ = id => document.getElementById(id);
const status = $('status');
const clustersDiv = $('clusters');

function parseKeywords(input){
  if(!input) return [];
  // split by newlines or commas
  const lines = input.split(/\n|,/).map(s => s.trim()).filter(Boolean);
  return lines;
}

function setStatus(s){
  status.textContent = s;
}

function renderResults(json){
  clustersDiv.innerHTML = '';
  const meta = json.meta || {};
  const results = json.results || [];
  const topTerms = json.top_terms || {};

  const grouped = {};
  (results).forEach(r => {
    const c = r.cluster || 0;
    if(!grouped[c]) grouped[c] = [];
    grouped[c].push(r);
  });

  Object.keys(grouped).sort((a,b)=>a-b).forEach(clusterId=>{
    const items = grouped[clusterId];
    const clusterBox = document.createElement('div');
    clusterBox.className = 'cluster';
    const header = document.createElement('h3');
    const terms = (topTerms[clusterId] || []).slice(0,5).join(', ');
    header.textContent = `Cluster ${clusterId} — ${terms}`;
    clusterBox.appendChild(header);

    const table = document.createElement('table');
    table.className = 'table';
    const thead = document.createElement('thead');
    thead.innerHTML = `<tr>
      <th>Keyword</th><th>Title</th><th>Snippet</th><th>URL</th><th class="small">Position</th>
    </tr>`;
    table.appendChild(thead);
    const tbody = document.createElement('tbody');
    items.forEach(it => {
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>${escapeHtml(it.keyword)}</td>
        <td>${escapeHtml(it.title)}</td>
        <td>${escapeHtml(it.snippet)}</td>
        <td><a href="${escapeAttr(it.url)}" target="_blank">${escapeHtml(it.url)}</a></td>
        <td class="small">${it.position || ''}</td>`;
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    clusterBox.appendChild(table);
    clustersDiv.appendChild(clusterBox);
  });
}

function escapeHtml(s){ if(!s) return ''; return s.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;'); }
function escapeAttr(s){ if(!s) return ''; return s.replaceAll('"','%22'); }

$('run').addEventListener('click', async () => {
  const input = $('keywords').value;
  const kws = parseKeywords(input);
  if(kws.length === 0){
    setStatus('Please enter at least one keyword.');
    return;
  }
  setStatus('Running scrape and clustering...');
  const per_keyword = parseInt($('per_keyword').value || '10', 10);
  const k = parseInt($('k').value || '0', 10) || undefined;
  try{
    const res = await fetch('/cluster', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ keywords: kws, per_keyword: per_keyword, k: k })
    });
    if(!res.ok){
      const err = await res.json().catch(()=>({error: 'unknown'}));
      setStatus('Error: ' + (err.error || res.statusText));
      return;
    }
    const json = await res.json();
    setStatus('Done. Displaying clusters.');
    renderResults(json);
  }catch(e){
    setStatus('Network or server error: ' + e.message);
  }
});

$('download-csv').addEventListener('click', () => {
  window.location.href = '/download/csv';
});
$('download-json').addEventListener('click', () => {
  window.location.href = '/download/json';
});
