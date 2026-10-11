import componentData from '../../dataset/component_catalog.json'
import deviceData from '../../dataset/device_salvage_catalog.json'
import logger from './logger'

const log = logger.forModule('Catalog')

const normalise = (value = '') => String(value).toLowerCase().replace(/[_\-./]+/g, ' ').replace(/\s+/g, ' ').trim()

export const catalog = [
  ...componentData.map((item) => ({ id: item.id, name: item.display_name, kind: 'component', category: item.category, aliases: item.aliases || [], original: item })),
  ...deviceData.map((item) => ({ id: item.device_id, name: item.device_name, kind: 'device', category: item.device_category, aliases: item.aliases || [], original: item })),
]

log.info(`Initialized local catalog index with ${catalog.length} items (${componentData.length} components, ${deviceData.length} devices)`)

export const findCatalogItem = (id, kind) => {
  const match = catalog.find((item) => item.id === id && (!kind || item.kind === kind))
  if (!match) {
    log.debug(`Catalog lookup missed for ID '${id}' (kind: ${kind || 'any'})`)
  }
  return match
}

export function searchCatalog(query) {
  const token = normalise(query)
  if (!token) return []
  const matches = catalog.filter((item) => [item.name, item.id, ...item.aliases].some((value) => normalise(value).includes(token))).slice(0, 8)
  log.debug(`Catalog search for '${query}' returned ${matches.length} matches`, { query, matchCount: matches.length })
  return matches
}
