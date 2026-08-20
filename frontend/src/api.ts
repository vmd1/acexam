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

export default api;
