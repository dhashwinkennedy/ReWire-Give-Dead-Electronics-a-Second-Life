import { useEffect, useMemo, useRef, useState } from 'react'
import { catalog, findCatalogItem, searchCatalog } from './services/catalog'
import { aiRepurposeInventory, detectImage, repurposeInventory } from './services/api'
import Results, { Notice } from './components/Results'
import LogViewer from './components/LogViewer'
import ErrorBoundary from './components/ErrorBoundary'
import NetworkStatusBanner from './components/NetworkStatusBanner'
import ActionableErrorNotice from './components/ActionableErrorNotice'
import logger from './services/logger'

const log = logger.forModule('App')

const CONDITION = ['working', 'defective', 'unknown']
const conditionLabel = (value) => value[0].toUpperCase() + value.slice(1)

function Icon({ name }) {
  return (
    <span aria-hidden="true" className={`icon ${name}`}>
      {name === 'component' ? '⌘' : name === 'device' ? '▣' : name === 'lens' ? '◉' : name === 'leaf' ? '◒' : '✦'}
    </span>
  )
}

function Search({ onAdd, disabled }) {
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(false)
  const results = useMemo(() => searchCatalog(query), [query])
  const input = useRef(null)
  const add = (item) => {
    onAdd(item)
    setQuery('')
    input.current?.focus()
  }
  return (
    <div className="search-wrap">
      <label htmlFor="catalog-search">What components or devices do you have?</label>
      <p>Search the verified ReWire catalog to build an inventory.</p>
      <div className="search-box">
        <span className="search-mark">⌕</span>
        <input
          ref={input}
          id="catalog-search"
          autoComplete="off"
          value={query}
          disabled={disabled}
          onFocus={() => setActive(true)}
          onChange={(event) => {
            setQuery(event.target.value)
            setActive(true)
          }}
          placeholder="Search components or devices…"
          aria-autocomplete="list"
          aria-expanded={active && Boolean(query)}
        />
        <span className="shortcut">⌘ K</span>
      </div>
      {active && query && (
        <div className="dropdown" role="listbox">
          {results.length ? (
            results.map((item) => (
              <button
                type="button"
                role="option"
                key={`${item.kind}-${item.id}`}
                onMouseDown={(event) => {
                  event.preventDefault()
                  add(item)
                }}
              >
                <Icon name={item.kind} />
                <span>
                  <b>{item.name}</b>
                  <small>{item.category || item.kind}</small>
                </span>
                <span className={`kind ${item.kind}`}>{item.kind}</span>
              </button>
            ))
          ) : (
            <div className="no-match">No catalog match yet. Try a different name or alias.</div>
          )}
        </div>
      )}
    </div>
  )
}

function ImageDetect({ onDetected, onError, onDetect, detecting }) {
  const picker = useRef(null)
  const [preview, setPreview] = useState('')

  const processFile = async (file) => {
    if (!file) return
    const allowed = ['image/jpeg', 'image/png', 'image/webp', 'image/bmp']
    if (file.type && !allowed.includes(file.type)) {
      onError({
        error: {
          errorCode: 'UNSUPPORTED_IMAGE_FORMAT',
          title: 'Unsupported Image Format',
          message: `The selected file format '${file.type || 'unknown'}' is not supported.`,
          remediation: 'Please convert the image to JPEG, PNG, WEBP, or BMP format before uploading.',
          status: 400,
        },
      })
      return
    }
    const MAX_SIZE = 15 * 1024 * 1024
    if (file.size > MAX_SIZE) {
      const sizeMb = (file.size / (1024 * 1024)).toFixed(1)
      onError({
        error: {
          errorCode: 'IMAGE_SIZE_LIMIT_EXCEEDED',
          title: 'Image Size Limit Exceeded',
          message: `The selected image (${sizeMb} MB) exceeds the 15 MB upload limit.`,
          remediation: 'Resize or compress the photo to under 15 MB before uploading.',
          status: 413,
        },
      })
      return
    }

    setPreview(URL.createObjectURL(file))
    try {
      const response = await onDetect(file)
      if (response.resolved && response.normalized_inventory?.name && response.normalized_inventory?.type) {
        onDetected(response)
      } else {
        onError({
          error: {
            errorCode: 'VISION_DETECTION_UNCERTAIN',
            title: 'Identification Uncertain',
            message: response.reason || response.message || "We couldn’t confidently identify this item.",
            remediation: 'Try photographing against a plain background, or search the verified catalog manually.',
            status: 200,
          },
        })
      }
    } catch (error) {
      onError({
        error,
        onRetry: () => processFile(file),
      })
    }
  }

  const handleFile = (event) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (file) processFile(file)
  }

  useEffect(() => () => { if (preview) URL.revokeObjectURL(preview) }, [preview])

  return (
    <div className="detect">
      <input ref={picker} type="file" accept="image/jpeg,image/png,image/webp,image/bmp" onChange={handleFile} hidden />
      <button
        type="button"
        className="camera-button"
        onClick={() => picker.current?.click()}
        disabled={detecting}
        aria-label="Identify component from image"
        title="Identify component from a photo"
      >
        <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
          <path d="M8.5 5.5 10 3.75h4l1.5 1.75h2.75A2.75 2.75 0 0 1 21 8.25v8A2.75 2.75 0 0 1 18.25 19h-12.5A2.75 2.75 0 0 1 3 16.25v-8A2.75 2.75 0 0 1 5.75 5.5H8.5Z" />
          <circle cx="12" cy="12.25" r="3.25" />
        </svg>
      </button>
      {preview && <img src={preview} className="thumbnail" alt="Selected for identification" />}
      {detecting && <span className="detecting"><i /> Identifying component…</span>}
    </div>
  )
}

function InventoryCard({ item, onChange, onRemove, componentOptions }) {
  const selected = item.defective_components || []
  const addFailed = (event) => {
    const id = event.target.value
    if (id && !selected.includes(id)) onChange({ defective_components: [...selected, id] })
    event.target.value = ''
  }
  return (
    <article className="inventory-card">
      <div className="inventory-top">
        <div>
          <span className={`kind ${item.kind}`}>{item.kind}</span>
          <h3>{item.name}</h3>
        </div>
        <button onClick={onRemove} className="text-button" aria-label={`Remove ${item.name}`}>
          Remove
        </button>
      </div>

      <div className="card-fields">
        <label>
          <span>Quantity</span>
          <input
            type="number"
            min="1"
            max="999"
            value={item.quantity}
            onChange={(event) =>
              onChange({ quantity: Math.max(1, parseInt(event.target.value, 10) || 1) })
            }
          />
        </label>
        <label>
          <span>Condition</span>
          <div className="select-wrap">
            <select
              value={item.condition}
              onChange={(event) => onChange({ condition: event.target.value })}
            >
              {CONDITION.map((condition) => (
                <option key={condition} value={condition}>
                  {conditionLabel(condition)}
                </option>
              ))}
            </select>
          </div>
        </label>
      </div>

      {item.kind === 'device' && item.condition === 'defective' && (
        <div className="defective-zone">
          <label>Defective components</label>
          <div className="select-wrap">
            <select value="" onChange={addFailed}>
              <option value="">+ Add a broken part</option>
              {componentOptions.map((component) => (
                <option key={component.id} value={component.id}>
                  {component.name}
                </option>
              ))}
            </select>
          </div>
          {selected.length > 0 && (
            <div className="tag-list">
              {selected.map((id) => (
                <button
                  type="button"
                  key={id}
                  onClick={() =>
                    onChange({
                      defective_components: selected.filter((selectedId) => selectedId !== id),
                    })
                  }
                  title="Remove from broken parts"
                >
                  {findCatalogItem(id, 'component')?.name || id} <span>×</span>
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </article>
  )
}

function App() {
  const [inventory, setInventory] = useState([])
  const [loading, setLoading] = useState('')
  const [result, setResult] = useState(null)
  const [resultMode, setResultMode] = useState('')
  const [statusNotice, setStatusNotice] = useState(null)
  const [actionableError, setActionableError] = useState(null)
  const componentOptions = useMemo(() => catalog.filter((item) => item.kind === 'component'), [])

  useEffect(() => {
    log.info('ReWire UI initialised', { catalogSize: catalog.length })
  }, [])

  const addItem = (entry) => {
    log.info(`Adding item to user inventory: ${entry.name}`, { id: entry.id, kind: entry.kind })
    const existing = inventory.find(
      (item) => item.id === entry.id && item.kind === entry.kind && item.condition === 'working'
    )
    if (existing) {
      setInventory((items) =>
        items.map((item) =>
          item === existing ? { ...item, quantity: Math.min(999, item.quantity + 1) } : item
        )
      )
    } else {
      setInventory((items) => [
        ...items,
        {
          id: entry.id,
          name: entry.name,
          kind: entry.kind,
          quantity: 1,
          condition: 'working',
          defective_components: [],
        },
      ])
    }
    setActionableError(null)
    setStatusNotice({ type: 'success', text: `${entry.name} added to your inventory.` })
  }

  const update = (index, changes) => {
    log.debug(`Updating inventory item #${index}`, changes)
    setInventory((items) => items.map((item, i) => (i === index ? { ...item, ...changes } : item)))
  }

  const detectBusy = async (file) => {
    log.info(`Initiating vision detection for file '${file.name}' (${file.size} bytes)`)
    setLoading('detect')
    setActionableError(null)
    try {
      const resp = await detectImage(file)
      log.info('Vision detection completed successfully', resp)
      return resp
    } catch (err) {
      log.error('Vision detection failed', { error: err.message })
      throw err
    } finally {
      setLoading('')
    }
  }

  const handleDetection = (data) => {
    const found = findCatalogItem(data.normalized_inventory.name, data.normalized_inventory.type)
    if (found) {
      addItem(found)
      log.info(`Auto-resolved vision detection to catalog item: ${found.name}`)
      setStatusNotice({ type: 'success', text: `${found.name} was identified and added.` })
    } else {
      log.warn(`Vision detection resolved item '${data.normalized_inventory.name}' but item not found in catalog`)
      setActionableError({
        error: {
          errorCode: 'CATALOG_ITEM_NOT_FOUND',
          title: 'Catalog Item Not Found',
          message: `The item '${data.normalized_inventory.name}' was detected but is not listed in the verified catalog.`,
          remediation: 'Try searching for an equivalent standard component or browse similar parts in the catalog.',
          status: 404,
        },
      })
    }
  }

  const submit = async (ai) => {
    if (!inventory.length) {
      log.warn('Submit attempted with empty inventory')
      setActionableError({
        error: {
          errorCode: 'EMPTY_INVENTORY',
          title: 'Inventory Is Empty',
          message: 'Add at least one component or device from the catalog to generate build recommendations.',
          remediation: 'Search the catalog above or take a photo of a circuit component to add it.',
          status: 422,
        },
      })
      return
    }

    const mode = ai ? 'ai' : 'rewire'
    setLoading(mode)
    setActionableError(null)
    setStatusNotice(null)
    log.info(`Submitting repurpose search (mode: ${mode}) with ${inventory.length} items`)

    try {
      const res = await (ai ? aiRepurposeInventory(inventory) : repurposeInventory(inventory))
      setResult(res)
      setResultMode(mode)
      log.info(`Search finished: ${res.recommendations?.length || 0} projects generated`)
    } catch (error) {
      log.error(`Repurpose search failed (${mode})`, {
        error: error.message,
        errorCode: error.errorCode,
        requestId: error.requestId,
      })

      setActionableError({
        error,
        onRetry: () => submit(ai),
        onFallback: ai ? () => submit(false) : null,
        fallbackLabel: '✦ Run Standard ReWire (Deterministic)',
      })
    } finally {
      setLoading('')
    }
  }

  const isBusy = Boolean(loading)

  return (
    <main>
      <NetworkStatusBanner />

      <header>
        <a href="#top" className="brand">
          <span className="brand-mark"><Icon name="leaf" /></span>
          <span>Re<span>Wire</span></span>
        </a>
        <p>Turn old electronics into something useful.</p>
        <div className="status-dot"><i />Catalog connected</div>
      </header>

      <section id="top" className="hero">
        <div className="hero-copy">
          <div className="eyebrow">REPURPOSE · REIMAGINE · REDUCE</div>
          <h1>Give your old tech<br /><em>a next life.</em></h1>
          <p>Build an inventory from the parts you already have. ReWire turns it into practical, lower-waste project ideas.</p>
          <div className="hero-tags">
            <span>◉ Catalog-powered</span>
            <span>✦ AI-assisted</span>
            <span>♺ Lower waste</span>
          </div>
        </div>
        <div className="orbital" aria-hidden="true">
          <div className="orbit orbit-one" />
          <div className="orbit orbit-two" />
          <div className="core">⌁</div>
          <span className="float-chip chip-one">◒</span>
          <span className="float-chip chip-two">⌘</span>
          <span className="float-chip chip-three">◈</span>
        </div>
      </section>

      <section className="builder">
        <div className="builder-head">
          <div>
            <div className="eyebrow">YOUR INVENTORY</div>
            <h2>Start with what you’ve got.</h2>
          </div>
          <span className="item-count">{inventory.length} {inventory.length === 1 ? 'item' : 'items'}</span>
        </div>

        <div className="input-row">
          <Search onAdd={addItem} disabled={isBusy} />
          <ImageDetect
            onDetect={detectBusy}
            detecting={loading === 'detect'}
            onDetected={handleDetection}
            onError={({ error, onRetry }) => {
              log.warn(`Image detection error handled: ${error?.message || error}`)
              setActionableError({
                error,
                onRetry,
              })
            }}
          />
        </div>

        {actionableError && (
          <ActionableErrorNotice
            error={actionableError.error}
            onRetry={actionableError.onRetry}
            onFallback={actionableError.onFallback}
            fallbackLabel={actionableError.fallbackLabel}
            onDismiss={() => setActionableError(null)}
          />
        )}

        {statusNotice && (
          <Notice type={statusNotice.type}>{statusNotice.text}</Notice>
        )}

        {inventory.length === 0 ? (
          <div className="empty-inventory">
            <span>⌘</span>
            <div>
              <b>Your bench is clear</b>
              <p>Search the catalog or identify a part from a photo to begin.</p>
            </div>
          </div>
        ) : (
          <div className="inventory-list">
            {inventory.map((item, index) => (
              <InventoryCard
                key={`${item.id}-${index}`}
                item={item}
                onChange={(changes) => update(index, changes)}
                onRemove={() => {
                  log.info(`Removed item #${index}: ${item.name}`)
                  setInventory((items) => items.filter((_, i) => i !== index))
                }}
                componentOptions={componentOptions}
              />
            ))}
          </div>
        )}

        <div className="actions">
          <div>
            <b>Ready to reimagine?</b>
            <span>
              {inventory.length
                ? `Analyze ${inventory.reduce((total, item) => total + item.quantity, 0)} item${
                    inventory.reduce((total, item) => total + item.quantity, 0) === 1 ? '' : 's'
                  } in your inventory.`
                : 'Add something from the catalog to get started.'}
            </span>
          </div>
          <div className="action-buttons">
            <button className="button secondary" disabled={isBusy} onClick={() => submit(false)}>
              {loading === 'rewire' ? <><i /> ReWiring…</> : <>✦ ReWire</>}
            </button>
            <button className="button primary" disabled={isBusy} onClick={() => submit(true)}>
              {loading === 'ai' ? <><i /> AI-ReWiring…</> : <>✦ AI-ReWire</>}
            </button>
          </div>
        </div>

        {loading === 'rewire' || loading === 'ai' ? (
          <div className="working">
            <i />{loading === 'ai' ? 'AI-ReWire is analyzing your inventory with local AI (this may take a minute or two)…' : 'Finding projects from your inventory…'}
          </div>
        ) : null}
      </section>

      <ErrorBoundary>
        <Results result={result} mode={resultMode} />
      </ErrorBoundary>

      <footer>
        <span>REWIRE</span>
        <p>Every component has potential.</p>
      </footer>

      {/* Embedded Live DevTools & Advanced Log Stream */}
      <LogViewer />
    </main>
  )
}

export default App
