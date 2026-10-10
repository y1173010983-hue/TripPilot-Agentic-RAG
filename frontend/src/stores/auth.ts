/** 账号状态：仅保存当前会话令牌，不保存用户密码。 */
import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api, post } from '../api'

export const useAuth = defineStore('auth', () => {
  const user = ref<{ id: string; username: string; role: string } | null>(null)

  async function login(username: string, password: string) {
    const result = await post('/auth/login', { username, password })
    sessionStorage.setItem('trip-token', result.access_token)
    user.value = result.user
  }

  async function load() {
    if (sessionStorage.getItem('trip-token')) user.value = await api('/auth/me')
  }

  function logout() {
    sessionStorage.removeItem('trip-token')
    user.value = null
  }

  return { user, login, load, logout }
})
