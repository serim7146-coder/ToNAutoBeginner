import { useState, useEffect } from 'react'
import { supabase } from '../supabase'

export function useRounds(playerHash) {
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    async function fetch() {
      setLoading(true)
      try {
        let all = []
        let offset = 0
        const limit = 1000
        while (true) {
          let query = supabase
            .from('ToNRoundStatistics')
            .select('*')
            .order('date', { ascending: false })
            .range(offset, offset + limit - 1)
          if (playerHash) query = query.eq('player_id', playerHash)
          const { data, error } = await query
          if (error) throw error
          all = all.concat(data)
          if (data.length < limit) break
          offset += limit
        }
        setRows(all)
      } catch(e) {
        setError(e.message)
      } finally {
        setLoading(false)
      }
    }
    fetch()
  }, [playerHash])

  return { rows, loading, error }
}