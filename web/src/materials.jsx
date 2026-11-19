import React, { useEffect, useRef, useState } from 'react';

const elementData = 'H:Hydrogen|He:Helium|Li:Lithium|Be:Beryllium|B:Boron|C:Carbon|N:Nitrogen|O:Oxygen|F:Fluorine|Ne:Neon|Na:Sodium|Mg:Magnesium|Al:Aluminum|Si:Silicon|P:Phosphorus|S:Sulfur|Cl:Chlorine|Ar:Argon|K:Potassium|Ca:Calcium|Sc:Scandium|Ti:Titanium|V:Vanadium|Cr:Chromium|Mn:Manganese|Fe:Iron|Co:Cobalt|Ni:Nickel|Cu:Copper|Zn:Zinc|Ga:Gallium|Ge:Germanium|As:Arsenic|Se:Selenium|Br:Bromine|Kr:Krypton|Rb:Rubidium|Sr:Strontium|Y:Yttrium|Zr:Zirconium|Nb:Niobium|Mo:Molybdenum|Tc:Technetium|Ru:Ruthenium|Rh:Rhodium|Pd:Palladium|Ag:Silver|Cd:Cadmium|In:Indium|Sn:Tin|Sb:Antimony|Te:Tellurium|I:Iodine|Xe:Xenon|Cs:Cesium|Ba:Barium|La:Lanthanum|Ce:Cerium|Pr:Praseodymium|Nd:Neodymium|Pm:Promethium|Sm:Samarium|Eu:Europium|Gd:Gadolinium|Tb:Terbium|Dy:Dysprosium|Ho:Holmium|Er:Erbium|Tm:Thulium|Yb:Ytterbium|Lu:Lutetium|Hf:Hafnium|Ta:Tantalum|W:Tungsten|Re:Rhenium|Os:Osmium|Ir:Iridium|Pt:Platinum|Au:Gold|Hg:Mercury|Tl:Thallium|Pb:Lead|Bi:Bismuth|Po:Polonium|At:Astatine|Rn:Radon|Fr:Francium|Ra:Radium|Ac:Actinium|Th:Thorium|Pa:Protactinium|U:Uranium|Np:Neptunium|Pu:Plutonium|Am:Americium|Cm:Curium|Bk:Berkelium|Cf:Californium|Es:Einsteinium|Fm:Fermium|Md:Mendelevium|No:Nobelium|Lr:Lawrencium|Rf:Rutherfordium|Db:Dubnium|Sg:Seaborgium|Bh:Bohrium|Hs:Hassium|Mt:Meitnerium|Ds:Darmstadtium|Rg:Roentgenium|Cn:Copernicium|Nh:Nihonium|Fl:Flerovium|Mc:Moscovium|Lv:Livermorium|Ts:Tennessine|Og:Oganesson';
const elements = Object.fromEntries(elementData.split('|').map((entry, index) => {
  const [symbol, name] = entry.split(':');
  return [symbol, { symbol, name, number: index + 1 }];
}));
const periods = [
  'H . . . . . . . . . . . . . . . . He',
  'Li Be . . . . . . . . . . B C N O F Ne',
  'Na Mg . . . . . . . . . . Al Si P S Cl Ar',
  'K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr',
  'Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe',
  'Cs Ba Ln Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn',
  'Fr Ra An Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og',
  '. . . La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu',
  '. . . Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr',
];
const families = {
  'Alkali metal': 'Li Na K Rb Cs Fr',
  'Alkaline earth': 'Be Mg Ca Sr Ba Ra',
  'Metalloid': 'B Si Ge As Sb Te',
  'Nonmetal': 'H C N O P S Se',
  'Halogen': 'F Cl Br I At Ts',
  'Noble gas': 'He Ne Ar Kr Xe Rn Og',
  'Post-transition': 'Al Ga In Sn Tl Pb Bi Po Nh Fl Mc Lv',
};
const colors = ['#1b72e7', '#ed724a', '#16a384', '#9765d9', '#ce476c', '#e2a234', '#4097a3'];
const atomColors = { H: '#bfcadb', C: '#526076', N: '#2458cf', O: '#ed534e', Si: '#dbab72', Al: '#79a1c8', Cu: '#c77942', S: '#e4b838', Cl: '#42a768' };
const atomColor = symbol => atomColors[symbol] || colors[(elements[symbol]?.number || 0) % colors.length];

function family(symbol, row) {
  if (row === 7) return 'Lanthanide';
  if (row === 8) return 'Actinide';
  return Object.keys(families).find(name => families[name].split(' ').includes(symbol)) || 'Transition metal';
}

export function MaterialsExplorer({ api, onError, useMaterial }) {
  const [entries, setEntries] = useState([]);
  const [selected, setSelected] = useState([]);
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(null);
  const [loading, setLoading] = useState(true);
  const [source, setSource] = useState('bundled');
  useEffect(() => {
    let current = true;
    api('/structures').then(items => {
      if (current) setEntries(items.map((item, index) => ({ ...item, index })));
    }).catch(error => { if (current) onError(error); })
      .finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, []);

  const matching = entries.filter(entry => selected.every(symbol => entry.elements.includes(symbol)) &&
    [entry.name, entry.formula].some(value => value.toLowerCase().includes(query.trim().toLowerCase())));
  const chosen = matching.find(entry => entry.index === active) || matching[0];
  const toggle = symbol => setSelected(current => current.includes(symbol)
    ? current.filter(item => item !== symbol) : [...current, symbol]);

  return <>
    <header className="explorer-heading"><h1>Materials explorer</h1>
      <p>Filter the bundled crystal structures by element and inspect their unit cells in 3D.</p>
    </header>
    <section className="panel elements-panel" aria-label="Periodic table">
      <div className="explorer-toolbar">
        <label>Catalog<select value={source} onChange={event => { setSource(event.target.value); setQuery(''); }}>
          <option value="bundled">Bundled examples</option>
          <option value="mp">Materials Project</option>
        </select></label>
        <label>Search<input type="search" placeholder={source === 'mp' ? 'Formula or material ID' : 'Formula or material name'} value={query}
          onChange={event => setQuery(event.target.value)} /></label>
        <button className="secondary clear-filters" disabled={!query && !selected.length}
          onClick={() => { setSelected([]); setQuery(''); }}>Clear filters</button>
      </div>
      <div className="periodic-scroll"><div className="periodic-table">
        {periods.flatMap((period, row) => period.split(' ').map((symbol, column) => {
          const element = elements[symbol];
          if (!element && symbol === '.') return <span key={row + ':' + column} aria-hidden="true" />;
          if (!element) return <span key={symbol} className="series-marker" title={symbol === 'Ln' ? 'Lanthanides' : 'Actinides'}>{symbol === 'Ln' ? '57–71' : '89–103'}</span>;
          const category = family(symbol, row);
          return <button key={symbol} className={'element-tile ' + category.toLowerCase().replaceAll(' ', '-')}
            type="button" title={element.name + ' · ' + category} aria-label={element.name + ' (' + symbol + ')'}
            aria-pressed={selected.includes(symbol)} onClick={() => toggle(symbol)}>
            <small>{element.number}</small><strong>{symbol}</strong>
          </button>;
        }))}
      </div></div>
      {selected.length > 0 && <div className="element-filters" aria-label="Selected elements">
        {selected.map(symbol => <button key={symbol} onClick={() => toggle(symbol)}
          aria-label={'Remove ' + elements[symbol].name}>{symbol} ×</button>)}
      </div>}
    </section>
    {source === 'mp' ? <MaterialsProject api={api} onError={onError} query={query} selected={selected} useMaterial={useMaterial} /> :
    <div className="materials-layout">
      <section className="panel material-list">
        <h2>Matching structures <span className="count">{matching.length}</span></h2>
        {loading && <p role="status">Loading examples…</p>}
        {!loading && !matching.length && <p>No bundled structures match these filters.</p>}
        {matching.map(entry => <button key={entry.index} className="material-row"
          aria-pressed={chosen?.index === entry.index} onClick={() => setActive(entry.index)}>
          <strong>{entry.formula}</strong><span>{entry.name}</span>
        </button>)}
      </section>
      {chosen && <CrystalPreview key={chosen.index} endpoint={'/structures/' + chosen.index} api={api} onError={onError} />}
    </div>}
  </>;
}

function MaterialsProject({ api, onError, query, selected, useMaterial }) {
  const [request, setRequest] = useState(null);
  const [response, setResponse] = useState(null);
  const [chosen, setChosen] = useState('');
  const [status, setStatus] = useState(null);
  const [error, setError] = useState('');
  useEffect(() => {
    api('/materials-project/status').then(data => setStatus(data.configured)).catch(onError);
  }, []);
  useEffect(() => {
    if (!request) return;
    let active = true;
    const params = new URLSearchParams(request);
    api('/materials-project/search?' + params).then(data => {
      if (!active) return;
      setResponse(data);
      setChosen(data.items[0]?.material_id || '');
    }).catch(error => {
      if (!active) return;
      if (error.status === 401) onError(error);
      else setError(error.message);
    });
    return () => { active = false; };
  }, [request]);

  function search(page, newSearch = false) {
    setError('');
    setResponse(null);
    setChosen('');
    setRequest(newSearch ? { query: query.trim(), elements: selected.join(','), page } : { ...request, page });
  }

  return <div className="materials-layout">
    <section className="panel material-list">
      <h2>Materials Project</h2>
      {status === false && <p>Add a Materials Project API key in the server configuration to search the online catalog.</p>}
      {status === true && <button className="primary" disabled={!query.trim() && !selected.length}
        onClick={() => search(0, true)}>Search catalog</button>}
      {request && !response && !error && <p role="status">Searching Materials Project…</p>}
      {error && <p className="error" role="alert">{error}</p>}
      {response && <p>{response.total.toLocaleString()} matches</p>}
      {response?.items.map(item => <button key={item.material_id} className="material-row"
        aria-pressed={item.material_id === chosen} onClick={() => setChosen(item.material_id)}>
        <strong>{item.formula_pretty}</strong><span>{item.material_id}</span>
      </button>)}
      {chosen && <button className="secondary use-material" onClick={() => useMaterial(chosen)}>Use {chosen} as simulation input</button>}
      {response && <div className="viewer-toolbar">
        <button type="button" disabled={request.page === 0} onClick={() => search(request.page - 1)}>Previous</button>
        <button type="button" disabled={(request.page + 1) * 25 >= response.total || request.page >= 10000}
          onClick={() => search(request.page + 1)}>Next</button>
      </div>}
    </section>
    {chosen && <CrystalPreview key={chosen} endpoint={'/materials-project/structure/' + encodeURIComponent(chosen)}
      api={api} onError={onError} />}
  </div>;
}

export function CrystalPreview({ endpoint, api, onError }) {
  const [structure, setStructure] = useState(null);
  const [error, setError] = useState('');
  useEffect(() => {
    let current = true;
    api(endpoint).then(data => { if (current) setStructure(data); }).catch(error => {
      if (!current) return;
      if (error.status === 401) onError(error);
      else setError(error.message);
    });
    return () => { current = false; };
  }, [endpoint]);
  return <section className="panel crystal-panel">
    <h2>{structure ? structure.formula + ' crystal structure' : 'Crystal structure'}</h2>
    {error && <p className="error" role="alert">{error}</p>}
    {!structure && !error && <p role="status">Loading crystal structure…</p>}
    {structure && <>
      <CrystalCanvas structure={structure} />
      <div className="crystal-legend">
        {[...new Set(structure.sites.map(site => site.element))].map(symbol => <span key={symbol}>
          {symbol} · {elements[symbol]?.name || symbol}
        </span>)}
      </div>
      <dl className="crystal-metrics">
        <div><dt>Sites</dt><dd>{structure.sites.length}</dd></div>
        <div><dt>Lengths (Å)</dt><dd>{structure.lengths.map(n => n.toFixed(2)).join(' / ')}</dd></div>
        <div><dt>Angles (°)</dt><dd>{structure.angles.map(n => n.toFixed(1)).join(' / ')}</dd></div>
      </dl>
    </>}
  </section>;
}

const initialView = { tilt: 0.49, turn: -0.58, zoom: 1 };
const edges = Array.from({ length: 8 }, (_, i) => [1, 2, 4].filter(bit => !(i & bit)).map(bit => [i, i | bit])).flat();

function CrystalCanvas({ structure }) {
  const ref = useRef(null);
  const drag = useRef(null);
  const [view, setView] = useState(initialView);
  const zoom = multiplier => setView(current => ({ ...current, zoom: Math.max(.35, Math.min(4, current.zoom * multiplier)) }));

  useEffect(() => {
    const canvas = ref.current;
    const context = canvas.getContext('2d');
    if (!context) return;
    const corners = Array.from({ length: 8 }, (_, i) => [0, 1, 2].map(axis =>
      structure.lattice.reduce((sum, vector, n) => sum + ((i >>> n) & 1) * vector[axis], 0)));
    const center = corners[7].map(value => value / 2);
    const radius = Math.max(...corners.map(point => Math.hypot(...point.map((value, i) => value - center[i]))), 1);
    const redraw = () => {
      const { width, height } = canvas.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      context.scale(dpr, dpr);
      const scale = Math.min(width, height) * .40 * view.zoom / radius;
      const cosX = Math.cos(view.tilt), sinX = Math.sin(view.tilt);
      const cosY = Math.cos(view.turn), sinY = Math.sin(view.turn);
      const project = point => {
        const [x, y, z] = point.map((value, i) => value - center[i]);
        const horizontal = x * cosY + z * sinY;
        const depth = z * cosY - x * sinY;
        return [width / 2 + scale * horizontal, height / 2 - scale * (y * cosX - depth * sinX), y * sinX + depth * cosX];
      };
      const projected = corners.map(project);
      const atoms = structure.sites.map(site => ({ ...site, screen: project(site.position) }));
      const lines = edges.map(([a, b]) => ({ a: projected[a], b: projected[b] }));
      const draw = [
        ...lines.map(line => ({ depth: (line.a[2] + line.b[2]) / 2, line })),
        ...atoms.map(atom => ({ depth: atom.screen[2], atom })),
      ].sort((a, b) => a.depth - b.depth);
      for (const item of draw) {
        context.beginPath();
        if (item.line) {
          const { a, b } = item.line;
          context.strokeStyle = '#6b82a8';
          context.lineWidth = 1.2;
          context.moveTo(a[0], a[1]);
          context.lineTo(b[0], b[1]);
          context.stroke();
        } else {
          const { element, screen: [x, y] } = item.atom;
          const size = Math.max(2, Math.min(22, scale * .22));
          const light = context.createRadialGradient(x - size * .28, y - size * .35, 0, x, y, size);
          light.addColorStop(0, '#f3f8ff');
          light.addColorStop(.48, atomColor(element));
          light.addColorStop(1, '#172946');
          context.fillStyle = light;
          context.arc(x, y, size, 0, Math.PI * 2);
          context.fill();
        }
      }
    };
    const observer = new ResizeObserver(redraw);
    observer.observe(canvas);
    redraw();
    return () => observer.disconnect();
  }, [structure, view]);

  return <>
    <div className="crystal-stage">
      <canvas ref={ref} tabIndex={0} aria-label="Interactive unit cell. Use arrow keys to rotate, or drag."
        onPointerDown={event => { event.currentTarget.setPointerCapture(event.pointerId); drag.current = [event.clientX, event.clientY]; }}
        onPointerMove={event => {
          if (!drag.current) return;
          const [x, y] = drag.current;
          drag.current = [event.clientX, event.clientY];
          setView(current => ({ ...current, tilt: current.tilt + (event.clientY - y) * .012,
            turn: current.turn + (event.clientX - x) * .012 }));
        }}
        onLostPointerCapture={() => { drag.current = null; }}
        onKeyDown={event => {
          const movement = { ArrowLeft: [0, -.16], ArrowRight: [0, .16], ArrowUp: [-.16, 0], ArrowDown: [.16, 0] }[event.key];
          if (!movement) return;
          event.preventDefault();
          setView(current => ({ ...current, tilt: current.tilt + movement[0], turn: current.turn + movement[1] }));
        }}
      />
    </div>
    <div className="viewer-toolbar">
      <span>Drag or use arrow keys to rotate</span>
      <button type="button" aria-label="Zoom in" onClick={() => zoom(1.2)}>+</button>
      <button type="button" aria-label="Zoom out" onClick={() => zoom(1 / 1.2)}>−</button>
      <button type="button" onClick={() => setView(initialView)}>Reset view</button>
    </div>
  </>;
}
