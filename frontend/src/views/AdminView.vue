<script setup lang="ts">
/** 管理端存库：创建知识库、批量拖入文档、处理进度、切片预览和检索验证。 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api, download } from '../api'

const bases = ref<any[]>([])
const selectedBaseId = ref('')
const documents = ref<any[]>([])
const files = ref<File[]>([])
const city = ref('')
const category = ref('综合攻略')
const sourceUrl = ref('')
const validUntil = ref('')
const newBaseName = ref('')
const newBaseVisibility = ref('public')
const uploadState = ref<Record<string, string>>({})
const searchText = ref('')
const searchResults = ref<any[]>([])
const preview = ref<any[]>([])
const error = ref('')
const busy = ref(false)
const replacementInput = ref<HTMLInputElement | null>(null)
const replacementDocument = ref<any>(null)
let timer: number | undefined
const selectedBase = computed(() => bases.value.find((item) => item.id === selectedBaseId.value))

async function loadBases() {
  bases.value = await api('/admin/knowledge-bases')
  if (!selectedBaseId.value && bases.value.length) selectedBaseId.value = bases.value[0].id
  if (selectedBaseId.value) await loadDocuments()
}

async function createBase() {
  if (!newBaseName.value.trim()) return
  const created = await api('/admin/knowledge-bases', {
    method: 'POST',
    body: JSON.stringify({ name: newBaseName.value.trim(), description: '', visibility: newBaseVisibility.value }),
  })
  newBaseName.value = ''
  bases.value.unshift(created)
  selectedBaseId.value = created.id
  await loadDocuments()
}

async function loadDocuments() {
  if (!selectedBaseId.value) return
  documents.value = await api(`/admin/knowledge-bases/${selectedBaseId.value}/documents`)
}

function acceptFiles(incoming: FileList | File[]) {
  const allowed = ['.pdf', '.docx', '.md', '.txt']
  for (const file of Array.from(incoming)) {
    const suffix = '.' + file.name.split('.').pop()?.toLowerCase()
    if (!allowed.includes(suffix)) error.value = `${file.name}：仅支持 PDF、DOCX、MD、TXT`
    else if (file.size > 20 * 1024 * 1024) error.value = `${file.name}：超过 20 MB`
    else if (!files.value.some((item) => item.name === file.name && item.size === file.size)) files.value.push(file)
  }
}

function dropped(event: DragEvent) {
  if (event.dataTransfer?.files) acceptFiles(event.dataTransfer.files)
}

async function uploadAll() {
  if (!selectedBaseId.value || !files.value.length) return
  busy.value = true
  error.value = ''
  for (const file of [...files.value]) {
    uploadState.value[file.name] = '正在上传'
    const body = new FormData()
    body.append('file', file)
    body.append('city', city.value)
    body.append('category', category.value)
    body.append('source_url', sourceUrl.value)
    if (validUntil.value) body.append('valid_until', validUntil.value)
    try {
      const result = await api(`/admin/knowledge-bases/${selectedBaseId.value}/documents`, { method: 'POST', body })
      uploadState.value[file.name] = result.duplicate ? '已存在' : '已保存，等待入库'
      if (!result.duplicate) monitorJob(file.name, result.job_id)
      files.value = files.value.filter((item) => item !== file)
    } catch (reason) {
      uploadState.value[file.name] = '上传失败'
      error.value = (reason as Error).message
    }
  }
  busy.value = false
  await loadDocuments()
}

function chooseReplacement(document: any) {
  replacementDocument.value = document
  replacementInput.value?.click()
}

async function uploadReplacement(event: Event) {
  /** 为现有文档创建新版本；后台仅在新版本完整入库后原子切换 active_version_id。 */
  const inputElement = event.target as HTMLInputElement
  const file = inputElement.files?.[0]
  const document = replacementDocument.value
  inputElement.value = ''
  if (!file || !document) return

  const previousFiles = files.value
  files.value = []
  acceptFiles([file])
  const validFile = files.value[0]
  files.value = previousFiles
  if (!validFile) return

  error.value = ''
  uploadState.value[file.name] = `正在上传新版本（${document.title}）`
  const body = new FormData()
  body.append('file', file)
  body.append('document_id', document.id)
  body.append('city', city.value)
  body.append('category', category.value)
  body.append('source_url', sourceUrl.value)
  if (validUntil.value) body.append('valid_until', validUntil.value)
  try {
    const result = await api(
      `/admin/knowledge-bases/${selectedBaseId.value}/documents`,
      { method: 'POST', body },
    )
    uploadState.value[file.name] = result.duplicate ? '该内容已存在' : '新版本已保存，等待入库'
    if (!result.duplicate) monitorJob(file.name, result.job_id)
    await loadDocuments()
  } catch (reason) {
    uploadState.value[file.name] = '新版本上传失败'
    error.value = (reason as Error).message
  } finally {
    replacementDocument.value = null
  }
}

function monitorJob(filename: string, jobId: string) {
  const poll = async () => {
    try {
      const job = await api(`/admin/ingestion-jobs/${jobId}`)
      uploadState.value[filename] = job.stage
      if (job.status === 'failed') uploadState.value[filename] = `失败：${job.error}`
      if (!['completed', 'failed', 'cancelled'].includes(job.status)) window.setTimeout(poll, 1500)
      else await loadDocuments()
    } catch (reason) { error.value = (reason as Error).message }
  }
  poll()
}

async function retry(job: any) {
  await api(`/admin/ingestion-jobs/${job.id}/retry`, { method: 'POST', body: '{}' })
  monitorJob('重试任务', job.id)
  await loadDocuments()
}

async function toggle(document: any) {
  await api(`/admin/documents/${document.id}`, { method: 'PATCH', body: JSON.stringify({ enabled: !document.enabled }) })
  await loadDocuments()
}

async function remove(document: any) {
  if (!confirm(`删除“${document.title}”？它会立即退出检索，随后清理原文件与切片。`)) return
  await api(`/admin/documents/${document.id}`, { method: 'DELETE' })
  await loadDocuments()
}

async function showChunks(document: any) {
  preview.value = await api(`/admin/documents/${document.id}/chunks`)
}

async function downloadOriginal(document: any) {
  try { await download(`/admin/documents/${document.id}/original`, document.title) }
  catch (reason) { error.value = (reason as Error).message }
}

async function testSearch() {
  searchResults.value = await api(`/admin/knowledge-bases/${selectedBaseId.value}/retrieval-test`, {
    method: 'POST', body: JSON.stringify({ query: searchText.value, top_k: 8 }),
  })
}

onMounted(async () => {
  try {
    await loadBases()
    timer = window.setInterval(loadDocuments, 4000)
  } catch (reason) { error.value = (reason as Error).message }
})
onUnmounted(() => timer && clearInterval(timer))
</script>

<template>
  <main class="admin-layout">
    <aside class="admin-sidebar">
      <span class="eyebrow">KNOWLEDGE BASES</span>
      <h2>旅游知识库</h2>
      <button v-for="base in bases" :key="base.id" @click="selectedBaseId = base.id; loadDocuments()" :class="{ active: base.id === selectedBaseId }">
        <span>{{ base.name }}</span><small>{{ base.visibility === 'public' ? '公开检索' : '仅管理员' }}</small>
      </button>
      <form class="new-base" @submit.prevent="createBase">
        <input v-model="newBaseName" maxlength="120" placeholder="新知识库名称" />
        <select v-model="newBaseVisibility"><option value="public">公开检索</option><option value="private">仅管理员</option></select>
        <button class="primary">＋ 创建</button>
      </form>
    </aside>

    <section class="admin-main">
      <input ref="replacementInput" class="hidden-file-input" type="file" accept=".pdf,.docx,.md,.txt" @change="uploadReplacement" />
      <header><span class="eyebrow">ADMIN CONSOLE</span><h1>{{ selectedBase?.name || '先创建知识库' }}</h1><p>上传成功表示原文件已保存；状态为“文档已入库”后，用户端才可以检索。</p></header>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <div class="admin-grid" v-if="selectedBaseId">
        <section class="panel upload-panel">
          <div><span class="step-number">01</span><h3>添加资料</h3></div>
          <div class="metadata-row"><input v-model="city" placeholder="城市，例如：北京" /><select v-model="category"><option>综合攻略</option><option>交通</option><option>景点</option><option>线路</option><option>美食</option><option>酒店</option><option>天气季节</option><option>票务</option><option>文化</option></select><input v-model="validUntil" type="date" title="资料有效期（可选）" /><input v-model="sourceUrl" type="url" placeholder="来源网址（可选）" /></div>
          <label class="dropzone" @dragover.prevent @drop.prevent="dropped">
            <input type="file" multiple accept=".pdf,.docx,.md,.txt" @change="acceptFiles(($event.target as HTMLInputElement).files || [])" />
            <span class="drop-icon">⇧</span><strong>把旅游资料拖到这里</strong><small>PDF、DOCX、MD、TXT · 每个不超过 20 MB</small>
          </label>
          <div v-for="file in files" :key="file.name + file.size" class="file-ready"><span>{{ file.name }}<small>{{ (file.size / 1024).toFixed(1) }} KB</small></span><button @click="files = files.filter(item => item !== file)">×</button></div>
          <button class="primary" :disabled="busy || !files.length" @click="uploadAll">{{ busy ? '正在上传…' : `上传并入库 ${files.length ? `(${files.length})` : ''}` }}</button>
          <div v-for="(status, name) in uploadState" :key="name" class="upload-status"><span>{{ name }}</span><strong>{{ status }}</strong></div>
        </section>

        <section class="panel search-panel"><div><span class="step-number">02</span><h3>检索验证</h3></div><p>用真实问题确认新资料能否被召回，结果会显示文件与页码。</p><form @submit.prevent="testSearch"><input v-model="searchText" required placeholder="例如：北京有哪些亲子博物馆？" /><button class="dark">检索</button></form><article v-for="(hit, i) in searchResults" :key="hit.chunk_id" class="search-hit"><strong>{{ i + 1 }} · {{ hit.filename }}</strong><span>得分 {{ hit.score.toFixed(4) }} {{ hit.page ? `· 第 ${hit.page} 页` : '' }}</span><p>{{ hit.content }}</p></article><div v-if="searchText && !searchResults.length" class="empty-inline">暂无命中，先确认文档是否已入库。</div></section>
      </div>

      <section class="document-section" v-if="selectedBaseId"><div class="section-heading"><div><span class="eyebrow">DOCUMENT INVENTORY</span><h2>已上传资料</h2></div><button class="text-btn" @click="loadDocuments">刷新状态</button></div>
        <div class="document-table"><div class="table-head"><span>文件</span><span>版本状态</span><span>切片</span><span>是否检索</span><span>操作</span></div>
          <div v-for="document in documents" :key="document.id" class="table-row"><span><strong>{{ document.title }}</strong><small>{{ document.versions?.[0]?.metadata_json?.category || '综合攻略' }} · {{ document.versions?.length || 0 }} 个版本</small></span><span><i class="status-dot" :class="document.versions?.[0]?.status?.toLowerCase()"></i>{{ document.job?.stage || document.versions?.[0]?.status || '等待处理' }}<small v-if="document.job?.error" class="danger">{{ document.job.error }}</small></span><span>{{ document.versions?.[0]?.chunk_count || 0 }}</span><span>{{ document.enabled ? '已启用' : '已停用' }}</span><span class="actions"><button @click="showChunks(document)">切片</button><button @click="downloadOriginal(document)">原文</button><button @click="chooseReplacement(document)">更新版本</button><button @click="toggle(document)">{{ document.enabled ? '停用' : '启用' }}</button><button v-if="document.job?.status === 'failed'" @click="retry(document.job)">重试</button><button class="danger" @click="remove(document)">删除</button></span></div>
          <div v-if="!documents.length" class="empty-state">这里还没有资料。拖入第一份文档，构建可检索的旅游知识。</div>
        </div>
      </section>

      <section v-if="preview.length" class="panel chunk-preview"><div class="section-heading"><h2>切片预览</h2><button class="text-btn" @click="preview = []">关闭</button></div><article v-for="chunk in preview" :key="chunk.id"><span>#{{ chunk.position + 1 }} {{ chunk.page ? `· 第 ${chunk.page} 页` : '' }} {{ chunk.heading }}</span><p>{{ chunk.content }}</p></article></section>
    </section>
  </main>
</template>
