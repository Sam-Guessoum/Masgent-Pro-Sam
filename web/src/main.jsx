import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';
import { CrystalPreview, MaterialsExplorer } from './materials.jsx';

async function api(path, body) {
  const response = await fetch('/api' + path, body === undefined ? {} : {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Masgent-Request': '1' },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const details = await response.json().catch(() => ({}));
    const error = new Error(details.error || `Request failed (${response.status}).`);
    error.status = response.status;
    throw error;
  }
  return response.json();
}

function App() {
  const [authenticated, setAuthenticated] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api('/session').then(data => setAuthenticated(data.authenticated)).catch(error => setError(error.message));
  }, []);

  async function signIn(event) {
    event.preventDefault();
    const password = new FormData(event.currentTarget).get('password');
    setBusy(true);
    setError('');
    try {
      await api('/session', { password });
      setAuthenticated(true);
    } catch (error) {
      setError(error.message);
    } finally {
      setBusy(false);
    }
  }

  const signOut = async () => {
    try {
      await api('/logout', {});
      setAuthenticated(false);
    } catch (error) {
      if (error.status === 401) setAuthenticated(false);
      else setError(error.message);
    }
  };

  if (authenticated) {
    return <Workspace signOut={signOut} sessionEnded={() => setAuthenticated(false)} globalError={error} />;
  }

  return <main className="lock-screen">
    <div className="sign-in panel">
      <Brand />
      <h1>Sign in to Masgent</h1>
      {authenticated === null && !error && <p role="status">Checking your session…</p>}
      {authenticated === false && <form onSubmit={signIn}>
        <label>Workspace password<input name="password" type="password" autoComplete="current-password" required /></label>
        <button className="primary" disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button>
      </form>}
      {error && <p className="error" role="alert">{error}</p>}
    </div>
  </main>;
}

function Brand() {
  return <div className="brand"><span className="mark" aria-hidden="true">M</span><span>Masgent<small>Materials Simulation Agent</small></span></div>;
}

function Workspace({ signOut, sessionEnded, globalError }) {
  const [catalog, setCatalog] = useState(null);
  const [toolId, setToolId] = useState('');
  const [mode, setMode] = useState('explorer');
  const [preview, setPreview] = useState('');
  const [runId, setRunId] = useState('');
  const [runs, setRuns] = useState([]);
  const [run, setRun] = useState(null);
  const [error, setError] = useState('');
  const reportError = error => error.status === 401 ? sessionEnded() : setError(error.message);

  useEffect(() => {
    api('/catalog').then(data => {
      setCatalog(data);
      setToolId(data.tools[0]?.id || '');
    }).catch(reportError);
  }, []);

  useEffect(() => {
    let active = true;
    let timer;
    async function refresh() {
      try {
        const [recent, selected] = await Promise.all([api('/runs'), runId ? api('/runs/' + runId) : null]);
        if (!active) return;
        setRuns(recent);
        setRun(selected);
        timer = setTimeout(refresh, 2000);
      } catch (error) {
        if (active) reportError(error);
      }
    }
    refresh();
    return () => { active = false; clearTimeout(timer); };
  }, [runId]);

  async function submit(params) {
    setError('');
    try {
      const result = await api('/runs', { tool: toolId, params });
      setRun(null);
      setRunId(result.run_id);
    } catch (error) {
      reportError(error);
    }
  }

  const tool = catalog?.tools.find(item => item.id === toolId);
  const groups = [...new Set(catalog?.tools.map(item => item.group))];

  return <div className="shell">
    <aside>
      <Brand />
      <nav aria-label="Workspace">
        <button className="nav-button explorer-nav" aria-current={mode === 'explorer' ? 'page' : undefined}
          onClick={() => setMode('explorer')}>Materials explorer</button>
        {groups.map(group => <section key={group}>
        <h2>{group}</h2>
        {catalog.tools.filter(item => item.group === group).map(item =>
          <button key={item.id} className="nav-button" aria-current={mode === 'tools' && item.id === toolId ? 'page' : undefined}
            onClick={() => { setToolId(item.id); setMode('tools'); }}>{item.title}</button>
        )}
      </section>)}</nav>
      <button className="secondary" onClick={signOut}>Sign out</button>
    </aside>
    <main className="workspace">
      {(error || globalError) && <p className="error" role="alert">{error || globalError}</p>}
      {mode === 'explorer' && <MaterialsExplorer api={api} onError={reportError} />}
      {mode === 'tools' && tool && <>
        <header><p className="group">{tool.group}</p><h1>{tool.title}</h1><p>{tool.description}</p></header>
        <ToolForm key={tool.id} tool={tool} examples={catalog.examples} submit={submit} />
      </>}
      {mode === 'tools' && <section className="panel runs" aria-label="Results library">
        <h2>Results library</h2>
        {runs.length ? <ul>{runs.map(item => <li key={item.run_id}>
          <button className="run-row" aria-pressed={runId === item.run_id} onClick={() => {
            if (item.run_id !== runId) { setRun(null); setRunId(item.run_id); }
          }}><span>{item.tool_title}</span><span className="status">{item.status}</span></button>
        </li>)}</ul> : <p>Completed and active runs appear here.</p>}
        {run && <div className="result">
          <h3>{run.title} <span className="status">{run.status}</span></h3>
          {run.message && <p>{run.message}</p>}
          {['queued', 'running'].includes(run.status) && <button className="secondary"
            onClick={() => api('/runs/' + runId + '/cancel', {}).catch(reportError)}>Cancel run</button>}
          {run.files.length > 0 && <ul>{run.files.map(file => {
            const path = file.split('/').map(encodeURIComponent).join('/');
            const canPreview = /(?:^|\/)(?:POSCAR|CONTCAR)$|\.(?:vasp|poscar|cif)$/i.test(file);
            return <li key={file} className="result-file">
              <a href={`/api/runs/${runId}/files/${path}`}>{file}</a>
              {canPreview && <button className="secondary" onClick={() => setPreview(`/runs/${runId}/structure/${path}`)}>View in 3D</button>}
            </li>;
          })}</ul>}
          {preview.startsWith(`/runs/${runId}/structure/`) && <CrystalPreview key={preview} endpoint={preview} api={api} onError={reportError} />}
        </div>}
      </section>}
    </main>
  </div>;
}

function ToolForm({ tool, examples, submit }) {
  const [values, setValues] = useState(() => Object.fromEntries(tool.fields.map(field => [
    field.name, field.kind === 'structure' ? examples[0] || '' : field.default ?? '',
  ])));
  const [busy, setBusy] = useState(false);
  const set = (name, value) => setValues(previous => ({ ...previous, [name]: value }));
  const fields = tool.fields.filter(field => field.name !== 'custom_kppa' || values.accuracy_level === 'Custom');

  async function run(event) {
    event.preventDefault();
    const params = Object.fromEntries(fields.map(field => {
      let value = values[field.name];
      if (field.kind === 'miller' && !Array.isArray(value)) value = value.trim().split(/\s+/).map(Number);
      if (['int', 'float', 'number'].includes(field.type) && value !== '') value = Number(value);
      return [field.name, value];
    }));
    setBusy(true);
    try { await submit(params); } finally { setBusy(false); }
  }

  return <form className="panel tool-form" onSubmit={run}>
    <div className="fields">{fields.map(field => {
      const value = values[field.name];
      const options = field.kind === 'structure' ? examples : field.options;
      let control;
      if (options) {
        control = <select value={value} required onChange={event => set(field.name, event.target.value)}>
          <option value="" disabled>Choose an option</option>
          {options.map(option => <option key={option}>{option}</option>)}
        </select>;
      } else if (field.type === 'bool') {
        control = <input type="checkbox" checked={Boolean(value)} onChange={event => set(field.name, event.target.checked)} />;
      } else {
        const numeric = ['int', 'float', 'number'].includes(field.type);
        control = <input type={numeric ? 'number' : 'text'} required min={field.min ?? undefined}
          max={field.max ?? undefined} step={field.type === 'int' ? 1 : 'any'}
          value={Array.isArray(value) ? value.join(' ') : value}
          onChange={event => set(field.name, event.target.value)} />;
      }
      return <label key={field.name}>{field.label}{control}</label>;
    })}</div>
    <button className="primary" disabled={busy}>{busy ? 'Submitting…' : 'Run tool'}</button>
  </form>;
}

createRoot(document.getElementById('root')).render(<App />);
