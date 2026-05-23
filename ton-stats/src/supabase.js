import { createClient } from '@supabase/supabase-js'

export const supabase = createClient(
  'https://cpmosqeufhdknzwyfvio.supabase.co',
  'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImNwbW9zcWV1Zmhka256d3lmdmlvIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzkwNDE2MDQsImV4cCI6MjA5NDYxNzYwNH0.k-EGeBWYZ6HexnK1o8jncneEK50Ff00qu5d5HGJCYSw'
)