<script setup lang="ts">
/** 用户端对话：会话历史、流式运行快照、停止生成、引用与方案版本。 */
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { api, post, watchRun } from '../api'
import AssistantMessage from '../components/AssistantMessage.vue'
import assistantAvatar from '../assets/trippilot-assistant-avatar.png'
import { createClientMessageId } from '../utils/clientMessageId'

/** 消息中的资料引用；数组类型保证模板按数字序号展示来源。 */
interface Citation {
  chunk_id: string
  filename: string
  page?: number
  heading?: string
  content: string
}

interface ChatMessage {
  id: string
  role: string
  content: string
  status?: string
  citations?: Citation[]
}

const sessions = ref<any[]>([])
const current = ref<any>(null)
const messages = ref<ChatMessage[]>([])
const plans = ref<any[]>([])
const input = ref('')
const running = ref(false)
const activeRunId = ref<string | null>(null)
const cancelling = ref(false)
const stage = ref('')
const error = ref('')
const controller = ref<AbortController | null>(null)
const messageArea = ref<HTMLElement | null>(null)
const starterQuestions = ['北京四天怎么玩？', '第一次去成都住哪里方便？', '带老人去西安，有哪些轻松线路？', '故宫附近有什么不辣的美食？']
const currentRequirements = computed(() => current.value?.requirements || {})
const latestPlan = computed(() => plans.value[0] || null)

async function loadSessions() {
  sessions.value = await api('/sessions')
  if (!current.value && sessions.value.length) await selectSession(sessions.value[0])
}

async function createSession() {
  const session = await post('/sessions', { title: '新对话' })
  sessions.value.unshift(session)
  await selectSession(session)
}

async function selectSession(session: any) {
  controller.value?.abort()
  controller.value = null
  activeRunId.value = null
  current.value = session
  running.value = false
  messages.value = await api(`/sessions/${session.id}/messages`)
  plans.value = await api(`/sessions/${session.id}/plans`)
  const fresh = (await api<any[]>('/sessions')).find((item) => item.id === session.id)
  if (fresh) current.value = fresh
  if (fresh?.active_run_id) await monitor(fresh.active_run_id)
  await scrollBottom()
}

async function removeSession(session: any) {
  if (!confirm(`删除对话“${session.title}”？消息和关联方案会被清理。`)) return
  await api(`/sessions/${session.id}`, { method: 'DELETE' })
  sessions.value = sessions.value.filter((item) => item.id !== session.id)
  current.value = null
  messages.value = []
  if (sessions.value.length) await selectSession(sessions.value[0])
}

async function send(prefill?: string) {
  if (!current.value) await createSession()
  const content = (prefill ?? input.value).trim()
  if (!content || running.value) return
  running.value = true
  input.value = ''
  error.value = ''
  const optimistic = { id: `local-${Date.now()}`, role: 'user', content, status: 'completed' }
  messages.value.push(optimistic)
  await scrollBottom()
  try {
    const result = await post('/chat', {
      session_id: current.value.id,
      client_message_id: createClientMessageId(),
      content,
    })
    await monitor(result.run_id)
  } catch (reason) {
    messages.value = messages.value.filter((message) => message.id !== optimistic.id)
    running.value = false
    error.value = (reason as Error).message
    await refreshCurrent()
  }
}

async function monitor(runId: string) {
  activeRunId.value = runId
  running.value = true
  stage.value = '正在读取上下文'
  controller.value?.abort()
  controller.value = new AbortController()
  const ownController = controller.value
  try {
    await watchRun(runId, async (snapshot) => {
      if (controller.value !== ownController) return
      stage.value = snapshot.stage || '正在处理'
      if (snapshot.message) {
        const index = messages.value.findIndex((message) => message.id === snapshot.message.id)
        if (index >= 0) messages.value[index] = { ...messages.value[index], ...snapshot.message, role: 'assistant' }
        else messages.value.push({ ...snapshot.message, role: 'assistant' })
      }
      if (['completed', 'failed', 'cancelled'].includes(snapshot.status)) running.value = false
      await scrollBottom()
    }, ownController.signal)
  } catch (reason) {
    if (controller.value === ownController && (reason as Error).name !== 'AbortError') error.value = (reason as Error).message
  } finally {
    if (controller.value === ownController) {
      running.value = false
      activeRunId.value = null
      await refreshCurrent()
    }
  }
}

async function refreshCurrent() {
  if (!current.value) return
  messages.value = await api(`/sessions/${current.value.id}/messages`)
  plans.value = await api(`/sessions/${current.value.id}/plans`)
  sessions.value = await api('/sessions')
  current.value = sessions.value.find((item) => item.id === current.value.id) || current.value
  await scrollBottom()
}

async function cancel() {
  // 使用本轮提交返回的任务 ID，避免会话快照尚未刷新时停止无效。
  const runId = activeRunId.value
  if (!runId || cancelling.value) return
  cancelling.value = true
  try {
    await post(`/chat/runs/${runId}/cancel`)
    // 保持监听，直到收到服务端取消后的最终快照。
  } catch (reason) {
    error.value = (reason as Error).message
  } finally {
    cancelling.value = false
  }
}

async function restorePlan(version: number) {
  if (!current.value || !latestPlan.value) return
  await post(`/sessions/${current.value.id}/plans/restore`, {
    version,
    base_version: latestPlan.value.version,
  })
  await refreshCurrent()
}

async function scrollBottom() {
  await nextTick()
  if (messageArea.value) messageArea.value.scrollTop = messageArea.value.scrollHeight
}

onMounted(async () => {
  try {
    await loadSessions()
    if (!sessions.value.length) await createSession()
  } catch (reason) {
    error.value = (reason as Error).message
  }
})
onUnmounted(() => controller.value?.abort())
</script>

<template>
  <main class="chat-layout">
    <aside class="conversation-list">
      <button class="primary full" @click="createSession">＋ 新建旅行对话</button>
      <p class="section-label">最近对话</p>
      <button v-for="session in sessions" :key="session.id" class="conversation-item" :class="{ active: session.id === current?.id }" @click="selectSession(session)">
        <span><strong>{{ session.title }}</strong><small>{{ new Date(session.created_at).toLocaleDateString() }}</small></span>
        <i @click.stop="removeSession(session)" title="删除">×</i>
      </button>
    </aside>

    <section class="conversation-main">
      <header class="conversation-title">
        <div><span class="eyebrow">TRAVEL CONVERSATION</span><h2>{{ current?.title || '新对话' }}</h2></div>
        <div class="requirements" v-if="Object.keys(currentRequirements).length">
          <span v-for="(value, key) in currentRequirements" :key="key">{{ key }} · {{ Array.isArray(value) ? value.join('、') : value }}</span>
        </div>
      </header>

      <div class="messages" ref="messageArea">
        <section v-if="!messages.length" class="welcome-chat">
          <span class="eyebrow">WHERE TO NEXT?</span>
          <h1>今天，想聊一趟怎样的旅行？</h1>
          <p>目的地还没想好也没关系。告诉我出发地、时间，或者你只是想去看看什么。</p>
          <div class="starter-grid">
            <button v-for="question in starterQuestions" :key="question" @click="send(question)">{{ question }}<span>↗</span></button>
          </div>
        </section>
        <article v-for="message in messages" :key="message.id" class="message" :class="message.role">
          <div class="avatar">
            <img v-if="message.role === 'assistant'" :src="assistantAvatar" alt="TripPilot 助手头像" />
            <span v-else>你</span>
          </div>
          <div class="message-body">
            <AssistantMessage v-if="message.role === 'assistant'" :content="message.content || (message.status === 'generating' ? '正在整理…' : '')" />
            <p v-else>{{ message.content }}</p>
            <details v-if="message.citations?.length" class="citations">
              <summary>查看 {{ message.citations.length }} 条资料来源</summary>
              <div v-for="(source, index) in message.citations" :key="source.chunk_id" class="citation">
                <strong>[{{ index + 1 }}] {{ source.filename }}</strong>
                <span>{{ source.page ? `第 ${source.page} 页` : source.heading || '文档片段' }}</span>
                <p>{{ source.content }}</p>
              </div>
            </details>
            <small v-if="message.status && message.status !== 'completed'">{{ message.status }}</small>
          </div>
        </article>
        <div v-if="running" class="working"><i></i><i></i><i></i><span>{{ stage }}</span></div>
        <p v-if="error" class="error" role="alert">{{ error }}</p>
      </div>

      <div class="composer-wrap">
        <details v-if="latestPlan" class="plan-card">
          <summary>已保存 {{ plans.length }} 个方案版本 · 最新版本 v{{ latestPlan.version }} · 查看行程</summary>
          <div class="plan-card-body">
            <article v-for="activity in latestPlan.content.activities || []" :key="`${activity.day}-${activity.start}-${activity.name}`">
              <strong>第 {{ activity.day }} 天 · {{ activity.start }}–{{ activity.end }}</strong>
              <span>{{ activity.name }}</span>
              <small>{{ activity.kind }} · {{ activity.estimated_cost == null ? '费用待核实' : `约 ¥${activity.estimated_cost}` }}</small>
            </article>
            <div v-if="plans.length > 1" class="plan-history">
              <span>历史版本</span>
              <button v-for="plan in plans.slice(1)" :key="plan.id" type="button" @click="restorePlan(plan.version)">恢复 v{{ plan.version }}</button>
            </div>
          </div>
        </details>
        <form class="composer" @submit.prevent="send()">
          <textarea v-model="input" maxlength="12000" rows="2" :disabled="running" placeholder="问问交通、景点、线路、美食、酒店，或让我调整已有行程…" @keydown.enter.exact.prevent="send()"></textarea>
          <button v-if="running" type="button" class="stop-btn" :disabled="!activeRunId || cancelling" @click="cancel">{{ cancelling ? '正在停止…' : activeRunId ? '■ 停止' : '正在提交…' }}</button>
          <button v-else class="send-btn" :disabled="!input.trim()">发送 ↗</button>
        </form>
        <small>具体价格、开放时间和库存以回答中的数据来源与查询时间为准。</small>
      </div>
    </section>
  </main>
</template>
