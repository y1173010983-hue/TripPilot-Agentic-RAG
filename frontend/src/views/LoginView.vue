<script setup lang="ts">
/** 登录和普通用户注册；管理员账号由后端运维命令创建。 */
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuth } from '../stores/auth'
import { post } from '../api'

const username = ref('')
const password = ref('')
const error = ref('')
const busy = ref(false)
const register = ref(false)
const router = useRouter()
const auth = useAuth()

async function submit() {
  busy.value = true
  error.value = ''
  try {
    if (register.value) await post('/auth/register', { username: username.value, password: password.value })
    await auth.login(username.value, password.value)
    router.push('/')
  } catch (reason) {
    error.value = (reason as Error).message
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <main class="login-layout">
    <section class="login-intro">
      <span class="eyebrow">MAKE ROOM FOR THE JOURNEY</span>
      <h1>下一站，<br />从一句话开始。</h1>
      <p>交通、景点、线路、美食与酒店。<br />把想法告诉我，一起找到适合你的旅行方式。</p>
      <div class="travel-stamp">EXPLORE<br /><strong>YOUR WAY ↗</strong></div>
    </section>
    <form class="login-card" @submit.prevent="submit">
      <h2>{{ register ? '创建账号' : '欢迎回来' }}</h2>
      <p class="muted">登录后，继续你的旅行对话。</p>
      <label>用户名<input v-model="username" required minlength="3" autocomplete="username" placeholder="至少 3 个字符" /></label>
      <label>密码<input v-model="password" required minlength="10" type="password" :autocomplete="register ? 'new-password' : 'current-password'" placeholder="至少 10 个字符" /></label>
      <p class="error" v-if="error" role="alert">{{ error }}</p>
      <button class="primary full" :disabled="busy">{{ busy ? '请稍候…' : register ? '注册并开始' : '登录' }}</button>
      <button type="button" class="text-btn full" @click="register = !register">{{ register ? '已有账号？去登录' : '还没有账号？创建普通用户' }}</button>
    </form>
  </main>
</template>
