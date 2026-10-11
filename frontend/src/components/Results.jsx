const title = (key) => String(key).replace(/_/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())
const formatValue = (value) => typeof value === 'object' ? JSON.stringify(value) : String(value)
const asList = (value) => Array.isArray(value) ? value : value ? [value] : []

export function Notice({ type = 'error', children }) { return <div className={`notice ${type}`} role={type === 'error' ? 'alert' : 'status'}>{type === 'error' ? '!' : '✓'}<span>{children}</span></div> }

function Detail({ value, depth = 0 }) {
  if (value === null || value === undefined) return null
  if (Array.isArray(value)) return <div className="detail-list">{value.map((item, i) => <Detail key={i} value={item} depth={depth + 1} />)}</div>
  if (typeof value !== 'object') return <span>{formatValue(value)}</span>
  return <div className={depth ? 'nested-detail' : 'detail-grid'}>{Object.entries(value).filter(([, v]) => v !== null && v !== undefined && v !== '' && (!Array.isArray(v) || v.length)).map(([key, item]) => <div className="detail-row" key={key}><b>{title(key)}</b><Detail value={item} depth={depth + 1} /></div>)}</div>
}

function Badge({ children, tone }) { return <span className={`badge ${tone || String(children).toLowerCase().replace(/\s/g, '-')}`}>{String(children).replace(/_/g, ' ')}</span> }
function Videos({ value }) {
  const found = []
  const scan = (node) => { if (!node) return; if (typeof node === 'string') { if (/^https?:\/\/(www\.)?(youtube\.com|youtu\.be)\//i.test(node)) found.push(node); return }; if (Array.isArray(node)) node.forEach(scan); else if (typeof node === 'object') Object.values(node).forEach(scan) }
  scan(value)
  const videos = [...new Set(found)]
  return videos.length ? <section className="videos"><h4>Build videos</h4>{videos.map((url) => <a href={url} target="_blank" rel="noreferrer" key={url} className="video-card"><span className="play">▶</span><span><b>Build tutorial</b><small>YouTube · Watch on YouTube</small></span><span>↗</span></a>)}</section> : null
}

function Requirements({ items }) { if (!items?.length) return null; return <section><h4>Requirements</h4><div className="requirements">{items.map((item, index) => <div className={`requirement ${item.status === 'missing' ? 'missing' : ''}`} key={index}><div><b>{item.role || item.name || 'Requirement'}</b><small>{item.critical ? 'Critical' : 'Optional'} · Need {item.needed_quantity ?? item.quantity ?? 1}</small></div><Badge>{item.status || 'unscored'}</Badge>{item.satisfied_by && <small>Matched: {typeof item.satisfied_by === 'string' ? item.satisfied_by : item.satisfied_by.name || formatValue(item.satisfied_by)}</small>}{item.notes && <small>{item.notes}</small>}</div>)}</div></section> }

function ProjectCard({ project, index }) {
  const name = project.project_name || project.name || project.title || `Recommendation ${index + 1}`
  const build = project.build
  return <article className="project-card"><div className="project-top"><div><div className="eyebrow">{project.category || 'ReWire recommendation'}</div><h3>{name}</h3></div><Badge>{project.tier || 'unscored'}</Badge></div>{project.description && <p>{project.description}</p>}<div className="metrics">{project.feasibility_score !== undefined && <span><b>{project.feasibility_score}%</b> feasibility</span>}{project.difficulty && <span><b>{project.difficulty}</b> difficulty</span>}{(project.estimated_build_time || project.build_time) && <span><b>{project.estimated_build_time || project.build_time}</b> build time</span>}{project.waste_reduction_grams !== undefined && <span><b>{project.waste_reduction_grams} g</b> saved</span>}</div>{asList(project.warnings).length > 0 && <div className="warnings">{asList(project.warnings).map((warning, i) => <div key={i}>⚠ {typeof warning === 'string' ? warning : warning.message || formatValue(warning)}</div>)}</div>}<Requirements items={project.requirements} />{project.shopping_list?.length > 0 && <section className="shopping"><h4>Missing parts</h4>{project.shopping_list.map((item, i) => <div key={i}><b>{item.name || item.component || formatValue(item)}</b>{item.quantity && ` × ${item.quantity}`}{item.notes && <small>{item.notes}</small>}</div>)}</section>}{build && <section className="build"><h4>Build details</h4><Detail value={build} /></section>}<Videos value={project} /></article>
}

export default function Results({ result, mode }) {
  if (!result) return null
  const summary = result.summary || result
  const stats = [['items_recognized', 'Items recognized'], ['projects_ready', 'Projects ready'], ['projects_almost', 'Almost ready'], ['top_waste_reduction_grams', 'Potential waste reduction']].filter(([key]) => summary[key] !== undefined)
  const recommendations = result.recommendations || result.projects || []
  return <section className="results" aria-live="polite"><div className="results-heading"><div><div className="eyebrow">{mode === 'ai' ? 'AI-ReWire' : 'Deterministic ReWire'}</div><h2>Your next build ideas</h2></div><Badge tone="ready">Results ready</Badge></div>{stats.length > 0 && <div className="stats">{stats.map(([key, label]) => <div className="stat" key={key}><b>{key.includes('grams') ? `${summary[key]} g` : summary[key]}</b><span>{label}</span></div>)}</div>}{asList(result.safety_banners).length > 0 && <div className="safety">{asList(result.safety_banners).map((banner, i) => <div key={i}>⚡ {typeof banner === 'string' ? banner : banner.message || formatValue(banner)}</div>)}</div>}{asList(result.unrecognized).length > 0 && <Notice type="info">Some items could not be recognized. {asList(result.unrecognized).map((item) => typeof item === 'string' ? item : item.input || item.name).join(', ')}</Notice>}{asList(result.excluded_defective).length > 0 && <section className="excluded"><h3>Excluded defective parts</h3><Detail value={result.excluded_defective} /></section>}{asList(result.skipped_for_safety).length > 0 && <section className="safety"><h3>Safety-restricted items</h3><Detail value={result.skipped_for_safety} /></section>}{Array.isArray(recommendations) && recommendations.length ? <div className="projects">{recommendations.map((project, i) => <ProjectCard key={project.project_id || project.id || i} project={project} index={i} />)}</div> : <div className="empty-results">No build recommendations were returned for this inventory.</div>}{result.best_combination && Object.keys(result.best_combination).length > 0 && <section className="combination"><div><div className="eyebrow">Resource plan</div><h3>Best combination</h3></div><Detail value={result.best_combination} /></section>}</section>
}
