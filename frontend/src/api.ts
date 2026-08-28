import axios from 'axios';

const api = axios.create({
  baseURL: '/api',
  withCredentials: true, // Needed for httpOnly cookies
});

// Request interceptor could be added here
api.interceptors.response.use(
  (response) => response,
  (error) => {
    // Handle global errors like 401 Unauthorized
    if (error.response && error.response.status === 401) {
        console.log("Unauthorized, please login.");
    }
    return Promise.reject(error);
  }
);

export function extractErrorMessage(err: any, fallback: string): string {
  const detail = err?.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail.map((d: any) => d?.msg || JSON.stringify(d)).join(', ');
  }
  return fallback;
}

export default api;
