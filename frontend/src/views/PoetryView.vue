<template>
  <div class="h-full flex flex-col bg-[#121212]">
    <!-- 顶栏 -->
    <div class="px-6 pt-4 pb-3 border-b border-[#2a2a30] shrink-0 flex items-center gap-3">
      <h2 class="text-sm font-semibold text-gray-200">Poetry 诗词研究闭环 · 音韵 / 格律 / 典故 / 版本</h2>
      <select v-model="form.style" class="text-xs bg-[#1a1a1f] border border-[#3f3f46] rounded px-2 py-1 text-gray-200">
        <option v-for="s in styles" :key="s" :value="s">{{ s }}</option>
      </select>
      <span class="text-[10px] text-gray-600">音韵为实用近似（平水韵+普通话），非学术级中古音重构</span>
    </div>

    <div class="flex-1 flex overflow-hidden">
      <!-- 左：输入与操作 -->
      <div class="w-[380px] border-r border-[#2a2a30] overflow-y-auto p-4 space-y-3">
        <div class="grid grid-cols-2 gap-2 text-xs">
          <input v-model="form.title" placeholder="标题" class="bg-[#1a1a1f] border border-[#3f3f46] rounded px-2 py-1 text-gray-200" />
          <input v-model="form.author" placeholder="作者" class="bg-[#1a1a1f] border border-[#3f3f46] rounded px-2 py-1 text-gray-200" />
          <input v-model="form.dynasty" placeholder="朝代" class="bg-[#1a1a1f] border border-[#3f3f46] rounded px-2 py-1 text-gray-200" />
          <input v-model="gen.theme" placeholder="生成主题(如 春/秋/月/思)" class="bg-[#1a1a1f] border border-[#3f3f46] rounded px-2 py-1 text-gray-200" />
        </div>

        <textarea v-model="form.text" rows="8" placeholder="输入诗词正文，每句一行（可用标点）"
          class="w-full bg-[#1a1a1f] border border-[#3f3f46] rounded p-2 text-xs text-gray-300 resize-none"></textarea>

        <div class="flex flex-wrap gap-2">
          <button class="text-xs bg-indigo-600/70 hover:bg-indigo-500 text-white rounded px-3 py-1" @click="analyze">分析</button>
          <button class="text-xs bg-emerald-700/70 hover:bg-emerald-600 text-white rounded px-3 py-1" @click="generate">生成</button>
          <button class="text-xs bg-amber-700/60 hover:bg-amber-600 text-white rounded px-3 py-1" @click="rewrite">改写建议</button>
          <button class="text-xs bg-[#2a2a30] hover:bg-gray-600 text-gray-300 rounded px-3 py-1" @click="saveWork">保存作品</button>
          <button class="text-xs bg-[#2a2a30] hover:bg-gray-600 text-gray-300 rounded px-3 py-1" @click="evaluate">评测集</button>
        </div>

        <div v-if="works.length" class="text-[11px] text-gray-400">
          <div class="mb-1">已保存作品（{{ works.length }}）</div>
          <div v-for="w in works" :key="w.work_id" class="flex items-center gap-2 cursor-pointer hover:bg-[#1a1a1f] rounded px-1 py-0.5"
               @click="viewWork(w.work_id)">
            <span class="text-xs text-gray-300 truncate flex-1">{{ w.title }} <span v-if="w.latest_version" class="text-gray-600">v{{ w.latest_version }}</span></span>
            <span class="text-[9px] text-gray-600">{{ w.author || '佚名' }}</span>
          </div>
        </div>

        <div v-if="message" class="text-[11px] text-emerald-400">{{ message }}</div>
        <div v-if="error" class="text-[11px] text-red-400">{{ error }}</div>
      </div>

      <!-- 右：结果 -->
      <div class="flex-1 overflow-y-auto px-6 py-4 space-y-5">
        <!-- 分析 -->
        <section v-if="analysis" class="space-y-3">
          <h3 class="text-xs font-semibold text-gray-300">分析结果 · 检测样式：{{ analysis.detected_style }}</h3>

          <!-- 逐字平仄 -->
          <div v-for="line in analysis.phonology.lines" :key="line.seq" class="flex flex-wrap items-center gap-1">
            <span class="text-[10px] text-gray-600 mr-1">{{ line.seq + 1 }}.</span>
            <span v-for="(c, i) in line.chars" :key="i"
                  :title="`${c.pinyin || ''} 平水韵:${c.rhyme_group || '—'} 声:${c.tone_class || '—'} 源:${c.source}`"
                  class="inline-flex items-center gap-1 text-sm rounded px-1"
                  :class="c.ping_ze === '平' ? 'text-sky-300 bg-sky-900/20' : (c.ping_ze === '仄' ? 'text-rose-300 bg-rose-900/20' : 'text-gray-500')">
              {{ c.char }}<span class="text-[9px]" :class="c.ping_ze === '平' ? 'text-sky-500' : 'text-rose-500'">{{ c.ping_ze || '?' }}</span>
            </span>
          </div>

          <!-- 格律规则 -->
          <div class="text-[11px] text-gray-400">格律验证（{{ analysis.meter.style_name }}）</div>
          <div v-for="rule in analysis.meter.rules" :key="rule.name" class="flex items-start gap-2 text-[11px] py-0.5">
            <span class="shrink-0 w-12 text-center rounded text-[10px] px-1"
                  :class="rule.status === 'pass' ? 'bg-emerald-900/40 text-emerald-400' : (rule.status === 'warn' ? 'bg-amber-900/40 text-amber-400' : 'bg-red-900/40 text-red-400')">
              {{ rule.status }}
            </span>
            <span class="text-gray-300">{{ rule.name }}</span>
            <span class="text-gray-500">{{ rule.detail }}</span>
          </div>

          <!-- 韵部候选 -->
          <div v-if="analysis.rhyme_candidates.length" class="text-[11px]">
            <span class="text-gray-400">韵脚候选字：</span>
            <span v-for="ch in analysis.rhyme_candidates" :key="ch" class="inline-block text-[11px] text-sky-300 bg-sky-900/20 rounded px-1.5 py-0.5 mr-1">{{ ch }}</span>
          </div>

          <!-- 典故 -->
          <div v-if="analysis.allusions.length" class="text-[11px]">
            <span class="text-gray-400">典故候选（{{ analysis.allusions.length }}）：</span>
            <div v-for="(a, i) in analysis.allusions" :key="i" class="text-[11px] mt-0.5">
              <span class="text-amber-300">{{ a.phrase }}</span>
              <span class="text-gray-500"> · {{ a.source }}</span>
              <a v-if="a.url" :href="a.url" target="_blank" class="text-sky-400 underline ml-1">回链出处</a>
              <span v-if="a.note" class="text-gray-600 ml-1">（{{ a.note }}）</span>
            </div>
          </div>
          <div v-else class="text-[11px] text-gray-600">未命中内置典故词典（词典覆盖《三国志》《诗经》等 20 余条）</div>
        </section>

        <!-- 生成 -->
        <section v-if="generated" class="space-y-2">
          <h3 class="text-xs font-semibold text-gray-300">生成结果（{{ generated.engine }}）· 韵部：{{ generated.rhyme_group || '—' }}</h3>
          <div v-for="(line, i) in generated.lines" :key="i" class="text-sm text-gray-200">{{ line }}</div>
          <div class="text-[10px] text-gray-600">{{ generated.note }}</div>
        </section>

        <!-- 改写 -->
        <section v-if="rewritten" class="space-y-2">
          <h3 class="text-xs font-semibold text-gray-300">改写建议（{{ rewritten.issue_count }} 处）· 样式：{{ rewritten.style }}</h3>
          <div v-for="(it, i) in rewritten.issues" :key="i" class="border-l-2 border-amber-500/50 pl-2 py-1">
            <div class="text-[11px] text-amber-300">{{ it.issue }}</div>
            <div v-for="(s, j) in it.suggestions" :key="j" class="text-[11px] text-gray-400">
              <span class="text-sky-300">{{ s.text }}</span>
              <span class="text-gray-600"> — {{ s.reason }}</span>
            </div>
          </div>
          <div v-if="!rewritten.issue_count" class="text-[11px] text-emerald-400">未发现需改写的格律问题</div>
        </section>

        <!-- 作品详情 -->
        <section v-if="workDetail" class="space-y-2">
          <h3 class="text-xs font-semibold text-gray-300">
            {{ workDetail.title }} <span class="text-gray-600">{{ workDetail.author }} {{ workDetail.dynasty }}</span>
          </h3>
          <div v-for="e in workDetail.editions" :key="e.edition_id" class="text-[11px] text-gray-400 border-l-2 border-zinc-600 pl-2">
            <div>v{{ e.version }} · 来源{{ e.provenance?.sources?.length || 0 }} 条</div>
            <div class="whitespace-pre-wrap text-gray-300">{{ e.text }}</div>
          </div>
        </section>

        <!-- 评测 -->
        <section v-if="evalResult" class="space-y-2">
          <h3 class="text-xs font-semibold text-gray-300">
            评测集：{{ evalResult.matched }}/{{ evalResult.total }} 命中 ·
            <span class="text-emerald-400">通过率 {{ (evalResult.pass_rate * 100).toFixed(1) }}%</span>
            <span class="text-gray-600"> · 硬性通过 {{ (evalResult.hard_pass_rate * 100).toFixed(1) }}%</span>
          </h3>
          <table class="w-full text-[11px] text-gray-400 border-collapse">
            <thead><tr class="text-gray-500 text-left">
              <th class="py-1 pr-2">样例</th><th class="pr-2">样式</th><th class="pr-2">句数</th><th class="pr-2">字数</th><th class="pr-2">押韵</th><th>命中</th>
            </tr></thead>
            <tbody>
              <tr v-for="it in evalResult.per_item" :key="it.id" class="border-t border-[#1f1f25]">
                <td class="py-1 pr-2 text-gray-300">{{ it.title }}</td>
                <td class="pr-2">{{ it.style }}</td>
                <td class="pr-2">{{ it.actual.line_count_ok ? '✓' : '✗' }}</td>
                <td class="pr-2">{{ it.actual.line_lengths_ok ? '✓' : '✗' }}</td>
                <td class="pr-2">{{ it.actual.rhyme_ok ? '✓' : '✗' }}</td>
                <td :class="it.matched ? 'text-emerald-400' : 'text-red-400'">{{ it.matched ? '命中' : '偏差' }}</td>
              </tr>
            </tbody>
          </table>
        </section>

        <div v-if="!analysis && !generated && !rewritten && !workDetail && !evalResult" class="text-gray-600 text-sm">
          在左侧输入诗词或选择生成主题，然后点击分析 / 生成 / 改写 / 评测。
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { getApiBaseURL } from '../utils/electron'

const styles = ['五绝', '七绝', '五律', '七律', '鹧鸪天']
const base = getApiBaseURL()

const form = reactive({ title: '', author: '', dynasty: '', style: '五绝', text: '' })
const gen = reactive({ theme: '春' })

const analysis = ref<any>(null)
const generated = ref<any>(null)
const rewritten = ref<any>(null)
const workDetail = ref<any>(null)
const evalResult = ref<any>(null)
const works = ref<any[]>([])
const message = ref('')
const error = ref('')

async function call(path: string, method = 'GET', body?: unknown): Promise<any> {
  const res = await fetch(base + path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}))
    throw new Error((detail as any).detail || (detail as any).message || `HTTP ${res.status}`)
  }
  const data = await res.json()
  // 兼容 core.response.ok 包装 与 直出对象
  return data && typeof data === 'object' && 'success' in data ? (data as any).data : data
}

async function analyze() {
  error.value = ''
  message.value = ''
  try {
    analysis.value = await call('/poetry/analyze', 'POST', {
      title: form.title || '未命名',
      author: form.author,
      dynasty: form.dynasty,
      genre: form.style,
      style: form.style,
      text: form.text,
    })
    generated.value = null
    rewritten.value = null
  } catch (e: any) {
    error.value = e.message || '分析失败'
  }
}

async function generate() {
  error.value = ''
  try {
    generated.value = await call('/poetry/generate', 'POST', { style: form.style, theme: gen.theme })
    analysis.value = null
    rewritten.value = null
  } catch (e: any) {
    error.value = e.message || '生成失败'
  }
}

async function rewrite() {
  error.value = ''
  try {
    rewritten.value = await call('/poetry/rewrite', 'POST', { text: form.text, target: 'prosody_fix', style: form.style })
    analysis.value = null
  } catch (e: any) {
    error.value = e.message || '改写失败'
  }
}

async function saveWork() {
  error.value = ''
  message.value = ''
  try {
    const saved = await call('/poetry/works', 'POST', {
      title: form.title || '未命名',
      author: form.author,
      dynasty: form.dynasty,
      style: form.style,
      text: form.text,
    })
    message.value = `已保存 v${saved.edition?.version}（${saved.work?.work_id}）`
    await loadWorks()
  } catch (e: any) {
    error.value = e.message || '保存失败'
  }
}

async function loadWorks() {
  try {
    const data = await call('/poetry/works')
    works.value = data?.works || []
  } catch {
    works.value = []
  }
}

async function viewWork(id: string) {
  error.value = ''
  try {
    workDetail.value = await call(`/poetry/works/${id}`)
    analysis.value = null
  } catch (e: any) {
    error.value = e.message || '读取失败'
  }
}

async function evaluate() {
  error.value = ''
  try {
    evalResult.value = await call('/poetry/evaluate')
  } catch (e: any) {
    error.value = e.message || '评测失败'
  }
}

onMounted(loadWorks)
</script>
