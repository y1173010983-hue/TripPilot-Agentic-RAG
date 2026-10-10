<script setup lang="ts">
/** 助手回答展示：安全解析 Markdown，支持流式更新和历史消息。 */
import { computed } from 'vue'
import MarkdownIt from 'markdown-it'
const props = defineProps<{ content: string }>()
// 禁止原始 HTML 和图片，避免脚本注入及外部图片自动请求。
const markdown = new MarkdownIt({ html: false, breaks: true, linkify: false })
markdown.disable('image')
const renderedContent = computed(() => markdown.render(props.content))
</script>
<template>
  <div class="assistant-markdown" v-html="renderedContent"></div>
</template>
