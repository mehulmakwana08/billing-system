/* api.js — Thin wrapper around fetch() for the Flask backend */

const API = (() => {
  const BASE = 'http://localhost:5000/api'

  async function request(method, path, body) {
    const opts = {
      method,
      headers: { 'Content-Type': 'application/json' }
    }
    if (body !== undefined) opts.body = JSON.stringify(body)
    const res = await fetch(BASE + path, opts)
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: res.statusText }))
      throw new Error(err.error || `HTTP ${res.status}`)
    }
    return res.json()
  }

  return {
    get:    (path)        => request('GET',    path),
    post:   (path, body)  => request('POST',   path, body),
    put:    (path, body)  => request('PUT',    path, body),
    delete: (path)        => request('DELETE', path),
  }
})()
