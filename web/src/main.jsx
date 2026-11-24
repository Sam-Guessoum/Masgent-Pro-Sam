import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';
import { CalculationResults } from './results.jsx';
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
  const [material, setMaterial] = useState('');
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
      setMode('results');
    } catch (error) {
      reportError(error);
    }
  }

  const simulationTool = catalog?.tools.find(item => item.id === 'run_simulation_using_mlps');
  const tool = catalog?.tools.find(item => item.id === toolId);
  const groups = [...new Set(catalog?.tools.map(item => item.group))];

  return <div className="shell">
    <aside>
      <Brand />
      <nav aria-label="Workspace">
        <button className="nav-button" aria-current={mode === 'explorer' ? 'page' : undefined}
          onClick={() => setMode('explorer')}>Materials explorer</button>
        {groups.map(group => <button key={group} className="nav-button"
          aria-current={mode === 'tools' && tool?.group === group ? 'page' : undefined}
          onClick={() => { if (tool?.group !== group) setToolId(catalog.tools.find(item => item.group === group).id); setMode('tools'); }}>
          {group}
        </button>)}
        <button className="nav-button" aria-current={mode === 'results' ? 'page' : undefined}
          onClick={() => setMode('results')}>Results library</button>
      </nav>
      <button className="secondary" onClick={signOut}>Sign out</button>
    </aside>
    <main className="workspace">
      {(error || globalError) && <p className="error" role="alert">{error || globalError}</p>}
      {mode === 'explorer' && <MaterialsExplorer api={api} onError={reportError} useMaterial={id => {
        setMaterial(id);
        if (simulationTool && !simulationTool.unavailable) setToolId(simulationTool.id);
        setMode('tools');
      }} />}
      {mode === 'tools' && tool && <>
        <header><p className="group">{tool.group}</p><h1>{tool.title}</h1><p>{tool.description}</p></header>
        <div className="workflow-tabs" role="group" aria-label={tool.group + ' workflows'}>
          {catalog.tools.filter(item => item.group === tool.group).map(item => <button key={item.id}
            aria-pressed={item.id === toolId} onClick={() => setToolId(item.id)}>{item.title}</button>)}
        </div>
        {tool.unavailable ? <p className="panel">{tool.unavailable}</p> :
          <ToolForm key={tool.id} tool={tool} examples={catalog.examples} material={material} submit={submit} />}
      </>}
      {mode === 'results' && <section className="panel runs" aria-label="Results library">
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
          {!['queued', 'running'].includes(run.status) && <CalculationResults key={runId} run={run} api={api} onError={reportError} />}
          {run.files.length > 0 && <ul aria-label="Output files">{run.files.map(file => {
            const path = file.split('/').map(encodeURIComponent).join('/');
            const canPreview = /(?:^|\/)(?:POSCAR|CONTCAR)(?:_[^/]+)?$|\.(?:vasp|poscar|cif)$/i.test(file);
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

function ToolForm({ tool, examples, material, submit }) {
  const [values, setValues] = useState(() => Object.fromEntries(tool.fields.map(field => [
    field.name, field.kind === 'structure' ? (material ? material : examples[0] || '') : field.default ?? '',
  ])));
  const [busy, setBusy] = useState(false);
  const [uploads, setUploads] = useState({});
  const [inputError, setInputError] = useState('');
  const set = (name, value) => setValues(previous => ({ ...previous, [name]: value }));
  const fields = tool.fields.filter(field => {
    if (field.name === 'custom_kppa') return values.accuracy_level === 'Custom';
    if (field.name === 'scale_factors' && 'task_type' in values) return values.task_type === 'eos';
    if (['temperature', 'md_steps', 'md_timestep'].includes(field.name) && 'task_type' in values) return values.task_type === 'md';
    if (['max_steps', 'fmax'].includes(field.name)) return values.task_type !== 'md';
    if (field.name === 'kpoint_levels') return values.test_type !== 'encut';
    if (field.name === 'encut_levels') return values.test_type !== 'kpoints';
    return true;
  });

  async function run(event) {
    event.preventDefault();
    setInputError('');
    setBusy(true);
    try {
      const params = Object.fromEntries(await Promise.all(fields.map(async field => {
      const file = uploads[field.name];
      if (file) return [field.name, { filename: file.name, upload: await encodeFile(file) }];
      let value = values[field.name];
      if (field.kind === 'miller' && !Array.isArray(value)) value = value.trim().split(/\s+/).map(Number);
      if (['list_float', 'list_int'].includes(field.kind) && !Array.isArray(value)) value = value.trim().split(/[\s,]+/).map(Number);
      if (['int', 'float', 'number'].includes(field.type) && value !== '') value = Number(value);
      return [field.name, value];
      })));
      await submit(params);
    } catch (error) { setInputError(error.message); }
    finally { setBusy(false); }
  }

  return <form className="panel tool-form" onSubmit={run}>
    <div className="fields">{fields.map(field => {
      const value = values[field.name];
      const options = field.kind === 'structure' ? (material ? [material, ...examples] : examples) : field.options;
      let control;
      if (field.kind === 'folder') {
        control = null;
      } else if (options) {
        control = <select value={value} required={!uploads[field.name]} disabled={Boolean(uploads[field.name])} onChange={event => set(field.name, event.target.value)}>
          <option value="" disabled>Choose an option</option>
          {options.map(option => <option key={option} value={option}>{option === material ? option.slice(option.indexOf(':') + 1) + ' · Imported crystal' : field.labels?.[option] ?? option}</option>)}
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
      const accept = { structure: '.cif,.vasp,.poscar,.contcar,*', folder: '.zip', csv: '.csv' }[field.kind];
      return <div key={field.name} className="tool-field">
        {control && <label>{field.label}{control}</label>}
        {accept && <label>{field.kind === 'folder' ? field.label + ' (ZIP)' : 'Or upload ' + field.label.toLowerCase()}
          <input type="file" accept={accept} required={field.kind === 'folder'} onChange={event => {
            const file = event.target.files[0];
            setUploads(current => ({ ...current, [field.name]: file }));
          }} />
        </label>}
      </div>;
    })}</div>
    {inputError && <p className="error" role="alert">{inputError}</p>}
    <button className="primary" disabled={busy}>{busy ? 'Submitting…' : 'Run tool'}</button>
  </form>;
}

function encodeFile(file) {
  if (file.size > 10 * 1024 * 1024) throw new Error('Each upload must be 10 MB or smaller.');
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result.split(',')[1]);
    reader.onerror = () => reject(new Error('Could not read ' + file.name + '.'));
    reader.readAsDataURL(file);
  });
}

createRoot(document.getElementById('root')).render(<App />);
