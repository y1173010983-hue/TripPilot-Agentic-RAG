<script setup lang="ts">
/** 应用外壳：品牌、导航以及当前真实服务能力。 */
import { onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuth } from './stores/auth'
import { api } from './api'

const auth = useAuth()
const route = useRoute()
const router = useRouter()
const health = ref<any>(null)

async function loadAppState() {
  try {
    await auth.load()
    if (route.path !== '/login') health.value = await api('/health')
  } catch {
    // API 层已负责清理失效令牌。
  }
}

onMounted(loadAppState)
watch(
  () => route.path,
  async (path) => {
    if (path !== '/login' && auth.user) health.value = await api('/health')
  },
)

function logout() {
  auth.logout()
  router.push('/login')
}
</script>

<template>
  <div class="app-shell" :class="{ 'chat-shell': route.path === '/' }">
    <header class="topbar">
      <RouterLink to="/" class="brand">
        <span class="brand-icon">↗</span>
        <span>TripPilot<small>你的旅行对话助手</small></span>
      </RouterLink>
      <nav v-if="route.path !== '/login'">
        <RouterLink to="/">旅行助手</RouterLink>
        <RouterLink to="/preferences">我的偏好</RouterLink>
        <RouterLink v-if="auth.user?.role === 'admin'" to="/admin">知识库管理</RouterLink>
      </nav>
      <div class="account" v-if="auth.user">
        <span>{{ auth.user.username }}</span>
        <button class="text-btn" @click="logout">退出</button>
      </div>
    </header>
    <div v-if="route.path !== '/login' && health" class="capabilities">
      <span class="dot" :class="{ off: !health.model_configured }"></span>
      {{ health.model_configured ? '对话模型已配置' : '对话模型未配置' }}
      <span>· {{ health.search_mode === 'milvus' ? '混合检索' : '关键词检索' }}</span>
      <span>· {{ health.map_configured ? '地图与天气已配置' : '实时地图、天气未配置' }}</span>
      <span>· 预订需前往官方平台</span>
    </div>
    <RouterView />
  </div>
</template>
