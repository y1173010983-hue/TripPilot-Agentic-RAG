<script setup lang="ts">
/** 用户主动保存的长期偏好：展示、增加和撤回。 */
import { onMounted, ref } from 'vue'
import { api } from '../api'

const list = ref<any[]>([])
const key = ref('')
const value = ref('')
const error = ref('')

async function load() {
  list.value = await api('/users/me/preferences')
}

async function save() {
  try {
    await api('/users/me/preferences/' + encodeURIComponent(key.value), {
      method: 'PUT',
      body: JSON.stringify({ value: value.value }),
    })
    key.value = ''
    value.value = ''
    await load()
  } catch (reason) {
    error.value = (reason as Error).message
  }
}

async function remove(preferenceKey: string) {
  try {
    await api('/users/me/preferences/' + encodeURIComponent(preferenceKey), { method: 'DELETE' })
    await load()
  } catch (reason) {
    error.value = (reason as Error).message
  }
}

onMounted(() => load().catch((reason) => (error.value = reason.message)))
</script>

<template>
  <main class="page narrow">
    <span class="eyebrow">PERSONAL MEMORY</span>
    <h1>让每次建议，更懂你一点。</h1>
    <p class="muted">只有你主动保存的偏好会跨对话使用，本次日期和预算不会自动成为长期习惯。</p>
    <form class="panel preference-form" @submit.prevent="save">
      <label>偏好名称<input v-model="key" required maxlength="100" placeholder="例如：旅行节奏" /></label>
      <label>具体偏好<input v-model="value" required maxlength="1000" placeholder="喜欢慢慢逛，每天下午安排休息" /></label>
      <button class="primary">保存偏好</button>
    </form>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <section v-for="item in list" :key="item.id" class="panel preference-row">
      <div><strong>{{ item.key }}</strong><p>{{ item.value }}</p></div>
      <button class="text-btn danger" @click="remove(item.key)">删除</button>
    </section>
    <div v-if="!list.length" class="empty-state">还没有保存偏好。可以从兴趣、口味或出行节奏开始。</div>
  </main>
</template>
