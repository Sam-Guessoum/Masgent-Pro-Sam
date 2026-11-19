import React, { useEffect, useState } from 'react';

const encoded = name => name.split('/').map(encodeURIComponent).join('/');
const dataFile = /\.csv$|(?:^|\/)(?:elastic_constants\.txt|masgent_mlps_(?:md|single)\.log)$/i;
const format = value => typeof value === 'number' ? Number(value.toPrecision(5)).toString() : (value ?? '—');

export function CalculationResults({ run, api, onError }) {
  const plots = run.files.filter(file => /\.png$/i.test(file));
  const datasets = run.files.filter(file => dataFile.test(file));
  const [picked, setPicked] = useState('');
  const active = datasets.includes(picked) ? picked : datasets[0];
  const metrics = Object.entries(run.metrics || {});
  return <section className="calculation-results" aria-label="Calculation results">
    <h4>Calculation results</h4>
    {metrics.length > 0 && <dl className="crystal-metrics">{metrics.map(([name, value]) =>
      <div key={name}><dt>{name.replaceAll('_', ' ')}</dt><dd>{format(value)}</dd></div>)}</dl>}
    {plots.length > 0 && <div className="calculation-plots">{plots.map(file =>
      <figure key={file}><img src={`/api/runs/${run.run_id}/plot/${encoded(file)}`}
        alt={file.split('/').pop().replaceAll('_', ' ')} loading="lazy" />
        <figcaption><a href={`/api/runs/${run.run_id}/files/${encoded(file)}`}>{file.split('/').pop()}</a></figcaption>
      </figure>)}</div>}
    {active && <>
      <label>Calculation data<select value={active} onChange={event => setPicked(event.target.value)}>
        {datasets.map(file => <option key={file}>{file}</option>)}
      </select></label>
      <Dataset key={active} endpoint={`/runs/${run.run_id}/data/${encoded(active)}`} api={api} onError={onError} />
    </>}
    {!plots.length && !active && !metrics.length && run.status === 'succeeded' &&
      <p>This run produced files without a numerical series to plot.</p>}
  </section>;
}

function Dataset({ endpoint, api, onError }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [x, setX] = useState(null);
  const [y, setY] = useState(null);
  useEffect(() => {
    let active = true;
    api(endpoint).then(result => { if (active) setData(result); }).catch(error => {
      if (!active) return;
      if (error.status === 401) onError(error);
      else setError(error.message);
    });
    return () => { active = false; };
  }, [endpoint]);
  if (error) return <p role="alert" className="error">{error}</p>;
  if (!data) return <p role="status">Loading calculation data…</p>;
  if (!data.rows.length) return <p>No recorded data points.</p>;
  const numeric = data.headers.map((_, index) => index).filter(index =>
    data.rows.some(row => Number.isFinite(row[index])));
  const horizontal = x ?? numeric.find(index => /volume|time|step|distance|reaction|temperature/i.test(data.headers[index])) ?? numeric[0];
  const vertical = y ?? numeric.find(index => index !== horizontal && /energy/i.test(data.headers[index])) ??
    numeric.find(index => index !== horizontal) ?? numeric[0];
  const maxTensorValue = data.matrix ? Math.max(1, ...data.rows.flatMap(row => row.slice(1).map(Math.abs))) : 1;
  const points = data.rows.map((row, i) => [horizontal === -1 ? i + 1 : row[horizontal], row[vertical]])
    .filter(point => point.every(Number.isFinite));
  return <>
    {data.matrix ? <div className="data-scroll"><table className="calculation-table"><caption>Elastic constants (GPa)</caption>
      <thead><tr>{data.headers.map((name, i) => <th key={i}>{name}</th>)}</tr></thead>
      <tbody>{data.rows.map((row, i) => <tr key={i}>{row.map((value, j) => <td key={j} className={j === 0 ? undefined :
        'tensor-' + (value < 0 ? 'negative-' : 'positive-') + Math.min(4, Math.round(Math.abs(value) / maxTensorValue * 4))}>{format(value)}</td>)}</tr>)}</tbody>
    </table></div> : numeric.length > 0 && <>
      <div className="chart-fields">
        <label>Horizontal axis<select value={horizontal} onChange={e => setX(Number(e.target.value))}>
          <option value={-1}>Row number</option>
          {numeric.map(i => <option key={i} value={i}>{data.headers[i]}</option>)}
        </select></label>
        <label>Vertical axis<select value={vertical} onChange={e => setY(Number(e.target.value))}>
          {numeric.map(i => <option key={i} value={i}>{data.headers[i]}</option>)}
        </select></label>
      </div>
      {points.length ? <NumericPlot points={points} xLabel={data.headers[horizontal] || 'Row number'} yLabel={data.headers[vertical]} />
        : <p>No paired numerical values in the selected columns.</p>}
    </>}
    {data.metrics && <dl className="crystal-metrics">{Object.entries(data.metrics).map(([key, value]) =>
      <div key={key}><dt>{key}</dt><dd>{format(value)}</dd></div>)}</dl>}
    {data.truncated && <p>Preview limited to the first 2,000 rows. Download the source file for all values.</p>}
  </>;
}

function NumericPlot({ points, xLabel, yLabel }) {
  const bounds = axis => {
    const values = points.map(point => point[axis]);
    const low = Math.min(...values), high = Math.max(...values);
    const pad = low === high ? Math.abs(low) * .05 || .5 : (high - low) * .04;
    return [low - pad, high + pad];
  };
  const [xmin, xmax] = bounds(0), [ymin, ymax] = bounds(1);
  const sx = value => 105 + (value - xmin) / (xmax - xmin) * 615;
  const sy = value => 244 - (value - ymin) / (ymax - ymin) * 210;
  const ticks = [0, .25, .5, .75, 1];
  return <svg className="numeric-plot" viewBox="0 0 750 320" role="img" aria-label={yLabel + ' versus ' + xLabel}>
    {ticks.map(t => {
      const x = xmin + t * (xmax - xmin), y = ymin + t * (ymax - ymin);
      return <g key={t}>
        <path className="plot-grid" d={`M105 ${sy(y)}H720 M${sx(x)} 34V244`} />
        <text x={sx(x)} y="267" textAnchor="middle">{format(x)}</text>
        <text x="96" y={sy(y) + 4} textAnchor="end">{format(y)}</text>
      </g>;
    })}
    <path className="plot-axis" d="M105 34V244H720" />
    {points.map(([x, y], i) => <circle key={i} cx={sx(x)} cy={sy(y)} r={points.length > 250 ? 1.7 : 3.5}>
      <title>{xLabel} {format(x)}, {yLabel} {format(y)}</title>
    </circle>)}
    <text x="412" y="302" textAnchor="middle">{xLabel}</text>
    <text transform="translate(18 139) rotate(-90)" textAnchor="middle">{yLabel}</text>
  </svg>;
}
