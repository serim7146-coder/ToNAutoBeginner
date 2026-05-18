import terrorsData from './terrors.json'
console.log(terrorsData.unbound["208"])

export function terrorName(id) {
  const s = String(id)
  return terrorsData.classic[s]
    || terrorsData.alternate[s]
    || terrorsData.unbound[s]  // ← これがありますか？
    || `ID:${id}`
}