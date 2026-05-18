import terrorsData from '../public/terrors.json'

export function terrorName(id) {
  const s = String(id)
  return terrorsData.classic[s]
    || terrorsData.alternate[s]
    || `ID:${id}`
}