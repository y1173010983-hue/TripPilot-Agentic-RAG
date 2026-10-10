/** API 边界：自动携带登录令牌、统一错误消息并解码 SSE 快照。 */
export const token = () => sessionStorage.getItem('trip-token') || ''

export async function api<T = any>(path: string, options: RequestInit = {}): Promise<T> {
  const isForm = options.body instanceof FormData
  const headers = new Headers(options.headers)
  if (!isForm) headers.set('Content-Type', 'application/json')
  if (token()) headers.set('Authorization', `Bearer ${token()}`)
  const response = await fetch('/api/v1' + path, { ...options, headers })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    if (response.status === 401) {
      sessionStorage.removeItem('trip-token')
      location.hash = '/login'
    }
    const message = typeof body.detail === 'string' ? body.detail : '输入格式不正确，请检查字段'
    throw new Error(message || `请求失败 (${response.status})`)
  }
  if (response.status === 204) return undefined as T
  return response.json()
}

export const post = <T = any>(path: string, body = {}) =>
  api<T>(path, { method: 'POST', body: JSON.stringify(body) })

export async function download(path: string, filename: string) {
  /** 使用 Bearer 令牌下载受保护原文，避免把访问凭证放进 URL。 */
  const response = await fetch('/api/v1' + path, { headers: { Authorization: `Bearer ${token()}` } })
  if (!response.ok) throw new Error('原文件下载失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(url)
}

export async function watchRun(
  id: string,
  callback: (data: any) => void,
  signal: AbortSignal,
) {
  const response = await fetch(`/api/v1/chat/runs/${id}/events`, {
    headers: { Authorization: `Bearer ${token()}` },
    signal,
  })
  if (!response.ok || !response.body) throw new Error('无法接收生成进度，请刷新恢复对话')
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) return
    buffer += decoder.decode(value, { stream: true })
    const frames = buffer.split('\n\n')
    buffer = frames.pop() || ''
    for (const frame of frames) {
      const line = frame.split('\n').find((item) => item.startsWith('data: '))
      if (line) callback(JSON.parse(line.slice(6)))
    }
  }
}
