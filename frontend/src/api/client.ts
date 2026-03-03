import axios from 'axios'

export const api = axios.create({
  baseURL: '/api',
  timeout: 10_000,
})

api.interceptors.response.use(
  (r) => r,
  (err) => {
    console.error('[API]', err.config?.url, err.response?.status)
    return Promise.reject(err)
  }
)
